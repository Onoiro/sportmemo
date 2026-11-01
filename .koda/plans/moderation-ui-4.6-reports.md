# SportMemo — подэтап 4.6: жалобы в интерфейсе

Дата создания: 2026-11-01
Статус: **утверждён** (владелец, 2026-11-01), включая четыре решения раздела 2:
журнал решения пишется по жалобе двумя новыми типами, свою жалобу модератор
не решает, жалоба без вида спорта видна всем модераторам, письмо ведёт
на `/moderation/reports/`.
Мастер-план: [`moderation-ui.md`](moderation-ui.md) — подэтап **4.6**, последний
в этапе 4.
Бэклог: [`.koda/backlog.md`](../backlog.md) — раздел «Этап 4»
Ветка: `feature/moderation-reports` (создаётся на шаге 1 от `master`)
База: `master`, версия 0.27.0, 1511 тестов
Финал: bump **0.27.0 → 0.28.0 (minor)** + `uv lock`, строка
`Bump version: 0.27.0 → 0.28.0 (minor)` в теле коммита

> **Для новой сессии.** Подэтапы 4.0–4.5 в `master`: область модератора
> (`User.can_moderate`, `User.moderated_sport_ids`), доверенные авторы
> (`initial_status_for`), сервис решений (`apps/moderation/services.py`),
> очередь черновиков (`apps/moderation/queue.py`, `/moderation/queue/`).
> Подача жалобы (`/reports/add/`) работает с 2.14 и **не меняется**:
> здесь только разбор. Работа начинается с шага 1. Шаги идут строго
> по порядку. Файл плана поддерживается в актуальном состоянии:
> `[ ]` → `[x]`. Отмечать шаг выполненным только после `grep`-проверки,
> что правка реально есть в целевых файлах, и после зелёного прогона
> тестов затронутых файлов.

---

## 1. Цель и границы

**Цель.** Модератор открывает с телефона `/moderation/reports/`, видит открытые
жалобы своих видов спорта (свежие сверху), берёт жалобу в работу и решает её
с обязательным текстом решения. Каждое решение идёт через сервис 4.3, поэтому
попадает в журнал `ModerationAction` тем же путём, что и в админке.

**Зачем отдельная страница.** Мастер-план: на телефоне разбор жалоб важнее
разбора черновиков — жалоба про нарушение прав или приватность не должна
ждать, пока модератор доберётся до компьютера. Админка на маленьком экране
неудобна и опасна.

**Что входит в подэтап:**

- `apps/moderation/reports.py` — правило «открытая жалоба», отбор по области,
  запрет саморазбора, карточка с готовыми полями, загрузка целей пачкой;
- три функции сервиса: `take_report`, `resolve_report`, `reject_report`;
- два новых типа журнала (`report_resolved`, `report_rejected`) и миграция
  `choices`;
- страница `/moderation/reports/` (список) и `/moderation/reports/<pk>/`
  (решение, только POST, с блокировкой строки);
- шаблоны страницы и карточки, форма решения с текстом без JavaScript;
- пункт меню, стили, локализация;
- письмо о новой жалобе со ссылкой на интерфейс вместо админки;
- тесты правил, доступа, действий и гонки;
- документация, версия.

**Что не входит:**

| Отложено | Куда / почему |
|---|---|
| Уведомление заявителя о решении жалобы | Нет страницы «моя жалоба»; отдельная идея в бэклог |
| Изменение объекта из карточки жалобы (скрыть, отредактировать) | Решение по объекту принимается в очереди и в админке; карточка даёт ссылку на объект |
| Показ решённых и отклонённых жалоб | Интерфейс про работу, а не про отчёт; история видна в админке |
| Пакетный разбор жалоб | Мастер-план: на телефоне неудобно, поток небольшой |
| Счётчик открытых жалоб в меню | Мастер-план: `count` на каждой странице — лишний запрос |
| Троттлинг подачи жалоб (P2-17) | Отложен при планировании 2.14 |
| Смена вида спорта у объекта после подачи жалобы | Мастер-план: ручная операция суперадмина, старая жалоба остаётся в прежней области |

---

## 2. Решения

### 2.1 Правило жалоб — `apps/moderation/reports.py`

Новый модуль рядом с `queue.py`, по тому же образцу: правило «жалоба открыта»
одно для списка и для действия. Разойдись они — жалоба была бы видна
в списке, но не решалась бы, а это худший вид дефекта: модератор нажимает
кнопку и не понимает, почему ничего не происходит.

```python
REPORT_PAGE_SIZE = 20
OPEN_STATUSES = (ReportStatus.NEW, ReportStatus.IN_REVIEW)


def open_reports(user):
    """Открытые жалобы области модератора, свежие сверху."""


def is_open(report) -> bool:
    """Открыта ли жалоба — то же правило для одного объекта."""


def is_own_report(report, user) -> bool:
    """Подал ли жалобу этот пользователь. Свою разбирает другой человек."""


def reports_page(user, request) -> (page_obj, cards):
    """Страница списка: пагинация и карточки с готовыми полями."""
```

