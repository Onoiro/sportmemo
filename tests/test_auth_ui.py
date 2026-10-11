"""Тесты страниц входа, регистрации и восстановления пароля.

Проверяем подхват наших шаблонов, доступ и полный цикл регистрации:
разметку не тестируем, но следим за ключевыми классами — по ним видно,
что страница отрисовалась нашим каркасом, а не штатным шаблоном allauth.
"""

import re

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.urls import reverse

from apps.accounts.models import User

pytestmark = pytest.mark.django_db

SIGNUP_URL = "/accounts/signup/"
LOGIN_URL = "/accounts/login/"
RESET_URL = "/accounts/password/reset/"

SIGNUP_DATA = {
    "email": "new@example.com",
    "display_name": "Новый",
    "password1": "very-secret-pass-42",
    "password2": "very-secret-pass-42",
    "terms_accepted": "on",
    "privacy_accepted": "on",
    "consent_pd": "on",
}


def confirm_email(client, address="new@example.com"):
    """Перейти по ссылке из письма и подтвердить адрес."""
    url = re.search(r"http\S+", mail.outbox[-1].body).group(0)
    return client.post(url.replace("http://testserver", ""), follow=True)


class TestLayout:
    """Страницы аккаунта живут внутри общего каркаса сайта."""

    PAGES = [
        "/accounts/login/",
        "/accounts/signup/",
        "/accounts/password/reset/",
        "/accounts/password/reset/done/",
        "/accounts/confirm-email/",
    ]

    @pytest.mark.parametrize("url", PAGES)
    def test_uses_site_layout(self, client, url):
        html = client.get(url).content.decode()

        assert 'class="site-header"' in html, url
        assert 'class="site-footer"' in html, url
        assert "auth-shell" in html, url

    @pytest.mark.parametrize("url", PAGES)
    def test_no_template_tags_leak(self, client, url):
        html = client.get(url).content.decode()

        assert "{#" not in html, url
        assert "{%" not in html, url

    def test_login_form_is_bootstrap(self, client):
        html = client.get(LOGIN_URL).content.decode()

        assert "form-control" in html
        assert "btn btn-primary" in html

    def test_signup_checkboxes_are_bootstrap(self, client):
        html = client.get(SIGNUP_URL).content.decode()

        assert html.count("form-check-input") == 3


class TestAnonymousAccess:
    @pytest.mark.parametrize(
        "url",
        [
            "/accounts/login/",
            "/accounts/signup/",
            "/accounts/password/reset/",
            "/accounts/password/reset/done/",
            "/accounts/confirm-email/",
        ],
    )
    def test_pages_open_for_anonymous(self, client, url):
        assert client.get(url).status_code == 200

    def test_logout_redirects_to_home(self, client):
        response = client.get("/accounts/logout/")

        assert response.status_code == 302
        assert response["Location"] == "/"


class TestSignupFlow:
    def test_signup_sends_email_and_creates_user(self, client):
        response = client.post(SIGNUP_URL, SIGNUP_DATA)

        assert response.status_code == 302
        assert User.objects.filter(email="new@example.com").exists()
        assert len(mail.outbox) == 1

    def test_signup_lands_on_check_your_email(self, client):
        response = client.post(SIGNUP_URL, SIGNUP_DATA, follow=True)

        html = response.content.decode()
        assert response.status_code == 200
        assert "Проверьте почту" in html
        assert 'class="site-header"' in html

    def test_inactive_account_page_uses_our_template(self, client):
        html = client.get("/accounts/inactive/").content.decode()

        assert "Аккаунт отключён" in html
        assert 'class="site-header"' in html

    def test_email_subject_has_site_name(self, client):
        client.post(SIGNUP_URL, SIGNUP_DATA)

        assert mail.outbox[0].subject == (
            "[SportMemo] Подтвердите адрес электронной почты"
        )

    def test_email_link_is_absolute(self, client):
        client.post(SIGNUP_URL, SIGNUP_DATA)

        url = re.search(r"http\S+", mail.outbox[0].body).group(0)
        assert url.startswith("http://testserver/accounts/confirm-email/")

    def test_consents_are_saved(self, client):
        client.post(SIGNUP_URL, SIGNUP_DATA)

        user = User.objects.get(email="new@example.com")
        assert user.has_consents
        assert user.consent_pd_version

    def test_confirmation_logs_user_in(self, client):
        client.post(SIGNUP_URL, SIGNUP_DATA)

        response = confirm_email(client)

        assert response.status_code == 200
        assert client.session["_auth_user_id"]
        address = EmailAddress.objects.get(email="new@example.com")
        assert address.verified

    def test_confirmation_page_renders_for_valid_key(self, client):
        client.post(SIGNUP_URL, SIGNUP_DATA)
        url = re.search(r"http\S+", mail.outbox[0].body).group(0)

        response = client.get(url.replace("http://testserver", ""))

        html = response.content.decode()
        assert response.status_code == 200
        assert "Подтверждение адреса" in html
        assert "Подтвердить" in html

    def test_invalid_key_explains_what_to_do(self, client):
        response = client.get("/accounts/confirm-email/broken-key/")

        html = response.content.decode()
        assert response.status_code == 200
        assert "устарела или не подходит" in html


