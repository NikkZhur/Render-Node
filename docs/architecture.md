# Render Node: архитектура Python-проекта

## 1. Назначение

Render Node — самостоятельный сервис для удалённого рендера Blender на одной
или нескольких GPU. Пользователь загружает `.blend`, создаёт задание, выбирает
движок и устройство, следит за прогрессом и скачивает результаты через браузер.

Проект реализуется самостоятельно, без копирования исходного кода, интерфейса,
названия и графических материалов Cloud Blender Render.

## 2. Цели

- Основной deployment — временный single-tenant GPU-сервер или облачный Pod:
  владелец запускает готовый образ, рендерит собственные сцены, скачивает
  результаты и удаляет узел. Приложение не является общей render farm.
- Одна выбранная рабочая версия Blender для всех заданий. Образ может включать
  5.2.0 и 4.1.1; backend регистрирует их только после проверки реальных
  бинарников. Отсутствующие версии устанавливаются пользователем из официального
  архива через штатный интерфейс.
- Загрузка `.blend` и ZIP-архивов; возобновление добавляется при подтверждённой
  необходимости работать с большими файлами через нестабильное соединение.
- Рендер одного кадра, диапазона или всей анимации.
- Cycles с CUDA и OptiX; Eevee и Workbench как дополнительные движки.
- Очередь заданий и история запусков.
- Live-логи, прогресс, preview и статистика GPU.
- Полноразмерный просмотр готового кадра, скачивание отдельного кадра и ZIP всей
  последовательности; списки кадров выдаются страницами не более 50 элементов.
- Метрики CPU, GPU и рабочих файловых систем с предупреждением о недостатке места.
- Явное назначение одной или нескольких GPU заданию.
- Раздельные действия загрузки и установки дополнительной версии Blender, а также
  ручная загрузка Linux x64 архива без автоматической установки.
- Восстановление состояния после перезапуска backend.
- Работа в одном контейнере сейчас и возможность перехода к нескольким render
  nodes в будущем.
- Разумная простота: для MVP выбирать наименьшее безопасное и проверяемое
  решение, не вводя инфраструктуру и абстракции без текущего сценария.

## 3. Не входит в первый MVP

- Распределение задания между несколькими машинами.
- Публичная render farm и биллинг.
- Одновременное редактирование проекта несколькими пользователями.
- Автоматическая установка произвольных Blender-аддонов.
- Гарантированная совместимость с любой когда-либо выпущенной версией Blender.

## 4. Технологический стек

| Слой | Технология | Назначение |
|---|---|---|
| Frontend | React + Vite | Интерфейс и production-сборка |
| Server state | TanStack Query | REST-кэш и синхронизация состояния |
| UI state | Zustand | Локальное состояние интерфейса |
| Backend | FastAPI + Uvicorn | HTTP API и WebSocket |
| Валидация | Pydantic | Контракты запросов, событий и конфигурации |
| База | SQLite | Задания, настройки и история |
| ORM/миграции | SQLAlchemy + Alembic | Доступ к SQLite и изменение схемы |
| Blender runner | `asyncio.subprocess` | Безопасный запуск и остановка Blender |
| GPU monitoring | `pynvml` | Метрики и обнаружение NVIDIA GPU |
| Файлы | `aiofiles` | Асинхронная работа с загрузками |
| Обнаружение кадров | Polling output в runner, финальная reconciliation в manager | Регистрация готовых кадров |
| Preview | Pillow | Создание уменьшенных PNG |
| Тесты | pytest + pytest-asyncio | Unit и integration тесты backend |
| Frontend QA | Playwright | Smoke-сценарии, responsive и accessibility |

DragonflyDB и Redis в одноузловой версии не используются. Состояние находится в
SQLite, а live-события распространяются встроенным WebSocket event bus.

## 5. Общая схема

```text
Browser
  |
  | HTTP REST + WebSocket
  v
FastAPI backend
  |-- REST API
  |-- WebSocket event hub
  |-- Job manager
  |-- Scheduler
  |-- Sandbox runner
  |-- GPU monitor
  |-- SQLite
  |
  `-- Blender subprocess(es)
        |-- per-job cgroup v2 (RAM/swap/pids)
        |
        `-- /workspace/jobs/{job_id}/output
```

Frontend собирается Vite и в production обслуживается Nginx, который также
проксирует API/WebSocket в FastAPI. В development Vite работает на `5173`,
FastAPI — на `8000`.

Основной one-command flow для Ubuntu Pod скачивает проверенный bundle последнего
стабильного GitHub Release. Frontend уже собран; Node.js и исходники frontend на
GPU Pod не нужны. Backend и внутренний Nginx запускаются нативными процессами без
Docker и systemd. Nginx слушает `8080`, обслуживает SPA и проксирует REST,
WebSocket и artifacts без публичного Bearer injection; platform proxy завершает
HTTPS. Browser boundary — first-browser pairing cookie, а закрытый Bearer
остаётся только у loopback/service clients.
Все долгоживущие процессы работают как UID 10001, backend port `8000` остаётся
loopback-only.

Повторный запуск той же команды выполняет install, repair или update. Новый
release и его per-release `.venv` полностью готовятся отдельно, затем symlink
`/opt/render-node/current` меняется атомарно. Failed readiness возвращает
предыдущий symlink и процесс. Сохраняются active и один previous release.
Dockerfile используется только для альтернативного готового GHCR image, который
RunPod запускает напрямую без Docker daemon внутри Pod.

## 6. Структура репозитория

Основные runtime-модули и границы ответственности (вспомогательные schemas,
types и exceptions опущены):

