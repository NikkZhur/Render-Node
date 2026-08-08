#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_USER="rendernode"
readonly APP_UID="10001"
readonly RELEASE_ROOT="/opt/render-node/current"
readonly STATE_ROOT="${RENDER_NODE_STATE_ROOT:-/workspace/.render-node}"
readonly RUNTIME_ROOT="${RENDER_NODE_RUNTIME_ROOT:-/run/render-node}"
readonly CONFIG_FILE="${STATE_ROOT}/render-node.env"

: "${RENDER_NODE_AUTH_TOKEN:?RENDER_NODE_AUTH_TOKEN is required}"
: "${RENDER_NODE_ALLOWED_ORIGINS:?RENDER_NODE_ALLOWED_ORIGINS is required}"
[[ "$RENDER_NODE_AUTH_TOKEN" =~ ^[A-Za-z0-9_-]{32,128}$ ]] \
  || { echo "RENDER_NODE_AUTH_TOKEN is invalid." >&2; exit 1; }
[[ "$RENDER_NODE_ALLOWED_ORIGINS" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]] \
  || { echo "RENDER_NODE_ALLOWED_ORIGINS must be one exact HTTPS origin." >&2; exit 1; }

if [[ "$(id -u)" == "0" ]]; then
  install -d -m 0750 -o "$APP_UID" -g "$APP_UID" \
    "$STATE_ROOT" "$STATE_ROOT/home" "$STATE_ROOT/logs" "$RUNTIME_ROOT" \
    /workspace/database /workspace/jobs \
    /workspace/blender/versions /workspace/blender/downloads \
    /workspace/blender/quarantine

  umask 077
  if [[ -n "${RENDER_NODE_ADMIN_HTPASSWD_B64:-}" ]]; then
    printf '%s' "$RENDER_NODE_ADMIN_HTPASSWD_B64" | base64 --decode \
      > "$STATE_ROOT/admin.htpasswd"
  else
    : "${RENDER_NODE_ADMIN_PASSWORD:?Set RENDER_NODE_ADMIN_PASSWORD or RENDER_NODE_ADMIN_HTPASSWD_B64}"
    admin_username="${RENDER_NODE_ADMIN_USERNAME:-render}"
    [[ "$admin_username" =~ ^[A-Za-z0-9._-]{1,64}$ ]] \
      || { echo "RENDER_NODE_ADMIN_USERNAME is invalid." >&2; exit 1; }
    [[ ${#RENDER_NODE_ADMIN_PASSWORD} -ge 12 ]] \
      || { echo "RENDER_NODE_ADMIN_PASSWORD must contain at least 12 characters." >&2; exit 1; }
    [[ "$RENDER_NODE_ADMIN_PASSWORD" != *$'\n'* && "$RENDER_NODE_ADMIN_PASSWORD" != *$'\r'* ]] \
      || { echo "RENDER_NODE_ADMIN_PASSWORD must not contain newlines." >&2; exit 1; }
    printf '%s\n' "$RENDER_NODE_ADMIN_PASSWORD" \
      | htpasswd -iBC 12 -c "$STATE_ROOT/admin.htpasswd" "$admin_username" >/dev/null
  fi
  unset RENDER_NODE_ADMIN_PASSWORD RENDER_NODE_ADMIN_HTPASSWD_B64 RUNPOD_API_KEY
  [[ "$(wc -l < "$STATE_ROOT/admin.htpasswd")" -eq 1 ]] \
    && grep -Eq '^[A-Za-z0-9._-]{1,64}:\$2[aby]\$' "$STATE_ROOT/admin.htpasswd" \
    || { echo "The htpasswd file must contain one bcrypt account." >&2; exit 1; }

  cat > "$CONFIG_FILE" <<EOF
RENDER_NODE_RELEASE_ROOT=${RELEASE_ROOT}
RENDER_NODE_STATE_ROOT=${STATE_ROOT}
RENDER_NODE_RUNTIME_ROOT=${RUNTIME_ROOT}
RENDER_NODE_ENV=production
RENDER_NODE_DEPLOYMENT_PROFILE=single_tenant
RENDER_NODE_RUNNER_MODE=local_trusted
RENDER_NODE_RENDER_SCHEDULER_ENABLED=true
RENDER_NODE_AUTH_TOKEN=${RENDER_NODE_AUTH_TOKEN}
RENDER_NODE_ALLOWED_ORIGINS=${RENDER_NODE_ALLOWED_ORIGINS}
RENDER_NODE_WORKSPACE=/workspace
RENDER_NODE_DATABASE_URL=sqlite+aiosqlite:////workspace/database/render-node.sqlite3
RENDER_NODE_BUNDLED_BLENDER_ROOT=/opt/render-node/blender
EOF
  while IFS= read -r accelerator_name; do
    case "$accelerator_name" in
      CUDA_*|NVIDIA_*|LD_LIBRARY_PATH)
        printf '%s=%q\n' "$accelerator_name" "${!accelerator_name}" >> "$CONFIG_FILE"
        ;;
    esac
  done < <(compgen -e)
  chown "$APP_UID:$APP_UID" "$CONFIG_FILE" "$STATE_ROOT/admin.htpasswd"
  chmod 0640 "$CONFIG_FILE" "$STATE_ROOT/admin.htpasswd"

  exec env -i \
    PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin \
    HOME="$STATE_ROOT/home" LANG="${LANG:-C.UTF-8}" \
    gosu "$APP_USER" "$RELEASE_ROOT/deploy/supervisor.sh" "$CONFIG_FILE"
fi

[[ "$(id -u)" == "$APP_UID" ]] \
  || { echo "Render Node must run as root or UID ${APP_UID}." >&2; exit 1; }
exec env -i PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin \
  HOME="$STATE_ROOT/home" LANG="${LANG:-C.UTF-8}" \
  "$RELEASE_ROOT/deploy/supervisor.sh" "$CONFIG_FILE"
