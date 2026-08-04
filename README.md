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

## Модерация

Жалобы попадают в очередь `/admin`. Оператор может оставить публикацию,
soft-delete её или закрыть жалобу. Каждое решение записывается в отдельный
`moderation_audit`, содержащий только оператора, действие, объект, время и
необязательную заметку — без IP и обычной пользовательской активности.

При soft delete комментарий остаётся узлом дерева с текстом
`[комментарий удалён]`; дочерние ответы сохраняются. Удалённый пост исключается
из ленты и профиля, но его прямая ссылка и существующая ветка остаются доступны.

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

Для HTTPS/Onion Service установите `COOKIE_SECURE=true`, задайте реальные хосты
в `ALLOWED_HOSTS` и новый `SECRET_KEY`. Production nginx/Tor deployment относится
к третьей итерации.

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
  cli.py          management command for the first admin
  config.py       environment configuration
  database.py     SQLAlchemy engine and sessions
  models.py       User, Post, Comment, Report, ModerationAudit
  security.py     Argon2id, CSRF and session-based rate limiting
  services.py     identities, authorization and safe comment tree
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
- Production nginx/Tor hardening и `.onion` deployment запланированы отдельно.
