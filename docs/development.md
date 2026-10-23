# Development

[English overview](../README.md) · [Русский](i18n/README.ru.md) · [简体中文](i18n/README.zh-CN.md)

## Local setup

Use Linux with Python 3.13, `uv`, Node.js 22.12+ and `make`. From the repository
root, install the locked dependencies:

```bash
(cd backend && uv sync --frozen)
npm --prefix frontend ci
```

Choose a writable data directory outside the checkout and export both settings
in the shell that starts the services:

```bash
export RENDER_NODE_WORKSPACE="$HOME/render-node-data"
export RENDER_NODE_DATABASE_URL="sqlite+aiosqlite:///$RENDER_NODE_WORKSPACE/database/render-node.sqlite3"
make dev
```

`make dev` applies Alembic migrations, starts the backend on port `8000`, waits
up to 60 seconds for `/ready`, then starts Vite on port `5173`. Failed backend
startup prevents Vite from starting. `Ctrl+C`, or an unexpected exit of either
service, stops both process groups.

The launcher inherits environment variables. Backend settings also read
`backend/.env` and `.env` in the backend's working directory; Alembic reads its
database URL from the process environment or `alembic.ini`, not those dotenv
files. Export both variables above so migrations and the application use the
same SQLite database. Without overrides, the workspace is `/workspace`.
See [`.env.example`](../.env.example) for the available settings.

The API can start with an empty Blender registry. In **Blender versions**, use
**Download → Install → Activate** before rendering. Runtime binaries are checked
against their exact patch version, and installation does not activate them
implicitly. Only one runtime is active; activation cannot change while a job is
queued or rendering. Detected bundled versions cannot be deleted.

Development defaults to `RENDER_NODE_RESOURCE_ISOLATION_MODE=trusted`, relying
on the host or container's memory/process limits. It does not enforce a per-job
cgroup boundary or apply `RLIMIT_AS`. The capabilities endpoint reports the
available boundary. Production requires strict cgroup v2 isolation; see the
[deployment guide](../deploy/README.md).

## Run services separately

Export the same workspace and database variables in each terminal first.

Backend:

```bash
cd backend
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
```

Frontend, in another terminal:

```bash
cd frontend
npm run dev
```

Vite listens on `0.0.0.0:5173` and proxies `/api` and application WebSockets to
`http://127.0.0.1:8000`. Set `RENDER_NODE_BACKEND_URL` when the backend is elsewhere.
Frontend and API must remain on the same browser-visible origin. Vite uses the
browser host for HMR, including through an HTTPS/WebSocket-capable reverse proxy;
no public-origin setting or browser Bearer token is needed.

| Endpoint | Purpose |
|---|---|
| [localhost:5173](http://localhost:5173) | Application |
| [localhost:8000/health](http://localhost:8000/health) | Public liveness probe |
| [localhost:8000/ready](http://localhost:8000/ready) | Public SQLite readiness probe |
| [localhost:8000/docs](http://localhost:8000/docs) | Swagger UI; disabled in production |

The first browser claims an unclaimed node. Other browsers use **Pair another
device**; the one-use invitation expires after five minutes, with up to five
browser sessions. Backend restart clears sessions and invitations. An open tab
can claim again on reconnect if the node is still unclaimed. These development
servers are for a trusted environment.

For UI work without a backend, stop the normal Vite instance and run
`npm --prefix frontend run dev:mock` from the repository root. This explicit
mode uses demo data and cannot run Blender.

The [VS Code Dev Container](../.devcontainer/devcontainer.json) provides the
development toolchain and forwards ports `5173` and `8000`. Install the project
dependencies with the commands above. Its Dockerfile is separate from the
production image recipe.

## Checks

Backend, from `backend/`:

```bash
uv run ruff format --check app tests
uv run ruff check app tests
uv run mypy app
uv run pytest
```

Frontend, from `frontend/`:

```bash
npm run lint
npm test
npm run build
```

`npm test` runs the `scripts/*.test.mjs` unit suites without a running server:
request security, proxy configuration, GPU mode normalization, artifact
presentation, and render progress/timing. These commands match the
[release workflow](../.github/workflows/release.yml).

## Browser checks

Install the Playwright browser once, from `frontend/`:

```bash
npx playwright install chromium
```

For mock UI scenarios, run `npm run dev:mock` in one terminal, then run these
from `frontend/` in another:

```bash
npm run qa:smoke
npm run qa:jobs
npm run qa:versions
npm run qa:multi-gpu
npm run qa:render-progress
```

These cover responsive layouts, job and version controls, GPU mode selection,
worker/overall progress, elapsed time, ETA, overflow, and progressbar
accessibility. They use simulated data.

Other scenarios require different environments:

| Command | Required setup | Coverage |
|---|---|---|
| `npm run qa:regressions` | Normal frontend; HTTP and WebSocket are replaced with isolated fixtures | Global queue summary, metric sample history, CPU/GPU selection, gallery log filtering, and desktop/mobile layout |
| `npm run qa:versions-empty` | Normal `npm run dev`; the script intercepts API routes with fixtures | Empty runtime registry, installation/activation, and job controls |
| `npm run qa:api` | Normal frontend and a disposable backend with verified 5.2.0 and 4.1.1 runtimes, with 5.2.0 active | Scene upload, `READY`, and reload persistence |
| `npm run qa:runner` | Normal frontend and a disposable backend with an active fake Blender supporting the scenario's markers | Progress/log events, frames/previews/ZIP, reload recovery, metrics, and cancellation |
| `npm run qa:pairing` | Normal frontend on `5173`, a disposable production-configured backend, OpenSSL, and free port `9443` | First-browser claim, invitations, access reset, and browser sessions |
| `npm run qa:same-origin` | Same HTTPS proxy setup as pairing | REST/WebSocket access through an arbitrary HTTPS origin |

`qa:api` and `qa:runner` create jobs and upload synthetic scenes. Use a temporary
workspace and explicit temporary database URL; do not point them at operator
data or use real Blender for fake-scene scenarios. The runner scenario expects
fake executable behavior provided by
[`backend/tests/fixtures/fake-blender`](../backend/tests/fixtures/fake-blender);
the browser script itself does not create that backend fixture.

The target-based scenarios accept `TARGET_URL` (default
`http://127.0.0.1:5173`). Pairing and same-origin checks instead create their own
local HTTPS proxy and require the fixed upstream port above. Run those two
separately. See [implementation status](implementation-state.md) for completed
checks and remaining environment validation.

Fake Blender tests do not validate real GPU drivers or kernel cgroup v2
delegation. Local bundle construction and release checks are described in
the [deployment guide](../deploy/README.md#publication).
