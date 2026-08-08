# Implementation state

Краткий handoff backend-реализации. Код и миграции имеют приоритет, если этот
файл с ними расходится.

## Текущее состояние

- Обновлено: 2026-08-08 после перехода native deployment на проверяемый GitHub
  Release bundle с repair/update/rollback.
- Фазы 1–6 из `BACKEND_IMPLEMENTATION_MASTER_PROMPT.md` завершены.
- Следующей фазы в master prompt нет; дальнейшая работа — отдельные deployment
  задачи, перечисленные ниже.
- Пользовательский `Untitled.blend` не изменялся.

## Реализовано

- Фазы 1–5: FastAPI/SQLite/Alembic, безопасные jobs/uploads, Blender registry,
  `SandboxRunner`, FIFO Job Manager, realtime, artifacts/previews и metrics.
- Bundled manifest сокращён до Blender 5.2.0 и 4.1.1; 5.2.0 активен по
  умолчанию. При обновлении registry удаляет устаревшие bundled-записи, которых
  больше нет в образе. Адаптеры 4.5.11, 4.2.22 и 3.6.23 сохранены для явной
  пользовательской установки этих версий.
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
- Единая security boundary для REST и WebSocket: production требует Bearer
  token длиной 32+ символа и точные HTTPS origins; `/health` и `/ready` публичны.
  Origin проверяется до API handler, Swagger/OpenAPI в production отключены.
- CORS ограничен настроенными origins, методами и заголовками; добавлены HSTS в
  production и общие browser security headers. JSON mutation body по умолчанию
  ограничен 1 MiB, клиентское WS-сообщение — 64 KiB; upload/archive limits
  остаются отдельными.
- Добавлен явный `deployment_profile`: безопасный default `isolated_worker`
  сохраняет production fail-closed без OS sandbox, а `single_tenant` разрешает
  прямой `local_trusted` runner в production на целиком арендованном одним
  оператором VM/Pod с собственными доверенными сценами.
- Для прямого запуска используется `runner_mode=local_trusted`. Корневой `.env`
  загружается автоматически, старый boolean-флаг мигрирует в новый режим.
  Production принимает его только вместе с `single_tenant`; локальный `.env`
  остаётся вне git.
- Добавлен production `linux/amd64` image: он потребляет тот же собранный Release
  bundle, скачивает Blender 5.2.0/4.1.1 только из официального архива и проверяет
  закреплённые SHA-256. Короткий entrypoint готовит volume и credentials, затем
  единый supervisor запускает долгоживущие процессы как UID 10001.
- Внутренний Nginx обслуживает SPA на 8080, требует Basic Auth и заменяет
  browser credential на закрытый Bearer для REST/WebSocket/download. Backend
  слушает только loopback; RunPod platform proxy завершает HTTPS.
- Основной `install.sh` поддерживает только Ubuntu 22.04/24.04 x86_64 GPU Pod.
  Он всегда скачивает latest stable Release bundle и `.sha256` либо explicit
  `vX.Y.Z`, безопасно проверяет tar paths/links, создаёт frozen per-release
  Python 3.13.7 `.venv` и не устанавливает Node.js/Docker/systemd. RunPod origin
  определяется по `RUNPOD_POD_ID`.
- `/opt/render-node/current` переключается атомарно только после полной подготовки
  release; failed readiness восстанавливает предыдущий release. No-op, repair
  после потери `/opt`, credentials/jobs persistence и смена Pod origin покрыты
  fake-command harness.
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
- Start/retry выполняют runner preflight до перехода в `QUEUED`; недоступный
  scheduler/runner возвращает `runner_unavailable`, сохраняя исходный статус.
  Frontend показывает capability runner и блокирует Start при недоступности.
- Restart очищает per-job `temp` и input незавершённого `CREATED` upload.
  Start/retry проверяет наличие contained regular scene. Retry очищает старые
  output/preview/log/temp и artifact metadata, сохраняя input; delete освобождает
  runtime locks.
- Frontend mock API загружается только по `VITE_RENDER_NODE_MOCK=true`; mock
  fixtures/chunk отсутствуют в production bundle. Vite proxy умеет server-side
  Bearer injection для HTTP и WebSocket без передачи секрета в browser bundle.
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

## Контракты и миграции

- Alembic head остаётся `20260730_0004`; schema change в фазе 6 не требовался,
  `alembic check` не обнаруживает новых операций.
- Blender API schema не изменилась: frontend теперь использует существующий
  `DELETE /api/v1/blender/versions/{version}`.
- Jobs API дополнен полным `PUT /api/v1/jobs/{job_id}` для editable-конфигурации
  и `POST /api/v1/jobs/{job_id}/rerender` (`201 Created`) для terminal jobs.
  Upload принимает `CREATED` и `READY`; для остальных статусов возвращает
  `job_upload_locked`. Миграция БД не требуется.
- `GET /api/v1/jobs/page?page=&page_size=` возвращает `items`, `page`,
  `page_size`, `total`, `pages`; сервер ограничивает страницу 10 записями.
  Старый `GET /api/v1/jobs` сохранён для совместимости. Миграция БД не требуется.
- Существующий `DELETE /api/v1/jobs/{job_id}` используется frontend: он удаляет
  job directory и artifact metadata, но возвращает
  `409 active_job_cannot_be_deleted` для `QUEUED`/`RENDERING`.
- Все `/api/v1/**` REST/WS routes используют один Bearer contract, если настроен
  `RENDER_NODE_AUTH_TOKEN`; production без token или HTTPS allowlist не стартует.
- `WS /api/v1/events` закрывает unauthorized/forbidden handshake кодами 4401/4403
  и oversized client message кодом 1009.