Модуль **импортирует** `Report` и `ReportStatus` из `apps.moderation.models`:
это своё приложение, цикла нет (в отличие от `queue.py`, где модели чужие
и берутся строками).

### 2.2 Право на решение — существующий `User.can_moderate(report)`

Новый метод не нужен: у `Report` есть `sport_id`, а `can_moderate` его читает.
Область для выборки — `User.moderated_sport_ids()`: `None` — все виды спорта,
пустой список — ничего. Второй способ фильтрации по `moderated_sports`
в представлениях заводить нельзя (правило 4.4).

### 2.3 Жалоба без вида спорта видна всем модераторам

`Report.sport` допускает `NULL` (объект без вида спорта, старая жалоба,
цель вне белого списка), и `can_moderate` на объекте без вида спорта
возвращает `True`. Значит фильтр списка обязан быть

```python
qs.filter(Q(sport__isnull=True) | Q(sport_id__in=sport_ids))
```

Иначе такая жалоба открывается по прямой ссылке, но теряется в списке —
прямое расхождение правил, и жалоба «не должна потеряться» (обоснование
в `apps/core/emails.py`).

### 2.4 Свою жалобу модератор не решает

Разбирать собственное заявление — то же, что одобрять свой черновик: второй
человек не нужен, а решение выглядит как проверка. Правило живёт в двух
местах, и оба обязательны:

1. **Список** — `open_reports()` исключает жалобы этого пользователя:

```python
qs.filter(Q(reporter__isnull=True) | ~Q(reporter=user))
```

Именно так, а не `exclude(reporter=user)`: анонимная жалоба (`reporter IS NULL`)
не должна исчезнуть из списка из-за сравнения с `NULL`.

2. **Действие** — `is_own_report(report, user)` в `report_decide` **до**
   `is_open`, ответ **404**. Номер жалобы угадывается по порядку, и POST
   обошёл бы скрытую в списке строку.

### 2.5 Статусы и порядок

- Открытые: `new` и `in_review`. Решённые (`resolved`) и отклонённые
  (`rejected`) в интерфейсе не показываются — они уже разобраны, а отчёт
  живёт в админке.
- Порядок — **свежие сверху** (`-created_at`, тай-брейк `-pk`): в отличие
  от очереди черновиков, где старое важнее, свежая жалоба о нарушении прав
  не должна ждать неделю.
- Порция — `REPORT_PAGE_SIZE = 20`: карточка жалобы легче карточки фото
  (превью цели не грузим), но 20 — уже предел для телефона.

### 2.6 Карточка — датакласс с готовыми полями

```python
@dataclass(frozen=True)
class ReportCard:
    report_id: int
    reason_label: str
    comment: str
    status_label: str
    is_new: bool
    target_title: str
    target_url: str | None      # None — ссылку дать некуда
    reporter: str               # «анонимно», если заявителя нет
    created_at: datetime
```

Шаблон печатает готовое и ничего не сравнивает — то же правило, что
у `Photo.upload_state`, `Event.date_label` и `QueueCard`.

**Ссылка на объект** строится только если объект существует, не удалён
и не слит: `getattr(obj, "is_deleted", False)`, `getattr(obj, "merged_into_id",
None)`, `getattr(obj, "get_absolute_url", None)` в `try/except` (у модели
вне белого списка адреса может не быть вовсе). Иначе `target_url=None`,
а шаблон печатает «объект удалён» / «объект недоступен» текстом.

**Заголовок объекта:** `obj.title`, если поле есть и заполнено (фото), иначе
`str(obj)` (команда и событие уже несут город и год в `__str__`).

**Загрузка целей пачкой.** Generic-FK даёт запрос на карточку, поэтому
выборка списка делает `select_related("content_type", "reporter", "sport")`,
а цели грузятся одним запросом на модель:

```python
by_type = defaultdict(list)          # content_type_id → [object_id]
for report in reports:
    by_type[report.content_type_id].append(report.object_id)
for ct_id, ids in by_type.items():
    ct = ContentType.objects.get_for_id(ct_id)   # кэш ContentType, без запроса
    model = ct.model_class()
    ...
    objects = model.objects.filter(pk__in=ids)
```

Число запросов не зависит от числа карточек — это проверяется тестом
(шаг 9, п. 12).

### 2.7 Доступ и пустая область

- `@login_required` — аноним уходит на вход;
- `if not request.user.is_moderator: raise PermissionDenied` — **403**,
  как в очереди: страница служебная, её адрес ничего не раскрывает,
  и 403 честнее отвечает на «почему меня не пускают»;