```text
render-node/
|-- backend/
|   |-- app/
|   |   |-- main.py
|   |   |-- config.py
|   |   |-- lifespan.py
|   |   |-- access.py
|   |   |-- security.py
|   |   |-- api/
|   |   |   |-- router.py
|   |   |   |-- access.py
|   |   |   |-- jobs.py
|   |   |   |-- uploads.py
|   |   |   |-- artifacts.py
|   |   |   |-- devices.py
|   |   |   |-- blender.py
|   |   |   |-- events.py
|   |   |   `-- system.py
|   |   |-- blender/
|   |   |   |-- command.py
|   |   |   |-- runner.py
|   |   |   |-- sandbox.py
|   |   |   |-- cgroups.py
|   |   |   |-- devices.py
|   |   |   |-- progress.py
|   |   |   |-- service.py
|   |   |   |-- official.py
|   |   |   |-- storage.py
|   |   |   `-- adapters/
|   |   |-- jobs/
|   |   |   |-- manager.py
|   |   |   |-- progress.py
|   |   |   |-- service.py
|   |   |   |-- upload_service.py
|   |   |   |-- models.py
|   |   |   `-- repository.py
|   |   |-- artifacts/
|   |   |   |-- service.py
|   |   |   |-- models.py
|   |   |   `-- repository.py
|   |   |-- events/
|   |   |   `-- hub.py
|   |   |-- system/
|   |   |   `-- metrics.py
|   |   `-- storage/
|   |       |-- database.py
|   |       |-- uploads.py
|   |       `-- jobs.py
|   |-- migrations/
|   |-- tests/
|   |-- pyproject.toml
|   `-- alembic.ini
|-- frontend/
|   |-- src/
|   |   |-- App.jsx
|   |   |-- AccessGate.jsx
|   |   |-- features/
|   |   |   |-- artifacts/Artifacts.jsx
|   |   |   `-- blender/VersionPanel.jsx
|   |   |-- ui/
|   |   |   |-- components.jsx
|   |   |   `-- dialogHooks.js
|   |   |-- api.js
|   |   |-- realtime.js
|   |   |-- jobPresentation.js
|   |   |-- store.js
|   |   `-- styles.css
|   |-- scripts/
|   |-- package.json
|   `-- vite.config.js
|-- deploy/
|   |-- nginx.conf.template
|   |-- container-entrypoint.sh
|   |-- supervisor.sh
|   |-- cgroup-delegation.sh
|   `-- README.md
|-- scripts/
|   |-- build-release-bundle.sh
|   |-- dev.py
|   `-- render-node
|-- docs/
|   |-- README.md
|   |-- development.md
|   |-- architecture.md
|   |-- implementation-state.md
|   |-- i18n/
|   `-- assets/
|-- .github/workflows/release.yml
|-- Dockerfile
|-- install.sh
`-- README.md
```

Production layout не зависит от checkout репозитория:

```text
/opt/render-node/releases/<version>  immutable release и per-release .venv
/opt/render-node/current             symlink на active release
/opt/render-node/blender/<version>   bundled Blender 5.2.0/4.1.1
/workspace/.render-node              persistent private config and logs
/workspace/database                  SQLite
/workspace/jobs                      scenes и результаты
/workspace/blender                   дополнительные Blender runtimes
/run/render-node                     PID и непостоянные gateway-файлы
```

Bundle создаётся локально запускаемым скриптом из явного manifest: backend
`app`, migrations, Alembic/pyproject/lock, готовый `frontend/dist`, единый Nginx
template, supervisor, cgroup delegation helper и management command. Frontend source, tests, `.venv`,
`node_modules`, caches, Blender binaries, uploads и secrets в bundle отсутствуют.
Stable tag `vX.Y.Z` публикуется только после backend/frontend checks; `master`
может публиковать только `latest`/SHA GHCR image после тех же checks.

### 6.1 Frontend architecture

`App.jsx` координирует layout, Job Setup, Preview, Jobs и System Metrics.
Artifacts и управление версиями Blender выделены в `features/`; общие визуальные
компоненты и lifecycle диалогов находятся в `ui/`. Presentation helpers остаются
небольшими независимыми модулями. TanStack Query владеет server state: jobs, artifacts, Blender
versions, operations и system metrics. Zustand хранит выбранный job и состояние
панели версий; тема использует React state и `localStorage` в `App.jsx`.
Бизнес-правила переходов
job и установки Blender во frontend не дублируются.

Основной desktop-интерфейс состоит из Job Setup, Preview, Jobs, Artifacts и
System Metrics. На viewport до 820 px Job Setup располагается перед Preview,
чтобы главное действие запуска было доступно в первом экране. System Metrics
показывает не более двух строк одновременно и прокручивает остальные внутри
панели. Jobs также имеет ограниченную высоту и внутреннюю прокрутку. История
показывается серверными страницами не более чем по 10 jobs; следующая страница
запрашивается только после перехода пользователя. Стрелка job на desktop
открывает сдвигом красное действие удаления; на touch-устройствах тот же слой
открывается горизонтальным свайпом. `QUEUED` и `RENDERING` удалить нельзя.
Подтверждение требуется только при наличии артефактов, после удаления выбранной
записи frontend выбирает следующую доступную.

Jobs показывает duration без отдельного поля БД: для `RENDERING` это
`now - started_at`, для terminal job — фиксированное
`finished_at - started_at`. Current task раз в секунду вычисляет elapsed и
приблизительную ETA из authoritative overall `progress`: до 5 секунд или 2%
показывается `Estimating…`, затем raw ETA сглаживается EWMA с `alpha=0.2` и
монотонным входным progress. ETA и worker snapshots остаются transient frontend
state и не восстанавливаются отдельным REST endpoint.

Один CPU/GPU process использует компактную общую шкалу. Запущенный
`FRAME_PER_GPU` с двумя и более workers показывает стабильные UUID-строки в
порядке выбранных GPU и `Overall` снизу. `ALL_GPUS_PER_FRAME` показывает одну
строку `All GPUs`. При добавлении worker rows внешняя высота центральной панели
не меняется: Current task растёт вверх, Preview уменьшается, одновременно видны
не более четырёх workers, остальные прокручиваются внутри списка.

Все модальные окна используют общий lifecycle:

- закрытие по `Escape`, кнопке и нажатию на backdrop;
- блокировку прокрутки фоновой страницы;
- начальный фокус внутри диалога и focus trap;
- возврат фокуса к открывшему элементу;
- анимацию только через `opacity` и `transform` с учётом
  `prefers-reduced-motion`.

Settings остаётся UI-заглушкой, пока не определены реальные настройки. Наличие
кнопки не создаёт backend API само по себе.

## 7. Доменная модель

### 7.1 Job

```text
id: UUID
name: string
source_filename: string
status: JobStatus
blender_version: string
engine: CYCLES | BLENDER_EEVEE | BLENDER_WORKBENCH
device: CPU | CUDA | OPTIX
gpu_ids: list[int]
gpu_render_mode: FRAME_PER_GPU | ALL_GPUS_PER_FRAME
frame_mode: SINGLE | RANGE | ALL
frame_start: int | null
frame_end: int | null
current_frame: int | null
progress: float
process_pid: int | null
created_at: datetime
started_at: datetime | null
finished_at: datetime | null
exit_code: int | null
error: string | null
```

### 7.2 Статусы

```text
CREATED -> READY -> QUEUED -> RENDERING -> COMPLETED
                         |       |-> FAILED
                         v       `-> CANCELLED
                       FAILED
```

Допустимые переходы проверяются в `JobManager`. API не может напрямую менять
статус произвольным значением. Загрузка не получает отдельный статус, пока MVP
принимает файл одним запросом. При отмене job остаётся `RENDERING` до фактической
остановки процесса и только затем становится `CANCELLED`. Job хранит точную
patch-версию Blender, которую нельзя изменить после постановки в очередь.

### 7.3 RenderArtifact

```text
id: UUID
job_id: UUID
filename: string
kind: IMAGE | VIDEO | LOG | OTHER
frame: int | null
size_bytes: int
mime_type: string
created_at: datetime
```