class TestLogin:
    def test_unverified_user_is_sent_to_verification_page(self, client, user):
        response = client.post(
            LOGIN_URL, {"login": user.email, "password": "test-pass-12345"}
        )

        assert response.status_code == 302
        assert response["Location"] == "/accounts/confirm-email/"

    def test_verified_user_lands_on_home(self, client, user):
        EmailAddress.objects.create(
            user=user, email=user.email, primary=True, verified=True
        )

        response = client.post(
            LOGIN_URL, {"login": user.email, "password": "test-pass-12345"}
        )

        assert response.status_code == 302
        assert response["Location"] == "/"

    def test_wrong_password_shows_error(self, client, user):
        response = client.post(
            LOGIN_URL, {"login": user.email, "password": "wrong-pass"}
        )

        html = response.content.decode()
        assert response.status_code == 200
        assert "alert-danger" in html
        assert "неверны" in html

    def test_login_page_links_to_reset_and_signup(self, client):
        html = client.get(LOGIN_URL).content.decode()

        assert reverse("account_reset_password") in html
        assert reverse("account_signup") in html


class TestPasswordReset:
    def test_reset_sends_email(self, client, user):
        response = client.post(RESET_URL, {"email": user.email})

        assert response.status_code == 302
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == "[SportMemo] Сброс пароля"

    def test_full_reset_cycle(self, client, user):
        client.post(RESET_URL, {"email": user.email})
        url = re.search(r"http\S+", mail.outbox[0].body).group(0)

        page = client.get(url.replace("http://testserver", ""))
        assert page.status_code == 302  # allauth уводит на форму нового пароля

        response = client.post(
            page["Location"],
            {"password1": "brand-new-pass-77", "password2": "brand-new-pass-77"},
        )
        assert response.status_code == 302

        user.refresh_from_db()
        assert user.check_password("brand-new-pass-77")

    def test_login_with_new_password(self, client, user):
        client.post(RESET_URL, {"email": user.email})
        url = re.search(r"http\S+", mail.outbox[0].body).group(0)
        page = client.get(url.replace("http://testserver", ""))
        client.post(
            page["Location"],
            {"password1": "brand-new-pass-77", "password2": "brand-new-pass-77"},
        )
        EmailAddress.objects.update_or_create(
            user=user,
            email=user.email,
            defaults={"primary": True, "verified": True},
        )

        response = client.post(
            LOGIN_URL, {"login": user.email, "password": "brand-new-pass-77"}
        )

        assert response.status_code == 302
        assert response["Location"] == "/"

    def test_broken_key_shows_error_branch(self, client):
        response = client.get("/accounts/password/reset/key/broken-key/")

        html = response.content.decode()
        assert response.status_code == 200
        assert "Ссылка не подходит" in html
        assert "новую ссылку" in html

    def test_reset_page_explains_what_happens(self, client):
        html = client.get(RESET_URL).content.decode()

        assert "пришлём ссылку для сброса" in html


class TestPasswordChange:
    def test_page_requires_login(self, client):
        response = client.get("/accounts/password/change/")

        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_logged_in_user_changes_password(self, client, user):
        client.force_login(user)

        response = client.post(
            "/accounts/password/change/",
            {
                "oldpassword": "test-pass-12345",
                "password1": "brand-new-pass-77",
                "password2": "brand-new-pass-77",
            },
        )

        assert response.status_code == 302
        user.refresh_from_db()
        assert user.check_password("brand-new-pass-77")


class TestLocalization:
    def test_english_pages_have_no_russian_text(self, client):
        html = client.get(LOGIN_URL, headers={"accept-language": "en"}).content.decode()

        assert "Sign in" in html
        assert not any("а" <= char <= "я" for char in html)

    def test_russian_email_subject(self, client):
        client.post(SIGNUP_URL, SIGNUP_DATA)

        assert "Подтвердите" in mail.outbox[0].subject
