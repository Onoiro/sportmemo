"""Тесты метатегов и Open Graph (подэтап 2.10).

Проверяем смысл, а не порядок строк: абсолютный `canonical`, карточку
мессенджера (`og:*`), картинку объекта и правило приватности имени.
Отдельная забота — `noindex` на служебных страницах: если он пропадёт,
в поиск поедут черновики и формы.
"""

import re

import pytest
from django.urls import reverse

from apps.catalog.models import NameVisibility, Person, Team
from apps.core.models import Status
from apps.core.seo import DESCRIPTION_LIMIT
from tests.factories import EventFactory, PhotoFactory
from tests.utils import make_image_file

pytestmark = pytest.mark.django_db

LOGIN_URL = reverse("account_login")


def make_team(baseball, user, name="Соколы", **kwargs):
    """Опубликованная команда; тесты меняют статус через kwargs."""
    kwargs.setdefault("status", Status.PUBLISHED)
    kwargs.setdefault("city", "Москва")
    return Team.objects.create(sport=baseball, name=name, created_by=user, **kwargs)


def make_person(user, first_name="Иван", last_name="Смирнов", **kwargs):
    kwargs.setdefault("status", Status.PUBLISHED)
    return Person.objects.create(
        first_name=first_name, last_name=last_name, created_by=user, **kwargs
    )


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


def meta_content(html, attribute, value):
    """Значение `<meta property=... content=...>` или `None`.

    Регулярка, а не парсер: разметка страницы не является предметом теста,
    а лишняя зависимость на BeautifulSoup в проекте не нужна.
    """
    pattern = rf'<meta {attribute}="{re.escape(value)}" content="([^"]*)"'
    match = re.search(pattern, html)
    return match.group(1) if match else None


def canonical_href(html):
    match = re.search(r'<link rel="canonical" href="([^"]*)"', html)
    return match.group(1) if match else None


def robots_content(html):
    return meta_content(html, "name", "robots")


def title_content(html):
    match = re.search(r"<title>(.*?)</title>", html, re.S)
    return match.group(1).strip() if match else None


class TestCanonical:
    def test_home_has_absolute_canonical(self, client):
        html = client.get(reverse("core:home")).content.decode()

        assert canonical_href(html) == "http://testserver/"

    def test_filters_are_kept_in_canonical(self, client, team):
        html = client.get(
            reverse("catalog:team_detail", args=[team.slug]), {"year": 1994}
        ).content.decode()

        assert canonical_href(html) == (
            f"http://testserver{team.get_absolute_url()}?year=1994"
        )

    def test_page_number_is_dropped(self, client):
        html = client.get(reverse("catalog:team_list"), {"page": 2}).content.decode()

        assert canonical_href(html) == "http://testserver/teams/"

    def test_second_page_is_noindex(self, client):
        for _ in range(30):
            PhotoFactory(year=1994)

        html = client.get(reverse("catalog:team_list"), {"page": 2}).content.decode()

        assert robots_content(html) == "noindex, follow"

    def test_first_page_is_indexable(self, client, team):
        html = client.get(reverse("catalog:team_list")).content.decode()

        assert robots_content(html) is None


class TestOpenGraph:
    def test_home_has_full_card(self, client):
        html = client.get(reverse("core:home")).content.decode()

        assert meta_content(html, "property", "og:site_name") == "SportMemo"
        assert meta_content(html, "property", "og:type") == "website"
        assert meta_content(html, "property", "og:title") == (
            "SportMemo — архив спортивных фотографий"
        )
        assert meta_content(html, "property", "og:url") == "http://testserver/"
        assert meta_content(html, "name", "twitter:card") == "summary_large_image"

    def test_home_uses_default_image(self, client):
        html = client.get(reverse("core:home")).content.decode()

        assert meta_content(html, "property", "og:image") == (
            "http://testserver/static/img/og-default.png"
        )
        assert meta_content(html, "property", "og:image:width") == "1200"
        assert meta_content(html, "property", "og:image:height") == "630"

    def test_og_locale_matches_language(self, client):
        html = client.get(reverse("core:home")).content.decode()
        english = client.get(
            reverse("core:home"), headers={"accept-language": "en"}
        ).content.decode()

        assert meta_content(html, "property", "og:locale") == "ru_RU"
        assert meta_content(english, "property", "og:locale") == "en_US"

    def test_photo_uses_preview_and_article_type(self, client, photo):
        html = client.get(photo.get_absolute_url()).content.decode()
        image = meta_content(html, "property", "og:image")

        assert meta_content(html, "property", "og:type") == "article"
        assert image is not None
        assert image.startswith("http://testserver/media/")
        assert image.endswith(".jpg")

    def test_team_uses_logo(self, client, baseball, user):
        team = make_team(
            baseball, user, logo=make_image_file(name="logo.jpg", size=(200, 200))
        )

        html = client.get(team.get_absolute_url()).content.decode()

        assert "/media/teams/logos/" in meta_content(html, "property", "og:image")

    def test_person_uses_hero_photo(self, client, user):
        photo = PhotoFactory(title="Портрет")
        person = make_person(user, avatar=photo)

        html = client.get(person.get_absolute_url()).content.decode()

        assert meta_content(html, "property", "og:image").startswith(
            "http://testserver/media/"
        )

    def test_team_without_logo_uses_default_image(self, client, team):
        html = client.get(team.get_absolute_url()).content.decode()

        assert meta_content(html, "property", "og:image") == (
            "http://testserver/static/img/og-default.png"
        )