- `@never_cache` — жалобу может разобрать другой модератор, и копия
  из кэша показала бы уже решённую;
- `noindex, nofollow` через `page_meta`;
- `moderated_sport_ids() == []` — плашка «Вам не назначены виды спорта:
  вы видите только жалобы без вида спорта». Список при этом **строится**:
  жалобы без вида спорта видны всем (2.3), и пустая страница соврала бы.

### 2.8 Действия: один POST-адрес, поле `action`

`/moderation/reports/<int:pk>/`, только POST (`@require_POST`).

| Поле | Смысл |
|---|---|
| `action` | `take` / `resolve` / `reject`; другое значение — 400 |
| `resolution` | текст решения, обязателен для `resolve` и `reject` |
| `next` | адрес возврата (страница списка с `?page=N`) |

Один адрес, а не три маршрута: решение по жалобе — одно событие с тремя
исходами, и разводить его по адресам значит трижды повторять поиск объекта,
проверку области и проверку «ещё открыта».

**Порядок проверок в представлении:**

1. нет жалобы — 404;
2. `not request.user.can_moderate(report)` — **404** (правило проекта:
   не раскрываем чужое, то же в `can_edit`, `can_edit_photo` и `queue_decide`);
3. `is_own_report(report, request.user)` — **404** (2.4). Проверка стоит
   **до** `is_open`: иначе своя уже решённая жалоба ответила бы
   «Уже обработано» и подсказала бы, что адрес существует;
4. `not is_open(report)` — «Уже обработано», редирект;
5. `action` из POST: `take` / `resolve` / `reject`, иначе 400;
6. форма `ReportResolutionForm` для `resolve` и `reject`: ошибка →
   `messages.error` + редирект;
7. вызов сервиса;
8. `False` от сервиса → «Уже обработано» (для `take` — «Жалоба уже в работе»),
   `True` → сообщение об успехе.

### 2.9 Сервис: три функции, журнал — по жалобе

`apps/moderation/services.py`:

```python
def take_report(report, user) -> bool:        # new → in_review
def resolve_report(report, user, resolution) -> bool:
def reject_report(report, user, resolution) -> bool:
```

- право проверяет сервис через существующий `_require_rights` (внутри —
  `can_moderate`); `PermissionDenied` из сервиса в представление не долетает,
  потому что право проверено выше, но представление его всё равно не глотает
  (Django отвечает 403, `atomic()` откатывает запись);
- текст решения обязателен для `resolve` и `reject`: пустой или пробельный
  поднимает `ValueError` — форма проверяет то же заранее, но правило живёт
  в сервисе, иначе админка и сайт отвечали бы по-разному (то же решение,
  что у `reject_object`);
- возврат `bool`: `False` — жалоба уже не в том состоянии (гонка двух
  модераторов, а не ошибка);
- **`take_report` журнал не пишет:** «взял в работу» — не решение, а служебный
  шаг; след остаётся в истории `Report` (`django-simple-history`). Журнал
  `ModerationAction` — про принятые решения, и размывать его отметками
  о прочтении нельзя;
- `resolve_report` / `reject_report` заполняют `status`, `resolution`,
  `resolved_by`, `resolved_at` и пишут `log_action(report, user, action,
  resolution)`.

**Типы журнала — новые.** `ModerationActionType` получает
`REPORT_RESOLVED = "report_resolved"` и `REPORT_REJECTED = "report_rejected"`
с подписями «жалоба решена» и «жалоба отклонена».

Почему не «дублировать решение в `ModerationAction` для объекта», как сказано
в мастер-плане: при разборе жалобы объект **не меняется** — модератор решает,
обоснована ли претензия, а скрытие и правка объекта остаются отдельными
действиями в админке и на странице объекта. Запись `HIDE` или `PUBLISH`
по объекту, которого не было, — ложь в журнале, а инвариант № 6 требует,
чтобы журнал был правдой. Запись по жалобе отвечает на вопрос «что решили
по претензии» и читается в админке рядом с `Report`.

Цена: миграция `AlterField` для `ModerationAction.action` и его исторической
модели (шаг 2). Схема данных не меняется — только список допустимых значений.

### 2.10 Гонка двух модераторов

Три слоя, как в 4.4, и все три нужны:

1. **Блокировка строки:** поиск и все проверки внутри `transaction.atomic()`
   с `select_for_update()`. Второй модератор ждёт, потом видит уже решённую
   жалобу и получает «Уже обработано».
2. **Проверка «ещё открыта» в представлении** (`is_open`) до вызова сервиса:
   `resolve_report` на отклонённой жалобе вернул бы `False`, но `take_report`
   на решённой вернул бы `True` и вернул жалобу в работу — второй модератор
   отменил бы решение первого.
3. **Обработка `False` от сервиса:** сверхузкая гонка заканчивается понятным
   сообщением, а не молчанием.

