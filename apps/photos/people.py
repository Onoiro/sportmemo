"""Кто может быть отмечен на фото и как его подписать.

Правило видимости одно на подсказку и на резолв: если подсказка
предложит человека, которого резолв потом не найдёт, автор увидит
в списке имя, а после отправки — «не нашли такого». Поэтому
`person_options()` и `resolve_person()` работают через одну выборку
`_visible_persons()`.

Приватность имени решается здесь, а не в шаблоне: скрытый человек
не показывается вовсе, а фамилия и варианты имён участвуют в поиске
только при полной видимости — триграммный индекс хранит «сырую»
фамилию, и поиск по ней раскрыл бы то, что `public_name` прячет.
`display_name` не ищется никогда.

Порядок кандидатов повторяет то, как человек вспоминает снимок: сначала
люди из состава команды того года, потом остальные. Логика периодов
не дублируется, её даёт `apps.catalog.roster.membership_years()`.
"""

from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.db.models import Case, IntegerField, Q, Value, When

from apps.catalog.models import NameKind, NameVisibility, Person, PersonName
from apps.catalog.roster import membership_years
from apps.core.models import Status
from apps.moderation.services import initial_status_for
from apps.photos.models import PhotoPerson

# Сколько кандидатов показывать в подсказке и в поиске. Больше человек
# не прочитает с телефона, а запрос на строку подписи дорожает.
SEARCH_LIMIT = 20

# Предел людей на одном фото: защита от двойного клика и от мусора.
MAX_PHOTO_PEOPLE = 30


@dataclass(frozen=True)
class PersonOption:
    """Готовый кандидат: объект, подпись и признак состава команды."""

    person: Person
    label: str
    is_team_member: bool


def _normalize(value) -> str:
    """Схлопнуть пробелы и привести к одному регистру для сравнения."""
    return " ".join(str(value or "").split()).casefold()


def _visible_persons(photo, user):
    """Люди, которых автор вправе увидеть в кандидатах.

    `Person.public` не отдаёт удалённых и слитых. Опубликованные видны
    всем, свои черновики — автору, уже отмеченные на этом фото — тоже
    (иначе снять отметку с чужого черновика было бы нечем). Скрытое имя
    исключено всегда: у такого человека нет публичной подписи.
    """
    visible = Q(status=Status.PUBLISHED) | Q(photo_persons__photo=photo)
    if user is not None and user.is_authenticated:
        visible |= Q(created_by=user)
    return Person.public.filter(visible).exclude(name_visibility=NameVisibility.HIDDEN)


def _match_q(text: str) -> Q:
    """Условие поиска по введённому тексту.

    Имя ищется всегда. Фамилия, отчество и варианты имён — только при
    полной видимости: у человека с `first_name_only` фамилия в базе есть,
    но публиковать её нельзя.
    """
    full = Q(name_visibility=NameVisibility.FULL)
    matched = Q(first_name__icontains=text)
    matched |= Q(last_name__icontains=text) & full
    matched |= Q(middle_name__icontains=text) & full
    matched |= Q(names__value__icontains=text) & full
    return matched


def _word_q(words) -> Q:
    """Сузить выборку до людей, у которых встречается хотя бы одно слово.

    Резолв всё равно сравнивает точные имена, а этот фильтр только
    избавляет от чтения всего архива ради нескольких совпадений.
    """
    narrowed = Q()
    for word in words:
        narrowed |= Q(first_name__iexact=word)
        narrowed |= Q(last_name__iexact=word)
        narrowed |= Q(middle_name__iexact=word)
        narrowed |= Q(names__value__icontains=word)
    return narrowed


def _roster_ids(photo) -> set[int]:
    """Люди из состава команды фото за его год.

    Год у фото не заполнен — весь состав команды: гадать, какой год имел
    в виду автор, нельзя. Незакрытые периоды считает `membership_years()`
    с годом фото как верхней границей.
    """
    if not photo.team_id:
        return set()

    ids = set()
    for membership in photo.team.memberships.all():
        if photo.year is None or photo.year in membership_years(membership, photo.year):
            ids.add(membership.person_id)
    return ids


