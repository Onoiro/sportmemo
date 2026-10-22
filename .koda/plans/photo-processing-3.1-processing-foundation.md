# SportMemo — подэтап 3.1: фундамент обработки

Дата создания: 2026-10-22
Дата утверждения: 2026-10-22
Статус: **завершён (0.17.0)**
Мастер-план: [`photo-processing.md`](photo-processing.md) (подэтап 3.1, решения 2.1–2.9)
Бэклог: [`.koda/backlog.md`](../backlog.md) — задачи P3-3, P3-13 (поле)
Ветка: `feature/photo-processing-foundation`
База: `master`, версия 0.16.0
Финал: bump **0.16.0 → 0.17.0 (minor)** + `uv lock`

> **Для новой сессии.** План утверждён целиком, включая решения A и B (раздел 2).
> Работа начинается с шага 1: `git switch -c feature/photo-processing-foundation`,
> затем `uv add numpy pillow-heif`. `opencv-python-headless` в этом подэтапе
> **не ставится**.

---

## 1. Цель и границы

**Цель.** Научиться безопасно принимать файл и получать из него нормализованную версию
для показа — без UI, без Celery. После подэтапа обработка запускается из консоли,
а публичные страницы берут превью из `display`, а не из оригинала.

**Что входит:**

- разбор EXIF по белому списку (GPS не сохраняется нигде);
- чистые функции обработки: нормализация ориентации, поворот, кроп, версия для показа,
  перцептивный хеш;
- сервис `process_photo()` — от оригинала до заполненных `display`, `width`, `height`,
  `phash`, `processing_status`;
- поля модели `crop_data`, `original_filename`, `uploaded_by` → `SET_NULL`;
- UUID-имена файлов;
- команда `photos_backfill` (`--dry-run`, `--only-missing`, `--limit`);
- превью и `og:image` строятся от `display`, без отката на оригинал;
- `rights_confirmed` выходит из правила видимости (решение 2.1);
- технические долги 2.9, кроме `docker-compose.yml` (он в 3.3).

**Что не входит (по мастер-плану):**

| Отложено | Куда |
|---|---|
| Форма загрузки, лимиты, `/photos/mine/`, `/rules/` | 3.2 |
| Celery-задачи, ретраи, статус «в обработке» из воркера | 3.3 |
| Cropper.js, валидация координат из браузера, оверлей в админке | 3.4 |
| Поиск похожих, страница в админке, пороги | 3.5 |
| `MergeableModel`, `merged_into`, редирект на канон | 3.6 |
| Автоуровни, CLAHE, подавление блика | после 3.6 |
| Сборка мусора старых производных | P3-11 |

---

## 2. Решения

Решения 2.1–2.9 мастер-плана действуют без изменений. Ниже — только то, что
мастер-план оставил на усмотрение подэтапа.

| # | Вопрос | Решение |
|---|---|---|
| 1 | Где живут числовые лимиты | `apps/photos/processing.py`: `MAX_UPLOAD_BYTES`, `MIN_SIDE_PX`, `MAX_SIDE_PX`, `WORK_COPY_MAX_SIDE` (2500), `DISPLAY_MAX_SIDE` (1280). В `config/settings.py` — только Django-специфичное: `Image.MAX_IMAGE_PIXELS`, `DATA_UPLOAD_MAX_NUMBER_FILES`, `CELERY_WORKER_CONCURRENCY` |
| 2 | Рабочая копия | `WORK_COPY_MAX_SIDE = 2500` по длинной стороне, уменьшение только вниз (`thumbnail()`), апскейла нет |
| 3 | `phash` | DCT-II на `numpy` (матрица косинусов 32×32, без `scipy` и `imagehash`), 64 бита → hex 16 символов. Считается от `display`: дубликаты ищутся по тому, что видит человек |
| 4 | EXIF в `display` | Не переносится вовсе: сохраняем через `PIL` без `exif=` и без `icc_profile` из оригинала |
| 5 | Невалидный `crop_data` | `apply_crop()` клампит координаты по границам; мусор (не словарь, версия ≠ 1, стороны < 100 px) → обработка как «без кропа» + `logger.warning`. Форма отклонит такой ввод в 3.4, сервис обязан не падать |
| 6 | `display` для файла ≤ 1280 px | Не растягиваем и не перекодируем без нужды, но всё равно сохраняем JPEG без EXIF: `display` — единственный источник превью, у него должен быть предсказуемый формат |
| 7 | Ошибка декодирования | `processing_status=failed`, `processing_error` — короткий текст без трейсбека, `logger.exception` в лог. Исключение наружу не выходит |
| 8 | Имя файла `display` | `photos/display/YYYY/MM/<uuid>.jpg`; UUID генерирует `upload_to`, сервис передаёт `"display.jpg"` |
| 9 | `original_filename` при бэкфилле | Не восстанавливается: исходное имя утеряно, поле остаётся пустым. Заполняется только при загрузке (3.2) |
| 10 | `width`/`height` | Пишутся из `display`, а не из оригинала: вёрстка резервирует место под то, что грузится |
| 11 | Судьба `SoftDeleteQuerySet.visible()` | **Удаляется** (подтверждено 2026-10-22). Метод фильтровал только `rights_confirmed` и после решения 2.1 не фильтрует ничего. Оставить пустой фильтр — соврать читателю. `visible_photos()` остаётся как точка входа и возвращает `Photo.public.published()` |
| 12 | Демо-фото «без прав» | В `seed_demo` три таких фото переводятся в `draft`: правило «без прав не публикуется» отменено, а показывать в демо-архиве опубликованное фото с `rights_confirmed=False` бессмысленно |
| 13 | Демо-данные и `display` | `seed_demo` вызывает `services.process_photo()` вместо ручной установки `processing_status`: демо-архив сразу получает `display`, размеры и `phash` |

