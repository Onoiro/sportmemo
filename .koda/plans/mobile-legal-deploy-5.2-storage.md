# SportMemo — подэтап 5.2: хранилище

Дата создания: 2026-11-15
Статус: **утверждён** (владелец, 2026-11-15)
Мастер-план: [`mobile-legal-deploy.md`](mobile-legal-deploy.md) — подэтап **5.2**,
второй в этапе 5
Бэклог: [`.koda/backlog.md`](../backlog.md) — раздел «Этап 5», задача `P5-13`
Ветка: `feature/s3-storage` (создаётся на шаге 1 от `master`)
База: `master`, версия 0.33.0, 1785 тестов
Финал: bump **0.33.0 → 0.34.0 (minor)** + `uv lock`, строка
`Bump version: 0.33.0 → 0.34.0 (minor)` в теле коммита

> **Для новой сессии.** Подэтапы 5.0 и 5.1 в `master`. Работа начинается с шага 1.
> Шаги идут строго по порядку, следующий начинается только после зелёной проверки
> текущего. Файл плана поддерживается в актуальном состоянии: `[ ]` → `[x]`.
> Отмечать шаг выполненным только после `grep`-проверки, что правка реально есть
> в целевых файлах, и после зелёного прогона тестов затронутых файлов.
>
> **Секреты в план и в git не попадают.** Ключи S3 живут только в `.env`
> (он в `.gitignore`). В плане, `.env.example`, README и сообщении коммита —
> только имена переменных и объяснение, откуда значения берутся.

---

## 1. Цель и границы

**Цель.** Превью имеют стабильный адрес и кешируются, а оригинал не отдаётся
никому, кроме пайплайна обработки.

Сейчас в проекте одно хранилище на всё (`STORAGES["default"]`) и
`AWS_QUERYSTRING_AUTH=True`. Из этого три дефекта:

- `og:image` берётся из `photo.preview.url`, а адрес подписанный — он меняется
  при каждом показе, поэтому карточка в мессенджере ломается, а кеш браузера
  и CDN невозможны;
- оригинал лежит в том же бакете, что и превью, и отличается от них только
  отсутствием ссылки в разметке: политика бакета одна на всё;
- `CACHES` не задан вовсе → LocMemCache: свой у каждого gunicorn-воркера
  и пустой после рестарта, а `imagekit` со стратегией `JustInTime` делает
  `storage.exists()` (HEAD в S3) при каждом показе превью до первого попадания
  в кеш воркера — и снова после каждого деплоя.

**Что входит в подэтап:**

- два хранилища: `originals` (приватное, подписанные ссылки) и `default`
  (публичное чтение);
- `Photo.original` и `Photo.processed` переводятся в `originals`;
  `display`, `thumbnail`, `preview` и `Team.logo` остаются в `default`;
- `CACHES` на Redis при `DEBUG=False`, LocMemCache в dev;
- стратегия `Optimistic` в `IMAGEKIT_DEFAULT_CACHEFILE_STRATEGY`;
- команда `photos_previews` — пересборка превью (принятый риск `Optimistic`);
- переименование переменных окружения `MINIO_*` → `S3_*`;
- документация: политики бакетов, создание бакетов в MinIO, го́тчи;
- тесты маршрутизации хранилищ и команды пересборки.

**Что не входит:**

| Отложено | Куда / почему |
|---|---|
| Конкретный провайдер S3 и его бакеты | `P5-13`, делается при деплое (5.7): код от провайдера не зависит |
| CDN перед публичным бакетом | не нужен, пока архив небольшой |
| Сборка мусора (удаление старых `display` и превью) | `P3-11` |
| Устойчивый кеш при недоступном Redis | новый `P5-15` (P2), заводится в шаге 8 |
| WebP и `srcset` | 5.9 |
| `AWS_S3_CUSTOM_DOMAIN` | не нужен: адрес публичного бакета и так стабилен |

---

## 2. Решения

### 2.1 Два бакета, а не префиксы с разными политиками

