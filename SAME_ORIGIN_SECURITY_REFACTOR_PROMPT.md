# Render Node: автоматическая same-origin защита без статической allowlist

Реализуй провайдер-независимую автоматическую защиту Origin в Render Node.

Перед началом прочитай `AGENTS.md`, `IMPLEMENTATION_STATE.md`, соседний
security/config/deployment-код и существующие тесты. Код и миграции имеют
приоритет над документацией. Выполни только эту задачу, не начинай несвязанный
рефакторинг и не создавай commit.

## Цель

Сейчас backend требует статический `RENDER_NODE_ALLOWED_ORIGINS`. Из-за этого
внешний URL приходится заранее определять и настраивать отдельно для RunPod,
другого облака, VPS или dev-прокси.

Замени статическую allowlist автоматической same-origin-проверкой:

- frontend и API публикуются через один публичный host;
- backend автоматически сравнивает браузерный `Origin` с `Host` текущего
  запроса;
- смена Pod, домена или хостинга не требует переменных окружения и изменения
  конфигурации;
- HTTP API и WebSocket сохраняют защиту от cross-site запросов;
- прямые API-клиенты с Bearer token и без `Origin` продолжают работать;
- backend в production по-прежнему доступен только через внутренний
  Nginx/gateway.

Не добавляй интеграции с конкретными провайдерами, каталоги адаптеров,
wildcard-домены, TOFU или сохранение первого увиденного host.

## Требуемое поведение

Для маршрутов под `/api/v1`:

1. Если `Origin` отсутствует:
   - разрешить дальнейшую обработку;
   - Bearer-аутентификация продолжает применяться как сейчас;
   - это сохраняет поддержку CLI и server-to-server клиентов.

2. Если `Origin` присутствует:
   - безопасно разобрать его стандартной библиотекой;
   - принимать только полноценный `http`/`https` origin без userinfo, path,
     query и fragment;
   - отклонять `null`, malformed значения и неоднозначные значения;
   - сравнивать нормализованный hostname и effective port с заголовком `Host`
     текущего запроса;
   - hostname сравнивать без учёта регистра;
   - корректно обработать стандартные порты 80/443, явные порты и IPv6;
   - в production разрешать браузерный `Origin` только с HTTPS;
   - совпавший same-origin запрос разрешать;
   - несовпадение возвращает HTTP 403 с существующим кодом `origin_denied`.

3. Для WebSocket использовать ту же проверку:
   - совпавший `Origin` разрешается;
   - несовпавший или malformed `Origin` закрывается кодом 4403;
   - отсутствие `Origin` допускается только вместе с действующей
     Bearer-аутентификацией;
   - лимит сообщений и остальные контракты не менять.

4. Не доверять `X-Forwarded-Host`, `X-Forwarded-Proto` или `Forwarded` при
   принятии решения. Они могут быть переданы клиентом. Использовать `Host`,
   который доверенный gateway сохраняет для backend.

5. Проверка Origin не заменяет авторизацию:
   - production по-прежнему требует `RENDER_NODE_AUTH_TOKEN`;
   - Nginx продолжает подставлять закрытый Bearer token;
   - health и readiness остаются публичными;
   - security headers и request limits не менять.

## Упрощение конфигурации

Полностью удали статическую origin-конфигурацию, если она больше нигде не нужна:

- удалить `Settings.allowed_origins`;
- удалить parser/validator и `cors_origins`;
- удалить production-требование HTTPS allowlist;
- удалить `RENDER_NODE_ALLOWED_ORIGINS` из backend, `.env.example`, deployment
  config, Docker entrypoint, installer и документации;
- удалить `CORSMiddleware`, поскольку поддерживаемая browser-схема становится
  строго same-origin;
- не сохранять public origin в backend config;
- не оставлять deprecated alias или второй параллельный security mode.

Cross-origin размещение frontend и API на разных доменах в эту задачу не входит.
Не добавляй настройки «на будущее».

## Installer и deployment

Установка приложения не должна требовать знания публичного URL:

- удалить обязательный `--origin` и связанную проверку из `install.sh`;
- установка вне RunPod должна запускаться без provider-specific переменных;
- удалить зависимость установки и container entrypoint от `RUNPOD_POD_ID` и
  `RENDER_NODE_PUBLIC_ORIGIN`;
- credentials должны хранить только действительно необходимые
  username/password;
- `render-node credentials` не должен требовать сохранённый URL;
- если для RunPod удобно вывести вычисленный URL в финальном сообщении
  installer, это допускается только как необязательная presentation-деталь;
  приложение и security не должны от него зависеть;
- для неизвестного хостинга достаточно сообщить, что панель доступна по
  HTTPS-адресу, который провайдер назначил открытому порту 8080;
- сохранить Basic Auth, закрытый backend port 8000, Bearer injection, UID 10001
  и текущий cgroup lifecycle.

Убедись, что Nginx сохраняет публичный `Host` для HTTP API и WebSocket. Не
создавай отдельную origin-проверку в Nginx, если единая backend-проверка уже
закрывает HTTP и WebSocket.