### Решения по зависимостям (утверждено владельцем 2026-10-22)

| # | Вопрос | Решение |
|---|---|---|
| A | `opencv-python-headless` в 3.1 | **Не ставим.** В 3.1 нет ни одной операции, которой нужен OpenCV: поворот, кроп, ресайз и DCT-хеш делаются на Pillow + numpy. Зависимость весит ~60 МБ и тянет системные библиотеки в образ. Добавляем в том подэтапе, где появится первая операция для неё (автоуровни/CLAHE — после 3.6, подавление блика — отдельным шагом). Перед добавлением — вернуться к вопросу отдельно |
| B | `pillow-heif` в 3.1 | **Ставим.** Регистрация HEIC в Pillow нужна уже бэкфиллу: если в архиве появится HEIC, обработка не должна падать. Регистрация — в `PhotosConfig.ready()` под `try/except ImportError`, чтобы отсутствие пакета не ломало проект |

Ставить в этом подэтапе: `numpy`, `pillow-heif`. Не ставить: `opencv-python-headless`,
`imagehash`, `scipy`.

---

## 3. Архитектура

### 3.1 Раскладка кода

| Слой | Где |
|---|---|
| Разбор EXIF | `apps/photos/exif.py` |
| Чистые функции обработки (PIL in → PIL out) | `apps/photos/processing.py` |
| Пайплайн и работа с моделью | `apps/photos/services.py` |
| Пути загрузки файлов | `apps/photos/models.py` (`photo_original_path` и др.) |
| Команда бэкфилла | `apps/photos/management/commands/photos_backfill.py` |
| Тесты | `tests/test_photo_exif.py`, `tests/test_photo_processing.py`, `tests/test_photo_backfill.py` |

`processing.py` не импортирует модели и Django-настройки хранилища: принимает
`PIL.Image` и параметры, возвращает изображение и метаданные. Это даёт быстрые
unit-тесты без базы и возможность гонять те же функции в команде и в воркере.

### 3.2 Поток `process_photo()`

```
оригинал (storage)
   │ Image.open → extract_exif()          # до транспонирования
   │ exif_transpose()                     # Orientation из EXIF
   │ thumbnail до 2500 px                 # рабочая копия против OOM
   │ apply_rotation(0/90/180/270)
   │ apply_crop(crop_data)                # клампинг, мусор → без кропа
   │ make_display() → JPEG ≤1280, без EXIF
   ▼
display в storage, width/height, phash, exif, processing_status=processed
```

### 3.3 Что происходит с файлами

| Поле | Содержимое |
|---|---|
| `original` | Байт в байт как загружен, EXIF и GPS на месте |
| `display` | Нормализованный оригинал или кроп, JPEG ≤1280 px, без EXIF |
| `thumbnail` | `ImageSpecField(source="display")`, 400 px |
| `preview` | `ImageSpecField(source="display")`, 1280 px |
| `processed` | Остаётся свободным под будущую обработку |

---

## 4. Шаги

### 1. [ ] Ветка и зависимости

