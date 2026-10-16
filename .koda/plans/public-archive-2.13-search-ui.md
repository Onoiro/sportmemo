# SportMemo — подэтап 2.13: поиск, интерфейс

Дата создания: 2026-10-16
Статус: **завершён (2026-10-16), v0.14.0**
Мастер-план: [`public-archive.md`](public-archive.md) (подэтап 2.13)
Бэклог: [`.koda/backlog.md`](../backlog.md) — задача P2-6
Ветка: `feature/search-ui`
База: `master`, версия 0.13.0
Финал: bump **0.13.0 → 0.14.0 (minor)** + `uv lock`

---

## 1. Цель подэтапа

Дать архиву страницу поиска. Инфраструктура готова в 2.12 (v0.13.0): поля
`search_vector`, GIN-индексы, расширение `pg_trgm`, сборка текста
(`build_search_text()`), сборка запроса (`build_search_query()`), сигналы
и команда `search-reindex`. Здесь появляется **интерфейс**: страница
`/search/?q=...` с группами результатов и «возможно, вы искали».

До этого подэтапа `/search/` — заглушка `core:search`
(`views.placeholder` + `templates/core/placeholder.html`), форма поиска уже
стоит в navbar, на главной и в футере.

**Что важно помнить про приватность.** Индекс не «расширяет» видимость:
полнотекстовый индекс скрытую фамилию не содержит вовсе (правило в
`indexing.py`), а **выдачу фильтрует запрос**. Триграммный индекс, наоборот,
хранит «сырую» фамилию — это зафиксировано тестом
`test_trigram_indexes_raw_last_name_by_design`. Значит подсказки «возможно,
вы искали» обязаны сравнивать только безопасные поля.

---

## 2. Решения, согласованные с владельцем (2026-10-16)

| # | Вопрос | Решение |
|---|---|---|
| 1 | Где живёт страница | **`apps/search`** (`search:index`), маршрут переносится из `core`. Заглушка `core:search`, `views.placeholder()` и `templates/core/placeholder.html` удаляются. Альтернатива (оставить в `core`) отклонена: это дало бы зависимость `core` → `search`, а `core` в проекте — нижний слой |
| 2 | Подсветка совпадений (`ts_headline` / `SearchHeadline`) | **Отложена.** Записана в бэклог как идея I-11 с тремя сложностями: (а) `ts_headline` возвращает HTML без экранирования — название вида `</script>` разорвало бы страницу; (б) `public_name` человека — свойство Python, а не колонка, подсветить его нельзя, а подсвечивать `last_name` нельзя по приватности; (в) карточки (`team_card`, `event_card`, `photos/partials/card`) станут двухрежимными — нужна необязательная переменная с готовым HTML |
| 3 | Объём выдачи | 20 элементов на группу (`GROUP_LIMIT`), общий счётчик `total` по каждой группе, подпись «показаны первые N из M» при обрезке. **Пагинации нет**: страница результатов — обзорная, а не список |
| 4 | «Возможно, вы искали» | До 5 подсказок (`SUGGESTION_LIMIT`), показываются **только** когда во всех группах пусто |
| 5 | Черновики в выдаче | Поиск — **публичная выдача**: черновик не показывается никому, включая автора и модератора. `get_visible_or_404` здесь не применяется: автор увидел бы свой черновик по случайному запросу и не понял бы, почему у остальных его нет |
| 6 | Люди в выдаче | `Person.public.published()` + `exclude(name_visibility=HIDDEN)`. Подсказки — по `first_name` всегда, по `last_name` только при `name_visibility=FULL`, по `PersonName.value` только при `FULL`; кандидаты дедуплицируются по pk с максимальной похожестью |
| 7 | Длина запроса | `MAX_QUERY_LENGTH = 100`: запрос обрезается до сборки `tsquery`. Иначе длинный ввод породил бы гигантский запрос к базе |
| 8 | Мусорный ввод | Непустой `q`, в котором не осталось слов (`!!!`, `&&&`) → состояние «ничего не найдено» **без** подсказок: сравнивать триграммами мусор нечего |
| 9 | `noindex` | Страница результатов закрыта от индексации (`noindex, follow`). `/search/` уже в `Disallow` файла `robots.txt` |
| 10 | Сортировка | `-rank` (`SearchRank`), затем «человеческий» порядок: команды и события — по названию, люди — по фамилии и имени, фото — по дате создания |

