"""Представления публичного каталога.

Списки и страницы команд, событий и людей.
"""

from django.db.models import F
from django.http import QueryDict
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET

from apps.catalog.models import Event, Person, Team
from apps.catalog.roster import memberships_in_year, roster_years
from apps.core.shortcuts import (
    PHOTO_ORDERING,
    PHOTO_PAGE_SIZE,
    get_visible_or_404,
    paginate,
    photos_count,
    visible_photos,
)

TEAM_PAGE_SIZE = 24
EVENT_PAGE_SIZE = 20
# Сколько человек показываем на странице команды без фильтра по году.
# Выбранный год показывается целиком: иначе фильтр отсекал бы людей,
# ради которых его и применили.
TEAM_ROSTER_PREVIEW = 12

# Подписи известных ключей `attributes`. Ключ, которого здесь нет, выводится
# как есть: набор атрибутов зависит от вида спорта и задаётся в админке.
ATTRIBUTE_LABELS = {
    "stage": _("Этап"),
    "score": _("Счёт"),
    "result": _("Результат"),
    "innings": _("Иннингов"),
    "teams": _("Команд"),
}

# Служебные ключи: в интерфейсе не показываются.
ATTRIBUTE_SERVICE_KEYS = {"demo"}


def attribute_items(attributes):
    """Атрибуты объекта парами «подпись, значение» для шаблона.

    `attributes` — JSONB без фиксированной схемы: служебные ключи прячем,
    известные подписываем, остальные выводим как есть. Списки склеиваем
    через запятую — таблицу произвольных значений в `dl` не уложить.
    """
    items = []
    for key, value in (attributes or {}).items():
        if key in ATTRIBUTE_SERVICE_KEYS:
            continue
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        items.append({"label": ATTRIBUTE_LABELS.get(key, key), "value": value})
    return items


def gallery_filter(**params):
    """Фильтр галереи для страниц, где он не лежит в `request.GET`.

    `QueryDict` собирается из словаря, а не из строки: slug может содержать
    кириллицу, пробелы и `&`, и склейка строки сломала бы ссылку.
    """
    query = QueryDict("", mutable=True)
    query.update(params)
    return query


def team_memberships(team):
    """Состав команды из опубликованных людей.

    Черновик человека в публичную выдачу не попадает, даже если он записан
    в команду. Срез здесь не делается: режимов показа два, и какой применить,
    решает представление.
    """
    return (
        team.memberships.filter(person__in=Person.public.published())
        .select_related("person")
        .order_by(
            F("year_from").desc(nulls_last=True),
            "person__last_name",
            "person__first_name",
        )
    )


def roster_fallback_year(team):
    """Последний год, известный по команде.

    Нужен незакрытым периодам: человек с `year_from` и пустым `year_to`
    числится в команде до этого года. Считается по членствам и событиям
    команды: у молодой команды без событий верхней границей останется
    год начала членства, и это лучше, чем «до сегодняшнего дня».
    """
    years = [
        year
        for year in team.memberships.values_list("year_from", "year_to")
        for year in year
        if year
    ]
    years += [
        year for year in team.event_teams.values_list("event__year", flat=True) if year
    ]
    return max(years) if years else None


def roster_year(request, years):
    """Год фильтра состава, если он есть в списке доступных.

    Неизвестный или нечисловой параметр считается неприменённым фильтром:
    опечатка в ссылке не должна давать пустую страницу.
    """
    value = request.GET.get("year")
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if year in years else None


def team_list(request):
    """Список опубликованных команд, по алфавиту."""
    teams = (
        Team.public.published()
        .select_related("sport")
        .annotate(photos_total=photos_count())
        .order_by("name")
    )
    context = {
        "page_obj": paginate(request, teams, TEAM_PAGE_SIZE),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"label": _("Команды")},
        ],
    }
    return render(request, "catalog/team_list.html", context)


def event_list(request):
    """Список опубликованных событий: от новых к старым.

    `nulls_last=True` обязателен: в PostgreSQL при `DESC` события без года
    иначе оказались бы первыми.
    """
    events = (
        Event.public.published()
        .select_related("sport")
        .annotate(photos_total=photos_count())
        .order_by(F("year").desc(nulls_last=True), "name")
    )
    context = {
        "page_obj": paginate(request, events, EVENT_PAGE_SIZE),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"label": _("События")},
        ],
    }
    return render(request, "catalog/event_list.html", context)


