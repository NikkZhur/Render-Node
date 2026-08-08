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
- Одна выбранная рабочая версия Blender для всех заданий. В образ входят 5.2.0
  и 4.1.1; дополнительные версии устанавливаются пользователем из официального
  архива.
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
| File watcher | `watchfiles` | Обнаружение готовых кадров |
| Preview | Pillow/OpenImageIO | Создание уменьшенных изображений |
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
        |
        `-- /workspace/jobs/{job_id}/output
```

Frontend собирается Vite и в production обслуживается FastAPI либо отдельным
reverse proxy. В development Vite работает на `5173`, FastAPI — на `8000`.

## 6. Структура репозитория

```text
render-node/
|-- backend/
|   |-- app/
|   |   |-- main.py
|   |   |-- config.py
|   |   |-- lifespan.py
|   |   |-- api/
|   |   |   |-- router.py
|   |   |   |-- jobs.py
|   |   |   |-- uploads.py
|   |   |   |-- artifacts.py
|   |   |   |-- devices.py
|   |   |   |-- versions.py
|   |   |   `-- websocket.py
|   |   |-- blender/
|   |   |   |-- command.py
|   |   |   |-- runner.py
|   |   |   |-- sandbox.py
|   |   |   |-- devices.py
|   |   |   |-- progress.py
|   |   |   |-- scripts.py
|   |   |   `-- versions.py
|   |   |-- jobs/
|   |   |   |-- manager.py
|   |   |   |-- scheduler.py
|   |   |   |-- models.py
|   |   |   `-- repository.py
|   |   |-- events/
|   |   |   |-- hub.py
|   |   |   `-- schemas.py
|   |   |-- monitoring/
|   |   |   |-- gpu.py
|   |   |   `-- system.py
|   |   |-- storage/
|   |   |   |-- database.py
|   |   |   |-- uploads.py
|   |   |   `-- artifacts.py
|   |   `-- security/
|   |       |-- auth.py
|   |       `-- paths.py
|   |-- migrations/
|   |-- tests/
|   |-- pyproject.toml
|   `-- alembic.ini
|-- frontend/
|   |-- src/
|   |   |-- app/
|   |   |-- features/
|   |   |   |-- jobs/
|   |   |   |-- render-preview/
|   |   |   |-- artifacts/
|   |   |   |-- blender-versions/
|   |   |   `-- system-metrics/
|   |   |-- shared/
|   |   |   |-- api/
|   |   |   |-- ui/
|   |   |   `-- hooks/
|   |   `-- store/
|   |-- scripts/
|   |-- public/
|   |-- package.json
|   `-- vite.config.js
|-- blender_scripts/
|   |-- configure_cycles_4_1.py
|   `-- report_scene.py
|-- docker/
|   `-- entrypoint.sh
|-- Dockerfile
|-- compose.yaml
`-- README.md
```

### 6.1 Frontend architecture

Frontend группируется по пользовательским возможностям, а не по типам React-
компонентов. TanStack Query владеет server state: jobs, artifacts, Blender
versions, operations и system metrics. Zustand хранит только локальное UI-
состояние: выбранное задание, тему и открытые панели. Бизнес-правила переходов
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
установке. Установка не активирует версию автоматически. Для bundled-версий
состояние всегда `INSTALLED`, а удалить их через API нельзя.

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
id: string
name: string
mount_point: string
total_bytes: int
free_bytes: int
read_bytes_per_second: int
write_bytes_per_second: int
status: HEALTHY | LOW_SPACE | READ_ONLY | UNAVAILABLE
```

Backend публикует только файловые системы, которые используются сервисом:
workspace, jobs, Blender versions и отдельное хранилище результатов, если оно
настроено. Внутренние pseudo-filesystems не попадают в API.

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
|       |   `-- frame_0002.webp
|       |-- logs/
|       |   `-- blender.log
|       `-- temp/
|-- blender/
|   |-- versions/
|   |-- downloads/
|   `-- quarantine/
`-- cache/
    `-- previews/
```

Каждое задание изолировано своей директорией. Пользовательские имена не
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
  "progress": 0.375,
  "elapsed_seconds": 41.2
}
```

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

`render.log` содержит timestamp, stream и одну нормализованную строку, но сырой
лог всё равно записывается на диск. `render.frame_ready` содержит номер кадра,
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
- непривилегированный worker без capabilities и с `no-new-privileges`;
- закрытые посторонние file descriptors;
- очищенное окружение без backend secrets, `PYTHONPATH`, `PYTHONHOME` и
  пользовательских site-packages;
- отдельные `HOME`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME` и `TMPDIR` внутри
  `/workspace/jobs/{job_id}/temp`;
- read-only Blender installation и version-specific backend scripts;
- read/write доступ только к директории текущего job;
- доступ только к назначенным GPU;
- лимиты wall time, CPU, RAM, pids и размера output;
- `SIGTERM`, grace timeout и `SIGKILL` для всей process group в `finally`.

Trust boundary выбирается явным deployment profile:

