"""Тесты комментариев и модерации."""

import pytest
from django.contrib.contenttypes.models import ContentType

from apps.comments.models import Comment
from apps.core.models import Status
from apps.moderation.models import (
    ModerationAction,
    ModerationActionType,
    Report,
    ReportStatus,
)

pytestmark = pytest.mark.django_db


class TestComment:
    def test_comment_attached_to_photo(self, photo, user):
        comment = Comment.objects.create(
            author=user,
            text="Отличное фото!",
            content_type=ContentType.objects.get_for_model(photo),
            object_id=photo.pk,
        )

        assert comment.content_object == photo

    def test_comment_attached_to_event(self, event, user):
        comment = Comment.objects.create(
            author=user,
            text="Помню этот турнир",
            content_type=ContentType.objects.get_for_model(event),
            object_id=event.pk,
        )

        assert comment.content_object == event

    def test_comment_attached_to_team(self, team, user):
        comment = Comment.objects.create(
            author=user,
            text="Мы играли против них",
            content_type=ContentType.objects.get_for_model(team),
            object_id=team.pk,
        )

        assert comment.content_object == team

    def test_reply_has_parent(self, photo, user):
        parent = Comment.objects.create(
            author=user,
            text="Вопрос",
            content_type=ContentType.objects.get_for_model(photo),
            object_id=photo.pk,
        )
        reply = Comment.objects.create(
            author=user,
            text="Ответ",
            parent=parent,
            content_type=ContentType.objects.get_for_model(photo),
            object_id=photo.pk,
        )

        assert reply.is_reply
        assert not parent.is_reply
        assert parent.replies.count() == 1

    def test_deleting_parent_deletes_replies(self, photo, user):
        parent = Comment.objects.create(
            author=user,
            text="Вопрос",
            content_type=ContentType.objects.get_for_model(photo),
            object_id=photo.pk,
        )
        Comment.objects.create(
            author=user,
            text="Ответ",
            parent=parent,
            content_type=ContentType.objects.get_for_model(photo),
            object_id=photo.pk,
        )

        parent.delete()

        assert Comment.objects.count() == 0

    def test_comment_has_status_and_history(self, photo, user, moderator):
        comment = Comment.objects.create(
            author=user,
            text="Текст",
            content_type=ContentType.objects.get_for_model(photo),
            object_id=photo.pk,
        )

        comment.hide(moderator=moderator, reason="спам")

        comment.refresh_from_db()
        assert comment.status == Status.HIDDEN
        assert comment.history.count() >= 2

    def test_soft_deleted_comment_leaves_public_queryset(self, photo, user):
        comment = Comment.objects.create(
            author=user,
            text="Текст",
            content_type=ContentType.objects.get_for_model(photo),
            object_id=photo.pk,
        )
        comment.soft_delete()

        assert not Comment.public.filter(pk=comment.pk).exists()
        assert Comment.objects.filter(pk=comment.pk).exists()


class TestReport:
    def test_anonymous_report_allowed(self, photo):
        report = Report.objects.create(
            content_type=ContentType.objects.get_for_model(photo),
            object_id=photo.pk,
            reason="copyright",
        )

        assert report.reporter is None
        assert report.status == ReportStatus.NEW

    def test_reporter_is_recorded(self, photo, user):
        report = Report.objects.create(
            reporter=user,
            content_type=ContentType.objects.get_for_model(photo),
            object_id=photo.pk,
            reason="wrong_person",
            comment="Это не он",
        )

        assert report.reporter == user
        assert report.content_object == photo

    def test_resolving_report_records_decision(self, photo, moderator):
        report = Report.objects.create(
            content_type=ContentType.objects.get_for_model(photo),
            object_id=photo.pk,
            reason="privacy",
        )

        report.status = ReportStatus.RESOLVED
        report.resolution = "Фото скрыто"
        report.resolved_by = moderator
        report.save()

        report.refresh_from_db()
        assert report.status == ReportStatus.RESOLVED
        assert report.resolved_by == moderator

    def test_report_has_history(self, photo, moderator):
        report = Report.objects.create(
            content_type=ContentType.objects.get_for_model(photo),
            object_id=photo.pk,
            reason="other",
        )

        report.status = ReportStatus.REJECTED
        report.save()

        assert report.history.count() >= 2


class TestModerationAction:
    def test_action_records_who_what_and_why(self, photo, moderator):
        action = ModerationAction.objects.create(
            moderator=moderator,
            action=ModerationActionType.HIDE,
            content_type=ContentType.objects.get_for_model(photo),
            object_id=photo.pk,
            reason="нарушение приватности",
        )

        assert action.content_object == photo
        assert action.action == ModerationActionType.HIDE
        assert action.reason == "нарушение приватности"

    def test_action_str(self, photo, moderator):
        action = ModerationAction.objects.create(
            moderator=moderator,
            action=ModerationActionType.PUBLISH,
            content_type=ContentType.objects.get_for_model(photo),
            object_id=photo.pk,
        )

        assert "опубликовать" in str(action)

    def test_deleting_moderator_is_forbidden(self, photo, moderator):
        from django.db.models import ProtectedError

        ModerationAction.objects.create(
            moderator=moderator,
            action=ModerationActionType.HIDE,
            content_type=ContentType.objects.get_for_model(photo),
            object_id=photo.pk,
        )

        with pytest.raises(ProtectedError):
            moderator.delete()
