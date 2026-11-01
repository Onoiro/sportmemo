# SportMemo — подэтап 4.4: очередь черновиков — список и действия

Дата создания: 2026-11-01
Статус: **утверждён** (владелец, 2026-11-01), включая два замечания ревью
2026-11-01: запрет самоодобрения через прямой POST (2.4, 2.9) и обработка
`False` от сервиса вместе с блокировкой строки (2.10)
Мастер-план: [`moderation-ui.md`](moderation-ui.md) — покрывает подэтапы **4.4 (список)**
и **4.5 (действия)**: мастер-план делит их формально, но страница без кнопок
бесполезна, а кнопки без страницы некуда поставить. Один подэтап — один план.
Бэклог: [`.koda/backlog.md`](../backlog.md) — раздел «Этап 4»
Ветка: `feature/moderation-queue` (создаётся на шаге 1 от `master`)
База: `master`, версия 0.26.0, 1459 тестов
Финал: bump **0.26.0 → 0.27.0 (minor)** + `uv lock`, строка
`Bump version: 0.26.0 → 0.27.0 (minor)` в теле коммита

> **Для новой сессии.** Подэтапы 4.0–4.3 в `master`: область модератора
> (`User.can_moderate`, `moderated_sports`), доверенные авторы
> (`initial_status_for`), сервис решений (`apps/moderation/services.py`:
> `publish_object`, `hide_object`, `reject_object`, `soft_delete_object`,
> `restore_object`, `log_action`). Работа начинается с шага 1. Шаги идут
> строго по порядку. Файл плана поддерживается в актуальном состоянии:
> `[ ]` → `[x]`. Отмечать шаг выполненным только после `grep`-проверки, что
> правка реально есть в целевых файлах, и после зелёного прогона тестов
> затронутых файлов.

---

## 1. Цель и границы

**Цель.** Модератор открывает с телефона `/moderation/queue/`, видит черновики
своего вида спорта от старых к новым и решает их здесь же: «Опубликовать» или
«Отклонить» с причиной. Каждое решение идёт через сервис 4.3, поэтому попадает
в журнал `ModerationAction` тем же путём, что и в админке.

**Зачем отдельная страница, а не админка.** Модераторы проекта — ветераны
бейсбола, у многих только телефон. Админка на телефоне неудобна и опасна:
одно неверное нажатие меняет чужие данные. Здесь две кнопки и ссылка на объект.

**Что входит в подэтап:**

- `User.moderated_sport_ids()` — правило области в форме, пригодной для фильтра
  выборок (сейчас правило живёт только в `can_moderate` для одного объекта);
- `apps/moderation/queue.py` — реестр моделей очереди, отбор «ждёт решения»,
  запрет самоодобрения (`is_own`), порядок FIFO, порция, датакласс карточки;
- страница `/moderation/queue/` (список) и `/moderation/queue/<kind>/<pk>/`
  (решение, только POST, с блокировкой строки);
- шаблоны страницы и карточки, форма отклонения с причиной без JavaScript;
- ссылка в меню пользователя, `robots.txt`, стили;
- тесты состава, изоляции, доступа, действий и гонки;
- локализация, документация, версия.

**Что не входит:**

| Отложено | Куда / почему |
|---|---|
| Жалобы в интерфейсе (`/moderation/reports/`) | 4.6 |
| Скрытие, мягкое удаление и восстановление из очереди | Мастер-план: очередь — про черновики. Спам и удаление остаются суперадмину в админке |
| Кнопки модерации на страницах объектов | Мастер-план: решение принимается в очереди, чтобы каждое попадало в журнал одинаково |
| Пакетная модерация (выбрать 20 и одобрить) | Мастер-план: на телефоне неудобно, поток небольшой |
| Счётчик черновиков в меню | Мастер-план: `count` на каждой странице — лишний запрос |
| Отдельная вкладка «опубликовано без проверки» | Решение владельца: хватает истории и писем |
| Троттлинг действий и уведомления автору о решении | P2-17, P5-11 |
| Правка метаданных фото прямо из очереди | 3.3 закрыл: правит автор и модератор на `/photos/<pk>/edit/` |

---

## 2. Решения

### 2.1 Правило области для выборок — `User.moderated_sport_ids()`

Сейчас правило области живёт в `User.can_moderate(obj)` и отвечает про **один**
объект. Очереди нужен фильтр **выборки**: «все черновики моих видов спорта».
Писать `Q(sport__in=user.moderated_sports.all())` в представлении нельзя:
суперадмин и админ видят всё, и условие пришлось бы повторять.

```python
def moderated_sport_ids(self):
    """Виды спорта области модератора или `None`, если фильтр не нужен.

    `None` — «все виды спорта»: суперадмин и роль `ADMIN`. Пустой список —
    модератор без области: он не видит чужого ничего, и фильтр
    `sport_id__in=[]` честно отдаёт пустую выборку.
    """
    if self.is_superuser or self.role == UserRole.ADMIN:
        return None
    return list(self.moderated_sports.values_list("pk", flat=True))
```

`can_moderate(obj)` переписывается через неё, чтобы правило не раздвоилось:

