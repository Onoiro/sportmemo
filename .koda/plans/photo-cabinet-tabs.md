# SportMemo — вкладки кабинета «Мои фото» и уборка из списка

Дата создания: 2026-11-08
Статус: **завершён** (0.29.0, 2026-11-08; подписи вкладок уточнены в 0.29.2)
Бэклог: [`.koda/backlog.md`](../backlog.md) — задача **P3-23** (заводится шагом 10)
Ветка: `feature/photo-cabinet-tabs` (создаётся на шаге 1 от `master`)
База: `master`, версия 0.28.1, 1565 тестов
Финал: bump **0.28.1 → 0.29.0 (minor)** + `uv lock`, строка
`Bump version: 0.28.1 → 0.29.0 (minor)` в теле коммита

---

## 1. Проблема и цель

`/photos/mine/` отдаёт `Photo.objects.filter(uploaded_by=user)` без фильтра: человек
видит всё, что когда-либо загрузил, — черновики, отклонённое, удалённое модератором,
слитые дубликаты, битые файлы. Список растёт и перестаёт читаться. Привести его
в порядок нельзя: ни спрятать, ни сгруппировать.

**Цель.** Кабинет остаётся личным альбомом — ни одно фото не исчезает по статусу, —
но человек сам решает, что видеть в списке, и может убрать из списка то, что считает
ошибочной загрузкой. Действие обратимо и на архив не влияет.

**Что не меняется:**

- `deleted_at` остаётся признаком решения модератора. Автор его не ставит никогда;
- фото не удаляются физически: файлы, связи, история и журнал `ModerationAction`
  остаются целыми (инварианты № 4, 5, 6, 9);
- суточная квота не возвращается: `uploads_today()` считает `Photo.objects`;
- публичная выдача не меняется: убранное из списка опубликованное фото остаётся
  в архиве и открывается по ссылке.

---

## 2. Решения, согласованные с владельцем (2026-11-08)

| # | Вопрос | Решение |
|---|---|---|
| 1 | Убирать фото по статусу автоматически | **Нет.** Статус не синоним мусора: отклонённый снимок может быть ценен автору. Убирает только человек и только своё фото |
| 2 | Физическое удаление фото автором | **Нет.** Ломает журнал модерации, квоту и инвариант «дубликаты сливаются, а не удаляются» |
| 3 | Массовое «Убрать все решённые» | **Не делаем.** В альбоме неуместно; вернёмся, если поштучно станет тяжело |
| 4 | Подтверждение перед уборкой | **Не нужно.** Действие обратимо, вкладка «Убранные» даёт возврат |
| 5 | Что показывает вкладка «Все» | Всё, **кроме** убранного: иначе она снова превращается в свалку |
| 6 | Где живёт признак «убрано из списка» | Новое поле `Photo.cabinet_removed_at`, а не `deleted_at` и не `status` |
| 7 | Кто может убирать и возвращать | Только автор фото (`uploaded_by`), модератор — нет: чужой кабинет не его дело |

### Решение, требующее внимания: `hidden` и `failed`

Утверждённая таблица вкладок называла «Не прошли» = `rejected`, `deleted`, `failed`.
При сверке с `Photo.upload_state` обнаружилась дырка: состояние `hidden` (скрыто
модератором) не попадает ни в одну вкладку, кроме «Все», и сумма вкладок не сходится
с общим числом. Фото, скрытое модератором, человек ищет в первую очередь.

Поэтому вкладка называется **«Не в архиве»** и включает четыре состояния:
`rejected`, `hidden`, `deleted`, `failed`. Обоснование: это ровно те фото, которых нет
и не будет в публичном архиве и которые не ждут проверки. Название «Не прошли»
для скрытого и сломанного было бы неточным.

### Раскладка состояний по вкладкам

Правило согласовано с `Photo.upload_state` (порядок проверок от сильного признака):

| `upload_state` | Вкладка |
|---|---|
| `published` | В архиве |
| `review`, `processing` | На проверке |
| `rejected`, `hidden`, `deleted`, `failed` | Не в архиве |
| любое, но `cabinet_removed_at` заполнено | Убранные |

