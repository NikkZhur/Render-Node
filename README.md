# Render Node

A single-node service for remote Blender rendering through a web browser. Users
upload a scene, choose a Blender version and compute resources, start a render,
and monitor jobs, logs, frames, and hardware status.

The project focuses on a secure and verifiable MVP without premature
infrastructure complexity. The first goal is a reliable render node running on
one machine, while keeping the public API suitable for moving the worker to a
separate node later.

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
- health/readiness endpoints, shared error responses, and development CORS.

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

The base image is planned to include Blender `5.2.0`, `4.5.11 LTS`, `4.2.22 LTS`,
`4.1.1`, and `3.6.23 LTS`. Only one version can be active at a time, and it cannot
be changed while a job is `QUEUED` or `RENDERING`.

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
`http://127.0.0.1:8000`.

## Quick Start: Backend

Python 3.13 and `uv` are required.

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
```

Local Blender execution is intentionally disabled by default. For an isolated
development machine only, set `RENDER_NODE_ALLOW_UNSANDBOXED_RUNNER=true`.
Production refuses to enable rendering until an OS-isolated worker sandbox is
available; the development fallback is never accepted there.

Open [http://localhost:8000/health](http://localhost:8000/health) for liveness,
[http://localhost:8000/ready](http://localhost:8000/ready) for SQLite readiness,
and [http://localhost:8000/docs](http://localhost:8000/docs) for Swagger UI.

### Dev Container

The repository includes a VS Code Dev Container configuration with Python 3.13,
`uv`, Node.js 22, and ports `5173` and `8000` forwarding. After opening the
repository in the container, run the commands shown above.

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

## Next Steps

1. Complete the phase-6 security, limits, cleanup, and restart-recovery audit.
2. Remove remaining mock-only dependencies from the production path.
3. Add an OS-isolated production worker boundary and cloud GPU smoke tests.