```python
sport_ids = self.moderated_sport_ids()
if sport_ids is None:
    return True
sport_id = getattr(obj, "sport_id", None)
return True if sport_id is None else sport_id in sport_ids
```

Почему не оставить два независимых правила: ошибка «в очереди видно, а по
прямой ссылке нет» (или наоборот) — это утечка чужого черновика, и она
обнаружится не тестом, а жалобой. Стоимость объединения — один запрос вместо
одного (`values_list` вместо `exists`), число запросов не растёт.

Тесты `tests/test_moderator_scope.py` и `tests/test_moderation_service.py`
после шага 2 правок **не требуют**: это и есть проверка, что поведение
не изменилось.

### 2.2 Реестр очереди — `apps/moderation/queue.py`

По образцу `apps/comments/targets.py`: **строки** `"catalog.Team"`,
`"catalog.Event"`, `"photos.Photo"` и `apps.get_model()` внутри функции.
Импорт моделей на уровне модуля дал бы зависимость `moderation` → `catalog` /
`photos` и риск цикла при загрузке приложений.

```python
@dataclass(frozen=True)
class QueueModel:
    key: str            # "team" / "event" / "photo" — часть адреса
    label: str          # подпись типа в карточке (gettext_lazy)
    model_label: str    # "catalog.Team" и т. д.
    author_field: str   # "created_by" / "uploaded_by"
    needs_processed: bool = False   # только у фото
```

Ключи (`team`, `event`, `photo`) совпадают с ключами `comments/targets.py`,
но реестр **свой**: там список комментируемых целей, здесь — модели с очередью.
Общий словарь связал бы две несвязанные причины изменения (появится модель
с очередью, но без комментариев — и наоборот).

`get_queue_model(key)` — модель по ключу, неизвестный ключ даёт `Http404`
(подделанный адрес не должен открывать ничего).

### 2.3 «Ждёт решения» — одна функция для выборки и для POST

```python
def pending_queryset(spec, sport_ids=None, exclude_author=None):
    """Черновики, которые модератор может решить сейчас."""
    model = get_queue_model(spec)
    qs = model.public.filter(status=Status.DRAFT, merged_into__isnull=True)
    if spec.needs_processed:
        qs = qs.filter(processing_status="processed")
    if sport_ids is not None:
        qs = qs.filter(sport_id__in=sport_ids)
    if exclude_author is not None:
        qs = qs.exclude(**{spec.author_field: exclude_author})
    return qs
```

```python
def is_pending(obj) -> bool:
    """Ждёт ли объект решения — то же правило для одного объекта."""
```

Правила, зафиксированные в мастер-плане:

- `status=draft` — скрытое, отклонённое и опубликованное уже решены;
- `deleted_at IS NULL` — через `Model.public`; явный `merged_into__isnull=True`
  оставляем рядом: слияние сейчас тоже помечает объект удалённым, но правило
  «слитое не решается» не должно зависеть от этой детали;
- для фото дополнительно `processing_status=processed`: судить по заглушке
  нельзя, а `failed` и «залипшие» — работа суперадмина в админке
  (кнопка «Обработать заново»).

`is_pending` и `pending_queryset` держим рядом и покрываем тестом, который
проверяет оба на одних и тех же объектах: список и действие, разошедшиеся
в правиле, дают «объект виден, но не решается» — худший вид дефекта.

### 2.4 Свой черновик не решается ни в списке, ни по адресу

Правило одно: **модератор не решает судьбу объекта, который создал сам.**
«Одобрить себя» — не решение модератора, а обход проверки: если модератор
может опубликовать своё, второй человек не нужен и пост-модерация
доверенных авторов теряет смысл.

Правило живёт в двух местах, и оба обязательны:

1. **Список** — `pending_queryset(..., exclude_author=user)`
   (`exclude(**{spec.author_field: user})`). Свой черновик в очередь
   не попадает: модератор видит его как автор — в кабинете и по прямой
   ссылке.
2. **Действие** — `is_own(obj, spec, user)` в `apps/moderation/queue.py`,
   проверка в `queue_decide` **до** `is_pending`, ответ **404**.

Почему одной проверки в списке недостаточно: адрес решения
`/moderation/queue/team/<pk>/` угадывается по номеру. Модератор с
областью `baseball` пройдёт `can_moderate(obj)` (вид спорта его),
пройдёт `is_pending(obj)` (объект действительно черновик) и опубликует
свой объект обычным POST. Невидимость ссылки — не защита, а косметика.

```python
def is_own(obj, spec, user) -> bool:
    """Создал ли этот пользователь объект. Своё решает другой человек."""
    return getattr(obj, f"{spec.author_field}_id", None) == user.pk
```

Код ответа — 404, а не 403: для этого человека адреса решения не
существует, и один код отвечает на «не ваш объект» и «нечего решать»
одинаково (то же правило, что `can_edit` и `can_edit_photo`).

Правило проверяется парным тестом (шаг 8, п. 4): на одном наборе объектов
(свой черновик, чужой черновик, опубликованный, удалённый, фото
не `processed`) ответ «виден в списке» обязан совпадать с ответом
«POST проходит проверки». Один такой тест закрывает весь класс
расхождений между списком и действием, а не один сценарий.

