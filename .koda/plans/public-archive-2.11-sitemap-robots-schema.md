# SportMemo — подэтап 2.11: sitemap, robots и Schema.org

Дата создания: 2026-10-15
Статус: **выполнен (2026-10-15)**
Мастер-план: [`public-archive.md`](public-archive.md) (подэтап 2.11)
Бэклог: [`.koda/backlog.md`](../backlog.md) — задача P2-5
Ветка: `feature/sitemap-robots-schema`
База: `master`, версия 0.11.0
Финал: bump **0.11.0 → 0.12.0 (minor)** + `uv lock`

---

## 1. Цель подэтапа

Сделать так, чтобы поисковики (Яндекс, Google) находили все страницы архива и понимали,
что на них написано.

Три вещи, каждая решает свою задачу:

1. **Карта сайта** (`/sitemap.xml`) — список всех публичных адресов с датой последнего
   изменения. Поисковик обходит сайт по ссылкам, но архив связан слабо: страницу человека
   видно только с фото или из состава команды, фото — со страницы года. Без карты половина
   архива просто не попадёт в индекс. Карта — это ещё и сигнал «страница обновилась»:
   без него поисковик приходит перепроверять страницу через месяцы.
2. **`robots.txt`** — просьба к роботам не ходить в служебные разделы (админка, вход,
   регистрация, поиск) и указание, где лежит карта сайта.
3. **Микроразметка Schema.org** (JSON-LD) — машинное описание страницы («это спортивная
   команда, название „Соколы“, город Москва»). Люди его не видят, поисковик читает как факт
   и может показать карточкой в расширенной выдаче. Для архива фотографий отдельный канал —
   поиск по картинкам.

**Что уже есть:** метатеги, `canonical`, `og:*` и `noindex` на служебных страницах
(подэтап 2.10, `apps/core/seo.py`). Поисковикам страницы уже понятны, но карты сайта
и микроразметки нет.

**Что важно помнить.** `robots.txt` — вежливая просьба, а не защита: страницу можно открыть
по прямому адресу. Настоящая защита черновиков у нас другая — `get_visible_or_404` отдаёт
посторонним 404. Карта сайта и разметка тоже содержат только то, что и так публично.

---

## 2. Решения, согласованные с владельцем (2026-10-15)

| # | Вопрос | Решение |
|---|---|---|
| 1 | Структура карты сайта | **Индекс + секции.** `/sitemap.xml` — оглавление, рядом `/sitemap-teams.xml`, `/sitemap-events.xml`, `/sitemap-people.xml`, `/sitemap-photos.xml`, `/sitemap-pages.xml`. У каждой группы свой `lastmod`, пагинация Django режет большие списки сама (лимит стандарта — 50 000 адресов) |
| 2 | Домен в `robots.txt` и карте сайта | Домен из `django.contrib.sites` (`sportmemo.ru`, задан миграцией `apps/core/migrations/0003_site_domain.py`), схема — из запроса. Не `request.get_host()`: за сайтом стоит nginx, и тогда адрес зависел бы от настроек прокси, а не от проекта |
| 3 | `BreadcrumbList` в микроразметке | **Не делаем.** Крошки уже есть в HTML обычными ссылками, Google их читает. Отдельный блок даёт мало, а работы добавляет. Мастер-план его не требует |
| 4 | Человек со скрытым именем | `Person` выводится при `is_full_name_public` (полное имя) и при `first_name_only` (только имя); при `hidden` блок не выводится вовсе. Фамилия в разметку не попадает ни при каком варианте — проверяется тестом |
| 5 | Годы жизни в разметке | **Не выводим.** В модели только год (`birth_year`), а `birthDate` по стандарту — дата. Неполная дата даёт предупреждения валидатора, а годы и так видны в HTML |
| 6 | `/accounts/` в `robots.txt` | **Закрываем раздел целиком** одной строкой, а не перечисляем страницы: там вход, регистрация, сброс пароля и профиль, индексировать нечего, а список не устареет при появлении новой страницы аккаунта |

---

## 3. Архитектура решения

### 3.1 Карта сайта

