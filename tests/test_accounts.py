"""Тесты приложения accounts: пользователи, согласия, роли, allauth."""

import pytest
from django.db import IntegrityError

from apps.accounts.models import User, UserRole

pytestmark = pytest.mark.django_db


class TestUserManager:
    def test_create_user_by_email(self):
        user = User.objects.create_user(
            email="test@example.com", password="secret-pass-1"
        )

        assert user.email == "test@example.com"
        assert user.check_password("secret-pass-1")
        assert user.role == UserRole.USER
        assert not user.is_staff
        assert not user.is_superuser

    def test_email_is_normalized(self):
        user = User.objects.create_user(
            email="Test@Example.COM", password="secret-pass-1"
        )

        assert user.email == "Test@example.com"

    def test_email_required(self):
        with pytest.raises(ValueError, match="Email обязателен"):
            User.objects.create_user(email="", password="secret-pass-1")

    def test_email_is_unique(self, user):
        with pytest.raises(IntegrityError):
            User.objects.create_user(email=user.email, password="another-pass-1")

    def test_create_superuser(self):
        user = User.objects.create_superuser(
            email="boss@example.com", password="secret-pass-1"
        )

        assert user.is_staff
        assert user.is_superuser
        assert user.role == UserRole.ADMIN

    def test_create_superuser_requires_staff(self):
        with pytest.raises(ValueError, match="is_staff=True"):
            User.objects.create_superuser(
                email="boss@example.com", password="secret-pass-1", is_staff=False
            )

    def test_create_superuser_requires_superuser(self):
        with pytest.raises(ValueError, match="is_superuser=True"):
            User.objects.create_superuser(
                email="boss@example.com", password="secret-pass-1", is_superuser=False
            )

    def test_username_field_is_email(self):
        assert User.USERNAME_FIELD == "email"


class TestUserProperties:
    def test_str_prefers_display_name(self):
        user = User(email="test@example.com", display_name="Андрей")

        assert str(user) == "Андрей"

    def test_str_falls_back_to_email(self):
        assert str(User(email="test@example.com")) == "test@example.com"

    def test_regular_user_is_not_moderator(self, user):
        assert not user.is_moderator

    def test_moderator_role_can_moderate(self, moderator):
        assert moderator.is_moderator

    def test_admin_role_can_moderate(self, admin_user):
        assert admin_user.is_moderator

    def test_has_consents_requires_all_three(self, user):
        assert user.has_consents

        user.consent_pd_at = None
        assert not user.has_consents


class TestPersonLink:
    def test_user_can_be_linked_to_person(self, user, person):
        user.person = person
        user.save()

        user.refresh_from_db()
        assert user.person == person
        assert person.user == user

    def test_deleting_person_keeps_user(self, user, person):
        user.person = person
        user.save()

        person.delete()

        user.refresh_from_db()
        assert user.person is None


class TestSignup:
    """Регистрация через allauth: согласия заполняются,
    email не подтверждён.
    """

    URL = "/accounts/signup/"

    def test_signup_creates_user_with_consents(self, client):
        response = client.post(
            self.URL,
            {
                "email": "new@example.com",
                "display_name": "Новый",
                "password1": "very-secret-pass-42",
                "password2": "very-secret-pass-42",
                "terms_accepted": "on",
                "privacy_accepted": "on",
                "consent_pd": "on",
            },
        )

        assert response.status_code == 302
        user = User.objects.get(email="new@example.com")
        assert user.display_name == "Новый"
        assert user.has_consents
        assert user.consent_pd_version
        assert user.role == UserRole.USER

    def test_signup_requires_consents(self, client):
        response = client.post(
            self.URL,
            {
                "email": "nope@example.com",
                "password1": "very-secret-pass-42",
                "password2": "very-secret-pass-42",
            },
        )

        assert response.status_code == 200
        assert not User.objects.filter(email="nope@example.com").exists()

    def test_email_is_not_verified_immediately(self, client):
        from allauth.account.models import EmailAddress

        client.post(
            self.URL,
            {
                "email": "verify@example.com",
                "password1": "very-secret-pass-42",
                "password2": "very-secret-pass-42",
                "terms_accepted": "on",
                "privacy_accepted": "on",
                "consent_pd": "on",
            },
        )

        address = EmailAddress.objects.get(email="verify@example.com")
        assert not address.verified


class TestAdminAccess:
    def test_admin_login_page_opens(self, client):
        assert client.get("/admin/login/").status_code == 200

    def test_regular_user_cannot_open_admin(self, client, user):
        client.force_login(user)

        response = client.get("/admin/")

        assert response.status_code == 302
