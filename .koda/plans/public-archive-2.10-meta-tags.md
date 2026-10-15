# SportMemo — подэтап 2.10: метатеги и Open Graph

Дата создания: 2026-10-14
Статус: **завершён** (2026-10-15, версия 0.11.0, ветка `feature/meta-tags`)
Мастер-план: [`public-archive.md`](public-archive.md) (подэтап 2.10)
Бэклог: [`.koda/backlog.md`](../backlog.md) — задача P2-4
Ветка: `feature/meta-tags`
База: `master`, версия 0.10.0
Финал: bump **0.10.0 → 0.11.0 (minor)** + `uv lock`

---

## 1. Цель подэтапа

Сделать так, чтобы ссылка на любую страницу архива красиво разворачивалась в мессенджерах
(Telegram, WhatsApp, VK) и была понятна поисковикам.

Что это даёт на практике: сейчас ссылка `sportmemo.ru/photos/12/`, отправленная в командный
чат, выглядит как голый адрес. После подэтапа вместо адреса появится карточка с фотографией,
заголовком и подписью. Пересылка ссылок в чатах — основной канал, по которому такой архив
будет расходиться; без карточек его почти не нажимают.

**Что уже есть:** обычные метатеги `title` и `description` (сделаны в 2.2–2.9, задаются
блоками в 14 шаблонах). Это работает для поисковиков.

**Чего нет:** Open Graph (`og:*`) для мессенджеров, `canonical` (метка «главного» адреса
страницы) и `noindex` (запрет индексации служебных страниц).

---

## 2. Решения, согласованные с владельцем (2026-10-14)

| # | Вопрос | Решение |
|---|---|---|
| 1 | Картинка-заглушка `og:image` | Генерируем статичный PNG 1200×630: сепийный фон, надпись «SportMemo», подпись «архив спортивных фотографий». Кладём в репозиторий (`static/img/og-default.png`), CDN не используем |
| 2 | `canonical` | Абсолютный URL без параметра `page`. Значимые фильтры (`?year=1994`, `?team=...`) сохраняются. При `?page>1` — `canonical` на первую страницу + `noindex, follow` |
| 3 | `og:type` | `article` для страницы фото, `website` для всех остальных |
| 4 | Картинки объектов | фото → `preview` (1280), команда → `logo` (эмблема), человек → главное фото, остальные страницы (списки, годы, события, главная) → заглушка. Фотография человека со скрытой фамилией **показывается** в карточке (она и так публичная), но фамилия в заголовок и описание не попадает — это гарантируется кодом и проверяется тестами |
| 5 | `noindex` | Черновики и неопубликованное, `?page>1`, `/search/`, страницы аккаунта (вход, регистрация, пароль, профиль), страницы 404 и 500 |
| 6 | Источник метатегов | `title`/`description` переносятся из шаблонов во вьюхи: единая функция `page_meta()` в `apps/core/seo.py`. Логика приватности имени живёт в одном месте и покрыта тестами |

---

## 3. Архитектура решения

### 3.1 Ключевой механизм

Все метатеги собираются в Python, во вьюхе, и передаются в шаблон одним словарём `meta`.
Партиал `templates/partials/meta.html` только выводит готовые значения — никакой логики
в разметке.

**Почему не в шаблонах.** Картинка для карточки зависит от данных страницы (у фото одно поле,
у команды другое, у человека третье), плюс есть правило приватности («при скрытой фамилии
взять нейтральное имя»). Логика, размазанная по 14 шаблонам, рано или поздно даст утечку
фамилии или битую картинку в одном из них.

### 3.2 Разделение источников

| Страницы | Источник `title`/`description` | Источник `og:*` |
|---|---|---|
| Публичные (главная, списки, команда, событие, человек, фото, годы, поиск, профиль, формы команды) | словарь `meta` из вьюхи | словарь `meta` |
| Аккаунт allauth (вход, регистрация, пароль), 404, 500 | блоки `{% block title %}` / `{% block description %}` — как сейчас | не выводятся: эти страницы под `noindex`, карточки в мессенджерах для них не нужны |

Блочная схема для allauth сохраняется сознательно: `head_title` — это блок шаблона,
из Python его не видно, а переписывать 12 шаблонов аккаунта ради `og:title` незачем.

### 3.3 Дефолт и приоритет