**Известное ограничение:** свой черновик решает другой модератор или
суперадмин в админке. Тексты кабинетов об этом пока молчат — отдельная
идея в бэклоге, не расширяем подэтап.

### 2.5 Порядок FIFO и пагинация без тяжёлых объектов

Три модели нельзя отсортировать одним SQL-запросом. Поэтому два прохода:

1. **Индекс очереди** — по каждой модели `values_list("pk", "created_at")`
   с теми же фильтрами. Складываем в список кортежей, сортируем
   `(created_at, kind, pk)`. Тай-брейк по `kind` и `pk` обязателен: у двух
   объектов может совпасть `created_at` до микросекунды, и без него одна
   и та же запись могла бы попасть на две страницы или исчезнуть;
2. **Загрузка порции** — `paginate()` (`apps/core/shortcuts.py`, `Paginator`
   работает и со списком) отдаёт срез, по нему грузим объекты
   `Model.objects.filter(pk__in=...)` и собираем карточки в порядке индекса.

Почему не грузить объекты целиком и не сортировать в Python: у фото
`ImageSpecField`, и лишние объекты в памяти тянут за собой поля и превью.
Индекс — два скалярных поля на строку.

Порция — `QUEUE_PAGE_SIZE = 12` (мастер-план): 24 превью в одном запросе —
24 конвертации `django-imagekit` на телефоне модератора.

Порядок — **старые первыми**: черновик, который ждёт неделю, важнее
сегодняшнего, а «свежие сверху» наказывают именно его.

### 2.6 Карточка — датакласс с готовыми полями

```python
@dataclass(frozen=True)
class QueueCard:
    kind: str
    kind_label: str
    pk: int
    title: str
    url: str
    author: str        # «автор неизвестен», если FK пуст
    created_at: datetime
    image_url: str | None   # превью или None
```

Шаблон печатает готовое и ничего не сравнивает — то же правило, что
у `Photo.upload_state` и `Event.date_label`.

Заголовок: у команды и события — `str(obj)` (там уже есть город и год),
у фото — `title`, иначе `original_filename`, иначе «Фотография».

Картинка: у фото — `thumbnail`, у команды — `logo`, у события — нет.
URL берётся в `try/except (OSError, ValueError)`: битый файл в хранилище
не должен ронять всю очередь (тот же приём, что в `page_meta`/`image_meta`).

Автор: `display_name` или `email`, пустой FK — «автор неизвестен».

### 2.7 Доступ: вход, роль, 403

- `@login_required` — аноним уходит на вход (адрес очереди известен только
  из меню модератора, но прямой переход должен вести на форму входа);
- `if not request.user.is_moderator: raise PermissionDenied` — вошедшему без
  роли **403, а не 404**: служебная страница не объект архива, скрывать её
  существование бессмысленно (её адрес ничего не раскрывает), а 403 честнее
  отвечает на «почему меня туда не пускает»;
- `@never_cache` — статус меняет другой модератор, и копия из кэша показала бы
  уже разобранный черновик (то же обоснование, что у `/photos/mine/`);
- `noindex, nofollow` через `page_meta`.

### 2.8 Пустая область — плашка, а не пустая страница

`moderated_sport_ids() == []` — модератору не назначены виды спорта. Пустой
список выглядел бы как «всё разобрано», и человек решил бы, что работы нет.
Поэтому отдельная плашка: «Вам не назначены виды спорта. Обратитесь
к администратору архива». Очередь при этом не строится вовсе.

### 2.9 Действия: один POST-адрес, поле `action`

`/moderation/queue/<kind>/<pk>/`, только POST (`@require_POST`).

| Поле | Смысл |
|---|---|
| `action` | `publish` или `reject`; другое значение — 400 |
| `reason` | причина, обязательна при `reject` |
| `next` | адрес возврата (страница очереди с `?page=N`) |

Почему один адрес и поле, а не два маршрута (`.../publish/`, `.../reject/`):
решение — одно событие с двумя исходами, и разводить его по адресам значит
дублировать поиск объекта, проверку области и проверку «ещё черновик».

**Права и объект.** Объект ищется в `Model.objects.filter(pk=pk)` (не `public`
и не `pending_queryset`): удалённый и уже решённый объект должны получить
понятный ответ, а не 404. Дальше:

1. нет объекта — 404;
2. `not request.user.can_moderate(obj)` — **404** (правило проекта: не
   раскрываем чужой черновик, то же в `can_edit` и `can_edit_photo`);
3. `is_own(obj, spec, request.user)` — **404** (2.4: свой черновик решает
   другой человек). Проверка стоит **до** `is_pending`: иначе свой уже
   опубликованный объект получил бы «Уже обработано» и подсказал бы, что
   адрес существует;
4. `not is_pending(obj)` — «Уже обработано», редирект (см. 2.10);
5. `action` из POST — `publish` или `reject`, другое значение — 400;
6. `publish` → `services.publish_object(obj, user)`;
   `reject` → форма причины → `services.reject_object(obj, user, reason)`;
7. `False` от сервиса — «Уже обработано» (2.10), `True` — сообщение
   об успехе.

