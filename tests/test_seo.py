"""Тесты карты сайта, `robots.txt` и микроразметки (подэтап 2.11).

Проверяем смысл, а не разметку XML: в карту попадает только публичное,
`robots.txt` закрывает служебные разделы, а микроразметка не раскрывает
скрытую фамилию и не ломает страницу кавычкой или `</script>`.
"""

import json
import re
from xml.etree import ElementTree

import pytest
from django.contrib.sites.models import Site
from django.urls import reverse

from apps.catalog.models import NameVisibility, Person, Team
from apps.core.models import Status
from apps.core.seo import SITE_NAME
from tests.factories import EventFactory, PhotoFactory

pytestmark = pytest.mark.django_db

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
# Домен карты сайта задаёт `django.contrib.sites` (data-миграция), а адреса
# микроразметки строит `seo.absolute()` из запроса — в тестах это testserver.
SITE_DOMAIN = "sportmemo.ru"
REQUEST_DOMAIN = "testserver"

SITEMAP_SECTIONS = ["pages", "teams", "events", "people", "photos"]


def make_team(baseball, user, name="Соколы", **kwargs):
    kwargs.setdefault("status", Status.PUBLISHED)
    kwargs.setdefault("city", "Москва")
    return Team.objects.create(sport=baseball, name=name, created_by=user, **kwargs)


def make_person(user, first_name="Иван", last_name="Смирнов", **kwargs):
    kwargs.setdefault("status", Status.PUBLISHED)
    return Person.objects.create(
        first_name=first_name, last_name=last_name, created_by=user, **kwargs
    )


def parse_xml(response):
    """Разобрать ответ как XML: битая разметка должна падать здесь."""
    return ElementTree.fromstring(response.content)


def locs(root):
    """Адреса из `<url><loc>` или `<sitemap><loc>`."""
    return [node.text for node in root.iter(f"{{{SITEMAP_NS}}}loc")]


def jsonld_blocks(html):
    """Разобрать блоки JSON-LD страницы в словари.

    Регулярка безопасна: опасные символы тег `{% jsonld %}` заменяет
    на `\\uXXXX`, поэтому сырого `</script>` внутри блока быть не может.
    """
    raw = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    return [json.loads(item) for item in raw]


def jsonld_of_type(html, schema_type):
    """Первый блок разметки нужного типа или `None`."""
    for block in jsonld_blocks(html):
        if block.get("@type") == schema_type:
            return block
    return None


def page_blocks(client, url):
    """Блоки разметки страницы по адресу."""
    return jsonld_blocks(client.get(url).content.decode())


def page_schema(client, url, schema_type):
    """Блок разметки нужного типа по адресу или `None`."""
    return jsonld_of_type(client.get(url).content.decode(), schema_type)


@pytest.fixture
def team(baseball, user):
    return make_team(baseball, user)


@pytest.fixture
def person(user):
    return make_person(user)


@pytest.fixture
def event(baseball, user):
    return EventFactory(
        sport=baseball,
        name="Первенство России",
        status=Status.PUBLISHED,
        created_by=user,
    )


class TestSitemapIndex:
    def test_index_returns_xml(self, client):
        response = client.get(reverse("django.contrib.sitemaps.views.index"))

        assert response.status_code == 200
        assert response.headers["Content-Type"] == "application/xml"

    def test_index_links_to_all_sections(self, client):
        root = parse_xml(client.get(reverse("django.contrib.sitemaps.views.index")))

        assert locs(root) == [
            f"http://{SITE_DOMAIN}/sitemap-{section}.xml"
            for section in SITEMAP_SECTIONS
        ]

    def test_index_is_in_sitemap_namespace(self, client):
        root = parse_xml(client.get(reverse("django.contrib.sitemaps.views.index")))

        assert root.tag == f"{{{SITEMAP_NS}}}sitemapindex"

    @pytest.mark.parametrize("section", SITEMAP_SECTIONS)
    def test_section_is_valid_xml(self, client, section):
        response = client.get(f"/sitemap-{section}.xml")

        assert response.status_code == 200
        root = parse_xml(response)
        assert root.tag == f"{{{SITEMAP_NS}}}urlset"


