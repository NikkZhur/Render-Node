#!/usr/bin/env bash
set -Eeuo pipefail

readonly CONFIG_FILE="${1:-/workspace/.render-node/render-node.env}"
[[ -r "$CONFIG_FILE" ]] || {
  echo "Render Node config is not readable: ${CONFIG_FILE}" >&2
  exit 1
}

set -a
# shellcheck source=/dev/null
source "$CONFIG_FILE"
set +a

: "${RENDER_NODE_RELEASE_ROOT:?RENDER_NODE_RELEASE_ROOT is required}"
: "${RENDER_NODE_STATE_ROOT:?RENDER_NODE_STATE_ROOT is required}"
: "${RENDER_NODE_RUNTIME_ROOT:?RENDER_NODE_RUNTIME_ROOT is required}"
: "${RENDER_NODE_AUTH_TOKEN:?RENDER_NODE_AUTH_TOKEN is required}"

readonly BACKEND_ROOT="${RENDER_NODE_RELEASE_ROOT}/backend"
readonly FRONTEND_ROOT="${RENDER_NODE_RELEASE_ROOT}/frontend/dist"
readonly VENV_BIN="${BACKEND_ROOT}/.venv/bin"
readonly NGINX_CONFIG="${RENDER_NODE_RUNTIME_ROOT}/nginx.conf"
readonly SERVICE_LOG="${RENDER_NODE_STATE_ROOT}/logs/service.log"
readonly TEST_BIN="${RENDER_NODE_TEST_BIN:-}"

[[ -x "${VENV_BIN}/uvicorn" ]] || {
  echo "Release virtual environment is incomplete." >&2
  exit 1
}
[[ -f "${FRONTEND_ROOT}/index.html" ]] || {
  echo "Release frontend is incomplete." >&2
  exit 1
}

mkdir -p \
  "${RENDER_NODE_STATE_ROOT}/logs" \
  "${RENDER_NODE_STATE_ROOT}/home" \
  "${RENDER_NODE_RUNTIME_ROOT}/nginx-client-body" \
  "${RENDER_NODE_RUNTIME_ROOT}/nginx-proxy"

export RENDER_NODE_FRONTEND_ROOT="$FRONTEND_ROOT"
envsubst '${RENDER_NODE_AUTH_TOKEN} ${RENDER_NODE_FRONTEND_ROOT} ${RENDER_NODE_STATE_ROOT} ${RENDER_NODE_RUNTIME_ROOT}' \
  < "${RENDER_NODE_RELEASE_ROOT}/deploy/nginx.conf.template" \
  > "$NGINX_CONFIG"
chmod 0600 "$NGINX_CONFIG"

runtime_path="${VENV_BIN}:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin"
if [[ -n "$TEST_BIN" && "${RENDER_NODE_TEST_MODE:-}" == "1" ]]; then
  runtime_path="${TEST_BIN}:${runtime_path}"
fi

runtime_env=(
  "PATH=${runtime_path}"
  "HOME=${RENDER_NODE_STATE_ROOT}/home"
  "LANG=${LANG:-C.UTF-8}"
  "LC_ALL=${LC_ALL:-C.UTF-8}"
  "SSL_CERT_FILE=${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}"
  "PYTHONUNBUFFERED=1"
)
while IFS= read -r name; do
  case "$name" in
    RENDER_NODE_*|CUDA_*|NVIDIA_*|LD_LIBRARY_PATH)
      case "$name" in
        RENDER_NODE_ADMIN_PASSWORD|RENDER_NODE_ADMIN_HTPASSWD_B64|RENDER_NODE_TEST_BIN|RENDER_NODE_TEST_MODE)
          ;;
        *) runtime_env+=("${name}=${!name}") ;;
      esac
      ;;
  esac
done < <(compgen -e)

cd "$BACKEND_ROOT"
env -i "${runtime_env[@]}" "${VENV_BIN}/alembic" upgrade head
env -i "${runtime_env[@]}" "${VENV_BIN}/uvicorn" \
  app.main:create_app --factory --host 127.0.0.1 --port 8000 &
backend_pid=$!
gateway_pid=""
log_limiter_pid=""

limit_logs() {
  while sleep 60; do
    for path in "$SERVICE_LOG" "${RENDER_NODE_STATE_ROOT}/logs/nginx-error.log"; do
      [[ -f "$path" ]] || continue
      size="$(wc -c < "$path")"
      if ((size > 10485760)); then
        tail -c 5242880 "$path" > "${RENDER_NODE_RUNTIME_ROOT}/log-tail.tmp"
        cp "${RENDER_NODE_RUNTIME_ROOT}/log-tail.tmp" "$path"
        rm -f -- "${RENDER_NODE_RUNTIME_ROOT}/log-tail.tmp"
      fi
    done
  done
}
limit_logs &
log_limiter_pid=$!

shutdown() {
  local status="${1:-0}"
  trap - INT TERM EXIT
  [[ -z "$gateway_pid" ]] || kill -TERM "$gateway_pid" 2>/dev/null || true
  [[ -z "$log_limiter_pid" ]] || kill -TERM "$log_limiter_pid" 2>/dev/null || true
  kill -TERM "$backend_pid" 2>/dev/null || true
  [[ -z "$gateway_pid" ]] || wait "$gateway_pid" 2>/dev/null || true
  [[ -z "$log_limiter_pid" ]] || wait "$log_limiter_pid" 2>/dev/null || true
  wait "$backend_pid" 2>/dev/null || true
  exit "$status"
}
trap 'shutdown 0' INT TERM
trap 'shutdown $?' EXIT

for ((attempt = 0; attempt < ${RENDER_NODE_READY_ATTEMPTS:-120}; attempt++)); do
  kill -0 "$backend_pid" 2>/dev/null || {
    wait "$backend_pid"
    exit $?
  }
  if env -i PATH="$runtime_path" curl --fail --silent \
    http://127.0.0.1:8000/ready >/dev/null; then
    break
  fi
  sleep 0.5
done
env -i PATH="$runtime_path" curl --fail --silent \
  http://127.0.0.1:8000/ready >/dev/null \
  || { echo "Backend did not become ready within 60 seconds." >&2; exit 1; }

env -i PATH="$runtime_path" nginx -c "$NGINX_CONFIG" -g 'daemon off;' &
gateway_pid=$!
set +e
wait -n "$backend_pid" "$gateway_pid"
status=$?
set -e
shutdown "$status"
