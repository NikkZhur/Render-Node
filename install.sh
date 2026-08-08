#!/usr/bin/env bash
set -Eeuo pipefail

readonly REPOSITORY="NikkZhur/Render-Node"
readonly ASSET="render-node-linux-x64.tar.gz"
readonly UV_VERSION="0.8.14"
readonly PYTHON_VERSION="3.13.7"
readonly BLENDER_5_VERSION="5.2.0"
readonly BLENDER_5_SHA256="96f6c181a30f4950607839dc84d42a354b250d8a0231b098b59b7bc69c351c48"
readonly BLENDER_4_VERSION="4.1.1"
readonly BLENDER_4_SHA256="ab2ea3fe991601a5e6bd2cda786ecaa919c0b39e0550e59978b5d40270c260d3"

if [[ "${RENDER_NODE_TEST_MODE:-}" == "1" ]]; then
  INSTALL_ROOT="${RENDER_NODE_INSTALL_ROOT:?}"
  STATE_ROOT="${RENDER_NODE_STATE_ROOT:?}"
  RUNTIME_ROOT="${RENDER_NODE_RUNTIME_ROOT:?}"
  MANAGEMENT_BIN="${RENDER_NODE_MANAGEMENT_BIN:?}"
  APP_USER="${RENDER_NODE_APP_USER:-$(id -un)}"
  APP_UID="$(id -u)"
else
  INSTALL_ROOT="/opt/render-node"
  STATE_ROOT="/workspace/.render-node"
  RUNTIME_ROOT="/run/render-node"
  MANAGEMENT_BIN="/usr/local/bin/render-node"
  APP_USER="rendernode"
  APP_UID="10001"
fi
readonly INSTALL_ROOT STATE_ROOT RUNTIME_ROOT MANAGEMENT_BIN APP_USER APP_UID
readonly RELEASES_ROOT="${INSTALL_ROOT}/releases"
readonly BLENDER_ROOT="${INSTALL_ROOT}/blender"
readonly CONFIG_FILE="${STATE_ROOT}/render-node.env"

public_origin="${RENDER_NODE_PUBLIC_ORIGIN:-}"
admin_username="${RENDER_NODE_ADMIN_USERNAME:-render}"
requested_version="latest"

usage() {
  cat <<'EOF'
Install, repair, or update Render Node inside an Ubuntu GPU Pod (no Docker).

Usage:
  curl -fsSL https://raw.githubusercontent.com/NikkZhur/Render-Node/master/install.sh | bash

Options:
  --origin URL       Exact public HTTPS origin (required outside RunPod)
  --username NAME    Panel username used only on first install (default: render)
  --version X.Y.Z    Install one explicit stable GitHub Release
  -h, --help         Show this help without changing the host

Without --version, every run uses the latest successfully published stable
Release bundle. Expose HTTP port 8080 before opening the panel.
EOF
}

