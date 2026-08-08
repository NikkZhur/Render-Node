# Render Node

A single-node service for remote Blender rendering through a web browser. Users
upload a scene, choose a Blender version and compute resources, start a render,
and monitor jobs, logs, frames, and hardware status.

The primary deployment is a temporary single-tenant GPU server or cloud Pod:
its owner starts Render Node from a prepared image, renders their own scenes,
downloads the results, and can then delete the whole node. The project keeps the
public API suitable for a separately isolated worker if a shared render service
is needed later.

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
- raw Blender logs stored and downloadable inside the job directory;
- WebSocket job/progress/log/preview/operation/metrics events with REST resync;
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
  exact-origin CORS, and a production Bearer boundary for REST and WebSocket.

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

The base image is planned to include Blender `5.2.0` and `4.1.1`. Other supported
versions can be installed explicitly. Only one version can be active at a time,
and it cannot be changed while a job is `QUEUED` or `RENDERING`. Additional
inactive versions can be deleted from the runtime manager; bundled versions are
immutable.

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
Environment variables such as `RENDER_NODE_WORKSPACE`,
`RENDER_NODE_BACKEND_AUTH_TOKEN`, and the development runner settings are passed
through unchanged. Settings are also loaded from the repository-root `.env`, so
copy `.env.example` to `.env` once and set
`RENDER_NODE_RUNNER_MODE=local_trusted` for persistent direct-on-host rendering.
This mode starts Blender as a subprocess of Render Node and must only receive
trusted `.blend`/ZIP files. If it is disabled, the UI reports that the runner is
unavailable and keeps a started job in `READY` instead of creating a failed job.

## Quick Start: Frontend

Node.js 22 and npm are required.

```bash
cd frontend
npm ci
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). Vite is configured to listen
on `0.0.0.0:5173`, so the page is accessible from the host when the container port
is forwarded.

`npm run dev` uses the real Job API through Vite's `/api` proxy. Run
`npm run dev:mock` only when the explicit legacy mock mode is needed. Set
`RENDER_NODE_BACKEND_URL` when the backend is not available at
`http://127.0.0.1:8000`. For a locally authenticated backend, the Vite server can
inject `RENDER_NODE_BACKEND_AUTH_TOKEN` into proxied HTTP and WebSocket requests;
this variable is server-side and must never use a `VITE_` prefix.

## Quick Start: Backend

Python 3.13 and `uv` are required.

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
```

Local Blender execution is intentionally disabled by default. For an isolated
development machine that receives only trusted scenes, set
`RENDER_NODE_RUNNER_MODE=local_trusted` in the repository-root `.env`.
An authenticated production node dedicated to one operator may use the explicit
`single_tenant` deployment profile described below. A shared service that accepts
scenes from unrelated users still requires a separate OS-isolated worker.

Open [http://localhost:8000/health](http://localhost:8000/health) for liveness,
[http://localhost:8000/ready](http://localhost:8000/ready) for SQLite readiness,
and [http://localhost:8000/docs](http://localhost:8000/docs) for Swagger UI.
Interactive API documentation is disabled when `RENDER_NODE_ENV=production`.

## Production Boundary

Production startup requires both a secret `RENDER_NODE_AUTH_TOKEN` of at least
32 characters and one or more exact HTTPS origins in
`RENDER_NODE_ALLOWED_ORIGINS`. Keep the token outside git and frontend build
variables. `/health` and `/ready` remain public for probes; every route under
`/api/v1`, including `WS /api/v1/events`, requires the same
`Authorization: Bearer ...` credential. Browser origins that are present but not
allowlisted are rejected before the API handler runs.

Browsers cannot attach a Bearer header to native WebSocket connections or plain
artifact download links. Deploy the static frontend and API behind one HTTPS
reverse proxy that injects the backend credential into both HTTP requests and
WebSocket upgrades, and keep port 8000 private. The proxy-facing browser origin
must be in `RENDER_NODE_ALLOWED_ORIGINS`. Do not expose the backend token to
JavaScript or place it in a `VITE_*` variable.

Render Node has two explicit trust profiles:

- `isolated_worker` is the default. Production rendering fails closed until a
  separate OS-isolated worker is available. Use it for a shared service that
  accepts scenes from unrelated users.
- `single_tenant` treats the entire rented VM or cloud Pod as the isolation
  boundary. It permits direct Blender execution in production only together
  with `RENDER_NODE_RUNNER_MODE=local_trusted`. The node must belong to one
  operator and accept only that operator's scenes.

An authenticated temporary RunPod-style node therefore uses:

```dotenv
RENDER_NODE_ENV=production
RENDER_NODE_DEPLOYMENT_PROFILE=single_tenant
RENDER_NODE_RUNNER_MODE=local_trusted
RENDER_NODE_AUTH_TOKEN=<unique-secret-with-at-least-32-characters>
RENDER_NODE_ALLOWED_ORIGINS=https://<public-panel-origin>
```

The single-tenant profile does not sandbox Blender away from the backend inside
the node. It is not suitable for a public render farm or any deployment where
unrelated users can submit files. Automatic Python remains disabled, subprocess
environment and paths remain constrained, and resource/time/output limits still
apply.

## Limits, Cleanup, and Backup

JSON mutations default to 1 MiB and WebSocket client messages to 64 KiB. Scene,
Blender archive, ZIP extraction, render time, process memory/pids, logs, and
outputs have separate settings in [`.env.example`](.env.example). Uploads are
bounded streaming multipart requests; resumable uploads are not implemented.

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

- publish a versioned single-tenant image and cloud Pod template;
- add a one-command installer for ordinary rented Ubuntu GPU servers;
- add the OS-isolated, non-root worker boundary before accepting third-party scenes;
- validate CUDA/OptiX and process isolation in a cloud GPU environment;
- add resumable uploads if deployment networks require them;
- add an operator-selected retention policy instead of deleting user results by
  age implicitly.