class TestSitemapPages:
    def test_static_pages_are_listed(self, client):
        root = parse_xml(client.get("/sitemap-pages.xml"))

        assert locs(root) == [
            f"http://{SITE_DOMAIN}/",
            f"http://{SITE_DOMAIN}/teams/",
            f"http://{SITE_DOMAIN}/events/",
            f"http://{SITE_DOMAIN}/years/",
        ]

    def test_static_pages_have_no_lastmod(self, client):
        root = parse_xml(client.get("/sitemap-pages.xml"))

        assert root.findall(f"{{{SITEMAP_NS}}}url/{{{SITEMAP_NS}}}lastmod") == []


class TestSitemapObjects:
    def test_published_team_is_listed_with_lastmod(self, client, team):
        root = parse_xml(client.get("/sitemap-teams.xml"))
        url = root.find(f"{{{SITEMAP_NS}}}url")

        assert locs(root) == [f"http://{SITE_DOMAIN}{team.get_absolute_url()}"]
        assert url.find(f"{{{SITEMAP_NS}}}lastmod") is not None

    @pytest.mark.parametrize("status", [Status.DRAFT, Status.HIDDEN, Status.REJECTED])
    def test_unpublished_team_is_not_listed(self, client, baseball, user, status):
        make_team(baseball, user, status=status)

        assert locs(parse_xml(client.get("/sitemap-teams.xml"))) == []

    def test_deleted_team_is_not_listed(self, client, team):
        team.soft_delete(reason="тест")

        assert locs(parse_xml(client.get("/sitemap-teams.xml"))) == []

    def test_merged_team_is_not_listed(self, client, baseball, user, team):
        other = make_team(baseball, user, name="Динамо")
        team.merge_into(other, reason="тест")

        assert locs(parse_xml(client.get("/sitemap-teams.xml"))) == [
            f"http://{SITE_DOMAIN}{other.get_absolute_url()}"
        ]

    def test_published_event_is_listed(self, client, event):
        assert locs(parse_xml(client.get("/sitemap-events.xml"))) == [
            f"http://{SITE_DOMAIN}{event.get_absolute_url()}"
        ]

    def test_draft_event_is_not_listed(self, client, baseball, user):
        EventFactory(sport=baseball, status=Status.DRAFT, created_by=user)

        assert locs(parse_xml(client.get("/sitemap-events.xml"))) == []

    def test_published_person_is_listed(self, client, person):
        assert locs(parse_xml(client.get("/sitemap-people.xml"))) == [
            f"http://{SITE_DOMAIN}{person.get_absolute_url()}"
        ]

    def test_hidden_name_person_is_still_listed(self, client, user):
        """Страница открыта, фамилии на ней нет — адрес в карте уместен."""
        person = make_person(user, name_visibility=NameVisibility.HIDDEN)

        assert locs(parse_xml(client.get("/sitemap-people.xml"))) == [
            f"http://{SITE_DOMAIN}{person.get_absolute_url()}"
        ]

    def test_public_photo_is_listed(self, client, photo):
        assert locs(parse_xml(client.get("/sitemap-photos.xml"))) == [
            f"http://{SITE_DOMAIN}{photo.get_absolute_url()}"
        ]

    def test_photo_without_rights_is_not_listed(self, client, baseball, user):
        PhotoFactory(sport=baseball, uploaded_by=user, rights_confirmed=False)

        assert locs(parse_xml(client.get("/sitemap-photos.xml"))) == []

    def test_draft_photo_is_not_listed(self, client, baseball, user):
        PhotoFactory(sport=baseball, uploaded_by=user, status=Status.DRAFT)

        assert locs(parse_xml(client.get("/sitemap-photos.xml"))) == []


class TestRobotsTxt:
    def test_returns_plain_text(self, client):
        response = client.get("/robots.txt")

        assert response.status_code == 200
        assert response.headers["Content-Type"] == "text/plain"

    def test_closes_service_sections(self, client):
        text = client.get("/robots.txt").content.decode()

        assert "User-agent: *" in text
        for path in ["/admin/", "/accounts/", "/search/", "/healthz/"]:
            assert f"Disallow: {path}" in text

    def test_points_to_absolute_sitemap(self, client):
        text = client.get("/robots.txt").content.decode()

        assert f"Sitemap: http://{SITE_DOMAIN}/sitemap.xml" in text

    def test_site_domain_is_not_example_com(self):
        """Домен задаёт data-миграция: иначе карта вела бы на example.com."""
        assert Site.objects.get(pk=1).domain == SITE_DOMAIN