- Навыки: koda-coder.
- Файлы: `pyproject.toml`, `uv.lock`.
- Содержание: ветка `feature/photo-processing-foundation` от `master`;
  `uv add numpy pillow-heif` (ровно две зависимости, см. решения A и B);
  проверить лицензии (numpy — BSD-3-Clause, pillow-heif — BSD-3-Clause) и записать
  их в README, как для `static/vendor/`.
- Критерий: `uv sync` проходит, `uv run python -c "import numpy, pillow_heif"` работает,
  `uv.lock` обновлён.

### 2. [ ] Настройки и технические долги 2.9 (1, 2, 4)

- Навыки: koda-coder.
- Файлы: `config/settings.py`, `config/__init__.py`, `tests/conftest.py`, `.env.example`.
- Содержание:
  - `config/__init__.py`: `from .celery import app as celery_app` и `__all__`;
  - `config/settings.py`: `CELERY_WORKER_CONCURRENCY = 2`,
    `DATA_UPLOAD_MAX_NUMBER_FILES = 12`, `Image.MAX_IMAGE_PIXELS = 60_000_000`
    (импорт `PIL.Image` рядом с настройками файлов, с комментарием «почему»);
  - `tests/conftest.py`: автофикстура `celery_eager`, ставящая
    `CELERY_TASK_ALWAYS_EAGER = True` и `CELERY_TASK_EAGER_PROPAGATES = True` явно,
    а не через `DEBUG`.
- Критерий: `manage.py check` чист; `celery -A config inspect registered` не падает
  без Redis (ошибка соединения допустима, но приложение Celery создаётся);
  тесты не ходят в брокер.

### 3. [ ] Модель: поля, UUID-пути, превью от `display`

- Навыки: koda-coder, semver-checker (в конце подэтапа).
- Файлы: `apps/photos/models.py`, `apps/photos/admin.py`,
  `apps/photos/migrations/0003_photo_processing_fields.py`.
- Содержание:
  - `crop_data = models.JSONField(default=dict, blank=True)` с help-текстом
    о формате `{"version": 1, "x", "y", "width", "height", "rotate"}`;
  - `original_filename = models.CharField(max_length=255, blank=True)`;
  - `uploaded_by`: `null=True`, `on_delete=SET_NULL`, `blank=False`
    (в админке поле остаётся обязательным);
  - функции `photo_original_path`, `photo_display_path`, `photo_processed_path`
    на уровне модуля: `photos/<kind>/%Y/%m/<uuid4().hex><ext>`;
  - `thumbnail` и `preview`: `source="display"`;
  - `rights_confirmed`: help-текст «исторический флаг, на видимость не влияет»;
  - админка: `original_filename` в «Служебное», `crop_data` в «Производные»
    (readonly), `uploaded_by` остаётся в «Правах».
- Критерий: миграция создана и применяется на пустой базе; `historicalphoto`
  получает те же поля; `manage.py makemigrations --check` чист.

### 4. [ ] Разбор EXIF

- Навыки: koda-coder.
- Файлы: `apps/photos/exif.py`, `apps/photos/apps.py`.
- Содержание:
  - белый список из решения 2.6 (IFD0: `Make`, `Model`, `Software`, `Artist`,
    `Copyright`, `ImageDescription`, `XResolution`, `YResolution`, `ResolutionUnit`,
    `Orientation`; ExifIFD: `DateTimeOriginal`, `DateTimeDigitized`, `ExposureTime`,
    `FNumber`, `ISOSpeedRatings`, `FocalLength`);
  - `extract_exif(image) -> dict` — только белый список, GPS IFD не читается;
  - нормализация значений для JSONB: `IFDRational` → `float`, `bytes` → ASCII
    или отброс, кортежи → список;
  - `taken_year(exif) -> int | None` — только `DateTimeOriginal`, диапазон
    1850 — текущий год, иначе `None`;
  - регистрация HEIC в `PhotosConfig.ready()`.
- Критерий: EXIF с GPS даёт словарь без единого GPS-ключа; `Make`/`Artist`/`Copyright`
  сохраняются; `DateTime` сканера без `DateTimeOriginal` не даёт года; дата 2026
  на снимке 1985-го в базу не попадает автоматически (только как значение функции).

### 5. [ ] Чистые функции обработки

