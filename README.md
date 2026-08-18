# Render Node

A single-node service for remote Blender rendering through a web browser. Users
upload a scene, choose a Blender version and compute resources, start a render,
and monitor jobs, logs, frames, and hardware status.

The primary deployment is a temporary single-tenant Ubuntu GPU Pod: its owner
installs the latest verified stable Release bundle with one command, renders
their own scenes, downloads the results, and deletes the node. A prepared GHCR
image is an alternative. The public API remains suitable for a separately
isolated worker if a shared render service is needed later.

> [!IMPORTANT]
> The repository now contains the persistent Job API, safe scene uploads, the
> Blender version manager, single-process render scheduler, realtime events,
> persistent result artifacts/previews, and system metrics.

## What Works Today

- responsive desktop and mobile interface;
- job setup for scene, render engine, compute device, frames, and GPUs;
- all available GPUs selected by default;
- persistent Job list and explicit lifecycle state machine;
- bounded `.blend`/ZIP uploads with contained server-generated paths;
- backend-owned Blender commands with automatic Python disabled;
- one active render process, FIFO queueing, persisted progress, timeout, restart
  recovery, and process-group cancellation;
- per-job cgroup v2 limits for actual system RAM, swap and pids in strict mode,
  with cgroup OOM counters and fail-closed diagnostics;
- stable NVML UUID reservations for single- and multi-GPU jobs; numeric API
  selections are resolved against the startup inventory and CUDA receives UUIDs;
- raw Blender logs stored and downloadable inside the job directory;
- WebSocket job/progress/log/preview/operation/metrics events with REST resync;
- live Current task progress with the current frame and Blender sample count
  parsed from real `Fra:… | Sample X/Y` output;
- persistent preview/original frame metadata, frame pages capped at 50, and
  disk-built frame ZIP downloads;
- real preview, log overlay, jobs, artifacts, CPU/GPU/storage metrics, and
  low-space warning in normal frontend mode;
- management of installed and available Blender versions;
- separate metric rows for every CPU and GPU with internal scrolling;
- light and dark themes, with dark used by default;
- Playwright smoke tests for the main UI workflows;
- FastAPI application lifecycle and versioned API router;
- async SQLite persistence with SQLAlchemy and Alembic migrations;
- public health/readiness probes, shared error responses, bounded request bodies,
  first-browser pairing sessions, and a private production Bearer boundary for
  loopback/service clients;
- support for verified bundled Blender 5.2.0 and 4.1.1 when their binaries are
  present in the runtime image;
- interactive one-command installer and `render-node` management command.

## Target MVP

- FastAPI REST API and WebSocket events;
- persistent job queue with an explicit state machine;
- job state and history stored in SQLite;
- safe Blender execution without a shell;
- process-group cancellation using `SIGTERM`, a timeout, and `SIGKILL`;
- GPU reservation with guaranteed release;
- `.blend` uploads and protected ZIP processing;
- persistent logs, frames, previews, and other artifacts;
- recovery of unfinished jobs after a backend restart;
- installation of additional versions only from the official Blender archive,
  with SHA-256 verification.

An image may include Blender `5.2.0` and `4.1.1`, but the backend registers each
bundled version only after executing its real binary and confirming the exact
patch version. An empty runtime registry is a ready application state. The user
then completes `Download → Install → Activate` in the runtime manager; no version
is activated automatically. Only one version can be active at a time, and it
cannot be changed while a job is `QUEUED` or `RENDERING`. Additional inactive
versions can be deleted; detected bundled versions are immutable.

## Technology Stack

| Area | Technology | Purpose |
|---|---|---|
| Frontend | React, Vite | User interface and production build |
| Server state | TanStack Query | Requests, caching, and synchronization |
| UI state | Zustand | Local interface state |
| Backend | Python, FastAPI, Uvicorn, Pydantic | REST API, WebSocket, and contracts |
| Storage | SQLite, SQLAlchemy, Alembic | Jobs, settings, history, and migrations |
| Blender | `asyncio.create_subprocess_exec` | Safe execution without `shell=True` |
| GPU | NVIDIA NVML through `pynvml` | Device discovery and metrics |
| Tests | pytest, pytest-asyncio, Playwright | Backend logic and user-facing UI flows |

Redis, DragonflyDB, RabbitMQ, and PostgreSQL are intentionally excluded from the
single-node MVP. They should be introduced only when horizontal scaling becomes
a real requirement.

## One-command installation

Use this inside a fresh Ubuntu 22.04/24.04 x86_64 GPU Pod as root:

```bash
curl -fsSL https://raw.githubusercontent.com/NikkZhur/Render-Node/master/install.sh | bash
```

