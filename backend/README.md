# Render Node backend

The backend currently provides the FastAPI lifecycle, async SQLite, Alembic,
health/readiness, persistent Job CRUD/actions, bounded `.blend`/ZIP uploads, the
bundled/official/manual Blender runtime registry, and a single-process Job
Manager using `SandboxRunner` as its only subprocess boundary.

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
```

Set `RENDER_NODE_RUNNER_MODE=local_trusted` only for trusted `.blend`/ZIP files.
It is available in development and in an explicit production
`RENDER_NODE_DEPLOYMENT_PROFILE=single_tenant`, where the entire rented VM or
cloud Pod belongs to one operator. Production keeps the `isolated_worker`
profile by default and fails closed there while no OS-isolated worker exists.
Settings load from the repository-root `.env`.

Resource isolation is separate from the scene trust profile:

- `RENDER_NODE_RESOURCE_ISOLATION_MODE=strict` requires a delegated writable
  cgroup v2 subtree and is mandatory in production. Each job sets
  `memory.max`, optional `memory.swap.max`, `pids.max`, and optional
  `memory.oom.group`; startup diagnostics reject rendering when this boundary
  cannot be prepared.
- `RENDER_NODE_RESOURCE_ISOLATION_MODE=trusted` is local/test-only. It never
  applies `RLIMIT_AS` and explicitly relies on external container/Pod limits.

`RENDER_NODE_WORKER_MEMORY_GB` is the actual system-RAM cgroup limit;
`RENDER_NODE_WORKER_SWAP_GB` defaults to zero, and
`RENDER_NODE_WORKER_PIDS_LIMIT` controls the per-job process count. An optional
`RENDER_NODE_CGROUP_ROOT` must remain inside the process's current cgroup subtree.
Only cgroup v2 is enforced; detected v1 or read-only hierarchies fail closed in
strict mode. `/api/v1/system/capabilities` exposes the selected backend and
feature support.

Production additionally requires a secret `RENDER_NODE_AUTH_TOKEN` with at least
32 characters and exact HTTPS `RENDER_NODE_ALLOWED_ORIGINS`. `/health` and
`/ready` are public probes; every REST and WebSocket route below `/api/v1`
requires the Bearer token. Keep the backend private behind an HTTPS reverse proxy
that injects the credential for the browser frontend and WebSocket upgrades.

Development checks:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy app tests
uv run pytest
```

Blender version API:

- `GET /api/v1/blender/versions` lists local runtimes;
- `GET /api/v1/blender/releases` reads the TTL-cached official Linux x64 catalog;
- download, manual upload and install return `202` with an operation UUID;
- `GET /api/v1/blender/operations/{id}` is the polling contract;
- installation verifies the official SHA-256 again and never activates implicitly.

Render lifecycle:

- `GET /api/v1/jobs/page?page=1&page_size=10` returns one ordered history page;
  `page_size` is capped at 10 and subsequent pages are fetched on demand;
- `DELETE /api/v1/jobs/{id}` removes the job directory and artifact metadata;
  queued and rendering jobs return `409` until cancelled;
- `POST /api/v1/jobs/{id}/start` queues a ready job;
- one scheduler task starts the oldest queued job;
- NVML inventory is captured at startup; scheduler reservations and
  `CUDA_VISIBLE_DEVICES` use stable GPU UUIDs even if runtime indices reorder;
- `GET /api/v1/jobs/{id}` exposes persisted progress and process state;
- Blender 4.1 progress lines such as `Fra:1 ... | Sample 48/512` update both
  persisted overall frame-range progress and the existing WebSocket
  `sample`/`total_samples` fields; unrecognized log lines remain non-fatal;
- `POST /api/v1/jobs/{id}/cancel` stops the whole process group before persisting
  `cancelled`;
- cgroup `memory.events` distinguishes actual system-RAM OOM from Blender/CUDA
  VRAM exhaustion and a plain `SIGABRT`; cgroups do not limit VRAM;
- `GET /api/v1/devices` returns an empty list safely when NVIDIA NVML is unavailable.