`PermissionDenied` из сервиса сюда не долетает: право проверено выше тем же
`can_moderate`. Ловим его всё равно — на случай гонки с сужением области,
и отвечаем 403.

### 2.10 Гонка двух модераторов

Гонка возможна между проверкой и решением: два модератора открыли очередь
одновременно и нажали разные кнопки на одном объекте. Закрываем её тремя
слоями, и все три нужны.

**Слой 1 — блокировка строки.** Поиск объекта и все проверки идут внутри
`transaction.atomic()` с `select_for_update()`:

```python
with transaction.atomic():
    obj = model.objects.select_for_update().filter(pk=pk).first()
    ...                     # 404, can_moderate, is_own, is_pending
    decided = services.publish_object(obj, request.user)
    # или services.reject_object(obj, request.user, reason)
if not decided:
    messages.info(request, _("Уже обработано"))
```

Второй модератор ждёт на блокировке, потом перечитывает объект, видит
`status != draft` и получает «Уже обработано». Без блокировки оба прошли бы
проверку и оба вызвали сервис.

**Слой 2 — проверка «всё ещё черновик» в представлении.**
`is_pending(obj)` до вызова сервиса. Почему проверки в сервисе мало:
`publish_object` вернёт `False` на опубликованном (гонка безопасна), но
`reject_object` на опубликованном объекте вернёт `True` и снова отклонит —
второй модератор отменил бы решение первого, и в журнале остались бы два
противоположных решения по одному объекту. Поэтому «всё ещё черновик»
проверяет вызывающий, а сервис остаётся про «уже в целевом состоянии».

**Слой 3 — обработка `False` от сервиса.** Тот же ответ «Уже обработано»
на `False`: сверхузкая гонка, при которой оба модератора прошли проверку
одновременно, заканчивается не молчанием, а понятным сообщением. Строка
дешёвая и закрывает последнюю щель.

Сервис по-прежнему вызывается всегда — своя запись журнала в представлении
запрещена (инвариант № 6, правило 4.3). Сервис 4.3 не меняем: в админке
решение по опубликованному объекту — законное действие модератора, и
менять его ради очереди значит ломать закрытый подэтап.

**Ограничение тестов:** на SQLite `select_for_update` игнорируется молча,
поэтому тест докажет логику проверок, а не саму блокировку. Настоящая
проверка блокировки — ручная, на PostgreSQL; в CI тесты идут на PostgreSQL 17
(`.github/workflows/ci.yml`), и там блокировка работает.

### 2.11 Причина отклонения — та же форма, другое оформление

`ModerationRejectForm` (`apps/moderation/forms.py`) живёт в админке и намеренно
без Bootstrap-классов (решение 4.3). Публичной странице классы нужны, но
второй экземпляр правила «причина обязательна и обрезается» недопустим:

```python
class QueueRejectForm(BootstrapFormMixin, ModerationRejectForm):
    """Та же причина отклонения, но с оформлением публичной страницы."""
```

Правило остаётся в `ModerationRejectForm.clean_reason`, `BootstrapFormMixin`
(`apps/core/forms.py`) только навешивает классы после `super().__init__()`.

Пустая причина → `messages.error` с текстом ошибки формы и редирект назад.
Рендер страницы с ошибкой не делаем: человек ничего не вводил, терять нечего,
а редирект убирает повторную отправку по F5.

### 2.12 Возврат в очередь и открытый редирект

`next` приходит скрытым полем из карточки (текущий адрес с `?page=N`).
Проверяется `url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()},
require_https=request.is_secure())`; небезопасный или пустой — `reverse("moderation:queue")`.
Тот же приём, что в `report_add` (`_safe_next`), но без «вернуть на объект»:
решение принимается в очереди, туда и возвращаемся.

### 2.13 Работа без JavaScript

Кнопка «Отклонить» раскрывает `<details>` с полем причины — нативная
разметка, как в форме ответа на комментарий (`templates/comments/partials/comment.html`).
Ни модального окна Bootstrap, ни htmx: модератор на телефоне может оказаться
с выключенным JS или на медленной сети.

`<details>` внутри карточки, а не отдельная страница: причина пишется
в контексте объекта, который человек только что посмотрел.

### 2.14 Ссылка в меню и `robots.txt`

- `templates/partials/navbar.html`: пункт «Очередь» в выпадающем меню
  пользователя под `{% if user.is_moderator %}`, без счётчика. Пункт ставится
  **перед** «Мои команды»: у модератора это ежедневная работа, а не отчёт
  о своём;
- `templates/robots.txt`: `Disallow: /moderation/`. Строка `/reports/add/`
  остаётся: это другой адрес (`/reports/add/`, не `/moderation/...`);
- `noindex` в `page_meta` — вторая линия защиты (вежливая просьба, а не
  защита; настоящая защита — 403 в представлении).

---

## 3. Шаги

### 1. [x] Ветка

1. `git status` — дерево чистое.
2. `git switch -c feature/moderation-queue` от `master`.
3. Проверка: `uv run pytest tests/test_moderator_scope.py tests/test_moderation_service.py -q`
   — зелено (поведение ещё не менялось).

Файлы: —

### 2. [x] `User.moderated_sport_ids()` и `can_moderate` через неё