---

## 3. Архитектура решения

### 3.1 Раскладка кода

| Что | Где |
|---|---|
| Сборка выдачи и подсказок | `apps/search/services.py` |
| Представление | `apps/search/views.py` |
| Маршрут | `apps/search/urls.py` (`search:index`), подключение в `config/urls.py` |
| Шаблоны | `templates/search/index.html`, `templates/search/partials/` |
| Карточка человека | `templates/catalog/partials/person_card.html` (новый) |
| Тесты | `tests/test_search_ui.py` |

### 3.2 Слой выдачи (`services.py`)

```
build_search_query(q)          ← из 2.12, не меняется
        ↓
search_page(query, text)       → SearchResult(groups, total, suggestions)
        ↓
  _group(...)  ← queryset + count() + срез GROUP_LIMIT
  suggestions(text)  ← только при total == 0
```

Структуры данных (dataclass — читаемее словаря и даёт свойство `truncated`):

- `SearchGroup(key, title, template, items, total)` — `template` это имя
  шаблона карточки (`catalog/partials/team_card.html` и т. д.), поэтому
  партиал группы обходится без ветвлений по типу;
- `SearchResult(groups, total, suggestions)`;
- `Suggestion(label, url, kind)` — готовая строка для шаблона: подсказки
  приходят из трёх разных моделей, и шаблон не должен знать, из какой.

Группы в фиксированном порядке: команды, события, люди, фотографии.

### 3.3 Выборки

| Группа | Выборка | Порядок |
|---|---|---|
| Команды | `Team.public.published().filter(search_vector=query)` | `-rank`, `name` |
| События | `Event.public.published().filter(search_vector=query)` | `-rank`, `-year`, `name` |
| Люди | `Person.public.published().exclude(name_visibility=HIDDEN).filter(...)` | `-rank`, `last_name`, `first_name` |
| Фотографии | `visible_photos().filter(search_vector=query)` | `-rank`, `-created_at` |

Ранжирование: `annotate(rank=SearchRank(F("search_vector"), query))`.
`distinct()` не нужен: текст лежит в `search_vector`, join'ов нет —
варианты имён попадают в текст человека при сборке индекса, а не через
связь в запросе.

### 3.4 Подсказки «возможно, вы искали»

Сравнение по безопасным полям через `Greatest(TrigramSimilarity(...))`
с порогом `SIMILARITY_THRESHOLD = 0.3`:

| Модель | Поля сравнения | Условие приватности |
|---|---|---|
| `Team` | `name`, `city` | — |
| `Event` | `name`, `city` | — |
| `Person` | `first_name` всегда; `last_name` — через `Case(When(name_visibility=FULL, then=...), default=0)` | `exclude(name_visibility=HIDDEN)` |
| `PersonName` | `value` | `person__in=Person.public.published()` и `person__name_visibility=FULL` |

Результаты трёх источников складываются в словарь по pk (побеждает
максимальная похожесть), затем сортировка по убыванию и срез
`SUGGESTION_LIMIT`.

### 3.5 Состояния страницы

| Условие | Что показывает |
|---|---|
| `q` пустой | Форма и подсказка «что можно искать» (фамилия, команда, турнир, год) |
| `q` непустой, слов не осталось | «Ничего не найдено» без блока подсказок |
| Есть результаты | Группы с карточками и счётчиками |
| Результатов нет | «Ничего не найдено» + «Возможно, вы искали» |

---

## 4. Шаги