The installer does not require a public URL, Pod ID, or provider-specific
variable. It checks the GPU, downloads the latest stable GitHub Release bundle and
checksum, installs a pinned Python runtime and the verified Blender versions,
then starts the already-built web interface as unprivileged Linux processes. It
does not download Node.js, build frontend source, install Docker/Compose, or use
systemd. Re-running the same command repairs or atomically updates the install;
failed readiness rolls back to the previous release.

Before running it:

- choose Ubuntu 22.04 or 24.04 where `nvidia-smi` succeeds;
- expose internal HTTP port `8080` in the Pod settings;
- use a machine dedicated to one operator and their own trusted scenes.

After installation:

```bash
render-node status
render-node logs
render-node reset-access
render-node restart
render-node uninstall
```

Opening the panel deliberately claims an unclaimed node for that browser. This
is an explicit TOFU race, not identity verification: whoever completes the
first browser claim gets access. Use **Pair another device** in the authorized
panel to create a one-use fragment link that expires after five minutes. At most
five browser sessions can exist. If every authorized browser is lost, run
`render-node reset-access`; the next browser can then claim the running node
without changing jobs or restarting the service. Backend restart also clears
all ephemeral browser sessions and invitations.

`uninstall` preserves the database, scenes, results and downloaded Blender
versions under `/workspace`. Exact paths, update/rollback behavior, explicit
`--version`, and release publication are documented in
[`deploy/README.md`](deploy/README.md).

### RunPod and other managed Pods

The command above is the main Ubuntu Pod flow: no nested Docker is used. As an
alternative, a Pod template may directly start the published
`ghcr.io/nikkzhur/render-node:latest` image. Both variants expose port
`8080/http`, keep data in `/workspace`, and use the platform HTTPS proxy.

## Quick Start: Full Development Stack

After installing the backend and frontend dependencies once, start both services
from the repository root with one command:

```bash
make dev
```

The command applies pending Alembic migrations, starts the backend on port `8000`,
waits up to 60 seconds for its public `/ready` endpoint, and only then starts the
frontend on port `5173`. If the backend exits or does not become ready, the frontend
is not started. `Ctrl+C` stops both processes; if either running service exits
unexpectedly, the other is stopped too.
Environment variables such as `RENDER_NODE_WORKSPACE` are passed through unchanged,
and settings are also loaded from the repository-root `.env`. Rendering is ready
without a separate trust/profile switch once a Blender version is installed and
activated through the UI. Development uses the
`RENDER_NODE_RESOURCE_ISOLATION_MODE=trusted` fallback unless a writable cgroup
v2 subtree has been delegated to the current user. This fallback never applies
`RLIMIT_AS`; it relies on the actual memory/pids boundary of the development
container or machine and is reported by `/api/v1/system/capabilities`.
Startup does not require Blender to exist. When no verified runtime is present,
open **Blender versions** in the UI and explicitly download, install, and activate
one before creating a render job.

`make dev` uses the same automatic first-browser claim and shows no login form.
It is a development server, not an anonymously safe Internet deployment. A
backend restart clears access; an open tab will claim again when it reconnects
if the node is still unclaimed.

## Quick Start: Frontend

Node.js 22 and npm are required.

```bash
cd frontend
npm ci
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). Vite is configured to listen
on `0.0.0.0:5173`, so the page is accessible from the host when the container port
is forwarded. Vite uses the current browser host for HMR, including through an
arbitrary HTTPS/WebSocket-capable reverse proxy; no public-origin variable or
provider detection is needed.

`npm run dev` uses the real Job API through Vite's `/api` proxy. Run
`npm run dev:mock` only when the explicit legacy mock mode is needed. Set
`RENDER_NODE_BACKEND_URL` when the backend is not available at
`http://127.0.0.1:8000`. Vite does not inject the private backend Bearer into
browser HTTP or WebSocket requests; browser access always uses pairing cookies.

Vite keeps REST and application WebSocket traffic on the current browser host.
The access boundary does not depend on the upstream `Host`, so another proxy may
rewrite it without configuration. Frontend and API must remain on the same
browser-visible HTTPS origin; cross-origin development is intentionally
unsupported.

## Quick Start: Backend