### 7.4 BlenderRuntime

```text
version: string
source: BUNDLED | OFFICIAL | MANUAL
state: AVAILABLE | DOWNLOADING | DOWNLOADED | INSTALLING | INSTALLED | FAILED
supported: bool
active: bool
archive_path: string | null
official_filename: string | null
expected_sha256: string | null
verified_sha256: string | null
operation_id: UUID | null
error: string | null
```

`AVAILABLE` означает наличие версии в официальном каталоге, но не на локальном
диске. `DOWNLOADED` означает проверенный и сохранённый архив, готовый к отдельной
установке. Установка не активирует версию автоматически. Для обнаруженных и
проверенных bundled-версий состояние всегда `INSTALLED`, а удалить их через API
нельзя. Запись bundled без доступного бинарника удаляется из runtime-реестра при
старте.

`source` описывает способ получения: `MANUAL` означает upload через frontend, а
не другой уровень доверия. Runtime в состоянии `DOWNLOADED` создаётся только
после совпадения `verified_sha256` с официальным `expected_sha256`.

### 7.5 BlenderOperation

```text
id: UUID
kind: DOWNLOAD | UPLOAD | INSTALL
version: string | null
state: PENDING | RUNNING | COMPLETED | FAILED
progress: float | null
bytes_processed: int | null
bytes_total: int | null
error: string | null
created_at: datetime
finished_at: datetime | null
```

Операция нужна для длительных download, upload и install без удержания HTTP-
соединения. Одновременно разрешена только одна изменяющая Blender операция.

### 7.6 SystemStorage

```text
id: int
name: string
mount_point: string
total_bytes: int
free_bytes: int
read_mbps: float
write_mbps: float
status: healthy | low_space
```

Backend публикует только файловые системы, которые используются сервисом:
workspace, jobs и Blender versions. Пути на одной файловой системе объединяются
в одну строку. Внутренние pseudo-filesystems не попадают в API.

## 8. Файловая модель

```text
/workspace/
|-- database/
|   `-- render-node.sqlite3
|-- jobs/
|   `-- {job_id}/
|       |-- input/
|       |   `-- scene.blend
|       |-- output/
|       |   |-- frame_0001.png
|       |   `-- frame_0002.png
|       |-- preview/
|       |   `-- frame_0002.png
|       |-- logs/
|       |   |-- blender.log
|       |   |-- blender-gpu-0.log
|       |   `-- blender-gpu-1.log
|       `-- temp/
`-- blender/
    |-- versions/
    |-- downloads/
    `-- quarantine/
