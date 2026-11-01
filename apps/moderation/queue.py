"""Очередь черновиков: реестр моделей, отбор и карточки.

Реестр держит модели строками (`"catalog.Team"`), а модель достаёт
`apps.get_model()`: импорт на уровне модуля дал бы зависимость
`moderation` → `catalog`/`photos` и риск цикла при загрузке приложений.
Тот же приём, что в `apps/comments/targets.py`, но реестр здесь свой:
там список комментируемых целей, здесь — модели, у которых есть очередь.
Общий словарь связал бы две несвязанные причины изменения.

Правило «ждёт решения» живёт в `pending_queryset` и `is_pending` рядом:
список и действие обязаны отвечать одинаково. Разойдись они — черновик
был бы виден в очереди, но не решался бы, а это худший вид дефекта:
модератор нажимает кнопку и не понимает, почему ничего не происходит.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from django.apps import apps
from django.http import Http404
from django.utils.translation import gettext_lazy as _

from apps.core.models import Status
from apps.core.shortcuts import paginate

# Порция очереди. Меньше, чем в галерее: каждая карточка фото — ещё одна
# конвертация `django-imagekit`, а модератор смотрит очередь с телефона.
QUEUE_PAGE_SIZE = 12


@dataclass(frozen=True)
class QueueModel:
    """Модель, которая попадает в очередь черновиков."""

    key: str
    label: str
    model_label: str
    author_field: str
    needs_processed: bool = False


QUEUE_MODELS = {
    "team": QueueModel(
        key="team",
        label=_("Команда"),
        model_label="catalog.Team",
        author_field="created_by",
    ),
    "event": QueueModel(
        key="event",
        label=_("Событие"),
        model_label="catalog.Event",
        author_field="created_by",
    ),
    "photo": QueueModel(
        key="photo",
        label=_("Фото"),
        model_label="photos.Photo",
        author_field="uploaded_by",
        # Судить по необработанному фото нельзя: модератор увидел бы
        # заглушку вместо снимка.
        needs_processed=True,
    ),
}

_SPECS_BY_LABEL = {spec.model_label.lower(): spec for spec in QUEUE_MODELS.values()}


def get_queue_model(key):
    """Модель по ключу очереди. Неизвестный ключ — 404.

    Подделанный адрес не должен открывать ничего: ключ приходит из URL.
    """
    spec = QUEUE_MODELS.get(key)
    if spec is None:
        raise Http404("Объект не найден")
    return apps.get_model(spec.model_label)


def _spec_for_object(obj):
    """Реестр для объекта. Модель вне очереди — программная ошибка."""
    spec = _SPECS_BY_LABEL.get(obj._meta.label_lower)
    if spec is None:
        raise ValueError(f"Модель {obj._meta.label} не входит в очередь")
    return spec


def pending_queryset(spec, sport_ids=None, exclude_author=None):
    """Черновики, которые модератор может решить сейчас.

    `sport_ids=None` — без фильтра по видам спорта (суперадмин и админ),
    пустой список — честная пустая выборка.
    """
    model = apps.get_model(spec.model_label)
    qs = model.public.filter(status=Status.DRAFT, merged_into__isnull=True)
    if spec.needs_processed:
        # Строкой, а не `ProcessingStatus`: импорт модели фото на уровне
        # модуля дал бы зависимость `moderation` → `photos`.
        qs = qs.filter(processing_status="processed")
    if sport_ids is not None:
        qs = qs.filter(sport_id__in=sport_ids)
    if exclude_author is not None:
        qs = qs.exclude(**{spec.author_field: exclude_author})
    return qs


def is_pending(obj) -> bool:
    """Ждёт ли объект решения — то же правило для одного объекта.

    Считается той же выборкой, а не копией условий: иначе список
    и действие разошлись бы при первой же правке одного из них.
    """
    spec = _spec_for_object(obj)
    return pending_queryset(spec).filter(pk=obj.pk).exists()


def is_own(obj, spec, user) -> bool:
    """Создал ли этот пользователь объект. Своё решает другой человек.

    Одобрить себя — не решение модератора, а обход проверки: если модератор
    публикует своё, второй человек не нужен. Проверка стоит в действии,
    а не только в списке: адрес решения угадывается по номеру.
    """
    user_id = getattr(user, "pk", None)
    if user_id is None:
        # Аноним «своим» не считается: у объекта без автора пустые поля
        # совпали бы, и он стал бы недоступен модератору.
        return False
    return getattr(obj, f"{spec.author_field}_id", None) == user_id


def queue_entries(user):
    """Индекс очереди: `(created_at, kind, pk)`, старые первыми.

    Три модели не отсортировать одним SQL-запросом, поэтому сначала
    собирается индекс из двух скалярных полей. Тянуть объекты целиком
    и сортировать в Python нельзя: у фото `ImageSpecField`, и лишние
    объекты в памяти тянут за собой превью.

    Тай-брейк по `kind` и `pk` обязателен: `created_at` может совпасть
    до микросекунды, и без него запись попала бы на две страницы
    или исчезла бы.
    """
    sport_ids = user.moderated_sport_ids()
    entries = []
    for spec in QUEUE_MODELS.values():
        qs = pending_queryset(spec, sport_ids=sport_ids, exclude_author=user)
        for pk, created_at in qs.values_list("pk", "created_at"):
            entries.append((created_at, spec.key, pk))
    entries.sort()
    return entries


def queue_page(user, request):
    """Страница очереди: `(page_obj, cards)` в порядке индекса."""
    page_obj = paginate(request, queue_entries(user), QUEUE_PAGE_SIZE)

    pks_by_kind = defaultdict(list)
    for _created_at, kind, pk in page_obj.object_list:
        pks_by_kind[kind].append(pk)

    loaded = {}
    for kind, pks in pks_by_kind.items():
        spec = QUEUE_MODELS[kind]
        model = apps.get_model(spec.model_label)
        objects = model.objects.filter(pk__in=pks).select_related(spec.author_field)
        for obj in objects:
            loaded[(kind, obj.pk)] = obj

    cards = [
        build_card(loaded[(kind, pk)], QUEUE_MODELS[kind])
        for _created_at, kind, pk in page_obj.object_list
        if (kind, pk) in loaded
    ]
    return page_obj, cards


@dataclass(frozen=True)
class QueueCard:
    """Карточка очереди с готовыми полями.

    Шаблон печатает значения и ничего не сравнивает — то же правило,
    что у `Photo.upload_state` и `Event.date_label`.
    """

    kind: str
    kind_label: str
    pk: int
    title: str
    url: str
    author: str
    created_at: datetime
    image_url: str | None


def build_card(obj, spec):
    """Карточка объекта очереди."""
    return QueueCard(
        kind=spec.key,
        kind_label=str(spec.label),
        pk=obj.pk,
        title=_title(obj, spec),
        url=obj.get_absolute_url(),
        author=_author(obj, spec),
        created_at=obj.created_at,
        image_url=_image_url(obj, spec),
    )


def _title(obj, spec) -> str:
    """Заголовок карточки.

    У команды и события он уже собран в `str()` вместе с городом и годом.
    У фото `str()` отдаёт «Фото #12», а человеку нужно название снимка.
    """
    if not spec.needs_processed:
        return str(obj)
    return obj.title or obj.original_filename or str(_("Фотография"))


def _author(obj, spec) -> str:
    """Подпись автора. Пустая связь — «автор неизвестен»."""
    author = getattr(obj, spec.author_field, None)
    if author is None:
        return str(_("автор неизвестен"))
    return author.display_name or author.email


def _image_url(obj, spec) -> str | None:
    """Адрес превью или `None`.

    Битый файл в хранилище не должен ронять всю очередь: карточка без
    картинки лучше пустой страницы. У события картинки нет вовсе.

    Поле выбирается по реестру, а не через `getattr` по двум именам:
    обращение к `ImageSpecField` у фото без `display` упало бы на
    построении спецификации ещё до `try`.
    """
    image = obj.thumbnail if spec.key == "photo" else getattr(obj, "logo", None)
    if not image:
        return None
    try:
        return image.url
    except (OSError, ValueError):
        return None
