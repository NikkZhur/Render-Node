# Implementation state

Краткий handoff между фазами backend-реализации. Код и миграции имеют приоритет,
если этот файл с ними расходится.

## Текущее состояние

- Обновлено: 2026-07-30.
- Завершена фаза 5 — Realtime и artifacts.
- Следующая фаза: 6 — Hardening и финал. Она не начата.
- Пользовательский `Untitled.blend` не изменялся.

## Реализовано

- Фазы 1–4: FastAPI/SQLite/Alembic foundation, safe Job/upload lifecycle,
  Blender registry, backend-owned command, `SandboxRunner`, FIFO Job Manager,
  progress, timeout и process-group cancel/recovery.
- In-process `EventHub` и `WS /api/v1/events`: bounded subscriber queues,
  job subscriptions, sequence/timestamp, overflow-событие `resync.required`.
  Публикуются job status, render log/progress/preview/frame, Blender operation и
  system metrics; изображения передаются только как HTTP URL.
- Artifact metadata сохраняется в SQLite. Output watcher регистрирует закрытые
  кадры; original остаётся неизменным, browser-safe PNG preview создаётся Pillow
  с pixel/dimension limits. Пути проверяются внутри job, symlink запрещён.
- Реализованы list/download/delete artifacts, frame pagination максимум 50,
  preview/original, disk-built `frames.zip`, `blender.log` и bounded log tail.
- CPU/RAM, best-effort temperature, NVML GPU/VRAM/power и storage free/throughput
  доступны через REST и WebSocket. `low_space` означает <10% или <50 GiB
  (пороги настраиваются); одинаковые filesystem mounts не дублируются.
- Frontend использует TanStack Query для REST snapshot и WebSocket invalidation,
  reconnect с exponential backoff и REST resync. Реальными стали Jobs,
  log overlay, preview/full frame, frame/ZIP/log downloads, artifacts и metrics;
  переключение job и reload восстанавливают данные с backend.

## Контракты и миграции

- Новый Alembic head: `20260730_0004`; таблица `artifacts` с UUID, job FK,
  kind, contained relative path, content type/size, frame и UTC created time.
- `WS /api/v1/events`; client action `{"action":"subscribe","job_ids":[...]}`
  либо `null` для всех jobs. При потере backpressure клиент получает
  `resync.required` и обновляет REST queries.
- `GET /api/v1/jobs/{id}/artifacts`, `GET/DELETE .../artifacts/{artifact_id}`.
- `GET /api/v1/jobs/{id}/frames?page=1&page_size=50`,
  `.../frames/{frame}/preview`, `.../original`, `.../frames.zip`.
- `GET /api/v1/jobs/{id}/logs/blender` и `.../logs/blender/tail?lines=100`.
- `GET /api/v1/system/metrics` возвращает CPU, GPU, storage и число WS clients.

## Последняя проверка

- Backend: Ruff format/lint и strict mypy — успешно; `pytest` — 100 passed.
  Покрыты EventHub filter/backpressure, WS contract, metrics/low-space,
  persisted preview/original/log/ZIP, pagination 51 -> 50+1, URL-only frame
  events, fake render/cancel и тесты предыдущих фаз.
- Frontend: ESLint и production Vite build — успешно.
- Playwright real backend + fake Blender: WebSocket log/progress, HTTP preview,
  full-size frame, frame и ZIP downloads, artifacts, CPU/storage metrics, job
  switch, reload recovery, empty artifact state и cancel — успешно.
- Playwright mock desktop/mobile smoke и real API upload/reload smoke — успешно.
  Dashboard 1440x900 проверен визуально и на horizontal overflow/viewport fit.

## Известные ограничения

- В dev-контейнере нет production Blender image и GPU. Реальный CPU Blender,
  CUDA/OptiX и bundled binaries не проверялись; GPU/OptiX работа не заявляется.
- Локальный test/development runner не является OS sandbox. Production
  fail-closed до появления отдельного namespace/container worker.
- EventHub однопроцессный и неперсистентный; REST является источником resync.
  Это соответствует одноузловому MVP, но не горизонтальному deployment.
- EXR/TIFF original скачивается без изменений; preview создаётся только для
  форматов, которые Pillow может безопасно декодировать. Если decode невозможен,
  original остаётся доступным без preview.

## Handoff следующей фазе

После следующего сообщения `продолжай` выполнить только фазу 6: auth/CORS/limits,
cleanup/restart audit, production sandbox fail-closed, удаление mock-зависимостей
из production path, финальная документация и полный verification matrix.