```

Файлы каждого задания находятся в отдельной директории. Пользовательские имена не
используются как директории. Все пути формируются backend из UUID и проверяются
на выход за пределы `/workspace/jobs`.

Official archives после download находятся в `blender/downloads` под именем,
сформированным backend. Manual uploads сначала находятся только в
`blender/quarantine/{operation_id}`. Ни пользовательское имя, ни номер версии не
используются как доверенный путь.

## 9. HTTP API

### Jobs

```text
POST   /api/v1/jobs
GET    /api/v1/jobs
GET    /api/v1/jobs/page?page=1&page_size=10
GET    /api/v1/jobs/{job_id}
PUT    /api/v1/jobs/{job_id}
DELETE /api/v1/jobs/{job_id}
POST   /api/v1/jobs/{job_id}/start
POST   /api/v1/jobs/{job_id}/cancel
POST   /api/v1/jobs/{job_id}/retry
POST   /api/v1/jobs/{job_id}/rerender
```

`GET /jobs` сохранён для обратной совместимости. Frontend использует
`GET /jobs/page`: ответ содержит `items`, `page`, `page_size`, `total` и
`pages`, а `page_size` ограничен значением 10.

`DELETE /jobs/{job_id}` полностью удаляет каталог job и artifact metadata.
Для `QUEUED` и `RENDERING` он возвращает `409 active_job_cannot_be_deleted`.

`PUT` заменяет render-настройки целиком и разрешён только для `CREATED` и
`READY`. После первого перехода в `QUEUED` конфигурация и входная сцена
неизменяемы. `rerender` разрешён для `COMPLETED`, `FAILED` и `CANCELLED`: он
создаёт отдельный `READY` job с копией настроек и безопасной серверной копией
input исходного job. Артефакты и runtime-состояние при этом не копируются.

Поле `gpu_render_mode` принимает `FRAME_PER_GPU` и
`ALL_GPUS_PER_FRAME`. При создании без поля backend выбирает распределение
кадров только для CUDA/OptiX с двумя и более GPU; CPU и одна GPU нормализуются в
`ALL_GPUS_PER_FRAME`. При update пропущенное совместимое значение сохраняется,
retry/rerender копируют его. Миграция `20260818_0005` заполняет существующие
строки безопасным legacy-значением `ALL_GPUS_PER_FRAME`.

### Uploads

```text
POST   /api/v1/jobs/{job_id}/uploads
```

Первая версия принимает один multipart-файл. Для `CREATED` это первичная
загрузка, для `READY` — атомарная замена сцены до запуска. Chunk upload
добавляется, когда реальные размеры сцен или нестабильное соединение подтвердят
эту необходимость.

### Artifacts

```text
GET    /api/v1/jobs/{job_id}/artifacts
GET    /api/v1/jobs/{job_id}/artifacts/{artifact_id}
DELETE /api/v1/jobs/{job_id}/artifacts/{artifact_id}
GET    /api/v1/jobs/{job_id}/frames?page=1&page_size=50
GET    /api/v1/jobs/{job_id}/frames/{frame}/preview
GET    /api/v1/jobs/{job_id}/frames/{frame}/original
GET    /api/v1/jobs/{job_id}/frames.zip
GET    /api/v1/jobs/{job_id}/logs/blender
```

`page_size` ограничен 50. Frame endpoint возвращает только зарегистрированные
artifacts и состояние готовности кадра. `original`, ZIP и log используют
`Content-Disposition: attachment`; preview допускает HTTP-кэширование. ZIP
создаётся потоково либо заранее как artifact и не собирается целиком в памяти.

### Devices

```text
GET    /api/v1/devices
GET    /api/v1/system/metrics
```

System metrics включает CPU, RAM, GPU и список `SystemStorage`. Для рабочего
хранилища backend вычисляет `LOW_SPACE`, когда свободно меньше 10% либо меньше
настраиваемого абсолютного порога, по умолчанию 50 GiB.

### Blender versions

```text
GET    /api/v1/blender/versions
GET    /api/v1/blender/releases
POST   /api/v1/blender/releases/{version}/download
POST   /api/v1/blender/versions/upload
POST   /api/v1/blender/versions/{version}/install
POST   /api/v1/blender/versions/{version}/activate
DELETE /api/v1/blender/versions/{version}
GET    /api/v1/blender/operations/{operation_id}
```

`versions` возвращает bundled, downloaded и installed runtimes. `releases`
лениво получает список непосредственно из официального архива Blender и
кэширует результат с ограниченным TTL. Backend, а не браузер, разбирает каталог
и выбирает Linux x64 artifact.

Checksum manifests загружаются только по HTTPS с `download.blender.org`, имеют
лимит размера и разбираются как соответствие `official_filename -> SHA-256`.
Неизвестный формат, неоднозначная запись, отсутствие manifest или сетевая ошибка
дают fail-closed результат: download/upload нельзя перевести в `DOWNLOADED`.

Official download, manual upload и install возвращают `202 Accepted` с
`operation_id`. Frontend получает состояние через operation endpoint и
WebSocket. После official download версия переходит в `DOWNLOADED`; только
после отдельного install она появляется в основном списке установленных версий.

Manual upload принимает только multipart Linux x64 `.tar.xz` или `.tar.bz2`;
версию, URL и checksum пользователь не передаёт. Backend не доверяет имени файла,
сохраняет поток в quarantine и одновременно вычисляет SHA-256. Полученный digest
ищется в локально кэшированном индексе официальных checksum manifests Blender.
Совпавшая запись определяет точные version, platform и official filename.

При отсутствии точного официального совпадения операция завершается ошибкой,
quarantine-файл удаляется, `BlenderRuntime` не создаётся и install недоступен.
При совпадении версия переходит в `DOWNLOADED`; установка всё равно выполняется
только отдельным явным запросом.

Удалить версию активного или поставленного в очередь задания нельзя. Активация
возвращает `200 OK` либо `409 Conflict`, если сейчас есть job в очереди или
рендере. Активной может быть только одна установленная версия.

## 10. WebSocket API

```text
WS /api/v1/events
```

После подключения клиент подписывается на нужные задания:

```json
{
  "action": "subscribe",
  "job_ids": ["d28e3f5f-8e1d-4a61-a01c-9d989eab6730"]
}
```

Примеры событий backend:

```json
{
  "type": "job.status_changed",
  "job_id": "d28e3f5f-8e1d-4a61-a01c-9d989eab6730",
  "status": "rendering"
}
```

```json
{
  "type": "render.progress",
  "job_id": "d28e3f5f-8e1d-4a61-a01c-9d989eab6730",
  "frame": 12,
  "sample": 384,
  "total_samples": 1024,
  "progress": 0.375
}
```

Для независимого worker событие расширяется обратно совместимыми полями:

```json
{
  "type": "render.progress",
  "job_id": "d28e3f5f-8e1d-4a61-a01c-9d989eab6730",
  "gpu_uuid": "GPU-acde0123",
  "frame": 12,
  "sample": 384,
  "total_samples": 1024,
  "worker_progress": 0.625,
  "progress": 0.375
}
```

`worker_progress` относится только к `gpu_uuid` этого snapshot. Поле
`progress` остаётся монотонным overall progress логического job. В
`ALL_GPUS_PER_FRAME` отдельного `worker_progress` нет: все UUID принадлежат
одному процессу. После reload frontend немедленно берёт Overall из Job REST
response, а worker rows ждут следующего live event.

```json
{
  "type": "render.preview_ready",
  "job_id": "d28e3f5f-8e1d-4a61-a01c-9d989eab6730",
  "artifact_id": "e7f20e20-fbe0-43cb-ad4e-a4aca29ab13f",
  "url": "/api/v1/jobs/d28e3f5f/artifacts/e7f20e20"
}
```

Preview передаётся URL, а не Base64 внутри WebSocket. Это уменьшает размер
сообщений и позволяет браузеру кэшировать изображения.

Дополнительные события, необходимые frontend:

```text
render.log
render.frame_ready
blender.operation_progress
blender.operation_completed
blender.operation_failed
system.metrics_updated
```

`render.log` содержит timestamp, stream, GPU UUID и одну нормализованную строку.
В `FRAME_PER_GPU` исходные потоки остаются в отдельных
`blender-gpu-{index}.log`, а совместимый `blender.log` получает строки с
префиксом UUID. `render.frame_ready` содержит номер кадра,
preview URL, original URL и признак готовности скачивания. Blender operation
events содержат `operation_id`, чтобы frontend не связывал состояние с текстом
кнопки или локальным таймером. System metrics можно отправлять не чаще одного
раза в секунду; при потере событий клиент восстанавливает состояние через REST.

## 11. Запуск Blender

Команда формируется только из валидированных значений и запускается без shell:

```python
process = await asyncio.create_subprocess_exec(
    version_manager.active_binary(expected_version=job.blender_version),
    "--background",
    "--factory-startup",
    "--disable-autoexec",
    str(blend_file),
    "--render-output",
    str(output_pattern),
    "--python-exit-code",
    "1",
    "--python",
    str(configure_script),
    "--render-frame",
    str(frame),
    "--",
    "--cycles-device",
    job.device.value,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.STDOUT,
    env=process_environment,
    cwd=job_directory,
    start_new_session=True,
)
```

Запрещается принимать готовую командную строку от frontend. Backend разрешает
только известные параметры и сам формирует список аргументов. Флаги
`--factory-startup` и `--disable-autoexec` обязательны для каждого job и не могут
быть отключены через API. `--enable-autoexec`, пользовательские `--python`,
`--python-expr`, `--python-text`, `--addons` и произвольные environment variables
запрещены.

`configure_script` выбирается backend по точной версии Blender, находится только
в read-only каталоге доверенных scripts и проверяется при старте приложения.
`--disable-autoexec` не блокирует этот явно переданный backend-скрипт, но
запрещает автоматические text blocks, Python drivers и startup handlers из
`.blend`.

### Sandbox worker

`.blend` и Blender subprocess считаются недоверенными. `--disable-autoexec`
является обязательной защитой, но не заменяет OS sandbox. Runner запускает
Blender через единый `SandboxRunner`; прямой вызов subprocess из API, scheduler
или произвольного service запрещён.

Минимальный профиль любого запуска:

- отдельная process group через `start_new_session=True`;
- `no-new-privileges`; production supervisor запускает backend и worker как UID 10001;
- закрытые посторонние file descriptors;
- очищенное окружение без backend secrets, `PYTHONPATH`, `PYTHONHOME` и
  пользовательских site-packages;
- отдельные `HOME`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME` и `TMPDIR` внутри
  `/workspace/jobs/{job_id}/temp`;
- доверенные version-specific backend scripts и проверенные пути input/output;
- рабочая директория текущего job;
- выбор назначенных GPU через UUID в `CUDA_VISIBLE_DEVICES` и Blender adapter;
- лимиты wall time, CPU и размера output, а также per-job cgroup v2 для
  фактически используемой RAM, swap и pids;
- `SIGTERM`, grace timeout и `SIGKILL` для всей process group в `finally`.