`apps/accounts/models.py`:

1. Добавить метод `moderated_sport_ids()` по черновику 2.1 с docstring,
   объясняющим `None` против `[]`.
2. Переписать тело `can_moderate(obj)` на вызов метода (2.1). Docstring
   обновить: «область в форме, пригодной и для одного объекта, и для
   фильтра выборок».
3. `is_moderator` не трогать: он остаётся «ролью без области».

Проверка:

```bash
grep -n "moderated_sport_ids" apps/accounts/models.py apps/moderation/
uv run pytest tests/test_moderator_scope.py tests/test_moderation_service.py -q
```

Оба файла тестов обязаны пройти **без правок** — это проверка, что правило
не изменилось.

Файлы:
- `apps/accounts/models.py`

### 3. [x] `apps/moderation/queue.py`

Новый модуль. Состав (по решениям 2.2–2.6):

1. Docstring модуля: почему реестр строками и почему правило «ждёт решения»
   одно для списка и для действия.
2. `QUEUE_PAGE_SIZE = 12`.
3. `@dataclass(frozen=True) class QueueModel` — поля из 2.2;
   `QUEUE_MODELS` — словарь `key → QueueModel` с тремя записями
   (`team`, `event`, `photo`).
4. `get_queue_model(key)` — `apps.get_model`, неизвестный ключ → `Http404`.
5. `pending_queryset(spec, sport_ids=None, exclude_author=None)` (2.3).
   `processing_status="processed"` — строкой с комментарием, что импорт
   `ProcessingStatus` на уровне модуля дал бы зависимость `moderation` →
   `photos` (правило `shortcuts.py`).
6. `is_pending(obj) -> bool` — то же правило для одного объекта, рядом
   с `pending_queryset`.
7. `is_own(obj, spec, user) -> bool` — «создал ли этот пользователь объект»
   (2.4). Стоит рядом с `is_pending`: обе функции отвечают на вопрос
   «этот модератор может решить этот объект», и разъехаться им нельзя.
8. `queue_entries(user) -> list[tuple]` — индекс очереди (2.5), сортировка
   `(created_at, kind, pk)`.
9. `queue_page(user, request) -> (page_obj, cards)` — пагинация индекса
   и сборка карточек.
10. `@dataclass(frozen=True) class QueueCard` (2.6) и `build_card(obj, spec)`.
11. `_image_url(obj)` — `thumbnail` / `logo` в `try/except (OSError, ValueError)`,
    иначе `None`.

Проверка:

```bash
grep -n "^def \|^class \|^QUEUE_PAGE_SIZE" apps/moderation/queue.py
grep -n "from apps.catalog\|from apps.photos" apps/moderation/queue.py   # пусто
```

Файлы:
- `apps/moderation/queue.py`

### 4. [x] Список: представление, маршрут, шаблоны

1. `apps/moderation/views.py` — `queue_list(request)`:
   `@login_required`, `@never_cache`, `@require_GET`; проверка
   `request.user.is_moderator` → `PermissionDenied`; `sport_ids =
   request.user.moderated_sport_ids()`; `scope_missing = sport_ids == []`;
   при `scope_missing` очередь не строится; `page_obj`, `cards`, `meta`
   (`noindex`, `nofollow`), крошки «Главная → Очередь черновиков».
2. `apps/moderation/urls.py` — `path("moderation/queue/", views.queue_list,
   name="queue")`. Объявить **до** маршрута решения, иначе `<str:kind>`
   перехватил бы адрес списка (правило `teams/new/` в каталоге).
3. `templates/moderation/queue.html`: `extends "base.html"`, заголовок,
   пояснение «здесь черновики ваших видов спорта», плашка пустой области,
   `ul.queue-grid` с карточками, `{% pagination page_obj %}`, пустое
   состояние «Очередь пуста».
4. `templates/moderation/partials/queue_card.html`: превью или заглушка,
   подпись типа, название-ссылка на `card.url`, автор, дата, действия
   (шаг 6 добавит формы — здесь только разметка под них).

Проверка:

```bash
uv run python manage.py check
curl -s localhost:8000/moderation/queue/ | head   # после шага 7
```

Файлы:
- `apps/moderation/views.py`
- `apps/moderation/urls.py`
- `templates/moderation/queue.html`
- `templates/moderation/partials/queue_card.html`

### 5. [x] Действия: форма, представление, маршрут

1. `apps/moderation/forms.py` — `QueueRejectForm(BootstrapFormMixin,
   ModerationRejectForm)` (2.11).
2. `apps/moderation/views.py` — `queue_decide(request, kind, pk)`:
   `@login_required`, `@require_POST`; проверка роли → `PermissionDenied`;
   `get_queue_model(kind)`; далее всё внутри `transaction.atomic()` с
   `select_for_update()` (2.10):
   объект из `Model.objects.select_for_update().filter(pk=pk)` → 404;
   `can_moderate(obj)` → 404; `queue.is_own(obj, spec, request.user)` → 404
   (2.4); `queue.is_pending(obj)` → «Уже обработано» + редирект;
   `action` из POST (`publish` / `reject`, иначе 400);
   для `reject` — `QueueRejectForm`, ошибка → `messages.error` + редирект;
   вызов `services.publish_object` / `services.reject_object`;
   `False` от сервиса → `messages.info` «Уже обработано» (2.10, слой 3);
   `True` → `messages.success` («Фотография опубликована» / «Отклонено:
   автор увидит причину» — по типу объекта через `spec.label`);
   `PermissionDenied` из сервиса → 403; редирект на `_safe_next(request)` (2.12).
   Импорт: `from django.db import transaction`.
