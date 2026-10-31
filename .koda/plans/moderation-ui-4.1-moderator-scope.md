# SportMemo — подэтап 4.1: доступ модератора и область по видам спорта

Дата создания: 2026-10-31
Статус: **утверждён** (владелец, 2026-10-31)
Мастер-план: [`moderation-ui.md`](moderation-ui.md) (подэтап 4.1, решения владельца)
Бэклог: [`.koda/backlog.md`](../backlog.md) — раздел «Этап 4», подэтап 4.1
Ветка: `feature/moderation-scope` (создана в 4.0)
База: `master`, версия 0.23.0, 1373 теста
Финал: bump **0.23.0 → 0.24.0 (minor)** + `uv lock`, строка
`Bump version: 0.23.0 → 0.24.0 (minor)` в теле коммита

> **Для новой сессии.** Подэтап 4.0 (сдвиг нумерации) уже закоммичен в этой ветке.
> Работа идёт с шага 1. Шаги выполняются строго по порядку, следующий начинается только
> после проверки текущего. Файл плана поддерживается в актуальном состоянии: `[ ]` → `[x]`.
> Отмечать шаг выполненным только после `grep`-проверки, что правка реально есть
> в целевых файлах.

---

## 1. Цель и границы

**Цель.** Модератор видит и решает только то, что относится к его видам спорта, —
и в публичном интерфейсе, и по прямой ссылке. Суперадмин и `role=admin` видят всё.
Правило области живёт в **одной** функции `User.can_moderate(obj)`, а не в трёх местах
по отдельности.

**Почему это не «только UI».** Сейчас `User` не знает про `Sport`, поэтому любой
модератор видит любой чужой черновик. Три точки проверки дублируют голый `is_moderator`:

| Место | Что проверяет сейчас |
|---|---|
| `apps/core/shortcuts.py` → `get_visible_or_404` | показ неопубликованного модератору |
| `apps/catalog/views.py` → `can_edit` | правка команды и события |
| `apps/photos/services.py` → `can_edit_photo` | правка метаданных фото |

Фильтр только в списке очереди (4.4) оставил бы доступ к чужим черновикам по прямой
ссылке — поэтому область вводится здесь, до очереди.

**Что входит в подэтап:**

- `User.is_trusted` (BooleanField, `db_index`) и `User.moderated_sports`
  (M2M на `core.Sport`, `blank=True`, `related_name="moderators"`);
- `User.can_moderate(obj=None)` — единственная точка проверки области;
- перевод трёх точек проверки на `can_moderate` (автор сохраняет доступ к своему);
- `Report.sport` (FK `core.Sport`, `null=True`, `PROTECT`) + заполнение при подаче
  жалобы + бэкфилл существующих жалоб;
- бэкфилл: всем существующим `moderator` и `admin` назначаются все виды спорта
  (поведение после деплоя не меняется);
- письма модераторам уходят по виду спорта объекта;
- `UserAdmin`: блок «Модерация», колонка и фильтры, все права — только суперадмину;
- тесты области, бэкфилла, писем и админки;
- локализация, документация, версия.

**Что не входит:**

| Отложено | Куда |
|---|---|
| Правило статуса и пост-модерация доверенного автора | **4.2** (`is_trusted` здесь только поле) |
| Сервис решений и запись журнала | **4.3** |
| Очередь черновиков и жалобы в интерфейсе | **4.4–4.6** |
| Смена вида спорта у объекта и перенос старых жалоб | решение владельца: ручная операция суперадмина, ограничение фиксируется в документации |
| Область для `Person` | инвариант № 1: человек может быть и в бейсболе, и в софтболе — люди доступны всем модераторам |
| Троттлинг и Telegram-уведомления | P2-17, P5-11 |

---

## 2. Решения

### 2.1 Семантика `can_moderate`

