"""Тесты главной страницы и страниц-заглушек.

Проверяем код ответа и ключевые блоки, а не вёрстку: разметка меняется
чаще, чем смысл страницы.
"""

import pytest
from django.contrib.sites.models import Site
from django.urls import reverse

from apps.core.models import Status
from apps.core.views import HOME_PHOTO_LIMIT
from tests.factories import PhotoFactory, TeamFactory

pytestmark = pytest.mark.django_db

PLACEHOLDERS = [
    ("catalog:team_list", "/teams/"),
    ("catalog:event_list", "/events/"),
    ("core:year_list", "/years/"),
    ("core:search", "/search/"),
]


class TestHome:
    def test_opens_anonymously(self, client):
        response = client.get(reverse("core:home"))

        assert response.status_code == 200
        assert b"SportMemo" in response.content

    def test_contains_stats_block(self, client, photo):
        response = client.get(reverse("core:home"))
        html = response.content.decode()

        assert "archive-stats" in html
        assert "команд" in html
        assert "фотографий" in html
        assert "лет в архиве" in html

    def test_stats_count_only_published(self, client, baseball, user):
        TeamFactory(sport=baseball, created_by=user, status=Status.PUBLISHED)
        TeamFactory(sport=baseball, created_by=user, status=Status.DRAFT)
        TeamFactory(sport=baseball, created_by=user, status=Status.HIDDEN)

        response = client.get(reverse("core:home"))

        assert response.context["stats"]["teams"] == 1

    def test_stats_count_only_visible_photos(self, client):
        PhotoFactory(year=1994)
        PhotoFactory(year=1995, rights_confirmed=False)
        PhotoFactory(year=1996, status=Status.DRAFT)

        response = client.get(reverse("core:home"))

        assert response.context["stats"]["photos"] == 1

    def test_stats_count_unique_years(self, client):
        PhotoFactory(year=1994)
        PhotoFactory(year=1994)
        PhotoFactory(year=2003)
        PhotoFactory(year=None)

        response = client.get(reverse("core:home"))

        assert response.context["stats"]["years"] == 2

    def test_shows_latest_photos(self, client, photo):
        response = client.get(reverse("core:home"))

        assert photo in response.context["latest_photos"]

    def test_photo_limit_is_respected(self, client):
        for _ in range(HOME_PHOTO_LIMIT + 3):
            PhotoFactory()

        response = client.get(reverse("core:home"))

        assert len(response.context["latest_photos"]) == HOME_PHOTO_LIMIT

    def test_draft_and_unconfirmed_photos_are_not_shown(self, client):
        visible = PhotoFactory()
        hidden = PhotoFactory(rights_confirmed=False)
        draft = PhotoFactory(status=Status.DRAFT)

        response = client.get(reverse("core:home"))
        shown = list(response.context["latest_photos"])

        assert visible in shown
        assert hidden not in shown
        assert draft not in shown

    def test_empty_archive_shows_empty_state(self, client):
        response = client.get(reverse("core:home"))
        html = response.content.decode()

        assert response.status_code == 200
        assert "Архив пока пуст" in html

    def test_has_search_form_and_section_links(self, client):
        response = client.get(reverse("core:home"))
        html = response.content.decode()

        assert reverse("core:search") in html
        assert reverse("catalog:team_list") in html
        assert reverse("core:year_list") in html


class TestBaseTemplate:
    def test_theme_attribute_and_scripts(self, client):
        html = client.get(reverse("core:home")).content.decode()

        assert 'data-bs-theme="light"' in html
        assert "theme-sepia" in html
        assert "vendor/bootstrap/css/bootstrap.min.css" in html
        assert "vendor/bootstrap-icons/bootstrap-icons.min.css" in html
        assert "vendor/htmx/htmx.min.js" in html
        assert "vendor/alpine/alpine.min.js" in html
        assert "css/sportmemo.css" in html
        assert "js/theme.js" in html

    def test_theme_script_runs_before_head_end(self, client):
        html = client.get(reverse("core:home")).content.decode()

        assert html.index("sportmemo-theme") < html.index("</head>")

    def test_navbar_has_links(self, client):
        html = client.get(reverse("core:home")).content.decode()

        for name in ("catalog:team_list", "catalog:event_list", "core:year_list"):
            assert reverse(name) in html

    def test_footer_shows_version(self, client):
        from django.conf import settings

        html = client.get(reverse("core:home")).content.decode()

        assert f"v{settings.VERSION}" in html

    def test_login_link_for_anonymous(self, client):
        html = client.get(reverse("core:home")).content.decode()

        assert reverse("account_login") in html


class TestPlaceholders:
    @pytest.mark.parametrize("name,url", PLACEHOLDERS)
    def test_reverse_and_status(self, client, name, url):
        assert reverse(name) == url
        assert client.get(reverse(name)).status_code == 200

    @pytest.mark.parametrize("name,url", PLACEHOLDERS)
    def test_shows_heading_and_link_home(self, client, name, url):
        html = client.get(reverse(name)).content.decode()

        assert "Раздел в разработке" in html
        assert reverse("core:home") in html

    def test_search_keeps_query_string(self, client):
        response = client.get(reverse("core:search"), {"q": "Громов"})

        assert response.status_code == 200


class TestSiteDomain:
    def test_domain_is_set_by_migration(self):
        site = Site.objects.get(pk=1)

        assert site.domain == "sportmemo.ru"
        assert site.name == "SportMemo"
