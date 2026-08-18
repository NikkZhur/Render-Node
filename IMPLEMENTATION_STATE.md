# Implementation state

Краткий handoff backend-реализации. Код и миграции имеют приоритет, если этот
файл с ними расходится.

## Текущее состояние

- Обновлено: 2026-08-18 после полной реализации multi-GPU распределения кадров;
  first-browser pairing, same-origin и always-local runner сохранены.
- Фазы 1–6 из `BACKEND_IMPLEMENTATION_MASTER_PROMPT.md` завершены.
- Следующей фазы в master prompt нет; дальнейшая работа — отдельные deployment
  задачи, перечисленные ниже.
- Пользовательский `Untitled.blend` не изменялся.

## Реализовано

- Фазы 1–5: FastAPI/SQLite/Alembic, безопасные jobs/uploads, Blender registry,
  `SandboxRunner`, FIFO Job Manager, realtime, artifacts/previews и metrics.
- Bundled manifest допускает Blender 5.2.0 и 4.1.1, но backend регистрирует
  версию только после запуска реального executable и точного сравнения
  patch-версии. Пустой registry не мешает readiness, а версия не активируется
  автоматически. Адаптеры 4.5.11, 4.2.22 и 3.6.23 сохранены для явной
  пользовательской установки этих версий.
- Startup проверяет также все official/manual runtimes в состоянии `INSTALLED`.
  Повреждённая или исчезнувшая версия получает `FAILED`, теряет `active` и
  очищается с диска; пригодный verified archive сохраняется для повторного
  Install. Collision с найденной bundled-версией нормализует source и metadata,
  не оставляя бесхозный archive. Другой runtime автоматически не активируется.
- Runtime manager позволяет после подтверждения удалить скачанную или
  установленную дополнительную версию вместе с архивом. Bundled и активная
  версии остаются защищены; frontend синхронно обновляет versions/catalog cache.
- Повторный install безопасно восстанавливает registry для уже существующего
  version directory только после проверки обычного каталога, non-symlink
  executable и точного вывода `blender --version`. После failed install
  проверенный архив остаётся доступным для retry без повторного download.
- `Job setup` отображает конфигурацию выбранного job. `CREATED`/`READY` можно
  менять и заменять их scene upload; после первого start настройки и сцена
  блокируются. `New job` открывает пустой editable draft, а `Rerender` создаёт
  отдельный `READY` job с копией настроек и server-side копией исходного input,
  без наследования артефактов.
- Нижний footer `Job setup` имеет отдельный message-slot над Runtime: runtime,
  runner status и actions сохраняют положение, а operation error заполняет
  зарезервированное место без увеличения desktop-панели или перекрытия GPU.
- Панель Jobs имеет ограниченную высоту и внутреннюю прокрутку. Она показывает
  не более 10 записей на серверную страницу; остальные страницы запрашиваются
  только при переходе через компактную пагинацию.
- Удаление job открывается desktop-стрелкой или горизонтальным touch-свайпом:
  строка сдвигается и показывает красное действие с урной. `QUEUED`/`RENDERING`
  заблокированы, подтверждение появляется только при наличии артефактов. Job,
  сцена и результаты удаляются полностью; для удалённой выбранной записи
  выбирается следующий доступный job. Закрытая строка сохраняет непрозрачный
  фон при hover, поэтому скрытое действие удаления не просвечивает.
- Адаптер Blender 4.5 преобразует стабильное API-значение `BLENDER_EEVEE` в
  фактический идентификатор Blender 4.5 `BLENDER_EEVEE_NEXT`.
- Первый браузер атомарно получает защищённую семидневную cookie-сессию. До
  пяти устройств подключаются через одноразовую pairing-ссылку с пятиминутным
  TTL; токен передаётся только во fragment, удаляется до API-запроса и хранится
  backend только как SHA-256 digest. In-memory access state требует одного
  Uvicorn worker и намеренно сбрасывается при рестарте процесса.