Контекстный процессор `seo_meta` отдаёт словарь `meta = {"noindex": True}` — безопасный
дефолт для всего, что не задало свой `meta` явно (аккаунт, 404, 500). Публичные вьюхи
передают свой `meta` в контекст, и он **перекрывает** процессор.

Это проверенный факт, а не предположение: в `django.template.context.Context.__init__`
контекст вьюхи лежит в `dicts[1]`, вывод процессоров — в `dicts[2]`, а обход идёт
`reversed(self.dicts)`. Поэтому `render(request, ..., {"meta": ...})` побеждает процессор.

**Важно:** если вьюха передаёт `meta`, он должен быть полным (из `page_meta()`), а не
частичным — `{{ meta.title }}` при частичном словаре даст пустоту, а не дефолт.

### 3.4 Разметка `base.html`

```html
<title>{% block title %}{{ meta.title|default:SPORTMEMO_SITE_NAME }}{% endblock %}</title>
<meta name="description" content="{% block description %}{{ meta.description }}{% endblock %}">
{% include "partials/meta.html" %}
```

- Публичные шаблоны **перестают** переопределять `title`/`description` — значения приходят
  из `meta`.
- `templates/allauth/layouts/base.html`, `templates/404.html`, `templates/500.html`
  блоки переопределяют, как и раньше.
- `partials/meta.html` выводит `canonical`, `robots` и блок `og:*`/`twitter:card`
  только при наличии `meta.og_title`.

---

## 4. Шаги

1. [x] **Модуль SEO-утилит `apps/core/seo.py`**
    - Навыки: koda-coder
    - Содержимое:
        - `SITE_NAME`, `DEFAULT_DESCRIPTION`, `OG_DEFAULT_IMAGE = "img/og-default.png"`,
          `DESCRIPTION_LIMIT = 160`;
        - `absolute(request, url)` — абсолютный URL через `request.build_absolute_uri`;
        - `canonical_url(request)` — путь без параметра `page`, остальные параметры
          сохраняются (собирается из `request.GET.copy()`);
        - `is_paginated_beyond_first(request)` — признак `?page>1` для `noindex`;
        - `image_meta(request, image)` — словарь `{"url", "alt", "width", "height"}` или
          `None`, если у объекта картинки нет; работает и с `ImageField`, и с `ImageSpecField`;
        - `page_meta(request, *, title="", description="", image=None, image_alt="",
          og_type="website", noindex=False)` — собирает полный словарь `meta`, добавляет
          суффикс «— SportMemo» к заголовку, обрезает описание до 160 символов,
          подставляет заглушку, если картинки нет, выставляет `noindex` автоматически
          при `?page>1`;
        - `og_locale()` — `ru_RU` / `en_US` из активной локали.
    - Файлы: `apps/core/seo.py`

2. [x] **Заглушка `og:image` — `static/img/og-default.png`**
    - Навыки: koda-coder
    - Генерируем одноразовым скриптом через Pillow (он уже в зависимостях, используется
      в `seed_demo`): 1200×630, сепийный фон из палитры сайта, шрифт DejaVu Sans
      (есть кириллица), надпись «SportMemo» и подпись «архив спортивных фотографий».
      Готовый файл кладём в репозиторий, скрипт в проект не коммитим.
    - Файлы: `static/img/og-default.png`

3. [x] **Контекстный процессор `seo_meta`**
    - Навыки: koda-coder
    - Отдаёт `{"meta": {"noindex": True}}` — дефолт для страниц без своего `meta`.
      Подключается в `TEMPLATES["OPTIONS"]["context_processors"]` после `project_info`.
    - Файлы: `apps/core/context_processors.py`, `config/settings.py`

4. [x] **Партиал `templates/partials/meta.html` и правка `base.html`**
    - Навыки: koda-coder
    - Партиал выводит: `<link rel="canonical">`, `<meta name="robots" content="noindex, follow">`
      (при `meta.noindex`), `og:site_name`, `og:locale`, `og:type`, `og:title`,
      `og:description`, `og:url`, `og:image`, `og:image:alt`, `twitter:card`
      (`summary_large_image`). OG-блок — только при `meta.og_title`.
    - `base.html`: `title`/`description` берут значения из `meta` с прежними блоками
      как фолбэком, рядом подключается партиал.
    - Файлы: `templates/partials/meta.html`, `templates/base.html`