| Что | Хранилище | Доступ | Почему |
|---|---|---|---|
| `original` | `originals` | приватный, подписанные ссылки | EXIF и GPS, отдаётся только пайплайну |
| `processed` | `originals` | приватный | производная оригинала, публично не отдаётся |
| `display` | `default` | публичное чтение | источник превью, EXIF в нём уже вырезан |
| `thumbnail`, `preview` | `default` | публичное чтение | стабильный адрес для `og:image` и кеша |
| логотипы команд | `default` | публичное чтение | публичная картинка, EXIF не хранится |

**Решение владельца 2026-11-15:** `processed` идёт в приватное хранилище.
Поле сейчас нигде не используется, но это производная оригинала, и класть
её в публичный бакет «на всякий случай» значит заранее создать утечку.

**Почему два бакета, а не префиксы.** Политика S3 задаётся на бакет
(`bucket policy`), а не на префикс; префикс с `Deny` пришлось бы описывать
условием на ключ, и одна ошибка в условии открыла бы оригиналы. Два бакета —
это две независимые политики, и приватность видна в консоли провайдера
одним взглядом.

### 2.2 `storage=` получает callable, а не экземпляр

`FileField.__init__` принимает `storage` как callable, вызывает его и хранит
ссылку на саму функцию в `_storage_callable`; `deconstruct()` отдаёт в миграцию
именно её. Экземпляр `S3Storage` миграция сериализовать не сможет — она упадёт
на `ValueError: Cannot serialize`.

Поэтому в `apps/core/storage.py` живёт функция:

```python
def originals_storage():
    """Приватное хранилище оригиналов.

    Callable, а не экземпляр: `FileField.deconstruct()` кладёт в миграцию
    ссылку на функцию, а экземпляр хранилища сериализовать нельзя.
    """
    return storages["originals"]
```

Проверено чтением `django/db/models/fields/files.py:250-312` и
`django/db/migrations/serializer.py:173` (`FunctionTypeSerializer`).

### 2.3 `ImageSpecField` ничего указывать не нужно

`ImageCacheFile` берёт хранилище из `imagekit.utils.get_storage()`, а та
возвращает `storages["default"]`. Значит `thumbnail` и `preview` попадут
в публичный бакет автоматически, как только `display` окажется в `default`.
Явный `cachefile_storage` не ставим: два места, задающих одно правило,
разойдутся.

### 2.4 Правило подписи живёт в настройках хранилищ, а не в переменной

`AWS_QUERYSTRING_AUTH` (глобальная настройка) **удаляется**. Вместо неё
`OPTIONS` каждого алиаса:

- `default`: `querystring_auth=False` — адрес не меняется;
- `originals`: `querystring_auth=True`, `querystring_expire=3600`.

Переменная окружения `S3_QUERYSTRING_AUTH` не заводится: перепутанное значение
сделало бы превью подписанными (и вернуло бы дефект `og:image`), а оригиналы
публичными. Правило фиксировано в коде, потому что оно и есть решение подэтапа.

### 2.5 Переименование `MINIO_*` → `S3_*`

**Решение владельца 2026-11-15.** В бою провайдер — Yandex Object Storage,
а имена `MINIO_*` вводили в заблуждение.

| Было | Стало |
|---|---|
| `MINIO_ENDPOINT` | `S3_ENDPOINT` |
| `MINIO_ACCESS_KEY` | `S3_ACCESS_KEY` |
| `MINIO_SECRET_KEY` | `S3_SECRET_KEY` |
| `MINIO_BUCKET` | `S3_BUCKET` (публичный) |
| — | `S3_PRIVATE_BUCKET` (новое, приватный) |
| `MINIO_USE_SSL` | `S3_USE_SSL` |
| `MINIO_REGION` | `S3_REGION` |
| `MINIO_QUERYSTRING_AUTH` | — (удалено, см. 2.4) |

**Переменные самого MinIO не переименовываются.** В `docker-compose.yml`
`MINIO_ROOT_USER` и `MINIO_ROOT_PASSWORD` — это переменные образа, а не наши
настройки; меняется только источник значения (`${S3_ACCESS_KEY:-minioadmin}`).

