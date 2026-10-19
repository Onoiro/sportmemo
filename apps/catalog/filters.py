"""Фильтры публичных списков каталога: команды и события.

Логика отбора живёт здесь, а не в шаблонах и не в представлениях: справочники
вариантов, разбор параметров и адрес `canonical` должны собираться в одном
месте, иначе шаблон начнёт сравнивать строки, а представление — склеивать
адреса. То же правило, что у `apps/catalog/roster.py` в подэтапе 2.7.

Состояние фильтров живёт в query-параметрах: ссылку можно сохранить
и переслать, JavaScript не нужен. Невалидное значение считается
неприменённым фильтром, а не пустой выдачей: опечатка в ссылке не должна
выглядеть как «ничего не найдено».
"""

from collections import Counter, defaultdict
from dataclasses import dataclass

from django.db.models import F
from django.http import QueryDict
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Event, EventType, Team
from apps.core.shortcuts import photos_count

# Подпись десятилетия: «1980-е». Год в подписи — начало десятилетия.
DECADE_LABEL = _("%(start)s-е")

TEAM_SORT_CHOICES = (
    ("name", _("По названию")),
    ("photos", _("По числу фотографий")),
)
DEFAULT_TEAM_SORT = "name"
TEAM_SORT_ORDER = {
    "name": ("name",),
    "photos": ("-photos_total", "name"),
}

EVENT_SORT_CHOICES = (
    ("new", _("Сначала новые")),
    ("old", _("Сначала старые")),
)
DEFAULT_EVENT_SORT = "new"
# `nulls_last` обязателен: в PostgreSQL при `DESC` события без года иначе
# оказались бы первыми.
EVENT_SORT_ORDER = {
    "new": (F("year").desc(nulls_last=True), "name"),
    "old": (F("year").asc(nulls_last=True), "name"),
}


@dataclass(frozen=True)
class SelectOption:
    """Вариант выпадающего списка."""

    value: str
    label: str
    selected: bool = False


@dataclass(frozen=True)
class SelectField:
    """Поле панели фильтров: подпись, варианты, выбранное значение.

    `all_label` пустой у сортировки: у порядка выдачи нет варианта «все».
    """

    name: str
    label: str
    options: tuple
    all_label: str = ""
    selected: str = ""
    is_active: bool = False


@dataclass(frozen=True)
class FilterPanel:
    """Готовая панель фильтров для шаблона.

    Шаблон только выводит поля: ни одного сравнения `request.GET` в разметке.
    `has_active` не учитывает сортировку — порядок выдачи не сужает выборку,
    и «Сбросить фильтры» не должен его менять.
    """

    fields: tuple
    sort: SelectField
    has_active: bool
    reset_url: str


@dataclass(frozen=True)
class ListFilters:
    """Результат отбора: выборка, панель и нормализованный запрос.

    `query` содержит только валидные применённые параметры: мусор в адресе
    не должен попадать в `canonical` и создавать «разные» адреса одной выдачи.
    """

    queryset: object
    panel: FilterPanel
    query: QueryDict


def decade_bounds(start):
    """Границы десятилетия по его началу: 1980 → (1980, 1989)."""
    return start, start + 9


def normalize_city(value):
    """Ключ сравнения городов: без регистра и лишних пробелов.

    «Москва», «москва» и « Москва » — один город: в архиве, который
    наполняют разные люди, написание расходится.
    """
    return " ".join(str(value).split()).casefold()


def city_choices(queryset):
    """Города, которые есть в выборке, по алфавиту.

    Варианты склеиваются по `normalize_city`: «Москва» и «москва» — один
    город. Подписью служит самое частое написание, при равенстве — первое
    по алфавиту: в архиве, который наполняют разные люди, написание
    расходится, и показывать случайное из них не стоит. Пустые значения
    пропускаются: вариант «без города» в фильтре выглядел бы как отдельный
    город.
    """
    spellings = defaultdict(Counter)
    for city in queryset.exclude(city="").values_list("city", flat=True):
        key = normalize_city(city)
        if key:
            spellings[key][city.strip()] += 1

    labels = [
        min(counter.items(), key=lambda item: (-item[1], item[0]))[0]
        for counter in spellings.values()
    ]
    return [(label, label) for label in sorted(labels, key=normalize_city)]


def decade_choices(queryset):
    """Десятилетия основания, от новых к старым."""
    starts = {
        year // 10 * 10
        for year in queryset.exclude(founded_year__isnull=True).values_list(
            "founded_year", flat=True
        )
    }
    return [
        (str(start), DECADE_LABEL % {"start": start})
        for start in sorted(starts, reverse=True)
    ]


def year_choices(queryset):
    """Годы событий, от новых к старым."""
    years = set(queryset.exclude(year__isnull=True).values_list("year", flat=True))
    return [(str(year), str(year)) for year in sorted(years, reverse=True)]


def type_choices(queryset):
    """Типы событий, которые есть в выборке.

    Порядок — из `EventType`, а не по алфавиту: так варианты читаются
    как в админке, и подпись берётся из переводов модели.
    """
    present = set(queryset.values_list("event_type", flat=True))
    return [(value, label) for value, label in EventType.choices if value in present]


