#!/usr/bin/env bash
set -Eeuo pipefail

readonly ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="${1:-}"
output_dir="${2:-${ROOT}/dist}"

[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo "Usage: $0 X.Y.Z [OUTPUT_DIRECTORY]" >&2
  exit 2
}
[[ -f "${ROOT}/frontend/dist/index.html" ]] || {
  echo "frontend/dist is missing; run the production frontend build first." >&2
  exit 1
}

staging="$(mktemp -d)"
trap 'rm -rf -- "$staging"' EXIT
bundle_root="${staging}/render-node"
mkdir -p \
  "${bundle_root}/backend" \
  "${bundle_root}/frontend" \
  "${bundle_root}/deploy" \
  "${bundle_root}/scripts"

printf '%s\n' "$version" > "${bundle_root}/VERSION"
cp -a "${ROOT}/backend/app" "${bundle_root}/backend/app"
cp -a "${ROOT}/backend/migrations" "${bundle_root}/backend/migrations"
cp "${ROOT}/backend/alembic.ini" "${ROOT}/backend/pyproject.toml" \
  "${ROOT}/backend/uv.lock" "${bundle_root}/backend/"
cp -a "${ROOT}/frontend/dist" "${bundle_root}/frontend/dist"
cp "${ROOT}/deploy/nginx.conf.template" "${bundle_root}/deploy/"
install -m 0755 "${ROOT}/deploy/supervisor.sh" "${bundle_root}/deploy/supervisor.sh"
install -m 0755 "${ROOT}/scripts/render-node" "${bundle_root}/scripts/render-node"

find "$bundle_root" -type d -name __pycache__ -prune -exec rm -rf -- {} +
find "$bundle_root" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
find "$bundle_root" -type d -exec chmod 0755 {} +
find "$bundle_root" -type f -not -path '*/scripts/render-node' \
  -not -path '*/deploy/supervisor.sh' -exec chmod 0644 {} +

mkdir -p "$output_dir"
archive="${output_dir}/render-node-linux-x64.tar.gz"
epoch="${SOURCE_DATE_EPOCH:-0}"
tar --sort=name --mtime="@${epoch}" --owner=0 --group=0 --numeric-owner \
  --format=posix --pax-option=delete=atime,delete=ctime \
  -C "$staging" -czf "$archive" render-node
(
  cd "$output_dir"
  sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256"
)
printf '%s\n' "$archive"
