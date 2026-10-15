# SportMemo — подэтап 2.12: поиск, инфраструктура

Дата создания: 2026-10-16
Статус: **выполнен (2026-10-16)**
Мастер-план: [`public-archive.md`](public-archive.md) (подэтап 2.12)
Бэклог: [`.koda/backlog.md`](../backlog.md) — задача P2-6
Ветка: `feature/search-index`
База: `master`, версия 0.12.0
Финал: bump **0.12.0 → 0.13.0 (minor)** + `uv lock`

---

## 1. Цель подэтапа

Дать архиву поиск. Подэтап делает **только инфраструктуру**: расширение, поля, индексы,
сборку поискового текста, переиндексацию и тесты. Страница результатов и строка поиска
в navbar — подэтап 2.13; до его завершения `/search/` остаётся заглушкой `core:search`.

Двухкомпонентная схема поиска (решение этапа 1, план `foundation-and-data-model.md`):

1. **Полнотекстовый поиск** (`tsvector`, конфигурация `russian`) — «нашёл по части слова»:
   «Гром» находит «Громов», «первенство» находит «Первенство России».
2. **Триграммы** (`pg_trgm`, `gin_trgm_ops`) — устойчивость к опечаткам и «возможно,
   вы искали»: `TrigramSimilarity` выдаёт похожие названия, когда точных совпадений нет.
   Этим же механизмом на этапе 3 будет пользоваться слияние дубликатов (`phash` — для фото,
   триграммы — для имён).

**Что важно помнить.** Индекс не выдаёт данных, которых нет в таблице: он не «расширяет»
видимость. Приватность обеспечивают две вещи:

- в полнотекстовый индекс **не попадает** то, что не должно быть найдено (скрытая фамилия
  при `name_visibility = first_name_only` и `hidden`) — это решается сборкой текста;
- выдачу фильтрует запрос (`public.published()`, `visible()`) — это задача 2.13.

---

## 2. Решения, согласованные с владельцем (2026-10-16)

| # | Вопрос | Решение |
|---|---|---|
| 1 | Где живут поля поиска | **В моделях каталога** (`Team`, `Person`, `Event`, `Photo`), миграция — в `apps/catalog`. `makemigrations` всегда пишет `AddField` в приложение-владелец модели; ручная миграция в `apps/search` с чужой моделью не собралась бы в `state`. `apps/search` владеет расширением, сборкой текста, сигналами и командой |
| 2 | Отдельная колонка `search_text` | **Не делаем.** Мастер-план требует `SearchVectorField` — этого достаточно. Текстовая копия полей была бы дублированием и источником рассинхрона. Текст собирается в Python и сразу превращается в `tsvector` |
| 3 | Как пишется индекс | `Model.objects.filter(pk=...).update(search_vector=...)` — **не** `save()`. Два следствия: `django-simple-history` не пишет мусорную запись на каждый пересчёт, а `updated_at` (`auto_now`) не сдвигается — «объект изменился» и «индекс пересчитан» разные события, и `lastmod` в sitemap не должен дёргаться от переиндексации. `update()` не шлёт сигналов, поэтому рекурсии нет |
| 4 | Где создаётся расширение | Миграция `apps/search/0001_pg_trgm.py`, операция `TrigramExtension` (штатная в Django). Порядок задан зависимостью: `catalog.0003 → search.0001`. Без расширения `gin_trgm_ops` не существует |
| 5 | Конфигурация словаря | `russian`, константа `SEARCH_CONFIG` в `apps/search/indexing.py`. Настраивать нечего, а латиница и опечатки закрываются триграммами |
| 6 | Что индексируется триграммами | «Сырые» текстовые поля: `Team.name`, `Team.city`, `Event.name`, `Event.city`, `Person.first_name`, `Person.last_name`, `PersonName.value`, `Photo.title`. Похожесть нужна по именам и названиям, а не по описаниям. Индекс не раскрывает данных — приватность обеспечивает фильтр запроса, а полнотекстовый индекс тем, что скрытой фамилии в нём нет |
| 7 | Приватность в полнотекстовом индексе | `Person`: `public_name`, `bio`, `first_name`, `middle_name` — всегда; `last_name` и варианты имён (`PersonName`) — **только при `Person.is_full_name_public`**. При `hidden` текст пустой: индексировать «Имя не указано» значит находить всех скрытых по слову «Имя» |
| 8 | Фото | `Photo.search_vector` добавляем **уже сейчас** (по `title` + `caption`): 2.13 обещает группу результатов «фото», а поле, индекс, сигнал и переиндексация — один и тот же код для всех моделей |
| 9 | `SearchVectorField` в historical-таблицах | Поле попадёт и в историю (`simple-history`), но всегда `NULL`: мы пишем через `update()`. Если на шаге 2 всплывёт несовместимость — исключаем поле из истории через `excluded_fields` (план Б, согласован) |
| 10 | Переиндексация | Management-команда `search_reindex`, батчами (`iterator(chunk_size=500)` + `bulk_update`), счётчики в выводе, флаг `--model`. Идемпотентна |
| 11 | Тесты только на PostgreSQL | `pg_trgm` и `tsvector` есть только там. На модуль — `skipif(connection.vendor != "postgresql")`: пропуск делает причину явной, а не даёт «непонятную ошибку на SQLite» |