```python
def can_moderate(self, obj=None) -> bool:
    """Может ли пользователь принимать решения по объекту.

    `obj=None` — проверка «модератор ли вообще» без области.
    Объект без вида спорта (`Person`) доступен всем модераторам.
    """
    if self.is_superuser or self.role == UserRole.ADMIN:
        return True
    if self.role != UserRole.MODERATOR:
        return False
    sport_id = getattr(obj, "sport_id", None)
    if sport_id is None:
        return True
    return self.moderated_sports.filter(pk=sport_id).exists()
```

Правила, которые нельзя нарушать:

- **Пустой `moderated_sports` = модератор не видит чужого ничего.** Своё он видит как
  автор (`created_by` / `uploaded_by`). Ошибка в сторону «не видит» исправляется
  щелчком в админке, ошибка в сторону «видит всё» — утечкой.
- **`is_moderator` остаётся** («роль без области»): им пользуются профиль
  (`templates/accounts/profile.html`) и проверка «показывать ли блок роли». Менять его
  поведение нельзя — иначе обычный модератор без видов спорта перестанет видеть строку
  «Роль» в профиле и потеряет понимание, кто он.
- **Объект без `sport_id` (`Person`) — доступен.** `Person` не привязан к виду спорта
  в схеме, а придумывать ему область значит нарушить инвариант № 1.
- **`obj` — любой объект с `sport_id`.** Тип не проверяется: метод не должен знать
  про `catalog` и `photos` (иначе `accounts` потянет приложения).

### 2.2 Три точки проверки: что меняется

| Функция | Было | Стало |
|---|---|---|
| `get_visible_or_404` | `getattr(user, "is_moderator", False)` | `_can_moderate(user, obj)` — обёртка через `getattr`, чтобы тестовая заглушка без метода не ломала хелпер |
| `can_edit(user, obj)` | `bool(getattr(user, "is_moderator", False))` | `user.can_moderate(obj)` |
| `can_edit_photo(user, photo)` | то же | `user.can_moderate(photo)` |

Автор объекта проверяется **первым** и от области не зависит: человек всегда видит
и правит своё, даже если ему не назначен ни один вид спорта.

Лишний запрос возникает только в ветке «объект не опубликован и пользователь —
модератор» (один `EXISTS` по M2M). Публичная выдача его не делает: там возврат
происходит раньше. Кешировать M2M не нужно (KISS): страница очереди будет фильтровать
выборку в SQL, а не звать `can_moderate` в цикле.

### 2.3 `Report.sport` денормализуется

`Report` — generic FK, и без собственного `sport` жалобы не отфильтровать по области
в SQL. Поле заполняется в `report_add` из объекта (`getattr(target_obj, "sport", None)`):
все цели белого списка `apps/comments/targets.py` (`photos.Photo`, `catalog.Team`,
`catalog.Event`) имеют `sport`.

**Известное ограничение (фиксируется в docstring и в правилах проекта):** если суперадмин
сменит вид спорта у объекта, старая жалоба останется в прежней области. Кодом не
защищаем: операция ручная и редкая, а пересчёт в сигнале добавил бы запись на каждое
сохранение объекта.

Индекс `("sport", "status")` добавляется сразу: список жалоб (4.6) фильтрует по обоим
полям, и второй миграции под индекс не понадобится.

### 2.4 Бэкфилл: поведение после деплоя не меняется

`accounts/0004` назначает **все** виды спорта из базы каждому пользователю с
`role in (moderator, admin)`. Причина: до этого подэтапа модератор видел всё, и после
деплоя он должен продолжать видеть всё, пока суперадмин не сузит область осознанно.

Порядок миграций важен: `accounts/0004` зависит от `core/0002_seed_sports`, иначе
на свежей базе бэкфилл отработает до появления «Бейсбола» и назначит пустой список.
Проверка «свежая база» входит в шаг проверки: `make migrate` на пустой базе даёт
модератору один вид спорта.

### 2.5 Письма: `sport=None` сохраняет прежнее поведение

`moderator_emails(sport=None)`:

- `sport is None` → все активные `moderator` и `admin` (как сейчас);
- иначе → `Q(role=ADMIN) | Q(is_superuser=True) | Q(moderated_sports=sport)`,
  `is_active=True`, `distinct()`.