Вкладки «В архиве» + «На проверке» + «Не в архиве» = «Все». Это проверяется тестом
паритета, а не поддерживается дисциплиной.

---

## 3. Архитектура

| Что | Где |
|---|---|
| Поле и методы модели | `apps/photos/models.py` (`cabinet_removed_at`, `is_removed_from_cabinet`, `remove_from_cabinet`, `restore_to_cabinet`, `is_page_available`) |
| Миграция | `apps/photos/migrations/0006_photo_cabinet_removed_at.py` |
| Вкладки, счётчики, право, действия | `apps/photos/cabinet.py` (новый модуль) |
| Представления и маршруты | `apps/photos/views.py`, `apps/photos/urls.py` |
| Общий хелпер возврата | `apps/core/auth.py` (`safe_next`) |
| Шаблоны | `templates/photos/my_photos.html`, `templates/photos/partials/cabinet_tabs.html` |
| Стили | `static/css/sportmemo.css` |
| Тесты | `tests/test_photo_cabinet.py` |

### 3.1 Почему новое поле, а не `deleted_at`

`deleted_at` в проекте значит «объект скрыт решением модератора»: на нём держатся
`Photo.public`, `can_edit_photo`, `get_visible_or_404` и `ModerationAction`. Если автор
ставит его сам, в админке и истории невозможно отличить решение модератора от уборки
в списке, а правила доступа начнут вести себя иначе, чем задумано.

`cabinet_removed_at` описывает только кабинет:

```python
cabinet_removed_at = models.DateTimeField(
    _("убрано из кабинета"), null=True, blank=True, db_index=True
)
```

`django-simple-history` пишет изменение автоматически — инвариант № 6 выполняется
без отдельного журнала. Имя с `cabinet_`, а не `hidden_`: `hidden` в проекте занято
модерацией (`Status.HIDDEN`), и второе значение того же слова читалось бы как ошибка.

### 3.2 Ссылка на карточке: `is_page_available`

Сейчас карточка удалённого модератором фото ведёт на 404: `get_visible_or_404` не
отдаёт удалённое даже автору. Слитое фото ведёт нормально — `follow_merged` отдаёт 301
на канон.

```python
@property
def is_page_available(self) -> bool:
    """Открывается ли страница фото.

    Удалённое модератором фото отдаёт 404 даже автору, поэтому ссылка
    на карточке вела бы в тупик. Слитый дубликат — исключение: он отдаёт
    301 на канон, и ссылка работает.
    """
    if not self.is_deleted:
        return True
    canonical = self.merged_into if self.merged_into_id else None
    return bool(canonical and canonical.is_public)
```

Выборка кабинета делает `select_related("merged_into")`, поэтому лишнего запроса нет.
В шаблоне: `{% if photo.is_page_available %}` — ссылка, иначе `<span>` с тем же текстом.

### 3.3 `apps/photos/cabinet.py`

По образцу `apps/catalog/filters.py`: правила отбора, счётчики и подписи собираются
здесь, шаблон ничего не сравнивает с `request.GET` и не решает, какое значение валидно.

```python
TAB_ALL = "all"
TAB_ARCHIVE = "archive"
TAB_REVIEW = "review"
TAB_FAILED = "failed"       # подпись «Не в архиве»
TAB_REMOVED = "removed"
```

- `selected_tab(request)` — значение `?tab=`, невалидное считается «Все» (опечатка
  в ссылке не должна давать пустую выдачу — то же правило, что в фильтрах каталога);
- `tab_filter(tab)` — `Q` для вкладки; один и тот же `Q` идёт и в `filter()`, и в
  `Count(filter=...)`, поэтому вкладки и счётчики разойтись не могут;
- `cabinet_photos(user, tab)` — выборка с `select_related("team", "event", "merged_into")`
  и `order_by("-created_at")`; вкладка «Все» исключает убранное;
- `cabinet_counts(user)` — один `aggregate` с пятью `Count(filter=...)`: пять вкладок
  не должны стоить пять запросов;
- `@dataclass(frozen=True) CabinetTab(key, label, count, selected)` и
  `cabinet_tabs(user, selected)` — готовая панель для шаблона;