- Browser boundary распознаёт `Origin`, Fetch Metadata, cookie и явный marker:
  требует валидный HTTPS Origin в production, `Sec-Fetch-Site: same-origin`,
  `X-Render-Node-Request: 1` для unsafe HTTP и paired cookie для остальных
  REST routes. WebSocket дополнительно использует фиксированный
  `render-node-browser-v1` subprotocol как compatibility context, когда реальный
  Chromium не присылает Fetch Metadata; присутствующий Fetch Metadata всё равно
  обязан быть `same-origin`. `Host` и forwarded-заголовки не участвуют в решении,
  поэтому внешний gateway может безопасно переписывать Host. Origin-less
  service clients по-прежнему используют внутренний Bearer token длиной 32+
  символа. `/health` и `/ready` публичны, Swagger/OpenAPI в production отключены.
- `CORSMiddleware` и статическая origin allowlist удалены: frontend и API
  поддерживаются только на одном публичном host. HSTS и общие browser security
  headers сохранены. JSON mutation body по умолчанию ограничен 1 MiB,
  клиентское WS-сообщение — 64 KiB; upload/archive limits остаются отдельными.
- Установленный Render Node всегда может запускать активированную через UI
  версию Blender: `deployment_profile`, `runner_mode` и legacy
  `allow_unsandboxed_runner` удалены. Старые environment variables игнорируются,
  поэтому после установки не требуется shell-команда или ручное редактирование
  `.env`. Pairing управляет доступом, а cgroup/external resource boundary —
  фактической доступностью runner.
- Добавлен production `linux/amd64` image: он потребляет тот же собранный Release
  bundle, скачивает Blender 5.2.0/4.1.1 только из официального архива и проверяет
  закреплённые SHA-256. Короткий entrypoint готовит volume и runtime state, затем
  единый supervisor запускает долгоживущие процессы как UID 10001.
- Внутренний Nginx обслуживает SPA на 8080 без Basic Auth и без подстановки
  Bearer для браузера. Он передаёт REST/WebSocket/download на loopback backend;
  внешний platform proxy завершает HTTPS, а first-browser pairing остаётся
  одинаковым у всех провайдеров.
- Основной `install.sh` поддерживает только Ubuntu 22.04/24.04 x86_64 GPU Pod.
  Он всегда скачивает latest stable Release bundle и `.sha256` либо explicit
  `vX.Y.Z`, безопасно проверяет tar paths/links, создаёт frozen per-release
  Python 3.13.7 `.venv` и не устанавливает Node.js/Docker/systemd. Public URL,
  Pod ID и `--origin` для установки не нужны.
- `/opt/render-node/current` переключается атомарно только после полной подготовки
  release; failed readiness восстанавливает предыдущий release. No-op, repair
  после потери `/opt`, update и jobs/internal-token persistence покрыты
  fake-command harness. Точные legacy-файлы `admin.htpasswd` и `credentials`
  удаляются только после успешной readiness, не затрагивая похожие файлы.
- `render-node` управляет process group без systemd. PID хранится только в
  `/run/render-node`; identity проверяет start time и ожидаемый supervisor
  command, поэтому stale/reused/foreign PID не принимается. Uninstall сохраняет
  database, jobs и дополнительные Blender runtimes.
- Dockerfile/GHCR image сохранены как отдельный альтернативный путь: RunPod сам
  запускает готовый image, Docker daemon внутри Pod не требуется.
- GitHub Actions сначала выполняет backend Ruff/strict mypy/full pytest и
  frontend lint/build. Один gated publish job создаёт bundle + checksum и
  non-draft/non-prerelease Release только для stable semver tag, затем публикует
  versioned GHCR image; `master` публикует только `latest`/SHA image.
- Корневой development launcher после миграций запускает backend, ожидает его
  публичный `/ready` до 60 секунд и только затем запускает frontend. При раннем
  завершении или неготовности backend frontend не запускается.
- Vite читает только server-side backend URL из корневого `.env`, использует
  текущий browser host для HMR и не определяет provider/domain. API/WS proxy не
  подставляет Bearer; browser Origin и Fetch Metadata доходят до backend даже
  при переписанном gateway Host.
- Start/retry выполняют runner preflight до перехода в `QUEUED`; недоступный
  scheduler/runner возвращает `runner_unavailable`, сохраняя исходный статус.
  Frontend показывает capability runner и блокирует Start при недоступности.
- Restart очищает per-job `temp` и input незавершённого `CREATED` upload.
  Start/retry проверяет наличие contained regular scene. Retry очищает старые
  output/preview/log/temp и artifact metadata, сохраняя input; delete освобождает
  runtime locks.