`notify_moderators(..., sport=None)` просто прокидывает параметр. Существующие вызовы
без `sport` продолжают работать, поэтому ломать сигнатуру не нужно.

`apps/catalog/emails.py` передаёт `sport=team.sport` / `sport=event.sport`,
`apps/moderation/emails.py` — `sport=report.sport`. У жалобы `sport` может быть `None`
(старые записи до бэкфилла не найдены, или цель без вида спорта) — тогда письмо уходит
всем, и это правильное поведение.

---

## 3. Шаги

### 3.1 Тестовая база

- [x] **Фикстуры: второй вид спорта и область у модератора**

  `tests/conftest.py`:

  1. Фикстура `softball` — `Sport.objects.get_or_create(slug="softball", defaults={"name": "Софтбол", "sort_order": 10})`.
     Комментарий: второй вид спорта нужен, чтобы проверять изоляцию области; в продукте
     он не появляется (инвариант № 1).
  2. Фикстура `moderator` после создания пользователя назначает ему **все** существующие
     виды спорта:

     ```python
     user.moderated_sports.set(Sport.objects.all())
     ```

     Без этого существующие тесты («модератор видит черновик», «письмо модератору
     уходит») начнут падать на пустой области. То же поведение даёт бэкфилл на проде.

  `tests/factories.py`: в `ModeratorFactory` добавить `@factory.post_generation`
  `moderated_sports` — назначить все виды спорта, если тест не задал свои. Фабрика
  используется в `ModerationActionFactory`; расхождение фикстуры и фабрики дало бы
  разные ответы на один вопрос.

  Проверка: `uv run pytest tests/test_visible_or_404.py tests/test_admin.py -q` — зелено
  (до правки модели поведение не меняется).

  Файлы:
  - `tests/conftest.py`
  - `tests/factories.py`

### 3.2 Модель `User`

- [x] **`is_trusted`, `moderated_sports`, `can_moderate`**

  `apps/accounts/models.py`:

  1. `is_trusted = models.BooleanField(_("доверенный автор"), default=False, db_index=True)`
     с docstring: доверенный автор публикует своё сразу (правило статуса — 4.2), очередь
     он не видит; способность независима от `role`.
  2. `moderated_sports = models.ManyToManyField("core.Sport", verbose_name=_("виды спорта модератора"), blank=True, related_name="moderators", help_text=...)`.
     `help_text`: «Пустой список — модератор не видит чужих объектов. Администраторы
     и суперадмины видят все виды спорта.»
  3. `can_moderate(obj=None)` — код из 2.1 с docstring, объясняющим пустой список
     и объекты без вида спорта.
  4. `is_moderator` не трогаем, но дополняем docstring: «роль без области; область
     проверяет `can_moderate()`».

  Проверка: `grep -n "def can_moderate\|moderated_sports\|is_trusted" apps/accounts/models.py`.

  Файлы:
  - `apps/accounts/models.py`

### 3.3 Миграции `accounts/0003` и `accounts/0004`

- [x] **M2M отдельной миграцией, `is_trusted` + бэкфилл — второй**

  1. Сгенерировать `make migrations` и разнести результат на две миграции:
     - `apps/accounts/migrations/0003_moderator_sports.py` — только `AddField`
       `moderated_sports`, `dependencies = [("accounts", "0002_add_person_link"), ("core", "0001_initial")]`;
     - `apps/accounts/migrations/0004_trusted_and_backfill.py` — `AddField is_trusted`
       + `RunPython(assign_all_sports, clear_sports)`, зависимости
       `[("accounts", "0003_moderator_sports"), ("core", "0002_seed_sports")]`.
  2. `assign_all_sports(apps, schema_editor)` — отдельная именованная функция модуля
     (не замыкание внутри `Migration`): её вызывает тест бэкфилла.
     Логика: `User = apps.get_model("accounts", "User")`,
     `sport_ids = list(apps.get_model("core", "Sport").objects.values_list("pk", flat=True))`,
     затем через `User.moderated_sports.through` вставить пары для
     `User.objects.filter(role__in=["moderator", "admin"])`. Массовая вставка
     `bulk_create(..., ignore_conflicts=True)`, а не цикл `set()`: модераторов может
     быть десяток, но идиома должна быть одна.
  3. Reverse — `RunPython.noop` для назначения (откат не должен снимать области,
     выставленные вручную после деплоя).
  4. В docstring миграции — почему назначены все виды спорта (2.4).

  Проверка:

  ```bash
  uv run python manage.py makemigrations --check --dry-run   # «No changes detected»
  uv run python manage.py migrate                            # на чистой базе применяется
  ```

  Файлы:
  - `apps/accounts/migrations/0003_moderator_sports.py`
  - `apps/accounts/migrations/0004_trusted_and_backfill.py`

