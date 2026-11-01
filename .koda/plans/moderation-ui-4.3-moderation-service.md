# SportMemo — подэтап 4.3: сервис решений модерации — единая точка журнала

Дата создания: 2026-11-01
Статус: **утверждён** (владелец, 2026-11-01)
Мастер-план: [`moderation-ui.md`](moderation-ui.md) (подэтап 4.3)
Бэклог: [`.koda/backlog.md`](../backlog.md) — раздел «Этап 4», подэтап 4.3
Ветка: `feature/moderation-service` (создаётся на шаге 1 от `master`)
База: `master`, версия 0.25.0, 1429 тестов
Финал: bump **0.25.0 → 0.26.0 (minor)** + `uv lock`, строка
`Bump version: 0.25.0 → 0.26.0 (minor)` в теле коммита

> **Для новой сессии.** Подэтапы 4.0–4.2 в `master`: область модератора
> (`User.can_moderate`, `moderated_sports`), `is_trusted` и правило статуса
> (`initial_status_for` в `apps/moderation/services.py`). Работа начинается
> с шага 1. Шаги идут строго по порядку, следующий начинается только после
> проверки текущего. Файл плана поддерживается в актуальном состоянии:
> `[ ]` → `[x]`. Отмечать шаг выполненным только после `grep`-проверки, что
> правка реально есть в целевых файлах.

---

## 1. Цель и границы

**Цель.** Каждое решение модератора (опубликовать, скрыть, отклонить,
мягко удалить, восстановить, слить) записывается в журнал `ModerationAction`
через **одну** функцию, а право на решение проверяет **одна** функция.
Админка и публичный интерфейс (4.4–4.6) обязаны отвечать одинаково:
инвариант № 6 («всё значимое логируется») держится только тогда, когда
вторая копия правила не появляется.

**Почему это не «перенести четыре строки».** Сейчас запись в журнал живёт
внутри админки, в четырёх местах `apps/core/admin_mixins.py`:

| Место | Что пишет |
|---|---|
| `_apply_status` | `PUBLISH` / `HIDE` / `REJECT` |
| `action_soft_delete` | `DELETE` |
| `action_restore` | `RESTORE` |
| `MergeActionsMixin.merge_view` | `MERGE` |

Мастер-план называет первые два — остальные две копии того же правила
(восстановление и слияние) переводим заодно: иначе сервис станет пятым
местом, а не единственным.

Плюс два правила, которых сейчас нет вовсе:

- **Право на решение проверяется в сервисе**, а не в представлении. Сейчас
  админка полагается на `has_change_permission`, а область модератора
  (`can_moderate`) в админке не проверяется вообще: модератор с `is_staff`
  и узкой областью может решить по чужому виду спорта.
- **Причина отклонения обязательна** (решение владельца в мастер-плане).
  В админке действие «Отклонить выбранные» отклоняет молча — причину
  собрать негде.

**Что входит в подэтап:**

- `log_action()` и сервисные функции решений в `apps/moderation/services.py`;
- проверка права и признака «объект ещё решается» в сервисе;
- перевод всех четырёх точек `apps/core/admin_mixins.py` на сервис;
- страница «Отклонить с причиной» в админке (по образцу `merge_view`);
- тесты сервиса и админки;
- локализация, документация, версия.

**Что не входит:**

| Отложено | Куда / почему |
|---|---|
| Очередь черновиков и её действия | 4.4 и 4.5; там сервис вызывается из представлений, но самих страниц здесь нет |
| Разбор жалоб, поля решения `Report` | 4.6 |
| Права `UserAdmin` по области модератора | 4.1 закрыл раздел пользователей суперадмином; сужение админки по области — отдельное решение |
| Пакетная модерация | Мастер-план: на телефоне неудобно, поток небольшой |
| Троттлинг действий, уведомления автору о решении | P2-17, P5-11 |
| Запись журнала для `seed_demo` | Демо-данные — инструмент разработки, а не решение модератора (см. 2.1) |

---

## 2. Решения (утверждены владельцем 2026-11-01)

### 2.1 `log_action()` — единственная точка записи журнала

```python
def log_action(obj, moderator, action, reason=""):
    """Записать решение модератора в журнал."""
    return ModerationAction.objects.create(
        moderator=moderator,
        action=action,
        content_type=ContentType.objects.get_for_model(obj),
        object_id=obj.pk,
        reason=reason or "",
    )
```

Через неё идут все решения и слияние. Своих `ModerationAction.objects.create`
в представлениях и админке остаться не должно — проверяется `grep`-ом
в шаге 9.

