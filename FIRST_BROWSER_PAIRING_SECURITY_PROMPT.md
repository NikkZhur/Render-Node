# Render Node: first-browser pairing без логина и статического origin

Реализуй для Render Node провайдер-независимую browser-защиту по модели
«первый браузер забирает ноду». Первый браузер, осознанно открывший ещё не
занятую панель, автоматически получает защищённую сессию. После этого новые
браузеры допускаются только по короткоживущей одноразовой ссылке, созданной в
уже авторизованной панели.

Перед началом полностью прочитай `AGENTS.md`, `IMPLEMENTATION_STATE.md`,
`SAME_ORIGIN_SECURITY_REFACTOR_PROMPT.md`, текущие security/config/deployment
файлы, frontend API/realtime/header и соседние тесты. Код и миграции имеют
приоритет над документацией. В рабочем дереве уже могут находиться изменения
предыдущего same-origin refactor: сохрани их и развивай, не откатывай и не
переписывай несвязанный код. Не создавай commit.

## Цель и принятая модель риска

Render Node — одноразовая однопользовательская render-нода. Пользователь явно
принимает небольшой TOFU-риск: если посторонний первым откроет публичный адрес и
выполнит browser claim, он получит доступ к ноде. Не пытайся скрыть или
маскировать этот контракт.

Нужное поведение:

- публичный URL не требуется знать или настраивать заранее;
- внешний reverse proxy может переписать `Host`, не ломая UI, REST и WebSocket;
- первый browser session получает доступ автоматически, без логина, пароля,
  CAPTCHA или кода;
- обычный GET страницы, `/health`, `/ready`, crawler preview и port probe сами
  по себе не должны забирать ноду;
- после первого успешного claim неизвестный браузер не получает API-доступ;
- максимум одновременно существует пять browser sessions;
- первая сессия создаётся только automatic claim, остальные четыре — только по
  одноразовым приглашениям от уже допущенного браузера;
- прямые CLI/server-to-server клиенты без browser-заголовков продолжают
  использовать закрытый Bearer token;
- backend port 8000 остаётся loopback-only и никогда не публикуется;
- frontend и API по-прежнему публикуются через один HTTPS origin.

Это не multi-user authentication. Не добавляй пользователей, роли, OAuth,
почту, пароль, CAPTCHA, fingerprinting браузера или привязку к IP/User-Agent.

## Жизненный цикл доступа

### Automatic claim

Добавь явный browser endpoint для claim. Рекомендуемый контракт (имена можно
слегка согласовать с существующим API style, но семантику не менять):

```text
POST /api/v1/access/claim
```

- endpoint вызывается frontend JavaScript только после загрузки приложения;
- первая конкурентно успешная попытка атомарно переводит access manager из
  `unclaimed` в `claimed` и создаёт первую browser session;
- ответ устанавливает session cookie и возвращает минимальный access status;
- повторный вызов с уже действующей cookie идемпотентен и не создаёт новую
  сессию;
- вызов из другого browser cookie jar после claim получает структурированную
  ошибку `node_already_claimed` (HTTP 409);
- гонку закрыть `asyncio.Lock` или эквивалентной атомарной секцией: два
  одновременных неизвестных браузера не могут оба стать первыми;
- GET страницы, статических файлов, health/readiness и любые OPTIONS/HEAD не
  меняют access state.

Frontend не должен запускать jobs queries или application WebSocket до
успешного claim/redeem. Пока доступ определяется, показывай спокойный экран
`Connecting to Render Node…`. При `node_already_claimed` показывай отдельный
экран:

```text
This Render Node is already paired with another browser.
Ask an authorized user to share a pairing link, or run
`render-node reset-access` on the node.
```

Добавь Retry, но не создавай бесконечный retry loop и не переходи автоматически
в mock mode.

### Browser sessions

- session token генерировать CSPRNG, не менее 256 бит энтропии;
- хранить на сервере только digest token, сравнивать безопасно;
- не помещать token в URL, DOM, JavaScript storage, логи или error payload;
- cookie должна быть opaque, `HttpOnly`, `SameSite=Strict`, без `Domain`, с
  `Path=/`;
- в production cookie обязательна `Secure` и должна использовать `__Host-`
  prefix; для локального HTTP development используй отдельное безопасно
  различимое имя без `Secure`;
- browser sessions живут только в единственном backend-процессе и сбрасываются
  при его рестарте; это допустимый и документированный контракт одноразовой
  ноды;
- browser cookie может иметь разумный absolute lifetime, достаточный для
  длинного рендера (не менее 24 часов); не вводи refresh token;
- просроченные записи удалять лениво и не допускать неограниченного роста;
- после исчезновения всех сессий не открывай claim автоматически в работающем
  процессе: повторное открытие выполняется только через локальный reset.