### 3.4 Проверка прав: три точки

- [x] **Перевести `get_visible_or_404`, `can_edit`, `can_edit_photo` на `can_moderate`**

  1. `apps/core/shortcuts.py`: локальная обёртка

     ```python
     def _can_moderate(user, obj) -> bool:
         checker = getattr(user, "can_moderate", None)
         return bool(checker(obj)) if checker else False
     ```

     и в `get_visible_or_404` — `_is_owner(obj, user) or _can_moderate(user, obj)`.
     Порядок именно такой: автор проверяется первым и от области не зависит.
     Комментарий: обёртка нужна, потому что хелпер вызывают с заглушкой запроса
     в тестах, а `shortcuts` не должен требовать полноценного `User`.

  2. `apps/catalog/views.py`, `can_edit`: `return user.can_moderate(obj) or obj.created_by_id == user.pk`
     с сохранением `is_authenticated`-проверки и docstring «правит автор и модератор
     своего вида спорта».

  3. `apps/photos/services.py`, `can_edit_photo`: то же через `user.can_moderate(photo)`.

  Проверка: `grep -rn "is_moderator" apps/` — остаётся только определение свойства
  в `apps/accounts/models.py` и его docstring; в трёх точках проверки вызовов нет.

  Файлы:
  - `apps/core/shortcuts.py`
  - `apps/catalog/views.py`
  - `apps/photos/services.py`

### 3.5 `Report.sport`

- [x] **Поле, заполнение, бэкфилл, админка**

  1. `apps/moderation/models.py`: `Report.sport = models.ForeignKey("core.Sport", verbose_name=_("вид спорта"), null=True, blank=True, on_delete=models.PROTECT, related_name="reports")`;
     `indexes` дополняется `models.Index(fields=["sport", "status"])`; docstring —
     про денормализацию и ограничение из 2.3.
  2. `apps/moderation/migrations/0002_report_sport.py`: `AddField`, `AddIndex`,
     `RunPython(backfill_report_sport, migrations.RunPython.noop)`.
     Бэкфилл идёт по `ContentType` и `object_id` (без `content_object`): для каждой
     цели берём `pk → sport_id` одним словарём на модель, а не запрос на строку.
  3. `apps/moderation/views.py`, `report_add`: `sport=getattr(target_obj, "sport", None)`
     при `Report.objects.create(...)`.
  4. `apps/moderation/admin.py`: `sport` в `list_display` и `list_filter`, `sport`
     в `readonly_fields` (поле производное: заполняется из цели).

  Проверка: `grep -n "sport" apps/moderation/models.py apps/moderation/views.py apps/moderation/admin.py`.

  Файлы:
  - `apps/moderation/models.py`
  - `apps/moderation/migrations/0002_report_sport.py`
  - `apps/moderation/views.py`
  - `apps/moderation/admin.py`

### 3.6 Письма по области

- [x] **`moderator_emails(sport=None)` и `notify_moderators(..., sport=None)`**

  1. `apps/core/emails.py`: `moderator_emails(sport=None)` — ветка «все» и ветка
     «по виду спорта» (2.5), `distinct()`; docstring объясняет, почему админы и
     суперадмины попадают всегда.
  2. `notify_moderators(subject, template, context, sport=None)` — прокидывает параметр.
  3. `apps/catalog/emails.py`: `sport=team.sport` / `sport=event.sport`.
  4. `apps/moderation/emails.py`: `sport=report.sport`.

  Проверка: `grep -n "sport" apps/core/emails.py apps/catalog/emails.py apps/moderation/emails.py`.

  Файлы:
  - `apps/core/emails.py`
  - `apps/catalog/emails.py`
  - `apps/moderation/emails.py`

