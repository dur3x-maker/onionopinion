# OpinionOnion

Небольшая privacy-first платформа для анонимных и псевдонимных текстовых
обсуждений. Все страницы рендерятся сервером и полностью работают без JavaScript.

## Быстрый запуск

1. Скопируйте `.env.example` в `.env`.
2. Замените `POSTGRES_PASSWORD` и `SECRET_KEY` длинными случайными значениями.
3. Запустите приложение:

```powershell
docker compose up --build
```

Сайт доступен на <http://127.0.0.1:8000>. Alembic-миграции применяются
автоматически при старте контейнера.
Host-порт можно изменить через `APP_PORT`, не меняя Compose-файл.

Проверка:

```powershell
docker compose ps
docker compose exec app alembic current
docker compose exec app alembic check
```

Остановка без удаления данных:

```powershell
docker compose down
```

PostgreSQL и обработанные аватары находятся в отдельных именованных volumes.
Команда `docker compose down -v` удалит оба хранилища без возможности восстановления.

## Первый администратор

Администратор не может зарегистрироваться через сайт. После запуска выполните:

```powershell
docker compose exec app python -m app.cli create-admin `
  --login operator `
  --display-name "Оператор"
```

Пароль дважды запрашивается интерактивно и не попадает в аргументы процесса или
историю shell. Команда откажется повышать уже существующую учётную запись. После
обычного входа оператору станет доступна страница `/admin`.

## Аватары

- Принимаются только настоящие JPEG, PNG и WebP до 2 MB.
- Фактический размер HTTP-тела ограничивается до multipart-парсера, даже если
  клиент не прислал `Content-Length` или указал ложное значение.
- Расширение, MIME и фактический формат должны совпадать.
- Проверяется число пикселей; повреждённые изображения и decompression bombs
  отклоняются.
- Сервер исправляет EXIF-ориентацию, делает центральный квадратный crop,
  уменьшает до 256×256 и создаёт новый WebP.
- EXIF, ICC и прочие исходные метаданные не переносятся.
- Исходное имя не используется. Файл получает случайный 128-битный идентификатор.
- Оригинал обрабатывается в памяти и никогда не публикуется.
- При замене или удалении старый обработанный файл удаляется после успешного
  изменения БД.

Анонимы не загружают файлы. Случайный CSS-аватар сохраняется вместе с их
случайным псевдонимом только для визуального различения участников.

## Прозрачная модерация

Принцип площадки: модерируются запрещённые действия и контент, а не мнения.
Политические взгляды, критика власти или администрации, критика правил и мат сами
по себе не являются основанием удаления.

Действующие правила хранятся как стабильный `Rule` (`R1`, `R2`...) и неизменяемые
`RuleVersion`. Новое содержание правила создаётся отдельной версией:

```powershell
docker compose exec app python -m app.cli add-rule-version `
  --code R2 `
  --title "Уточнённое название" `
  --text "Новый полный текст правила"
```

Старая версия остаётся read-only. Только текущую версию активного правила можно
выбрать для нового решения.

Жалобы попадают в `/admin`. Для soft delete оператор обязан выбрать конкретную
действующую `RuleVersion` и дать короткое публичное пояснение. Сервер создаёт один
`ModerationDecision` на активный объект и одновременно закрывает связанные новые
жалобы. Уникальный active-target ключ защищает от дубликатов при повторной обработке.

Удалённый узел сохраняет исходный текст в БД и публично показывает правило,
пояснение, дату и ссылку на `/moderation/decisions/{id}`. Страница решения содержит
исторический текст правила и отдельное древовидное обсуждение на том же comment
engine. Анонимные и зарегистрированные пользователи могут критиковать решение без
JavaScript. Login оператора публично не выводится.

Оператор может отменить решение. Тогда создаётся `ModerationReview`, контент и
ветка восстанавливаются, а первоначальное решение, RuleVersion и публичная история
сохраняются. Внутренний `moderation_audit` знает реальные operator accounts, но не IP.

## Защита `/admin`

Role-based login/password остаётся обязательным. Опциональный сетевой барьер
задаётся только окружением:

```dotenv
ADMIN_ALLOWED_NETWORKS=192.0.2.10/32,2001:db8:1234::/48
TRUSTED_PROXY_NETWORKS=172.20.0.0/24
```

Это documentation ranges, а не production-значения. Если
`ADMIN_ALLOWED_NETWORKS` пуст, дополнительная проверка выключена. Если задана —
доступ требует одновременно актуальной admin-сессии и попадания client address в
CIDR. Отказ по сети возвращает нейтральный 404 и не публикует конфигурацию.

Uvicorn запущен с `--no-proxy-headers`. Приложение игнорирует `Forwarded`,
`X-Real-IP` и `X-Forwarded-For` от недоверенного TCP peer. Для peer из
`TRUSTED_PROXY_NETWORKS` разбирается только `X-Forwarded-For`: доверенные proxy hops
снимаются справа, а ближайший оставшийся адрес считается клиентом. Все реальные
proxy hops должны быть перечислены; отсутствующая или некорректная цепочка
отклоняется. Сети `/0` запрещены, чтобы клиент не мог объявить себя доверенным proxy.
IP используется только в памяти для access control, не пишется в БД, audit или
application logs.

## Локальная разработка

Нужны Python 3.11+ и PostgreSQL:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
Copy-Item .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --no-access-log
```

