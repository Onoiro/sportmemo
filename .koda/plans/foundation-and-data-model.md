# SportMemo — Этап 1: фундамент репозитория и модель данных

Дата создания: 2026-10-06 (обновлён 2026-10-06 после ответов пользователя)
Статус: **реализован** 2026-10-10 (ветка `feature/foundation` → squash-merge в `master`, версия 0.1.1)
Репозиторий: github.com/Onoiro/sportmemo
Домен: sportmemo.ru

> **Результат реализации.** Все 13 шагов выполнены. 117 тестов проходят на Python 3.12 и 3.13,
> линтер чист, CI настроен. Отклонения от плана зафиксированы в
> [`.koda/backlog.md`](../backlog.md) (раздел «Этап 1»): порядок приложений (`accounts` до
> `catalog`), `Sport` через data-миграцию вместо фикстуры, автоматическое разбиение миграции
> `catalog` на 0001/0002 из-за цикла `Person.avatar ↔ photos.Photo`.

---

## 1. Контекст и принятые решения

SportMemo — публичный архив спортивных фотографий (на старте — бейсбол в России: детские, юношеские,
ветеранские команды). Пользователи оцифровывают личные бумажные архивы и публикуют их в общий архив
с атрибуцией (кто на фото, команда, событие, год, источник).

### Согласовано с пользователем

| Решение | Значение |
|---|---|
| Модель доступа | Публично для всех |
| Кто загружает | Любой зарегистрированный пользователь |
| Кто заводит команды | Пользователи, команда создаётся в статусе `draft` и публикуется модератором |
| Регистрация | Email + пароль, **с подтверждением email** |
| Модератор | Пользователь (владелец), возможно позже отдельный человек |
| Масштаб на 3–5 лет | Тысячи пользователей, один VPS |
| Хостинг | Основное хранилище + вторая независимая копия; обе — в РФ (152-ФЗ) |
| Почта | Транзакционная отправка — Yandex Cloud Postbox (первые 2 000 писем/мес бесплатно) |
| Мультиспортность | Архитектура мультиспортная (`Sport` в схеме), продукт — одномспортный (только бейсбол) |
| Публичный UI | Серверный рендеринг (Django templates) ради SEO — вариант А |
| Пакеты | `uv` (не poetry) — стандарт 2026, консистентность с Lex |
| Файлы правил | `KODA.md` (русский) + `AGENTS.md` (английский) — поддерживаются параллельно |
| Стек | «Скучный», переиспользующий опыт TaskMan (Django) |

### Ключевые архитектурные правила проекта

1. **Архитектура мультиспортная, продукт одномспортный.** В схеме есть `Sport`, в UI выбора вида
   спорта нет (появится, когда найдётся евангелист по другому виду спорта).
2. **Никакой бейсбольной специфики в структуре таблиц.** Общие сущности (Team/Event/Person),
   специфика вида спорта — в `attributes` (JSONB).
3. **`Person` ≠ `User`.** Человек на фото — публичная сущность, не требующая аккаунта. Пользователь —
   приватный аккаунт, опционально связанный с `Person` («это я»).
4. **Оригинал фото никогда не перезаписывается.** Обработанная версия — производная, хранится отдельно.
5. **Дубликаты не удаляются, а сливаются.** Есть поле-указатель на канонический объект и операция
   слияния, переносящая связи.
6. **Всё значимое логируется.** `django-simple-history` на всех содержательных моделях + журнал
   модераторских решений.
7. **Уникальность slug — в пределах вида спорта** (для Team/Event). Для `Person` — глобальная
   уникальность с числовым суффиксом при коллизии.
8. **Фамилии несовершеннолетних не публикуются** — управляется полем `Person.name_visibility`.

### Осознанно отложено (не делаем в этапе 1)

- API (DRF/django-ninja) — не нужен, пока клиент один и это сайт.
- Обработка фото (OpenCV), перцептивный хеш — этап 3.
- Публичный UI, галереи, поиск, SEO — этап 2.
- PWA, водяные знаки, юридические страницы — этап 4.
- Настройка SMTP (Postbox) — этап 4; в dev используется console email backend.
- Мультиспортный UI, «конструктор видов спорта» — не делаем, пока не появится второй спорт.

---

## 2. Стек