3. `_safe_next(request)` — приватная функция в `views.py` по 2.12
   (не переиспользуем `_safe_next` из `report_add`: там третий аргумент —
   объект, и общая функция стала бы функцией с двумя режимами).
4. `apps/moderation/urls.py` — `path("moderation/queue/<str:kind>/<int:pk>/",
   views.queue_decide, name="queue_decide")`.

Проверка:

```bash
uv run python manage.py check
grep -n "queue_decide\|QueueRejectForm" apps/moderation/views.py apps/moderation/forms.py
grep -rn "ModerationAction.objects.create" apps/moderation/views.py   # пусто
```

Файлы:
- `apps/moderation/forms.py`
- `apps/moderation/views.py`
- `apps/moderation/urls.py`

### 6. [x] Формы в карточке без JavaScript

1. `templates/moderation/partials/queue_card.html` — блок действий:
   - форма «Опубликовать»: `method="post"`, `action="{% url
     'moderation:queue_decide' card.kind card.pk %}"`, `csrf_token`,
     `input type=hidden name=action value=publish`, `input type=hidden
     name=next value="{{ request.get_full_path }}"`, кнопка
     `btn btn-primary`;
   - `<details class="queue-reject">` с `<summary>` «Отклонить с причиной»
     и формой внутри: `action=reject`, поле `reason` из `QueueRejectForm`
     (передаётся в контекст представлением как `reject_form`), кнопка
     «Отклонить»;
   - форма рендерится один раз на страницу (одна на все карточки) —
     `{{ reject_form.reason }}` нельзя переиспользовать дважды с одинаковым
     `id`, поэтому виджет рендерится в каждой карточке, а `id`/`for`
     собираются вручную с суффиксом `card.pk` (в шаблоне:
     `id="reason-{{ card.kind }}-{{ card.pk }}"`).
2. `templates/moderation/partials/reject_form.html` — партиал с `<details>`
   и полем причины (мастер-план называет этот файл; в 4.6 он же
   переиспользуется для решения по жалобе).
3. В `queue.html` передать `reject_form` в карточку через `{% include ...
   with card=card reject_form=reject_form %}`.

Проверка (без JS страница должна работать):

```bash
uv run python manage.py check
uv run pytest tests/test_moderation_queue_actions.py -q   # после шага 10
```

Файлы:
- `templates/moderation/partials/queue_card.html`
- `templates/moderation/partials/reject_form.html`
- `templates/moderation/queue.html`

### 7. [x] Меню, `robots.txt`, стили

1. `templates/partials/navbar.html` — пункт «Очередь» под
   `{% if user.is_moderator %}` перед «Мои команды», ссылка
   `{% url 'moderation:queue' %}`, иконка `bi-inbox`.
2. `templates/robots.txt` — `Disallow: /moderation/`.
3. `static/css/sportmemo.css` — новый блок «--- Очередь черновиков ---»
   после блока карточек кабинета: `.queue-grid` (как `.upload-grid`),
   `.queue-card`, `.queue-card__media`, `.queue-card__image`,
   `.queue-card__body`, `.queue-card__kind`, `.queue-card__title`,
   `.queue-card__meta`, `.queue-card__actions` (кнопки в столбик на узком
   экране, в строку на широком), `.queue-reject` (`<summary>` как ссылка,
   форма с отступом), `.queue-reject__field`.
4. Мобильная проверка: `max-width: 575.98px` — кнопки на всю ширину,
   `min-height: 44px` (палец, а не курсор).

Проверка:

```bash
grep -n "queue-grid\|queue-card\|queue-reject" static/css/sportmemo.css
grep -n "moderation:queue" templates/partials/navbar.html
grep -n "moderation" templates/robots.txt
```

Файлы:
- `templates/partials/navbar.html`
- `templates/robots.txt`
- `static/css/sportmemo.css`

### 8. [x] Тесты очереди: `tests/test_moderation_queue.py`

Сценарии (фабрики из `tests/factories.py`, фикстуры `baseball`, `softball`,
`moderator`, `user` из `tests/conftest.py`):

1. **Состав.** Черновик команды в очереди; опубликованная — нет; удалённая —
   нет; слитая (`merge_into`) — нет; событие и фото — в очереди.
2. **Фото.** `processing_status=processed` — в очереди; `uploaded`,
   `processing`, `failed` — нет.
3. **`is_pending` = `pending_queryset`.** Для набора объектов (черновик,
   опубликованный, удалённый, фото не processed) ответы функции и выборки
   совпадают.