class TestTitlesAndDescriptions:
    def test_team_title_has_city_and_site_name(self, client, team):
        html = client.get(team.get_absolute_url()).content.decode()

        assert title_content(html) == "Соколы (Москва) — SportMemo"

    def test_team_description_uses_own_text(self, client, baseball, user):
        team = make_team(baseball, user, description="Клуб из Москвы.")

        html = client.get(team.get_absolute_url()).content.decode()

        assert meta_content(html, "property", "og:description") == "Клуб из Москвы."

    def test_team_without_description_gets_generic_text(self, client, team):
        html = client.get(team.get_absolute_url()).content.decode()

        assert meta_content(html, "property", "og:description") == (
            "Команда «Соколы» в архиве SportMemo."
        )

    def test_event_title_has_year(self, client, event):
        html = client.get(event.get_absolute_url()).content.decode()

        assert title_content(html) == (f"{event.name} ({event.year}) — SportMemo")

    def test_year_page_mentions_year(self, client):
        PhotoFactory(year=1994)

        html = client.get(reverse("core:year_detail", args=[1994])).content.decode()

        assert title_content(html) == "1994 год — SportMemo"
        assert "1994" in meta_content(html, "property", "og:description")

    def test_description_is_clipped(self, client, baseball, user):
        team = make_team(baseball, user, description="а" * 400)

        html = client.get(team.get_absolute_url()).content.decode()
        description = meta_content(html, "property", "og:description")

        assert len(description) == DESCRIPTION_LIMIT
        assert description.endswith("…")

    def test_quotes_in_name_are_escaped(self, client, baseball, user):
        """Кавычка в названии не должна ломать разметку карточки."""
        team = make_team(baseball, user, name='Клуб "Соколы" & Ко')

        html = client.get(team.get_absolute_url()).content.decode()

        assert 'content="Клуб &quot;Соколы&quot; &amp; Ко (Москва) — SportMemo"' in html
        assert title_content(html) == (
            "Клуб &quot;Соколы&quot; &amp; Ко (Москва) — SportMemo"
        )


class TestNamePrivacy:
    def test_hidden_person_has_no_last_name_in_title(self, client, user):
        person = make_person(user, name_visibility=NameVisibility.HIDDEN)

        html = client.get(person.get_absolute_url()).content.decode()

        assert "Смирнов" not in html
        assert title_content(html) == "Имя не указано — SportMemo"

    def test_first_name_only_person_hides_last_name(self, client, user):
        person = make_person(user, name_visibility=NameVisibility.FIRST_NAME_ONLY)

        html = client.get(person.get_absolute_url()).content.decode()

        assert "Смирнов" not in html
        assert title_content(html) == "Иван — SportMemo"
        assert meta_content(html, "property", "og:description") == (
            "Публичный архив спортивных фотографий: команды, события, люди"
        )

    def test_full_name_person_gets_named_description(self, client, person):
        html = client.get(person.get_absolute_url()).content.decode()

        assert meta_content(html, "property", "og:description") == (
            "Человек Иван Смирнов в архиве SportMemo."
        )

    def test_hidden_person_photo_still_shows_in_card(self, client, user):
        """Картинка не раскрывает фамилию: фото и так публичное."""
        photo = PhotoFactory(title="Портрет")
        person = make_person(user, avatar=photo, name_visibility=NameVisibility.HIDDEN)

        html = client.get(person.get_absolute_url()).content.decode()

        assert meta_content(html, "property", "og:image").startswith(
            "http://testserver/media/"
        )


class TestNoindex:
    def test_draft_team_is_noindex_for_author(self, client, user, baseball):
        team = make_team(baseball, user, status=Status.DRAFT)
        client.force_login(user)

        html = client.get(team.get_absolute_url()).content.decode()

        assert robots_content(html) == "noindex, follow"

    def test_published_team_is_indexable(self, client, team):
        html = client.get(team.get_absolute_url()).content.decode()

        assert robots_content(html) is None

    def test_draft_photo_is_noindex(self, client, user):
        photo = PhotoFactory(status=Status.DRAFT, uploaded_by=user)
        client.force_login(user)

        html = client.get(photo.get_absolute_url()).content.decode()

        assert robots_content(html) == "noindex, follow"

    @pytest.mark.parametrize(
        "name",
        ["core:search", "catalog:my_teams", "catalog:team_create", "accounts:profile"],
    )
    def test_closed_pages_are_noindex_nofollow(self, client, user, name):
        client.force_login(user)

        html = client.get(reverse(name)).content.decode()

        assert robots_content(html) == "noindex, nofollow"

    def test_login_page_is_noindex(self, client):
        html = client.get(LOGIN_URL).content.decode()

        assert robots_content(html) == "noindex, follow"

    def test_login_page_has_no_open_graph(self, client):
        """Карточка в мессенджере служебной странице не нужна."""
        html = client.get(LOGIN_URL).content.decode()

        assert meta_content(html, "property", "og:title") is None

    def test_404_is_noindex(self, client, settings):
        settings.DEBUG = False
        settings.ALLOWED_HOSTS = ["testserver"]

        html = client.get("/no-such-page/").content.decode()

        assert robots_content(html) == "noindex, follow"

    def test_500_is_noindex(self, client, settings):
        settings.DEBUG = False
        settings.ALLOWED_HOSTS = ["testserver"]
        settings.ROOT_URLCONF = "tests.urls_error"
        client.raise_request_exception = False

        html = client.get("/boom/").content.decode()

        assert robots_content(html) == "noindex, follow"


class TestSeoDefaults:
    def test_processor_marks_unknown_pages_noindex(self, client):
        """Страница без своего `meta` получает безопасный дефолт."""
        html = client.get(reverse("account_signup")).content.decode()

        assert robots_content(html) == "noindex, follow"

    def test_view_meta_overrides_processor(self, client):
        """Контекст представления перекрывает вывод процессора."""
        html = client.get(reverse("core:home")).content.decode()

        assert robots_content(html) is None
        assert meta_content(html, "property", "og:title") is not None