| Слой | Выбор | Обоснование |
|---|---|---|
| Язык | Python 3.12 | Поддерживается Django 5.2 LTS, стабилен, есть в Docker |
| Фреймворк | Django 5.2 LTS | LTS до апреля 2028, экосистема проверена; апгрейд на 6.2 LTS в апреле 2027 |
| Публичный UI | Django templates + Bootstrap 5 | SEO из коробки, быстрый отклик, Open Graph для карточек в мессенджерах |
| Интерактив | htmx + Alpine.js | «Показать ещё», модалки, превью загрузки — без SPA и сборки |
| БД | PostgreSQL 17 | Связи, `pg_trgm` (похожие имена), `tsvector` (поиск), JSONB (атрибуты) |
| Файлы | MinIO (S3 API) через `django-storages` | Бесплатно на своём сервере, переезд в облако = одна настройка |
| Превью | `django-imagekit` | Превью генерируются лениво и кэшируются в MinIO |
| Аудит | `django-simple-history` | Журнал изменений с автором и откатом |
| Аутентификация | `django-allauth` (email + пароль, подтверждение email) | Готовые подтверждение email, восстановление пароля, защита |
| Почта | Yandex Cloud Postbox (SMTP) | 2 000 писем/мес бесплатно, DKIM/SPF/DMARC, российская юрисдикция |
| Фон. задачи | Celery + Redis | Обработка фото, генерация производных, отчёты |
| Изображения | Pillow + OpenCV (headless) + `imagehash` | Этап 3, но зависимости закладываются сразу |
| Пакеты | `uv` | Стандарт 2026, быстрее poetry, `uv.lock` + `uv sync` + `uv run` |
| Линтер | ruff | Один инструмент вместо flake8+isort+black |
| Тесты | pytest + pytest-django + factory_boy | Консистентность с TaskMan/Lex |
| Инфра | Docker Compose на VPS, Nginx | Как в существующих проектах; без k8s и микросервисов |

---

## 3. Структура репозитория

```
sportmemo/
├── manage.py
├── pyproject.toml               # зависимости, настройки ruff/pytest, version
├── uv.lock
├── Makefile                     # up/down/migrate/run/test/lint/shell
├── Dockerfile                   # python:3.12-slim + системные либы
├── docker-compose.yml           # db, redis, minio, web, worker
├── .env.example
├── .gitignore / .dockerignore
├── README.md
├── AGENTS.md                    # правила проекта для агентов (английский)
├── KODA.md                      # правила проекта для Koda CLI (русский) + память проекта
├── config/
│   ├── settings.py              # единый файл, значения из env (django-environ)
│   ├── urls.py
│   ├── wsgi.py / asgi.py
│   └── celery.py
├── apps/
│   ├── core/                    # абстрактные модели, Sport, healthcheck
│   ├── accounts/                # User, согласия, роли
│   ├── catalog/                 # Team, TeamMembership, Person, PersonName, Event, EventTeam
│   ├── photos/                  # Source, Photo, PhotoPerson
│   ├── comments/                # Comment
│   └── moderation/              # Report, ModerationAction
├── templates/
│   ├── base.html
│   └── ...
├── static/
├── locale/                      # ru, en
├── tests/                       # conftest, factories, тесты приложений
├── docker/
│   └── nginx/                   # конфиги для прода (позже)
└── .koda/
    ├── plans/
    └── backlog.md
```

Приложения лежат в пакете `apps/` (в `INSTALLED_APPS` — `apps.catalog` и т. д.), чтобы корень
репозитория не зарастал.

### Двуязычные файлы правил

- **`KODA.md` (русский)** — рабочий документ владельца проекта и сессий Koda CLI: правила, структура,
  стек, ссылки на планы, накопленная память проекта.
- **`AGENTS.md` (английский)** — каноническая англоязычная версия тех же правил для внешних
  контрибьюторов и других агентов (стандарт agents.md, поддерживается SourceCraft, Cursor, Copilot и др.).
- Оба файла содержат одинаковый набор правил; при изменении правил обновляются **оба**.
  В каждом файле — строка-напоминание об этом.

---

## 4. Модель данных

### 4.1 Абстрактные базовые модели (`apps/core/models.py`)