- `can_manage_cabinet(user, photo)` — только автор: модератор чужой кабинет не правит;
- `remove_from_cabinet(photo) -> bool` / `restore_to_cabinet(photo) -> bool` — `False`,
  если фото уже в целевом состоянии: это гонка двух вкладок, а не ошибка (то же
  правило, что у сервиса решений 4.3).

Условия вкладок (сверены с `upload_state`):

| Вкладка | `Q` |
|---|---|
| В архиве | `status=published, deleted_at IS NULL, processing_status=processed` |
| На проверке | `deleted_at IS NULL` и не `failed` и (`processing_status in (uploaded, processing)` или `status=draft`) |
| Не в архиве | `deleted_at IS NOT NULL` или `processing_status=failed` или `status in (rejected, hidden)` |
| Убранные | `cabinet_removed_at IS NOT NULL` |
| Все | `cabinet_removed_at IS NULL` |

### 3.4 Представления и маршруты

- `my_photos` переписывается на `cabinet.cabinet_photos()` и `cabinet.cabinet_tabs()`;
  `@never_cache` остаётся: статус меняет модератор;
- `photos:photo_remove` (`POST /photos/<pk>/remove/`) и `photos:photo_restore`
  (`POST /photos/<pk>/restore/`) — через `post_action(fallback="photos:my_photos")`
  из `apps/core/auth.py`: `login_required` на POST увёл бы анонима на вход с `next`,
  равным адресу самого POST, и человек застрял бы в петле 405;
- объект берётся из `Photo.objects`, а не из `Photo.public`: убрать из списка нужно
  уметь и фото, удалённое модератором, — оно как раз чаще всего и мешает. Это не
  публичная выборка, а действие автора над своим фото, как и сам `/photos/mine/`;
- постороннему — **404**, а не 403: не раскрываем существование чужого черновика
  (правило `can_edit_photo` и `can_edit`);
- маршруты объявляются до `photos/<int:pk>/` — для читателя, приоритет явный
  (правило `teams/new/` в каталоге);
- возврат — `safe_next()`: `next` из скрытого поля с проверкой
  `url_has_allowed_host_and_scheme`, иначе `reverse("photos:my_photos")`.

### 3.5 `safe_next` в `apps/core/auth.py`

Функция нужна кабинету и (потенциально) другим POST-действиям. Своя копия
в `photos/views.py` завела бы третье правило проверки открытого редиректа.

```python
def safe_next(request, fallback):
    """Адрес возврата из скрытого поля формы или `fallback`."""
```

`_safe_next()` в `apps/moderation/views.py` **не трогаем**: у него третий аргумент —
объект и своя семантика «вернуть на страницу объекта». Переписывать закрытый подэтап
4.4 ради общего хелпера не нужно.

### 3.6 Шаблон

`templates/photos/partials/cabinet_tabs.html` — чипы-ссылки (`?tab=`), у каждого
подпись и счётчик, активный помечен `aria-current="page"`. Обычные ссылки: работают
без JavaScript, адрес можно сохранить.

Карточка:

- кнопка **«Убрать из списка»** — на всех вкладках, кроме «Убранные»;
- кнопка **«Вернуть в список»** — только на «Убранных»;
- обе — `method="post"`, `csrf_token`, скрытое поле `next` с текущим адресом;
- под кнопкой ничего не подписываем (место в карточке дорого): честная формулировка
  живёт в `messages.success` («Фото убрано из списка. Оно осталось в архиве.»)
  и в подписи вкладки «Убранные» («Эти фото остаются в архиве и открываются по ссылке»).

Пустые состояния по вкладкам: «Все» — прежнее «Вы ещё не загружали фото» с кнопкой
загрузки; «Убранные» — «Вы ничего не убирали»; остальные — «Здесь пока пусто».

### 3.7 Стили

Блок `/* --- Кабинет автора: вкладки --- */` после блока карточек кабинета:
`.cabinet-tabs` (горизонтальная прокрутка на узком экране), `.cabinet-tab`,
`.cabinet-tab--active`, `.cabinet-tab__count`, `.cabinet-note`,
`.upload-card__action--button` (кнопка выглядит как ссылка: действие в карточке
не должно спорить с «Изменить данные»). На `max-width: 575.98px` — `min-height: 44px`
и прокрутка вкладок пальцем.

