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

Set `RENDER_NODE_ALLOW_UNSANDBOXED_RUNNER=true` only for explicit local
development. The production configuration fails closed while the deployment has
no OS-isolated worker sandbox.

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

- `POST /api/v1/jobs/{id}/start` queues a ready job;
- one scheduler task starts the oldest queued job;
- `GET /api/v1/jobs/{id}` exposes persisted progress and process state;
- `POST /api/v1/jobs/{id}/cancel` stops the whole process group before persisting
  `cancelled`;
- `GET /api/v1/devices` returns an empty list safely when NVIDIA NVML is unavailable.