| Модель | Поля/поведение | Зачем |
|---|---|---|
| `TimeStampedModel` | `created_at`, `updated_at` | Единый аудит времени |
| `SlugModel` | `slug`, `get_absolute_url()` | Человекочитаемые URL, SEO |
| `StatusModel` | `status` (draft/published/hidden/rejected), `moderated_by`, `moderated_at`, `moderation_reason` | Публикация и модерация всех сущностей единообразно |
| `SoftDeleteModel` | `deleted_at`, `deleted_reason` | «Удаление» по требованию без потери связей и истории |
| `MergeableModel` | `merged_into` (FK self), `merge_into(target)` | Слияние дубликатов: перенос связей, пометка источника |

Все содержательные модели наследуют `TimeStampedModel, StatusModel, SoftDeleteModel` и подключают
`HistoricalRecords()`.

### 4.2 `Sport` (`apps/core`)

`name`, `slug` (unique), `sort_order`, `is_active`.
На старте одна запись — «Бейсбол». UI выбора вида спорта не показывает.

### 4.3 `Team` (`apps/catalog`)

| Поле | Тип | Обоснование |
|---|---|---|
| `sport` | FK → Sport (PROTECT) | Мультиспортность |
| `name` | CharField(200) | «Динамо», «Сборная России» |
| `slug` | SlugField | URL; `UniqueConstraint(sport, slug)` |
| `city` | CharField(blank) | В бейсболе город = часть идентичности команды |
| `founded_year` | SmallInteger(null) | Неполные данные — норма |
| `description` | TextField(blank) | История команды |
| `logo` | ImageField(null) | Хранится в MinIO |
| `created_by` | FK → User(null) | Кто завёл |
| `status` | StatusModel | Создаётся в `draft`, публикует модератор |
| `merged_into` | FK self(null) | Слияние дубликатов |

Связи: `Photo.team`, `TeamMembership`, `EventTeam`.

### 4.4 `TeamMembership` (`apps/catalog`) — человек ↔ команда

`team`, `person`, `year_from`, `year_to` (null), `role` (player/coach/manager/staff),
`number` (blank), `position` (blank, амплуа: питчер/кэтчер…), `note`.
Позволяет строить «состав команды по годам» — одна из главных ценностей архива.
Уникальность: `(team, person, role, year_from)`.

### 4.5 `Person` (`apps/catalog`)

| Поле | Тип | Обоснование |
|---|---|---|
| `first_name` | CharField(100) | Может быть единственным известным |
| `last_name` | CharField(100, blank) | Часто неизвестна — это норма |
| `middle_name` | CharField(100, blank) | |
| `display_name` | CharField(200) | Как показывать (вычисляется при сохранении) |
| `birth_year` | SmallInteger(null) | Год достаточно, дата — редкость |
| `death_year` | SmallInteger(null) | Мемориальные записи |
| `name_visibility` | choices: `full` / `first_name_only` / `hidden` | Приватность несовершеннолетних |
| `bio` | TextField(blank) | |
| `avatar` | FK → Photo(null, SET_NULL) | Главное фото |
| `is_claimed` | Boolean(default False) | «Это я» подтверждено модератором |
| `created_by` | FK → User(null) | |
| `merged_into` | FK self(null) | Слияние дубликатов |
| `slug` | SlugField(unique) | Глобальная уникальность, суффикс при коллизии |

`Person` **не привязан к виду спорта** — человек сквозной (один и тот же может быть в бейсболе и
крикете). Связь с фото — через `PhotoPerson`, с командой — через `TeamMembership`.

### 4.6 `PersonName` (`apps/catalog`) — варианты имён

`person`, `value` (CharField), `kind` (birth_name / married_name / nickname / alt_spelling).
Нужно для поиска («Смирнов»/«Смирнова»/«Смит»), для отображения прежних фамилий и как материал
для слияния дубликатов.

### 4.7 `Event` (`apps/catalog`)

| Поле | Тип | Обоснование |
|---|---|---|
| `sport` | FK → Sport (PROTECT) | |
| `name` | CharField(300) | «Первенство России 1994» |
| `slug` | SlugField | `UniqueConstraint(sport, slug)` |
| `event_type` | choices: tournament/series/friendly/training/trip/ceremony/other | Навигация и фильтры |
| `date_from` / `date_to` | DateField(null/blank) | Даты часто известны неточно |
| `year` | SmallInteger(db_index) | Денормализация ради навигации «по годам» |
| `city` / `country` | CharField(blank) | Турне по США, Европе |
| `attributes` | JSONB(default dict) | Счёт, этап, дивизион, результат — без колонок под каждый спорт |
| `description` | TextField(blank) | |
| `created_by`, `merged_into` | | |

