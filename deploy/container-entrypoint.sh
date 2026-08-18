#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_USER="rendernode"
readonly APP_UID="10001"
readonly RELEASE_ROOT="/opt/render-node/current"
readonly STATE_ROOT="${RENDER_NODE_STATE_ROOT:-/workspace/.render-node}"
readonly RUNTIME_ROOT="${RENDER_NODE_RUNTIME_ROOT:-/run/render-node}"
readonly CONFIG_FILE="${STATE_ROOT}/render-node.env"

: "${RENDER_NODE_AUTH_TOKEN:?RENDER_NODE_AUTH_TOKEN is required}"
[[ "$RENDER_NODE_AUTH_TOKEN" =~ ^[A-Za-z0-9_-]{32,128}$ ]] \
  || { echo "RENDER_NODE_AUTH_TOKEN is invalid." >&2; exit 1; }

if [[ "$(id -u)" == "0" ]]; then
  install -d -m 0750 -o "$APP_UID" -g "$APP_UID" \
    "$STATE_ROOT" "$STATE_ROOT/home" "$STATE_ROOT/logs" "$RUNTIME_ROOT" \
    /workspace/database /workspace/jobs \
    /workspace/blender/versions /workspace/blender/downloads \
    /workspace/blender/quarantine

  # shellcheck source=/opt/render-node/cgroup-delegation.sh
  source /opt/render-node/cgroup-delegation.sh
  prepare_render_node_cgroup "$APP_UID"

  # These exact paths belong to the removed Basic Auth deployment contract.
  rm -f -- "$STATE_ROOT/admin.htpasswd" "$STATE_ROOT/credentials"
  umask 077
  unset RUNPOD_API_KEY

  cat > "$CONFIG_FILE" <<EOF
RENDER_NODE_RELEASE_ROOT=${RELEASE_ROOT}
RENDER_NODE_STATE_ROOT=${STATE_ROOT}
RENDER_NODE_RUNTIME_ROOT=${RUNTIME_ROOT}
RENDER_NODE_ENV=production
RENDER_NODE_RESOURCE_ISOLATION_MODE=strict
RENDER_NODE_RENDER_SCHEDULER_ENABLED=true
RENDER_NODE_AUTH_TOKEN=${RENDER_NODE_AUTH_TOKEN}
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
  chown "$APP_UID:$APP_UID" "$CONFIG_FILE"
  chmod 0640 "$CONFIG_FILE"

  exec env -i \
    PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin \
    HOME="$STATE_ROOT/home" LANG="${LANG:-C.UTF-8}" \
    gosu "$APP_USER" "$RELEASE_ROOT/deploy/supervisor.sh" "$CONFIG_FILE"
fi

[[ "$(id -u)" == "$APP_UID" ]] \
  || { echo "Render Node must run as root or UID ${APP_UID}." >&2; exit 1; }
rm -f -- "$STATE_ROOT/admin.htpasswd" "$STATE_ROOT/credentials"
exec env -i PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin \
  HOME="$STATE_ROOT/home" LANG="${LANG:-C.UTF-8}" \
  "$RELEASE_ROOT/deploy/supervisor.sh" "$CONFIG_FILE"
