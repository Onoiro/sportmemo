# SportMemo

Публичный архив спортивных фотографий. На старте — бейсбол в России: детские, юношеские и
ветеранские команды, личные бумажные архивы игроков, тренеров и болельщиков.

Пользователи оцифровывают свои архивы (бумажные фото, сканы, газетные вырезки) и публикуют их
в общий архив с атрибуцией: кто на фото, команда, событие, год, источник.

- Домен: [sportmemo.ru](https://sportmemo.ru)
- Репозиторий: [github.com/Onoiro/sportmemo](https://github.com/Onoiro/sportmemo)

## Состояние

**Этап 1 (фундамент) завершён:** схема данных, админка с модерацией и слиянием дубликатов,
тесты, CI, инфраструктура разработки. Публичного UI пока нет — это этап 2.

Версия: 0.1.1

## Стек

| Слой | Технология |
|---|---|
| Язык | Python 3.12 (проверяется также на 3.13) |
| Фреймворк | Django 5.2 LTS |
| Шаблоны / UI | Django templates + Bootstrap 5, htmx, Alpine.js |
| БД | PostgreSQL 17 |
| Файлы | MinIO (S3 API) через `django-storages` |
| Превью | `django-imagekit` |
| Аудит | `django-simple-history` |
| Аутентификация | `django-allauth` (email + пароль, обязательное подтверждение email) |
| Фоновые задачи | Celery + Redis |
| Пакеты | `uv` |
| Линтер | `ruff` |
| Тесты | `pytest` + `pytest-django` + `factory_boy` |
| Инфраструктура | Docker Compose, Nginx |

## Структура

```
sportmemo/
├── config/                  # settings, urls, wsgi/asgi, celery
├── apps/
│   ├── core/                # абстрактные модели, Sport, миксины админки, healthcheck
│   ├── accounts/            # User (email), согласия, роли, форма регистрации
│   ├── catalog/             # Team, TeamMembership, Person, PersonName, Event, EventTeam
│   ├── photos/              # Source, Photo, PhotoPerson
│   ├── comments/            # Comment (generic FK, ответы)
│   └── moderation/          # Report, ModerationAction
├── templates/               # base.html, шаблоны админки
├── static/                  # статика проекта
├── locale/                  # переводы (ru, en)
├── tests/                   # conftest, фабрики, тесты по приложениям
├── .agents/skills/          # навыки агентов (semver-checker)
├── .github/workflows/       # CI
└── .koda/                   # планы и backlog проекта
```

## Модель данных

Ключевые решения (подробности — в [плане этапа 1](.koda/plans/foundation-and-data-model.md)):

- **Архитектура мультиспортная, продукт одномспортный.** В схеме есть `Sport`, в UI выбора
  вида спорта нет. Специфика вида спорта — в `attributes` (JSONB), а не в колонках.
- **`Person` ≠ `User`.** Человек на фото — публичная сущность без аккаунта. Пользователь —
  приватный аккаунт, опционально связанный с `Person` («это я»).
- **Оригинал фото не перезаписывается.** Обработанная версия — производная в отдельном поле.
- **Дубликаты сливаются, а не удаляются.** `merged_into` + `merge_into(target)`, операция
  идемпотентна, связи переносятся.
- **Slug уникален в пределах вида спорта** для `Team`/`Event`; у `Person` — глобально, с
  числовым суффиксом при коллизии.
- **Фамилии несовершеннолетних не публикуются** — управляется `Person.name_visibility`,
  публичный контекст использует `Person.public_name`.
- **Soft-delete:** объект с `deleted_at` исчезает из публичных выборок (`Model.public`),
  но сохраняет связи и историю.
- **Всё значимое логируется:** `django-simple-history` на содержательных моделях плюс журнал
  решений модератора (`ModerationAction`).

## Запуск

### Локально (без Docker)

Нужны Python 3.12+ и PostgreSQL 17 (или поднимите только БД через Docker).

```bash
uv sync                       # установить зависимости
cp .env.example .env          # настроить окружение
make migrate                  # применить миграции (создаст вид спорта «Бейсбол»)
make superuser                # создать администратора
uv run python manage.py runserver
```

- Сайт: http://localhost:8000
- Админка: http://localhost:8000/admin/
- Проверка работоспособности: http://localhost:8000/healthz/ → `{"app": "ok", "db": "ok"}`

### Docker Compose

```bash
cp .env.example .env
make up                       # db, redis, minio, web, worker
make ps                       # статус сервисов
make logs                     # логи
```

Консоль MinIO: http://localhost:9001 (`minioadmin` / `minioadmin` в dev).

> **Внимание.** MinIO закрыл публичную раздачу образов (сентябрь 2026), поэтому в
> `docker-compose.yml` используется переупаковка `elestio/minio` с зафиксированным digest.
> Перед деплоем нужно выбрать целевое решение — см. риск R-1 в [бэклоге](.koda/backlog.md).

## Команды

```bash
make help          # список всех команд
make lint          # проверка кода и форматирования (ruff)
make format        # форматирование (ruff format)
make test          # тесты (pytest)
make test-cov      # тесты с покрытием
make migrate       # применить миграции
make migrations    # создать миграции
make shell         # Django shell
make up / down     # Docker Compose
make i18n          # извлечь и скомпилировать переводы
```

## Тесты

```bash
make test
```

117 тестов: инварианты схемы (уникальность slug, слияние дубликатов, `public_name`,
soft-delete), пользователи и согласия, фотографии (права, дубликаты, превью), комментарии
и модерация, админка (модераторские действия и слияние через UI).

Тесты используют PostgreSQL (в CI — сервис `postgres:17`), файловое хранилище вместо MinIO
и временный каталог для медиа.

## Разработка

Стиль кода задаёт `ruff` (конфигурация в `pyproject.toml`): **88 символов** для кода и **79** для
комментариев и docstrings — как в Django, с соблюдением PEP 8 для документации. `make lint`
проверяет и правила, и форматирование; `make format` приводит код к стилю.

Перед коммитом: `make lint && make test`. Перед коммитом также применяется навык
[semver-checker](.agents/skills/semver-checker/SKILL.md) — версия живёт в `pyproject.toml`,
после её изменения нужно выполнить `uv lock`.

Сообщения коммитов пишутся **на простом английском**, понятном не носителю языка: короткие
предложения, простые слова, без идиом. Тело коммита объясняет, **почему** сделано изменение.
Подробнее — в [KODA.md](KODA.md) (раздел «Язык общения»).

## Документация

- [`KODA.md`](KODA.md) — правила проекта (русский), для сессий Koda CLI
- [`AGENTS.md`](AGENTS.md) — те же правила (английский), для внешних агентов и контрибьюторов
- [`.koda/plans/`](.koda/plans/) — планы этапов
- [`.koda/backlog.md`](.koda/backlog.md) — бэклог задач и инфраструктурные риски

## Лицензия

Все права защищены. Материалы архива принадлежат их правообладателям.