Отдельной сущности `Season` нет: навигация «по годам» строится на `Event.year` и `Photo.year`.

### 4.8 `EventTeam` (`apps/catalog`) — участие команды в событии

`event`, `team`, `result_note` (blank, свободный текст: «1 место», «проиграли в финале»).
Сущность нужна как **связь** «какие команды участвовали в событии» (навигация «все турниры команды X»,
состав участников). Отдельного числового поля `place` нет — результат пишется текстом.

### 4.9 `Source` (`apps/photos`) — откуда фото родом

`kind` (paper_photo / negative / scan / digital / newspaper / magazine / program / ticket / other),
`title`, `publication` (blank), `date` (null), `page` (blank), `url` (blank),
`holder` (CharField blank — «семейный архив Ивановых»), `owner` FK → User(null), `notes`.
Отделено от `Photo`: одна газетная страница = несколько фото; одно фото = один источник.

### 4.10 `Photo` (`apps/photos`)

| Поле | Тип | Обоснование |
|---|---|---|
| `sport` | FK → Sport (PROTECT) | Обязателен: любое фото принадлежит виду спорта |
| `source` | FK → Source(null) | |
| `event` | FK → Event(null) | |
| `team` | FK → Team(null) | |
| `year` | SmallInteger(db_index, null) | Навигация «по годам», автозаполнение из события/EXIF |
| `taken_date` | DateField(null) | |
| `title` / `caption` | CharField(300) / TextField(blank) | Подпись — то, что делает архив архивом |
| `original` | ImageField | **Неизменяемый оригинал** |
| `processed` | ImageField(null) | Результат обработки (этап 3) |
| `display` | ImageField(null) | Что показывать (processed или original) |
| `width` / `height` | PositiveInteger(null) | |
| `phash` | CharField(64, db_index, blank) | Перцептивный хеш (этап 3) |
| `duplicate_of` | FK self(null) | Указатель на каноническое фото |
| `status` | StatusModel | Публикация |
| `processing_status` | choices: uploaded/processing/processed/failed | Отдельно от публикации |
| `processing_error` | TextField(blank) | |
| `rights_confirmed` | Boolean + `rights_confirmed_at` | Юридическая защита (кто и когда подтвердил права) |
| `exif` | JSONB(default dict) | Метаданные съёмки |
| `views_count` | PositiveInteger(default 0) | |
| `uploaded_by` | FK → User(PROTECT) | Кто оцифровал |

### 4.11 `PhotoPerson` (`apps/photos`) — люди на фото

`photo`, `person`, `role` (player/coach/umpire/spectator/photographer/other),
`position` (blank — «слева второй»), `note`, `added_by`, `added_at`.
Уникальность: `(photo, person)`. Роль важна: на фото есть тренеры, судьи, зрители, сам фотограф.

### 4.12 `Comment` (`apps/comments`)

`author` FK → User(PROTECT), `text`, `parent` FK self(null — ответы),
`content_type` + `object_id` (GenericForeignKey: фото, событие, команда, человек),
`status` (StatusModel), `created_at`, `updated_at`. `HistoricalRecords()`.

### 4.13 `Report` (`apps/moderation`) — жалоба

`reporter` FK → User(null — возможна анонимная жалоба), `content_type` + `object_id`,
`reason` (wrong_person / wrong_data / copyright / inappropriate / privacy / other),
`comment`, `status` (new/in_review/resolved/rejected), `resolution`, `resolved_by`, `resolved_at`.

### 4.14 `ModerationAction` (`apps/moderation`) — журнал решений

`moderator` FK → User, `action` (hide/publish/reject/merge/delete/restore),
`content_type` + `object_id`, `reason`, `created_at`.
Дополняет `simple-history`: история фиксирует изменения полей, журнал — решения модератора.

### 4.15 `User` (`apps/accounts`)

Кастомный (`AbstractBaseUser` + `PermissionsMixin`), `USERNAME_FIELD = "email"`:

| Поле | Обоснование |
|---|---|
| `email` (unique) | Единственный идентификатор входа |
| `display_name` | Как обращаться; не путать с `Person.display_name` |
| `person` | OneToOne → Person(null, SET_NULL) — «это я» |
| `role` | choices: user / moderator / admin |
| `terms_accepted_at`, `privacy_accepted_at` | Согласие с документами + дата |
| `consent_pd_at`, `consent_pd_version` | Согласие на обработку ПД (152-ФЗ) + версия документа |
| `email_verified` | Управляется allauth |
| `is_active`, `is_staff`, `created_at`, `last_login` | |