def _ordered(queryset, roster_ids):
    """Сначала состав команды, затем обычный порядок по фамилии и имени."""
    order = []
    if roster_ids:
        order.append(
            Case(
                When(pk__in=roster_ids, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
    return queryset.order_by(*order, "last_name", "first_name", "pk")


def _membership_team(person, team_id):
    """Команда для подписи: команда фото, иначе последняя по годам."""
    memberships = list(person.memberships.all())
    for membership in memberships:
        if team_id and membership.team_id == team_id:
            return membership.team
    return memberships[0].team if memberships else None


def _membership_year(person):
    """Последний известный год членства — замена году рождения в подписи."""
    years = [
        year
        for membership in person.memberships.all()
        for year in (membership.year_to, membership.year_from)
        if year
    ]
    return max(years) if years else None


def _option(person, *, team_id, roster_ids) -> PersonOption:
    """Собрать подпись один раз: шаблон печатает готовую строку.

    «Иванов Сергей» без контекста ничего не говорит, когда Ивановых
    в архиве пятеро, поэтому в подпись идут год и команда.
    """
    parts = [person.public_name]
    year = person.birth_year or _membership_year(person)
    if year:
        parts.append(str(year))
    team = _membership_team(person, team_id)
    if team:
        parts.append(team.name)
    return PersonOption(
        person=person,
        label=", ".join(parts),
        is_team_member=person.pk in roster_ids,
    )


def _prepared(photo, user, *, text=""):
    """Общая выборка кандидатов: видимость, фильтр, порядок, предвыборка."""
    queryset = _visible_persons(photo, user)
    if text:
        queryset = queryset.filter(_match_q(text))
    roster_ids = _roster_ids(photo)
    queryset = _ordered(queryset.distinct(), roster_ids)
    # Подпись берёт команду и год из членств: без предвыборки это был бы
    # запрос на каждую строку списка.
    return queryset.prefetch_related("memberships__team", "names"), roster_ids


def person_options(photo, user, query="") -> list[PersonOption]:
    """Кандидаты для подсказки и поиска, лучшие первыми."""
    queryset, roster_ids = _prepared(photo, user, text=_normalize(query))
    return [
        _option(person, team_id=photo.team_id, roster_ids=roster_ids)
        for person in queryset[:SEARCH_LIMIT]
    ]


def _match_names(person) -> set[str]:
    """Все допустимые написания имени для точного сравнения."""
    names = {_normalize(person.public_name)}
    if not person.is_full_name_public:
        # У `first_name_only` и `hidden` фамилия не публикуется: сравнивать
        # с полным именем значило бы находить человека по скрытой фамилии.
        return names
    names.add(_normalize(person.full_name))
    names.add(_normalize(f"{person.last_name} {person.first_name}"))
    names.add(
        _normalize(f"{person.last_name} {person.first_name} {person.middle_name}")
    )
    names.update(_normalize(variant.value) for variant in person.names.all())
    return names


def resolve_person(photo, user, value) -> list[PersonOption]:
    """Кандидаты по введённому имени: 0 — не нашли, >1 — уточнить.

    Сравнение точное и с обратным порядком: «Иванов Сергей» и «Сергей
    Иванов» одинаково часты, а угадывание дало бы отметку не на того.
    """
    text = _normalize(value)
    if not text:
        return []

    words = text.split()
    queryset, roster_ids = _prepared(photo, user, text="")
    queryset = queryset.filter(_word_q(words))
    matched = [person for person in queryset if text in _match_names(person)]
    return [
        _option(person, team_id=photo.team_id, roster_ids=roster_ids)
        for person in matched[:SEARCH_LIMIT]
    ]


def people_limit_reached(photo) -> bool:
    """Исчерпан ли предел людей на одном фото."""
    return photo.photo_persons.count() >= MAX_PHOTO_PEOPLE


def add_person(photo, person, *, user, role, position="") -> PhotoPerson | None:
    """Отметить человека на фото. `None` — уже отмечен или предел.

    `get_or_create` держит двойной клик по «Добавить»: уникальность пары
    `(photo, person)` не даст второй связи, а автор увидит «уже отмечен»
    вместо страницы с ошибкой. `IntegrityError` при гонке двух запросов
    тоже означает «уже отмечен», поэтому наружу он не выходит.
    """
    if people_limit_reached(photo):
        return None
    try:
        with transaction.atomic():
            link, created = PhotoPerson.objects.get_or_create(
                photo=photo,
                person=person,
                defaults={"role": role, "position": position, "added_by": user},
            )
    except IntegrityError:
        return None
    return link if created else None


def remove_person(photo, link) -> bool:
    """Снять отметку. `False` — связи уже нет или она с другого фото.

    Номер связи угадывается по порядку, поэтому принадлежность фото
    проверяется здесь, а не только правом на правку: иначе по чужому
    номеру можно было бы снять отметку с другого снимка.
    """
    if link.photo_id != photo.pk:
        return False
    deleted, _ = PhotoPerson.objects.filter(pk=link.pk, photo=photo).delete()
    return bool(deleted)


def create_person_for_photo(
    photo, person, *, user, role, position="", alias=""
) -> Person:
    """Сохранить человека, вариант имени и отметку одной транзакцией.

    Человек заводится из фото и почти всегда нужен на нём, поэтому
    отметка создаётся сразу. Ошибка на втором шаге откатила бы и человека,
    иначе в архиве остался бы сирота без единого снимка.

    Введённое в поиске имя пишется в `PersonName`: «Алеша Петров» иначе
    не найдётся ни поиском, ни триграммами. Мусорный вариант (пустой или
    равный полному имени) не пишется — он мешал бы слиянию дубликатов.
    """
    alias = " ".join(str(alias or "").split())
    with transaction.atomic():
        # Предел проверяется до сохранения человека: заведённый без
        # отметки человек остался бы в архиве сиротой, а откатывать
        # успешное создание молча — терять ввод автора.
        if people_limit_reached(photo):
            raise ValueError("Предел людей на фото исчерпан")
        person.status = initial_status_for(user)
        person.created_by = user
        person.save()
        if alias and _normalize(alias) != _normalize(person.full_name):
            PersonName.objects.get_or_create(
                person=person,
                value=alias,
                defaults={"kind": NameKind.ALT_SPELLING},
            )
        add_person(photo, person, user=user, role=role, position=position)
    return person
