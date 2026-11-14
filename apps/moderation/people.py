"""Очередь черновиков людей: отбор, карточки и публикация вместе с фото.

Люди стоят отдельно от общего реестра `QUEUE_MODELS`: у `Person` нет вида
спорта, а `pending_queryset()` фильтрует `sport_id__in`, и на этой модели
выборка падала бы `FieldError`. Обходной флаг «модель без вида спорта»
завёл бы в общей очереди две разные карточки и второй набор условий.

Правило «ждёт решения» живёт в `_pending_qs`, `pending_people` и
`is_pending_person` рядом: список и действие обязаны отвечать одинаково.
Разойдись они — человек был бы виден в очереди, но не решался бы.

Модели `catalog` и `photos` достаются через `apps.get_model()` внутри
функций: импорт на уровне модуля дал бы зависимость `moderation` →
`catalog`/`photos` и риск цикла при загрузке приложений (то же правило,
что в `queue.py`).
"""

from dataclasses import dataclass
from datetime import datetime

from django.apps import apps
from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import Status
from apps.core.shortcuts import paginate
from apps.moderation import services

# Порция очереди. Карточка человека с превью, а модератор смотрит
# с телефона — столько же, сколько в очереди черновиков.
PEOPLE_PAGE_SIZE = 12


def _PersonModel():
    return apps.get_model("catalog.Person")


def _PhotoModel():
    return apps.get_model("photos.Photo")


def _PhotoPersonModel():
    return apps.get_model("photos.PhotoPerson")


def _pending_qs():
    """Черновики людей без учёта автора: одно условие на оба пути.

    `public` уже отсекает удалённых, но `merged_into` проверяется явно:
    слияние помечает источник удалённым, и полагаться на это косвенно
    значит сломать правило при первой же правке `merge_into()`.
    """
    return _PersonModel().public.filter(status=Status.DRAFT, merged_into__isnull=True)


def pending_people(user):
    """Черновики людей для списка, старые первыми.

    Своих исключаем через `Q(created_by__isnull=True) | ~Q(...)`:
    `exclude(created_by=user)` выбросил бы и людей без автора, потому что
    сравнение с `NULL` не бывает истинным.

    Фильтра по видам спорта нет вовсе: у человека вида спорта нет и быть
    не может (инвариант № 1), поэтому очередь общая для всех модераторов.
    """
    qs = _pending_qs().select_related("created_by")
    user_id = getattr(user, "pk", None)
    if user_id is not None:
        qs = qs.filter(Q(created_by__isnull=True) | ~Q(created_by_id=user_id))
    return qs.order_by("created_at", "pk")


def is_pending_person(person) -> bool:
    """Ждёт ли человек решения — то же правило для одного объекта.

    Считается общей выборкой, а не копией условий: иначе список и действие
    разошлись бы при первой же правке одного из них. Исключение автора
    сюда не входит: «свой» относится к конкретному пользователю.
    """
    return _pending_qs().filter(pk=person.pk).exists()


def is_own_person(person, user) -> bool:
    """Создал ли этого человека модератор. Своего решает другой человек.

    Одобрить себя — не решение модератора, а обход проверки. Правило стоит
    и в выборке, и в действии: номер человека угадывается по порядку.
    """
    user_id = getattr(user, "pk", None)
    if user_id is None:
        # Аноним «своим» не считается: у человека без автора пустые поля
        # совпали бы, и он стал бы недоступен модератору.
        return False
    return person.created_by_id == user_id


def _membership_year(membership) -> int | None:
    return membership.year_to or membership.year_from


def _membership_fields(person) -> tuple[int | None, str]:
    """Год и команда для карточки: год рождения или последний год членства.

    `memberships` предвыбраны в `people_page`, поэтому запросов здесь нет.
    Команда выбирается по последнему году явно, а не порядком выборки:
    у членства без годов `year_from` пуст, и сортировка по убыванию
    поставила бы его первым.
    """
    memberships = list(person.memberships.all())
    latest = max(memberships, key=lambda m: _membership_year(m) or 0, default=None)
    year = person.birth_year or (latest and _membership_year(latest)) or None
    team = latest.team.name if latest else ""
    return year, team


def _author(person) -> str:
    """Подпись автора. Пустая связь — «автор неизвестен»."""
    author = person.created_by
    if author is None:
        return str(_("автор неизвестен"))
    return author.display_name or author.email