- `isolated_worker` — безопасное значение по умолчанию для shared deployment.
  Production sandbox запрещает сеть и доступ к database, соседним jobs, Unix
  sockets, container runtime socket, host devices кроме назначенных GPU и
  секретам backend. Пока отдельный worker container/namespace не реализован,
  scheduler в этом production-профиле не проходит readiness.
- `single_tenant` — весь временно арендованный VM/Pod принадлежит одному
  оператору и принимает только его собственные сцены. В production он разрешает
  прямой `local_trusted` subprocess; границей изоляции является сам облачный
  узел. Этот профиль не изолирует Blender от backend внутри узла и запрещён для
  общей render farm или загрузок от несвязанных пользователей.

Ни один production-профиль не выбирается неявно: прямой runner требует
одновременно `deployment_profile=single_tenant` и `runner_mode=local_trusted`.

Пример минимального окружения процесса:

```text
HOME=/workspace/jobs/{job_id}/temp/home
XDG_CONFIG_HOME=/workspace/jobs/{job_id}/temp/config
XDG_CACHE_HOME=/workspace/jobs/{job_id}/temp/cache
TMPDIR=/workspace/jobs/{job_id}/temp
CUDA_VISIBLE_DEVICES=0
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

Образ содержит закреплённые версии:

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

Сервис хранит одну `active_version`; по умолчанию это 5.2.0. Установка новой
версии не активирует её. Переключение выполняется явно и запрещено, пока есть
`QUEUED` или `RENDERING` jobs. При создании job текущая активная patch-версия
записывается в него для истории и проверки перед запуском. Сам процесс Blender
существует только во время рендера, и одновременно запускается не более одного.

### Остановка

1. Job Manager отмечает запрос отмены, не меняя статус до остановки процесса.
2. Процессу отправляется `SIGTERM`.
3. Backend ждёт заданный timeout.
4. Если процесс не завершился, отправляется `SIGKILL`.
5. После завершения процесса статус фиксируется как `CANCELLED`.

Backend должен запускать Blender в отдельной process group, чтобы остановка
завершала также дочерние процессы.

## 12. Работа с GPU

`pynvml` обнаруживает NVIDIA GPU и возвращает:

- стабильный UUID;
- индекс;
- название;
- загрузку;
- занятую и общую VRAM;
- температуру;
- энергопотребление.

Задание получает список `gpu_ids`. Доступ ограничивается через окружение:

```text
CUDA_VISIBLE_DEVICES=0,1
```

### Режимы планирования

**Multi-device frame** — несколько GPU совместно считают один кадр. Подходит для
тяжёлых одиночных изображений.

**Frame parallelism** — отдельный Blender-процесс на каждую GPU получает свою
часть кадров. Обычно обеспечивает лучший throughput для анимации.

Первый MVP поддерживает один процесс и один набор GPU. Поэтому отдельные GPU-locks
не нужны: наличие активного процесса уже является блокировкой. Резервации GPU и
параллельные процессы добавляются только вместе с параллельными jobs.

## 13. Очередь и Scheduler

В одноузловой версии очередь находится в SQLite, а scheduler работает как
долгоживущая `asyncio.Task` внутри backend.

Алгоритм:

1. Если активного процесса нет, найти старейший `QUEUED` job.
2. Проверить наличие запрошенных GPU.
3. Запустить Blender.
4. Передавать события и обновлять прогресс.
5. После завершения перейти к следующему job.

После перезапуска backend задание со статусом `RENDERING` переводится в `FAILED`
с причиной прерывания. Пользователь может повторить его.

FastAPI `BackgroundTasks` не используется для длительного рендера: процессами
управляет отдельный Job Manager с явным жизненным циклом.

## 14. Progress parser

Сырые строки Blender всегда записываются в `blender.log`. Дополнительно parser
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
Парсер покрывается тестами на сохранённых фрагментах вывода Blender 4.1.

## 15. Preview и результаты

Watcher реагирует только на закрытие или стабилизацию нового файла. Preview:

1. Проверяет, что путь принадлежит output-директории job.
2. Проверяет формат и максимальный размер.
3. Создаёт WebP/JPEG с ограничением ширины и высоты.
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
кадров. ZIP и отдельные originals обслуживаются потоково с лимитами размера и
времени.

## 16. Безопасность

- Backend не выполняет пользовательские shell-команды.
- Имена файлов очищаются, пути проверяются через `resolve()`.
- ZIP распаковывается с защитой от Zip Slip и лимитом суммарного размера.
- Размер upload и количество chunks ограничены.
- Blender всегда запускается с `--factory-startup`, `--disable-autoexec` и
  доверенным version-specific backend-скриптом.
- Blender запускается через единый `SandboxRunner`; прямой production runner
  разрешён только на однопользовательском арендованном узле с явным профилем
  `single_tenant`, а shared deployment требует непривилегированного OS sandbox.
- Окружение subprocess строится по allowlist и не наследует backend secrets,
  `PYTHONPATH`, `PYTHONHOME` или пользовательские site-packages.
- Jupyter не входит в публичный runtime-образ.
- Порт SQLite и внутренние процессы наружу не публикуются.
- API защищается session cookie или bearer token.
- WebSocket проверяет ту же авторизацию, что REST API.
- CORS принимает только настроенные origins.
- Аддоны по умолчанию запрещены; доверенные аддоны устанавливает администратор.
- Manual Blender archive хранится в quarantine и не распаковывается до точного
  совпадения SHA-256 с официальным checksum manifest для Linux x64.
- Пользователь не может передать доверенный checksum, version или download URL.
  Несовпавший archive удаляется и никогда не запускается.
- После checksum match отдельно проверяются пути, symlinks, число файлов,
  суммарный распакованный размер и точная версия бинарника.

`.blend` считается потенциально недоверенным файлом во всех режимах. Основной
single-tenant сценарий принимает только сцены владельца временного узла и
использует сам VM/Pod как внешнюю границу доверия. Для shared/public deployment,
где сцены отправляют несвязанные пользователи, render worker дополнительно
отделяется sandbox-границей без сети и с лимитами CPU, RAM, VRAM, pids, времени
и размера результатов.

## 17. Конфигурация

Настройки читаются из environment variables через Pydantic Settings:

```text
RENDER_NODE_ENV=production
RENDER_NODE_DEPLOYMENT_PROFILE=single_tenant
RENDER_NODE_RUNNER_MODE=local_trusted
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
RENDER_NODE_MAX_OUTPUT_GB=50
RENDER_NODE_MAX_WORKER_PIDS=128
RENDER_NODE_MAX_ACTIVE_JOBS=1
RENDER_NODE_ALLOWED_ORIGINS=https://example.runpod.net
RENDER_NODE_AUTH_TOKEN=...
```

Секреты не записываются в Dockerfile или git.
`local_trusted` разрешён в production только вместе с явным
`single_tenant`; Pydantic Settings отклоняет его с профилем
`isolated_worker`. Readiness проверяет, что выбранная trust boundary доступна.

## 18. Логирование и наблюдаемость

Backend пишет структурированные JSON-логи:

```json
{
  "level": "info",
  "event": "blender.started",
  "job_id": "d28e3f5f-8e1d-4a61-a01c-9d989eab6730",
  "pid": 1241,
  "gpu_ids": [0]
}
```

Минимальные метрики:

- количество jobs по статусам;
- длительность подготовки и рендера;
- размер очереди;
- число активных WebSocket-клиентов;
- GPU utilization и VRAM;
- CPU utilization, RAM и температура, если sensor доступен;
- total/free space и read/write throughput рабочих файловых систем;
- прогресс и ошибки Blender download/upload/install operations;
- количество завершений, отмен и ошибок.

## 19. Тестирование

### Unit

- переходы JobStatus;
- построение аргументов Blender;
- обязательные `--factory-startup`, `--disable-autoexec`,
  `--python-exit-code 1` и запрет пользовательских Python/autoexec flags;
- allowlist окружения worker и очистка `PYTHONPATH`, `PYTHONHOME` и secrets;
- валидация запрета disabled sandbox в production;
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
- проверка API schemas.

### Integration

- создание job и upload;
- запуск fake Blender executable;
- запуск только через `SandboxRunner`, а не напрямую из API/Job Manager;
- запрет чтения соседнего job и записи вне текущей job directory;
- запрет сети и превышения pids/resource limits в production sandbox fixture;
- live-события и завершение;
- отмена всей process group, включая дочерний fake process;
- восстановление после перезапуска;
- создание preview и выдача artifact;
- пагинация кадров с `page_size <= 50`, original download и потоковый ZIP;
- official `download -> install -> activate`;
- manual `upload -> official checksum match -> install` с fake archive;
- отказ от manual archive без официального checksum и quarantine cleanup.

### Frontend

- lint и production build;
- один основной Playwright smoke-сценарий desktop/mobile;
- Job Setup находится перед Preview на mobile, а основное действие помещается в
  первый viewport;
- dropdown и segmented controls доступны с клавиатуры;
- модалки закрываются по `Escape`, удерживают фокус и восстанавливают его;
- список кадров содержит не более 50 элементов на страницу;
- version manager покрывает official `download -> install`, manual upload с
  checksum match, отказ неизвестного архива, activation и состояния ошибок;
- CPU, GPU и storage rows не создают горизонтальный overflow, дополнительные
  строки доступны через внутреннюю прокрутку.

### Blender smoke test

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
- Single-tenant production profile для временно арендованного узла.
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
- Один Pod и один активный Blender process.
- Каждый `.blend` запускается через `SandboxRunner` с `--factory-startup` и
  `--disable-autoexec`; production direct runner требует явного single-tenant
  профиля, а shared профиль остаётся fail-closed без изолированного worker.
- В образе закреплены Blender 5.2.0 и 4.1.1; одна версия явно выбирается как
  рабочая для всех jobs.
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