Менеджер `UserManager` с `create_user` / `create_superuser` по email.

---

## 5. Правила и инварианты (проверяются тестами)

1. `Team.slug` и `Event.slug` уникальны **в пределах своего `sport`**; `Person.slug` — глобально.
2. `merge_into(target)` переносит все связи (фото, состав, события, комментарии, жалобы), помечает
   источник `merged_into` и скрывает его из публичных выборок; повторный вызов идемпотентен.
3. `Photo.original` не изменяется никогда; производные пишутся в отдельные поля.
4. Фото с `rights_confirmed = False` не публикуется.
5. `Person` с `name_visibility = first_name_only` не отдаёт фамилию в публичный контекст
   (проверяется в `__str__`/шаблонах через метод `public_name`).
6. Soft-delete: объект с `deleted_at` исчезает из публичных выборок, но сохраняет связи.
7. Каждое изменение содержательной модели пишет запись в historical-таблицу.

---

## 6. Шаги

### 1. [x] **Каркас репозитория: uv, линтер, Makefile, env, файлы правил**

- Навыки: koda-coder
- Файлы: `pyproject.toml`, `uv.lock`, `.gitignore`, `.dockerignore`, `.env.example`,
  `Makefile`, `README.md`, `KODA.md`, `AGENTS.md`, `.koda/backlog.md`
- Содержание: зависимости через uv (Django 5.2, psycopg, django-environ, simple-history, storages,
  imagekit, Pillow, celery, redis, allauth, pytest, pytest-django, factory_boy, ruff), конфигурация
  ruff и pytest в `pyproject.toml`, README со стеком и запуском, Makefile (`up`, `down`, `migrate`,
  `run`, `test`, `lint`, `shell`), `KODA.md` (русский) и `AGENTS.md` (английский) с одинаковым
  набором правил проекта, ветка `feature/foundation`
- Проверка: `uv sync` проходит; `make lint` без ошибок на пустом проекте

### 2. [x] **Docker: Dockerfile и docker-compose (db, redis, minio, web, worker)**

- Навыки: koda-coder
- Файлы: `Dockerfile`, `docker-compose.yml`, `.env.example`
- Содержание: python:3.12-slim + системные зависимости для Pillow/OpenCV headless, сервисы
  postgres:17, redis, minio (volume), web (runserver в dev), worker (celery)
- Проверка: `make up` поднимает все сервисы, `docker compose ps` — все healthy; MinIO-консоль
  открывается, бакет создаётся

### 3. [x] **Django-проект: config, settings по env, приложения, i18n, шаблоны**

- Навыки: koda-coder
- Файлы: `manage.py`, `config/settings.py`, `config/urls.py`, `config/wsgi.py`, `config/asgi.py`,
  `config/celery.py`, `apps/*/__init__.py`, `templates/base.html`, `locale/`
- Содержание: единый settings с `django-environ` (DATABASE_URL, REDIS_URL, MINIO_*, SECRET_KEY,
  DEBUG, ALLOWED_HOSTS, EMAIL_*), `INSTALLED_APPS` (6 приложений + сторонние), `LocaleMiddleware`
  (ru/en), `LANGUAGES`, статика, хранилище по умолчанию — MinIO через `django-storages`,
  healthcheck-страница `/healthz/`
- Проверка: `make migrate` проходит, `/healthz/` отвечает 200, `/admin/` открывается

### 4. [x] **core: абстрактные модели + Sport**

- Навыки: koda-coder
- Файлы: `apps/core/models.py`, `apps/core/admin.py`, `apps/core/migrations/0001_*`,
  `apps/core/apps.py`
- Содержание: `TimeStampedModel`, `SlugModel`, `StatusModel`, `SoftDeleteModel`, `MergeableModel`,
  `Sport`; миграция; фикстура с «Бейсбол»
- Проверка: миграции применяются, `Sport.objects.get(slug="baseball")` работает в `make shell`

### 5. [x] **catalog: Team + TeamMembership**

