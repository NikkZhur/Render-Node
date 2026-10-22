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

No public URL or provider-specific variable is required. Open the panel through
the HTTPS address that the provider assigns to exposed port 8080. The same
installer works when a Pod ID or hosting domain changes. Only Ubuntu
22.04/24.04, x86_64, root, and a working `nvidia-smi` are accepted. Debian,
Compose, nested Docker, and systemd flows are intentionally unsupported.

The installer creates a frozen per-release Python 3.13.7 environment with
pinned `uv`, installs verified Blender 5.2.0 and 4.1.1 from the official Blender
archive, and starts Nginx plus FastAPI as UID 10001. It waits for `/ready` through
port 8080. An update switches `/opt/render-node/current` atomically only after
the new release is complete; failed readiness restores and restarts the previous
release. The active and one previous release are retained.

Before dropping privileges, `render-node start` detects the caller's cgroup v2
mount/current path, enables delegated `memory` and `pids` controllers when the
host permits it, and gives UID 10001 only `<current-cgroup>/render-node`. The
backend then creates one child cgroup per job. If the host exposes only cgroup v1
or does not delegate writable v2 controllers, installation and HTTP readiness
remain available, but strict runner diagnostics reject Start/Retry. Do not make
the host cgroup tree broadly writable; delegate only the service subtree.

Paths:

| Path | Purpose |
|---|---|
| `/opt/render-node/releases/<version>` | Immutable application and per-release Python environment |
| `/opt/render-node/current` | Symlink to the active release |
| `/opt/render-node/blender/<version>` | Bundled Blender 5.2.0 and 4.1.1 |
| `/workspace/.render-node` | Persistent private config and bounded logs |
| `/workspace/database` | Persistent SQLite database |
| `/workspace/jobs` | Scenes, raw render logs and results |
| `/workspace/blender` | Additional user-installed Blender runtimes |
| `/run/render-node` | Ephemeral PID and generated Nginx runtime files |

The internal Bearer token is generated only on first install. Reinstall, repair,
update, hosting changes, and loss of `/opt/render-node` preserve that token,
database, jobs, and additional Blender runtimes. No public URL, username, or
password is deployment state. A successful update removes only the exact legacy
`/workspace/.render-node/credentials` and `admin.htpasswd` files; it waits until
readiness succeeds so an older release can still be rolled back safely.

The first browser to open the panel claims the backend process automatically.
This is a deliberate TOFU race, not identity verification. Additional devices
use the five-minute, one-use fragment link created by **Pair another device**;
five browser sessions are allowed. Browser sessions are in memory and disappear
on backend restart. If no paired browser remains, run `render-node reset-access`.
It calls the loopback backend with the private Bearer through curl stdin, does
not expose the token in argv/output, does not restart the service, and does not
change jobs or Blender operations.

Manage the native processes without systemd:

```bash
render-node start
render-node stop
render-node restart
render-node status
render-node logs
render-node reset-access
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
```

The image uses the same Nginx template and supervisor as the native release.
Its short entrypoint prepares writable state, removes the two known legacy Basic
Auth files, clears the inherited environment, and starts all long-lived
processes as UID 10001. Port 8000 remains loopback-only. Public Nginx serves the
SPA and proxies API/WebSocket without Basic Auth or Bearer injection. Browser
access uses the same first-browser pairing cookie contract as native install.
A valid browser `Origin` is strictly parsed but not compared with upstream
`Host`, so an outer provider proxy may rewrite `Host`; forwarded headers are not
trusted. No provider/domain configuration is needed. Frontend and API must share
one public HTTPS origin, and port 8000 must never be published.

Production configuration sets
`RENDER_NODE_RESOURCE_ISOLATION_MODE=strict`. A root-started image entrypoint
attempts the same narrow cgroup v2 delegation before switching to UID 10001. A
platform that starts the image directly as UID 10001 must pre-create/delegate
the subtree instead. Read-only cgroups, cgroup v1, or missing `memory`/`pids`
controllers leave rendering fail-closed; the capabilities endpoint contains the
reason. The local-only `trusted` fallback is intentionally rejected in
production.

`RENDER_NODE_WORKER_MEMORY_GB` maps to actual `memory.max`,
`RENDER_NODE_WORKER_SWAP_GB` to `memory.swap.max` when available, and
`RENDER_NODE_WORKER_PIDS_LIMIT` to `pids.max`. `memory.oom.group=1` and
`memory.events` are used when supported. This limits host RAM/swap/processes,
not GPU VRAM; VRAM errors come from Blender/CUDA logs.

## Publication

`.github/workflows/release.yml` runs backend Ruff, strict mypy and full pytest,
then frontend lint, all Node unit suites (`npm test`), and production build. The
single publish job depends on both
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

Backend deployment tests run the installer and management command with temporary
paths and fake external commands. The bundle test stages runtime sources and a
small static frontend fixture in a temporary checkout, so backend checks do not
depend on a pre-existing `frontend/dist`. The production build and real bundle
assembly are separate release checks. These tests do not verify an actual Ubuntu
installation, GPU runtime, or delegated kernel cgroup v2.