- Навыки: koda-coder.
- Файлы: `apps/photos/processing.py`.
- Содержание: `normalize_orientation()`, `apply_rotation()`, `apply_crop()`,
  `make_display()`, `perceptual_hash()`, `image_info()`, `hamming_distance()`;
  константы лимитов. Все функции принимают и возвращают `PIL.Image`, ничего не пишут
  на диск и не знают о моделях. `apply_rotation` принимает только 0/90/180/270,
  иное значение игнорируется с предупреждением. `perceptual_hash` — DCT-II
  на numpy, hex-строка 16 символов.
- Критерий: юнит-тесты без базы проходят; функции не импортируют `django.db`
  и `apps.*`.

### 6. [ ] Сервис `process_photo()`

- Навыки: koda-coder.
- Файлы: `apps/photos/services.py`.
- Содержание:
  - `process_photo(photo) -> Photo`: статус `processing` → чтение оригинала →
    `extract_exif` → `exif_transpose` → рабочая копия → поворот → кроп →
    `display` → `width`/`height`/`phash`/`exif` → `processed`;
  - любое исключение → `failed` + `processing_error` (короткий текст),
    `logger.exception`, объект возвращается;
  - `save(update_fields=[...])` с `updated_at`, чтобы история писалась одной записью;
  - повторный вызов пересчитывает `display` (старый файл остаётся — P3-11).
- Критерий: фото с `Orientation=6` даёт `display` в правильной ориентации; битый
  файл и отсутствующий оригинал дают `failed`, а не исключение наружу.

### 7. [ ] Команда `photos_backfill`

- Навыки: koda-coder.
- Файлы: `apps/photos/management/commands/photos_backfill.py`, `Makefile`.
- Содержание: по умолчанию — фото с пустым `display` или пустым `phash`;
  `--only-missing` — только пустой `display`; `--limit N`; `--dry-run` — отчёт
  без записи. Ошибки отдельных фото не прерывают проход, в конце — сводка
  «обработано / пропущено / ошибок». Цель `make photos-backfill`.
- Критерий: `--dry-run` не меняет ни одного поля; без флага заполняет `display`,
  `width`, `height`, `phash`; битое фото попадает в счётчик ошибок.

### 8. [ ] Публичная видимость без `rights_confirmed`

- Навыки: koda-coder.
- Файлы: `apps/core/models.py`, `apps/core/shortcuts.py`, `apps/photos/models.py`.
- Содержание:
  - `SoftDeleteQuerySet.visible()` удаляется;
  - `visible_photos()` → `Photo.public.published()`;
  - `photos_count()` — без `photos__rights_confirmed=True`;
  - `Photo.is_public` — статус и отсутствие удаления, без прав;
  - `_is_publicly_visible()` — только `is_published`.
- Критерий: фото без `rights_confirmed` попадает в галерею, на страницы команды,
  события, человека, в карту сайта и в поиск; черновик по-прежнему не попадает.

### 9. [ ] Превью и метатеги без отката на оригинал

- Навыки: koda-coder.
- Файлы: `apps/photos/views.py`, `apps/core/schema.py`.
- Содержание: `page_meta(..., image=photo.preview)` и
  `photo_schema()` — `image = photo.preview`. Фото без `display` получает
  заглушку `og:image` и не отдаёт JSON-LD-блок с картинкой.
- Критерий: в HTML страницы фото нет адреса `photos/original/`; фото без `display`
  отдаёт заглушку и не падает.

### 10. [ ] Новые тесты

- Навыки: koda-coder.
- Файлы: `tests/test_photo_exif.py`, `tests/test_photo_processing.py`,
  `tests/test_photo_backfill.py`, `tests/utils.py`.
- Содержание:
  - `tests/utils.py`: `make_quadrant_image()` (четыре цветных квадранта),
    `make_image_with_exif_orientation()`, `image_pixel()`;
  - EXIF: белый список, GPS отсутствует, нормализация типов, `taken_year`
    (валидный, отсутствующий, дата сканера, дата вне диапазона);
  - ориентация: `Orientation=6` и `3` — углы после нормализации;
  - поворот и кроп: по одному и в комбинации, проверка по цветам углов;
  - `make_display`: ≤1280, JPEG, нет EXIF;
  - `phash`: одинаковое фото в разном масштабе и качестве — расстояние ≤ 10,
    разные картинки — > 16;
  - `process_photo`: успех, битый файл, отсутствующий оригинал, мусор в `crop_data`;
  - бэкфилл: `--dry-run`, `--only-missing`, `--limit`, сводка ошибок.
- Критерий: новые тесты зелёные, `make lint` чист.

### 11. [ ] Правка существующих тестов и демо-данных