5. [x] **Метатеги страниц `core`: главная, годы, поиск**
    - Навыки: koda-coder
    - `home` (картинка — заглушка, `website`), `year_list`, `year_detail` (описание с годом),
      `search` и `placeholder` (`noindex, nofollow`). Из шаблонов убираем блоки
      `title`/`description`.
    - Файлы: `apps/core/views.py`, `templates/core/home.html`,
      `templates/core/year_list.html`, `templates/core/year_detail.html`,
      `templates/core/placeholder.html`

6. [x] **Метатеги публичных страниц `catalog`**
    - Навыки: koda-coder
    - `team_list`, `event_list` — `website`, заглушка;
    - `team_detail` — картинка `team.logo`, `og:type=website`, `noindex` для черновика
      (у автора и модератора страница открывается);
    - `event_detail` — `website`, заглушка;
    - `person_detail` — картинка главного фото (`hero_photo.preview`), `website`,
      `og:title` и описание **только из `public_name`**; при `is_full_name_public = False`
      — нейтральное описание сайта. Из шаблонов убираем блоки `title`/`description`.
    - Файлы: `apps/catalog/views.py`, `templates/catalog/team_list.html`,
      `templates/catalog/event_list.html`, `templates/catalog/team_detail.html`,
      `templates/catalog/event_detail.html`, `templates/catalog/person_detail.html`

7. [x] **Метатеги закрытых страниц `catalog`: формы команды и «мои команды»**
    - Навыки: koda-coder
    - `team_create`, `team_update`, `my_teams` — `noindex, nofollow`.
    - Файлы: `apps/catalog/views.py`, `templates/catalog/team_form.html`,
      `templates/catalog/my_teams.html`

8. [x] **Метатеги страницы фото**
    - Навыки: koda-coder
    - `photo_detail`: `og:type=article`, `og:image` — `photo.preview`, `image_alt` из
      заголовка или подписи, описание обрезано до 160. Из шаблона убираем блоки
      `title`/`description`.
    - Файлы: `apps/photos/views.py`, `templates/photos/photo_detail.html`

9. [x] **`noindex` для профиля, страниц аккаунта и ошибок**
    - Навыки: koda-coder
    - `profile` задаёт `meta` с заголовком «Профиль» и `noindex=True`;
    - аккаунт allauth, 404 и 500 закрыты дефолтом процессора — проверяем, что так и есть,
      и что блоки `title`/`description` в этих шаблонах продолжают работать.
    - Файлы: `apps/accounts/views.py`, `templates/accounts/profile.html`

10. [x] **Локализация**
    - Навыки: koda-coder
    - `make i18n`. Строки, переехавшие из шаблонов в Python, сохраняют msgid
      (`{% blocktranslate with name=... %}` → `_("%(name)s ...") % {"name": ...}`),
      но `makemessages` помечает новые записи `fuzzy` и подставляет перевод похожей
      старой строки — снять флаги, дописать переводы en вручную.
    - Файлы: `locale/ru/LC_MESSAGES/django.po`, `locale/en/LC_MESSAGES/django.po`

11. [x] **Тесты `tests/test_meta_tags.py`**
    - Навыки: koda-coder
    - Проверяем: абсолютный `canonical` и его отсутствие при `?page>1`; наличие
      `og:title`/`og:description`/`og:url`/`og:image`/`twitter:card`; картинку объекта
      (фото → preview, команда → эмблема, человек → главное фото) и заглушку на главной;
      `noindex` у черновика команды, `?page>1`, `/search/`, страниц аккаунта, 404 и 500;
      отсутствие фамилии в `og:title` и описании при `first_name_only` и `hidden`;
      обрезку описания до 160 символов; корректное экранирование кавычек в названии.
    - Файлы: `tests/test_meta_tags.py`

12. [x] **Проверка, документация и версия**
    - Навыки: koda-coder, semver-checker
    - `make lint && make test`, `make i18n`, ручная проверка `<head>` в браузере
      на 5–6 страницах (главная, фото, команда, человек, поиск, вход);
      сверить существующие тесты, которые смотрят на `<title>`/описание
      (`tests/test_error_pages.py`, `tests/test_home.py`, `tests/test_auth_ui.py`).
    - Документация: `README.md` (состояние), `KODA.md` + `AGENTS.md` (правило: SEO-метатеги
      задаются во вьюхах через `page_meta()`, `noindex` — дефолт для незаданных страниц),
      мастер-план `public-archive.md` (2.10 → `[x]`, раздел «Итог», таблица «Прогресс»),
      бэклог (P2-4 → готово, следующий — 2.11).
    - Версия: 0.10.0 → 0.11.0 (minor) в `pyproject.toml`, затем `uv lock`.
    - Файлы: `README.md`, `KODA.md`, `AGENTS.md`, `.koda/plans/public-archive.md`,
      `.koda/backlog.md`, `pyproject.toml`, `uv.lock`