---

## 4. Шаги

### 1. [x] Ветка и исходная проверка

1. `git status` — дерево чистое (ожидается `master`, `e54a307`).
2. `git switch -c feature/photo-cabinet-tabs`.
3. `uv run pytest tests/test_photo_upload.py tests/test_photo_dedup.py -q` — зелено,
   поведение кабинета ещё не менялось.

Навыки: koda-coder.
Файлы: —

### 2. [x] Поле модели, методы и миграция

`apps/photos/models.py`:

1. Поле `cabinet_removed_at` рядом с `deleted_at`-полями — с docstring, объясняющим,
   чем оно отличается от `deleted_at`.
2. Свойство `is_removed_from_cabinet`.
3. Методы `remove_from_cabinet()` и `restore_to_cabinet()` — по образцу
   `SoftDeleteModel.soft_delete()` / `restore()`: `save(update_fields=[...])`,
   чтобы не плодить лишние записи истории.
4. Свойство `is_page_available` (3.2).

`make migrations` → `apps/photos/migrations/0006_photo_cabinet_removed_at.py`.

Проверка:

```bash
grep -n "cabinet_removed_at\|is_page_available\|def remove_from_cabinet\|def restore_to_cabinet" apps/photos/models.py
uv run python manage.py makemigrations --check --dry-run   # пусто
```

Навыки: koda-coder.
Файлы:
- `apps/photos/models.py`
- `apps/photos/migrations/0006_photo_cabinet_removed_at.py`

### 3. [x] Модуль вкладок `apps/photos/cabinet.py`

Новый модуль по 3.3: константы вкладок, `selected_tab`, `tab_filter`,
`cabinet_photos`, `cabinet_counts`, `CabinetTab`, `cabinet_tabs`,
`can_manage_cabinet`, `remove_from_cabinet`, `restore_to_cabinet`.

Docstring модуля объясняет: почему правило отбора одно для выборки и счётчика
и почему оно согласовано с `Photo.upload_state`.

Проверка:

```bash
grep -n "^def \|^class \|^TAB_" apps/photos/cabinet.py
grep -n "from apps.photos.models" apps/photos/cabinet.py   # только модели фото
```

Навыки: koda-coder.
Файлы:
- `apps/photos/cabinet.py`

### 4. [x] `safe_next` и представления с маршрутами

1. `apps/core/auth.py` — `safe_next(request, fallback)` (3.5).
2. `apps/photos/views.py`:
   - `my_photos` — через `cabinet.selected_tab`, `cabinet.cabinet_photos`,
     `cabinet.cabinet_tabs`; в контекст `tabs`, `selected_tab`, `note`;
   - `_get_own_photo_or_404(request, pk)` — `Photo.objects`, 404 для чужого;
   - `photo_remove` и `photo_restore` — `@post_action("photos:my_photos")`,
     сообщения по результату `True`/`False`.
3. `apps/photos/urls.py` — `photos/<int:pk>/remove/`, `photos/<int:pk>/restore/`
   до `photos/<int:pk>/`.

Проверка:

```bash
grep -n "photo_remove\|photo_restore\|cabinet\." apps/photos/views.py apps/photos/urls.py
grep -rn "ModerationAction" apps/photos/views.py   # пусто: журнал не наш
uv run python manage.py check
```

Навыки: koda-coder.
Файлы:
- `apps/core/auth.py`
- `apps/photos/views.py`
- `apps/photos/urls.py`

### 5. [x] Шаблоны кабинета

1. `templates/photos/partials/cabinet_tabs.html` — чипы с подписями и счётчиками.
2. `templates/photos/my_photos.html`:
   - панель вкладок над сеткой;
   - ссылка на карточке только при `photo.is_page_available`, иначе текст;
   - формы «Убрать из списка» / «Вернуть в список» с `csrf_token` и `next`;
   - подпись вкладки «Убранные» и пустые состояния по вкладкам.

Проверка:

```bash
grep -n "cabinet_tabs\|is_page_available\|photo_remove\|photo_restore" templates/photos/my_photos.html templates/photos/partials/cabinet_tabs.html
uv run pytest tests/test_photo_upload.py -q   # старые тесты кабинета
```

