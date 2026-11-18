"""Тесты режимов стейджа: закрытая регистрация и noindex.

Стейдж стоит на зарубежном сервере, поэтому два переключателя проверяются
отдельно от остального интерфейса: закрытая регистрация не должна пускать
реальных людей в базу, а noindex-режим должен закрывать все ответы, включая
служебные, а не только страницы с шаблонами.
"""

import pytest
from django.core import mail
from django.urls import reverse

from apps.accounts.models import User

pytestmark = pytest.mark.django_db

SIGNUP_URL = "/accounts/signup/"
LOGIN_URL = "/accounts/login/"

SIGNUP_DATA = {
    "email": "stage@example.com",
    "display_name": "Стейдж",
    "password1": "very-secret-pass-42",
    "password2": "very-secret-pass-42",
    "terms_accepted": "on",
    "privacy_accepted": "on",
    "consent_pd": "on",
}


class TestClosedSignup:
    def test_signup_page_says_closed(self, client, settings):
        settings.SPORTMEMO_SIGNUP_OPEN = False

        response = client.get(SIGNUP_URL)

        assert response.status_code == 200
        assert "Регистрация закрыта" in response.content.decode()

    def test_closed_signup_page_uses_site_layout(self, client, settings):
        settings.SPORTMEMO_SIGNUP_OPEN = False

        html = client.get(SIGNUP_URL).content.decode()

        assert 'class="site-header"' in html

    def test_post_does_not_create_user(self, client, settings):
        settings.SPORTMEMO_SIGNUP_OPEN = False

        client.post(SIGNUP_URL, SIGNUP_DATA)

        assert not User.objects.filter(email=SIGNUP_DATA["email"]).exists()

    def test_post_does_not_send_email(self, client, settings):
        settings.SPORTMEMO_SIGNUP_OPEN = False

        client.post(SIGNUP_URL, SIGNUP_DATA)

        assert mail.outbox == []

    def test_login_page_hides_signup_link(self, client, settings):
        settings.SPORTMEMO_SIGNUP_OPEN = False

        html = client.get(LOGIN_URL).content.decode()

        assert reverse("account_signup") not in html

    def test_login_page_keeps_password_reset(self, client, settings):
        settings.SPORTMEMO_SIGNUP_OPEN = False

        html = client.get(LOGIN_URL).content.decode()

        assert reverse("account_reset_password") in html
        assert "Забыли пароль?" in html

    def test_open_signup_keeps_link(self, client, settings):
        settings.SPORTMEMO_SIGNUP_OPEN = True

        html = client.get(LOGIN_URL).content.decode()

        assert reverse("account_signup") in html


class TestNoindexAll:
    def test_home_carries_noindex_header(self, client, settings):
        settings.SPORTMEMO_NOINDEX_ALL = True

        response = client.get(reverse("core:home"))

        assert response["X-Robots-Tag"] == "noindex, nofollow"

    def test_healthz_carries_noindex_header(self, client, settings):
        settings.SPORTMEMO_NOINDEX_ALL = True

        response = client.get(reverse("healthz"))

        assert response["X-Robots-Tag"] == "noindex, nofollow"

    def test_robots_disallows_everything(self, client, settings):
        settings.SPORTMEMO_NOINDEX_ALL = True

        text = client.get("/robots.txt").content.decode()

        disallow = [line for line in text.splitlines() if line.startswith("Disallow")]
        assert disallow == ["Disallow: /"]

    def test_robots_has_no_sitemap(self, client, settings):
        settings.SPORTMEMO_NOINDEX_ALL = True

        text = client.get("/robots.txt").content.decode()

        assert "Sitemap:" not in text

    def test_no_header_by_default(self, client, settings):
        settings.SPORTMEMO_NOINDEX_ALL = False

        response = client.get(reverse("core:home"))

        assert "X-Robots-Tag" not in response

    def test_robots_lists_sections_by_default(self, client, settings):
        settings.SPORTMEMO_NOINDEX_ALL = False

        text = client.get("/robots.txt").content.decode()

        assert "Disallow: /admin/" in text
        assert "Sitemap:" in text