1. [x] **Слой выдачи `apps/search/services.py`**
    - Навыки: koda-coder
    - Константы: `GROUP_LIMIT = 20`, `SUGGESTION_LIMIT = 5`,
      `SIMILARITY_THRESHOLD = 0.3`, `MAX_QUERY_LENGTH = 100`.
    - `normalize_query(raw)` — обрезка и `strip()`; вызывается представлением
      до `build_search_query()`.
    - `SearchGroup`, `SearchResult`, `Suggestion` — dataclass'ы.
    - `search_page(query, text)` — группы по таблице 3.3, общий `total`,
      подсказки только при нулевом итоге.
    - `suggestions(text)` — по таблице 3.4, дедупликация по pk.
    - Модели импортируются **внутри** функций: на уровне модуля это дало бы
      зависимость `search` → `catalog`/`photos` (правило `shortcuts.py`).
    - Файлы: `apps/search/services.py`

2. [x] **Маршрут и представление: перенос `/search/` из `core` в `search`**
    - Навыки: koda-coder
    - `apps/search/urls.py`: `app_name = "search"`, `path("search/", views.index,
      name="index")`; подключение в `config/urls.py` рядом с другими include.
    - `apps/search/views.py`: `index` (`require_GET`) — `q` из `request.GET`,
      `normalize_query()`, `build_search_query()`; состояния по таблице 3.5;
      `page_meta(request, title=_("Поиск"), noindex=True)`; крошки
      «Главная → Поиск»; `jsonld_blocks` не задаём.
    - Обновление ссылок `core:search` → `search:index`: `templates/partials/navbar.html`,
      `templates/partials/footer.html`, `templates/core/home.html`,
      `apps/core/schema.py` (`website_schema`, `urlTemplate` для `SearchAction`).
    - Удаление заглушки: маршрут из `apps/core/urls.py`, `views.placeholder()`
      и `views.search()` из `apps/core/views.py`, файл
      `templates/core/placeholder.html`.
    - Проверить, что после удаления не осталось неиспользуемых импортов
      (`ruff` ловит `F401`).
    - Файлы: `apps/search/urls.py`, `apps/search/views.py`, `config/urls.py`,
      `apps/core/urls.py`, `apps/core/views.py`, `apps/core/schema.py`,
      `templates/partials/navbar.html`, `templates/partials/footer.html`,
      `templates/core/home.html`; удаляется `templates/core/placeholder.html`

3. [x] **Шаблоны страницы результатов**
    - Навыки: koda-coder
    - `templates/search/index.html`: форма поиска со значением `q` (её
      отсутствие на странице результатов не дало бы уточнить запрос),
      три состояния по таблице 3.5, счётчик «Найдено N».
    - `templates/search/partials/group.html`: заголовок группы, счётчик,
      сетка карточек через `{% include group.template %}`, подпись
      «показаны первые N из M» при `group.truncated`.
    - `templates/search/partials/suggestions.html`: список ссылок
      «название — тип» с пометкой, что точных совпадений нет.
    - `templates/catalog/partials/person_card.html`: `public_name`, годы
      жизни. Стиль — общий `.entity-card` (как у карточек команды
      и события): отдельная сетка стилей не нужна. `display_name` не выводится
      никогда.
    - Карточки команд, событий и фото переиспользуются как есть.
    - Файлы: `templates/search/index.html`,
      `templates/search/partials/group.html`,
      `templates/search/partials/suggestions.html`,
      `templates/catalog/partials/person_card.html`

4. [x] **Стили и локализация**
    - Навыки: koda-coder
    - `static/css/sportmemo.css`: `.search-page`, `.search-page__form`,
      `.search-group`, `.search-group__head`, `.search-group__count`,
      `.search-group__note`, `.search-suggestions`; мобильный блок
      в `@media (max-width: 575.98px)`.
    - Новые строки через `gettext`: заголовок страницы, подсказка при пустом
      запросе, `blocktranslate count` для «Найдено N», подписи групп
      («Люди» и «Фотографии» — с заглавной буквы, в моделях они строчными),
      «Ничего не найдено», «Возможно, вы искали», «Показаны первые N из M»,
      подписи типов для подсказок.
    - `make i18n`, затем: снять `fuzzy`-флаги (makemessages ставит их, найдя
      похожую старую строку), выровнять `msgstr` по `msgid` в `locale/ru`,
      заполнить `locale/en` вручную (автоперевод не используем).
    - Файлы: `static/css/sportmemo.css`, `locale/ru/LC_MESSAGES/django.po`,
      `locale/en/LC_MESSAGES/django.po`