def _photo_fields(photo) -> tuple[str, str | None, str, str | None]:
    """Название снимка, ссылка, подпись вместо ссылки и превью.

    Ссылка строится только у живого снимка: удалённый отдаёт 404 даже
    автору, а слитый уводит на канон, и модератор попал бы не туда, куда
    ведёт подпись. Модель фото без названия подписывается «Фотография»,
    а не «Фото #12»: номер модератору ничего не говорит.
    """
    if photo is None:
        return "", None, str(_("снимок не указан")), None

    title = photo.title or photo.original_filename or str(_("Фотография"))
    if photo.is_deleted or photo.merged_into_id:
        return title, None, str(_("снимок удалён")), None
    return title, photo.get_absolute_url(), "", _image_url(photo)


def _image_url(photo) -> str | None:
    """Адрес превью или `None`.

    Битый файл в хранилище не должен ронять очередь: карточка без картинки
    лучше страницы с ошибкой.
    """
    try:
        image = photo.thumbnail
        return image.url if image else None
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class PersonCard:
    """Карточка человека с готовыми полями.

    Шаблон печатает значения и ничего не сравнивает — то же правило,
    что у `Photo.upload_state`, `Event.date_label` и `QueueCard`.
    """

    pk: int
    name: str
    visibility_label: str
    year: int | None
    team: str
    author: str
    created_at: datetime
    photo_title: str
    photo_url: str | None
    photo_note: str
    image_url: str | None


def build_card(person, photo=None) -> PersonCard:
    """Карточка человека.

    Имя — `public_name`, а не `display_name`: служебная страница не повод
    раскрывать скрытую фамилию. Подпись видимости рядом показывает
    модератору, что именно он публикует.
    """
    year, team = _membership_fields(person)
    title, url, note, image_url = _photo_fields(photo)
    return PersonCard(
        pk=person.pk,
        name=person.public_name,
        visibility_label=str(person.get_name_visibility_display()),
        year=year,
        team=team,
        author=_author(person),
        created_at=person.created_at,
        photo_title=title,
        photo_url=url,
        photo_note=note,
        image_url=image_url,
    )


def _photos_for(people) -> dict[int, object]:
    """Первое фото каждого человека пачкой.

    Обратная связь `photo_persons` дала бы запрос на карточку, и число
    запросов росло бы вместе с порцией. Два запроса на всю страницу
    дешевле любого кэша.
    """
    person_ids = [person.pk for person in people]
    if not person_ids:
        return {}

    links = (
        _PhotoPersonModel()
        .objects.filter(person_id__in=person_ids)
        .order_by("photo_id")
    )
    first_photo = {}
    for link in links:
        first_photo.setdefault(link.person_id, link.photo_id)

    if not first_photo:
        return {}

    loaded = {
        photo.pk: photo
        for photo in _PhotoModel().objects.filter(pk__in=first_photo.values())
    }
    return {
        person_id: loaded[photo_id]
        for person_id, photo_id in first_photo.items()
        if photo_id in loaded
    }


def people_page(user, request):
    """Страница очереди людей: `(page_obj, cards)` со старых черновиков."""
    qs = pending_people(user).prefetch_related("memberships__team")
    page_obj = paginate(request, qs, PEOPLE_PAGE_SIZE)
    people = list(page_obj.object_list)
    photos = _photos_for(people)
    cards = [build_card(person, photos.get(person.pk)) for person in people]
    return page_obj, cards


def publish_people_for_photo(photo, user) -> tuple[int, int]:
    """Опубликовать черновики людей на снимке: `(опубликовано, своих)`.

    Цикл по `publish_object()`, а не запись статуса пачкой: журнал решений
    обязан получить запись на каждого человека (инвариант № 6), а сервис —
    единственная точка и решения, и журнала.

    Своих людей пропускаем: иначе правило «своего решает другой модератор»
    обходилось бы через чужое фото — модератор опубликовал бы себя,
    отметившись на снимке другого автора.
    """
    person_ids = (
        _PhotoPersonModel()
        .objects.filter(photo_id=photo.pk)
        .values_list("person_id", flat=True)
    )
    drafts = _pending_qs().filter(pk__in=person_ids)

    published = 0
    skipped = 0
    for person in drafts:
        if is_own_person(person, user):
            skipped += 1
            continue
        if services.publish_object(person, user):
            published += 1
    return published, skipped


def draft_people_counts(photo_ids) -> dict[int, int]:
    """Число черновиков людей по фото: `photo_id → количество`.

    Один запрос на всю страницу очереди: счётчик на карточку дал бы
    запрос на каждое фото, и страница росла бы вместе с порцией.
    """
    ids = list(photo_ids)
    if not ids:
        return {}

    rows = (
        _PhotoPersonModel()
        .objects.filter(
            photo_id__in=ids,
            person__status=Status.DRAFT,
            person__deleted_at__isnull=True,
            person__merged_into__isnull=True,
        )
        .values("photo_id")
        .annotate(total=Count("pk", distinct=True))
    )
    return {row["photo_id"]: row["total"] for row in rows}