def selected_value(request, name, allowed):
    """Значение параметра, если оно есть среди допустимых.

    Возвращает пустую строку для мусора: фильтр с опечаткой не применяется.
    """
    value = (request.GET.get(name) or "").strip()
    return value if value in allowed else ""


def selected_city(request, choices):
    """Выбранный город с написанием из справочника.

    Сравнение идёт по нормализованному ключу, а в выборку и в адрес попадает
    написание из базы: `?city=москва` находит «Москва» и не плодит адреса.
    """
    wanted = normalize_city(request.GET.get("city") or "")
    if not wanted:
        return ""
    for value, _label in choices:
        if normalize_city(value) == wanted:
            return value
    return ""


def _select(name, label, all_label, choices, selected):
    """Поле фильтра с отметкой выбранного варианта."""
    return SelectField(
        name=name,
        label=label,
        options=tuple(
            SelectOption(
                value=value,
                label=str(text),
                selected=value == selected,
            )
            for value, text in choices
        ),
        all_label=all_label,
        selected=selected,
        is_active=bool(selected),
    )


def _sort_field(choices, selected):
    """Поле сортировки: варианта «все» нет, активным фильтром не считается."""
    return SelectField(
        name="sort",
        label=_("Сортировка"),
        options=tuple(
            SelectOption(
                value=value,
                label=str(text),
                selected=value == selected,
            )
            for value, text in choices
        ),
        selected=selected,
    )


def _query(params):
    """`QueryDict` только из непустых параметров."""
    query = QueryDict("", mutable=True)
    for name, value in params.items():
        if value:
            query[name] = value
    return query


def _reset_url(request, sort, default_sort):
    """Адрес сброса: чистый список, сортировка сохраняется.

    Порядок выдачи — не фильтр: сбрасывать его вместе с условиями значит
    менять то, о чём пользователь не просил.
    """
    if sort == default_sort:
        return request.path
    return f"{request.path}?{_query({'sort': sort}).urlencode()}"


def team_filters(request):
    """Фильтры списка команд: город и десятилетие основания.

    Справочники считаются по всей публичной выборке, а не по отфильтрованной:
    иначе после выбора десятилетия из списка городов исчезли бы города,
    в которых в этом десятилетии команд нет, и снять фильтр было бы нечем.
    """
    published = Team.public.published()
    cities = city_choices(published)
    decades = decade_choices(published)

    city = selected_city(request, cities)
    decade = selected_value(request, "decade", {value for value, _ in decades})
    sort = selected_value(request, "sort", set(TEAM_SORT_ORDER)) or DEFAULT_TEAM_SORT

    queryset = published.annotate(photos_total=photos_count())
    if city:
        queryset = queryset.filter(city__iexact=city)
    if decade:
        start, end = decade_bounds(int(decade))
        queryset = queryset.filter(founded_year__gte=start, founded_year__lte=end)
    queryset = queryset.order_by(*TEAM_SORT_ORDER[sort])

    panel = FilterPanel(
        fields=(
            _select("city", _("Город"), _("Все города"), cities, city),
            _select(
                "decade",
                _("Год основания"),
                _("Все десятилетия"),
                decades,
                decade,
            ),
        ),
        sort=_sort_field(TEAM_SORT_CHOICES, sort),
        has_active=bool(city or decade),
        reset_url=_reset_url(request, sort, DEFAULT_TEAM_SORT),
    )
    query = _query(
        {
            "city": city,
            "decade": decade,
            "sort": "" if sort == DEFAULT_TEAM_SORT else sort,
        }
    )
    return ListFilters(queryset=queryset, panel=panel, query=query)


def event_filters(request):
    """Фильтры списка событий: год, тип и город."""
    published = Event.public.published().select_related("sport")
    years = year_choices(published)
    types = type_choices(published)
    cities = city_choices(published)

    year = selected_value(request, "year", {value for value, _ in years})
    event_type = selected_value(request, "type", {value for value, _ in types})
    city = selected_city(request, cities)
    sort = selected_value(request, "sort", set(EVENT_SORT_ORDER)) or DEFAULT_EVENT_SORT

    queryset = published.annotate(photos_total=photos_count())
    if year:
        queryset = queryset.filter(year=int(year))
    if event_type:
        queryset = queryset.filter(event_type=event_type)
    if city:
        queryset = queryset.filter(city__iexact=city)
    queryset = queryset.order_by(*EVENT_SORT_ORDER[sort])

    panel = FilterPanel(
        fields=(
            _select("year", _("Год"), _("Все годы"), years, year),
            _select("type", _("Тип события"), _("Все типы"), types, event_type),
            _select("city", _("Город"), _("Все города"), cities, city),
        ),
        sort=_sort_field(EVENT_SORT_CHOICES, sort),
        has_active=bool(year or event_type or city),
        reset_url=_reset_url(request, sort, DEFAULT_EVENT_SORT),
    )
    query = _query(
        {
            "year": year,
            "type": event_type,
            "city": city,
            "sort": "" if sort == DEFAULT_EVENT_SORT else sort,
        }
    )
    return ListFilters(queryset=queryset, panel=panel, query=query)