---

## 3. Архитектура решения

### 3.1 Раскладка кода

| Что | Где |
|---|---|
| Расширение `pg_trgm` | `apps/search/migrations/0001_pg_trgm.py` |
| Поля и индексы | `apps/catalog/migrations/0003_search_indexes.py` |
| Сборка поискового текста | `apps/search/indexing.py` |
| Сигналы | `apps/search/signals.py`, подключение в `SearchConfig.ready()` |
| Переиндексация | `apps/search/management/commands/search_reindex.py` |
| Тесты | `tests/test_search_index.py` |

### 3.2 Что попадает в `search_vector`

| Модель | Состав текста | Приватность |
|---|---|---|
| `Team` | `name`, `city`, `description`, значения `attributes` (кроме служебного ключа `demo`) | — |
| `Event` | `name`, `city`, `country`, `description`, `get_event_type_display()`, значения `attributes` (кроме `demo`) | — |
| `Person` | `public_name`, `first_name`, `middle_name`, `bio`; при `is_full_name_public` — ещё `last_name` и все `PersonName.value` | `last_name` и варианты имён — только при полном имени; при `hidden` текст пустой |
| `Photo` | `title`, `caption` | — |

Сборка живёт в одной функции `build_search_text(obj)` с ветками по модели. Модели
импортируются **внутри** функции: на уровне модуля это дало бы зависимость
`search` → `catalog`/`photos` и риск цикла (то же правило, что в `apps/core/shortcuts.py`).

### 3.3 Индексы

| Индекс | Поля | Зачем |
|---|---|---|
| GIN по `search_vector` | `search_vector` | Полнотекстовый поиск (`SearchQuery` + `SearchRank`) |
| GIN `gin_trgm_ops` | `Team.name`, `Team.city` | Похожие названия команд |
| GIN `gin_trgm_ops` | `Event.name`, `Event.city` | Похожие названия событий |
| GIN `gin_trgm_ops` | `Person.first_name`, `Person.last_name` | Похожие имена (и будущее слияние дубликатов) |
| GIN `gin_trgm_ops` | `PersonName.value` | Варианты имён |
| GIN `gin_trgm_ops` | `Photo.title` | Похожие заголовки фото |

### 3.4 Поток обновления индекса

```
сохранение объекта  → post_save  → reindex_instance(obj)  → queryset.update()
сохранение PersonName → post_save/post_delete → reindex_instance(person)
полный проход       → make search-reindex → reindex_queryset() → bulk_update()
```

Ни одна из ветвей не вызывает `save()` у объекта: иначе переиндексация двигала бы
`updated_at`, писала историю и зацикливалась на сигналах.

---

## 4. Шаги

1. [x] **Каркас `apps/search`: подключение и сборка поискового текста**
    - Навыки: koda-coder
    - `apps/search/__init__.py`, `apps/search/apps.py` (`SearchConfig`, `verbose_name`),
      `apps/search/indexing.py`:
        - `SEARCH_CONFIG = "russian"`;
        - `build_search_text(obj)` — по таблице 3.2;
        - `build_search_vector(obj)` → `SearchVector(Value(text), config=SEARCH_CONFIG)`;
        - `reindex_instance(obj)` — пересчёт одной строки через `update()`;
        - `reindex_queryset(model, queryset=None)` — батчами, `bulk_update`, счётчик.
    - `config/settings.py`: `"django.contrib.postgres"` в `DJANGO_APPS` (без него не работают
      `SearchVectorField`, `GinIndex`, лукапы `trigram_similar` и `SearchLookup`),
      `"apps.search"` в `LOCAL_APPS` после `apps.catalog` и `apps.photos`.
    - Файлы: `apps/search/__init__.py`, `apps/search/apps.py`, `apps/search/indexing.py`,
      `config/settings.py`

