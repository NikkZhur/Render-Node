# Промт: лаконичный deployment Render Node

Продолжи работу в репозитории Render Node. Сначала полностью прочитай
`AGENTS.md`, `IMPLEMENTATION_STATE.md`, `PYTHON_PROJECT_ARCHITECTURE.md`, текущий
незакоммиченный diff и все untracked deployment-файлы.

Не отменяй пользовательские и несвязанные изменения. Не делай commit, push или
PR. Текущая deployment-доработка не является обязательной архитектурой: если
проще и надёжнее заменить её целиком, перепиши её с нуля, сохранив только
проверенные контракты.

## Результат для пользователя

Основной сценарий — временный single-tenant Ubuntu GPU Pod:

1. Пользователь арендует Ubuntu 22.04/24.04 x86_64 Pod с NVIDIA GPU.
2. Открывает HTTP-порт 8080.
3. Запускает одну неизменяемую команду:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/NikkZhur/Render-Node/master/install.sh | bash
   ```

4. Команда всегда устанавливает последний успешно опубликованный стабильный
   build.
5. Приложение запускается без Docker, Docker Compose и systemd внутри Pod.
6. Пользователь открывает HTTPS URL платформы, рендерит собственные доверенные
   сцены, скачивает результат и удаляет Pod.

Сохрани альтернативный сценарий: RunPod может напрямую запустить готовый GHCR
image. Dockerfile нужен GitHub Actions, но Docker daemon внутри Pod не нужен.

Не добавляй поддержку Debian, обычных VM, Caddy или Compose в этой задаче.

## Минимальная архитектура

Используй одну понятную раскладку:

```text
/opt/render-node/releases/<version>  immutable application release
/opt/render-node/current             symlink на активный release
/opt/render-node/blender/<version>   bundled Blender 5.2.0 и 4.1.1
/workspace/.render-node              persistent config, credentials и logs
/workspace/database                  SQLite
/workspace/jobs                      scenes и render results
/workspace/blender                   дополнительные Blender runtimes
/run/render-node                     непостоянные PID и runtime-файлы
```

Не храни PID в `/workspace`. Не дублируй install/update-логику. Повторный запуск
той же one-line команды является одновременно install, repair и update.

Используй один Nginx template и один supervisor для native и image deployment,
только если они остаются простыми. Допустимы короткие отдельные entrypoint-
обёртки. Не объединяй файлы ценой большого количества условных веток.

Удаляй существующий deployment-код, который после новой схемы не вызывается.
В частности, удали Compose/Caddy и дублирующиеся native/container файлы, если они
больше не имеют самостоятельного контракта.

## GitHub Release bundle

Для стабильного semver-тега `vX.Y.Z` GitHub Actions должен:

1. Выполнить backend Ruff, strict mypy и полный pytest.
2. Выполнить frontend lint и production build.
3. Только после успешных проверок собрать
   `render-node-linux-x64.tar.gz` и его `.sha256`.
4. Явно создать non-draft, non-prerelease GitHub Release и загрузить оба asset.
5. Только после успешных проверок опубликовать versioned GHCR image.

Publish job должен зависеть от checks и один иметь необходимые
`contents: write`/`packages: write`. При ошибке проверки Release, assets и image
не публикуются. Для GitHub Release предпочитай штатный `gh`, не добавляй action,
если он не нужен.

Вынеси сборку bundle в небольшой локально запускаемый скрипт. Bundle содержит:

- `VERSION` с точным semver;
- backend `app`, migrations, `alembic.ini`, `pyproject.toml`, `uv.lock`;
- готовый frontend `dist`;
- необходимые gateway/supervisor/management scripts.

Bundle не содержит `.venv`, `node_modules`, frontend source, Blender binaries,
cache, test fixtures, uploads или секреты. Сборка должна быть воспроизводимой в
разумных пределах и не зависеть от файлов вне явного manifest.

Workflow для `master` может публиковать `latest` GHCR image после тех же checks,
но GitHub Release bundle создаётся только из стабильного semver-тега.

## install.sh: latest, repair и rollback

Команда без `--version` при каждом запуске получает
`releases/latest/download/render-node-linux-x64.tar.gz` и соответствующий
`.sha256`. Она не содержит зашитого номера версии, не использует draft,
prerelease, source archive из `master` или старый cache как скрытый fallback.
Если latest build недоступен или checksum неверна, установка завершается с
понятной ошибкой и не повреждает текущую версию.

`--version 1.2.3` — явное исключение для установки конкретного стабильного
Release `v1.2.3`.

Перед распаковкой проверь SHA-256 и содержимое tar: запрети абсолютные пути,
`..`, выход из release root и небезопасные symlink/hardlink. Распаковывай в новый
staging/release directory, а не поверх активной версии.

Установка должна:

- проверить Ubuntu 22.04/24.04, x86_64, root и рабочий `nvidia-smi`;
- установить только необходимые runtime packages;
- установить закреплённый `uv` и выбранную совместимую Python 3.13 patch-версию;
- создать per-release `.venv` через frozen lock без dev dependencies;
- не скачивать Node.js и не собирать frontend на GPU Pod;
- скачать Blender 5.2.0/4.1.1 из официального архива, проверить закреплённые
  SHA-256 и выполнить `blender --version`;
- сгенерировать Basic Auth и backend Bearer token только при первой установке;
- определить RunPod origin как
  `https://${RUNPOD_POD_ID}-8080.proxy.runpod.net`, а вне RunPod принять
  `--origin`;