- `GET /api/v1/system/capabilities` возвращает `runner.available`, `mode` и
  пользовательское `message`; изменения схемы БД не потребовалось.
- Настройки deployment boundary: `RENDER_NODE_DEPLOYMENT_PROFILE` принимает
  `isolated_worker` или `single_tenant`; `RENDER_NODE_RUNNER_MODE=local_trusted`
  в production валиден только со вторым профилем. Миграция БД не требуется.
- Остальные security-настройки: `RENDER_NODE_AUTH_TOKEN`,
  `RENDER_NODE_MAX_API_REQUEST_MB`, `RENDER_NODE_WEBSOCKET_MESSAGE_MAX_KB`.
- Deployment contract не меняет REST API или БД. Для прямого image остаются
  container-only границы: `RENDER_NODE_ADMIN_HTPASSWD_B64` либо
  `RENDER_NODE_ADMIN_USERNAME` + `RENDER_NODE_ADMIN_PASSWORD` для прямого image;
  plaintext password удаляется из environment до запуска backend.

## Последняя проверка

- Backend: Ruff format/lint — успешно; strict mypy `app` — успешно. Целевые 23
  pytest tests профиля/config/security пройдены. WebSocket limit-тест теперь
  корректно пропускает уже поставленные в очередь metrics-события до close 1009.
  Финальный полный набор: 147 passed, 1 skipped (system Nginx отсутствует), одно
  upstream Starlette warning. Ruff format/check и strict mypy `app` пройдены.
  Команда `mypy app tests` дополнительно находит три ранее существовавшие ошибки
  типизации в `tests/test_dev_script.py`; production-код `app` чистый.
  Покрыты auth/CORS/headers, REST+WS boundary, body/WS limits, production sandbox
  fail-closed, editable/locked job settings, безопасная замена upload, rerender с
  копией вложенного input, retry cleanup, missing scene, restart recovery,
  адаптеры Blender, bounded streaming official catalog, clean
  install-validation env, CPU-time limit и migration bootstrap нового workspace.
- Реальный development CPU smoke: официальный Blender 4.5.11 LTS с проверенным
  SHA-256 успешно отрендерил кадр 1 `Untitled.blend` в Eevee через публичный Job
  API; job `COMPLETED`, progress `1.0`, exit code `0`. Original 600×900 PNG,
  preview, однокадровый ZIP и raw log выданы API. SHA-256 сохранённой сцены
  совпал с пользовательским файлом, исходный файл не изменялся.
- Alembic: upgrade нового вложенного SQLite path, current/check, downgrade base и
  повторный upgrade — успешно; head `20260730_0004`, schema diff отсутствует.
- Frontend: ESLint, production Vite build и explicit mock build — успешно.
  Production bundle проверен на отсутствие mock chunk и fixture markers.
- Целевой Playwright `qa:capabilities`: available/unavailable/API-error состояния,
  блокировка Start и desktop/mobile viewport fit — успешно; screenshots
  просмотрены. Появление job error отдельно проверено обычным кликом: высота
  desktop-панели и положение Runtime/actions не меняются; clipping, overlap и
  horizontal overflow не обнаружены.
- После обычного перезапуска `make dev` реальные `/ready` и
  `/api/v1/system/capabilities` вернули ready и `local_trusted/available=true`;
  настройки процесса вручную через shell env больше не требуются. Порядок
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
- Deployment: 16 пройденных behavioral tests используют только временные install/state/run
  roots и fake external commands. Покрыты bundle manifest, latest/explicit URL,
  checksum/tar safety, fresh/no-op/repair, persistence/origin, update/rollback,
  partial cleanup, PID identity/process group, stdin password, clean child env,
  envsubst и workflow checks gate. `bash -n` пройден; workflow проверен
  `@action-validator/cli`. ShellCheck, system Nginx и Docker engine недоступны.
  Frontend lint и production build пройдены. Реальные Pod/image/GPU smoke ниже
  остаются внешними проверками.

## Известные ограничения и дальнейшая работа

- Нативный installer ещё не прогонялся целиком на реальном Ubuntu GPU Pod;
  необходимо проверить apt/runtime libraries, обе bundled Blender версии,
  RunPod HTTPS/WebSocket proxy и CUDA/OptiX до публикации команды пользователям.
- GHCR workflow ещё не выполнялся для альтернативного image deployment; готовый
  RunPod template ещё не создан.
- Worker image/namespace с network/filesystem/device isolation и non-root
  runtime ещё не реализован; поэтому shared production в профиле
  `isolated_worker` не достигает readiness при включённом scheduler.
- `local_trusted` запускает Blender subprocess напрямую и предназначен только для
  доверенных сцен; он не является sandbox. В production он разрешён только для
  явного `single_tenant`, где весь временный VM/Pod принадлежит одному оператору.
- Production image объявляет bundled Blender binaries, но локально не собирался.
  Development CPU/Eevee для Blender 4.5.11 проверен ранее через executable
  override; CUDA/OptiX и bundled 5.2.0/4.1.1 runtime smoke должны быть
  проверены на реальном облачном GPU до заявления их работоспособности.
- Для custom browser deployment вне поставляемого gateway backend также должен
  оставаться приватным за HTTPS proxy, который подставляет Bearer в HTTP и
  WebSocket upgrades. Токен нельзя помещать в `VITE_*` или JavaScript.
- Resumable upload и автоматическая retention policy не реализованы. Cleanup
  explicit; оператор удаляет jobs и выполняет согласованный backup SQLite/jobs.
- TestClient выдаёт upstream Starlette deprecation warning о будущем `httpx2`;
  на результат тестов это не влияет.