2. [x] **Миграции: расширение `pg_trgm` и поля с индексами**
    - Навыки: koda-coder
    - `apps/search/migrations/__init__.py`, `apps/search/migrations/0001_pg_trgm.py` —
      `TrigramExtension`.
    - `apps/catalog/migrations/0003_search_indexes.py` — `AddField` `search_vector`
      (`SearchVectorField(null=True)`) на `Team`, `Person`, `Event`, `Photo` и `AddIndex`
      по таблице 3.3; `dependencies = [("search", "0001_pg_trgm"), ("catalog", "0002_...")]`.
    - Проверить: миграции применяются на чистой базе, `makemigrations --check` чист,
      `django-simple-history` работает (вопрос 9; при поломке — `excluded_fields`).
    - Файлы: `apps/search/migrations/__init__.py`,
      `apps/search/migrations/0001_pg_trgm.py`, `apps/catalog/migrations/0003_search_indexes.py`

3. [x] **Сигналы: пересчёт индекса при сохранении**
    - Навыки: koda-coder
    - `apps/search/signals.py`: `post_save` на `Team`, `Person`, `Event`, `Photo`;
      `post_save` и `post_delete` на `PersonName` (варианты имён входят в текст человека,
      поэтому меняется индекс человека).
    - Подключение в `SearchConfig.ready()`: импорт `signals` внутри `ready()` и проверка
      готовности приложения — сигналы не должны срабатывать во время загрузки миграций.
    - Пересчёт только через `update()` (решение 3).
    - Файлы: `apps/search/signals.py`, `apps/search/apps.py`

4. [x] **Management-команда `search_reindex` и цель `make`**
    - Навыки: koda-coder
    - `apps/search/management/__init__.py`, `apps/search/management/commands/__init__.py`,
      `apps/search/management/commands/search_reindex.py`: флаг `--model` (`team`, `person`,
      `event`, `photo`; без флага — все), вывод счётчиков по моделям, повторный запуск
      даёт те же значения.
    - `Makefile`: цель `search-reindex` + строка в `.PHONY` (help собирается автоматически
      из комментария `##`).
    - Файлы: `apps/search/management/**`,
      `apps/search/management/commands/search_reindex.py`, `Makefile`

5. [x] **Тесты `tests/test_search_index.py`**
    - Навыки: koda-coder
    - `skipif(connection.vendor != "postgresql")` на модуль.
    - Расширение включено; `search_vector` заполняется сигналом при создании и при
      изменении объекта.
    - Полнотекстовый поиск: частичное слово («Гром» → «Громов»), поиск по описанию,
      поиск по варианту имени (`PersonName`).
    - Триграммы: `trigram_similar` находит по опечатке.
    - Приватность: при `first_name_only` и `hidden` фамилия не находится ни
      полнотекстовым поиском, ни триграммами; `display_name` в индекс не попадает;
      `attributes["demo"]` не индексируется.
    - Переиндексация: `search_reindex` заполняет объекты с пустым индексом, не пишет
      историю (`historical_team`/`historical_person` — ноль новых записей), не меняет
      `updated_at`, идемпотентна.
    - Файлы: `tests/test_search_index.py`

6. [x] **Проверка**
    - Навыки: koda-coder
    - `make lint`, `make test`, `uv run python manage.py makemigrations --check --dry-run`.
    - Ручная проверка в shell на демо-данных (`make seed-demo`): полнотекстовый запрос
      через `SearchQuery`/`SearchRank`, `TrigramSimilarity` по фамилии с опечаткой,
      два прогона `make search-reindex`.
    - Новых строк интерфейса нет — `make i18n` не нужен; проверить, что каталоги
      переводов не изменились.
    - Файлы: —

7. [x] **Документация и версия**
    - Навыки: koda-coder, semver-checker
    - `README.md`: состояние, число тестов, раздел «Поиск: инфраструктура» (расширение,
      поля, индексы, приватность индекса, `make search-reindex`, требование PostgreSQL),
      таблица URL (пометка, что `/search/` — заглушка до 2.13).
    - `KODA.md` + `AGENTS.md`: правило «индекс пересчитывается через `update()`, а не
      `save()`», «скрытая фамилия в индекс не попадает», «поиск работает только
      на PostgreSQL», «сборка текста живёт в `apps/search/indexing.py`».
    - Мастер-план `public-archive.md`: подэтап 2.12 → `[x]`, раздел «Итог», таблица
      «Прогресс»; бэклог: P2-6 → 2.12 сделан, следующий — 2.13.
    - Версия: 0.12.0 → 0.13.0 (minor) в `pyproject.toml`, затем `uv lock`.
    - Файлы: `README.md`, `KODA.md`, `AGENTS.md`, `.koda/plans/public-archive.md`,
      `.koda/backlog.md`, `pyproject.toml`, `uv.lock`