`RLIMIT_AS` не используется: CUDA/OptiX резервируют большие диапазоны
виртуального адресного пространства, не равные фактически занятой RAM. В
`strict`-режиме runner до exec создаёт отдельный cgroup v2, задаёт `memory.max`,
`pids.max`, доступные `memory.swap.max` и `memory.oom.group`, а child setup пишет
себя в `cgroup.procs`. Поэтому Blender и все его потомки наследуют один cgroup.
После любого результата runner читает `memory.events`, завершает оставшихся
членов через `cgroup.kill` или `SIGKILL` fallback и удаляет job-cgroup.

Версия и текущий путь cgroup определяются из `/proc/self/mountinfo` и
`/proc/self/cgroup`. Backend пишет только в делегированное writable-поддерево
текущего cgroup. cgroup v1 и read-only/неделегированный v2 дают явную
недоступность strict runner; production не переключается автоматически в
`trusted`. Локальный `trusted`-режим не создаёт per-job cgroup и явно полагается
на внешний лимит контейнера/Pod, также без `RLIMIT_AS`.

Границей выполнения является установленный Render Node: VM, контейнер или
облачный Pod. Отдельного operator-controlled trust profile и выключенного по
умолчанию runner нет. После установки и активации версии Blender через UI
scheduler может запускать её сразу. Browser pairing управляет доступом к узлу,
а не выбором subprocess trust mode. Multi-tenant render farm потребует отдельной
worker isolation architecture и не является режимом этого одноузлового продукта.
Runner не создаёт filesystem/network namespaces и не ограничивает GPU через
device cgroup. Проверка путей API и allowlist окружения не запрещают самому
Blender читать доступные service UID файлы соседних jobs или обращаться к сети.

Пример минимального окружения процесса:

```text
HOME=/workspace/jobs/{job_id}/temp/home
XDG_CONFIG_HOME=/workspace/jobs/{job_id}/temp/config
XDG_CACHE_HOME=/workspace/jobs/{job_id}/temp/cache
TMPDIR=/workspace/jobs/{job_id}/temp
CUDA_VISIBLE_DEVICES=GPU-<stable-nvml-uuid>[,GPU-<stable-nvml-uuid>...]
PYTHONPATH=
PYTHONHOME=
```

Некоторые сцены с Python drivers или scripted Freestyle при
`--disable-autoexec` рендерятся иначе либо завершаются ошибкой. MVP сообщает
несовместимость через job error и log, но не предлагает пользователю включить
autoexec. Trusted-script mode не входит в MVP.

### Версии Blender

Frontend отдельно показывает предустановленные/установленные версии и лениво
открываемый официальный каталог. Для official download он передаёт backend
только номер версии: URL архива от клиента не принимается. Backend самостоятельно
находит Linux x64 архив в `https://download.blender.org/release/`, проверяет
официальный SHA-256 и сохраняет его в cache. Скачивание не распаковывает и не
активирует Blender.

При отдельном install backend повторно проверяет digest, безопасно распаковывает
архив во временную директорию и запускает `blender --version`. После проверки
каталог атомарно переносится в `/workspace/blender/versions/{version}`. Версии из
образа находятся в `/opt/render-node/blender/{version}` и не скрываются
persistent volume. Если после потери registry-записи точный каталог версии уже
существует, явный install может восстановить запись только после fail-closed
проверки: каталог и executable не являются symlink, а `blender --version`
сообщает ожидаемую patch-версию. Проверенный архив после неудачного install
сохраняется доступным для повторной установки без нового download.

Manual archive проходит тот же install pipeline, но до него загружается в
quarantine под server-generated UUID. До распаковки backend вычисляет SHA-256 и
ищет точное совпадение в официальных checksum manifests. Имя пользовательского
файла используется только для отображения и не влияет на version, platform или
путь. Несовпавший архив никогда не распаковывается и не запускается.

После официального совпадения backend запрещает absolute paths, `..`, device
files, опасные symlinks и превышение лимита распакованного размера. Проверка
`blender --version` выполняется только для уже подтверждённых официальных bytes
и должна вернуть patch-версию из manifest. `supported` определяется точной
версией и наличием адаптера, а не способом download/upload.

При каждом старте backend запускает `blender --version` для каждого найденного
bundled runtime и каждой registry-записи `INSTALLED` из workspace. Сравнивается
точная patch-версия. Отсутствующий или повреждённый runtime не мешает readiness:
bundled-запись удаляется, official/manual-запись переходит в `FAILED` и теряет
`active`, а пригодный проверенный архив сохраняется для повторного явного
install. Другой runtime при этом автоматически не активируется.

Образ может содержать закреплённые версии:

```text
5.2.0
4.1.1
```

Их точные URL и SHA-256 хранятся в manifest образа. Дополнительные официальные
версии можно установить, но `supported=true` получают только версии с адаптером
и smoke-тестом. Установленная неподдерживаемая версия отображается с
предупреждением и может быть явно выбрана без гарантий совместимости. Одновременно
выполняется не более одной Blender operation; перед download, upload и install
проверяются свободное место, лимит архива и безопасные пути. Встроенные версии
удалить через API нельзя. MVP устанавливает Linux x64 `.tar.xz` и `.tar.bz2`
только при наличии точной записи в официальном checksum manifest. Старые архивы
без доверенного официального SHA-256 не устанавливаются.

Сервис допускает не более одной `active_version`; после первого запуска и при
пустом registry активной версии нет. Download и install не активируют версию.
Переключение выполняется явно и запрещено, пока есть
`QUEUED` или `RENDERING` jobs. При создании job текущая активная patch-версия
записывается в него для истории и проверки перед запуском. Сам процесс Blender
существует только во время рендера, и одновременно запускается не более одного.

### Остановка

1. Job Manager отмечает запрос отмены, не меняя статус до остановки процесса.
2. Процессу отправляется `SIGTERM`.
3. Backend ждёт заданный timeout.
4. Если процесс не завершился, отправляется `SIGKILL`.
5. Считываются cgroup OOM counters, уничтожаются оставшиеся члены cgroup и
   удаляется его каталог.
6. После очистки ресурсов статус фиксируется как `CANCELLED`.

Backend должен запускать Blender в отдельной process group, чтобы остановка
завершала также дочерние процессы.

## 12. Работа с GPU

`GpuDiscovery` использует общий обход NVML для inventory и метрик. В обычном
режиме он возвращает физическую NVIDIA GPU; при включённом MIG — доступные
compute instances этой GPU. Родительская карта не добавляется вместе с
MIG-разделами. Ошибка доступа к одной карте или одному разделу не скрывает
остальные устройства; отсутствие поддержки MIG сохраняет обычный GPU-путь.

Оба типа устройств используют одну модель и возвращают:

- стабильный UUID;
- числовой логический ID внутри процесса backend;
- название;
- загрузку;
- занятую и общую VRAM;
- температуру;
- энергопотребление.