### 3.7 `UserAdmin`

- [x] **Блок «Модерация» и права только суперадмину**

  1. `apps/accounts/admin.py`:
     - fieldset «Модерация» после «Профиля»: `role`, `is_trusted`, `moderated_sports`
       (`filter_horizontal = ("moderated_sports",)`);
     - `list_display`: колонка `sport_scope` — метод с `@admin.display(description=_("виды спорта"))`,
       возвращает «все» для `admin`/суперадмина, иначе перечисление названий или «—»;
       M2M в `list_display` напрямую нельзя;
     - `list_filter`: `role`, `is_trusted`, `moderated_sports`;
     - `search_fields`: без изменений;
     - `help_text` поля уже задан в модели — в админке он выводится автоматически.
  2. Права: переопределить `has_module_permission`, `has_view_permission`,
     `has_add_permission`, `has_change_permission`, `has_delete_permission` —
     `request is not None and request.user.is_superuser`. Причина в docstring:
     `role` и `moderated_sports` — это права, и модератор с `is_staff` иначе назначит
     себе область и увидит чужие черновики.
  3. `add_fieldsets` не трогаем: новый пользователь создаётся без области, назначение —
     осознанное действие суперадмина.

  Проверка: `grep -n "moderated_sports\|is_trusted\|is_superuser" apps/accounts/admin.py`.

  Файлы:
  - `apps/accounts/admin.py`

### 3.8 Тесты

- [x] **`tests/test_moderator_scope.py`**

  Сценарии (по мастер-плану, плюс бэкфилл и админка):

  1. **Страница объекта.** Модератор с `baseball` открывает черновик бейсбольной команды
     (200) и получает 404 на черновике софтбольной; то же для события и фото
     (`photo_detail`).
  2. **Правка.** `team_update` и `photo_edit`: 200 для своего вида спорта, 404 для
     чужого.
  3. **Вседозволенность.** Суперадмин и пользователь с `role=admin` видят черновики
     обоих видов спорта.
  4. **Пустая область.** Модератор без видов спорта не видит чужого (404), но видит
     свой черновик как автор.
  5. **Автор вне области.** Автор софтбольного черновика без роли видит и правит его
     (право автора от области не зависит).
  6. **`Person`.** Черновик человека виден любому модератору независимо от области.
  7. **`can_moderate` как функция.** Юнит-тесты: `None` → «модератор ли вообще»;
     объект без `sport_id` → True; `role=user` → False; админ → True.
  8. **Бэкфилл.** `assign_all_sports` из миграции `0004` (импорт через
     `importlib.import_module("apps.accounts.migrations.0004_trusted_and_backfill")`)
     на двух спортах: модератору и админу назначены оба, обычному пользователю —
     ни одного.
  9. **Админка.** `is_staff=True, role=MODERATOR, is_superuser=False` → 403 на
     `/admin/accounts/user/`; суперадмин → 200.
  10. **Письма.** Новая команда вида спорта `softball`: письмо уходит модератору
      софтбола и админу, не уходит модератору только бейсбола (`mail.outbox`).
      Жалоба на софтбольную команду получает `report.sport == softball` и письмо
      только модератору софтбола.
  11. **Жалоба без вида спорта.** Цель без `sport` (или `sport=None`) — письмо уходит
      всем, ошибки нет.

  Проверка: `uv run pytest tests/test_moderator_scope.py -q`.

  Файлы:
  - `tests/test_moderator_scope.py`

### 3.9 Локализация