---

## 5. Критерии готовности

- `make migrate` на чистой PostgreSQL создаёт расширение `pg_trgm` и все индексы;
  `makemigrations --check` чист.
- После сохранения объекта и после `make search-reindex` полнотекстовый поиск находит
  объект по частичному слову, а триграммы — по опечатке.
- Поиск находит человека по варианту имени (`PersonName`).
- Скрытая фамилия не находится поиском ни при `first_name_only`, ни при `hidden`;
  `display_name` и служебный `attributes["demo"]` в индекс не попадают.
- Переиндексация не пишет записи в `django-simple-history` и не сдвигает `updated_at`;
  повторный запуск идемпотентен.
- `make lint && make test` чистые; версия 0.13.0; документация и планы обновлены.

---

## 6. Результат (2026-10-16)

Выполнено всё; версия 0.12.0 → **0.13.0** (minor), ветка `feature/search-index`.

- `apps/search`: `indexing.py` (сборка текста, сборка запроса, пересчёт), `signals.py`,
  команда `search_reindex`; моделей нет — поля поиска живут в `catalog` и `photos`.
- Миграции: `search/0001_pg_trgm`, `catalog/0003_search_indexes`, `photos/0002_search_indexes`.
- `django.contrib.postgres` в `DJANGO_APPS`, `apps.search` в `LOCAL_APPS`.
- `Makefile`: цель `search-reindex`.
- Тесты: `tests/test_search_index.py` (41), всего 736.

**Отклонение от плана:** в `indexing.py` добавлена `build_search_query()`. План предполагал
только сборку текста, но проверка в shell показала, что частичное слово не находится:
русский словарь приводит «Громовцы» к «громовц», а `plainto_tsquery('гром')` даёт «гром»,
и они не совпадают. Запрос строится в режиме `raw` с префиксом `:*` на каждое слово;
символы `&`, `|`, `!`, `:` при этом отбрасываются — в `raw`-режиме это язык запросов,
и сырой ввод уронил бы страницу.

**Прочее:** `SearchVectorField` попал и в historical-таблицы (`simple-history`), колонка
остаётся пустой — пишем через `update()`. План Б (`excluded_fields`) не понадобился.

**Известное ограничение для 2.13:** триграммный индекс хранит «сырую» фамилию, поэтому
скрытие в триграммной выдаче обеспечивает фильтр запроса. Публичные результаты обязаны
идти через `public.published()` и учитывать `name_visibility`; в тестах это зафиксировано
явно (`test_trigram_indexes_raw_last_name_by_design`).

---

## 7. Риски и известные ограничения

| Риск / ограничение | Что делаем |
|---|---|
| `CREATE EXTENSION` требует прав суперпользователя | В dev и CI пользователь БД суперпользователь. В чек-лист деплоя (этап 4) — отдельная строка |
| `SearchVectorField` в historical-таблицах `simple-history` | Колонка всегда `NULL` (пишем через `update()`). Если всплывёт несовместимость — `excluded_fields` |
| Расширение не создано, а индекс создаётся | Порядок задан явно: `catalog.0003 → search.0001` |
| Переиндексация «сдвинула бы» `updated_at` и `lastmod` в sitemap | Пишем через `queryset.update()` — `auto_now` не срабатывает |
| Поиск не работает на SQLite | `skipif` по вендору + явная запись в документации: проект работает на PostgreSQL |
| Утечка скрытой фамилии в индекс | Сборка текста живёт в одном месте (`indexing.py`), правило проверяется тестами на `first_name_only` и `hidden` |
| Дублирование логики в 2.13 | В 2.12 не создаём страницу результатов: интерфейс — отдельный подэтап, чтобы инфраструктуру можно было проверить в shell и тестах |

---

## 8. Ссылки

- Мастер-план этапа 2: [`public-archive.md`](public-archive.md), подэтап 2.12
- Предыдущий подэтап: [`public-archive-2.11-sitemap-robots-schema.md`](public-archive-2.11-sitemap-robots-schema.md)
- Схема данных и выбор поиска: [`foundation-and-data-model.md`](foundation-and-data-model.md)
- Правила публичных выборок: `apps/core/shortcuts.py`