class TestWebsiteSchema:
    def test_home_has_website_block(self, client):
        block = page_schema(client, reverse("core:home"), "WebSite")

        assert block["@context"] == "https://schema.org"
        assert block["name"] == SITE_NAME
        assert block["url"] == f"http://{REQUEST_DOMAIN}/"

    def test_home_has_search_action(self, client):
        html = client.get(reverse("core:home")).content.decode()
        block = jsonld_of_type(html, "WebSite")

        assert block["potentialAction"]["@type"] == "SearchAction"
        assert block["potentialAction"]["target"]["urlTemplate"] == (
            f"http://{REQUEST_DOMAIN}/search/?q={{search_term_string}}"
        )

    @pytest.mark.parametrize(
        "name", ["core:year_list", "catalog:team_list", "catalog:event_list"]
    )
    def test_list_pages_have_no_schema(self, client, name):
        assert jsonld_blocks(client.get(reverse(name)).content.decode()) == []

    def test_year_page_has_no_schema(self, client):
        PhotoFactory(year=1994)

        html = client.get(reverse("core:year_detail", args=[1994])).content.decode()

        assert jsonld_blocks(html) == []


class TestTeamSchema:
    def test_team_block_has_facts(self, client, team):
        block = jsonld_of_type(
            client.get(team.get_absolute_url()).content.decode(), "SportsTeam"
        )

        assert block["name"] == "Соколы"
        assert block["url"] == f"http://{REQUEST_DOMAIN}{team.get_absolute_url()}"
        assert block["sport"] == "Бейсбол"
        assert block["address"]["addressLocality"] == "Москва"

    def test_team_block_has_founded_year(self, client, baseball, user):
        team = make_team(baseball, user, founded_year=1980)

        block = jsonld_of_type(
            client.get(team.get_absolute_url()).content.decode(), "SportsTeam"
        )

        assert block["foundingDate"] == "1980"

    def test_team_without_city_has_no_address(self, client, baseball, user):
        team = make_team(baseball, user, city="")

        block = jsonld_of_type(
            client.get(team.get_absolute_url()).content.decode(), "SportsTeam"
        )

        assert "address" not in block

    def test_draft_team_has_no_schema(self, client, baseball, user):
        team = make_team(baseball, user, status=Status.DRAFT)
        client.force_login(user)

        assert jsonld_blocks(client.get(team.get_absolute_url()).content.decode()) == []


class TestEventSchema:
    def test_event_block_has_dates(self, client, baseball, user):
        event = EventFactory(
            sport=baseball,
            name="Кубок",
            status=Status.PUBLISHED,
            created_by=user,
            date_from="1994-05-01",
            date_to="1994-05-03",
        )

        block = jsonld_of_type(
            client.get(event.get_absolute_url()).content.decode(), "SportsEvent"
        )

        assert block["name"] == "Кубок"
        assert block["startDate"] == "1994-05-01"
        assert block["endDate"] == "1994-05-03"

    def test_event_without_dates_has_no_dates(self, client, baseball, user):
        event = EventFactory(
            sport=baseball,
            status=Status.PUBLISHED,
            created_by=user,
            date_from=None,
            year=1994,
        )

        block = jsonld_of_type(
            client.get(event.get_absolute_url()).content.decode(), "SportsEvent"
        )

        assert "startDate" not in block

    def test_event_block_has_location(self, client, baseball, user):
        event = EventFactory(
            sport=baseball,
            status=Status.PUBLISHED,
            created_by=user,
            city="Москва",
            country="Россия",
        )

        block = jsonld_of_type(
            client.get(event.get_absolute_url()).content.decode(), "SportsEvent"
        )

        address = block["location"]["address"]
        assert address["addressLocality"] == "Москва"
        assert address["addressCountry"] == "Россия"

    def test_draft_event_has_no_schema(self, client, baseball, user):
        event = EventFactory(sport=baseball, status=Status.DRAFT, created_by=user)
        client.force_login(user)

        assert page_blocks(client, event.get_absolute_url()) == []


class TestPersonSchema:
    def test_full_name_person_is_listed(self, client, person):
        block = jsonld_of_type(
            client.get(person.get_absolute_url()).content.decode(), "Person"
        )

        assert block["name"] == "Иван Смирнов"
        assert block["url"] == f"http://{REQUEST_DOMAIN}{person.get_absolute_url()}"

    def test_first_name_only_hides_last_name(self, client, user):
        person = make_person(user, name_visibility=NameVisibility.FIRST_NAME_ONLY)

        html = client.get(person.get_absolute_url()).content.decode()

        assert jsonld_of_type(html, "Person")["name"] == "Иван"
        assert "Смирнов" not in html

    def test_hidden_person_has_no_block(self, client, user):
        person = make_person(user, name_visibility=NameVisibility.HIDDEN)

        assert (
            jsonld_of_type(
                client.get(person.get_absolute_url()).content.decode(), "Person"
            )
            is None
        )

    def test_draft_person_has_no_schema(self, client, user):
        person = make_person(user, status=Status.DRAFT)
        client.force_login(user)

        assert page_blocks(client, person.get_absolute_url()) == []