Не добавляй БД или Alembic migration только ради этих эфемерных сессий. Render
Node запускается одним Uvicorn worker; явно сохрани этот deployment contract.

### Pair another device

Авторизованный browser session может создать приглашение:

```text
POST /api/v1/access/invitations
```

Требования:

- не создавать приглашение, если уже заняты все пять browser slots;
- invitation token — CSPRNG не менее 256 бит;
- хранить только digest;
- срок действия — 5 минут;
- token одноразовый; два конкурентных redeem дают ровно одну новую сессию;
- после успешного redeem token немедленно удалить;
- истёкшие tokens удалять лениво;
- ответ возвращает token и expiration, но не пытается строить public URL на
  backend.

Frontend строит ссылку только из `window.location.origin` и URL fragment:

```text
https://current-public-host/#pair=<opaque-token>
```

Не используй query string: invitation не должен попадать в access logs,
reverse-proxy request target или `Referer`. При загрузке pairing URL frontend
должен:

1. извлечь fragment локально;
2. немедленно убрать его из address bar через `history.replaceState`;
3. отправить token в JSON body на `POST /api/v1/access/pair`;
4. получить session cookie;
5. только затем запускать основной UI, queries и WebSocket.

Pair endpoint принимает только действующее неиспользованное приглашение. Для
invalid/expired/used token возвращай стабильные структурированные ошибки без
утечки внутреннего состояния.

## Кнопка в верхней панели

Добавь `Pair another device` первым интерактивным элементом существующего
`.header-controls`: сразу после brand block и до summary/connection/version
controls. На desktop и mobile кнопка должна оставаться видимой.

Внешний вид:

- только существующий стиль `icon-button`;
- понятная share-иконка в текущем inline SVG `Icon`, без новой icon library;
- `aria-label="Pair another device"`;
- tooltip виден и при hover, и при keyboard focus;
- текст tooltip:
  `Pair another device — create a one-time link that grants access to this Render Node.`

Не ограничивайся одним `title`, если он не даёт доступного keyboard tooltip.

По нажатию открой компактный modal/dialog в стиле существующего интерфейса:

- заголовок `Pair another device`;
- короткое предупреждение, что ссылка выдаёт полный доступ, одноразовая и
  истекает через 5 минут;
- loading/error/success states;
- копируемая ссылка и явная кнопка `Copy link`;
- если доступен `navigator.share`, можно также предложить системный Share, но
  copy flow обязателен и должен работать на desktop;
- после копирования показать ненавязчивое подтверждение;
- не печатать invitation token в console;
- корректно сообщить `Device limit reached`, если заняты пять slots;
- focus trap, Escape/close, возврат фокуса и остальные modal accessibility
  следуют уже существующим паттернам проекта.

QR code и новая frontend dependency не требуются. Не добавляй их, если в
репозитории уже нет готовой реализации.

## Browser security без сравнения Host

Переписанный внешним proxy `Host` нельзя использовать для доказательства
browser same-origin. Удали решающее сравнение `Origin == Host` и не заменяй его
`X-Forwarded-Host`, `Forwarded`, allowlist доменов или provider detection.

Для browser API используй одновременно session cookie и browser-controlled
request context:

- отклоняй `Sec-Fetch-Site: cross-site` и `same-site`;
- принимай `Sec-Fetch-Site: same-origin` для обычных UI REST requests;
- `Sec-Fetch-Site: none` допускай только там, где это действительно безопасная
  user navigation; он не разрешает mutation;
- для `POST`, `PUT`, `PATCH`, `DELETE` frontend добавляет фиксированный custom
  header, например `X-Render-Node-Request: 1`;
- browser mutation без этого заголовка отклоняется;
- CORS не включать: cross-origin JavaScript не должен получить разрешающий
  preflight;
- `Origin`, если он присутствует, по-прежнему строго парсить: ровно одно
  значение, только `http`/`https`, без `null`, userinfo, path, query, fragment,
  whitespace/control characters; в production только HTTPS;
- валидный browser Origin больше не сравнивается с backend-visible `Host`;
- не доверять `X-Forwarded-Host`, `X-Forwarded-Proto` и `Forwarded` для
  security decision.

Для application WebSocket:

- browser connection требует действующую session cookie;
- требует допустимый browser request context и `Sec-Fetch-Site: same-origin`;
- требует строго валидный HTTPS Origin в production, но не сравнивает его с
  переписанным `Host`;
- cross-site/malformed/missing browser context закрывается 4403;
- отдельный WS ticket в этой модели не добавлять;
- native/non-browser WebSocket без Origin допускается только с действующим
  Bearer token;
- сохранить существующий message-size limit и event contracts.

Различай browser и service-client запросы консервативно. Наличие публично
известного Bearer или его автоматическая подстановка gateway не должно позволять
непарному браузеру обойти session gate. Добавь negative tests именно на это.