`AWS_*` в `config/settings.py` остаются: это имена, которые читает
`django-storages`, и переименовывать их нельзя. Изменение касается только
имён переменных окружения.

### 2.6 Кеш: Redis в бою, LocMem в dev

```python
CACHE_URL = env("CACHE_URL", default="redis://localhost:6379/1")
if DEBUG:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": CACHE_URL,
            "KEY_PREFIX": "sportmemo",
            "TIMEOUT": None,
            "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
        }
    }
```

Обоснования:

- **`/1`, а не `/0`** — база `0` занята брокером Celery
  (`CELERY_BROKER_URL`). Смешивать их значит получить `clear()` кеша,
  стирающий очередь задач.
- **`TIMEOUT=None`** — состояние превью не должно истекать: иначе `imagekit`
  снова начнёт проверять существование файла в S3. Это ровно то, от чего
  избавляемся.
- **LocMem в dev** — `make run` не должен требовать Redis: сейчас в dev
  задачи Celery идут синхронно, и Redis не обязателен.
- **`socket_connect_timeout=2`** — недоступный Redis должен падать быстро,
  а не держать воркер gunicorn.

`IMAGEKIT_DEFAULT_CACHEFILE_STRATEGY = "imagekit.cachefiles.strategies.Optimistic"`.

### 2.7 Что именно делает `Optimistic` и чего он не делает

Проверено чтением `imagekit/cachefiles/strategies.py` и `cachefiles/__init__.py`:

- превью генерируются в `on_source_saved`, то есть по сигналу `post_save`
  при изменении поля-источника (`display`);
- `should_verify_existence()` возвращает `False`, поэтому `bool(preview)`
  и `preview.url` **не читают кеш** и не делают HEAD в S3;
- кеш участвует только в записи состояния во время генерации.

**Следствия, которые надо принять:**

1. **Потерянный файл превью не восстановится сам** — страница покажет битую
   картинку. Лечится командой `photos_previews` (шаг 5).
2. **Ошибка генерации превью всплывает в `photo.save()`.** В `process_photo()`
   это превратится в `failed` через общий `except`, и фото переобработается
   повторной задачей. Это лучше молчаливой потери превью, но знать надо.
3. **Недоступный Redis ломает сохранение фото**, потому что `get_state()`
   бросит исключение. Это принятый риск (решение владельца 2026-11-15):
   задача на устойчивый кеш заводится в бэклоге (`P5-15`), код подэтапа
   её не решает.

---

## 3. Факты и дефекты, проверенные при планировании

Каждый пункт подтверждён кодом или чтением библиотеки, а не предположен.

1. **`AWS_QUERYSTRING_AUTH=True` и `og:image` из превью.** `config/settings.py`
   ставит глобальную подпись, `apps/photos/views.py:543` берёт `photo.preview`
   в `page_meta`. Адрес меняется при каждом показе.
2. **`FileField` принимает callable storage** и сохраняет её для
   `deconstruct()` (`django/db/models/fields/files.py:250-312`).
3. **`S3Storage` читает `querystring_auth` из `OPTIONS`** алиаса
   (`storages/backends/s3.py:412`), свой подкласс не нужен.
4. **`ImageSpecField` берёт хранилище из `storages["default"]`**
   (`imagekit/utils.py:get_storage`). Явно указывать не надо.
5. **`Optimistic` не имеет `on_existence_required`** — значит генерации
   по требованию не будет, только по `post_save`.
6. **`django.core.cache.backends.redis.RedisCache` в Django 5.2 не поддерживает
   `IGNORE_EXCEPTIONS`** (проверено `grep` по `django/core/cache/backends/redis.py`).
   Поэтому падение Redis видно вызывающему — см. риск 2.7.3.
7. **`ImageSpecField` без `display` даёт `MissingSource` при `.url`**
   (проверено запуском). `bool()` возвращает `False`, и `image_meta()`
   ловит `(OSError, ValueError)`: `MissingSource` наследует `ValueError`
   (`imagekit/exceptions.py`), поэтому страница без `display` не падает —
   это уже покрыто тестом `test_photo_without_display_shows_placeholder`.