## Production configuration

Перед публичным запуском задайте минимум:

| Переменная | Требование |
|---|---|
| `ENVIRONMENT` | `production`; включает строгую проверку конфигурации и HSTS |
| `SECRET_KEY` | новый случайный секрет длиной 32+; placeholder/test значения отклоняются |
| `DATABASE_URL` | PostgreSQL URL с отдельным сильным паролем, без default credentials |
| `POSTGRES_PASSWORD` | тот же сильный пароль для текущего Compose-варианта |
| `COOKIE_SECURE` | `true` в production; cookie также HttpOnly и SameSite=Lax |
| `ALLOWED_HOSTS` | все реальные clearnet/onion hostnames через запятую, без `*` |
| `ADMIN_ALLOWED_NETWORKS` | production admin IP/CIDR, если сетевой барьер включён |
| `TRUSTED_PROXY_NETWORKS` | только фактические сети reverse proxy |
| `AVATAR_STORAGE_DIR` | постоянный writable-каталог; в Compose это `/data/avatars` |
| `OFFICIAL_CLEARNET_URL` | реальный официальный HTTPS URL либо пусто |
| `OFFICIAL_ONION_URL` | реальный v3 `.onion` HTTP(S) URL либо пусто |

Дополнительно доступны `AVATAR_MAX_BYTES`, `ALLOWED_HOSTS`, лимиты текста и rate
limit из `app/config.py`. Ненастроенные официальные адреса не заменяются фиктивными;
страница `/addresses` показывает только значения окружения.

Предполагаемая следующая схема — две точки входа (clearnet и Onion Service) через
reverse proxy к **одному** backend и **одной** PostgreSQL. Отдельные users/content DB
не нужны. Tor, nginx, TLS и `.onion` этим репозиторием сейчас не устанавливаются.

Перед запуском нужно отдельно:

- настроить TLS для clearnet, Tor Onion Service и persistent onion keys;
- указать реальные hostnames, proxy CIDR, admin CIDR и официальные адреса;
- проверить ownership/permissions постоянного avatar volume;
- настроить регулярные проверяемые backups PostgreSQL и avatar volume, retention и restore drill;
- ограничить доступ к PostgreSQL, не публиковать app-порт напрямую и проверить firewall;
- применить `alembic upgrade head`, затем проверить `/healthz` и operator flow.

## Тесты

```powershell
pip install -e ".[test]"
pytest
```

Тесты используют временную SQLite БД и отдельный временный каталог аватаров.
PostgreSQL и рабочие media-файлы они не затрагивают.

## Структура

```text
app/
  admin.py        operator routes, moderation and audit
  avatars.py      validation, crop and safe WebP encoding
  cli.py          first-admin and immutable rule-version commands
  config.py       environment configuration
  database.py     SQLAlchemy engine and sessions
  models.py       content, reports, versioned rules, decisions and reviews
  moderation.py   rule validation, idempotent delete, notices and reversal
  security.py     Argon2id, CSRF, rate limiting and trusted client IP resolution
  services.py     identities, authorization and shared safe comment tree
  web.py          public SSR routes and forms
  main.py         application, middleware and security headers
  templates/      Jinja2 templates
  static/         local CSS only
alembic/          database migrations
tests/            integration and security regression tests
```

## Безопасность и приватность

- Нет аналитики, трекеров, CDN, внешних frontend-запросов или fingerprinting.
- Приложение не сохраняет IP в БД; HTTP access log Uvicorn отключён.
- Login никогда не выводится публично.
- Jinja2 экранирует пользовательский текст; CSP запрещает выполнение script.
- Все изменяющие состояние формы защищены CSRF.
- Сессия очищается при login/register. Серверная `session_version` инвалидирует
  старые cookie после нового входа или logout и исключает сохранение украденной
  привилегии.
- Роль администратора всегда читается из БД, а не из cookie.
- Лимитер использует случайный ключ сессии, не IP.
- Production-конфигурация отклоняет weak secret, insecure cookie, localhost/wildcard
  hosts и SQLite/default database credentials.
- Request body ограничен до multipart parsing; аватары дополнительно проверяются
  по MIME, сигнатуре, пикселям и итоговому формату.

Подписанная cookie не шифруется, поэтому в неё помещаются только случайные
технические идентификаторы, user id и версия сессии — не пароль, login или
персональные данные.

## Известные ограничения

- Rate limiter хранится в памяти одного процесса. Текущий deployment должен
  запускаться с одним worker. При горизонтальном масштабировании понадобится
  общее TTL-хранилище, но Redis намеренно не добавлен в текущий scope.
- Новый login инвалидирует предыдущую сессию этой учётной записи. Это простое
  безопасное ограничение текущей версии.
- Нет восстановления пароля, банов, поиска, сообщений, голосований и уведомлений.
- Приложение минимизирует данные, но не обещает абсолютную анонимность.
- Встроенный limiter сессионный: он снижает случайный abuse, но не заменяет
  edge-level anti-DoS/rate limiting перед публичным сервисом.
- `RuleVersion` неизменяем на уровне ORM и не имеет update endpoint; доступ к БД
  всё равно должен быть ограничен доверенными операторами.
- Production reverse proxy/Tor hardening, TLS, backups и `.onion` deployment —
  отдельный следующий инфраструктурный этап.