5. [x] **Тесты `tests/test_search_ui.py` и правки существующих**
    - Навыки: koda-coder
    - `skipif(connection.vendor != "postgresql")` на модуль: поиск работает
      только на PostgreSQL (правило 2.12).
    - Пустой `q`: страница открывается, заглушки нет, поиск не выполняется,
      форма есть, `noindex` в HTML.
    - Результаты: команда по части слова, событие по описанию, человек
      по имени, человек по варианту имени (`PersonName`), фото по подписи;
      счётчики групп.
    - Приватность: `hidden` не находится; при `first_name_only` фамилия
      не находится, а имя находится; черновики, скрытое, отклонённое,
      удалённое и слитое не выдаются **ни одному** пользователю, включая
      автора (решение 5); фото без подтверждённых прав не выдаётся.
    - Подсказки: появляются только при пустом итоге; лимит 5; `hidden`
      не предлагается; при `first_name_only` не предлагается фамилия,
      но предлагается имя.
    - Лимит группы: 25 команд → 20 в выдаче, `total` = 25, подпись об обрезке.
    - Гигиена запроса: длинный `q` не падает; `!!!` даёт «ничего не найдено»;
      `<script>` в `q` не ломает страницу (экранирование в поле формы).
    - Ссылки: `reverse("search:index") == "/search/"`, ссылки в navbar,
      на главной и в футере ведут на новый маршрут, карточки — на страницы
      объектов.
    - Правки существующих тестов: `tests/test_home.py` (убрать `PLACEHOLDERS`
      и тесты заглушки, `core:search` → `search:index`),
      `tests/test_public_lists.py` (`test_search_is_still_a_placeholder` →
      проверка настоящей страницы), `tests/test_meta_tags.py` и
      `tests/test_seo.py` (переименование маршрута).
    - Файлы: `tests/test_search_ui.py`, `tests/test_home.py`,
      `tests/test_public_lists.py`, `tests/test_meta_tags.py`,
      `tests/test_seo.py`

6. [x] **Проверка**
    - Навыки: koda-coder
    - `make lint`, `make test`, `uv run python manage.py makemigrations
      --check --dry-run` (миграций быть не должно).
    - Ручная проверка на `make seed-demo` + `make run`: частичное слово,
      опечатка (блок подсказок), пустой запрос, мусорный запрос, русская
      и английская локали, мобильное разрешение.
    - Файлы: —

7. [x] **Документация и версия**
    - Навыки: koda-coder, semver-checker
    - `README.md`: раздел «Поиск» (страница результатов, группы, подсказки,
      `noindex`), таблица URL (`/search/` → `search:index`), число тестов.
    - `KODA.md` + `AGENTS.md`: правила — «поиск отдаёт только публичное
      и не показывает черновики даже автору», «подсказки сравнивают только
      безопасные поля: `first_name` всегда, `last_name` и варианты имён —
      при полной видимости», «страница поиска живёт в `apps/search`».
    - Мастер-план `public-archive.md`: подэтап 2.13 → `[x]`, раздел «Итог»,
      таблица «Прогресс»; бэклог: P2-6 → готово.
    - Версия: 0.13.0 → 0.14.0 (minor) в `pyproject.toml`, затем `uv lock`.
    - Файлы: `README.md`, `KODA.md`, `AGENTS.md`, `.koda/plans/public-archive.md`,
      `.koda/backlog.md`, `pyproject.toml`, `uv.lock`

---

## 5. Критерии готовности

- `/search/` открывается анонимно; пустой запрос не выполняет поиск
  и показывает форму с подсказкой.
- Поиск находит команду по части слова, событие по описанию, человека
  по имени и по варианту имени, фото по подписи.
