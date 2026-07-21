# Render Node

A single-node service for remote Blender rendering through a web browser. Users
upload a scene, choose a Blender version and compute resources, start a render,
and monitor jobs, logs, frames, and hardware status.

The project focuses on a secure and verifiable MVP without premature
infrastructure complexity. The first goal is a reliable render node running on
one machine, while keeping the public API suitable for moving the worker to a
separate node later.

> [!IMPORTANT]
> The repository currently contains an interactive frontend prototype backed by
> mock data. It does not launch Blender or perform real renders yet. The backend,
> persistent job queue, storage, and Blender runner are defined in the
> architecture and will be implemented in the next stages.

## What Works Today

- responsive desktop and mobile interface;
- job setup for scene, render engine, compute device, frames, and GPUs;
- all available GPUs selected by default;
- job list with the primary lifecycle states;
- preview, live log, and simulated render progress;
- artifacts and a paginated frame list with 150 items per page;
- management of installed and available Blender versions;
- separate metric rows for every CPU and GPU with internal scrolling;
- light and dark themes, with dark used by default;
- Playwright smoke tests for the main UI workflows.

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

### Dev Container

The repository includes a VS Code Dev Container configuration with Python 3.13,
`uv`, Node.js 22, and port `5173` forwarding. After opening the repository in the
container, run the frontend commands shown above.

The current [`.devcontainer/Dockerfile`](.devcontainer/Dockerfile) is intended for
development and is not the production Render Node image.

## Frontend Verification

```bash
cd frontend
npm run lint
npm run build
npm run qa:smoke
```

If Playwright Chromium is not installed yet:

```bash
npx playwright install chromium
```

The smoke test covers desktop, ultrawide, compact desktop, and mobile layouts. It
checks GPU selection, Blender version management, mock render start and cancel,
the live log, artifacts, frame pagination, themes, and horizontal overflow.

## Current Repository Structure

```text
.
├── .devcontainer/                 # Development environment and port forwarding
├── frontend/                      # Working React prototype
│   ├── scripts/qa-smoke.mjs       # Playwright smoke test
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
  ├── Scheduler and GPU locks
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
- GPU resources are released in `finally`, including cancellation and failures;
- secrets, uploads, Blender binaries, and render results are never committed;
- production processes must not run as root.

## Next Steps

1. Create the FastAPI application skeleton, configuration, and health endpoint.
2. Add SQLite models, migrations, and the job state machine.
3. Implement safe upload and artifact storage.
4. Add the scheduler, GPU reservation, and Blender runner.
5. Connect the frontend to REST and WebSocket instead of `mockApi`.
6. Cover critical logic with unit and integration tests using a fake Blender
   executable.