## Внутренний Bearer и reset-access

`RENDER_NODE_AUTH_TOKEN` остаётся обязательным в production, но становится
только закрытым service/management credential:

- не передавать его frontend;
- не устанавливать его в cookie;
- не публиковать в Nginx response;
- public Nginx больше не должен безусловно подставлять Bearer каждому browser
  API/WebSocket request;
- origin-less CLI/server-to-server запрос с правильным Bearer продолжает
  работать через loopback/private backend;
- browser request с Origin/Fetch Metadata и Bearer, но без session cookie,
  должен быть отклонён.

Добавь локальную recovery-команду:

```text
render-node reset-access
```

Она выполняется с root/operator shell, читает внутренний Bearer из закрытого
deployment config и вызывает backend по `127.0.0.1:8000`. Reset должен атомарно:

- инвалидировать все browser sessions и invitations;
- вернуть access state в `unclaimed`;
- не рестартовать сервис;
- не отменять и не менять jobs или Blender operations;
- вывести: `Access reset; the next browser can claim this node.`

Нельзя публиковать reset endpoint без Bearer или разрешать его browser cookie.
Не логируй Bearer в command line/process list или stdout.

## Удаление Basic Auth и credentials

Поскольку user-facing boundary теперь first-browser pairing, полностью удали
устаревший Basic Auth:

- удалить `auth_basic` и `auth_basic_user_file` из Nginx;
- удалить генерацию `admin.htpasswd`;
- удалить `RENDER_NODE_ADMIN_USERNAME`, `RENDER_NODE_ADMIN_PASSWORD` и
  `RENDER_NODE_ADMIN_HTPASSWD_B64` из entrypoint, installer, examples, docs и
  tests;
- удалить plaintext `credentials` state и вывод Username/Password;
- удалить `render-node credentials`; вместо него документировать
  `render-node reset-access`;
- удалить ставшую ненужной runtime/build зависимость `htpasswd`/Apache utils,
  если она больше нигде не используется;
- update/repair старой установки должен безопасно удалить только известные
  legacy credential files в точных Render Node state paths, не затрагивая
  пользовательские jobs, database, Blender runtimes или посторонние файлы.

Не оставляй два параллельных публичных режима Basic-or-pairing и не добавляй
feature flag. Внешняя provider authentication может существовать дополнительно,
но Render Node от неё не зависит.

## Backend structure

Держи session/invitation state и атомарные переходы вне HTTP handlers. Допустим
один небольшой typed access manager/service; не создавай универсальный auth
framework или hierarchy «на будущее».

Сохрани единый security contract для HTTP и WebSocket настолько, насколько это
практично. Используй существующие `ErrorResponse`, request ID и security
headers. Health/readiness остаются публичными. Request/upload limits и render
state machine не менять.

## Development и frontend initialization

- `make dev` не показывает login screen: первый открытый browser автоматически
  claims текущий backend process;
- backend restart в dev сбрасывает pairing; уже открытая вкладка должна
  автоматически повторить claim, если нода снова unclaimed;
- не добавляй secrets в `VITE_*` или production bundle;
- REST остаётся относительным `/api/v1`, WebSocket строится из
  `window.location`;
- mock UI может явно обходить access bootstrap только в существующем mock
  build mode; production mode не должен иметь runtime bypass;
- не используй `allowedHosts: true` как security boundary и не утверждай, что
  Vite dev server безопасно публиковать анонимно в Internet;
- production и внешний acceptance scenario проверяй через HTTPS same-host
  gateway, который намеренно переписывает upstream `Host`.

## Обязательные тесты

Обновляй существующие suites, не создавай параллельную реализацию.

Backend:

- первый deliberate browser POST atomically claims unclaimed node;
- два concurrent независимых claim: успех ровно у одного;
- GET frontend/health/ready/probe access state не меняет;
- повторный claim с той же session идемпотентен;
- неизвестный второй browser получает `node_already_claimed` и не может читать
  API, менять jobs или открыть WebSocket;
- session cookie flags production/development;
- подделанный, malformed, expired и неизвестный session token отклоняется;
- `Sec-Fetch-Site: cross-site` и `same-site` отклоняются даже с украденной или
  действующей cookie;
- browser mutation без custom header отклоняется;
- arbitrary public HTTPS Origin работает при переписанном upstream Host;
- malformed/null/userinfo/path/query/fragment/duplicate Origin отклоняется;
- forwarded headers не участвуют в разрешении;
- browser Bearer без session не обходит gate;
- origin-less REST и WebSocket service client с Bearer работают;
- browser WebSocket: valid session/same-origin проходит, missing session,
  cross-site и malformed Origin получают корректные close codes;
