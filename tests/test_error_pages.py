"""Тесты страниц ошибок 404 и 500.

Django берёт `templates/404.html` и `templates/500.html` только при
`DEBUG=False`, поэтому тесты включают его вручную. Настройки меняются
фикстурой `settings`: pytest-django не умеет применять
`override_settings` к классу, не наследующему `SimpleTestCase`.
"""

import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def no_debug(settings):
    settings.DEBUG = False
    settings.ALLOWED_HOSTS = ["testserver"]
    return settings


class TestNotFound:
    def test_unknown_url_returns_404_page(self, client, no_debug):
        response = client.get("/no-such-page/")

        assert response.status_code == 404
        html = response.content.decode()
        assert "404" in html
        assert "Страница не найдена" in html

    def test_error_page_has_link_home(self, client, no_debug):
        html = client.get("/no-such-page/").content.decode()

        assert 'href="/"' in html

    def test_error_page_has_no_search_form(self, client, no_debug):
        html = client.get("/no-such-page/").content.decode()

        assert "home-hero__search" not in html
        assert 'id="navbar-search"' not in html


class TestServerError:
    def test_failing_view_renders_500_page(self, client, settings):
        settings.DEBUG = False
        settings.ALLOWED_HOSTS = ["testserver"]
        settings.ROOT_URLCONF = "tests.urls_error"
        client.raise_request_exception = False

        response = client.get("/boom/")

        assert response.status_code == 500
        html = response.content.decode()
        assert "500" in html
        assert "Ошибка сервера" in html
        assert 'href="/"' in html