`seed_demo` (`apps/core/management/commands/seed_demo.py`) публикует объекты
напрямую через `obj.publish(moderator=...)` и журнал не ведёт. Это
осознанно: демо-архив не содержит решений модератора, а записи журнала
с демо-модератором засорили бы админку. В плане фиксируется, чтобы
следующая сессия не «починила» это по инерции.

### 2.2 Сервисные функции возвращают `bool`

`True` — решение записано, `False` — объект уже в целевом состоянии
(или удалён, см. 2.4). Вызывающий сам решает, что сказать человеку:

- пакетное действие админки считает «сколько обновлено» по `bool`;
- 4.5 по `False` скажет «Уже обработано» и вернётся в очередь, а не
  перезапишет чужое решение.

Возврат `bool` вместо исключения — потому что «уже опубликовано» не ошибка,
а нормальная гонка двух модераторов.

### 2.3 Право проверяет сервис

```python
def _require_rights(obj, user):
    if not getattr(user, "can_moderate", None) or not user.can_moderate(obj):
        raise PermissionDenied
```

- `getattr` вместо прямого вызова: `AnonymousUser` не имеет метода, и это
  должно давать `PermissionDenied`, а не `AttributeError`.
- Объект передаётся в `can_moderate(obj)` — там уже есть и роль, и область
  по видам спорта, и «админ/суперадмин видит всё». Отдельная проверка
  `is_moderator` не нужна и завела бы второе правило.
- Объект без вида спорта (`Person`) открыт всем модераторам — это поведение
  `can_moderate`, дублировать его в сервисе не надо.

Реакцию на отказ выбирает вызывающий:

| Вызывающий | Реакция |
|---|---|
| Пакетное действие админки | объект пропускается, `messages.WARNING` «пропущено N: нет области модерации» — одно чужое фото в выборке не должно ронять всё действие |
| Страница `reject_view` (админка) | 403 (`PermissionDenied` наружу) |
| Представления 4.5 | 403 |

### 2.4 Удалённый объект не решается

`publish_object`, `hide_object`, `reject_object`, `soft_delete_object`
на объекте с `deleted_at` возвращают `False` и ничего не пишут: удалённый
объект не показывается, и решение по нему записало бы в журнал действие,
которого нет. Исключение — `restore_object`: он как раз про удалённый.

Проверка через `getattr(obj, "is_deleted", False)`: сервис вызывают и модели
без `SoftDeleteModel` (в тестах — заглушки).

### 2.5 Причина: обязательна для отклонения, необязательна для остальных

- `reject_object(obj, user, reason)` — пустая или пробельная причина даёт
  `ValueError`, объект не меняется, журнал пуст. Формы проверяют то же
  заранее (`required=True`), но правило живёт в сервисе: админка и фронтенд
  обязаны отвечать одинаково.
- `publish_object`, `hide_object`, `soft_delete_object`, `restore_object`
  принимают `reason=""`. Миксин подставляет «удалено модератором» /
  «восстановлено модератором», как сейчас.

### 2.6 `action_reject` в админке открывает форму

Действие списка больше не отклоняет молча: оно ведёт на страницу
с `Textarea` (одна причина на всю выборку), по образцу `MergeActionsMixin`:
действие → `redirect(f"{url}?ids=…")` → GET показывает выбранные объекты
и поле причины → POST отклоняет и возвращает в список.

Почему не «спросить причину в модальном окне»: админка должна работать
без JavaScript, а `<details>`/модалка в списке действий Django не
поддерживается. Почему не оставить молчаливое отклонение: требование
«причина обязательна всегда» относится к обоим интерфейсам.

### 2.7 `_apply_status` теряет ветку отклонения

После 2.6 отклонение в админке идёт только через форму, поэтому
`_apply_status` обслуживает два статуса — `PUBLISHED` и `HIDDEN`.
Ветку `REJECTED` не оставляем «на всякий случай»: она вызывала бы
`reject_object` без причины и падала бы на `ValueError` вместо понятного
маршрута.

---

## 3. Шаги

### 1. [x] Ветка

1. `git status` — дерево чистое.
2. `git switch -c feature/moderation-service` от `master`.
3. Проверка: `uv run pytest tests/test_admin.py tests/test_moderation.py -q` —
   зелено (поведение ещё не менялось).

Файлы: —

### 2. [x] Сервис решений в `apps/moderation/services.py`

Дописать в существующий модуль (не создавать новый: правило статуса из 4.2
и правило решений — один домен). Docstring модуля расширяется: «правила
модерации: статус нового объекта, права на решение и запись журнала».

Черновик:

```python
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied

from apps.core.models import Status
from apps.moderation.models import ModerationAction


def log_action(obj, moderator, action, reason=""):
    """Записать решение модератора в журнал."""
    ...


def _require_rights(obj, user):
    """Право на решение проверяет сервис, а не представление."""
    ...


def _is_decidable(obj) -> bool:
    """Решение по удалённому объекту записало бы действие, которого нет."""
    return not getattr(obj, "is_deleted", False)


def publish_object(obj, user, reason="") -> bool: ...
def hide_object(obj, user, reason="") -> bool: ...
def reject_object(obj, user, reason) -> bool: ...
def soft_delete_object(obj, user, reason="") -> bool: ...
def restore_object(obj, user, reason="") -> bool: ...
```

Тело каждой функции — по одному правилу:

1. `_require_rights(obj, user)`;
2. `_is_decidable(obj)` (кроме `restore_object`) → иначе `False`;
3. «уже в целевом состоянии» → `False` (для `soft_delete_object` —
   `obj.is_deleted`; для `restore_object` — `not obj.is_deleted`);
4. смена состояния методом модели (`obj.publish(moderator=user, reason=reason)`
   и т. д.);
5. `log_action(...)` с соответствующим `ModerationActionType`;
6. `return True`.

Импорт `ModerationAction` из `apps.moderation.models` безопасен: это то же
приложение, циклов нет. Модели `catalog`/`photos` модуль **не импортирует**:
сервис принимает любой объект с нужными полями.

Проверка:

```bash
grep -n "^def " apps/moderation/services.py
# log_action, initial_status_for, publish_object, hide_object,
# reject_object, soft_delete_object, restore_object + приватные
```

Файлы:
- `apps/moderation/services.py`

### 3. [x] Админ-миксин на сервис

`apps/core/admin_mixins.py`:

1. Импорт: `from apps.moderation import services` (или поимённо). Импорт
   `ModerationAction` остаётся только там, где нужен (после шага 3 — не нужен
   вовсе, убрать; `ruff` подскажет).
2. `_apply_status(request, queryset, status, action)`:
   - `Status.PUBLISHED` → `services.publish_object(obj, request.user)`;
   - `Status.HIDDEN` → `services.hide_object(obj, request.user)`;
   - ветки `REJECTED` больше нет (2.7);
   - `PermissionDenied` ловится вокруг вызова: объект пропускается,
     `denied += 1`;
   - счётчик `updated` — по `True`;
   - после цикла: `message_user` «обновлено N» + при `denied`
     `message_user(..., messages.WARNING)` «пропущено N: нет области
     модерации».
3. `action_soft_delete` → `services.soft_delete_object(obj, request.user,
   reason=_("удалено модератором"))`, тот же счётчик и то же предупреждение.
4. `action_restore` → `services.restore_object(obj, request.user,
   reason=_("восстановлено модератором"))`.
5. `MergeActionsMixin.merge_view` → `services.log_action(source,
   request.user, ModerationActionType.MERGE, reason=...)` вместо
   `ModerationAction.objects.create(...)`.
6. `MODERATION_ACTIONS` не меняется: `action_reject` остаётся в наборе,
   меняется только его поведение (шаг 4).

Проверка:

```bash
grep -n "ModerationAction.objects.create" apps/core/admin_mixins.py   # пусто
uv run pytest tests/test_admin.py -q
```

Существующие тесты `tests/test_admin.py` (публикация, скрытие, мягкое
удаление, восстановление, «уже опубликовано не логируется повторно»,
слияние) правок не требуют — это и есть проверка, что поведение не
изменилось.

Файлы:
- `apps/core/admin_mixins.py`

### 4. [x] Страница «Отклонить с причиной»

1. `apps/moderation/forms.py` — форма:

   ```python
   class ModerationRejectForm(BootstrapFormMixin, forms.Form):
       """Причина отклонения: одна на всю выборку объектов."""

       reason = forms.CharField(
           label=_("Причина отклонения"),
           widget=forms.Textarea(attrs={"rows": 3}),
       )

       def clean_reason(self):
           value = self.cleaned_data["reason"].strip()
           if not value:
               raise forms.ValidationError(_("Опишите причину: автор её увидит."))
           return value
   ```

   `BootstrapFormMixin` (`apps/core/forms.py`) здесь не обязателен —
   админка оформляет поля сама. Решить при реализации по виду формы
   в браузере; по умолчанию **не** подключать, чтобы не тянуть
   Bootstrap-классы в админку.