- запустить долгоживущие процессы как отдельного non-root пользователя;
- дождаться `/ready` через gateway на 8080.

Повторный запуск должен сохранять credentials, database, jobs и дополнительные
Blender runtimes. Если `/workspace` сохранился, а `/opt/render-node` исчез после
reset Pod, команда восстанавливает приложение. Если изменился Pod ID, обнови
allowed origin и URL credentials, не меняя пароль и token.

Обновление выполняй атомарно:

1. Полностью подготовь новый release отдельно.
2. Сохрани ссылку на текущий рабочий release.
3. Останови сервис и переключи `current` атомарной заменой symlink.
4. Запусти новую версию и дождись readiness.
5. При ошибке верни symlink, запусти предыдущую версию и заверши команду с
   ошибкой.
6. После успеха оставь только активный и один предыдущий release.

Если уже установлена актуальная версия, не переустанавливай её без причины, но
выполни repair отсутствующих runtime-файлов и обеспечь запуск сервиса. Не
перезаписывай посторонний `/usr/local/bin/render-node`.

## Процессы, секреты и логи

`render-node start|stop|restart|status|logs|credentials|uninstall` работает без
systemd. PID находится только в `/run/render-node`; проверяй идентичность
процесса, а не только `kill -0`. Stale/reused PID не должен блокировать запуск
или позволять остановить посторонний процесс. Остановка завершает supervisor,
Nginx, backend и их дочерние процессы.

Не передавай пароль аргументом `htpasswd`; используй stdin. Plaintext password
не должен наследоваться дочерними процессами. Backend не должен наследовать
`RUNPOD_API_KEY` и другие cloud secrets. Формируй чистое runtime environment,
сохраняя только настройки Render Node и необходимые `PATH`, locale, certificate,
`LD_LIBRARY_PATH`, `NVIDIA_*` и `CUDA_*`. Не утверждай, что этот allowlist
достаточен для CUDA/OptiX без реального GPU smoke.

Token не должен попадать во frontend, git или logs. Config, credentials и
htpasswd получают минимальные права. Ограничь служебные логи без отдельной
тяжёлой logging-системы: отключи ненужный access log и обеспечь простой предел
или ротацию service/error log.

## Тесты

Не ограничивайся поиском строк. Добавь поведенческий test harness с временными
root/state/run каталогами и fake external commands. Тесты не меняют реальные
`/opt`, `/workspace`, `/run`, пользователей или процессы.

Покрой минимум:

- shell syntax и безопасный `--help`;
- latest и explicit-version URLs;
- успешную и неверную checksum;
- безопасную проверку tar paths/links;
- отсутствие Node/Docker/systemd в native flow;
- состав bundle и отсутствие запрещённых файлов;
- fresh install, повторный no-op, repair после потери `/opt`;
- сохранение credentials/jobs и обновление RunPod origin;
- atomic update и rollback после failed readiness;
- partial-install cleanup;
- stale/reused/foreign PID и fake start/stop/restart;
- пароль через stdin и удаление cloud secrets из child environment;
- `envsubst` без повреждения Nginx variables;
- Nginx config test, если binary доступен;
- workflow dependency: publish невозможно до checks.

Не привязывай тест к абсолютному `/usr/bin/envsubst`. Docker image smoke с
`blender --version` выполняй только при доступном Docker; GPU тесты локально не
требуются.

## Документация и проверка

Обнови `README.md`, `deploy/README.md`, `PYTHON_PROJECT_ARCHITECTURE.md` и
`IMPLEMENTATION_STATE.md`. Простыми словами раздели два способа:

- Ubuntu Pod: one-line installer скачивает проверенный latest Release bundle;
- prepared image: RunPod напрямую запускает GHCR image без Docker внутри Pod.

Запусти реально доступные bash syntax/ShellCheck, Ruff format/check, strict mypy
`app`, полный backend pytest, frontend lint/build, workflow schema validation и
`git diff --check`. Не заявляй CUDA/OptiX без реального RunPod GPU smoke.

В конце сообщи:

1. итоговую архитектуру и пользовательский flow;
2. что было переписано, объединено или удалено;
3. выполненные проверки и недоступные реальные smoke tests;
4. риски, оставшиеся до публикации первого Release;
5. что commit/push не выполнялись.

Перед изменениями зафиксируй baseline размера текущей deployment-доработки.
После изменений отдельно сравни production deployment LOC, tests и docs. Не
включай в подсчёт этот prompt-файл `RELEASE_BUNDLE_REFACTOR_PROMPT.md` и
generated artifacts; сокращение строк не важнее корректности и простоты.
