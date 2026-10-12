"""Тесты страницы профиля и меню пользователя в шапке.

Проверяем поведение, а не вёрстку: кто видит страницу, что на ней есть,
как сохраняется имя и какие ссылки попадают в меню. Отдельное внимание —
приватности: профиль показывает данные только текущего пользователя.
"""

import pytest
from allauth.account.models import EmailAddress
from django.urls import reverse

from apps.catalog.models import Person
from apps.core.models import Status

pytestmark = pytest.mark.django_db

PROFILE_URL = "/accounts/profile/"
LOGIN_URL = "/accounts/login/"


def make_person(user, **kwargs):
    """Опубликованный человек, созданный этим пользователем."""
    kwargs.setdefault("status", Status.PUBLISHED)
    return Person.objects.create(
        first_name="Иван", last_name="Смирнов", created_by=user, **kwargs
    )


def log_in(client, user):
    """Войти без подтверждения адреса: тестам важен факт входа."""
    client.force_login(user)


class TestProfileAccess:
    def test_anonymous_is_redirected_to_login(self, client):
        response = client.get(PROFILE_URL)

        assert response.status_code == 302
        assert response["Location"] == f"{LOGIN_URL}?next={PROFILE_URL}"

    def test_login_returns_to_profile(self, client, user):
        EmailAddress.objects.create(
            user=user, email=user.email, primary=True, verified=True
        )

        response = client.post(
            f"{LOGIN_URL}?next={PROFILE_URL}",
            {"login": user.email, "password": "test-pass-12345"},
        )

        assert response.status_code == 302
        assert response["Location"] == PROFILE_URL

    def test_logged_in_user_sees_profile(self, client, user):
        log_in(client, user)

        response = client.get(PROFILE_URL)

        assert response.status_code == 200
        assert "Профиль" in response.content.decode()


class TestProfileContent:
    def test_shows_email_and_join_date(self, client, user):
        log_in(client, user)

        html = client.get(PROFILE_URL).content.decode()

        assert user.email in html
        assert user.date_joined.strftime("%Y") in html

    def test_role_is_hidden_from_regular_user(self, client, user):
        log_in(client, user)

        html = client.get(PROFILE_URL).content.decode()

        assert "<dt>Роль</dt>" not in html

    @pytest.mark.parametrize("fixture_name", ["moderator", "admin_user"])
    def test_role_is_shown_to_moderator_and_admin(self, client, request, fixture_name):
        account = request.getfixturevalue(fixture_name)
        log_in(client, account)

        html = client.get(PROFILE_URL).content.decode()

        assert "<dt>Роль</dt>" in html
        assert account.get_role_display() in html

    def test_shows_consent_dates_and_version(self, client, user):
        log_in(client, user)

        html = client.get(PROFILE_URL).content.decode()

        assert user.terms_accepted_at.strftime("%Y") in html
        assert user.consent_pd_version in html

    def test_new_consent_version_is_shown(self, client, user):
        log_in(client, user)
        user.consent_pd_version = "2027-01-01"
        user.save(update_fields=["consent_pd_version"])

        html = client.get(PROFILE_URL).content.decode()

        assert "2027-01-01" in html

    def test_links_to_password_and_email_pages(self, client, user):
        log_in(client, user)

        html = client.get(PROFILE_URL).content.decode()

        assert reverse("account_change_password") in html
        assert reverse("account_email") in html


class TestProfilePersonLink:
    def test_without_link_shows_no_person_url(self, client, user):
        log_in(client, user)

        html = client.get(PROFILE_URL).content.decode()

        assert "/people/" not in html
        assert "не связан" in html

    def test_with_link_points_to_person_page(self, client, user):
        person = make_person(user)
        user.person = person
        user.save(update_fields=["person"])
        log_in(client, user)

        html = client.get(PROFILE_URL).content.decode()

        assert person.get_absolute_url() in html

    def test_no_claim_button_while_claim_is_not_implemented(self, client, user):
        log_in(client, user)

        html = client.get(PROFILE_URL).content.decode()

        assert "Это я" not in html