Таблица UUID → ID принадлежит `GpuDiscovery` и сохраняется до рестарта backend.
Она общая для inventory и телеметрии: смена физических/MIG-индексов и временная
недоступность соседнего устройства не перенумеровывают уже известные устройства.
NVML session и эта таблица защищены одним thread lock, поскольку scheduler и
сбор метрик выполняют синхронные запросы через `asyncio.to_thread`. Handles
используются только внутри session и заново запрашиваются при каждом обходе.

Публичный контракт задания сохраняет список числовых `gpu_ids`. Scheduler при
старте фиксирует inventory и переводит ID в UUID. Резервации, повторная проверка
доступности и ограничение subprocess используют UUID, поэтому пересозданный
MIG-раздел с новым UUID не может заменить прежнее выбранное устройство. Новые
устройства становятся доступными для выбора после рестарта backend.

Один и тот же worker environment передаёт UUID обычных GPU или MIG-разделов:

```text
CUDA_VISIBLE_DEVICES=GPU-<uuid-a>,GPU-<uuid-b>
CUDA_VISIBLE_DEVICES=MIG-<uuid-a>,MIG-<uuid-b>
```

Недоступные utilization/temperature/power возвращаются как `null` независимо
друг от друга в REST и WebSocket. Capacity/used VRAM читаются с handle самого
устройства; родительские показатели не подставляются вместо MIG-метрик.
Frontend преобразует оба источника через `metricPresentation.js` и показывает
прочерк без графика для неизвестного показателя, сохраняя настоящий ноль.
Переподключение WebSocket и `resync.required` обновляют также кэш `/devices`,
чтобы UI перечитал startup inventory после рестарта backend.