`django.contrib.sitemaps` — штатный механизм Django. Класс `Sitemap` описывает, какие объекты
попадают в карту, откуда берётся адрес и дата изменения.

**Что и куда попадает:**

| Секция | Адрес | Источник | `lastmod` |
|---|---|---|---|
| `pages` | `/sitemap-pages.xml` | `reverse()` главной, `/teams/`, `/events/`, `/years/` | нет (статические страницы) |
| `teams` | `/sitemap-teams.xml` | `Team.public.published()` | `updated_at` |
| `events` | `/sitemap-events.xml` | `Event.public.published()` | `updated_at` |
| `people` | `/sitemap-people.xml` | `Person.public.published()` | `updated_at` |
| `photos` | `/sitemap-photos.xml` | `visible_photos()` (`published` + `rights_confirmed`) | `updated_at` |

Правило публичности берётся из `apps/core/shortcuts.py`, а не собирается заново: список
в карте сайта и список на странице обязаны совпадать. Черновики, скрытое, отклонённое,
удалённое и слитое в карту не попадают.

`i18n = False` обязателен: у нас нет языковых префиксов в адресах, и `i18n = True`
продублировал бы каждый адрес по числу языков в `LANGUAGES`.

**Маршруты.** Имена маршрутов заданы не нами, а Django: `views.index` строит ссылки на секции
через `reverse("django.contrib.sitemaps.views.sitemap", kwargs={"section": ...})`. Имена
менять нельзя — иначе индекс отдаст битые ссылки.

**`django.contrib.sitemaps` добавляется в `INSTALLED_APPS`.** Не ради моделей, а ради
шаблонов `sitemap.xml` и `sitemap_index.xml` из пакета: без приложения в `INSTALLED_APPS`
Django их не найдёт и упадёт с `TemplateDoesNotExist`. Свои шаблоны не копируем —
штатные корректны и обновляются вместе с Django.

### 3.2 `robots.txt`

Отдаётся Django (`templates/robots.txt` + представление), а не nginx: адрес карты сайта
и список закрытых разделов живут рядом с кодом и попадают в репозиторий.

Содержимое:

- `User-agent: *`
- `Disallow:` `/admin/`, `/accounts/`, `/search/`, `/healthz/`, `/teams/new/`, `/teams/mine/`
- `Sitemap:` абсолютный адрес индекса, домен из Sites + схема из запроса

### 3.3 Микроразметка (JSON-LD)

**Ключевой принцип — тот же, что у метатегов в 2.10:** словари собираются в Python
(`apps/core/schema.py`), а разметка их только выводит. Причина та же: правила приватности
имени и выбора картинки должны жить в одном месте, а не расползаться по шаблонам.

| Страница | Блок Schema.org |
|---|---|
| Главная | `WebSite` + `SearchAction` (поисковая строка сайта в выдаче Google) |
| Команда | `SportsTeam` — название, адрес, эмблема, город, год основания, вид спорта |
| Событие | `SportsEvent` — название, даты, место, адрес |
| Человек | `Person` — только имя (без фамилии, если она скрыта) |
| Фото | `ImageObject` — файл, размеры, название, описание |
| Остальные | разметки нет |

**Вывод в HTML.** Тег `{% jsonld %}` (`apps/core/templatetags/jsonld.py`) сериализует словарь
и оборачивает его в `<script type="application/ld+json">`. Опасные символы (`<`, `>`, `&`)
и разделители строк U+2028/U+2029 заменяются на `\uXXXX`: без этого название вида
`</script>` или название с переносом строки сломало бы страницу. Партиал
`templates/partials/jsonld.html` подключается в `base.html` рядом с `partials/meta.html`
и на страницах без разметки выводит пустоту.

**Отклонение от мастер-плана:** там указаны отдельные шаблоны
`templates/partials/jsonld/*.html`. Делаем один партиал: данные и так собираются в Python,
и плодить пять шаблонов с одним и тем же `<script>` незачем.

**Черновики и непубличное.** Разметка задаётся только там, где страница публична:
у черновика команды, события, человека и фото (автор и модератор их видят) блок не выводится.

---

## 4. Шаги