Ограничение: на SQLite `select_for_update` игнорируется молча, поэтому тесты
доказывают логику проверок, а не саму блокировку. Настоящая проверка —
ручная на PostgreSQL и прогон CI на PostgreSQL 17.

### 2.11 Форма решения — одна на оба исхода

```python
class ReportResolutionForm(BootstrapFormMixin, forms.Form):
    resolution = forms.CharField(
        label=_("Решение"),
        max_length=MAX_REPORT_COMMENT_LENGTH,   # 2000, как у текста жалобы
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean_resolution(self):
        """Пробельный текст — та же пустота: заявителю нечего прочитать."""
```

`MAX_REPORT_COMMENT_LENGTH` уже живёт в `apps/moderation/forms.py` — второй
константы не заводим.

Две кнопки в одной форме: `<button name="action" value="resolve">Решена</button>`
и `<button name="action" value="reject">Отклонена</button>`. Одна форма, потому
что текст решения один и тот же, и две формы с одинаковым полем дали бы
дублирующиеся `id`.

Ошибка формы → `messages.error` с текстом ошибки и редирект назад: человек
ничего не вводил, терять нечего, а редирект убирает повторную отправку по F5.

### 2.12 Работа без JavaScript

`<details>` с `<summary>` «Решить с текстом» — нативная разметка, как
в карточке очереди. Кнопка «Взять в работу» — отдельная обычная форма.
Ни модального окна Bootstrap, ни htmx: модератор на телефоне может оказаться
с выключенным JS или на медленной сети.

Поле рендерится вручную с `id="resolution-{{ card.report_id }}"`: экземпляр
формы на странице один, а карточек двадцать, и одинаковые `id` увели бы
подпись в первое поле списка.

### 2.13 Письмо о новой жалобе ведёт в интерфейс

`notify_moderators_new_report(report, request=None)`:

- в контекст добавляется `reports_url` — абсолютный адрес `/moderation/reports/`;
- с `request` адрес строится `request.build_absolute_uri(reverse(...))`;
- без `request` (тесты и будущие вызовы из команд) — из
  `django.contrib.sites`: `https://<domain><path>`. Протокол — `https`,
  как в `robots.txt` боевой сайт за nginx; в dev письма печатаются в консоль,
  и адрес там всё равно не открывают;
- `admin_url` из контекста **убирается**, шаблон письма печатает
  «Разобрать жалобу: {{ reports_url }}».

Попутно исправляется дефект: сейчас письмо содержит относительный адрес
`/admin/...`, а почтовый клиент относительные адреса не открывает.
`apps/core/emails.py` не меняется — он уже умеет `sport`.

### 2.14 Меню и стили

- `templates/partials/navbar.html`: пункт «Жалобы» (`bi-flag`) сразу после
  «Очереди», под `{% if user.is_moderator %}`, без счётчика;
- `static/css/sportmemo.css`: новый блок «--- Жалобы ---» после блока очереди.
  Имена **`.reports-*`**, а не `.report-*`: `.report-shell`, `.report-form`,
  `.report-target` уже заняты страницей подачи жалобы, и общий префикс свёл бы
  два разных экрана в один набор стилей. Сетка — одна колонка на телефоне,
  две на широком (`grid-template-columns: repeat(auto-fill, minmax(20rem, 1fr))`),
  кнопки на всю ширину и `min-height: 44px` на узком экране;
- `templates/robots.txt` менять не нужно: `/moderation/` уже закрыт (4.4).

---

## 3. Шаги

### 1. [ ] Ветка

1. `git status` — дерево чистое.
2. `git switch -c feature/moderation-reports` от `master`.
3. Проверка: `uv run pytest tests/test_moderation_service.py tests/test_moderation_queue.py tests/test_moderation_queue_actions.py tests/test_reports_ui.py -q` — зелено (поведение ещё не менялось).

Файлы: —

### 2. [ ] Типы журнала и миграция

1. `apps/moderation/models.py` — в `ModerationActionType` добавить
   `REPORT_RESOLVED = "report_resolved", _("жалоба решена")` и
   `REPORT_REJECTED = "report_rejected", _("жалоба отклонена")`.
2. `uv run python manage.py makemigrations moderation` — одна миграция
   `0003_*` с двумя `AlterField` (`ModerationAction.action` и
   `HistoricalModerationAction.action`); имя файла переименовать
   в `0003_report_action_types.py`, docstring объясняет, зачем типы.
3. Проверка: `uv run python manage.py makemigrations --check --dry-run` —
   «No changes detected».

Файлы:
- `apps/moderation/models.py`
- `apps/moderation/migrations/0003_report_action_types.py`

### 3. [ ] Сервис решений по жалобам

`apps/moderation/services.py` — три функции по 2.9, рядом с решениями
по объектам (один модуль — одна тема: «что модератор решил»).