2. `ModerationActionsMixin`:
   - `action_reject` → `redirect(reverse(f"admin:{app}_{model}_reject")
     + f"?ids={ids}")`, как `action_merge`;
   - `get_urls()` добавляет `path("reject/", self.admin_site.admin_view(
     self.reject_view), name=f"{app_label}_{model_name}_reject")`;
     `MergeActionsMixin.get_urls` уже вызывает `super()`, поэтому маршруты
     складываются (проверяется тестом на `PhotoAdmin`, у которого оба
     миксина);
   - `reject_view(request)`: `has_change_permission` → иначе
     `PermissionDenied`; `ids` из POST/GET; `sources` из `self.model.objects`
     (не `public`: удалённый объект должен попасть в список и быть
     пропущенным с сообщением, а не исчезнуть молча); на POST — форма,
     при `is_valid()` цикл по `sources`: `reject_object` → счётчик,
     `PermissionDenied` → пропуск; сообщения «отклонено N» /
     «пропущено N: нет области модерации»; `redirect` в changelist.

3. `templates/admin/moderation/reject_form.html` — по образцу
   `templates/admin/core/merge_form.html`: `extends "admin/base_site.html"`,
   крошки, список выбранных объектов, `{{ form.as_p }}` (или явное поле
   с `{{ form.reason.errors }}`), `csrf_token`, скрытый `ids`,
   «Отклонить» + «Отмена» на список.

Проверка:

```bash
grep -n "reject_view\|_reject" apps/core/admin_mixins.py
uv run pytest tests/test_admin.py -q
```

Файлы:
- `apps/moderation/forms.py`
- `apps/core/admin_mixins.py`
- `templates/admin/moderation/reject_form.html`

### 5. [x] Тесты сервиса: `tests/test_moderation_service.py`

Сценарии (использовать фабрики из `tests/factories.py`):

1. **Журнал.** `publish_object(team, moderator)` → `status=PUBLISHED`,
   `moderated_by=moderator`, ровно одна запись: `action=PUBLISH`,
   `content_type`/`object_id` про команду, `moderator`, `reason`.
   То же для `hide_object`, `reject_object` (с причиной),
   `soft_delete_object` (`deleted_at` заполнен, `DELETE`),
   `restore_object` (`RESTORE`).
2. **Причина отклонения.** `reject_object(..., "")` и `reject_object(..., "   ")`
   → `ValueError`, `status` не изменился, `ModerationAction.objects` пуст.
3. **Права.** Обычный пользователь → `PermissionDenied`; модератор чужого
   вида спорта (`softball`) на объекте `baseball` → `PermissionDenied`;
   состояние объекта и журнал не тронуты. Модератор `softball` на `Person`
   (без вида спорта) — разрешено. `AnonymousUser` → `PermissionDenied`.
4. **Идемпотентность.** Повторный `publish_object` на опубликованном →
   `False`, вторая запись не появилась. `restore_object` на неудалённом →
   `False`.
5. **Удалённый объект.** `publish_object`/`hide_object`/`reject_object`
   на удалённой команде → `False`, статус прежний, журнал пуст;
   `restore_object` работает.
6. **`log_action`** пишет запись и для объекта без вида спорта (`Person`),
   и для модели без `StatusModel` (если понадобится — достаточно `Person`
   с ручным `action`).

Проверка: `uv run pytest tests/test_moderation_service.py -q`.

Файлы:
- `tests/test_moderation_service.py`

### 6. [x] Тесты админки

Дописать в `tests/test_admin.py`:

1. `action_reject` → 302, в адресе `reject`, в строке `ids=<pk>`.
2. Страница `admin:<app>_<model>_reject` открывается (200) и показывает
   выбранный объект.
3. POST с причиной: объект `REJECTED`, `moderation_reason` = причина,
   в журнале `REJECT` с той же причиной.
4. POST с пустой причиной: объект не отклонён, ответ 200 (форма с ошибкой).
5. Модератор без области (создать `ModeratorFactory(moderated_sports=[])`,
   `is_staff=True`, `can_moderate` для своих объектов — как автор):
   действие `action_publish` на двух командах — своя публикуется, чужая
   остаётся черновиком, в ответе предупреждение о пропуске.
6. `PhotoAdmin` имеет оба маршрута: `admin:photos_photo_merge` и
   `admin:photos_photo_reject` отвечают 200 (проверка, что `get_urls`
   двух миксинов не перетёрли друг друга).

Проверка: `uv run pytest tests/test_admin.py -q`.

Файлы:
- `tests/test_admin.py`

### 7. [x] Локализация

1. `make i18n`.
2. `locale/ru/LC_MESSAGES/django.po`: снять `fuzzy` у новых записей,
   выровнять пустые `msgstr` по `msgid`.
