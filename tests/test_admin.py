"""Тесты админки: модераторские действия и слияние дубликатов."""

import pytest
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Team
from apps.core.models import Status
from apps.moderation.models import ModerationAction, ModerationActionType
from tests.factories import PhotoFactory, PhotoPersonFactory, SportFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client_logged_in(client, admin_user):
    client.force_login(admin_user)
    return client


class TestAdminRegistration:
    def test_all_meaningful_models_are_registered(self):
        expected = {
            "accounts.User",
            "catalog.Team",
            "catalog.TeamMembership",
            "catalog.Person",
            "catalog.PersonName",
            "catalog.Event",
            "catalog.EventTeam",
            "photos.Source",
            "photos.Photo",
            "photos.PhotoPerson",
            "comments.Comment",
            "moderation.Report",
            "moderation.ModerationAction",
            "core.Sport",
        }

        registered = {model._meta.label for model in admin.site._registry}

        assert expected <= registered

    def test_moderation_action_journal_is_read_only(self):
        model_admin = admin.site._registry[ModerationAction]

        assert not model_admin.has_add_permission(None)


class TestModerationActions:
    def test_publish_action_sets_status_and_logs(
        self, admin_client_logged_in, team, admin_user
    ):
        url = reverse("admin:catalog_team_changelist")

        admin_client_logged_in.post(
            url,
            {"action": "action_publish", "_selected_action": [str(team.pk)]},
            follow=True,
        )

        team.refresh_from_db()
        assert team.status == Status.PUBLISHED
        assert team.moderated_by == admin_user

        action = ModerationAction.objects.get(object_id=team.pk)
        assert action.action == ModerationActionType.PUBLISH
        assert action.moderator == admin_user

    def test_hide_action_logs_decision(self, admin_client_logged_in, team):
        url = reverse("admin:catalog_team_changelist")

        admin_client_logged_in.post(
            url,
            {"action": "action_hide", "_selected_action": [str(team.pk)]},
            follow=True,
        )

        team.refresh_from_db()
        assert team.status == Status.HIDDEN
        assert ModerationAction.objects.filter(
            object_id=team.pk, action=ModerationActionType.HIDE
        ).exists()

    def test_soft_delete_action_hides_from_public(self, admin_client_logged_in, team):
        url = reverse("admin:catalog_team_changelist")

        admin_client_logged_in.post(
            url,
            {"action": "action_soft_delete", "_selected_action": [str(team.pk)]},
            follow=True,
        )

        team.refresh_from_db()
        assert team.is_deleted
        assert not Team.public.filter(pk=team.pk).exists()
        assert ModerationAction.objects.filter(
            object_id=team.pk, action=ModerationActionType.DELETE
        ).exists()

    def test_restore_action_returns_to_public(self, admin_client_logged_in, team):
        team.soft_delete()
        url = reverse("admin:catalog_team_changelist")

        admin_client_logged_in.post(
            url,
            {"action": "action_restore", "_selected_action": [str(team.pk)]},
            follow=True,
        )

        team.refresh_from_db()
        assert not team.is_deleted
        assert Team.public.filter(pk=team.pk).exists()

    def test_already_published_object_is_not_logged_again(
        self, admin_client_logged_in, team, admin_user
    ):
        team.publish(moderator=admin_user)
        url = reverse("admin:catalog_team_changelist")

        admin_client_logged_in.post(
            url,
            {"action": "action_publish", "_selected_action": [str(team.pk)]},
            follow=True,
        )

        assert not ModerationAction.objects.filter(
            object_id=team.pk, action=ModerationActionType.PUBLISH
        ).exists()