Обнаружение MIG не гарантирует поддержку любого render engine/runtime.
Видимость нескольких MIG-разделов в одном процессе определяется ограничениями
[CUDA и драйвера NVIDIA](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/mig-device-names.html#cuda-device-enumeration).
Аппаратные CUDA/OptiX проверки остаются отдельными от fake Blender tests.

### Режимы планирования

**All GPUs per frame (`ALL_GPUS_PER_FRAME`)** — один Blender-процесс видит все
выбранные UUID и считает кадры последовательно. Подходит для тяжёлых одиночных
изображений и сохраняет поведение jobs, созданных до миграции.

**Frame per GPU (`FRAME_PER_GPU`)** — отдельный Blender-процесс видит ровно один
UUID и получает свою часть кадров. Обычно обеспечивает лучший throughput для
анимации. `SINGLE` запускает только worker первой GPU. Для RANGE число workers
не превышает длину включительного API-диапазона; для ALL используются все
выбранные GPU.

После загрузки `.blend` доверенный backend-адаптер читает фактические
`frame_start`, `frame_end` и `frame_step` сцены. Worker `i` получает
round-robin-последовательность `start + i*scene_step` с шагом
`scene_step*worker_count`; RANGE ограничивает её API-границами. Такой контракт
не дублирует и не пропускает кадры, включая `frame_step > 1`.

`GpuScheduler` атомарно резервирует все UUID логического job до общего
`finally`. Все workers разделяют один per-job cgroup, абсолютный deadline,
output limit и log budget, но имеют отдельные process groups и temp/home/cache.
Ошибка запуска или выполнения одного worker, cancel, timeout или превышение
лимита останавливает все peer groups до освобождения GPU.

## 13. Очередь и Scheduler

В одноузловой версии очередь находится в SQLite, а scheduler работает как
долгоживущая `asyncio.Task` внутри backend.

Алгоритм:

1. Если активного процесса нет, найти старейший `QUEUED` job.
2. Проверить наличие запрошенных GPU.
3. Зарезервировать все выбранные GPU по UUID.
4. Создать per-job cgroup и запустить один Blender либо группу workers согласно
   `gpu_render_mode`.
5. Передавать GPU-tagged логи и сериализованно обновлять общий прогресс.
6. В `finally` удалить cgroup и освободить все GPU.
7. После завершения перейти к следующему job.

После перезапуска backend задание со статусом `RENDERING` переводится в `FAILED`
с причиной прерывания. Пользователь может повторить его.

FastAPI `BackgroundTasks` не используется для длительного рендера: процессами
управляет отдельный Job Manager с явным жизненным циклом.

## 14. Progress parser

Сырые строки Blender всегда записываются в per-worker raw log и совместимый
`blender.log`. Дополнительно parser
пытается извлечь:

- текущий кадр;
- номер sample;
- общее количество samples;
- время;
- расход памяти;
- путь сохранённого файла;
- сообщения CUDA/OptiX;
- traceback Python.

Неизвестная строка не считается ошибкой и отправляется как событие `render.log`.
Для Blender 4.1 поддерживается фактический формат
`Fra:1 ... | Sample 48/512` (включая необязательные пробелы), а legacy-строки
`Rendering 48 / 512 samples` сохраняются для совместимости с fake/старыми
адаптерами. В распределённом режиме общий progress вычисляется под lock как
сумма завершённых кадров и активных worker fractions, остаётся монотонным и
записывается как состояние одного job. Output path дедуплицируется до
регистрации artifact, поэтому `render.frame_ready` и preview публикуются один
раз. В том же сериализованном snapshot вычисляется монотонный
`worker_progress`: завершённые назначенные кадры плюс sample fraction только
текущего кадра конкретного UUID. Frontend сопоставляет snapshots по UUID;
отсутствие transient snapshot не сбрасывает persisted Overall.

## 15. Preview и результаты

Runner опрашивает output directory, сравнивает размеры файлов между snapshots
и выполняет заключительный snapshot после завершения Blender. Manager повторно
сканирует output перед фиксацией terminal state, чтобы восстановить пропущенные
artifacts. Preview:

1. Проверяет, что путь принадлежит output-директории job.
2. Проверяет поддерживаемый формат и лимит числа пикселей.
3. Создаёт PNG через Pillow с ограничением ширины и высоты. EXR сохраняется как
   original без автоматического preview.
4. Регистрирует artifact в SQLite.
5. Отправляет `render.preview_ready` с HTTP URL.

Оригинальное изображение не перекодируется.

Frontend показывает последние строки `render.log` поверх preview, но это только
presentation: полный `blender.log` остаётся artifact и скачивается отдельно.
Кнопка полноразмерного просмотра загружает original либо browser-safe
представление по HTTP в адаптивный modal. Кнопка скачивания кадра появляется
только после `render.frame_ready`; WebSocket не передаёт бинарные изображения.

Artifacts относятся к выбранному job. Последовательность запрашивается
страницами по 50 кадров, а переход между страницами не загружает thumbnails всех
кадров. ZIP собирается во временный файл на диске с проверкой лимита размера;
ZIP и отдельные originals скачиваются по HTTP без загрузки целиком в память.

## 16. Безопасность

- Backend не выполняет пользовательские shell-команды.
- Имена файлов очищаются, пути проверяются через `resolve()`.
- ZIP распаковывается с защитой от Zip Slip и лимитом суммарного размера.
- Размер upload и количество chunks ограничены.
- Blender всегда запускается с `--factory-startup`, `--disable-autoexec` и
  доверенным version-specific backend-скриптом.
- Blender запускается через единый `SandboxRunner` внутри границы установленного
  Render Node; отдельного конфигурационного разрешения для runner нет.
- Окружение subprocess строится по allowlist и не наследует backend secrets,
  `PYTHONPATH`, `PYTHONHOME` или пользовательские site-packages.
- Jupyter не входит в публичный runtime-образ.
- Порт SQLite и внутренние процессы наружу не публикуются.
- Первый deliberate browser POST атомарно создаёт process-local session;
  обычные page GET, probes, `HEAD` и `OPTIONS` access state не меняют. Session
  token имеет не менее 256 бит энтропии, на сервере хранится только digest, а
  production cookie использует `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/`
  и `__Host-` prefix.
- Дополнительные браузеры получают session только по одноразовому 256-bit
  fragment invitation со сроком пять минут. Одновременно допускается пять
  sessions; restart одного Uvicorn worker или локальный `reset-access` очищает
  sessions/invitations без изменения jobs.
- Browser REST требует session cookie и `Sec-Fetch-Site: same-origin`; mutations
  дополнительно требуют `X-Render-Node-Request: 1`. WebSocket требует session,
  валидный HTTPS Origin и фиксированный `render-node-browser-v1` subprotocol;
  присутствующий `Sec-Fetch-Site` обязан быть `same-origin`, а subprotocol
  служит явным compatibility context для Chromium handshake без Fetch Metadata.
- Browser `Origin` строго разбирается, но не сравнивается с backend-visible
  `Host`, поэтому внешний proxy может переписать upstream Host. `Forwarded` и
  `X-Forwarded-*` не участвуют в решении, CORS не включается, а cross-origin
  frontend/API не поддерживается.
- Закрытый bearer token обязателен в production только для origin-less
  loopback/service clients и reset. Публичный gateway не подставляет его в
  browser HTTP/WebSocket; Basic Auth, username/password и credentials state
  отсутствуют.
- Аддоны по умолчанию запрещены; доверенные аддоны устанавливает администратор.
- Manual Blender archive хранится в quarantine и не распаковывается до точного
  совпадения SHA-256 с официальным checksum manifest для Linux x64.
- Пользователь не может передать доверенный checksum, version или download URL.
  Несовпавший archive удаляется и никогда не запускается.
- После checksum match отдельно проверяются пути, symlinks, число файлов,
  суммарный распакованный размер и точная версия бинарника.

`.blend` считается потенциально недоверенным файлом, поэтому сохраняются
`--disable-autoexec`, allowlisted subprocess environment, безопасные пути и
лимиты. VM/контейнер/Pod является внешней границей установленного узла. Для
отдельного multi-tenant продукта понадобилась бы дополнительная worker sandbox
без сети и с отдельными filesystem/device boundaries.

## 17. Конфигурация

Настройки читаются из environment variables через Pydantic Settings:

```text
RENDER_NODE_ENV=production
RENDER_NODE_RESOURCE_ISOLATION_MODE=strict
# optional: pre-delegated child of the current cgroup
RENDER_NODE_CGROUP_ROOT=/sys/fs/cgroup/render-node
RENDER_NODE_WORKSPACE=/workspace
RENDER_NODE_DATABASE_URL=sqlite+aiosqlite:////workspace/database/render-node.sqlite3
RENDER_NODE_BLENDER_ROOT=/workspace/blender/versions
RENDER_NODE_BUNDLED_BLENDER_ROOT=/opt/render-node/blender
RENDER_NODE_MAX_UPLOAD_GB=20
RENDER_NODE_MAX_BLENDER_ARCHIVE_GB=2
RENDER_NODE_RELEASE_CATALOG_TTL_SECONDS=3600
RENDER_NODE_LOW_SPACE_PERCENT=10
RENDER_NODE_LOW_SPACE_GB=50
RENDER_NODE_BLENDER_TIMEOUT_SECONDS=21600
RENDER_NODE_BLENDER_TERM_GRACE_SECONDS=15
RENDER_NODE_MAX_RENDER_OUTPUT_GB=50
RENDER_NODE_WORKER_MEMORY_GB=16
RENDER_NODE_WORKER_SWAP_GB=0
RENDER_NODE_WORKER_PIDS_LIMIT=128
RENDER_NODE_MAX_ACTIVE_JOBS=1
RENDER_NODE_AUTH_TOKEN=...
```

Секреты не записываются в Dockerfile или git. Настроек trust profile/runner mode
нет: установленный узел всегда может запускать активированный через UI Blender.
Production требует `resource_isolation_mode=strict`.
`WORKER_MEMORY_GB` означает фактический `memory.max`, а не виртуальное адресное
пространство; `WORKER_SWAP_GB` и pids limit применяются только соответствующими
cgroup v2 controllers. Readiness API остаётся доступным для диагностики, но
Start/Retry fail-closed, если strict cgroup недоступен.

## 18. Логирование и наблюдаемость

Ошибки API и сбора метрик используют стандартный Python logging. Blender stdout
сохраняется в `blender.log` (и отдельных worker logs для `FRAME_PER_GPU`) и
публикуется через `render.log`. Единого JSON-формата backend log сейчас нет.

`/api/v1/system/metrics` возвращает:

- число активных WebSocket-клиентов;
- GPU utilization и VRAM;
- CPU utilization, RAM и температура, если sensor доступен;
- total/free space и read/write throughput рабочих файловых систем.

Состояние jobs и Blender operations, timestamps, progress и errors доступны
через их REST-модели и WebSocket events. Отдельных счётчиков очереди/завершений
и Prometheus endpoint пока нет.

## 19. Тестирование

### Unit

- переходы JobStatus;
- построение аргументов Blender;
- обязательные `--factory-startup`, `--disable-autoexec`,
  `--python-exit-code 1` и запрет пользовательских Python/autoexec flags;
- allowlist окружения worker и очистка `PYTHONPATH`, `PYTHONHOME` и secrets;
- валидация обязательного strict resource isolation в production;
- progress parser;
- защита путей и ZIP;
- обнаружение версий, SHA-256 и безопасная распаковка Blender;
- парсинг официального release-каталога и выбор Linux x64 archive;
- quarantine, лимиты и official checksum matching manual Blender upload;
- обязательный отказ и cleanup при неизвестном SHA-256;
- переходы Blender operation и запрет параллельных операций;
- правила выбора единственной активной версии;
- расчёт storage status и low-space thresholds;
- GPU scheduler;
- валидация/default/normalization `gpu_render_mode`;
- round-robin frame plan с фактическим scene `frame_step`;
- монотонная агрегация concurrent progress и дедупликация outputs;
- per-worker progress, UUID/sample binding, раннее завершение одного worker и
  совместимость shared-process режима;
- duration formatting, ETA guards/rounding/EWMA и strategy-aware progress rows;
- cgroup v2 detection/configuration, read-only/v1 failure, strict/trusted mode;
- отсутствие `RLIMIT_AS`, cgroup OOM counters и cleanup всех terminal paths;
- проверка API schemas.

### Integration

- создание job и upload;
- запуск fake Blender executable;
- запуск только через `SandboxRunner`, а не напрямую из API/Job Manager;
- containment путей API/uploads/artifacts и очистка worker environment;
- live-события и завершение;
- отмена всей process group, включая дочерний fake process;
- создание fake cgroup v2, наследование потомками и cleanup после
  success/failure/cancel/timeout/setup failure;
- UUID-резервация и освобождение одной/нескольких GPU без настоящей GPU;
- одновременный старт per-GPU fake workers, отдельные UUID/logs и общий cgroup;
- peer teardown при startup/failure/cancel/timeout и общий log/output budget;
- восстановление после перезапуска;
- создание preview и выдача artifact;
- пагинация кадров с `page_size <= 50`, original download и потоковый ZIP;
- official `download -> install -> activate`;
- manual `upload -> official checksum match -> install` с fake archive;
- отказ от manual archive без официального checksum и quarantine cleanup.

Fake cgroup fixtures проверяют настройку и lifecycle вызовов, но не enforcement
RAM/pids ядром. Kernel cgroup v2, реальный GPU и доступ к filesystem/network
нельзя считать проверенными этими тестами; filesystem/network isolation внутри
узла вообще не реализована.

### Frontend

- lint, все Node unit suites через `npm test` и production build;
- один основной Playwright smoke-сценарий desktop/mobile;
- selector multi-GPU стратегии: видимость, default, normalization, persistence
  и desktop/mobile screenshots;
- Job Setup находится перед Preview на mobile, а основное действие помещается в
  первый viewport;
- dropdown и segmented controls доступны с клавиатуры;
- модалки закрываются по `Escape`, удерживают фокус и восстанавливают его;
- список кадров содержит не более 50 элементов на страницу;
- version manager покрывает official `download -> install`, manual upload с
  checksum match, отказ неизвестного архива, activation и состояния ошибок;
- CPU, GPU и storage rows не создают горизонтальный overflow, дополнительные
  строки доступны через внутреннюю прокрутку.

### Blender smoke test (отдельная проверка реального runtime)

- запуск каждой версии из manifest без GPU;
- проверка заявленного номера версии;
- открытие тестовой `.blend` с registered Python text block: marker-файл не
  создаётся при `--disable-autoexec`;
- выполнение доверенного version-specific configure script при отключённом
  autoexec;
- создание минимальной сцены;
- рендер одного маленького кадра CPU;
- отдельный cloud-тест CUDA/OptiX.

## 20. Этапы реализации

### Этап 1 — базовый backend

- FastAPI application factory и lifespan.
- SQLite, Alembic и Job model.
- CRUD API для jobs.
- Изолированная файловая структура.

### Этап 2 — Blender runner

- Реестр bundled/official/manual версий.
- Official release catalog и операции download/install.
- Quarantine, official checksum matching и install pipeline для manual archives.
- Безопасное построение команды.
- `SandboxRunner`, обязательный disable-autoexec и изолированное окружение.
- Async stdout reader.
- Отмена и timeout.
- Progress parser.

### Этап 3 — frontend MVP

- Создание и просмотр jobs.
- Upload `.blend`.
- Просмотр, установка и выбор версии Blender.
- Раздельные Download и Install, ручной upload архива.
- Выбор кадра, движка и устройства.
- Live-лог поверх preview и статусы.
- Полноразмерный кадр, пагинация по 50 и скачивание frame/ZIP/log.

### Этап 4 — preview и мониторинг

- File watcher и thumbnails.
- NVML GPU metrics.
- CPU/RAM metrics.
- Метрики рабочих файловых систем и low-space status.
- Графики frontend.

### Этап 5 — очередь и multi-GPU

- GPU reservations.
- Несколько jobs в очереди.
- Multi-device single frame.
- Разделение анимации по GPU.

### Этап 6 — production hardening

- Авторизация.
- Upload limits и resumable upload.
- Cleanup policy.
- Cleanup загруженных Blender archives и abandoned quarantine operations.
- Single-tenant deployment для временно арендованного узла без trust/profile toggle.
- Production worker sandbox без сети, соседних jobs и backend secrets перед
  shared deployment.
- Health/readiness endpoints.
- Непривилегированный контейнер.
- Backup SQLite и результатов.

## 21. Переход к нескольким render nodes

SQLite и встроенная очередь подходят для одного Pod. При горизонтальном
масштабировании компоненты заменяются по границам интерфейсов:

```text
SQLite repository -> PostgreSQL repository
In-process queue   -> Redis Streams / RabbitMQ
Local artifacts    -> S3-compatible object storage
Local scheduler    -> central coordinator
```

Frontend API при этом не должен существенно измениться. Render worker становится
отдельным процессом, который получает job, скачивает входные файлы, запускает
Blender и публикует результаты.

## 22. Решения для MVP

- Backend: Python/FastAPI.
- Frontend: React/Vite.
- База: SQLite.
- Realtime: нативный WebSocket.
- Один Pod и одна активная логическая job; `FRAME_PER_GPU` допускает несколько
  Blender processes внутри этой job.
- Каждый `.blend` запускается через `SandboxRunner` с `--factory-startup` и
  `--disable-autoexec`; production требует strict resource isolation.
  Shared/multi-tenant режим с отдельной worker sandbox не реализован.
- `RLIMIT_AS` не применяется. Strict production требует writable delegated
  cgroup v2 на job; local trusted fallback использует внешний лимит узла и явно
  отображается в diagnostics.
- Если образ содержит Blender 5.2.0 и 4.1.1, backend проверяет их бинарники и
  точные версии перед регистрацией; одна установленная версия явно выбирается
  как рабочая для всех jobs.
- Результаты в `/workspace/jobs`.
- Official Blender использует явный `download -> install`; manual archive
  использует `upload -> official checksum match -> install`. Архив без точного
  официального SHA-256 отклоняется и никогда не активируется автоматически.
- Frontend получает server state через TanStack Query и realtime-события, а
  Zustand не хранит копии jobs, artifacts или system metrics.
- System metrics включают CPU, GPU и только рабочие storage mounts.
- Кадры выдаются страницами не более 50 элементов; originals и ZIP скачиваются
  по HTTP, а не через WebSocket.
- Без DragonflyDB, Redis и Jupyter.
- Собственный интерфейс, название и графические материалы.