- Навыки: koda-coder
- Файлы: `apps/catalog/models.py`, `apps/catalog/admin.py`, `apps/catalog/migrations/0001_*`
- Содержание: `Team` (уникальность slug в пределах спорта, стартовый статус `draft`),
  `TeamMembership`, `merge_into`, `HistoricalRecords`, `public_name`, `get_absolute_url`
- Проверка: тесты — уникальность slug внутри спорта, слияние переносит состав и фото, идемпотентность

### 6. [x] **catalog: Person + PersonName**

- Навыки: koda-coder
- Файлы: `apps/catalog/models.py`, `apps/catalog/admin.py`, `apps/catalog/migrations/0002_*`
- Содержание: `Person` (неполные данные, `name_visibility`, `is_claimed`, глобальный slug с
  суффиксом), `PersonName`, метод `public_name`, `merge_into` с переносом имён и связей
- Проверка: тесты — `public_name` при `first_name_only` не содержит фамилии; слияние переносит
  `PhotoPerson`, `TeamMembership`, `PersonName`

### 7. [x] **catalog: Event + EventTeam**

- Навыки: koda-coder
- Файлы: `apps/catalog/models.py`, `apps/catalog/admin.py`, `apps/catalog/migrations/0003_*`
- Содержание: `Event` (JSONB `attributes`, `year`, `event_type`), `EventTeam` (`result_note`),
  `merge_into`, автозаполнение `year` из `date_from`
- Проверка: тесты — `year` заполняется из `date_from`; слияние переносит фото и `EventTeam`;
  `attributes` валидируется как словарь

### 8. [x] **accounts: кастомный User, согласия, роли, связь с Person**

- Навыки: koda-coder
- Файлы: `apps/accounts/models.py`, `apps/accounts/managers.py`, `apps/accounts/admin.py`,
  `apps/accounts/migrations/0001_*`, `config/settings.py` (AUTH_USER_MODEL, allauth)
- Содержание: `User` по email, `UserManager`, поля согласий (terms/privacy/152-ФЗ + версия),
  `role`, OneToOne на `Person`, настройка `django-allauth` (email + пароль, **обязательное
  подтверждение email**, в dev — console email backend)
- Проверка: тесты — `create_user`/`create_superuser` по email; нельзя создать двух пользователей с
  одним email; регистрация через allauth создаёт `User` с заполненными согласиями и неподтверждённым
  email
- **Важно:** миграция accounts обязана быть **до** первой миграции с FK на User (catalog/photos)

### 9. [x] **photos: Source, Photo, PhotoPerson**

- Навыки: koda-coder
- Файлы: `apps/photos/models.py`, `apps/photos/admin.py`, `apps/photos/migrations/0001_*`
- Содержание: `Source`, `Photo` (все поля из п. 4.10, `processing_status`, `rights_confirmed`,
  `duplicate_of`, `phash`), `PhotoPerson` (роль, позиция в кадре), `ImageSpecField` превью,
  `get_absolute_url`
- Проверка: тесты — фото без `rights_confirmed` не публикуется; `duplicate_of` не может ссылаться
  на себя; превью генерируются из тестового изображения

### 10. [x] **comments + moderation: Comment, Report, ModerationAction**

- Навыки: koda-coder
- Файлы: `apps/comments/models.py`, `apps/comments/admin.py`, `apps/comments/migrations/0001_*`,
  `apps/moderation/models.py`, `apps/moderation/admin.py`, `apps/moderation/migrations/0001_*`
- Содержание: `Comment` (generic FK, ответы, статусы), `Report` (причины, статусы, решение),
  `ModerationAction` (журнал решений), связь жалобы с решением
- Проверка: тесты — комментарий можно привязать к фото и к событию; жалоба меняет статус и пишет
  `ModerationAction`

### 11. [x] **Админка: модерация и слияние дубликатов**

- Навыки: koda-coder
- Файлы: `apps/*/admin.py`, `apps/core/admin_mixins.py`, `templates/admin/*`
- Содержание: регистрация всех моделей, `SimpleHistoryAdmin` для истории и отката, действия
  «Слить в выбранный объект» для Team/Person/Event, действия «Скрыть/Опубликовать/Отклонить»,
  фильтры по статусу/спорту/году, поиск (`pg_trgm` для Person/Team), inline для `PhotoPerson`,
  `TeamMembership`, `EventTeam`
- Проверка: вручную — создать дубликат команды, слить, убедиться что связи перенесены; скрыть фото
  из админки и увидеть запись в `ModerationAction`