3. `locale/en/LC_MESSAGES/django.po`: заполнить переводы вручную.
4. `make compile`; `git diff --stat locale/` — только ожидаемые строки.

Новые строки: «Причина отклонения», «Опишите причину: автор её увидит.»,
«Отклонить с причиной», «Отклонено объектов: N», «Пропущено N: нет области
модерации», подписи страницы.

Файлы:
- `locale/ru/LC_MESSAGES/django.po`
- `locale/en/LC_MESSAGES/django.po`

### 8. [x] Документация

1. `KODA.md` и `AGENTS.md` (синхронно):
   - «Правила для кода»: запись журнала — только через
     `log_action()`/сервис решений; своих `ModerationAction.objects.create`
     в представлениях и админке нет; причина отклонения обязательна
     и проверяется в сервисе; удалённый объект не решается, кроме
     восстановления; право на решение проверяет сервис через
     `can_moderate(obj)`, а представление только выбирает реакцию на отказ;
   - раздел про админку: абзац «Сервис решений (4.3)» — четыре точки
     миксина переведены на сервис, отклонение в админке идёт через форму
     с причиной, `seed_demo` журнал не ведёт;
   - «Текущее состояние»: 4.3 закрыт, версия 0.26.0, число тестов.
2. `.koda/backlog.md`: подэтап 4.3 → готово.
3. `.koda/plans/moderation-ui.md`: у 4.3 пометка «сделано» со ссылкой
   на этот файл.

Файлы:
- `KODA.md`
- `AGENTS.md`
- `.koda/backlog.md`
- `.koda/plans/moderation-ui.md`

### 9. [x] Проверка и коммит

```bash
make lint
make test
uv run python manage.py makemigrations --check --dry-run   # схема не меняется
```

Проверки grep:

```bash
grep -rn "ModerationAction.objects.create" apps/
# только apps/moderation/services.py
grep -rn "ModerationAction.objects.create" apps/core/admin_mixins.py   # пусто
```

Semver: **minor** (новая возможность; миграций нет, но правило проекта —
4.2–4.6 идут как minor). Навык `semver-checker`, затем `uv lock`.

Коммит:

```
feat: write moderation decisions in one place

Moderation decisions were logged inside the admin mixin, in four
places. The public interface will need the same journal soon, and a
second copy of the rule would drift from the first one.

The new service also checks who may decide about an object. Before
this change the admin relied on its own permission check and ignored
the moderator sport scope, so a moderator with a narrow scope could
decide about another sport.

A rejection now needs a reason, and the reason is checked in the
service. The admin action opens a page with a text field instead of
rejecting silently.

Bump version: 0.25.0 → 0.26.0 (minor)
```

После коммита `git status` чистый.

Файлы:
- `pyproject.toml`
- `uv.lock`

---

## 4. Риски и как их ловим

| Риск | Как проверяем |
|---|---|
| Своя запись журнала осталась в админке | grep в шаге 9: `ModerationAction.objects.create` только в `apps/moderation/services.py` |
| Пакетное действие падает на одном чужом объекте | Тест 6.5: своя команда публикуется, чужая пропускается, предупреждение в ответе |
| Отклонение в админке осталось молчаливым | Тест 6.1: `action_reject` → 302 на форму; тест 6.4: без причины объект не меняется |
| `get_urls()` одного миксина теряет маршруты другого | Тест 6.6: у `PhotoAdmin` открываются и `merge/`, и `reject/` |
| Сервис отказывает там, где админка раньше разрешала | Тесты `test_admin.py` не правятся и обязаны пройти (кроме новых) |
| Модератор с `is_staff` и узкой областью внезапно получает отказ в админке | Это и есть цель 2.3; на проде область назначается бэкфиллом 4.1, поведение не меняется для тех, кому назначены все виды спорта |
| Демо-данные начинают писать журнал | `seed_demo` не трогаем, решение 2.1 зафиксировано |
| `reject_object` вызывают без причины из старого кода | grep в шаге 9: вызов только из `reject_view` и тестов; `ValueError` виден в тесте 5.2 |

---

## 5. Прогресс

| # | Шаг | Статус |
|---|---|---|
| 1 | Ветка | [x] |
| 2 | Сервис решений | [x] |
| 3 | Админ-миксин на сервис | [x] |
| 4 | Страница «Отклонить с причиной» | [x] |
| 5 | Тесты сервиса | [x] |
| 6 | Тесты админки | [x] |
| 7 | Локализация | [x] |
| 8 | Документация | [x] |
| 9 | Проверка и коммит | [x] |