class TestMergeAction:
    def test_merge_action_redirects_to_merge_page(self, admin_client_logged_in, team):
        url = reverse("admin:catalog_team_changelist")

        response = admin_client_logged_in.post(
            url,
            {"action": "action_merge", "_selected_action": [str(team.pk)]},
        )

        assert response.status_code == 302
        assert "merge" in response.url
        assert f"ids={team.pk}" in response.url

    def test_merge_page_lists_sources_and_candidates(
        self, admin_client_logged_in, team, baseball, user
    ):
        target = Team.objects.create(sport=baseball, name="Канон", created_by=user)
        url = reverse("admin:catalog_team_merge")

        response = admin_client_logged_in.get(url, {"ids": str(team.pk)})

        assert response.status_code == 200
        assert team in response.context["sources"]
        assert target in response.context["candidates"]
        assert team not in response.context["candidates"]

    def test_merge_moves_relations_and_logs(
        self, admin_client_logged_in, team, person, admin_user
    ):
        from apps.catalog.models import TeamMembership

        target = Team.objects.create(
            sport=team.sport, name="Канон", created_by=admin_user
        )
        membership = TeamMembership.objects.create(
            team=team, person=person, year_from=1994
        )
        url = reverse("admin:catalog_team_merge")

        admin_client_logged_in.post(
            url,
            {"ids": str(team.pk), "target": str(target.pk)},
            follow=True,
        )

        membership.refresh_from_db()
        team.refresh_from_db()
        assert membership.team == target
        assert team.merged_into == target
        assert team.is_deleted
        assert ModerationAction.objects.filter(
            object_id=team.pk, action=ModerationActionType.MERGE
        ).exists()

    def test_merge_rejects_target_from_sources(self, admin_client_logged_in, team):
        url = reverse("admin:catalog_team_merge")

        response = admin_client_logged_in.post(
            url,
            {"ids": str(team.pk), "target": str(team.pk)},
            follow=True,
        )

        team.refresh_from_db()
        assert team.merged_into is None
        assert response.status_code == 200

    def test_merge_without_target_is_rejected(self, admin_client_logged_in, team):
        url = reverse("admin:catalog_team_merge")

        admin_client_logged_in.post(
            url, {"ids": str(team.pk), "target": ""}, follow=True
        )

        team.refresh_from_db()
        assert team.merged_into is None

    def test_person_merge_page_works(self, admin_client_logged_in, person):
        url = reverse("admin:catalog_person_merge")

        response = admin_client_logged_in.get(url, {"ids": str(person.pk)})

        assert response.status_code == 200

    def test_event_merge_page_works(self, admin_client_logged_in, event):
        url = reverse("admin:catalog_event_merge")

        response = admin_client_logged_in.get(url, {"ids": str(event.pk)})

        assert response.status_code == 200

    def test_moderator_without_permission_cannot_merge(self, client, team, moderator):
        """Модератор с ролью, но без прав Django-админки
        не может открыть слияние.
        """
        client.force_login(moderator)
        url = reverse("admin:catalog_team_merge")

        response = client.get(url, {"ids": str(team.pk)})

        assert response.status_code == 302


class TestAdminSearchAndFilters:
    def test_person_admin_searches_by_name(self, admin_client_logged_in, person):
        url = reverse("admin:catalog_person_changelist")

        response = admin_client_logged_in.get(url, {"q": "Смирнов"})

        assert response.status_code == 200
        assert person in response.context["cl"].queryset

    def test_photo_admin_filters_by_processing_status(
        self, admin_client_logged_in, photo
    ):
        url = reverse("admin:photos_photo_changelist")

        response = admin_client_logged_in.get(url, {"processing_status": "uploaded"})

        assert response.status_code == 200

    def test_team_admin_has_merge_action(self, admin_client_logged_in):
        model_admin = admin.site._registry[Team]

        assert "action_merge" in model_admin.actions

    def test_regular_user_cannot_open_admin(self, client, user):
        client.force_login(user)

        assert client.get(reverse("admin:index")).status_code == 302


class TestReportAdminActions:
    def test_resolve_report_action(self, admin_client_logged_in, photo, admin_user):
        from apps.moderation.models import Report

        report = Report.objects.create(
            content_type=ContentType.objects.get_for_model(photo),
            object_id=photo.pk,
            reason="copyright",
        )
        url = reverse("admin:moderation_report_changelist")

        admin_client_logged_in.post(
            url,
            {"action": "action_resolve", "_selected_action": [str(report.pk)]},
            follow=True,
        )

        report.refresh_from_db()
        assert report.status == "resolved"
        assert report.resolved_by == admin_user


