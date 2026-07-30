# Implementation state

Краткий handoff backend-реализации. Код и миграции имеют приоритет, если этот
файл с ними расходится.

## Текущее состояние

- Обновлено: 2026-07-30.
- Фазы 1–6 из `BACKEND_IMPLEMENTATION_MASTER_PROMPT.md` завершены.
- Следующей фазы в master prompt нет; дальнейшая работа — отдельные deployment
  задачи, перечисленные ниже.
- Пользовательский `Untitled.blend` не изменялся.

## Реализовано

- Фазы 1–5: FastAPI/SQLite/Alembic, безопасные jobs/uploads, Blender registry,
  `SandboxRunner`, FIFO Job Manager, realtime, artifacts/previews и metrics.
- Единая security boundary для REST и WebSocket: production требует Bearer
  token длиной 32+ символа и точные HTTPS origins; `/health` и `/ready` публичны.
  Origin проверяется до API handler, Swagger/OpenAPI в production отключены.
- CORS ограничен настроенными origins, методами и заголовками; добавлены HSTS в
  production и общие browser security headers. JSON mutation body по умолчанию
  ограничен 1 MiB, клиентское WS-сообщение — 64 KiB; upload/archive limits
  остаются отдельными.
- Production scheduler fail-closed без OS sandbox. Development override не
  разрешается в production и не считается production-проверкой.
- Restart очищает per-job `temp` и input незавершённого `CREATED` upload.
  Start/retry проверяет наличие contained regular scene. Retry очищает старые
  output/preview/log/temp и artifact metadata, сохраняя input; delete освобождает
  runtime locks.
- Frontend mock API загружается только по `VITE_RENDER_NODE_MOCK=true`; mock
  fixtures/chunk отсутствуют в production bundle. Vite proxy умеет server-side
  Bearer injection для HTTP и WebSocket без передачи секрета в browser bundle.
- README и env examples описывают auth/reverse proxy, limits, cleanup, backup и
  остаточные production-риски.

## Контракты и миграции

- Alembic head остаётся `20260730_0004`; schema change в фазе 6 не требовался,
  `alembic check` не обнаруживает новых операций.
- Все `/api/v1/**` REST/WS routes используют один Bearer contract, если настроен
  `RENDER_NODE_AUTH_TOKEN`; production без token или HTTPS allowlist не стартует.
- `WS /api/v1/events` закрывает unauthorized/forbidden handshake кодами 4401/4403
  и oversized client message кодом 1009.
- Новые настройки: `RENDER_NODE_AUTH_TOKEN`, `RENDER_NODE_MAX_API_REQUEST_MB`,
  `RENDER_NODE_WEBSOCKET_MESSAGE_MAX_KB`.

## Последняя проверка

- Backend: Ruff format/lint — успешно; strict mypy `app tests` — успешно;
  все 113 pytest tests пройдены двумя исчерпывающими группами (39 + 74).
  Покрыты auth/CORS/headers, REST+WS boundary, body/WS limits, production sandbox
  fail-closed, retry cleanup, missing scene и restart cleanup/recovery.
- Alembic: upgrade пустой БД, `alembic check` и `current` — успешно, head
  `20260730_0004`.
- Frontend: ESLint, production Vite build и explicit mock build — успешно.
  Production bundle проверен на отсутствие mock chunk и fixture markers.
- Playwright mock smoke: desktop/mobile fit, versions, render/cancel, live log и
  dialogs — успешно. Playwright real API smoke: upload -> `READY` и reload —
  успешно как без auth, так и через server-side Bearer proxy для REST+WebSocket.

## Известные ограничения и дальнейшая работа

- Production worker image/namespace с network/filesystem/device isolation и
  non-root runtime ещё не реализован; поэтому production rendering намеренно не
  достигает readiness при включённом scheduler.
- В контейнере нет production Blender image и GPU. Реальный Blender CPU,
  CUDA/OptiX, GPU isolation и bundled binaries не проверялись; их работа не
  заявляется.
- Browser deployment с Bearer требует приватного backend за HTTPS reverse proxy,
  который добавляет credential в HTTP и WebSocket upgrades. Токен нельзя
  помещать в `VITE_*` или JavaScript.
- Resumable upload и автоматическая retention policy не реализованы. Cleanup
  explicit; оператор удаляет jobs и выполняет согласованный backup SQLite/jobs.
- TestClient выдаёт upstream Starlette deprecation warning о будущем `httpx2`;
  на результат тестов это не влияет.
