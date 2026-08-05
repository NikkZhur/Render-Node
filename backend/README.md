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

Set `RENDER_NODE_RUNNER_MODE=local_trusted` only for explicit local development
with trusted `.blend`/ZIP files. Settings load from the repository-root `.env`.
The production configuration fails closed while the deployment has no
OS-isolated worker sandbox.

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
- `GET /api/v1/jobs/{id}` exposes persisted progress and process state;
- `POST /api/v1/jobs/{id}/cancel` stops the whole process group before persisting
  `cancelled`;
- `GET /api/v1/devices` returns an empty list safely when NVIDIA NVML is unavailable.