1. `take_report(report, user)`: `_require_rights`; `False`, если
   `report.status != ReportStatus.NEW`; иначе `status = IN_REVIEW`,
   `save(update_fields=["status", "updated_at"])`, возврат `True`.
   Журнал не пишет (2.9).
2. `resolve_report(report, user, resolution)`: `_require_rights`; пустой
   текст → `ValueError("Текст решения обязателен")`; `False`, если жалоба
   не открыта; иначе `status=RESOLVED`, `resolution`, `resolved_by`,
   `resolved_at=timezone.now()`, `save(update_fields=[...])`,
   `log_action(report, user, REPORT_RESOLVED, resolution)`, `True`.
3. `reject_report(report, user, resolution)`: то же с `REJECTED`
   и `REPORT_REJECTED`.
4. Импорт `timezone` и `ReportStatus`; docstring модуля дополнить строкой
   про жалобы (решение по жалобе пишется в журнал по жалобе, а не по объекту).

Проверка:

```bash
grep -n "def take_report\|def resolve_report\|def reject_report" apps/moderation/services.py
grep -n "REPORT_RESOLVED\|REPORT_REJECTED" apps/moderation/services.py
grep -rn "ModerationAction.objects.create" apps/   # только services.py, одна строка
```

Файлы:
- `apps/moderation/services.py`

### 4. [ ] `apps/moderation/reports.py`

Новый модуль по 2.1, 2.3–2.6.

1. Docstring: почему правило одно для списка и действия и почему своя
   жалоба не разбирается.
2. `REPORT_PAGE_SIZE = 20`, `OPEN_STATUSES = (ReportStatus.NEW,
   ReportStatus.IN_REVIEW)`.
3. `open_reports(user)` — `Report.objects.filter(status__in=OPEN_STATUSES)`,
   фильтр области `Q(sport__isnull=True) | Q(sport_id__in=sport_ids)` при
   `sport_ids is not None`, исключение своих `Q(reporter__isnull=True) |
   ~Q(reporter=user)`, `select_related("content_type", "reporter", "sport")`,
   `order_by("-created_at", "-pk")`.
4. `is_open(report) -> bool` — то же правило для одного объекта.
5. `is_own_report(report, user) -> bool` — `report.reporter_id == user.pk`,
   `False` для анонима.
6. `_targets(reports) -> dict[(content_type_id, object_id), obj]` — пачка
   по 2.6.
7. `@dataclass(frozen=True) class ReportCard` и `build_card(report, target)`.
8. `reports_page(user, request) -> (page_obj, cards)` — `paginate()`
   из `apps/core/shortcuts.py`.

Проверка:

```bash
grep -n "^def \|^class \|^REPORT_PAGE_SIZE" apps/moderation/reports.py
grep -n "select_related" apps/moderation/reports.py
```

Файлы:
- `apps/moderation/reports.py`

### 5. [ ] Список: представление, маршрут, шаблоны

1. `apps/moderation/views.py` — `reports_list(request)`: `@login_required`,
   `@never_cache`, `@require_GET`; не-модератор → `PermissionDenied`;
   `scope_missing = request.user.moderated_sport_ids() == []`;
   `page_obj, cards = reports.reports_page(...)` (строится всегда, 2.7);
   контекст: `cards`, `page_obj`, `scope_missing`, `resolution_form`,
   `meta` (`noindex`, `nofollow`), крошки «Главная → Жалобы».
2. `apps/moderation/urls.py` — `path("moderation/reports/",
   views.reports_list, name="reports")`. Объявить **до** маршрута решения
   и **до** `reports/add/` (префиксы разные, но порядок делает приоритет
   явным, правило `teams/new/`).
3. `templates/moderation/report_list.html`: `extends "base.html"`,
   заголовок «Жалобы», пояснение «Открытые жалобы ваших видов спорта:
   свежие сверху», плашка пустой области, `ul.reports-grid` с карточками,
   `{% pagination page_obj %}`, пустое состояние «Открытых жалоб нет»
   / «Все жалобы разобраны».
4. `templates/moderation/partials/report_card.html`: бейдж статуса
   (`badge-status`, класс по `is_new`), причина, текст жалобы, объект
   (ссылка или текст «объект удалён» / «объект недоступен»), заявитель
   и дата, блок действий (шаг 6 добавит формы).

Проверка:

```bash
uv run python manage.py check
grep -n "reports_list" apps/moderation/views.py apps/moderation/urls.py
```

Файлы:
- `apps/moderation/views.py`
- `apps/moderation/urls.py`
- `templates/moderation/report_list.html`
- `templates/moderation/partials/report_card.html`

### 6. [ ] Действия: форма, представление, маршрут, партиал