- Frontend mock API загружается только по `VITE_RENDER_NODE_MOCK=true`; mock
  fixtures/chunk отсутствуют в production bundle. Access Gate до монтирования
  приложения выполняет claim либо redeem из `#pair=...`, показывает locked
  recovery UI, а REST/WS делают ограниченный повторный claim после backend
  restart. `Pair another device` создаёт ссылку только по явному действию,
  поддерживает keyboard/focus trap/copy и не сохраняет секрет после закрытия.
  Относительные `/api` URL и `window.location` сохраняют REST и application
  WebSocket на текущем browser host.
- В real API mode frontend не подставляет фиктивную Blender 5.2.0. При пустом
  registry Header и Job Setup показывают `No active Blender version`, manager
  ведёт через `Download → Install → Activate`, а create/upload/start/rerender
  заблокированы. Исторические jobs и artifacts остаются доступны для просмотра;
  после activate Query cache обновляет UI без reload.
- README и env examples описывают auth/reverse proxy, limits, cleanup, backup и
  остаточные production-риски.
- Финальный аудит усилил official catalog: HTML/manifest читаются потоково с
  лимитом до буферизации, origin проверяется разбором URL, а download ограничен
  также фактически свободным местом при неизвестном `Content-Length`.
- Проверка устанавливаемого `blender --version` больше не наследует окружение и
  секреты backend, запускается с минимальным env в отдельной process group и
  убивает всю группу по timeout.
- `RLIMIT_CPU` учитывает параллельные render threads и больше не может завершить
  CPU-рендер раньше настроенного wall-time. Alembic создаёт отсутствующий
  родительский каталог абсолютной SQLite path, поэтому migration bootstrap
  работает на полностью новом workspace.
- `RLIMIT_AS` полностью удалён из Blender child setup: CUDA/OptiX больше не
  ограничены ложным пределом виртуального адресного пространства. Strict mode
  обнаруживает mount/current cgroup без предположений о `/sys/fs/cgroup`,
  требует delegated writable cgroup v2 и создаёт отдельную группу job с
  `memory.max`, optional `memory.swap.max`, `pids.max` и optional
  `memory.oom.group`.
- Blender присоединяется к job-cgroup до exec, поэтому его потомки наследуют
  группу. После success/failure/cancel/timeout/setup error сохраняется прежнее
  завершение process group, читаются `memory.events`, уничтожаются оставшиеся
  члены и удаляется cgroup. cgroup RAM OOM, Blender/CUDA VRAM OOM и обычный
  `SIGABRT` имеют разные job errors.
- Progress parser распознаёт реальный вывод Blender 4.1
  `Fra:1 ... | Sample 48/512` и вариант с пробелами, а также прежний
  `Rendering X / Y samples`. Job Manager объединяет sample текущего кадра с
  диапазоном кадров в persisted overall progress; WebSocket snapshot с
  `sample`/`total_samples` теперь отображается в Current task на desktop/mobile
  и очищается при смене кадра, resync или terminal status.
- Local/test `trusted` resource mode явно использует внешний лимит узла без
  per-job cgroup и без `RLIMIT_AS`; production принимает только `strict` и
  отказывает Start/Retry, если cgroup создать нельзя. cgroup v1 распознаётся и
  даёт проверенный fail-closed fallback, записи в него не выполняются.
- NVML inventory фиксируется при startup. Совместимые публичные `gpu_ids`
  переводятся в стабильные UUID; scheduler резервирует все UUID до `finally`,
  повторно проверяет их наличие и передаёт Blender UUID через
  `CUDA_VISIBLE_DEVICES`, включая multi-GPU process.
- Jobs поддерживают `FRAME_PER_GPU` и `ALL_GPUS_PER_FRAME`. Первый режим для
  RANGE/ALL параллельно запускает ограниченное число workers с одним стабильным
  UUID
  на процесс и round-robin планом по фактическому scene `frame_step`; SINGLE
  использует только первую GPU. Второй режим сохраняет один Blender-процесс со
  всеми UUID.
- Группа workers имеет один job cgroup, deadline, output limit и общий log
  budget. Failure/startup error/cancel/timeout останавливает все process groups,
  после чего scheduler освобождает всю резервацию. Сырые логи разделены по
  `blender-gpu-{index}.log`, совместимый `blender.log` и live events помечены
  GPU UUID.