---

## 5. Критерии готовности

- В исходном коде страницы фото, команды, события и человека есть корректные `og:title`,
  `og:description`, `og:image` (абсолютный URL) и `canonical`.
- Ссылка на фото и команду разворачивается в мессенджере карточкой с картинкой.
- Фамилия человека со скрытым именем не появляется ни в `<title>`, ни в `og:*` —
  проверяется тестом.
- Черновик, `?page>1`, `/search/`, страницы аккаунта, 404 и 500 содержат `noindex`.
- Переводы ru/en не потеряны, `make lint && make test` чистые.
- Версия 0.11.0, документация и планы обновлены.

---

## 6. Риски и известные ограничения

| Риск | Что делаем |
|---|---|
| Квадратная маленькая эмблема команды выглядит в карточке мелко | Начинаем с эмблемы, смотрим результат в реальном мессенджере. Альтернатива (выбирать «главное фото» команды) — заметно больше работы, понятия «главное фото команды» сейчас нет |
| `makemessages` ставит `fuzzy` и подставляет перевод похожей строки (случалось в 2.5–2.9) | После `make i18n` проверять fuzzy-блоки и пустые `msgstr`; для ru выравнивать по `msgid`, для en заполнять вручную |
| Перенос `title`/`description` в Python может потерять строку перевода | msgid сохраняем дословно; сверяем `git diff` каталогов `.po` до и после |
| Мессенджеры кэшируют карточки ссылок | Если карточка не обновилась — это кэш мессенджера, а не ошибка; проверять на новом URL или через отладочные инструменты |

---

## 7. Итог

Реализовано 2026-10-15, версия 0.11.0 (minor), ветка `feature/meta-tags`.

**Что сделано.** Метатеги собираются в Python: `apps/core/seo.py` (`page_meta()`, `canonical_url()`,
`image_meta()`, `og_locale()`), партиал `templates/partials/meta.html` только выводит готовый
словарь. Контекстный процессор `seo_meta` отдаёт дефолт `{"noindex": True}`. Заглушка
`static/img/og-default.png` (1200×630) сгенерирована одноразовым скриптом на Pillow.
Метатеги заданы на всех публичных страницах (главная, годы, списки, команда, событие,
человек, фото) и `noindex` — на служебных (поиск, формы команды, «мои команды», профиль,
страницы аккаунта, 404, 500). Тесты — `tests/test_meta_tags.py` (36), всего 636.

**Отклонения от плана.**

1. Адрес заглушки собирается из `settings.STATIC_URL`, а не через `{% static %}`: тег отдаёт
   имя с хэшем содержимого, и адрес картинки менялся бы после каждого деплоя, а мессенджеры
   держат карточку ссылки в кэше.
2. `noindex` для страницы 500 стоит прямо в шаблоне: Django рендерит её без `request`,
   контекстный процессор не работает, партиал метатегов пуст.
3. Описание главной берётся общее, из `page_meta()`, а не своё: иначе английская локаль
   подхватывала русский текст и падал тест `test_english_menu_has_no_russian_text`.
4. `noindex` добавлен и на страницы событий-черновиков и людей-черновиков (в плане
   упоминались только команды): правило одно для всех содержательных сущностей.
5. В `page_meta()` есть параметр `append_site_name`: главной он не нужен (имя сайта там
   стоит первым), остальным страницам — нужен.

**Найденная посторонняя проблема (не связана с 2.10).** Тест
`tests/test_photos.py::TestSource::test_str_falls_back_to_kind` падает при запуске вместе
с `tests/test_person_page.py` или `tests/test_profile.py` — и на чистом `master` тоже.
Причина: тесты с `headers={"accept-language": "en"}` оставляют активной английскую локаль,
и следующий тест получает `str(source) == "negative"` вместо «негатив». Требует отдельного
разбора (сброс локали между тестами).