1. `apps/moderation/forms.py` — `ReportResolutionForm` по 2.11.
2. `apps/moderation/views.py` — `report_decide(request, pk)`:
   `@login_required`, `@require_POST`; не-модератор → `PermissionDenied`;
   `next_url = _reports_next(request)`; далее всё внутри
   `transaction.atomic()` с `select_for_update()` (2.10):
   `Report.objects.select_for_update().select_related("content_type",
   "sport").filter(pk=pk).first()` → 404;
   `can_moderate(report)` → 404; `reports.is_own_report(report, user)` → 404;
   `reports.is_open(report)` → «Уже обработано» + редирект;
   `action` из POST (`take` / `resolve` / `reject`, иначе 400);
   для `resolve`/`reject` — `ReportResolutionForm`, ошибка →
   `messages.error` + редирект; вызов сервиса; `False` →
   `messages.info` («Уже обработано» / для `take` «Жалоба уже в работе»);
   `True` → `messages.success` («Жалоба взята в работу» / «Жалоба решена» /
   «Жалоба отклонена»).
3. `_reports_next(request)` — приватная функция по образцу `_queue_next`
   (`url_has_allowed_host_and_scheme`, fallback —
   `reverse("moderation:reports")`).
4. `apps/moderation/urls.py` — `path("moderation/reports/<int:pk>/",
   views.report_decide, name="report_decide")`.
5. `templates/moderation/partials/report_resolution_form.html` — `<details>`
   с полем `resolution` и двумя кнопками (`action=resolve`, `action=reject`)
   по 2.12; форма «Взять в работу» (`action=take`) живёт в карточке рядом.

Проверка:

```bash
uv run python manage.py check
grep -n "report_decide\|ReportResolutionForm" apps/moderation/views.py apps/moderation/forms.py apps/moderation/urls.py
grep -rn "ModerationAction.objects.create" apps/moderation/views.py   # пусто
grep -n "select_for_update" apps/moderation/views.py                 # две строки: очередь и жалобы
```

Файлы:
- `apps/moderation/forms.py`
- `apps/moderation/views.py`
- `apps/moderation/urls.py`
- `templates/moderation/partials/report_resolution_form.html`
- `templates/moderation/partials/report_card.html`

### 7. [ ] Письмо о новой жалобе

1. `apps/moderation/emails.py` — `notify_moderators_new_report(report,
   request=None)` по 2.13: `_reports_url(request)`, `reports_url`
   в контексте, `admin_url` убрать; docstring обновить («ведёт в интерфейс,
   потому что админка модератору на телефоне неудобна»).
2. `templates/moderation/email/new_report_notification.txt` — строка
   `{% trans "Разобрать жалобу" %}: {{ reports_url }}` вместо «В админке».
3. `apps/moderation/views.py` — вызов в `report_add` передаёт `request`.

Проверка:

```bash
grep -rn "admin_url" apps/moderation/ templates/moderation/   # пусто
grep -n "reports_url" apps/moderation/emails.py templates/moderation/email/new_report_notification.txt
uv run pytest tests/test_reports_ui.py tests/test_moderator_scope.py -q
```

Файлы:
- `apps/moderation/emails.py`
- `templates/moderation/email/new_report_notification.txt`
- `apps/moderation/views.py`

### 8. [ ] Меню и стили

1. `templates/partials/navbar.html` — пункт «Жалобы» (`bi-flag`) сразу
   после «Очереди», только модератору.
2. `static/css/sportmemo.css` — блок «--- Жалобы ---» после блока очереди:
   `.reports-grid`, `.reports-card`, `.reports-card__head`,
   `.reports-card__reason`, `.reports-card__comment`,
   `.reports-card__target`, `.reports-card__meta`, `.reports-card__actions`,
   `.reports-resolve` (как `.queue-reject`), `.reports-resolve__field`.
3. Мобильная проверка: `max-width: 575.98px` — кнопки на всю ширину,
   `min-height: 44px`.

Проверка:

```bash
grep -n "reports-grid\|reports-card\|reports-resolve" static/css/sportmemo.css
grep -n "moderation:reports" templates/partials/navbar.html
```

Файлы:
- `templates/partials/navbar.html`
- `static/css/sportmemo.css`

### 9. [ ] Тесты: `tests/test_moderation_reports_ui.py`

Фабрики из `tests/factories.py`, фикстуры `baseball`, `softball`,
`moderator`, `user`, `team`, `photo` из `tests/conftest.py`. Хелперы:
`reports_url()`, `decide_url(pk)`, `report_for(obj, **kwargs)`.

1. **Состав.** Жалоба `new` и `in_review` в списке; `resolved` и `rejected`
   — нет; удалённая жалоба? — жалоб не удаляют, пункт не нужен.
2. **Изоляция области.** Модератор `baseball` видит жалобу `baseball`
   и не видит `softball`; суперадмин и роль `ADMIN` видят обе.
