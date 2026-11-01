"""Тесты сервиса решений модерации.

Правило подэтапа 4.3: каждое решение пишется в журнал через одну
функцию, право на решение проверяет сервис. Проверяется, что сервис
и журнал отвечают одинаково на все пять решений, что причина отклонения
обязательна и что удалённый объект не решается.
"""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied

from apps.catalog.models import Team
from apps.core.models import Status
from apps.moderation import services
from apps.moderation.models import ModerationAction, ModerationActionType
from tests.factories import ModeratorFactory, PersonFactory, TeamFactory

pytestmark = pytest.mark.django_db


def make_moderator(sport, email="scope-moderator@example.com"):
    """Модератор ровно одного вида спорта."""
    return ModeratorFactory(email=email, moderated_sports=[sport])


def actions():
    """Все записи журнала: порядок проверки не важен."""
    return ModerationAction.objects.all()


class TestJournal:
    """Каждое решение оставляет ровно одну запись журнала."""

    def test_publish_writes_status_and_journal(self, moderator, team):
        assert services.publish_object(team, moderator, reason="проверено")

        team.refresh_from_db()
        assert team.status == Status.PUBLISHED
        assert team.moderated_by == moderator
        assert team.moderation_reason == "проверено"
        action = actions().get()
        assert action.action == ModerationActionType.PUBLISH
        assert action.moderator == moderator
        assert action.reason == "проверено"
        assert action.content_type == ContentType.objects.get_for_model(Team)
        assert action.object_id == team.pk

    def test_hide_writes_status_and_journal(self, moderator, team):
        assert services.hide_object(team, moderator, reason="не по теме")

        team.refresh_from_db()
        assert team.status == Status.HIDDEN
        action = actions().get()
        assert action.action == ModerationActionType.HIDE
        assert action.reason == "не по теме"

    def test_reject_writes_status_and_journal(self, moderator, team):
        assert services.reject_object(team, moderator, "не та команда")

        team.refresh_from_db()
        assert team.status == Status.REJECTED
        assert team.moderation_reason == "не та команда"
        action = actions().get()
        assert action.action == ModerationActionType.REJECT
        assert action.reason == "не та команда"

    def test_soft_delete_writes_journal(self, moderator, team):
        assert services.soft_delete_object(team, moderator, "удалено модератором")

        team.refresh_from_db()
        assert team.is_deleted
        action = actions().get()
        assert action.action == ModerationActionType.DELETE
        assert action.reason == "удалено модератором"

    def test_restore_writes_journal(self, moderator, team):
        team.soft_delete(reason="ошибочно")

        assert services.restore_object(team, moderator, "восстановлено модератором")

        team.refresh_from_db()
        assert not team.is_deleted
        action = actions().get()
        assert action.action == ModerationActionType.RESTORE
        assert action.reason == "восстановлено модератором"

    def test_log_action_writes_for_object_without_sport(self, moderator):
        """Журнал не зависит от вида спорта: у человека его нет."""
        person = PersonFactory()

        services.log_action(
            person, moderator, ModerationActionType.HIDE, reason="скрыть"
        )

        action = actions().get()
        assert action.object_id == person.pk
        assert action.content_type == ContentType.objects.get_for_model(type(person))


class TestRejectReason:
    """Причина отклонения обязательна и проверяется в сервисе."""

    @pytest.mark.parametrize("reason", ["", "   ", "\n\t"])
    def test_empty_reason_is_refused(self, moderator, team, reason):
        with pytest.raises(ValueError):
            services.reject_object(team, moderator, reason)

        team.refresh_from_db()
        assert team.status == Status.DRAFT
        assert not actions().exists()

    def test_reason_reaches_object_and_journal(self, moderator, team):
        """Автор увидит ту же причину, что записана в журнале."""
        assert services.reject_object(team, moderator, "не наш год")

        team.refresh_from_db()
        assert team.status == Status.REJECTED
        assert team.moderation_reason == "не наш год"
        assert actions().get().reason == "не наш год"


class TestRights:
    """Право на решение проверяет сервис, а не вызывающий код."""

    def test_plain_user_is_refused(self, user, team):
        with pytest.raises(PermissionDenied):
            services.publish_object(team, user)

        team.refresh_from_db()
        assert team.status == Status.DRAFT
        assert not actions().exists()

    def test_other_sport_moderator_is_refused(self, baseball, softball):
        moderator = make_moderator(softball)
        team = TeamFactory(sport=baseball)

        with pytest.raises(PermissionDenied):
            services.publish_object(team, moderator)

        team.refresh_from_db()
        assert team.status == Status.DRAFT
        assert not actions().exists()

    def test_person_without_sport_is_open_to_every_moderator(self, softball):
        moderator = make_moderator(softball)
        person = PersonFactory()

        assert services.publish_object(person, moderator)

        person.refresh_from_db()
        assert person.status == Status.PUBLISHED

    def test_anonymous_user_is_refused(self, team):
        with pytest.raises(PermissionDenied):
            services.publish_object(team, AnonymousUser())

        team.refresh_from_db()
        assert team.status == Status.DRAFT


class TestIdempotency:
    """Повторное решение не пишет вторую запись."""

    def test_second_publish_returns_false(self, moderator, team):
        assert services.publish_object(team, moderator)
        assert services.publish_object(team, moderator) is False

        assert actions().count() == 1

    def test_second_hide_returns_false(self, moderator, team):
        assert services.hide_object(team, moderator)
        assert services.hide_object(team, moderator) is False

        assert actions().count() == 1

    def test_second_reject_returns_false(self, moderator, team):
        assert services.reject_object(team, moderator, "не по теме")
        assert services.reject_object(team, moderator, "не по теме") is False

        assert actions().count() == 1

    def test_second_soft_delete_returns_false(self, moderator, team):
        assert services.soft_delete_object(team, moderator)
        assert services.soft_delete_object(team, moderator) is False

        assert actions().count() == 1

    def test_restore_of_alive_object_returns_false(self, moderator, team):
        assert services.restore_object(team, moderator) is False

        team.refresh_from_db()
        assert not team.is_deleted
        assert not actions().exists()


class TestDeletedObject:
    """Удалённый объект не решается, кроме восстановления."""

    @pytest.fixture
    def deleted_team(self, team):
        team.soft_delete(reason="ошибочно")
        return team

    def test_publish_is_refused(self, moderator, deleted_team):
        assert services.publish_object(deleted_team, moderator) is False

        deleted_team.refresh_from_db()
        assert deleted_team.status == Status.DRAFT
        assert not actions().exists()

    def test_hide_is_refused(self, moderator, deleted_team):
        assert services.hide_object(deleted_team, moderator) is False

        deleted_team.refresh_from_db()
        assert deleted_team.status == Status.DRAFT
        assert not actions().exists()

    def test_reject_is_refused(self, moderator, deleted_team):
        assert services.reject_object(deleted_team, moderator, "не по теме") is False

        deleted_team.refresh_from_db()
        assert deleted_team.status == Status.DRAFT
        assert not actions().exists()

    def test_restore_works(self, moderator, deleted_team):
        assert services.restore_object(deleted_team, moderator)

        deleted_team.refresh_from_db()
        assert not deleted_team.is_deleted
        assert actions().get().action == ModerationActionType.RESTORE