class TestPhotoMergeAction:
    """Слияние фото из админки: кандидаты сужены, решение пишется в журнал."""

    def test_photo_admin_has_merge_action(self):
        from apps.photos.models import Photo

        model_admin = admin.site._registry[Photo]

        assert "action_merge" in model_admin.actions

    def test_merge_action_redirects_to_merge_page(self, admin_client_logged_in, photo):
        url = reverse("admin:photos_photo_changelist")

        response = admin_client_logged_in.post(
            url,
            {"action": "action_merge", "_selected_action": [str(photo.pk)]},
        )

        assert response.status_code == 302
        assert f"ids={photo.pk}" in response.url

    def test_merge_page_lists_sources_and_candidates(
        self, admin_client_logged_in, photo
    ):
        candidate = PhotoFactory(sport=photo.sport)
        url = reverse("admin:photos_photo_merge")

        response = admin_client_logged_in.get(url, {"ids": str(photo.pk)})

        assert response.status_code == 200
        assert list(response.context["sources"]) == [photo]
        assert list(response.context["candidates"]) == [candidate]
        content = response.content.decode()
        assert str(photo) in content
        assert str(candidate) in content

    def test_candidates_exclude_merged_deleted_and_other_sport(
        self, admin_client_logged_in, photo
    ):
        allowed = PhotoFactory(sport=photo.sport)
        PhotoFactory(sport=photo.sport, merged_into=allowed)
        PhotoFactory(sport=photo.sport, deleted_at=timezone.now())
        other_sport = SportFactory(slug="hockey", name="Хоккей")
        PhotoFactory(sport=other_sport)
        url = reverse("admin:photos_photo_merge")

        response = admin_client_logged_in.get(url, {"ids": str(photo.pk)})

        assert list(response.context["candidates"]) == [allowed]

    def test_target_from_other_sport_is_rejected(self, admin_client_logged_in, photo):
        other_sport = SportFactory(slug="hockey", name="Хоккей")
        stranger = PhotoFactory(sport=other_sport)
        link = PhotoPersonFactory(photo=photo)
        url = reverse("admin:photos_photo_merge")

        admin_client_logged_in.post(
            url,
            {"ids": str(photo.pk), "target": str(stranger.pk)},
            follow=True,
        )

        photo.refresh_from_db()
        link.refresh_from_db()
        assert photo.merged_into is None
        assert not photo.is_deleted
        # Отклонённая цель не должна ничего переносить: иначе фото осталось
        # бы на месте без части связей.
        assert link.photo == photo

    def test_deleted_target_is_rejected(self, admin_client_logged_in, photo):
        """Удалённое фото каноном быть не может: ссылка вела бы в никуда."""
        deleted = PhotoFactory(sport=photo.sport, deleted_at=timezone.now())
        url = reverse("admin:photos_photo_merge")

        admin_client_logged_in.post(
            url,
            {"ids": str(photo.pk), "target": str(deleted.pk)},
            follow=True,
        )

        photo.refresh_from_db()
        assert photo.merged_into is None

    def test_merged_target_is_rejected(self, admin_client_logged_in, photo):
        canonical = PhotoFactory(sport=photo.sport)
        merged = PhotoFactory(sport=photo.sport, merged_into=canonical)
        url = reverse("admin:photos_photo_merge")

        admin_client_logged_in.post(
            url,
            {"ids": str(photo.pk), "target": str(merged.pk)},
            follow=True,
        )

        photo.refresh_from_db()
        assert photo.merged_into is None

    def test_merge_moves_photo_and_logs(self, admin_client_logged_in, photo):
        target = PhotoFactory(sport=photo.sport)
        url = reverse("admin:photos_photo_merge")

        admin_client_logged_in.post(
            url,
            {"ids": str(photo.pk), "target": str(target.pk)},
            follow=True,
        )

        photo.refresh_from_db()
        assert photo.merged_into == target
        assert photo.is_deleted
        assert ModerationAction.objects.filter(
            object_id=photo.pk, action=ModerationActionType.MERGE
        ).exists()

    def test_merge_page_without_candidates_says_so(self, admin_client_logged_in, photo):
        url = reverse("admin:photos_photo_merge")

        response = admin_client_logged_in.get(url, {"ids": str(photo.pk)})

        assert response.status_code == 200
        assert "Нет подходящих объектов" in response.content.decode()