@require_GET
def team_detail(request, slug):
    """Страница команды: визитка, фотографии, события и состав.

    Состав можно отфильтровать по году (`?year=1994`): архив наполняется
    по чужим альбомам, и состав за конкретный год заведомо неполный, поэтому
    фильтр отвечает на вопрос «кто известен по этому году», а не показывает
    полный состав.

    Черновик, скрытое, отклонённое, удалённое и слитое отсеивает
    `get_visible_or_404`: автор и модератор видят страницу с плашкой,
    остальные получают 404.
    """
    team = get_visible_or_404(Team, request=request, slug=slug)
    # Хелпер отдаёт объект без предвыборки связей — перечитываем один раз,
    # чтобы шаблон не делал отдельный запрос за видом спорта.
    team = Team.public.select_related("sport").get(pk=team.pk)

    photos = visible_photos().filter(team=team).select_related("team", "event")
    events = (
        Event.public.published()
        .filter(event_teams__team=team)
        .select_related("sport")
        .annotate(photos_total=photos_count())
        .order_by(F("year").desc(nulls_last=True), "name")
        .distinct()
    )
    # Состав — только из опубликованных людей: черновик человека в публичную
    # выдачу не попадает, даже если он записан в команду.
    memberships = list(team_memberships(team))
    fallback_year = roster_fallback_year(team)
    years = roster_years(memberships, fallback_year)
    selected_year = roster_year(request, years)
    if selected_year:
        # Выбранный год показываем целиком: срез отсекал бы людей, ради
        # которых фильтр и применили.
        roster = memberships_in_year(memberships, selected_year, fallback_year)
    else:
        roster = memberships[:TEAM_ROSTER_PREVIEW]

    context = {
        "team": team,
        # Фильтр не лежит в `request.GET`: кнопка «показать ещё» получает его
        # из `gallery_query`.
        "page_obj": paginate(
            request, photos.order_by(*PHOTO_ORDERING), PHOTO_PAGE_SIZE
        ),
        "gallery_query": gallery_filter(team=team.slug),
        "events": events,
        "roster": roster,
        "roster_total": len(memberships),
        "roster_years": years,
        "selected_year": selected_year,
        "attribute_items": attribute_items(team.attributes),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"url": reverse("catalog:team_list"), "label": _("Команды")},
            {"label": team.name},
        ],
    }
    return render(request, "catalog/team_detail.html", context)


@require_GET
def event_detail(request, slug):
    """Страница события: визитка, команды-участники и фотографии."""
    event = get_visible_or_404(Event, request=request, slug=slug)
    event = Event.public.select_related("sport").get(pk=event.pk)

    photos = visible_photos().filter(event=event).select_related("team", "event")
    # Команда-черновик остаётся в списке: её название выводится текстом,
    # ссылки на неопубликованную команду не будет.
    participants = event.event_teams.select_related("team")

    context = {
        "event": event,
        "page_obj": paginate(
            request, photos.order_by(*PHOTO_ORDERING), PHOTO_PAGE_SIZE
        ),
        "gallery_query": gallery_filter(event=event.slug),
        "participants": participants,
        "attribute_items": attribute_items(event.attributes),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"url": reverse("catalog:event_list"), "label": _("События")},
            {"label": event.name},
        ],
    }
    return render(request, "catalog/event_detail.html", context)


@require_GET
def person_detail(request, slug):
    """Страница человека: главное фото, биография, команды и фотографии.

    Фамилия несовершеннолетних не публикуется: заголовок, крошки и варианты
    имён строятся только из `public_name` и `is_full_name_public`.
    """
    person = get_visible_or_404(Person, request=request, slug=slug)
    # Хелпер отдаёт объект без предвыборки — перечитываем один раз, иначе
    # шаблон сделает отдельный запрос за главным фото.
    person = Person.public.select_related("avatar").get(pk=person.pk)

    photos = visible_photos().filter(photo_persons__person=person)
    # Главное фото: аватар, если он видим; иначе — самое свежее видимое фото
    # с этим человеком. Невидимый аватар игнорируется: правило прав и статуса
    # живёт в `Photo.is_public`.
    hero_photo = None
    if person.avatar_id and person.avatar.is_public:
        hero_photo = person.avatar
    else:
        hero_photo = photos.order_by(*PHOTO_ORDERING).first()

    # Команда-черновик остаётся в списке: её название выводится текстом.
    memberships = person.memberships.select_related("team").order_by(
        F("year_from").desc(nulls_last=True), "team__name"
    )

    context = {
        "person": person,
        "hero_photo": hero_photo,
        "memberships": memberships,
        # Варианты имён скрываются здесь, а не в шаблоне: прозвище раскрыло бы
        # скрытую фамилию, и правило не должно зависеть от разметки.
        "name_variants": person.names.all() if person.is_full_name_public else [],
        "page_obj": paginate(
            request,
            photos.select_related("team", "event").order_by(*PHOTO_ORDERING),
            PHOTO_PAGE_SIZE,
        ),
        "gallery_query": gallery_filter(person=person.slug),
        "attribute_items": attribute_items(person.attributes),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"label": person.public_name},
        ],
    }
    return render(request, "catalog/person_detail.html", context)