Python 3.13 and `uv` are required.

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
```

Blender execution is part of the installed Render Node and has no separate
trust/profile setting. Install and activate a Blender version through the UI;
the backend then runs it inside the node's configured resource boundary.

Open [http://localhost:8000/health](http://localhost:8000/health) for liveness,
[http://localhost:8000/ready](http://localhost:8000/ready) for SQLite readiness,
and [http://localhost:8000/docs](http://localhost:8000/docs) for Swagger UI.
Interactive API documentation is disabled when `RENDER_NODE_ENV=production`.

## Production Boundary

Production startup requires a secret `RENDER_NODE_AUTH_TOKEN` of at least 32
characters. It is a private management/service credential for origin-less
clients on loopback or another private backend path; it is never sent to the
frontend, installed as a browser cookie, or injected into public Nginx requests.
`/health` and `/ready` remain public probes. Keep port `8000` loopback-only.

The first deliberate JavaScript `POST /api/v1/access/claim` atomically claims an
unclaimed backend process and sets an opaque `HttpOnly`, `Secure`,
`SameSite=Strict`, `__Host-` session cookie. A page GET, crawler preview, probe,
`HEAD`, or `OPTIONS` never claims the node. Browser requests additionally require
same-origin Fetch Metadata; unsafe methods carry `X-Render-Node-Request: 1`.
Browser WebSockets require the session cookie, valid HTTPS `Origin`, and the
fixed non-secret `render-node-browser-v1` subprotocol. When Fetch Metadata is
present it must be `Sec-Fetch-Site: same-origin`; the subprotocol is the explicit
compatibility context for Chromium WebSocket handshakes that omit Fetch Metadata.
Malformed origins and cross-/same-site contexts are rejected, but a valid
browser `Origin` is deliberately not compared with backend-visible `Host`.
`Forwarded` and `X-Forwarded-*` are never trusted.

Additional browsers use one-time 256-bit invitation tokens created by an
authorized browser. The frontend puts the token only in `#pair=...`, removes the
fragment before sending it in a JSON body, and never stores session tokens in
JavaScript storage. Invitations expire after five minutes; the total browser
session limit is five. Sessions and invitations live only in the single Uvicorn
worker and reset on backend restart. `render-node reset-access` performs the same
reset locally without touching jobs or Blender operations.

Deploy the static frontend and `/api` behind one HTTPS reverse proxy on one
public origin. The proxy may rewrite upstream `Host`; no public URL or origin is
configured. Separate cross-origin frontend/API hosting is unsupported. Do not
expose port `8000`, the internal Bearer, or any `VITE_*` secret. Render Node no
longer has Basic Auth, usernames, passwords, or a credentials command; provider
authentication may still be added outside it.

Render Node treats the installed VM, container, or cloud Pod as its execution
boundary. There is no runner trust toggle to enable after deployment: browser
pairing grants access to the node, while Blender runtime download, installation,
activation, upload, and render actions are managed through the interface.

A production process uses:

```dotenv
RENDER_NODE_ENV=production
RENDER_NODE_RESOURCE_ISOLATION_MODE=strict
RENDER_NODE_AUTH_TOKEN=<unique-secret-with-at-least-32-characters>
```

Pairing is an access boundary, not a sandbox between Blender and the backend.
Automatic Python remains disabled, subprocess environment and paths remain
constrained, and resource/time/output limits still apply. A multi-tenant render
farm would require a separate worker isolation design outside this node product.

Production resource isolation is always strict. Render Node detects the current
cgroup hierarchy from `/proc/self/mountinfo` and `/proc/self/cgroup`; it does not
assume that `/sys/fs/cgroup` is writable. A usable deployment must delegate a
writable cgroup v2 subtree with the `memory` and `pids` controllers to the
unprivileged backend. The native root management command and root image
entrypoint try to prepare `<current-cgroup>/render-node` before dropping
privileges. Managed containers must expose/delegate those controllers; a
read-only host cgroup mount is insufficient. `RENDER_NODE_CGROUP_ROOT` may name
an already-delegated child of the backend's current cgroup.

If only cgroup v1 is available, or v2 is not writable/delegated, strict mode
keeps the API ready for diagnostics but reports the runner unavailable and
rejects Start/Retry before queueing. The verified fallback is
`RENDER_NODE_RESOURCE_ISOLATION_MODE=trusted`, accepted only in development/test
and never selected automatically in production.

## Limits, Cleanup, and Backup

JSON mutations default to 1 MiB and WebSocket client messages to 64 KiB. Scene,
Blender archive, ZIP extraction, render time, actual system RAM, swap, pids,
logs, and outputs have separate settings in [`.env.example`](.env.example).
`RENDER_NODE_WORKER_MEMORY_GB` now means `memory.max`, not virtual address space;
`RENDER_NODE_WORKER_SWAP_GB` maps to `memory.swap.max` when supported, and
`RENDER_NODE_WORKER_PIDS_LIMIT` maps to `pids.max`. Uploads are
bounded streaming multipart requests; resumable uploads are not implemented.