- Навыки: koda-coder.
- Файлы: `tests/factories.py`, `tests/conftest.py`,
  `apps/core/management/commands/seed_demo.py`, `tests/test_seed_demo.py`,
  `tests/test_public_queries.py`, `tests/test_visible_or_404.py`,
  `tests/test_photos.py`, `tests/test_photo_gallery.py`, `tests/test_home.py`,
  `tests/test_public_lists.py`, `tests/test_team_page.py`, `tests/test_event_page.py`,
  `tests/test_person_page.py`, `tests/test_seo.py`, `tests/test_search_ui.py`,
  `tests/test_search_index.py`.
- Содержание:
  - `PhotoFactory` и фикстура `photo` заполняют `display` (`make_image_file()`),
    иначе превью от `display` в тестах пусто;
  - тесты «фото не видно» переводятся на `status=Status.DRAFT` вместо
    `rights_confirmed=False`; добавляется тест «исторический флаг не влияет
    на видимость»;
  - `seed_demo`: три фото «без прав» → `draft`; `process_photo()` вместо ручного
    `processing_status`, `width`/`height` больше не задаются вручную;
  - `test_photos.py`: тест «превью строится от `display`» (оригинал 120×80,
    `display` 60×40 → thumbnail не больше 60×40);
  - `test_seo.py`/`test_photo_gallery.py`: тест «фото без `display` показывает
    плейсхолдер, а не 500».
- Критерий: `make test` зелёный целиком; ни один тест не проверяет отменённое
  правило «без прав не публикуется».

### 12. [ ] Проверка

- Навыки: koda-coder.
- Содержание: `make lint && make test`; `manage.py check`;
  на демо-данных: `make seed-demo`, затем `photos_backfill --dry-run` (ничего
  не меняет) и реальный проход; открыть страницу фото и главную, убедиться, что
  картинки на месте, в исходнике нет `photos/original/`, размеры совпадают
  с `display`; проверить фото без `display` (создать вручную) — плейсхолдер.
- Критерий: критерии готовности мастер-плана (раздел 3.1) выполнены полностью.

### 13. [ ] Документация и версия

- Навыки: semver-checker, koda-coder.
- Файлы: `README.md`, `KODA.md`, `AGENTS.md`, `.koda/backlog.md`,
  `.koda/plans/photo-processing.md`, `pyproject.toml`, `uv.lock`.
- Содержание:
  - README: раздел «Обработка фото» (порядок операций, `display` как источник
    превью, `crop_data`, лимиты, `photos_backfill`, новые зависимости и лицензии),
    обновить строки про `rights_confirmed` и счётчик тестов;
  - KODA.md / AGENTS.md: правила — чистые функции в `processing.py`, `display`
    единственный источник превью, `rights_confirmed` не влияет на видимость,
    EXIF по белому списку и GPS не хранится;
  - бэклог: P3-3 → готово, P3-13 (поле) → готово;
  - мастер-план: отметка 3.1 в «Прогрессе», при отклонениях — строка в итогах;
  - semver: **minor** 0.16.0 → 0.17.0 + `uv lock`.
- Критерий: документация совпадает с кодом, версия синхронизирована в `uv.lock`.

---

## 5. Открытые вопросы

1. **`opencv-python-headless`** — закрыт: в 3.1 не нужен (решение A). Вернуться
   перед автоуровнями/CLAHE (после 3.6) и перед подавлением блика.
2. **Порог `phash`** — в 3.1 проверяется только «одинаковое близко, разное далеко»;
   рабочие пороги (≤10 / 11–16) уточняются в 3.5 на реальных парах.

---

## 6. Прогресс

| # | Шаг | Статус |
|---|---|---|
| 1 | Ветка и зависимости | [x] |
| 2 | Настройки и технические долги 2.9 (1, 2, 4) | [x] |
| 3 | Модель: поля, UUID-пути, превью от `display` | [x] |
| 4 | Разбор EXIF | [x] |
| 5 | Чистые функции обработки | [x] |
| 6 | Сервис `process_photo()` | [x] |
| 7 | Команда `photos_backfill` | [x] |
| 8 | Публичная видимость без `rights_confirmed` | [x] |
| 9 | Превью и метатеги без отката на оригинал | [x] |
| 10 | Новые тесты | [x] |
| 11 | Правка существующих тестов и демо-данных | [x] |
| 12 | Проверка | [x] |
| 13 | Документация и версия | [x] |