- Concurrent progress сериализуется и остаётся монотонным; output paths и
  artifacts дедуплицируются до preview/frame-ready событий. Frontend selector
  показывается только для CUDA/OptiX с двумя GPU, нормализует несовместимые
  конфигурации и сохраняет режим через save/retry/rerender/reload.

## Контракты и миграции

- Alembic head `20260818_0005` добавляет обязательный `jobs.gpu_render_mode` с
  server default `ALL_GPUS_PER_FRAME` для существующих строк; downgrade удаляет
  колонку, `alembic check` не обнаруживает новых операций.
- Blender API schema не изменилась: frontend теперь использует существующий
  `DELETE /api/v1/blender/versions/{version}`.
- Jobs API дополнен полным `PUT /api/v1/jobs/{job_id}` для editable-конфигурации
  и `POST /api/v1/jobs/{job_id}/rerender` (`201 Created`) для terminal jobs.
  Upload принимает `CREATED` и `READY`; для остальных статусов возвращает
  `job_upload_locked`. Create/update/response содержат `gpu_render_mode` со
  значениями `FRAME_PER_GPU` и `ALL_GPUS_PER_FRAME`; create default зависит от
  device/числа GPU, update сохраняет совместимое значение или нормализует его.
- `GET /api/v1/jobs/page?page=&page_size=` возвращает `items`, `page`,
  `page_size`, `total`, `pages`; сервер ограничивает страницу 10 записями.
  Старый `GET /api/v1/jobs` сохранён для совместимости. Миграция БД не требуется.
- Существующий `DELETE /api/v1/jobs/{job_id}` используется frontend: он удаляет
  job directory и artifact metadata, но возвращает
  `409 active_job_cannot_be_deleted` для `QUEUED`/`RENDERING`.
- `POST /api/v1/access/claim` атомарно выдаёт или восстанавливает browser session;
  `POST /api/v1/access/invitations` создаёт одноразовую pairing-ссылку,
  `POST /api/v1/access/pair` обменивает её на session, а origin-less
  `POST /api/v1/access/reset` требует internal Bearer и сбрасывает access state.
  Лимиты: session TTL 7 дней, invitation TTL 5 минут, максимум 5 sessions.
- Browser `/api/v1/**` REST/WS routes требуют paired cookie и проверенный browser
  context; cookie не заменяется Bearer. Origin-less service clients используют
  `RENDER_NODE_AUTH_TOKEN`, без которого production не стартует. WS browser
  handshake без сессии закрывается 4401; service WS без Origin требует Bearer.
- `WS /api/v1/events` закрывает unauthorized/forbidden handshake кодами 4401/4403
  и oversized client message кодом 1009.
- `GET /api/v1/system/capabilities` возвращает `runner.available`, `mode` и
  пользовательское `message`, а также additive `runner.isolation` с mode,
  backend/cgroup version, лимитами, feature support и диагностикой; изменения
  схемы БД не потребовалось.
- Схема `render.progress` не менялась: frontend начал использовать уже
  существующие optional поля `frame`, `sample`, `total_samples` и `progress`.
- Конфигурационного scene-trust gate больше нет; capability mode всегда `local`,
  а `available=false` означает реальную недоступность scheduler/resource
  isolation, не необходимость ручного включения. Миграция БД не требуется.
- Остальные security-настройки: `RENDER_NODE_AUTH_TOKEN`,
  `RENDER_NODE_MAX_API_REQUEST_MB`, `RENDER_NODE_WEBSOCKET_MESSAGE_MAX_KB`.
- Resource settings: `RENDER_NODE_RESOURCE_ISOLATION_MODE=strict|trusted`,
  optional `RENDER_NODE_CGROUP_ROOT`, `RENDER_NODE_WORKER_MEMORY_GB` как
  `memory.max`, `RENDER_NODE_WORKER_SWAP_GB` как optional `memory.swap.max` и
  `RENDER_NODE_WORKER_PIDS_LIMIT` как `pids.max`. Production требует `strict`.
- Deployment contract не меняет БД и не требует username/password. Image и
  native install сохраняют только внутренний `RENDER_NODE_AUTH_TOKEN`; публичный
  Nginx не знает этот секрет. `render-node reset-access` отправляет токен backend
  через stdin curl config, не показывает его в argv/output и не перезапускает
  сервис.