class TestPhotoSchema:
    def test_photo_block_has_image(self, client, photo):
        block = jsonld_of_type(
            client.get(photo.get_absolute_url()).content.decode(), "ImageObject"
        )

        assert block["contentUrl"].startswith(f"http://{REQUEST_DOMAIN}/media/")
        assert block["url"] == f"http://{REQUEST_DOMAIN}{photo.get_absolute_url()}"
        assert block["name"] == photo.title

    def test_photo_with_size_has_dimensions(self, client, baseball, user):
        """Размеры заполняет обработка (этап 3) — берём то, что есть."""
        photo = PhotoFactory(sport=baseball, uploaded_by=user, width=1280, height=960)

        block = jsonld_of_type(
            client.get(photo.get_absolute_url()).content.decode(), "ImageObject"
        )

        assert block["width"] == 1280
        assert block["height"] == 960

    def test_photo_without_size_has_no_dimensions(self, client, photo):
        assert photo.width is None

        block = jsonld_of_type(
            client.get(photo.get_absolute_url()).content.decode(), "ImageObject"
        )

        assert "width" not in block
        assert "height" not in block

    def test_photo_without_title_gets_generic_name(self, client, baseball, user):
        photo = PhotoFactory(sport=baseball, uploaded_by=user, title="")

        block = jsonld_of_type(
            client.get(photo.get_absolute_url()).content.decode(), "ImageObject"
        )

        assert block["name"] == "Фотография"

    def test_photo_block_has_caption_as_description(self, client, baseball, user):
        photo = PhotoFactory(sport=baseball, uploaded_by=user, caption="Финал сезона")

        block = jsonld_of_type(
            client.get(photo.get_absolute_url()).content.decode(), "ImageObject"
        )

        assert block["description"] == "Финал сезона"

    def test_draft_photo_has_no_schema(self, client, user):
        photo = PhotoFactory(status=Status.DRAFT, uploaded_by=user)
        client.force_login(user)

        assert page_blocks(client, photo.get_absolute_url()) == []


class TestServicePagesHaveNoSchema:
    @pytest.mark.parametrize(
        "name",
        ["search:index", "account_login", "account_signup", "catalog:team_list"],
    )
    def test_page_has_no_blocks(self, client, name):
        assert jsonld_blocks(client.get(reverse(name)).content.decode()) == []

    def test_404_has_no_blocks(self, client, settings):
        settings.DEBUG = False
        settings.ALLOWED_HOSTS = ["testserver"]

        assert jsonld_blocks(client.get("/no-such-page/").content.decode()) == []


class TestEscaping:
    def test_script_tag_in_name_does_not_break_page(self, client, baseball, user):
        team = make_team(baseball, user, name="</script><script>alert(1)</script>")

        html = client.get(team.get_absolute_url()).content.decode()

        assert "<script>alert(1)</script>" not in html
        assert jsonld_of_type(html, "SportsTeam")["name"] == (
            "</script><script>alert(1)</script>"
        )

    def test_quotes_and_cyrillic_survive(self, client, baseball, user):
        team = make_team(baseball, user, name='Клуб "Соколы" & Ко')

        html = client.get(team.get_absolute_url()).content.decode()

        assert jsonld_of_type(html, "SportsTeam")["name"] == 'Клуб "Соколы" & Ко'

    def test_line_separators_are_escaped(self, client, baseball, user):
        """U+2028 валиден в JS, но ломает разбор JSON — тег его экранирует."""
        team = make_team(baseball, user, name="Соколы\u2028Ко")

        html = client.get(team.get_absolute_url()).content.decode()
        raw = re.search(
            r'<script type="application/ld\+json">(.*?)</script>', html, re.S
        ).group(1)

        assert "\u2028" not in raw
        assert "\\u2028" in raw
        assert jsonld_of_type(html, "SportsTeam")["name"] == "Соколы\u2028Ко"
