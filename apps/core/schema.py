"""Сборка микроразметки Schema.org (JSON-LD).

Разметка собирается в Python, а не в шаблонах, по той же причине, что
и метатеги (`apps/core/seo.py`): правило приватности имени и выбор картинки
должны жить в одном месте. Шаблон только сериализует готовый словарь.

Функции возвращают `dict` или `None` (страница без разметки), а `compact()`
выбрасывает пустые блоки — представление не думает о том, что показывать.
"""

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.core.seo import SITE_NAME, absolute

SCHEMA_CONTEXT = "https://schema.org"
# Значение `query-input` для `SearchAction`: без него Google не показывает
# поисковую строку сайта в выдаче.
SEARCH_QUERY_INPUT = "required name=search_term_string"


def compact(*blocks):
    """Список непустых блоков разметки.

    `None` приходит от страниц, которым разметка не положена: партиал
    выводит пустоту, а представление не проверяет каждую ветку.
    """
    return [block for block in blocks if block]


def _page_url(request, obj) -> str:
    return absolute(request, obj.get_absolute_url())


def website_schema(request) -> dict:
    """`WebSite` с поисковой строкой — только для главной.

    `urlTemplate` ведёт на `/search/?q=...`: по этому адресу открывается
    страница результатов (`search:index`).
    """
    return {
        "@context": SCHEMA_CONTEXT,
        "@type": "WebSite",
        "name": SITE_NAME,
        "url": absolute(request, reverse("core:home")),
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": (
                    f"{absolute(request, reverse('search:index'))}"
                    "?q={search_term_string}"
                ),
            },
            "query-input": SEARCH_QUERY_INPUT,
        },
    }


def team_schema(request, team) -> dict:
    """`SportsTeam`: название, город, эмблема, год основания, вид спорта."""
    block = {
        "@context": SCHEMA_CONTEXT,
        "@type": "SportsTeam",
        "name": team.name,
        "url": _page_url(request, team),
        "sport": team.sport.name,
    }
    if team.city:
        block["address"] = {"@type": "PostalAddress", "addressLocality": team.city}
    if team.founded_year:
        block["foundingDate"] = str(team.founded_year)
    if team.logo:
        try:
            block["logo"] = absolute(request, team.logo.url)
        except (OSError, ValueError):
            # Битый файл эмблемы не повод терять всю разметку команды.
            pass
    return block


def event_schema(request, event) -> dict:
    """`SportsEvent`: название, даты и место."""
    block = {
        "@context": SCHEMA_CONTEXT,
        "@type": "SportsEvent",
        "name": event.name,
        "url": _page_url(request, event),
    }
    # `str(date)` даёт ISO 8601 — тот формат, которого ждёт Schema.org.
    # Дата может прийти строкой (фикстуры, data-миграции), поэтому `str`,
    # а не `isoformat()`.
    if event.date_from:
        block["startDate"] = str(event.date_from)
    if event.date_to:
        block["endDate"] = str(event.date_to)

    address = {}
    if event.city:
        address["addressLocality"] = event.city
    if event.country:
        address["addressCountry"] = event.country
    if address:
        place = {"@type": "Place", "address": {"@type": "PostalAddress", **address}}
        if event.city:
            place["name"] = event.city
        block["location"] = place
    return block


def person_schema(request, person) -> dict | None:
    """`Person` — только имя, без фамилии при скрытой видимости.

    `public_name` уже учитывает `name_visibility`: при `first_name_only`
    это имя, при `hidden` — нейтральная подпись. Страницу со скрытым именем
    в разметку не отдаём вовсе: нейтральная подпись как «имя человека»
    вводила бы поисковик в заблуждение.
    """
    from apps.catalog.models import NameVisibility

    if person.name_visibility == NameVisibility.HIDDEN:
        return None
    return {
        "@context": SCHEMA_CONTEXT,
        "@type": "Person",
        "name": person.public_name,
        "url": _page_url(request, person),
    }


def photo_schema(request, photo) -> dict | None:
    """`ImageObject` для страницы фотографии.

    `contentUrl` — превью, а не оригинал: поисковику нужна картинка,
    которую он покажет в выдаче, а не многомегабайтный скан.
    """
    image = photo.preview or photo.original
    try:
        content_url = absolute(request, image.url)
    except (OSError, ValueError):
        return None

    block = {
        "@context": SCHEMA_CONTEXT,
        "@type": "ImageObject",
        "contentUrl": content_url,
        "url": _page_url(request, photo),
        "name": photo.title or str(_("Фотография")),
    }
    if photo.caption:
        block["description"] = photo.caption
    if photo.width and photo.height:
        block["width"] = photo.width
        block["height"] = photo.height
    return block