## Последняя проверка

- Multi-GPU: backend Ruff format/check, strict mypy `app tests`, полный pytest
  (`244 passed, 1 skipped`) и полный Alembic cycle
  `upgrade/current/check/downgrade/upgrade` успешны. Integration-тесты на fake
  Blender покрывают одновременный старт, UUID isolation, RANGE/ALL/SINGLE,
  `frame_step`, logs, progress/artifact dedup, peer failure/startup error,
  cancel/timeout и освобождение GPU.
- Frontend ESLint, production/mock builds, все Node contract tests и полный
  набор Playwright QA успешны: mock smoke/jobs/versions/sample/multi-GPU,
  route-fixtures delete/pagination/capabilities/empty registry, real API,
  fake-runner, pairing и same-origin. Desktop/mobile multi-GPU и proxy
  screenshots просмотрены; selector расположен перед GPU allocation, primary
  action доступен, clipping/overlap/overflow нет. Настоящий Blender и
  CUDA/OptiX render в этой работе не запускались.
- Always-local runner follow-up и first-browser pairing: backend Ruff
  format/check, strict mypy `app tests`, Alembic check и полный pytest успешны —
  223 passed, 1 skipped из-за
  отсутствующих system `nginx/envsubst`, одно upstream Starlette warning.
  47 security tests покрывают atomic claim, sessions/cookies, Fetch Metadata,
  marker, произвольный Origin при переписанном Host, Bearer service clients,
  invitation expiry/single-use/concurrency, пять sessions, reset и HTTP/WS
  limits. Legacy runner trust variables игнорируются; реальный development
  capability без `.env` вернул `local/available=true` и `Ready to render`.
  Миграция БД не потребовалась.
- Frontend: ESLint, production build, 6 access, 3 Vite config и 4 realtime
  progress tests успешны. Production bundle содержит только основной JS/CSS и
  не содержит internal Bearer, mock fixture/test bypass markers. Access Gate,
  same-document fragment redeem, REST recovery и browser WebSocket compatibility
  subprotocol проверены реальным Chromium. Runner-ready viewport screenshot
  просмотрен; status зелёный, Start доступен, horizontal overflow отсутствует.
- Целевой Playwright через произвольный внешний HTTPS hostname и намеренно
  переписанный upstream Host проверил claim до queries/WS, locked+Retry без
  фонового loop, REST mutation, Blender Download action, direct artifact route,
  application WebSocket, одноразовый fragment redeem/replay, пять sessions,
  device limit, Copy link, tooltip, focus trap/Escape и очистку modal state.
  Desktop/mobile dashboard, tooltip и modal screenshots просмотрены; найденный
  mobile header overflow исправлен, clipping/overflow/console errors нет.
- Deployment: 18 behavioral tests прошли, один system Nginx/envsubst test
  skipped. Fresh/no-op/update/repair сохраняют jobs/database/runtimes/internal
  token, удаляют только точные legacy credential files и не требуют public URL,
  username/password/origin. Reset не рестартует сервис и не раскрывает token.
  Runtime-аудит не нашёл Basic Auth, admin env, static origin allowlist или
  public Bearer injection; backend остаётся loopback-only. `bash -n`,
  `git diff --check` и проверка свободных портов успешны. Реальный Blender/GPU
  render не запускался.
- Sample progress: 16 целевых backend-тестов parser/Job Manager и 4 frontend
  realtime contract-теста успешны. Сохранённый реальный Blender 4.1.1 log
  разобран без запуска Blender: 64 sample updates, для кадров 1 и 2 от `0/512`
  до `512/512`. Отдельный desktop/mobile mock sample smoke успешен; screenshots
  просмотрены, clipping и horizontal overflow не
  обнаружены. В ходе этого follow-up реальный render не запускался.
- Фактическая системная диагностика: devcontainer использует cgroup v1 без
  writable v2 delegation. Strict mode вернул `available=false` с явным
  требованием delegated cgroup v2; trusted mode вернул external boundary и не
  обращался к cgroupfs. Kernel cgroup v2 setup/attach/OOM/cleanup и его
  совместная работа с GPU-процессом не заявляются проверенными.