- [x] **`make i18n`, снять `fuzzy`, заполнить английский каталог**

  Новые строки: `verbose_name` полей (`доверенный автор`, `виды спорта модератора`),
  `help_text` области, `@admin.display` колонки, docstring-строки не переводятся.

  1. `make i18n`.
  2. Проверить `locale/ru/LC_MESSAGES/django.po`: снять `fuzzy` у новых записей,
     выровнять пустые `msgstr` по `msgid`.
  3. Заполнить `locale/en/LC_MESSAGES/django.po` вручную (автоперевод не используем).
  4. `make compile` и `git diff --stat locale/` — только ожидаемые строки.

  Файлы:
  - `locale/ru/LC_MESSAGES/django.po`
  - `locale/en/LC_MESSAGES/django.po`

### 3.10 Документация

- [x] **Правила проекта: область модератора**

  `KODA.md` и `AGENTS.md` (синхронно, по правилу проекта):

  1. В «Правилах для кода»:
     - область модератора проверяет только `User.can_moderate(obj)`; пустой список
       видов спорта — модератор не видит чужого, своё видит как автор;
     - `is_moderator` — «роль без области», для проверки доступа не используется;
     - объект без вида спорта (`Person`) доступен всем модераторам (инвариант № 1);
     - `Report.sport` денормализован: смена вида спорта у объекта оставит старую жалобу
       в прежней области — известное ограничение, кодом не защищаем;
     - письма модераторам уходят по виду спорта объекта, админы и суперадмины получают
       всё.
  2. В разделе «Аккаунты, команды, комментарии» — абзац «Область модератора (4.1)»
     с кратким описанием полей, бэкфилла и прав `UserAdmin` (только суперадмин).
  3. В «Текущем состоянии»: 4.1 закрыт, версия 0.24.0, число тестов.

  Файлы:
  - `KODA.md`
  - `AGENTS.md`

### 3.11 Проверка и коммит

- [x] **Линтер, тесты, миграции, версия**

  ```bash
  make lint
  make test
  uv run python manage.py makemigrations --check --dry-run
  ```

  Semver: **minor** (миграции схемы и новая возможность) → 0.24.0 в `pyproject.toml`,
  затем `uv lock`. Проверить, что `uv.lock` изменился только в строке версии проекта.

  Коммит:

  ```
  feat: add moderator sport scope and trusted flag

  A moderator must see only the drafts of their own sport. Before this
  change every moderator saw every draft, because the user model did not
  know about sports. The scope now lives in one method, so the page, the
  edit form and the photo form give the same answer.

  The flag for trusted authors is added now, but the status rule that
  uses it comes in the next sub-step. Reports get their own sport field:
  a report is a generic link and cannot be filtered by sport otherwise.

  Bump version: 0.23.0 → 0.24.0 (minor)
  ```

  После коммита `git status` чистый.

  Файлы:
  - `pyproject.toml`
  - `uv.lock`

---

## 4. Риски и как их ловим

| Риск | Как проверяем |
|---|---|
| Существующие тесты падают из-за пустой области у фикстуры `moderator` | Шаг 3.1 идёт первым; полный `make test` на шаге 3.11 |
| Бэкфилл отработал до появления вида спорта | Зависимость `accounts/0004` от `core/0002_seed_sports` + `migrate` на чистой базе |
| Модератор с `is_staff` назначает себе область | Права `UserAdmin` только суперадмину + тест 403 |
| Утечка чужого черновика по прямой ссылке | Область проверяется в `get_visible_or_404`, а не только в будущей очереди |
| Письма перестали доходить (фильтр слишком узкий) | Тесты 10–11: админ получает всегда, жалоба без вида спорта — всем |
| Автор потерял доступ к своему объекту | Тесты 4–5: право автора проверяется до области |

---

## 5. Проверка подэтапа

- `grep -rn "is_moderator" apps/` — только определение свойства, ни одного вызова
  в проверках доступа.
- `uv run pytest tests/test_moderator_scope.py tests/test_visible_or_404.py tests/test_admin.py -q` — зелено.
- `make lint && make test` — зелено, число тестов выросло.
- `makemigrations --check --dry-run` — «No changes detected».
- Версия 0.24.0 в `pyproject.toml` и в `uv.lock`.
- Ручная проверка на телефоне входит в UI-подэтапы (4.4–4.6), здесь не нужна.