class TestProfileSave:
    def test_post_saves_display_name(self, client, user):
        log_in(client, user)

        response = client.post(PROFILE_URL, {"display_name": "Андрей"})

        assert response.status_code == 302
        assert response["Location"] == PROFILE_URL
        user.refresh_from_db()
        assert user.display_name == "Андрей"

    def test_post_shows_message(self, client, user):
        log_in(client, user)

        response = client.post(PROFILE_URL, {"display_name": "Андрей"}, follow=True)

        assert "Профиль обновлён" in response.content.decode()

    def test_reload_after_save_keeps_value(self, client, user):
        log_in(client, user)
        client.post(PROFILE_URL, {"display_name": "Андрей"})

        html = client.get(PROFILE_URL).content.decode()

        assert 'value="Андрей"' in html

    def test_empty_name_is_allowed(self, client, user):
        user.display_name = "Андрей"
        user.save(update_fields=["display_name"])
        log_in(client, user)

        response = client.post(PROFILE_URL, {"display_name": ""})

        assert response.status_code == 302
        user.refresh_from_db()
        assert user.display_name == ""

    def test_post_requires_login(self, client, user):
        response = client.post(PROFILE_URL, {"display_name": "Андрей"})

        assert response.status_code == 302
        user.refresh_from_db()
        assert user.display_name == ""

    def test_email_cannot_be_changed_here(self, client, user):
        log_in(client, user)

        client.post(PROFILE_URL, {"display_name": "Андрей", "email": "new@example.com"})

        user.refresh_from_db()
        assert user.email == "user@example.com"


class TestNavbarMenu:
    def test_anonymous_sees_login_link(self, client):
        html = client.get("/").content.decode()

        assert reverse("account_login") in html
        assert reverse("accounts:profile") not in html

    def test_logged_in_sees_profile_and_logout(self, client, user):
        log_in(client, user)

        html = client.get("/").content.decode()

        assert reverse("accounts:profile") in html
        assert reverse("account_logout") in html
        assert reverse("account_change_password") in html

    def test_menu_shows_display_name(self, client, user):
        user.display_name = "Андрей"
        user.save(update_fields=["display_name"])
        log_in(client, user)

        html = client.get("/").content.decode()

        assert "Андрей" in html

    def test_menu_shows_email_without_display_name(self, client, user):
        log_in(client, user)

        html = client.get("/").content.decode()

        assert user.email in html


class TestProfileIsPersonal:
    def test_shows_own_email_not_the_other_users(self, client, user, other_user):
        log_in(client, other_user)

        html = client.get(PROFILE_URL).content.decode()

        assert other_user.email in html
        assert user.email not in html


class TestOtherAccountPages:
    """Страницы allauth, не переопределённые вручную, получают наш каркас."""

    def test_email_page_uses_site_layout(self, client, user):
        EmailAddress.objects.create(
            user=user, email=user.email, primary=True, verified=True
        )
        log_in(client, user)

        html = client.get(reverse("account_email")).content.decode()

        assert 'class="site-header"' in html
        assert "auth-shell" in html
        assert user.email in html
        assert "Подтвержден" in html

    def test_email_page_loads_allauth_scripts(self, client, user):
        EmailAddress.objects.create(
            user=user, email=user.email, primary=True, verified=True
        )
        log_in(client, user)

        html = client.get(reverse("account_email")).content.decode()

        # Без этих скриптов кнопки управления адресом не работают:
        # их allauth отдаёт через блок `extra_body`.
        assert "account/js/account.js" in html
        assert "account/js/onload.js" in html

    def test_reauthenticate_page_uses_site_layout(self, client, user):
        log_in(client, user)

        html = client.get(reverse("account_reauthenticate")).content.decode()

        assert 'class="site-header"' in html
        assert "auth-shell" in html


class TestLocalization:
    def test_english_profile_has_no_russian_text(self, client, user):
        log_in(client, user)

        html = client.get(
            PROFILE_URL, headers={"accept-language": "en"}
        ).content.decode()

        assert "Account" in html
        assert "Save" in html
        assert not any("а" <= char <= "я" for char in html)

    def test_english_menu_has_no_russian_text(self, client, user):
        log_in(client, user)

        html = client.get("/", headers={"accept-language": "en"}).content.decode()

        assert "User menu" in html
        assert not any("а" <= char <= "я" for char in html)