- Реальный development GPU smoke после перезапуска backend с новым runner:
  официальный Blender 4.1.1 отрендерил кадры 1–2 пользовательской сцены через
  Cycles/OptiX на NVIDIA RTX A5000. Job
  `54c6f8ca-bd41-4218-80a2-1bc02169a8f1` завершён как `COMPLETED`, progress
  `1.0`, exit code `0`; сохранены `frame_0001.png` (5 239 667 bytes) и
  `frame_0002.png` (5 237 288 bytes). Во время трассировки наблюдались 100% GPU,
  около 3.36 GiB VRAM и более 35 GiB virtual address space, что подтверждает
  устранение ложного OOM от прежнего `RLIMIT_AS`. Это проверка текущего
  trusted/devcontainer runtime, а не released production image или strict
  delegated-cgroup deployment.
- Реальный development CPU smoke: официальный Blender 4.5.11 LTS с проверенным
  SHA-256 успешно отрендерил кадр 1 `Untitled.blend` в Eevee через публичный Job
  API; job `COMPLETED`, progress `1.0`, exit code `0`. Original 600×900 PNG,
  preview, однокадровый ZIP и raw log выданы API. SHA-256 сохранённой сцены
  совпал с пользовательским файлом, исходный файл не изменялся.
- Alembic: upgrade нового вложенного SQLite path, current/check, downgrade base и
  повторный upgrade — успешно; текущий head `20260818_0005`, schema diff
  отсутствует.
- Frontend: ESLint, production Vite build и explicit mock build — успешно.
  Production bundle проверен на отсутствие mock chunk и fixture markers.
- Целевой Playwright `qa:versions-empty`: пустой real-mode registry, сохранение
  просмотра исторических jobs/artifacts, блокировка upload/start/rerender,
  `Download → Install → Activate` без reload и последующая разблокировка —
  успешно. Desktop/mobile screenshots просмотрены; найденный тестом mobile
  header overflow исправлен, повторная проверка границ прошла. Регрессионный
  `qa:versions` для bundled/mock manager также успешно пройден.
- Development same-origin не требует списка hostname: локальный и внешний
  proxy передают browser `Origin` и исходный `Host`, а spoofed
  `X-Forwarded-Host`/`X-Forwarded-Proto` не влияют на решение backend.
- Целевой Playwright `qa:capabilities`: available/unavailable/API-error состояния,
  блокировка Start и desktop/mobile viewport fit — успешно; screenshots
  просмотрены. Появление job error отдельно проверено обычным кликом: высота
  desktop-панели и положение Runtime/actions не меняются; clipping, overlap и
  horizontal overflow не обнаружены.
- RunPod proxy HMR smoke через реальный Chromium: страница и same-origin API
  вернули `200`, HMR и application WebSocket использовали только
  `wss://<pod>-5173.proxy.runpod.net`, localhost/127.0.0.1 sockets отсутствуют.
  Новый Node config-test (3 tests), frontend ESLint и production build успешны;
  desktop screenshot просмотрен, prompt, clipping и overflow не обнаружены.
- После обычного перезапуска `make dev` реальные `/ready` и
  `/api/v1/system/capabilities` вернули ready и `local/available=true` без `.env`;
  настройки процесса вручную через shell env не требуются. Порядок
  запуска проверен по реальным логам: frontend стартовал только после успешного
  backend startup и ответа `200` от `/ready`; добавлены unit-тесты readiness-gate.
- Целевой Playwright `qa:versions`: две bundled-версии, install, отмена и
  подтверждение delete, возврат версии в каталог, desktop/mobile viewport fit —
  успешно; итоговые screenshots просмотрены, clipping и overflow не обнаружены.
- Реальный recovery 4.5.11: существующий валидный runtime принят после проверки,
  install operation завершена, версия показана в installed list без дубликата и
  старой ошибки; 4.1.1 осталась активной. Screenshot и modal fit проверены.
- Целевой Playwright `qa:jobs`: selected job settings, read-only после start,
  editable rerender-копия, сохранение настроек, New job/upload и desktop/mobile
  fit — успешно; screenshots просмотрены, overlap и horizontal overflow не
  обнаружены.
- Целевой Playwright `qa:jobs-pagination`: по 10/10/3 jobs на трёх страницах,
  отсутствие предварительной загрузки страниц 2–3, стабильная высота панели,
  внутренняя прокрутка и desktop/mobile fit — успешно; screenshots просмотрены,
  overlap и horizontal overflow не обнаружены.