1. [x] **Sitemap: модуль и маршруты**
    - Навыки: koda-coder
    - `apps/core/sitemaps.py`:
        - `StaticSitemap` — главная, `/teams/`, `/events/`, `/years/` через `reverse()`,
          без `lastmod`;
        - `TeamSitemap`, `EventSitemap`, `PersonSitemap`, `PhotoSitemap` — выборки через
          `Team.public.published()`, `Event.public.published()`, `Person.public.published()`
          и `visible_photos()`; `lastmod(item) -> item.updated_at`;
        - модели импортируются **внутри** `items()`: на уровне модуля это дало бы
          зависимость `core` → `catalog`/`photos` (правило `shortcuts.py`);
        - `i18n = False` у всех классов.
    - `config/settings.py`: `"django.contrib.sitemaps"` в `DJANGO_APPS`.
    - `config/urls.py`: словарь `SITEMAPS`, маршрут индекса (`views.index`) и маршрут секций
      (`views.sitemap`) с именами `django.contrib.sitemaps.views.index` и
      `django.contrib.sitemaps.views.sitemap`.
    - Файлы: `apps/core/sitemaps.py`, `config/settings.py`, `config/urls.py`

2. [x] **`robots.txt`**
    - Навыки: koda-coder
    - `apps/core/views.py`: представление `robots_txt` (`require_GET`, `content_type="text/plain"`),
      домен берёт из `get_current_site(request)`, схему — из `request.scheme`.
    - `templates/robots.txt`: закрытые разделы и абсолютный адрес карты сайта.
    - `config/urls.py`: маршрут `robots.txt` в корне, вне namespace.
    - Файлы: `apps/core/views.py`, `templates/robots.txt`, `config/urls.py`

3. [x] **Микроразметка: модуль сборки и вывод в разметку**
    - Навыки: koda-coder
    - `apps/core/schema.py`: `website_schema()`, `team_schema()`, `event_schema()`,
      `person_schema()`, `photo_schema()`; абсолютные адреса — через `seo.absolute()`;
      функции возвращают `dict` или `None`; `compact(*blocks)` выбрасывает пустые.
    - `apps/core/templatetags/jsonld.py`: тег `{% jsonld data %}` — `json.dumps` с
      `ensure_ascii=False` и `DjangoJSONEncoder`, замена `<`, `>`, `&`, U+2028, U+2029
      на `\uXXXX`, обёртка в `<script type="application/ld+json">`.
    - `templates/partials/jsonld.html`: цикл по блокам, пустой вывод без данных.
    - `templates/base.html`: подключение партиала после `partials/meta.html`.
    - Файлы: `apps/core/schema.py`, `apps/core/templatetags/jsonld.py`,
      `templates/partials/jsonld.html`, `templates/base.html`

4. [x] **Микроразметка на публичных страницах `core`**
    - Навыки: koda-coder
    - `home`: `WebSite` + `SearchAction` (`urlTemplate` ведёт на `/search/?q={search_term_string}`).
    - `year_list`, `year_detail`: разметки нет — блок не задаётся, партиал пуст.
    - Файлы: `apps/core/views.py`

5. [x] **Микроразметка на страницах `catalog`**
    - Навыки: koda-coder
    - `team_detail` — `SportsTeam` (только для опубликованной команды);
    - `event_detail` — `SportsEvent` (даты выводятся, если они есть; только для
      опубликованного события);
    - `person_detail` — `Person` при `is_full_name_public` и при `first_name_only`
      (только имя), при `hidden` блока нет.
    - Файлы: `apps/catalog/views.py`

6. [x] **Микроразметка на странице фото**
    - Навыки: koda-coder
    - `photo_detail` — `ImageObject` (`contentUrl` — preview, иначе оригинал; название,
      описание, размеры, адрес страницы); только для `photo.is_public`.
    - Файлы: `apps/photos/views.py`