### 12. [x] **Тесты и CI**

- Навыки: koda-coder
- Файлы: `tests/conftest.py`, `tests/factories.py`, `tests/test_core.py`, `tests/test_catalog.py`,
  `tests/test_accounts.py`, `tests/test_photos.py`, `tests/test_moderation.py`,
  `.github/workflows/ci.yml`
- Содержание: фабрики factory_boy для всех моделей, тесты инвариантов из раздела 5, CI (ruff +
  pytest на Python 3.12 с сервисом postgres:17; в тестах файловое хранилище вместо MinIO)
- Проверка: `make test` зелёный локально; CI зелёный в GitHub Actions

### 13. [x] **Документация и правила проекта**

- Навыки: koda-coder
- Файлы: `README.md`, `KODA.md`, `AGENTS.md`, `.koda/backlog.md`,
  `.agents/skills/semver-checker/SKILL.md`, `pyproject.toml` (version 0.1.0)
- Содержание: README (что это, стек, структура, запуск, тесты), `KODA.md` (русский) и `AGENTS.md`
  (английский) с одинаковым набором правил: Person≠User, оригинал не перезаписывается, slug в
  пределах спорта, мультиспортная схема/одномспортный продукт, команда создаётся в `draft`, semver,
  где живёт версия, напоминание синхронизировать оба файла; backlog с этапами 2–4, скилл
  semver-checker (версия в `pyproject.toml`, `uv lock` после изменения)
- Проверка: документация соответствует фактической структуре (сверить `git ls-files`); содержимое
  `KODA.md` и `AGENTS.md` не расходится по смыслу

---

## 7. Дальнейшие этапы (отдельными планами)

- **Этап 2 — публичный архив:** layout и навигация (команды / годы / события), главная, страницы
  команды/события/человека/фото, метатеги и Open Graph, sitemap/robots, микроразметка, галереи на
  htmx («показать ещё»), поиск (`pg_trgm` + `tsvector`), комментарии и жалобы в UI, регистрация и
  вход в UI, создание команды пользователем (форма → `draft`).
- **Этап 3 — обработка фото:** загрузка с камеры, ручная коррекция кадра (Cropper.js), серверный
  пайплайн OpenCV (поиск границ → перспективное выпрямление → автоуровни/CLAHE → подавление блика),
  Celery-задачи и статусы обработки, перцептивный хеш и слияние дубликатов с подтверждением.
- **Этап 4 — мобильный, юридический контур и эксплуатация:** PWA (манифест, service worker, offline,
  установка), политика и соглашение, страница удаления данных, водяные знаки на превью, лог
  скачиваний, настройка Yandex Cloud Postbox (DKIM/SPF/DMARC, 2 000 писем/мес бесплатно), деплой на
  VPS, бэкапы в независимое российское хранилище, мониторинг и алерты.

---

## 8. Открытые вопросы (не блокируют этап 1)

Решённые вопросы (перенесены из предыдущей итерации плана):

| Вопрос | Решение |
|---|---|
| Почтовый провайдер | Yandex Cloud Postbox (2 000 писем/мес бесплатно, DKIM/SPF/DMARC, РФ) |
| Подтверждение email | Включаем сразу; в dev — console backend, SMTP настраивается на этапе 4 |
| Создание команды | Пользователь создаёт в `draft`, публикует модератор |
| `Season` | Не нужен: навигация «по годам» через `Event.year` и `Photo.year` |
| `EventTeam.place` | Не нужен: результат пишется текстом в `result_note` |

Остаются открытыми (уточнить на этапе 2 или при реализации):

1. **Поведение фото, привязанных к команде в `draft`** (появляется только вместе с публичным UI):
   публиковать фото сразу, показывая название команды без ссылки на её страницу, или держать фото
   неопубликованным до одобрения команды? Предложение: публиковать фото, ссылку на страницу команды
   отдавать только для `published`.
2. **Почтовый ящик для приёма писем** (`info@sportmemo.ru`, обращения и жалобы). Postbox умеет только
   отправку. Варианты: Яндекс 360 для бизнеса (платно) или переадресация на личный ящик у
   регистратора. Не блокирует: в UI достаточно формы обратной связи, пишущей в БД.
3. **Где размещать вторую копию архива** (другой российский провайдер/город) — решается на этапе 4.