- Целевой Playwright `qa:job-delete`: desktop reveal/close, touch swipe в обе
  стороны, блокировка active job, удаление без диалога, подтверждение и отмена
  при артефактах, выбор следующей записи и desktop/mobile fit — успешно;
  закрытый hover дополнительно проверен на непрозрачность. Hover, transition,
  revealed и confirmation screenshots просмотрены, clipping, overlap и
  horizontal overflow не обнаружены.
- Playwright mock smoke: desktop/compact/ultrawide/mobile fit, versions,
  render/cancel, live log, dialogs и frame pagination — успешно; итоговые
  скриншоты просмотрены, clipping/overlap/horizontal overflow не обнаружены.
- Playwright real API smoke: versions, upload -> `READY` и persistence после
  reload — успешно. Fake Blender runner smoke: WebSocket log/progress, HTTP
  preview/original/ZIP, reload recovery, metrics и process-group cancellation —
  успешно.
- Deployment harness использует только временные install/state/run roots и fake
  external commands. Дополнительно покрыты bundle manifest, latest/explicit
  release URL, checksum/tar safety, rollback, partial cleanup, PID identity и
  process group, stdin password, clean child env и workflow checks gate.
  ShellCheck, system Nginx и Docker engine недоступны; реальные Pod/image/GPU
  smoke ниже остаются внешними проверками.

## Известные ограничения и дальнейшая работа

- Нативный installer ещё не прогонялся целиком на реальном Ubuntu GPU Pod;
  необходимо проверить apt/runtime libraries, обе bundled Blender версии,
  RunPod HTTPS/WebSocket proxy и CUDA/OptiX до публикации команды пользователям.
- GHCR workflow ещё не выполнялся для альтернативного image deployment; готовый
  RunPod template ещё не создан.
- Отдельная multi-tenant worker architecture с network/filesystem/device
  isolation не реализована и не является режимом текущего одноузлового продукта.
- Текущее devcontainer-окружение использует cgroup v1 и не делегирует writable
  cgroup v2. Здесь проверены v1 detection/strict fail-closed и trusted fallback;
  реальный kernel cgroup v2 setup/attach/OOM/cleanup требует Ubuntu Pod с
  delegated `memory`/`pids`. Fake cgroup v2 покрывает эти lifecycle-контракты.
- Blender subprocess запускается внутри VM/контейнера/Pod установленного узла;
  pairing не изолирует его от backend. `--disable-autoexec`, allowlisted env,
  path/resource/time/output limits остаются обязательными.
- Production image может содержать bundled Blender binaries, но локально не
  собирался. Development CPU/Eevee для Blender 4.5.11 проверялся ранее; глобальный
  executable override удалён, поэтому повторная проверка должна использовать
  обычный UI workflow и реальный version-specific runtime. Blender 4.1.1 с
  Cycles/OptiX проверен на RTX A5000 в текущем devcontainer; bundled 5.2.0,
  released production image и совместный strict cgroup v2 + GPU lifecycle ещё
  должны быть проверены на целевом облачном Pod.
- Реального совместного smoke `strict cgroup v2 + Blender/CUDA/OptiX` нет:
  текущий GPU smoke относится только к trusted devcontainer без per-job cgroup.
  После просьбы пользователя дальнейшие render smoke не запускаются; целевой
  strict/GPU сценарий остаётся ручной внешней проверкой.
- Реальный multi-GPU `FRAME_PER_GPU` smoke с Blender/CUDA/OptiX намеренно не
  выполнялся; concurrency и lifecycle проверены fake Blender без использования
  GPU. Его следует отдельно подтвердить в целевом cloud Pod после делегации
  strict cgroup v2.
- Для custom browser deployment backend должен оставаться приватным за HTTPS
  proxy на том же public origin, что и frontend. Proxy не должен подставлять
  internal Bearer в browser HTTP/WebSocket; переписывание `Host` допустимо.
  In-memory access state требует одного backend worker и повторного claim после
  рестарта. Internal token нельзя помещать в `VITE_*` или JavaScript.
- Resumable upload и автоматическая retention policy не реализованы. Cleanup
  explicit; оператор удаляет jobs и выполняет согласованный backup SQLite/jobs.
- TestClient выдаёт upstream Starlette deprecation warning о будущем `httpx2`;
  на результат тестов это не влияет.