7. [x] **Тесты `tests/test_seo.py`**
    - Навыки: koda-coder
    - Карта сайта: индекс отдаёт 200 и ссылки на все секции; в секциях есть опубликованные
      объекты и нет черновиков, скрытых, отклонённых, удалённых, слитых и фото без
      подтверждённых прав; `lastmod` на месте; XML разбирается `xml.etree.ElementTree`
      и лежит в namespace `http://www.sitemaps.org/schemas/sitemap/0.9`.
    - `robots.txt`: код 200, `text/plain`, ожидаемые `Disallow`, абсолютный `Sitemap`.
    - Микроразметка: блоки разбираются `json.loads`, типы и поля на месте; на служебных
      страницах (вход, поиск) блоков нет; фамилия отсутствует при `first_name_only`
      и `hidden`; кавычки, кириллица и `</script>` в названии не ломают страницу.
    - Файлы: `tests/test_seo.py`

8. [x] **Проверка**
    - Навыки: koda-coder
    - `make lint && make test`; если появились новые строки — `make i18n` (снять `fuzzy`,
      дописать переводы en вручную).
    - Ручная проверка: `/sitemap.xml`, `/sitemap-teams.xml`, `/sitemap-photos.xml`,
      `/robots.txt`, `<head>` главной, команды, события, человека и фото; микроразметка —
      через валидатор Schema.org.
    - Файлы: —

9. [x] **Документация и версия**
    - Навыки: koda-coder, semver-checker
    - `README.md` (состояние и число тестов), `KODA.md` + `AGENTS.md` (правило: микроразметка
      собирается в `apps/core/schema.py`, карта сайта отдаёт только публичное, `robots.txt`
      и `/sitemap.xml` обслуживает Django), мастер-план `public-archive.md` (2.11 → `[x]`,
      раздел «Итог», таблица «Прогресс»), бэклог (P2-5 → готово, следующий — 2.12).
    - Версия: 0.11.0 → 0.12.0 (minor) в `pyproject.toml`, затем `uv lock`.
    - Файлы: `README.md`, `KODA.md`, `AGENTS.md`, `.koda/plans/public-archive.md`,
      `.koda/backlog.md`, `pyproject.toml`, `uv.lock`

---

## 5. Критерии готовности

- `/sitemap.xml` открывается и ведёт на пять секций; каждая секция валидна как XML
  и содержит только публичные объекты.
- `/robots.txt` отдаётся как текст, закрывает служебные разделы и указывает адрес карты сайта.
- На главной, странице команды, события, человека и фото есть блоки JSON-LD корректного
  синтаксиса; на служебных страницах их нет.
- Фамилия человека со скрытым именем не появляется в микроразметке — проверяется тестом.
- Название с кавычками, кириллицей или `</script>` не ломает разметку страницы.
- `make lint && make test` чистые, переводы ru/en не потеряны.
- Версия 0.12.0, документация и планы обновлены.

---

## 6. Риски и известные ограничения

| Риск / ограничение | Что делаем |
|---|---|
| Забыть `django.contrib.sitemaps` в `INSTALLED_APPS` | Штатные шаблоны пакета не найдутся, упадёт `TemplateDoesNotExist`. Проверяем сразу после шага 1 |
| Переименовать маршруты sitemap «по-своему» | `views.index` строит ссылки на секции по фиксированным именам Django. Имена оставляем как есть |
| `i18n = True` в классе `Sitemap` | Продублировал бы каждый адрес по числу языков в `LANGUAGES`. Ставим `False` |
| Слияние фото (`duplicate_of`) не учитывается | `Photo.merge_into` — этап 3, второго правила публичности не заводим. Отмечено как известное ограничение |
| Микроразметка «протечёт» на служебные страницы | Блоки задаются только во вьюхах публичных страниц; дефолт — пустой список |
| nginx перехватит `/sitemap.xml` или `/robots.txt` | Оба адреса отдаёт Django. На этапе 4 (деплой) учесть в конфиге nginx |
| `robots.txt` воспринимают как защиту | В плане и документации прямо написано: это просьба, а не замок. Черновики защищает `get_visible_or_404` |

---

## 7. Ссылки

- Мастер-план этапа 2: [`public-archive.md`](public-archive.md), подэтап 2.11
- Предыдущий подэтап: [`public-archive-2.10-meta-tags.md`](public-archive-2.10-meta-tags.md)
- Правила публичных выборок: `apps/core/shortcuts.py`
- Метатеги и `noindex`: `apps/core/seo.py`, `templates/partials/meta.html`