- Выдача содержит только опубликованное и видимое; черновик не видит даже
  автор; фото без подтверждённых прав не выдаётся.
- При `hidden` человек не находится и не предлагается; при `first_name_only`
  фамилия не находится и не предлагается.
- При пустой выдаче появляются подсказки (не больше пяти), при непустой —
  их нет.
- Длинный и мусорный запросы не роняют страницу; `<script>` в запросе
  не ломает разметку.
- Все ссылки на поиск (navbar, главная, футер, `SearchAction`) ведут
  на `search:index`; заглушка `core:search` и её шаблон удалены.
- `make lint && make test` чистые; миграций нет; версия 0.14.0; документация
  и планы обновлены.

---

## 6. Результат

**Завершён 2026-10-16, v0.14.0 (minor).**

- `apps/search/services.py`: `normalize_query()`, `search_page()`, `suggestions()`,
  dataclass'ы `SearchGroup` / `SearchResult` / `Suggestion`; константы `GROUP_LIMIT = 20`,
  `SUGGESTION_LIMIT = 5`, `SIMILARITY_THRESHOLD = 0.3`, `MAX_QUERY_LENGTH = 100`.
- `apps/search/views.py` + `apps/search/urls.py` (`search:index`), подключение
  в `config/urls.py`; заглушка удалена (`core:search`, `views.placeholder()`,
  `templates/core/placeholder.html`); ссылки обновлены в navbar, футере, на главной
  и в `SearchAction` микроразметки.
- Шаблоны `templates/search/index.html`, `templates/search/partials/group.html`,
  `templates/search/partials/suggestions.html`, `templates/catalog/partials/person_card.html`;
  стили `.search-page*`, `.search-group*`, `.search-suggestions*` + мобильный блок;
  локализация ru/en (10 новых строк).
- `tests/test_search_ui.py` — 64 теста; правки `test_home.py`, `test_public_lists.py`,
  `test_meta_tags.py`, `test_seo.py`. Всего 799 тестов, линтер чист, миграций нет.

**Найденная особенность.** `TrigramSimilarity` требует литерал, обёрнутый в `Value(...)`:
без обёртки параметр не подставляется и похожесть всегда равна нулю. Обнаружено отладкой
на этапе тестов подсказок.

**Отклонение от плана.** Подсветка совпадений отложена в бэклог как идея I-11 (решение 2
утверждено владельцем до начала работ).

---

## 7. Риски и известные ограничения

| Риск / ограничение | Что делаем |
|---|---|
| Утечка скрытой фамилии через подсказки (триграммный индекс хранит «сырую» фамилию) | Сравнение только по безопасным полям; `last_name` и `PersonName` — под условием `name_visibility=FULL`; `hidden` исключён. Проверяется тестами |
| Черновик в выдаче по случайному запросу | Поиск идёт только через `public.published()` / `visible_photos()` и не даёт поблажек автору (решение 5) |
| Долгий запрос на большом архиве | `GROUP_LIMIT` и `SUGGESTION_LIMIT` ограничивают выдачу, `MAX_QUERY_LENGTH` — запрос; GIN-индексы из 2.12 покрывают обе ветки |
| Триграммные запросы дороже полнотекстовых | Подсказки выполняются только при полностью пустой выдаче |
| XSS через подсветку | Подсветка отложена (решение 2, идея I-11) — сейчас в выдаче только экранированные значения |
| Поиск не работает на SQLite | `skipif` по вендору + запись в документации: проект работает на PostgreSQL |
| Перенос маршрута ломает забытую ссылку | `NoReverseMatch` уронит страницу сразу; футер рендерится на всех страницах, поэтому забытое место найдётся на первом же прогоне тестов |

---

## 8. Ссылки

- Мастер-план этапа 2: [`public-archive.md`](public-archive.md), подэтап 2.13
- Предыдущий подэтап: [`public-archive-2.12-search-index.md`](public-archive-2.12-search-index.md)
- Правила публичных выборок: `apps/core/shortcuts.py`
- Сборка индекса и запроса: `apps/search/indexing.py`