3. **Жалоба без вида спорта.** Видна модератору `baseball`, видна модератору
   с пустой областью; в списке пустой области стоит плашка, но карточка есть.
4. **Своя жалоба.** Модератор не видит в списке жалобу, которую подал сам;
   анонимные жалобы при этом остаются (проверка `Q(reporter__isnull=True)`).
5. **Своя жалоба через прямой POST** → **404** для `take`, `resolve`
   и `reject`; статус и журнал не изменились.
6. **Взять в работу.** POST `take` → 302, `status=in_review`, журнал пуст
   (служебный шаг не решение).
7. **Решена.** POST `resolve` с текстом → 302, `status=resolved`,
   `resolution`, `resolved_by`, `resolved_at` заполнены, ровно одна запись
   журнала `REPORT_RESOLVED` с тем же текстом.
8. **Отклонена.** То же для `reject` → `REPORT_REJECTED`.
9. **Пустой текст.** `""` и `"   "` для `resolve` и `reject` → статус
   не изменился, журнал пуст, редирект с сообщением об ошибке.
10. **Гонка.** Жалоба уже решена, приходит POST `reject` → статус
    не изменился, новых записей журнала нет, «Уже обработано»;
    `take` по решённой → «Уже обработано», статус не вернулся в работу.
11. **`False` от сервиса.** `services.resolve_report` подменяется моком,
    возвращающим `False` → «Уже обработано», без сообщения об успехе.
12. **Число запросов.** `CaptureQueriesContext`: список из 1 и из 5 жалоб
    даёт одинаковое число запросов (цели грузятся пачкой).
13. **Доступ.** Аноним — редирект на вход; обычный пользователь — 403;
    модератор — 200; в разметке `noindex`.
14. **Метод и адрес.** GET на адрес решения → 405; несуществующий `pk` →
    404; неизвестное `action` → 400.
15. **Возврат.** `next=/moderation/reports/?page=2` → редирект туда;
    `next=https://evil.example/` → на `reverse("moderation:reports")`.
16. **Без JavaScript.** В HTML есть `method="post"`, кнопки со значениями
    `take`, `resolve`, `reject`, `<details>` и поле `resolution`.
17. **Карточка объекта.** Удалённый объект даёт текст без ссылки
    (жалоба на фото, фото удалено модератором); существующий — ссылку
    на `get_absolute_url()`.
18. **Письмо.** `notify_moderators_new_report(report)` без `request` —
    в теле абсолютный адрес `/moderation/reports/`; с `request` — адрес
    начинается с `http://testserver`.

Проверка: `uv run pytest tests/test_moderation_reports_ui.py -q`.

Файлы:
- `tests/test_moderation_reports_ui.py`

### 10. [ ] Локализация

1. `make i18n`.
2. `locale/ru/LC_MESSAGES/django.po`: снять `fuzzy`, выровнять пустые
   `msgstr` по `msgid`.
3. `locale/en/LC_MESSAGES/django.po`: заполнить переводы вручную.
4. `make compile`; `git diff --stat locale/` — только ожидаемые строки.

Новые строки: «Жалобы», «Открытые жалобы ваших видов спорта: свежие сверху»,
«Вам не назначены виды спорта», «Открытых жалоб нет», «Все жалобы разобраны»,
«Взять в работу», «Решена», «Отклонена», «Решение», «Решить с текстом»,
«Жалоба взята в работу», «Жалоба решена», «Жалоба отклонена»,
«Жалоба уже в работе», «Уже обработано», «объект удалён»,
«объект недоступен», «анонимно», «жалоба решена», «жалоба отклонена»,
«Разобрать жалобу», «Текст решения обязателен».

Файлы:
- `locale/ru/LC_MESSAGES/django.po`
- `locale/en/LC_MESSAGES/django.po`

### 11. [ ] Документация

1. `KODA.md` и `AGENTS.md` (синхронно):
   - «Правила для кода»: правило жалоб живёт в `apps/moderation/reports.py`
     (`open_reports` + `is_open`), своё — в представлении заводить нельзя;
     жалоба без вида спорта видна всем модераторам (`Q(sport__isnull=True)`),
     иначе она теряется; **свою жалобу модератор не разбирает** —
     `is_own_report` в списке и 404 в действии; решение по жалобе пишет
     журнал **по жалобе** двумя типами, а не по объекту, потому что объект
     при разборе не меняется; `take_report` журнал не пишет; текст решения
     обязателен и проверяется в сервисе; блокировка строки и `is_open`
     до сервиса — как в очереди;
   - «Публичный интерфейс»: абзац «Жалобы (4.6)» — адрес, порция 20,
     свежие сверху, плашка пустой области, 403 для не-модератора,
     `<details>` вместо модального окна, письмо ведёт в интерфейс;
   - «Текущее состояние»: этап 4 закрыт, версия 0.28.0, число тестов.