## Development и Vite

Удали связь Vite HMR с backend allowlist:

- `RENDER_NODE_ALLOWED_ORIGINS` больше не должен управлять HMR;
- не определяй хостинг по `RUNPOD_POD_ID` или спискам доменных суффиксов;
- обычные REST-запросы и application WebSocket должны использовать
  относительный `/api` и текущий `window.location`;
- Vite proxy должен сохранять исходные `Host` и `Origin`;
- backend Bearer token остаётся server-only и не попадает в browser bundle.

Предпочти стандартное автоматическое поведение Vite через текущий browser host
и WebSocket-capable reverse proxy. Используй актуальный `server.ws`, если нужна
настройка WebSocket.

Если `RENDER_NODE_DEV_PUBLIC_ORIGIN` объективно нужен только как необязательный
transport override для нестандартного HMR proxy, его можно оставить, но:

- он не должен быть обязательным;
- он не должен участвовать в backend security;
- обычный локальный и внешний same-host запуск должен работать без него;
- не добавляй provider-specific auto-detection.

## Тесты

Обнови существующие тесты вместо создания параллельного набора.

Backend обязательно покрыть:

- arbitrary HTTPS host + совпадающий `Origin` проходит с Bearer token;
- RunPod-подобный host не требует специальной настройки;
- другой произвольный hosting domain также работает без настройки;
- несовпадающий `Origin` получает 403 `origin_denied`;
- spoofed `X-Forwarded-Host`/`X-Forwarded-Proto` не помогает пройти проверку;
- malformed `Origin`, `null`, userinfo, path/query/fragment отклоняются;
- hostname case normalization;
- default 80/443 и явные порты;
- IPv4, localhost и IPv6;
- Bearer-клиент без `Origin` работает;
- production HTTP `Origin` отклоняется;
- WebSocket: matching, mismatching, malformed и missing `Origin`;
- auth, message-size limit и security headers не регрессируют;
- Settings production больше не требует allowed origins.

Deployment обязательно покрыть:

- fresh install без `RUNPOD_POD_ID` и без `--origin`;
- отсутствие `RENDER_NODE_ALLOWED_ORIGINS` в generated config;
- сохранение credentials, jobs и token при repair/update;
- container entrypoint стартует без origin variable;
- backend остаётся loopback-only;
- Nginx сохраняет `Host` и проксирует WebSocket;
- shell syntax и release bundle contract.

Frontend обязательно проверить:

- local same-origin REST и application WebSocket;
- внешний произвольный HTTPS hostname через proxy;
- POST mutation больше не получает `Request origin is not allowed`;
- application WebSocket подключается;
- HMR не обращается к localhost с внешней страницы;
- production bundle не содержит mock fixtures или backend secrets.

## Проверки

Запусти минимальные целевые тесты во время реализации, затем:

Backend:

- `uv run ruff format --check app tests`
- `uv run ruff check app tests`
- `uv run mypy app tests`
- `uv run pytest`
- `uv run alembic check`

Frontend:

- `npm run lint`
- `npm run build`
- релевантные Node config/realtime tests;
- один целевой Playwright-сценарий для внешнего same-host proxy.

Deployment:

- релевантные behavioral tests;
- `bash -n` для изменённых shell scripts;
- `git diff --check`.

Playwright-проверку не считать завершённой только по DOM: просмотри
desktop/mobile screenshots и проверь отсутствие ошибок, clipping и horizontal
overflow. Не запускай реальный Blender/GPU render smoke — эта задача не меняет
render pipeline.

## Документация и handoff

Обнови:

- `README.md`;
- `backend/README.md`;
- `deploy/README.md`;
- `.env.example`;
- `PYTHON_PROJECT_ARCHITECTURE.md`;
- `IMPLEMENTATION_STATE.md`.

Удаляй устаревшие инструкции про обязательную allowlist, `--origin`, ручное
добавление proxy URL и RunPod-specific security detection.

Документация должна ясно сообщать:

- Render Node автоматически принимает только same-origin browser requests;
- пользователь может открыть панель через любой HTTPS reverse proxy без
  настройки backend origin;
- frontend и API должны находиться на одном публичном host;
- backend port 8000 нельзя публиковать;
- Basic Auth и Bearer остаются обязательными production-границами;
- отдельное cross-origin размещение frontend не поддерживается.

## Ограничения реализации

- Не добавляй новые зависимости.
- Не создавай новую middleware hierarchy или отдельный security package.
- Не дублируй проверку между HTTP и WebSocket.
- Не доверяй первому запросу и не сохраняй динамический host.
- Не разрешай `*`, regex по доменам хостингов или весь
  `*.proxy.runpod.net`.
- Не принимай решение на основании клиентских forwarded-заголовков.
- Не меняй публичные Job/Blender/Artifact API.
- Не меняй БД и не создавай миграцию без фактической необходимости.
- Сохрани пользовательские и несвязанные изменения.

В финальном отчёте укажи изменённый security-контракт, удалённую конфигурацию,
выполненные проверки и всё, что нельзя было проверить локально.