Every strict job gets its own cgroup before Blender exec. Blender joins it in
the child setup, so descendants inherit membership, while process-group
cancellation still uses `SIGTERM` → grace period → `SIGKILL`. Cleanup also uses
`cgroup.kill` when available and removes the per-job directory. Failure
diagnostics prioritize `memory.events` for system-RAM OOM, then Blender/CUDA log
markers for VRAM or shared GPU-memory exhaustion, then report a plain `SIGABRT`
or other exit. Cgroups do not impose a VRAM limit.

Cleanup is conservative and explicit:

- `DELETE /api/v1/jobs/{id}` removes the database row, artifacts, and the whole
  server-generated job directory when the job is not active;
- job settings and the uploaded scene can be replaced while a job is `CREATED`
  or `READY`; they become read-only after the first start;
- rerender creates a separate editable `READY` job with copied settings and a
  server-side copy of the original input, without copying artifacts;
- retry removes old output, previews, logs, temporary files, and artifact rows,
  while preserving the uploaded scene;
- startup clears per-job temporary directories and discards an input directory
  left by an upload that never advanced its job beyond `CREATED`;
- failed Blender download/install operations remove incomplete downloads and
  quarantine/extraction data.

There is no automatic age-based retention. Operators should delete completed
jobs explicitly and monitor the low-space signal. For a consistent backup, stop
new mutations and renders, then back up the SQLite database together with
`/workspace/jobs` and `/workspace/blender/versions`. Restore the matching set and
run `uv run alembic upgrade head` before startup.

### Dev Container

The repository includes a VS Code Dev Container configuration with Python 3.13,
`uv`, Node.js 22, `make`, and ports `5173` and `8000` forwarding. After opening
the repository in the container, use `make dev` to run the full development
stack.

The current [`.devcontainer/Dockerfile`](.devcontainer/Dockerfile) is intended for
development and is not the production Render Node image.

## Frontend Verification

```bash
cd frontend
npm run lint
npm run build
npm run dev:mock
# In another terminal:
npm run qa:smoke
npm run qa:versions-empty
```

If Playwright Chromium is not installed yet:

```bash
npx playwright install chromium
```

The mock smoke test covers desktop, ultrawide, compact desktop, and mobile
layouts. With backend and real-mode Vite running, `npm run qa:api` additionally
checks scene upload, `READY`, and persistence after reload. `npm run qa:runner`
uses fake Blender to check WebSocket progress/logs, preview and full-frame
delivery, frame/ZIP downloads, artifact recovery after reload, metrics, and
process-group cancellation.

## Current Repository Structure

```text
.
├── .devcontainer/                 # Development environment and port forwarding
├── backend/                       # FastAPI, async SQLite, Alembic, and tests
│   ├── app/                       # Application, API, config, and storage code
│   ├── migrations/                # Alembic schema history
│   └── tests/                     # Backend unit and integration tests
├── frontend/                      # Working React prototype
│   ├── scripts/qa-smoke.mjs       # Playwright smoke test
│   ├── src/api.js                 # REST API boundary
│   ├── src/realtime.js            # WebSocket reconnect and REST resync
│   ├── src/App.jsx                # Layout and UI workflows
│   ├── src/mockApi.js             # Temporary mock data source
│   ├── src/store.js               # Zustand UI state
│   └── src/styles.css             # Responsive components and themes
└── README.md
```

The `проект пример/` directory is used only as reference material. It is fully
excluded through `.gitignore` and is not part of Render Node.

## Architecture

```text
Browser
  │
  │ REST + WebSocket
  ▼
FastAPI
  ├── API and validation
  ├── Job Manager and state machine
  ├── Single-process scheduler and GPU validation
  ├── SQLite
  ├── GPU/System monitoring
  └── Blender subprocess group
        └── /workspace/jobs/{job_id}/
```

Every job is isolated under `/workspace/jobs/{job_id}`. User-provided filenames
never determine filesystem paths. The backend constructs Blender arguments from
validated parameters and never accepts a ready-made shell command from the
client.

## Security Principles

- `.blend` files, ZIP archives, and user-provided paths are untrusted;
- arbitrary Python scripts and automatic add-on installation are disabled by
  default;
- ZIP archives are checked for Zip Slip, file count, and total extracted size;
- upload size, render duration, and result size are limited;
- Blender runs in a separate process group without a shell;
- the single worker slot is released in `finally`, including cancellation and failures;
- secrets, uploads, Blender binaries, and render results are never committed;
- production processes must not run as root.

## Remaining Deployment Work

- publish the first GHCR image and create a ready-to-click RunPod template;
- add the OS-isolated, non-root worker boundary before accepting third-party scenes;
- repeat the CUDA/OptiX smoke in the released Pod image and validate delegated
  cgroup v2 process isolation there;
- add resumable uploads if deployment networks require them;
- add an operator-selected retention policy instead of deleting user results by
  age implicitly.