2. `.koda/backlog.md`: 4.6 → готово; этап 4 → завершён; идея «заявитель
   не узнаёт решение своей жалобы» (нет страницы «моя жалоба»).
3. `.koda/plans/moderation-ui.md`: у 4.6 пометка «сделано» со ссылкой
   на этот файл и перечень отличий от записи (журнал по жалобе, своя жалоба,
   жалоба без вида спорта, письмо в интерфейс).

Файлы:
- `KODA.md`
- `AGENTS.md`
- `.koda/backlog.md`
- `.koda/plans/moderation-ui.md`

### 12. [ ] Проверка и коммит

```bash
make lint
make test
uv run python manage.py makemigrations --check --dry-run
```

Проверки grep:

```bash
grep -rn "ModerationAction.objects.create" apps/     # только services.py
grep -rn "admin_url" apps/moderation/                # пусто
grep -n "is_own_report" apps/moderation/reports.py apps/moderation/views.py
grep -n "select_for_update" apps/moderation/views.py
grep -rn "moderated_sports" apps/moderation/views.py apps/moderation/reports.py
# пусто: выборки фильтруются через moderated_sport_ids()
```

Ручная проверка (чек-лист этапа): открыть `/moderation/reports/`
в мобильной ширине, взять жалобу в работу, решить одну с текстом, отклонить
другую, убедиться, что письмо о новой жалобе ведёт на страницу интерфейса;
на PostgreSQL — два решения по одной жалобе из двух вкладок, второе даёт
«Уже обработано».

Semver: **minor** (новая возможность). Навык `semver-checker`, затем `uv lock`.

Коммит:

```
feat: let moderators handle reports on the site

Reports were handled only in the admin site. Moderators are veterans
who often work from a phone, and the admin site is hard to use on a
small screen.

The new page shows open reports of the moderator's own sports and has
three actions: take in work, resolve or reject with a text. The text
is required because the person who sent the report needs an answer.

A moderator does not handle their own report. That needs a second
person, the same way as with drafts.

Bump version: 0.27.0 → 0.28.0 (minor)
```

После коммита `git status` чистый.

Файлы:
- `pyproject.toml`
- `uv.lock`

---

## 4. Риски и как их ловим

| Риск | Как проверяем |
|---|---|
| Жалоба без вида спорта видна по ссылке, но не в списке | `Q(sport__isnull=True)` в `open_reports`; тест 9.3 |
| Анонимная жалоба исчезает из списка | Явное `Q(reporter__isnull=True) \| ~Q(reporter=user)`; тест 9.4 |
| Модератор разбирает свою жалобу | `is_own_report` в списке и 404 в действии; тест 9.5 для трёх действий |
| Своя запись журнала в представлении | grep в шаге 12: `ModerationAction.objects.create` только в `services.py` |
| Ложное решение по объекту в журнале | Журнал пишется по жалобе новыми типами; grep `admin_url` не нужен, проверяется шаг 2 |
| Второй модератор отменяет решение первого | `select_for_update` + `is_open` до сервиса + обработка `False`; тест 9.10–9.11 |
| `take` возвращает решённую жалобу в работу | `take_report` требует `status=NEW`; тест 9.10 |
| Пустой текст решения проходит | `ValueError` в сервисе + `clean_resolution` в форме; тест 9.9 |
| N+1 по generic-FK на карточках | Цели пачкой по `content_type_id`; тест 9.12 |
| Удалённый объект даёт битую ссылку | `is_deleted`/`merged_into_id` в карточке; тест 9.17 |
| Письмо ведёт на относительный адрес | `reports_url` абсолютный; тест 9.18 |
| Открытый редирект через `next` | `url_has_allowed_host_and_scheme`; тест 9.15 |
| Форма не работает без JavaScript | `<details>` и обычный POST; тест 9.16 |
| Блокировка строки «не работает» в тестах | Ограничение из 2.10: на SQLite `select_for_update` игнорируется; блокировку проверяют CI на PostgreSQL 17 и ручная проверка |
| Миграция `choices` ломает историю | `AlterField` для обеих моделей в одной миграции; `makemigrations --check` в шаге 12 |

---

## 5. Прогресс

| # | Шаг | Статус |
|---|---|---|
| 1 | Ветка | [ ] |
| 2 | Типы журнала и миграция | [ ] |
| 3 | Сервис решений по жалобам | [ ] |
| 4 | `apps/moderation/reports.py` | [ ] |
| 5 | Список: представление, маршрут, шаблоны | [ ] |
| 6 | Действия: форма, представление, маршрут, партиал | [ ] |
| 7 | Письмо о новой жалобе | [ ] |
| 8 | Меню и стили | [ ] |
| 9 | Тесты | [ ] |
| 10 | Локализация | [ ] |
| 11 | Документация | [ ] |
| 12 | Проверка и коммит | [ ] |