Навыки: koda-coder.
Файлы:
- `templates/photos/my_photos.html`
- `templates/photos/partials/cabinet_tabs.html`

### 6. [x] Стили

`static/css/sportmemo.css` — блок вкладок кабинета по 3.7; проверить обе темы
(светлая сепия и тёмная) и мобильную ширину.

Проверка:

```bash
grep -n "cabinet-tab\|cabinet-note\|upload-card__action--button" static/css/sportmemo.css
```

Навыки: koda-coder.
Файлы:
- `static/css/sportmemo.css`

### 7. [x] Локализация

1. `make i18n`.
2. `locale/ru/LC_MESSAGES/django.po` — снять `fuzzy`, выровнять `msgstr` по `msgid`.
3. `locale/en/LC_MESSAGES/django.po` — переводы вручную (автоперевод не используем).
4. `make compile`.

Новые строки: «Все», «В архиве», «На проверке», «Не в архиве», «Убранные»,
«Убрать из списка», «Вернуть в список», «Фото убрано из списка. Оно осталось
в архиве.», «Фото уже убрано из списка.», «Фото возвращено в список.»,
«Эти фото остаются в архиве и открываются по ссылке», «Вы ничего не убирали»,
«Здесь пока пусто».

Навыки: koda-coder.
Файлы:
- `locale/ru/LC_MESSAGES/django.po`
- `locale/en/LC_MESSAGES/django.po`

### 8. [x] Тесты `tests/test_photo_cabinet.py`

1. **Паритет вкладок.** Для всех семи `upload_state` (`review`, `processing`,
   `published`, `hidden`, `rejected`, `failed`, `deleted`) вкладка, в которой фото
   видно, совпадает с таблицей 2. Один тест закрывает весь класс расхождений.
2. **Сумма вкладок.** «В архиве» + «На проверке» + «Не в архиве» = «Все»;
   счётчики в панели совпадают с длиной выборок.
3. **Убранные.** Убранное фото не видно ни в одной вкладке, кроме «Убранные»;
   в «Убранных» есть, счётчик учитывает.
4. **Невалидный `tab`.** `?tab=zzz` отдаёт «Все», а не пустую страницу.
5. **Убрать.** POST → 302, `cabinet_removed_at` заполнен, сообщение об успехе;
   повторный POST → «уже убрано», поле не меняется.
6. **Вернуть.** POST на «Убранном» → поле пусто, фото снова в своей вкладке.
7. **Права.** Чужое фото → 404; модератор чужое → 404; аноним → редирект на вход
   с `next` на список; GET на адрес действия → 405.
8. **Архив не меняется.** Убранное опубликованное фото остаётся в публичной выдаче
   (`visible_photos()`), открывается автору по ссылке, `deleted_at` пуст.
9. **Квота.** `uploads_today()` не меняется после уборки.
10. **Ссылки карточек.** Удалённое модератором фото — без ссылки; слитое в живой
    канон — со ссылкой (301).
11. **Возврат.** `next=/photos/mine/?tab=removed` → редирект туда;
    `next=https://evil.example/` → редирект на список.
12. **Пагинация.** `?tab=` сохраняется в ссылках страниц.
13. **Без JavaScript.** В HTML есть `method="post"`, `csrfmiddlewaretoken`,
    обе кнопки.

Проверка: `uv run pytest tests/test_photo_cabinet.py -q`.

Навыки: koda-coder.
Файлы:
- `tests/test_photo_cabinet.py`

### 9. [x] Проверка

```bash
make lint
make test
uv run python manage.py makemigrations --check --dry-run
grep -rn "cabinet_removed_at" apps/ | grep -v "models.py\|cabinet.py\|migrations"
grep -rn "deleted_at =" apps/photos/views.py   # пусто: автор не ставит deleted_at
```

Ручная проверка (`make run`): загрузить фото, дождаться обработки, убрать его из
списка, увидеть его во вкладке «Убранные», вернуть; проверить вкладки на мобильной
ширине; открыть убранное опубликованное фото по ссылке из архива.

Навыки: koda-coder.
Файлы: —

### 10. [x] Документация и версия