- invitation требует авторизованную session;
- invitation истекает без реального sleep через управляемые clock fixtures;
- invitation single-use, включая concurrent redeem;
- нельзя создать больше пяти sessions;
- reset инвалидирует sessions/invitations, возвращает unclaimed и не меняет
  jobs;
- auth regression, request limits, security headers и WebSocket message limit.

Frontend/unit/config:

- access bootstrap завершается до queries и WebSocket;
- locked/loading/error/retry states;
- pairing fragment очищается до дальнейшей навигации и отправляется только в
  JSON body;
- unsafe request получает custom header, GET — нет;
- share button является первым control в `.header-controls`, имеет share icon,
  aria-label и доступный tooltip с заданным текстом;
- modal создаёт invitation только по действию пользователя, копирует ссылку,
  показывает expiration/error/device-limit и очищает sensitive UI state после
  закрытия;
- mobile layout не скрывает и не обрезает share control;
- direct artifact/download links работают с cookie;
- production bundle не содержит internal Bearer, test bypass или mock secrets.

Deployment:

- fresh install/container start не требуют username/password/origin/public URL;
- generated config содержит private Bearer, но не содержит admin credentials;
- Nginx не содержит Basic Auth и не injects Bearer в public browser requests;
- backend остаётся loopback-only, frontend/API/WS идут через один public port;
- update/repair сохраняют jobs, database, Blender runtimes и internal token;
- legacy Render Node credential files удаляются безопасно;
- `render-node reset-access` использует loopback, не раскрывает token и работает
  без service restart;
- shell syntax, bundle contract и container UID/cgroup lifecycle не
  регрессируют.

Browser acceptance / Playwright:

1. Запусти production-like HTTPS proxy на произвольном hostname.
2. Proxy намеренно переписывает upstream `Host`, сохраняя browser Origin и
   Fetch Metadata.
3. Первый browser context автоматически claims node.
4. Проверить REST mutation, Blender Download action и application WebSocket.
5. Второй независимый context заблокирован.
6. В первом context нажать самый первый share icon, скопировать invitation.
7. Открыть fragment link во втором context и убедиться, что он получает доступ.
8. Третий context не может повторно использовать тот же link.
9. Довести число sessions до пяти и проверить отказ шестой.
10. Проверить desktop и mobile screenshots, tooltip, modal focus, clipping,
    horizontal overflow и отсутствие console errors.

Не считать browser-проверку завершённой только по DOM assertions: просмотри
screenshots.

## Проверки перед завершением

Используй команды, реально объявленные в репозитории. Как минимум выполни:

Backend:

- `uv run ruff format --check app tests`
- `uv run ruff check app tests`
- `uv run mypy app tests`
- `uv run pytest`
- `uv run alembic check`

Frontend:

- `npm run lint`
- `npm run build`
- релевантные Node/frontend tests;
- один целевой Playwright pairing scenario, описанный выше.

Deployment:

- релевантные behavioral tests;
- `bash -n` для изменённых shell scripts;
- `git diff --check`;
- поиск runtime-остатков `auth_basic`, admin password/username, htpasswd,
  credentials command, статической origin allowlist и public Bearer injection.

Не запускай настоящий Blender/GPU render smoke: access refactor не меняет
render pipeline.

## Документация и handoff

Обнови `README.md`, `backend/README.md`, `deploy/README.md`, `.env.example`,
`PYTHON_PROJECT_ARCHITECTURE.md` и `IMPLEMENTATION_STATE.md`.

Документация должна прямо объяснять:

- первый браузер автоматически забирает одноразовую ноду;
- это осознанная TOFU race, а не подтверждение личности;
- остальные устройства подключаются только по одноразовой 5-minute fragment
  link из share-кнопки;
- максимум пять browser sessions;
- при потере доступа оператор выполняет `render-node reset-access`;
- restart backend сбрасывает access state;
- public URL не настраивается, переписанный proxy Host допустим;
- port 8000 приватный, internal Bearer предназначен только для loopback/service
  clients;
- `make dev` не является анонимным production deployment.

В финальном отчёте перечисли новый access contract, удалённый Basic/credential
contract, API и UI pairing flow, migration поведения deployment, выполненные
проверки и всё, что нельзя было проверить локально.

## Ограничения

- Не создавай commit, push или PR.
- Не добавляй provider-specific интеграции.
- Не добавляй CAPTCHA, пароль, аккаунты, роли, OAuth или внешний auth service.
- Не добавляй QR dependency.
- Не передавай secrets через URL query, local/session storage или logs.
- Не доверяй IP, User-Agent, forwarded headers или первому увиденному Host.
- Не публикуй backend port 8000.
- Не меняй Job/Blender/Artifact API кроме общей access middleware.
- Не меняй render execution, scheduler, Blender sandbox или GPU lifecycle.
- Не создавай БД migration без обнаруженной необходимости.
- Сохраняй пользовательские и несвязанные изменения.