8. **В dev `static(MEDIA_URL, document_root=MEDIA_ROOT)` отдаёт весь
   `MEDIA_ROOT`** (`config/urls.py`). Значит приватность оригинала в dev
   не обеспечивается — только в бою, политикой бакета. Это записывается
   в README явно, чтобы никто не считал dev-режим защитой.
9. **`seed_demo --flush` удаляет файлы через `photo.original.storage`**
   (`apps/core/management/commands/seed_demo.py:1052`) — после смены
   хранилища продолжит работать без правок.
10. **`crop_overlay` в админке показывает `obj.original.url`**
    (`apps/photos/admin.py:328`). После перевода в приватное хранилище
    это будет подписанная ссылка на час: картинка работает, но после
    истечения срока её надо перезагрузить. Для админки приемлемо.
11. **Штатная команда `generateimages` не пересобирает существующие превью**:
    `image_file.generate()` вызывается без `force`
    (`imagekit/management/commands/generateimages.py`). Поэтому нужна своя
    команда, а не обёртка над штатной.
12. **`MINIO_*` встречается в 4 файлах** (`config/settings.py`,
    `docker-compose.yml`, `.env.example`, `README.md`) плюс исторический
    план этапа 1 — его не правим, это архив.

---

## 4. Предусловия (делает владелец, не шаг плана)

Коммита здесь нет, скрипт выполнения плана его не увидит.

- [ ] **`.env` обновить: `MINIO_*` → `S3_*`.** При `USE_S3=False` (текущее
      значение в dev) переменные не читаются, и ничего не сломается, но после
      переименования старые имена станут мёртвыми. Пока в `.env` не появится
      `S3_PRIVATE_BUCKET`, включение `USE_S3=True` даст `ImproperlyConfigured`.
- [ ] **Два бакета в MinIO** (для проверки в Docker): публичный `sportmemo`
      и приватный `sportmemo-private`. Политика публичного — анонимное чтение,
      приватного — нет. Команды `mc` записываются в README на шаге 7.
- [ ] **Для боя — бакеты Yandex Object Storage** (при деплое, 5.7):
      публичный с `bucket policy` на чтение, приватный без анонимного доступа.

---

## 5. Шаги

### 1. [x] Ветка

1. `git status` — дерево чистое (допускается только файл этого плана, он
   коммитится до шага 1).
2. `git switch master`.
3. `git switch -c feature/s3-storage`.
4. Проверка: `git branch --show-current` → `feature/s3-storage`.

Файлы: —

### 2. [x] Настройки: два хранилища и переименование `S3_*`