4. **Список = действие.** На одном наборе объектов (свой черновик, чужой
   черновик своего вида спорта, чужой вид спорта, опубликованный, удалённый,
   слитый, фото не `processed`) проверяется паритет: «объект виден в очереди»
   ⇔ «POST на адрес решения проходит проверки». Тест ловит весь класс
   расхождений между списком и действием, а не один сценарий.
5. **Изоляция области.** Модератор `baseball` не видит черновик `softball`
   и видит свой; суперадмин и роль `ADMIN` видят оба; модератор с пустой
   областью видит плашку «Вам не назначены виды спорта».
6. **Свой черновик.** Модератор не видит в очереди объект, который создал сам.
7. **Доступ.** Аноним — редирект на вход; обычный пользователь — 403;
   модератор — 200.
8. **Порядок и порция.** Старый черновик выше нового; 13 черновиков дают
   две страницы, на второй один; `?page=abc` не роняет страницу.
9. **Метатеги.** В разметке `noindex`.

Проверка: `uv run pytest tests/test_moderation_queue.py -q`.

Файлы:
- `tests/test_moderation_queue.py`

### 9. [x] Тесты действий: `tests/test_moderation_queue_actions.py`

1. **Одобрение.** POST `action=publish` → 302, `status=published`,
   `moderated_by` = модератор, в журнале ровно одна запись `PUBLISH`.
2. **Отклонение.** POST `action=reject` с причиной → 302, `status=rejected`,
   `moderation_reason` = причина, журнал `REJECT` с той же причиной.
3. **Пустая причина.** `reason=""` и `"   "` → объект остался `draft`,
   журнал пуст, ответ — редирект с сообщением об ошибке.
4. **Самоодобрение через прямой POST.** Модератор создал команду-черновик
   своего вида спорта; POST `action=publish` на адрес решения → **404**,
   статус остался `draft`, журнал пуст. То же для `action=reject`
   и для своего события и своего фото. Это проверка 2.4 на уровне действия:
   невидимость ссылки в списке дыру не закрывает.
5. **Гонка: объект уже решён.** Объект опубликован, приходит POST `reject` →
   статус не изменился, новых записей журнала нет, в сообщениях
   «Уже обработано». То же для повторного `publish`.
6. **`False` от сервиса.** `services.publish_object` подменяется моком,
   возвращающим `False` (сверхузкая гонка, объект ещё `draft` в памяти,
   но уже решён в базе): ответ — редирект с «Уже обработано»,
   без сообщения об успехе.
7. **Права.** Модератор чужого вида спорта → 404; обычный пользователь → 403;
   аноним — редирект на вход.
8. **Метод.** GET на адрес решения → 405.
9. **Неизвестный `kind`** и несуществующий `pk` → 404.
10. **Неизвестное `action`** → 400, статус не изменился.
11. **Возврат.** `next=/moderation/queue/?page=2` → редирект туда;
    `next=https://evil.example/` → редирект на `reverse("moderation:queue")`.
12. **Без JavaScript.** В HTML очереди есть `method="post"`,
    `name="action"` со значениями `publish` и `reject`, `<details>`
    и поле `reason` — то есть формы работают без скриптов.

Проверка: `uv run pytest tests/test_moderation_queue_actions.py -q`.

Файлы:
- `tests/test_moderation_queue_actions.py`

### 10. [x] Локализация

1. `make i18n`.
2. `locale/ru/LC_MESSAGES/django.po`: снять `fuzzy` у новых записей,
   выровнять пустые `msgstr` по `msgid`.
3. `locale/en/LC_MESSAGES/django.po`: заполнить переводы вручную
   (автоперевод не используем).
4. `make compile`; `git diff --stat locale/` — только ожидаемые строки.

Новые строки: «Очередь черновиков», «Здесь черновики ваших видов спорта:
старые сверху», «Вам не назначены виды спорта», «Очередь пуста»,
«Все черновики разобраны», «Команда», «Событие», «Фото», «Опубликовать»,
«Отклонить», «Отклонить с причиной», «Уже обработано», «автор неизвестен»,
подписи сообщений об успехе.

Файлы:
- `locale/ru/LC_MESSAGES/django.po`
- `locale/en/LC_MESSAGES/django.po`

### 11. [x] Документация

1. `KODA.md` и `AGENTS.md` (синхронно):
   - «Правила для кода»: очередь строится через `apps/moderation/queue.py`
     (`pending_queryset` + `is_pending`), своё правило «ждёт решения»
     в представлении заводить нельзя; **свой черновик не решается ни в
     списке, ни по прямому адресу** — `is_own` в `queue.py`, 404 в
     `queue_decide`, и проверка в списке её не заменяет; решение из очереди
     идёт через сервис 4.3, «всё ещё черновик» проверяет представление
     (гонка), а не сервис, и `False` от сервиса даёт то же «Уже обработано»;
     решение берёт блокировку строки (`select_for_update`), иначе два
     модератора отменят друг друга; область для выборок —
     `moderated_sport_ids()`, для одного объекта — `can_moderate()`;
   - раздел «Публичный интерфейс»: абзац «Очередь черновиков (4.4)» —
     адрес, порция 12, FIFO, плашка пустой области, 403 для не-модератора,
     `<details>` вместо модального окна, свой черновик решает другой человек;
   - «Текущее состояние»: 4.4 закрыт, версия 0.27.0, число тестов.
