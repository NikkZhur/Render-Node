# Render Node deployment

Render Node is a single-tenant appliance for one operator's own trusted scenes.
It is not a security boundary for a shared render farm.

## Ubuntu GPU Pod: one-line installer

Rent an Ubuntu 22.04 or 24.04 x86_64 Pod with an NVIDIA GPU, expose HTTP port
`8080`, open its terminal as root, and run:

```bash
curl -fsSL https://raw.githubusercontent.com/NikkZhur/Render-Node/master/install.sh | bash
```

The unchanged command is install, repair, and update. On every run it downloads
`render-node-linux-x64.tar.gz` and its checksum from the latest stable GitHub
Release, validates the checksum and archive links/paths, and prepares a new
release away from the active one. It never downloads a source archive or builds
the frontend on the Pod. To select one stable release explicitly:

```bash
curl -fsSL https://raw.githubusercontent.com/NikkZhur/Render-Node/master/install.sh \
  | bash -s -- --version 1.2.3
```

RunPod origin is derived from `RUNPOD_POD_ID`. Outside RunPod, pass one exact
HTTPS origin with `--origin https://pod.example.com`. Only Ubuntu 22.04/24.04,
x86_64, root, and a working `nvidia-smi` are accepted. Debian, ordinary VM,
Compose, Caddy, nested Docker, and systemd flows are intentionally unsupported.

The installer creates a frozen per-release Python 3.13.7 environment with
pinned `uv`, installs verified Blender 5.2.0 and 4.1.1 from the official Blender
archive, and starts Nginx plus FastAPI as UID 10001. It waits for `/ready` through
port 8080. An update switches `/opt/render-node/current` atomically only after
the new release is complete; failed readiness restores and restarts the previous
release. The active and one previous release are retained.

Paths:

| Path | Purpose |
|---|---|
| `/opt/render-node/releases/<version>` | Immutable application and per-release Python environment |
| `/opt/render-node/current` | Symlink to the active release |
| `/opt/render-node/blender/<version>` | Bundled Blender 5.2.0 and 4.1.1 |
| `/workspace/.render-node` | Persistent private config, credentials and bounded logs |
| `/workspace/database` | Persistent SQLite database |
| `/workspace/jobs` | Scenes, raw render logs and results |
| `/workspace/blender` | Additional user-installed Blender runtimes |
| `/run/render-node` | Ephemeral PID and generated Nginx runtime files |

Credentials are generated only on the first install. Reinstall, repair, update,
Pod-ID changes, and loss of `/opt/render-node` preserve the password, backend
token, database, jobs, and additional Blender runtimes. A changed Pod ID updates
the saved URL and backend allowed origin. The password is passed to `htpasswd`
on stdin; cloud secrets and the plaintext password are absent from backend child
environments.

Manage the native processes without systemd:

```bash
render-node start
render-node stop
render-node restart
render-node status
render-node logs
render-node credentials
render-node uninstall
```

PID identity includes the process start time and expected supervisor command,
so a stale or reused PID cannot stop another process. `uninstall` removes the
application and private service config while preserving database, jobs, and
additional Blender runtimes.

## Prepared GHCR image: alternative

RunPod may directly start `ghcr.io/nikkzhur/render-node:latest`. Dockerfile is a
GitHub Actions build recipe; no Docker daemon runs inside the Pod. Expose HTTP
port `8080`, mount persistent storage at `/workspace`, assign NVIDIA GPUs, and
set:

```dotenv
RENDER_NODE_AUTH_TOKEN=<32-128 URL-safe random characters>
RENDER_NODE_ALLOWED_ORIGINS=https://<exact-platform-proxy-origin>
RENDER_NODE_ADMIN_USERNAME=render
RENDER_NODE_ADMIN_PASSWORD=<unique-password-with-at-least-12-characters>
```

The image uses the same Nginx template and supervisor as the native release.
Its short entrypoint prepares writable state, converts the password to bcrypt,
clears the inherited environment, and starts all long-lived processes as UID
10001. Port 8000 remains loopback-only; Nginx requires Basic Auth and replaces
it with the private Bearer token for API, WebSocket, and download requests.

## Publication

`.github/workflows/release.yml` runs backend Ruff, strict mypy and full pytest,
then frontend lint and production build. The single publish job depends on both
check jobs. A stable `vX.Y.Z` tag creates an explicit non-draft,
non-prerelease GitHub Release with the bundle and `.sha256`, then publishes the
matching versioned `linux/amd64` GHCR image. `master` publishes `latest` and a
commit-SHA image only after the same checks; it does not create a GitHub Release.

Build a local bundle after `npm --prefix frontend run build` with:

```bash
scripts/build-release-bundle.sh 1.2.3 dist
```

The bundle manifest includes only backend runtime files, migrations, frozen
lock metadata, built frontend files, and deployment scripts. It excludes source
frontend, tests, caches, `.venv`, `node_modules`, Blender binaries, uploads, and
secrets.