1. `config/settings.py`:
   - в объявлении `environ.Env(...)` заменить `MINIO_USE_SSL` на `S3_USE_SSL`;
   - блок `--- Файлы ---` переписать: `STORAGES` объявляется сразу с тремя
     алиасами (`default`, `originals`, `staticfiles`), оба файловых —
     `FileSystemStorage`; при `USE_S3` подменяются на `S3Storage`
     с `OPTIONS` из решения 2.1 и 2.4;
   - удалить глобальные `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
     `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_ENDPOINT_URL`, `AWS_S3_USE_SSL`,
     `AWS_S3_REGION_NAME`, `AWS_S3_FILE_OVERWRITE`, `AWS_DEFAULT_ACL`,
     `AWS_QUERYSTRING_AUTH`, `AWS_S3_OBJECT_PARAMETERS` — всё уходит
     в `OPTIONS` алиасов;
   - `MEDIA_URL`/`MEDIA_ROOT` остаются только для ветки `USE_S3=False`.
2. `apps/core/storage.py` — новый модуль с `originals_storage()` (решение 2.2).
   Docstring объясняет, почему callable.
3. Проверка: `grep -n "MINIO_" config/settings.py` → пусто;
   `grep -n "originals" config/settings.py` → алиас на месте;
   `uv run python manage.py check` → без ошибок.

**Го́тча.** `S3Storage` в `OPTIONS` принимает только имена из
`get_default_settings()` (`storages/backends/s3.py:383-440`); незнакомый ключ
даёт `ImproperlyConfigured: Invalid setting`. Значит `access_key`, `secret_key`,
`bucket_name`, `endpoint_url`, `region_name`, `use_ssl`, `file_overwrite`,
`default_acl`, `querystring_auth`, `querystring_expire`, `object_parameters` —
можно, `AWS_...` в `OPTIONS` — нельзя.

Файлы: `config/settings.py`, `apps/core/storage.py`

### 3. [x] Поля модели: оригиналы в приватное хранилище

1. `apps/photos/models.py`: `original` и `processed` получают
   `storage=originals_storage`; `display` не трогается (остаётся в `default`).
   Импорт — `from apps.core.storage import originals_storage`.
2. `uv run python manage.py makemigrations photos` → одна миграция `AlterField`
   на два поля. Проверить, что в ней `storage=apps.core.storage.originals_storage`,
   а не сериализованный экземпляр.
3. `uv run python manage.py makemigrations --check --dry-run` → «No changes».
4. Проверка в шелле:
   `Photo().original.storage is storages["originals"]` → `True`;
   `Photo().display.storage is storages["default"]` → `True`;
   `Photo().thumbnail.storage is storages["default"]` → `True`.
5. `Team.logo` не трогаем: публичная картинка. Проверка в шелле:
   `Team().logo.storage is storages["default"]` → `True`.

**Почему `display` остаётся публичным.** `thumbnail` и `preview` строятся
из него и живут в `default`; положить источник в другой бакет значит получить
кросс-бакетное чтение в `imagekit`, которое никто не настраивал. EXIF в
`display` уже вырезан (`encode_display()` не передаёт `exif=`), GPS там нет.

Файлы: `apps/photos/models.py`, `apps/photos/migrations/00XX_*.py`

### 4. [x] Кеш на Redis и стратегия `Optimistic`

1. `config/settings.py`: блок `CACHES` по решению 2.6, рядом комментарии
   про `/1` и `TIMEOUT=None`.
2. `IMAGEKIT_DEFAULT_CACHEFILE_STRATEGY = "imagekit.cachefiles.strategies.Optimistic"`.
3. `uv run python manage.py check` → без ошибок.
4. Проверка: `uv run python -c "from django.conf import settings; print(settings.IMAGEKIT_DEFAULT_CACHEFILE_STRATEGY)"`
   → `imagekit.cachefiles.strategies.Optimistic`.
5. Прогнать `uv run pytest tests/test_photos.py -q` — превью должны
   генерироваться в тестах (кеш в тестах LocMem, `DEBUG` в CI `True`).

**Го́тча.** В тестах `DEBUG=True` из CI, поэтому кеш там LocMem. Тест,
проверяющий Redis-настройки, обязан подменять `DEBUG` через
`settings`-фикстуру и смотреть на `settings.CACHES`, а не на живое соединение:
Redis в CI нет.

Файлы: `config/settings.py`

### 5. [x] Команда `photos_previews`

1. `apps/photos/management/commands/photos_previews.py`:
   - по умолчанию пересобирает **только отсутствующие** превью
     (`image.name` пуст или `image.storage.exists(image.name)` → `False`);
   - `--all` — пересобрать всё с `generate(force=True)`;
   - `--limit N`, `--dry-run` — как в `photos_backfill`;
   - фото без `display` пропускается с подписью;
   - ошибка одного фото не прерывает проход, печатается предупреждением;
   - итог: «пересобрано: N, пропущено: M, ошибок: K».
2. `Makefile`: цель `photos-previews` рядом с `photos-backfill`.
3. Проверка вручную на dev-базе: удалить файл превью из `media/CACHE`
   и запустить `uv run python manage.py photos_previews` → файл вернулся.

**Почему своя команда.** Штатная `generateimages` вызывает `generate()`
без `force` (факт 3.11), то есть на существующем состоянии ничего не делает.
Вторая реализация отбора при этом не появляется: команда переиспользует
`ImageSpecField` модели.

Файлы: `apps/photos/management/commands/photos_previews.py`, `Makefile`

### 6. [x] Тесты

1. `tests/test_photo_storage.py` — новый файл:
   - `Photo.original` и `Photo.processed` → `storages["originals"]`;
   - `Photo.display`, `thumbnail`, `preview`, `Team.logo` → `storages["default"]`;
   - `settings.STORAGES["originals"]` содержит `querystring_auth: True`
     при `USE_S3=True` (через `override_settings` с явными `OPTIONS`,
     без сети и без boto);
   - `settings.STORAGES["default"]` содержит `querystring_auth: False`;
   - публичный адрес превью не содержит `X-Amz-Signature` — проверяется
     на `FileSystemStorage` (в dev подписи нет вовсе) плюс отдельный тест
     на `S3Storage.url()` с `querystring_auth=False` и фиктивным клиентом
     не заводим: слишком дорого и хрупко. Достаточно проверки `OPTIONS`
     и теста `og:image`, что в разметке нет `X-Amz`;
   - `CACHES` при `DEBUG=False` → `RedisCache` и `LOCATION` оканчивается
     на `/1`; при `DEBUG=True` → `LocMemCache`;
   - `IMAGEKIT_DEFAULT_CACHEFILE_STRATEGY` — `Optimistic`.
2. `tests/test_photo_previews.py` — новый файл:
   - команда возвращает удалённое превью;
   - `--all` пересобирает существующее (меняется `mtime` или размер);
   - `--dry-run` ничего не создаёт;
   - фото без `display` пропускается без ошибки;
   - `--limit` ограничивает число обработанных.
3. Проверить, что существующие тесты не поломались:
   `uv run pytest tests/test_photos.py tests/test_photo_gallery.py tests/test_seo.py tests/test_meta_tags.py -q`.

**Го́тча.** `tests/conftest.py` уже чистит кеш между тестами (`cleared_cache`).
С `Optimistic` это по-прежнему нужно: состояние превью живёт в кеше, и без
очистки тест увидел бы файл из предыдущего теста.

Файлы: `tests/test_photo_storage.py`, `tests/test_photo_previews.py`

### 7. [x] Окружение и compose

1. `.env.example`: блок MinIO переписать на `S3_*`, добавить
   `S3_PRIVATE_BUCKET=sportmemo-private`, `CACHE_URL=redis://localhost:6379/1`.
   Комментарий: в бою это Yandex Object Storage, имена `S3_*` — наши,
   `AWS_*` внутри `django-storages` не переименовываются.
