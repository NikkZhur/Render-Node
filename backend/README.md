# Render Node backend

The backend currently provides the FastAPI lifecycle, async SQLite, Alembic,
health/readiness, persistent Job CRUD/actions, bounded `.blend`/ZIP uploads, the
bundled/official/manual Blender runtime registry, and a logical-job Manager that
uses `SandboxRunner` as its only subprocess boundary and can supervise parallel
per-GPU Blender workers.

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
```

Blender execution belongs to the installed Render Node and has no separate
runner trust/profile switch. Once a verified Blender version is installed and
activated through the UI, the backend can run it inside the node's resource
boundary. Settings read `backend/.env` and `.env` in the backend's working
directory. Export `RENDER_NODE_DATABASE_URL` for Alembic as well; see the
[development guide](../docs/development.md) for a consistent workspace setup.

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
32 characters. `/health` and `/ready` are public probes. Origin-less
CLI/server clients authenticate with this private Bearer; public browser
requests do not receive it and cannot use it to bypass pairing. Keep port 8000
loopback-only.

The first deliberate browser `POST /api/v1/access/claim` creates an in-memory
session and an opaque `HttpOnly`, `SameSite=Strict` cookie (`Secure` and
`__Host-` in production). Later browsers use `POST /api/v1/access/pair` with a
one-time 256-bit invitation created by `POST /api/v1/access/invitations`.
Invitations expire after five minutes and at most five browser sessions exist.
Sessions and invitations are deliberately process-local: production must run
one Uvicorn worker, and restart resets access without changing persistent jobs.
`POST /api/v1/access/reset` accepts only an origin-less service Bearer and is
used by the local `render-node reset-access` command.

Browser REST requires `Sec-Fetch-Site: same-origin`; mutations additionally
require `X-Render-Node-Request: 1`. Browser WebSocket requires a valid session
cookie, one strictly parsed HTTPS `Origin` in production, and the fixed
`render-node-browser-v1` subprotocol. Present Fetch Metadata must be
`same-origin`; the subprotocol provides explicit context for real Chromium
handshakes that omit those headers. A valid origin is not compared with
backend-visible `Host`, so a gateway may rewrite upstream `Host`; forwarded
headers are ignored. CORS is not enabled, and frontend/API must remain on one
browser-visible HTTPS origin.

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
- `GET /api/v1/jobs/summary` returns global `queued_count` and `rendering_count`
  independently of the current history page;
- `DELETE /api/v1/jobs/{id}` removes the job directory and artifact metadata;
  queued and rendering jobs return `409` until cancelled;
- `POST /api/v1/jobs/{id}/start` queues a ready job;
- one scheduler task starts the oldest queued job;
- NVML discovery and telemetry share one device enumeration: a physical GPU in
  normal mode, or its accessible MIG compute instances in MIG mode. A MIG parent
  is never offered alongside its children. Inaccessible devices do not hide
  other GPUs, and cards without MIG support retain the ordinary discovery path;
- numeric `gpu_ids` identify logical devices within the backend process. Their
  UUID mapping is shared by inventory and metrics and survives NVML reordering or
  temporary device loss. Scheduler reservations and `CUDA_VISIBLE_DEVICES` use
  the same `GPU-...` or `MIG-...` UUIDs; a recreated MIG instance with a new UUID
  cannot silently replace a selected device. New devices require a backend
  restart before they become selectable in the startup scheduler inventory;
- unsupported GPU utilization, temperature and power measurements are `null` in
  REST/WebSocket metrics. They do not remove a device or turn into a measured
  zero; the UI shows an unavailable indicator without a chart for that value.
  Memory values belong to the selected GPU/MIG device, never its parent;
- Job create/update/response models persist `gpu_render_mode` as
  `FRAME_PER_GPU` or `ALL_GPUS_PER_FRAME`. Omitted create requests choose frame
  distribution for CUDA/OptiX with two or more GPUs; CPU and one-GPU jobs use
  `ALL_GPUS_PER_FRAME`. Omitted updates preserve a compatible saved value, while
  incompatible updates normalize it to `ALL_GPUS_PER_FRAME`;
- `ALL_GPUS_PER_FRAME` retains one Blender process with every reserved UUID.
  `FRAME_PER_GPU` uses one process and one UUID per worker, except that `SINGLE`
  runs only on the first GPU and RANGE is bounded by its inclusive API span;
- RANGE and ALL workers are partitioned deterministically round-robin by a
  trusted Blender adapter after the scene loads, so the actual scene
  `frame_step` is respected;
- single-process RANGE/ALL runs use the same adapter's scene frame plan to
  calculate overall progress and ETA, including CPU and one-GPU runs. A missing
  plan never treats a per-frame fraction as the whole animation's progress;
- a distributed run has one job cgroup, absolute deadline, output limit and
  shared log budget. Startup failure, non-zero exit, cancellation, timeout, or a
  shared limit terminates every worker process group before releasing all GPUs;
- raw logs are stored as `logs/blender-gpu-{worker}.log`; the compatible
  `logs/blender.log` aggregate prefixes each line with its stable GPU UUID, as do
  live log events;
- `GET /api/v1/jobs/{id}` exposes persisted progress and process state;
- Job responses already expose UTC `started_at` and `finished_at`; render
  duration is derived from those fields and requires no stored duration column;
- Blender 4.1 progress lines such as `Fra:1 ... | Sample 48/512` update both
  persisted overall frame-range progress and the existing WebSocket
  `sample`/`total_samples` fields; unrecognized log lines remain non-fatal;
- concurrent worker progress is serialized into one monotonic job value.
  GPU-tagged `render.progress` events for `FRAME_PER_GPU` add numeric
  `worker_progress` scoped to the event's stable `gpu_uuid`. It includes that
  worker's completed assigned frames plus its current-frame sample fraction;
  `progress` remains the authoritative overall value used by existing clients;
  `ALL_GPUS_PER_FRAME` keeps the original joint-process event without a false
  per-device snapshot. Worker progress is transient and is not written to
  SQLite or exposed through a new REST endpoint. Output paths are de-duplicated
  so a frame artifact, preview event, and `render.frame_ready` event are emitted
  once;
- `POST /api/v1/jobs/{id}/cancel` stops the whole process group before persisting
  `cancelled`;
- cgroup `memory.events` distinguishes actual system-RAM OOM from Blender/CUDA
  VRAM exhaustion and a plain `SIGABRT`; cgroups do not limit VRAM;
- `GET /api/v1/devices` returns an empty list safely when NVIDIA NVML is unavailable.

Scene uploads from the browser include multipart `auto_configure=true`. The
upload returns a ready Job with a separate `analysis_status` (`pending`,
`running`, `completed`, or `failed`). Legacy API callers omit the flag to keep
their explicit settings. `analysis_id` identifies each file generation;
`scene_metadata`, `analysis_error`, and `auto_configured` survive restart.
The `job.analysis_updated` event invalidates REST job data, with frontend polling
while analysis is pending. Start and configuration updates return `409` with
`scene_analysis_pending` until analysis finishes; replacement and deletion cancel
the reader before touching its input. Analysis failure preserves the upload and
allows manual setup. Interrupted analyses become failed at startup.

The recorded Blender runtime inspects the scene with autoexec disabled inside
the existing resource boundary. Analysis is serialized, uses a separate temporary
directory, has a 1 MiB output/log budget per process, and a total configurable
120-second deadline (`RENDER_NODE_SCENE_ANALYSIS_TIMEOUT_SECONDS`). Each GPU/MIG
UUID is probed in a separate clean Blender process. Cycles prefers available
OptiX, then CUDA, then CPU; path guiding stays on CPU and OSL excludes CUDA.
Animations distribute frames across compatible GPUs, capped by the actual frame
count and 16 devices. Single-frame jobs use the shared process. Scene engine,
frame step, and image quality are preserved; zero/negative bounds use ALL.
Eevee/Workbench display Automatic device because graphics selection belongs to
Blender, independently of Cycles device settings. Capability discovery is not a
benchmark or a guarantee that a scene fits in VRAM/RAM.

Alembic head `20261023_0006` adds scene-analysis state with `none` for legacy jobs;
existing jobs are not reconfigured. The preceding `20260818_0005` migration adds
the strategy column with `ALL_GPUS_PER_FRAME` as its legacy default.