log() { printf '[render-node] %s\n' "$*"; }
die() { printf '[render-node] ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --origin)
      (($# >= 2)) || die "--origin requires a value"
      public_origin="${2%/}"
      shift 2
      ;;
    --username)
      (($# >= 2)) || die "--username requires a value"
      admin_username="$2"
      shift 2
      ;;
    --version)
      (($# >= 2)) || die "--version requires a value"
      requested_version="${2#v}"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done

[[ "$requested_version" == "latest" || "$requested_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
  || die "--version must be a stable semantic version such as 1.2.3."
[[ "$admin_username" =~ ^[A-Za-z0-9._-]{1,64}$ ]] \
  || die "--username contains unsupported characters."

if [[ "${RENDER_NODE_TEST_MODE:-}" != "1" ]]; then
  [[ "$(id -u)" == "0" ]] || die "Run this command as root inside the Pod."
  [[ "$(uname -m)" == "x86_64" ]] || die "Only x86_64/amd64 Pods are supported."
  [[ -r /etc/os-release ]] || die "Cannot identify the operating system."
  # shellcheck source=/dev/null
  source /etc/os-release
  [[ "${ID:-}" == "ubuntu" && ("${VERSION_ID:-}" == "22.04" || "${VERSION_ID:-}" == "24.04") ]] \
    || die "Only Ubuntu 22.04 and 24.04 x86_64 Pods are supported."
fi

if [[ -z "$public_origin" && -n "${RUNPOD_POD_ID:-}" ]]; then
  public_origin="https://${RUNPOD_POD_ID}-8080.proxy.runpod.net"
fi
[[ "$public_origin" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]] \
  || die "Pass one exact HTTPS origin with --origin outside RunPod."

command -v nvidia-smi >/dev/null 2>&1 \
  || die "The Pod does not expose nvidia-smi."
nvidia-smi >/dev/null || die "nvidia-smi cannot communicate with the assigned GPU."

temporary_root="$(mktemp -d)"
staging_release=""
cleanup() {
  local status=$?
  trap - EXIT
  [[ -z "$staging_release" ]] || rm -rf -- "$staging_release"
  rm -rf -- "$temporary_root"
  exit "$status"
}
trap cleanup EXIT

release_base="https://github.com/${REPOSITORY}/releases/latest/download"
if [[ "$requested_version" != "latest" ]]; then
  release_base="https://github.com/${REPOSITORY}/releases/download/v${requested_version}"
fi
bundle="${temporary_root}/${ASSET}"
checksum="${bundle}.sha256"
log "Downloading verified ${requested_version} Release bundle..."
curl --fail --location --proto '=https' --tlsv1.2 "${release_base}/${ASSET}" -o "$bundle" \
  || die "The requested stable Release bundle is unavailable; the active version was not changed."
curl --fail --location --proto '=https' --tlsv1.2 "${release_base}/${ASSET}.sha256" -o "$checksum" \
  || die "The Release checksum is unavailable; the active version was not changed."

expected_sha=""
expected_name=""
extra_checksum_field=""
read -r expected_sha expected_name extra_checksum_field < "$checksum" || true
expected_sha="${expected_sha,,}"
expected_name="${expected_name#\*}"
[[ "$expected_sha" =~ ^[0-9a-f]{64}$ && "$expected_name" == "$ASSET" \
  && -z "$extra_checksum_field" && "$(wc -l < "$checksum")" -eq 1 ]] \
  || die "The Release checksum asset has an invalid format."
actual_sha="$(sha256sum "$bundle" | awk '{print $1}')"
[[ "$actual_sha" == "$expected_sha" ]] \
  || die "Release bundle checksum mismatch; the active version was not changed."

python3 - "$bundle" <<'PY' || exit $?
import posixpath
import sys
import tarfile

archive = sys.argv[1]
with tarfile.open(archive, "r:gz") as bundle:
    members = bundle.getmembers()
    if not members:
        raise SystemExit("Release bundle is empty.")
    for member in members:
        name = member.name
        normalized = posixpath.normpath(name)
        if name.startswith("/") or normalized == ".." or normalized.startswith("../"):
            raise SystemExit(f"Unsafe archive path: {name}")
        if normalized != name.rstrip("/") or not (
            normalized == "render-node" or normalized.startswith("render-node/")
        ):
            raise SystemExit(f"Unexpected archive path: {name}")
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise SystemExit(f"Unsupported archive entry: {name}")
        if member.issym():
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), member.linkname))
            if member.linkname.startswith("/") or not target.startswith("render-node/"):
                raise SystemExit(f"Unsafe symlink: {name}")
        if member.islnk():
            target = posixpath.normpath(member.linkname)
            if member.linkname.startswith("/") or not target.startswith("render-node/"):
                raise SystemExit(f"Unsafe hardlink: {name}")
PY

mkdir -p "$RELEASES_ROOT"
staging_release="${RELEASES_ROOT}/.staging-$$"
rm -rf -- "$staging_release"
mkdir -p "$staging_release"
tar --extract --gzip --file "$bundle" --strip-components=1 \
  --no-same-owner --no-same-permissions --directory "$staging_release"
[[ -r "$staging_release/VERSION" ]] || die "Release bundle has no VERSION file."
release_version="$(tr -d '\r\n' < "$staging_release/VERSION")"
[[ "$release_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
  || die "Release VERSION is not stable semantic version."
[[ "$requested_version" == "latest" || "$release_version" == "$requested_version" ]] \
  || die "Release VERSION does not match --version."
for required in \
  backend/app backend/migrations backend/alembic.ini backend/pyproject.toml backend/uv.lock \
  frontend/dist/index.html deploy/nginx.conf.template deploy/supervisor.sh scripts/render-node; do
  [[ -e "$staging_release/$required" ]] || die "Release bundle is missing ${required}."
done

release_dir="${RELEASES_ROOT}/${release_version}"
release_is_complete() {
  local root="$1"
  [[ -r "$root/VERSION" \
    && -d "$root/backend/app" \
    && -d "$root/backend/migrations" \
    && -r "$root/backend/alembic.ini" \
    && -r "$root/backend/pyproject.toml" \
    && -r "$root/backend/uv.lock" \
    && -x "$root/backend/.venv/bin/uvicorn" \
    && -f "$root/frontend/dist/index.html" \
    && -r "$root/deploy/nginx.conf.template" \
    && -x "$root/deploy/supervisor.sh" \
    && -x "$root/scripts/render-node" ]]
}
if release_is_complete "$release_dir"; then
  rm -rf -- "$staging_release"
  staging_release=""
fi

if [[ "${RENDER_NODE_SKIP_PACKAGES:-}" != "1" ]]; then
  log "Ensuring native runtime packages are installed..."
  export DEBIAN_FRONTEND=noninteractive
  policy_created=false
  if [[ ! -e /usr/sbin/policy-rc.d ]]; then
    printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
    chmod 0755 /usr/sbin/policy-rc.d
    policy_created=true
  fi
  apt-get update
  apt-get install -y --no-install-recommends \
    apache2-utils ca-certificates curl gettext-base nginx-light openssl tar util-linux xz-utils \
    libdbus-1-3 libegl1 libfontconfig1 libfreetype6 libgl1 libice6 libpulse0 \
    libsm6 libwayland-client0 libx11-6 libxcursor1 libxfixes3 libxi6 \
    libxinerama1 libxkbcommon0 libxrandr2 libxrender1 libxxf86vm1
  rm -rf /var/lib/apt/lists/*
  [[ "$policy_created" != true ]] || rm -f -- /usr/sbin/policy-rc.d
fi

if [[ "${RENDER_NODE_TEST_MODE:-}" != "1" ]]; then
  getent group "$APP_USER" >/dev/null || groupadd --gid "$APP_UID" "$APP_USER"
  id "$APP_USER" >/dev/null 2>&1 || useradd --uid "$APP_UID" --gid "$APP_UID" \
    --no-create-home --home-dir "$STATE_ROOT/home" --shell /usr/sbin/nologin "$APP_USER"
  [[ "$(id -u "$APP_USER")" == "$APP_UID" ]] \
    || die "Existing ${APP_USER} user has an unexpected UID."
fi

if [[ -n "$staging_release" ]]; then
  log "Creating the frozen per-release Python ${PYTHON_VERSION} environment..."
  mkdir -p "$staging_release/.tools" "$staging_release/.python"
  curl --fail --location --proto '=https' --tlsv1.2 \
    "https://astral.sh/uv/${UV_VERSION}/install.sh" -o "$temporary_root/install-uv.sh"
  env UV_INSTALL_DIR="$staging_release/.tools" UV_NO_MODIFY_PATH=1 \
    sh "$temporary_root/install-uv.sh"
  uv_bin="$staging_release/.tools/uv"
  [[ -x "$uv_bin" ]] || die "Pinned uv ${UV_VERSION} installation failed."
  export UV_PYTHON_INSTALL_DIR="$staging_release/.python"
  export UV_CACHE_DIR="$temporary_root/uv-cache"
  "$uv_bin" python install "$PYTHON_VERSION"
  (
    cd "$staging_release/backend"
    "$uv_bin" sync --frozen --no-dev --no-install-project --python "$PYTHON_VERSION"
  )
  rm -rf -- "$staging_release/.tools"
  [[ -x "$staging_release/backend/.venv/bin/uvicorn" ]] \
    || die "Release virtual environment is incomplete."
fi

install_blender() {
  local version="$1" series="$2" expected_sha="$3"
  local destination="${BLENDER_ROOT}/${version}"
  if [[ -x "$destination/blender" ]]; then
    "$destination/blender" --version | head -n 1 | grep -F "Blender ${version}" >/dev/null \
      || die "Existing bundled Blender ${version} failed verification."
    return 0
  fi
  [[ ! -e "$destination" ]] || die "Refusing to overwrite invalid ${destination}."
  local archive="blender-${version}-linux-x64.tar.xz"
  local archive_path="${temporary_root}/${archive}"
  local stage="${BLENDER_ROOT}/.staging-${version}-$$"
  log "Downloading and verifying Blender ${version}..."
  curl --fail --location --proto '=https' --tlsv1.2 \
    "https://download.blender.org/release/${series}/${archive}" -o "$archive_path"
  printf '%s  %s\n' "$expected_sha" "$archive_path" | sha256sum --check --strict
  mkdir -p "$stage"
  tar --extract --xz --file "$archive_path" --strip-components=1 \
    --no-same-owner --no-same-permissions --directory "$stage"
  [[ -x "$stage/blender" ]] || die "Blender ${version} executable is missing."
  "$stage/blender" --version | head -n 1 | grep -F "Blender ${version}" >/dev/null \
    || die "Blender ${version} reported an unexpected version."
  mv "$stage" "$destination"
}

mkdir -p "$BLENDER_ROOT"
install_blender "$BLENDER_5_VERSION" "Blender5.2" "$BLENDER_5_SHA256"
install_blender "$BLENDER_4_VERSION" "Blender4.1" "$BLENDER_4_SHA256"

if [[ "${RENDER_NODE_TEST_MODE:-}" == "1" ]]; then
  install -d -m 0750 "$STATE_ROOT" "$STATE_ROOT/home" "$STATE_ROOT/logs" "$RUNTIME_ROOT" \
    "${RENDER_NODE_WORKSPACE_ROOT:-$temporary_root/workspace}/database" \
    "${RENDER_NODE_WORKSPACE_ROOT:-$temporary_root/workspace}/jobs" \
    "${RENDER_NODE_WORKSPACE_ROOT:-$temporary_root/workspace}/blender/versions"
  workspace_root="${RENDER_NODE_WORKSPACE_ROOT:-$temporary_root/workspace}"
else
  install -d -m 0750 -o "$APP_UID" -g "$APP_UID" \
    "$STATE_ROOT" "$STATE_ROOT/home" "$STATE_ROOT/logs" "$RUNTIME_ROOT" \
    /workspace/database /workspace/jobs /workspace/blender/versions \
    /workspace/blender/downloads /workspace/blender/quarantine
  workspace_root="/workspace"
fi

if [[ -e "$MANAGEMENT_BIN" && ! -L "$MANAGEMENT_BIN" ]]; then
  die "Refusing to overwrite unrelated ${MANAGEMENT_BIN}."
fi
if [[ -L "$MANAGEMENT_BIN" ]]; then
  [[ "$(readlink "$MANAGEMENT_BIN")" == "${INSTALL_ROOT}/current/scripts/render-node" ]] \
    || die "Refusing to overwrite unrelated ${MANAGEMENT_BIN}."
else
  mkdir -p "$(dirname "$MANAGEMENT_BIN")"
  ln -s "${INSTALL_ROOT}/current/scripts/render-node" "$MANAGEMENT_BIN"
fi

if [[ -r "$STATE_ROOT/credentials" && -r "$CONFIG_FILE" && -r "$STATE_ROOT/admin.htpasswd" ]]; then
  saved_username="$(sed -n 's/^USERNAME=//p' "$STATE_ROOT/credentials" | head -n 1)"
  saved_password="$(sed -n 's/^PASSWORD=//p' "$STATE_ROOT/credentials" | head -n 1)"
  auth_token="$(sed -n 's/^RENDER_NODE_AUTH_TOKEN=//p' "$CONFIG_FILE" | head -n 1)"
  [[ -n "$saved_username" && -n "$saved_password" && "$auth_token" =~ ^[A-Za-z0-9_-]{32,128}$ ]] \
    || die "Persistent credentials are malformed; refusing to replace them."
  admin_username="$saved_username"
  admin_password="$saved_password"
else
  if [[ -e "$STATE_ROOT/credentials" || -e "$CONFIG_FILE" || -e "$STATE_ROOT/admin.htpasswd" ]]; then
    die "Persistent credential state is incomplete; refusing to replace existing secrets."
  fi
  auth_token="$(openssl rand -hex 32)"
  admin_password="$(openssl rand -hex 12)"
  umask 077
  printf '%s\n' "$admin_password" \
    | htpasswd -iBC 12 -c "$STATE_ROOT/admin.htpasswd" "$admin_username" >/dev/null
fi
[[ "$(wc -l < "$STATE_ROOT/admin.htpasswd")" -eq 1 ]] \
  && grep -Eq '^[A-Za-z0-9._-]{1,64}:\$2[aby]\$' "$STATE_ROOT/admin.htpasswd" \
  || die "Persistent htpasswd must contain one bcrypt account."

old_origin="$(sed -n 's/^RENDER_NODE_ALLOWED_ORIGINS=//p' "$CONFIG_FILE" 2>/dev/null | head -n 1 || true)"
umask 077
cat > "$CONFIG_FILE" <<EOF
RENDER_NODE_RELEASE_ROOT=${INSTALL_ROOT}/current
RENDER_NODE_STATE_ROOT=${STATE_ROOT}
RENDER_NODE_RUNTIME_ROOT=${RUNTIME_ROOT}
RENDER_NODE_INSTALLED_VERSION=${release_version}
RENDER_NODE_ENV=production
RENDER_NODE_DEPLOYMENT_PROFILE=single_tenant
RENDER_NODE_RUNNER_MODE=local_trusted
RENDER_NODE_RENDER_SCHEDULER_ENABLED=true
RENDER_NODE_AUTH_TOKEN=${auth_token}
RENDER_NODE_ALLOWED_ORIGINS=${public_origin}
RENDER_NODE_WORKSPACE=${workspace_root}
RENDER_NODE_DATABASE_URL=sqlite+aiosqlite:////${workspace_root#/}/database/render-node.sqlite3
RENDER_NODE_BUNDLED_BLENDER_ROOT=${BLENDER_ROOT}
EOF
while IFS= read -r accelerator_name; do
  case "$accelerator_name" in
    CUDA_*|NVIDIA_*|LD_LIBRARY_PATH)
      printf '%s=%q\n' "$accelerator_name" "${!accelerator_name}" >> "$CONFIG_FILE"
      ;;
  esac
done < <(compgen -e)
if [[ "${RENDER_NODE_TEST_MODE:-}" == "1" ]]; then
  printf 'RENDER_NODE_READY_ATTEMPTS=%s\n' "${RENDER_NODE_READY_ATTEMPTS:-2}" >> "$CONFIG_FILE"
fi
cat > "$STATE_ROOT/credentials" <<EOF
URL=${public_origin}
USERNAME=${admin_username}
PASSWORD=${admin_password}
EOF
chmod 0640 "$CONFIG_FILE" "$STATE_ROOT/admin.htpasswd"
chmod 0600 "$STATE_ROOT/credentials"
if [[ "${RENDER_NODE_TEST_MODE:-}" != "1" ]]; then
  chown "$APP_UID:$APP_UID" "$CONFIG_FILE" "$STATE_ROOT/admin.htpasswd"
  chown -R "$APP_UID:$APP_UID" "$STATE_ROOT/home" "$STATE_ROOT/logs" "$RUNTIME_ROOT"
fi

management_env=()
if [[ "${RENDER_NODE_TEST_MODE:-}" == "1" ]]; then
  management_env=(
    RENDER_NODE_TEST_MODE=1
    RENDER_NODE_INSTALL_ROOT="$INSTALL_ROOT"
    RENDER_NODE_STATE_ROOT="$STATE_ROOT"
    RENDER_NODE_RUNTIME_ROOT="$RUNTIME_ROOT"
    RENDER_NODE_MANAGEMENT_BIN="$MANAGEMENT_BIN"
    RENDER_NODE_APP_USER="$APP_USER"
    RENDER_NODE_TEST_BIN="${RENDER_NODE_TEST_BIN:-}"
  )
fi

previous_target=""
if [[ -L "$INSTALL_ROOT/current" ]]; then
  previous_target="$(readlink -f "$INSTALL_ROOT/current" 2>/dev/null || true)"
  [[ "$previous_target" == "$RELEASES_ROOT/"* ]] || previous_target=""
fi
broken_release=""
prepared_new_release=false
if release_is_complete "$release_dir"; then
  [[ -z "$staging_release" ]] || rm -rf -- "$staging_release"
  staging_release=""
else
  if [[ -e "$release_dir" ]]; then
    broken_release="${RELEASES_ROOT}/.broken-${release_version}-$$"
    [[ -z "$staging_release" ]] && die "Verified repair release is unavailable."
    env "${management_env[@]}" "$staging_release/scripts/render-node" stop \
      >/dev/null 2>&1 || true
    mv "$release_dir" "$broken_release"
  fi
  mv "$staging_release" "$release_dir"
  staging_release=""
  prepared_new_release=true
fi

current_target="$(readlink -f "$INSTALL_ROOT/current" 2>/dev/null || true)"
switched=false
if [[ "$current_target" != "$release_dir" ]]; then
  env "${management_env[@]}" "$MANAGEMENT_BIN" stop >/dev/null 2>&1 || true
  new_link="${INSTALL_ROOT}/.current-$$"
  ln -s "releases/${release_version}" "$new_link"
  mv -Tf "$new_link" "$INSTALL_ROOT/current"
  switched=true
fi

start_failed=false
if [[ "$switched" == true || "$old_origin" != "$public_origin" || -n "$broken_release" ]]; then
  env "${management_env[@]}" "$MANAGEMENT_BIN" restart || start_failed=true
else
  env "${management_env[@]}" "$MANAGEMENT_BIN" start || start_failed=true
fi

if [[ "$start_failed" == true ]]; then
  env "${management_env[@]}" "$MANAGEMENT_BIN" stop >/dev/null 2>&1 || true
  if [[ -n "$broken_release" ]]; then
    failed_release="${RELEASES_ROOT}/.failed-${release_version}-$$"
    mv "$release_dir" "$failed_release"
    mv "$broken_release" "$release_dir"
    rm -rf -- "$failed_release"
  elif [[ "$prepared_new_release" == true ]]; then
    rm -rf -- "$release_dir"
  fi
  if [[ -n "$previous_target" && -d "$previous_target" ]]; then
    rollback_link="${INSTALL_ROOT}/.rollback-$$"
    ln -s "releases/$(basename "$previous_target")" "$rollback_link"
    mv -Tf "$rollback_link" "$INSTALL_ROOT/current"
    sed -i "s/^RENDER_NODE_INSTALLED_VERSION=.*/RENDER_NODE_INSTALLED_VERSION=$(basename "$previous_target")/" \
      "$CONFIG_FILE"
    env "${management_env[@]}" "$MANAGEMENT_BIN" start \
      || die "The update failed and the previous release could not be restarted."
  else
    rm -f -- "$INSTALL_ROOT/current"
  fi
  die "The new release failed readiness; the previous release was restored."
fi

rm -rf -- "$broken_release"
active_target="$(readlink -f "$INSTALL_ROOT/current")"
for candidate in "$RELEASES_ROOT"/*; do
  [[ -d "$candidate" ]] || continue
  [[ "$candidate" == "$active_target" || "$candidate" == "$previous_target" ]] || rm -rf -- "$candidate"
done

log "Render Node ${release_version} is ready."
cat <<EOF

  URL:      ${public_origin}
  Username: ${admin_username}
  Password: ${admin_password}

Credentials are stored root-only in ${STATE_ROOT}/credentials.
Manage the service with: render-node status|logs|restart|stop|start|credentials|uninstall
EOF