2. `docker-compose.yml`:
   - сервис `minio`: `MINIO_ROOT_USER: ${S3_ACCESS_KEY:-minioadmin}`,
     `MINIO_ROOT_PASSWORD: ${S3_SECRET_KEY:-minioadmin}`;
   - `web` и `worker`: `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`,
     `S3_BUCKET`, `S3_PRIVATE_BUCKET`, `S3_USE_SSL: "False"`, `CACHE_URL`.
3. Проверка: `grep -n "MINIO_" docker-compose.yml` → только
   `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` (переменные образа);
   `docker compose config` → без ошибок подстановки.

**Го́тча.** `docker-compose.yml` включает S3 по умолчанию
(`USE_S3: ${USE_S3:-True}`). Если переименовать переменные в `settings.py`,
но забыть compose, контейнер `web` упадёт на первом же обращении к файлу.

Файлы: `.env.example`, `docker-compose.yml`

### 8. [x] Документация и бэклог

1. `README.md`, раздел «Хранилище» (рядом с блоком MinIO в «Docker Compose»):
   - таблица «что в каком бакете» из решения 2.1;
   - создание бакетов в MinIO и политики:
     `mc mb local/sportmemo local/sportmemo-private`,
     `mc anonymous set download local/sportmemo`;
   - для боя: публичный бакет — `bucket policy` на чтение, приватный — без
     анонимного доступа;
   - **честная строка:** в dev оба алиаса смотрят в `MEDIA_ROOT`, оригинал
     лежит в `media/photos/original/` и отдаётся dev-сервером; приватность
     обеспечивает только политика бакета в бою;
   - правило: оригинал не попадает ни в разметку, ни в `og:image`, ни в JSON-LD;
   - команда `photos_previews` и когда её запускать.
