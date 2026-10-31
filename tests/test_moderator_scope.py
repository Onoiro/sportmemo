"""Тесты области модератора: доступ по видам спорта.

Правило подэтапа 4.1: модератор решает только по своим видам спорта.
Проверяется в трёх точках входа (страница, правка команды и события,
правка фото), в письмах и в админке пользователей. Объект без вида спорта
(`Person`) доступен всем модераторам — инвариант «архитектура
мультиспортная».
"""

import importlib

import pytest
from django.apps import apps as django_apps
from django.core import mail
from django.urls import reverse

from apps.accounts.models import User, UserRole
from apps.catalog.emails import notify_moderators_new_team
from apps.catalog.models import Person
from apps.core.models import Status
from apps.moderation.emails import notify_moderators_new_report
from apps.moderation.models import Report
from apps.photos.services import can_edit_photo
from tests.factories import (
    EventFactory,
    ModeratorFactory,
    PersonFactory,
    PhotoFactory,
    ReportFactory,
    TeamFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


def make_moderator(sport, email="scope-moderator@example.com"):
    """Модератор ровно одного вида спорта."""
    return ModeratorFactory(email=email, moderated_sports=[sport])


class TestCanModerateMethod:
    """`can_moderate` — единственная точка проверки области."""

    def test_plain_user_cannot_moderate(self, user, baseball):
        assert not user.can_moderate()
        assert not user.can_moderate(TeamFactory(sport=baseball))

    def test_moderator_without_scope_is_moderator_but_not_for_object(self, baseball):
        moderator = UserFactory(role=UserRole.MODERATOR)

        assert moderator.can_moderate()
        assert not moderator.can_moderate(TeamFactory(sport=baseball))

    def test_moderator_sees_own_sport(self, baseball):
        moderator = make_moderator(baseball)

        assert moderator.can_moderate(TeamFactory(sport=baseball))

    def test_object_without_sport_is_open_to_every_moderator(self, softball):
        moderator = make_moderator(softball)

        assert moderator.can_moderate(PersonFactory())

    def test_admin_role_and_superuser_see_everything(self, baseball, softball):
        admin = UserFactory(role=UserRole.ADMIN)
        superuser = UserFactory(is_superuser=True)
        team = TeamFactory(sport=softball)

        assert admin.can_moderate(team)
        assert superuser.can_moderate(team)


class TestObjectPages:
    """Черновик чужого вида спорта по прямой ссылке даёт 404."""

    def test_own_sport_team_draft_opens(self, client, baseball):
        moderator = make_moderator(baseball)
        team = TeamFactory(sport=baseball)
        client.force_login(moderator)

        response = client.get(team.get_absolute_url())

        assert response.status_code == 200

    def test_other_sport_team_draft_is_404(self, client, baseball, softball):
        moderator = make_moderator(baseball)
        team = TeamFactory(sport=softball)
        client.force_login(moderator)

        response = client.get(team.get_absolute_url())

        assert response.status_code == 404

    def test_other_sport_event_draft_is_404(self, client, baseball, softball):
        moderator = make_moderator(baseball)
        event = EventFactory(sport=softball)
        client.force_login(moderator)

        assert client.get(event.get_absolute_url()).status_code == 404

    def test_other_sport_photo_draft_is_404(self, client, baseball, softball):
        moderator = make_moderator(baseball)
        photo = PhotoFactory(sport=softball, status=Status.DRAFT)
        client.force_login(moderator)

        response = client.get(reverse("photos:photo_detail", args=[photo.pk]))

        assert response.status_code == 404

    def test_own_sport_photo_draft_opens(self, client, baseball):
        moderator = make_moderator(baseball)
        photo = PhotoFactory(sport=baseball, status=Status.DRAFT)
        client.force_login(moderator)

        response = client.get(reverse("photos:photo_detail", args=[photo.pk]))

        assert response.status_code == 200

    def test_person_draft_is_open_to_every_moderator(self, client, softball):
        moderator = make_moderator(softball)
        person = PersonFactory()
        client.force_login(moderator)

        response = client.get(person.get_absolute_url())

        assert response.status_code == 200
        assert Person.objects.filter(pk=person.pk).exists()


class TestEditPages:
    """Правка: своё — 200, чужое — 404, автор — всегда своё."""

    def test_own_sport_team_edit_opens(self, client, baseball):
        moderator = make_moderator(baseball)
        team = TeamFactory(sport=baseball)
        client.force_login(moderator)

        response = client.get(reverse("catalog:team_update", args=[team.slug]))

        assert response.status_code == 200

    def test_other_sport_team_edit_is_404(self, client, baseball, softball):
        moderator = make_moderator(baseball)
        team = TeamFactory(sport=softball)
        client.force_login(moderator)

        response = client.get(reverse("catalog:team_update", args=[team.slug]))

        assert response.status_code == 404

    def test_other_sport_photo_edit_is_404(self, client, baseball, softball):
        moderator = make_moderator(baseball)
        photo = PhotoFactory(sport=softball, status=Status.DRAFT)
        client.force_login(moderator)

        response = client.get(reverse("photos:photo_edit", args=[photo.pk]))

        assert response.status_code == 404

    def test_own_sport_photo_edit_opens(self, client, baseball):
        moderator = make_moderator(baseball)
        photo = PhotoFactory(sport=baseball, status=Status.DRAFT)
        client.force_login(moderator)

        response = client.get(reverse("photos:photo_edit", args=[photo.pk]))

        assert response.status_code == 200

    def test_author_keeps_access_outside_scope(self, client, softball):
        """Право автора от области не зависит."""
        author = UserFactory(email="author@example.com")
        team = TeamFactory(sport=softball, created_by=author)
        client.force_login(author)

        assert client.get(team.get_absolute_url()).status_code == 200
        assert (
            client.get(reverse("catalog:team_update", args=[team.slug])).status_code
            == 200
        )

    def test_author_keeps_photo_access_outside_scope(self, softball):
        author = UserFactory(email="author@example.com")
        photo = PhotoFactory(sport=softball, status=Status.DRAFT, uploaded_by=author)

        assert can_edit_photo(author, photo)


class TestFullAccess:
    """Админ и суперадмин видят черновики всех видов спорта."""

    def test_admin_role_sees_other_sport_draft(self, client, softball):
        admin = UserFactory(role=UserRole.ADMIN)
        team = TeamFactory(sport=softball)
        client.force_login(admin)

        assert client.get(team.get_absolute_url()).status_code == 200
        assert (
            client.get(reverse("catalog:team_update", args=[team.slug])).status_code
            == 200
        )

    def test_superuser_sees_other_sport_draft(self, client, softball):
        superuser = UserFactory(is_superuser=True)
        photo = PhotoFactory(sport=softball, status=Status.DRAFT)
        client.force_login(superuser)

        response = client.get(reverse("photos:photo_edit", args=[photo.pk]))

        assert response.status_code == 200


class TestEmptyScope:
    """Пустой список — модератор не видит чужого, своё видит как автор."""

    def test_moderator_without_sports_sees_no_foreign_draft(self, client, softball):
        moderator = UserFactory(role=UserRole.MODERATOR)
        team = TeamFactory(sport=softball)
        client.force_login(moderator)

        assert client.get(team.get_absolute_url()).status_code == 404

    def test_moderator_without_sports_sees_own_draft(self, client, softball):
        moderator = UserFactory(role=UserRole.MODERATOR)
        team = TeamFactory(sport=softball, created_by=moderator)
        client.force_login(moderator)

        assert client.get(team.get_absolute_url()).status_code == 200


class TestBackfill:
    """Бэкфилл миграции: поведение после деплоя не меняется."""

    def test_assigns_all_sports_to_moderators_and_admins(self, baseball, softball):
        module = importlib.import_module(
            "apps.accounts.migrations.0004_trusted_and_backfill"
        )
        moderator = UserFactory(role=UserRole.MODERATOR)
        admin = UserFactory(role=UserRole.ADMIN)
        plain = UserFactory()

        module.assign_all_sports(django_apps, None)

        assert set(moderator.moderated_sports.values_list("slug", flat=True)) == {
            "baseball",
            "softball",
        }
        assert set(admin.moderated_sports.values_list("slug", flat=True)) == {
            "baseball",
            "softball",
        }
        assert not plain.moderated_sports.exists()


class TestUserAdminPermissions:
    """Роль и область — это права, поэтому раздел открыт только суперадмину."""

    def test_staff_moderator_gets_403(self, client, baseball):
        moderator = ModeratorFactory(email="staff-moderator@example.com", is_staff=True)
        client.force_login(moderator)

        response = client.get(reverse("admin:accounts_user_changelist"))

        assert response.status_code == 403

    def test_superuser_opens_user_list(self, client):
        superuser = UserFactory(is_superuser=True, is_staff=True)
        client.force_login(superuser)

        response = client.get(reverse("admin:accounts_user_changelist"))

        assert response.status_code == 200

    def test_scope_column_shows_names(self, baseball, softball):
        from django.contrib import admin

        model_admin = admin.site._registry[User]
        moderator = make_moderator(baseball)

        assert model_admin.sport_scope(moderator) == "Бейсбол"
        assert model_admin.sport_scope(UserFactory(role=UserRole.ADMIN)) == "все"
        assert model_admin.sport_scope(UserFactory()) == "—"


class TestScopedEmails:
    """Письма уходят по виду спорта объекта."""

    def test_team_letter_goes_to_own_sport_moderator_only(self, baseball, softball):
        baseball_moderator = make_moderator(baseball, email="base@example.com")
        softball_moderator = make_moderator(softball, email="soft@example.com")
        admin = UserFactory(role=UserRole.ADMIN, email="admin-role@example.com")
        team = TeamFactory(sport=softball)

        notify_moderators_new_team(team)

        assert len(mail.outbox) == 1
        recipients = mail.outbox[0].to
        assert softball_moderator.email in recipients
        assert admin.email in recipients
        assert baseball_moderator.email not in recipients

    def test_report_gets_sport_of_target(self, client, softball):
        team = TeamFactory(sport=softball)
        team.publish()

        response = client.post(
            reverse("moderation:report_add"),
            {
                "target": "team",
                "object_id": team.pk,
                "reason": "wrong_data",
                "comment": "Неверный город.",
            },
        )

        assert response.status_code == 302
        report = Report.objects.get(object_id=team.pk)
        assert report.sport == softball

    def test_report_letter_goes_by_report_sport(self, baseball, softball):
        baseball_moderator = make_moderator(baseball, email="base@example.com")
        softball_moderator = make_moderator(softball, email="soft@example.com")
        team = TeamFactory(sport=softball)
        report = ReportFactory(sport=softball, object_id=team.pk)

        notify_moderators_new_report(report)

        recipients = mail.outbox[0].to
        assert softball_moderator.email in recipients
        assert baseball_moderator.email not in recipients

    def test_report_without_sport_goes_to_all_moderators(self, baseball):
        baseball_moderator = make_moderator(baseball, email="base@example.com")
        report = ReportFactory(sport=None, object_id=1)

        notify_moderators_new_report(report)

        assert baseball_moderator.email in mail.outbox[0].to