2. `.koda/backlog.md`: подэтап 4.4 → готово; идея о том, что кабинет автора
   не объясняет модератору, почему его собственный черновик ждёт другого
   человека (текст плашки в `/photos/mine/`, «Мои команды», «Мои события»).
3. `.koda/plans/moderation-ui.md`: у 4.4 и 4.5 пометка «сделано» со ссылкой
   на этот файл.

Файлы:
- `KODA.md`
- `AGENTS.md`
- `.koda/backlog.md`
- `.koda/plans/moderation-ui.md`

### 12. [x] Проверка и коммит

```bash
make lint
make test
uv run python manage.py makemigrations --check --dry-run   # схема не меняется
```

Проверки grep:

```bash
grep -rn "ModerationAction.objects.create" apps/     # только services.py
grep -rn "moderated_sports" apps/moderation/views.py apps/moderation/queue.py
# пусто: выборки фильтруются через moderated_sport_ids()
grep -n "is_own" apps/moderation/queue.py apps/moderation/views.py
grep -n "select_for_update" apps/moderation/views.py
grep -n "queue" templates/robots.txt templates/partials/navbar.html
```

Ручная проверка (чек-лист этапа): открыть `/moderation/queue/` в мобильной
ширине, одобрить фото, отклонить команду с причиной, проверить, что причина
видна в кабинете автора; на PostgreSQL — попробовать два решения по одному
объекту из двух вкладок и убедиться, что второе даёт «Уже обработано».

Semver: **minor** (новая возможность). Навык `semver-checker`, затем `uv lock`.

Коммит:

```
feat: add moderation queue for drafts

Moderators work from a phone. The admin site is hard to use on a
small screen and easy to break. The queue shows only drafts of the
moderator's own sports and has two buttons: publish or reject with a
reason.

The queue and the decide view share one rule for "waits for a
decision". If they drifted apart, a draft could be visible in the
list but impossible to decide.

A moderator does not see their own drafts here. Publishing your own
object is not a moderation decision, so it needs a second person.

Bump version: 0.26.0 → 0.27.0 (minor)
```

После коммита `git status` чистый.

Файлы:
- `pyproject.toml`
- `uv.lock`

---

## 4. Риски и как их ловим

| Риск | Как проверяем |
|---|---|
| Список и действие разошлись в правиле «ждёт решения» | `is_pending` и `pending_queryset` лежат рядом; тест 8.3 проверяет оба на одном наборе объектов |
| Модератор одобряет свой черновик по прямому адресу | `is_own` в `queue_decide` → 404; тест 9.4 для команды, события и фото |
| Список и действие разошлись в правиле «свой не решается» | Парный тест 8.4: «виден в списке» ⇔ «POST проходит проверки» на семи видах объектов |
| Второй модератор отменяет решение первого | `select_for_update` + `is_pending` до сервиса; тест 9.5 |
| Гонка прошла все проверки, но сервис вернул `False` | Обработка `False` → «Уже обработано»; тест 9.6 с моком |
| Своя запись журнала в представлении | grep в шаге 12: `ModerationAction.objects.create` только в `services.py` |
| Утечка чужого черновика через очередь | Фильтр `sport_id__in` из `moderated_sport_ids()`; тест 8.5 (оба вида спорта) |
| Утечка через подделанный POST на чужой объект | `can_moderate(obj)` → 404; тест 9.7 |
| Открытый редирект через `next` | `url_has_allowed_host_and_scheme`; тест 9.11 |
| Форма не работает без JavaScript | `<details>` и обычный POST; тест 9.12 |
| Превью роняет очередь на битом файле | `try/except` в `_image_url`; шаблон показывает заглушку |
| Свои черновики забивают очередь модератору-автору | `exclude(author_field=user)`; тест 8.6 |
| Правило области раздвоилось после рефакторинга `can_moderate` | Тесты `test_moderator_scope.py` и `test_moderation_service.py` без правок (шаг 2) |
| Блокировка строки «не работает» в тестах | Ограничение из 2.10: на SQLite `select_for_update` игнорируется, тесты доказывают логику проверок; блокировку проверяют CI на PostgreSQL 17 и ручная проверка |
| Пагинация теряет или дублирует запись при равных `created_at` | Тай-брейк `(created_at, kind, pk)`; тест 8.8 |
| Модератор без области видит «пусто» и думает, что работы нет | Плашка «Вам не назначены виды спорта»; тест 8.5 |

---

## 5. Прогресс

| # | Шаг | Статус |
|---|---|---|
| 1 | Ветка | [x] |
| 2 | `moderated_sport_ids()` | [x] |
| 3 | `apps/moderation/queue.py` | [x] |
| 4 | Список: представление, маршрут, шаблоны | [x] |
| 5 | Действия: форма, представление, маршрут | [x] |
| 6 | Формы в карточке без JavaScript | [x] |
| 7 | Меню, `robots.txt`, стили | [x] |
| 8 | Тесты очереди | [x] |
| 9 | Тесты действий | [x] |
| 10 | Локализация | [x] |
| 11 | Документация | [x] |
| 12 | Проверка и коммит | [x] |