1. `KODA.md` и `AGENTS.md` (синхронно):
   - «Правила для кода»: `cabinet_removed_at` — про кабинет, `deleted_at` — про
     решение модератора; смешивать нельзя. Вкладки и право собирает
     `apps/photos/cabinet.py`, шаблон не сравнивает статусы. Убирает и возвращает
     только автор (`can_manage_cabinet`), модератор — нет. Уборка не возвращает
     квоту и не влияет на публичную выдачу;
   - «Публичный интерфейс»: абзац про вкладки кабинета (пять вкладок, раскладка
     состояний, «Все» без убранных, обратимость);
   - «Текущее состояние»: версия 0.29.0, число тестов.
2. `README.md`: строки маршрутов `/photos/<pk>/remove/`, `/photos/<pk>/restore/`,
   раздел про кабинет, число тестов.
3. `.koda/backlog.md`: задача **P3-23** «Вкладки кабинета и уборка из списка» —
   завести и сразу отметить готовой.
4. `pyproject.toml` + `uv lock` — навык `semver-checker`, **minor** 0.28.1 → 0.29.0.

Коммит:

```
feat: add cabinet tabs and remove from list

The "My photos" page showed every upload ever made: drafts, rejected
photos, deleted photos and merged duplicates. The list grows and stops
being readable.

Tabs split the list without hiding anything. The author can also remove
a photo from the list. This is a personal action, not a moderation
decision, so it uses its own field. The photo stays in the archive and
the action can be undone.

Bump version: 0.28.1 → 0.29.0 (minor)
```

После коммита `git status` чистый.

Навыки: semver-checker, koda-coder.
Файлы:
- `KODA.md`
- `AGENTS.md`
- `README.md`
- `.koda/backlog.md`
- `pyproject.toml`
- `uv.lock`

---

## 5. Риски

| Риск | Как ловим |
|---|---|
| Вкладки и `upload_state` разошлись: фото не попадает ни в одну или в две | Тест паритета (шаг 8, п. 1) на всех семи состояниях |
| Сумма вкладок не равна «Все» | Тест 8.2 |
| Авторская уборка подменяет решение модератора | Отдельное поле; `grep` на `deleted_at =` в `photos/views.py` пуст; тест 8.8 |
| Карточка ведёт на 404 | `is_page_available` + тест 8.10 |
| Квота обходится уборкой | Тест 8.9 |
| Модератор убирает чужое фото | `can_manage_cabinet`, 404, тест 8.7 |
| Открытый редирект через `next` | `safe_next` + тест 8.11 |
| Аноним застревает в петле 405 после POST | `post_action` вместо `login_required` + тест 8.7 |

---

## 6. Прогресс

| # | Шаг | Статус |
|---|---|---|
| 1 | Ветка и исходная проверка | [x] |
| 2 | Поле модели, методы и миграция | [x] |
| 3 | Модуль вкладок `apps/photos/cabinet.py` | [x] |
| 4 | `safe_next` и представления с маршрутами | [x] |
| 5 | Шаблоны кабинета | [x] |
| 6 | Стили | [x] |
| 7 | Локализация | [x] |
| 8 | Тесты `tests/test_photo_cabinet.py` | [x] |
| 9 | Проверка | [x] |
| 10 | Документация и версия | [x] |

---

## 7. Результат

Выполнено 2026-11-08, версия **0.29.0**, тестов **1594** (было 1565): 29 новых
в `tests/test_photo_cabinet.py`. Линтер чист, миграций кроме
`photos/0006_photo_cabinet_removed_at.py` нет.

Пять вкладок `?tab=` и обратимая уборка из списка работают, владелец проверил
в браузере. Шаг 10 не дописал этот раздел, поэтому он добавлен при закрытии
сессии 0.29.2.

Два замечания владельца вынесены в отдельный план
[`photo-cabinet-fixes.md`](photo-cabinet-fixes.md) и исправлены в 0.29.1–0.29.2:
`collectstatic` падал на sourcemap вендорного Bootstrap, а слово «архив» во вкладках
читалось наоборот. Подписи вкладок теперь «Опубликованы» и «Не опубликованы»,
ключи — `?tab=published` и `?tab=unpublished`, константы — `TAB_PUBLISHED`
и `TAB_UNPUBLISHED`.