2. `KODA.md` / `AGENTS.md`, раздел «Правила для кода» — три правила:
   - «`Photo.original` и `Photo.processed` живут в `storages["originals"]`,
     остальные файлы — в `default`; правило одно на модель, второе хранилище
     в представлениях не выбирается»;
   - «подпись ссылки задаётся `OPTIONS` алиаса, а не глобальной настройкой:
     публичное — без подписи, приватное — с подписью»;
   - «превью строятся при сохранении `display` (`Optimistic`), а не HEAD-запросом
     на каждом показе; потерянное превью восстанавливает `photos_previews`».
3. `.koda/backlog.md`:
   - `P5-13` — статус «сделано в 5.2» с оговоркой, что бакеты провайдера
     заводятся при деплое;
   - новая `P5-15` (P2): устойчивый кеш при недоступном Redis — `django-redis`
     с `IGNORE_EXCEPTIONS` или локальный fallback. Причина: `RedisCache`
     в Django 5.2 не умеет глотать ошибки, и падение Redis ломает сохранение
     фото. Заводится по решению владельца 2026-11-15.
4. Проверка: `grep -n "P5-15" .koda/backlog.md`; `grep -n "originals" README.md`.

Файлы: `README.md`, `KODA.md`, `AGENTS.md`, `.koda/backlog.md`

### 9. [x] Проверка и коммит

1. `make lint` — чисто.
2. `make test` — все тесты зелёные (было 1785, станет больше).
3. Активировать навык `semver-checker`: bump **0.33.0 → 0.34.0 (minor)**,
   `uv lock`.
4. Ручная проверка в dev:
   - `make seed-demo`, открыть страницу фото → превью на месте;
   - `grep -r "photos/original" media/CACHE` — пусто (превью не от оригинала);
   - HTML страницы фото не содержит `photos/original/`;
   - повторный показ не создаёт новых файлов в `media/CACHE`
     (`find media/CACHE -newer` до/после);
   - `manage.py photos_previews --dry-run` показывает 0 к пересборке.
5. Коммит: `feat: split storage into private originals and public previews`
   + тело с причиной (подписанные ссылки ломали `og:image`, политика бакета
   была одна на всё) + строка версии.
6. Отметить 5.2 в `.koda/plans/mobile-legal-deploy.md` и в таблице
   `.koda/backlog.md`; записать итог подэтапа.

Файлы: `pyproject.toml`, `uv.lock`, `.koda/plans/mobile-legal-deploy.md`,
`.koda/backlog.md`

---

## 6. Риски

| Риск | Что делаем |
|---|---|
| `Optimistic` не замечает потерянное превью — битая картинка | команда `photos_previews` (шаг 5) + строка в README |
| Недоступный Redis ломает сохранение фото | принято владельцем 2026-11-15; задача `P5-15` в бэклоге |
| Ошибка генерации превью всплывает в `photo.save()` и даёт `failed` | фото переобрабатывается повторной задачей; поведение записано в план и README |
| Подписанная ссылка на оригинал в админке истекает через час | приемлемо: админка, обновление страницы |
| Dev-режим не защищает оригинал | записано в README явно; защита — политика бакета в бою |
| Переименование `S3_*` ломает чужой `.env` | предусловие раздела 4; при `USE_S3=False` ничего не читается |

## 7. Проверка подэтапа

- `make lint && make test` на шаге 9, semver по навыку `semver-checker`.
- Ручная проверка: загрузка фото → `display` и превью на месте → повторный
  показ не создаёт файлов → оригинала нет в HTML → `og:image` стабилен.
- После подэтапа: `README.md` знает два бакета и команду пересборки,
  `KODA.md`/`AGENTS.md` — три правила хранилища, бэклог — `P5-13` закрыт
  и `P5-15` заведён.
