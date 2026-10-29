"""Тесты ядра: абстрактные модели, Sport, healthcheck."""

import pytest
from django.urls import reverse

from apps.catalog.models import Team
from apps.core.models import Status

pytestmark = pytest.mark.django_db


class TestSport:
    def test_seeded_baseball_exists(self):
        from apps.core.models import Sport

        sport = Sport.objects.get(slug="baseball")
        assert sport.name == "Бейсбол"
        assert sport.is_active

    def test_str(self, baseball):
        assert str(baseball) == "Бейсбол"


class TestStatusModel:
    def test_team_starts_as_draft(self, team):
        assert team.status == Status.DRAFT
        assert not team.is_published

    def test_publish_sets_moderator_and_timestamp(self, team, moderator):
        team.publish(moderator=moderator, reason="проверено")

        team.refresh_from_db()
        assert team.status == Status.PUBLISHED
        assert team.is_published
        assert team.moderated_by == moderator
        assert team.moderated_at is not None
        assert team.moderation_reason == "проверено"

    def test_hide_and_reject(self, team, moderator):
        team.hide(moderator=moderator)
        assert team.status == Status.HIDDEN

        team.reject(moderator=moderator)
        assert team.status == Status.REJECTED


class TestSoftDelete:
    def test_soft_delete_hides_from_public_but_keeps_object(self, team):
        pk = team.pk
        team.soft_delete(reason="по требованию")

        assert Team.objects.filter(pk=pk).exists()
        assert not Team.public.filter(pk=pk).exists()
        assert team.is_deleted
        assert team.deleted_reason == "по требованию"

    def test_restore_returns_to_public(self, team):
        team.soft_delete()
        team.restore()

        assert Team.public.filter(pk=team.pk).exists()
        assert not team.is_deleted
        assert team.deleted_reason == ""

    def test_public_manager_excludes_deleted(self, team, baseball, user):
        Team.objects.create(sport=baseball, name="Вторая", created_by=user)
        team.soft_delete()

        assert Team.public.count() == 1


class TestMergeable:
    def test_merge_into_self_raises(self, team):
        with pytest.raises(ValueError, match="самим собой"):
            team.merge_into(team)

    def test_merge_marks_source_and_moves_memberships(self, team, person, user):
        from apps.catalog.models import Team, TeamMembership

        target = Team.objects.create(
            sport=team.sport, name="Динамо (канон)", created_by=user
        )
        membership = TeamMembership.objects.create(
            team=team, person=person, year_from=1994
        )

        team.merge_into(target)

        membership.refresh_from_db()
        team.refresh_from_db()
        assert membership.team == target
        assert team.merged_into == target
        assert team.is_deleted
        assert team.is_merged

    def test_merge_is_idempotent(self, team, baseball, user):
        from apps.catalog.models import Team

        target = Team.objects.create(sport=baseball, name="Канон", created_by=user)

        team.merge_into(target)
        team.merge_into(target)

        team.refresh_from_db()
        assert team.merged_into == target

    def test_merged_team_disappears_from_public(self, team, baseball, user):
        from apps.catalog.models import Team

        target = Team.objects.create(sport=baseball, name="Канон", created_by=user)
        team.merge_into(target)

        assert not Team.public.filter(pk=team.pk).exists()
        assert Team.public.filter(pk=target.pk).exists()


class TestCanonical:
    def test_get_canonical_follows_chain(self, team, baseball, user):
        from apps.catalog.models import Team

        middle = Team.objects.create(sport=baseball, name="Середина", created_by=user)
        canon = Team.objects.create(sport=baseball, name="Канон", created_by=user)
        team.merge_into(middle)
        middle.merge_into(canon)

        team.refresh_from_db()

        assert team.get_canonical() == canon
        assert canon.get_canonical() == canon

    def test_merge_into_resolves_target_to_canonical(
        self, team, baseball, user, person
    ):
        from apps.catalog.models import Team, TeamMembership

        middle = Team.objects.create(sport=baseball, name="Середина", created_by=user)
        canon = Team.objects.create(sport=baseball, name="Канон", created_by=user)
        middle.merge_into(canon)
        membership = TeamMembership.objects.create(
            team=team, person=person, year_from=1994
        )

        team.merge_into(middle)

        membership.refresh_from_db()
        team.refresh_from_db()
        assert membership.team == canon
        assert team.merged_into == canon

    def test_get_canonical_returns_none_on_cycle(self, team, baseball, user):
        from apps.catalog.models import Team

        other = Team.objects.create(sport=baseball, name="Второй", created_by=user)
        Team.objects.filter(pk=team.pk).update(merged_into_id=other.pk)
        Team.objects.filter(pk=other.pk).update(merged_into_id=team.pk)
        team.refresh_from_db()

        assert team.get_canonical() is None

    def test_get_canonical_stops_at_max_depth(self, team, baseball, user):
        from apps.catalog.models import Team

        current = team
        for index in range(12):
            nxt = Team.objects.create(
                sport=baseball, name=f"Звено {index}", created_by=user
            )
            current.merge_into(nxt)
            current = nxt

        team.refresh_from_db()

        assert team.get_canonical() is None

    def test_merge_into_rejects_broken_chain(self, team, baseball, user):
        from apps.catalog.models import Team

        other = Team.objects.create(sport=baseball, name="Второй", created_by=user)
        Team.objects.filter(pk=team.pk).update(merged_into_id=other.pk)
        Team.objects.filter(pk=other.pk).update(merged_into_id=team.pk)
        other.refresh_from_db()

        with pytest.raises(ValueError, match="битую"):
            team.merge_into(other)


class TestHealthz:
    def test_returns_ok(self, client):
        response = client.get(reverse("healthz"))

        assert response.status_code == 200
        assert response.json() == {"app": "ok", "db": "ok"}

    def test_rejects_post(self, client):
        assert client.post(reverse("healthz")).status_code == 405


class TestDefaultSport:
    """Вид спорта по умолчанию для объектов, создаваемых пользователем."""

    def test_returns_first_active_by_sort_order(self, baseball):
        from apps.core.models import Sport
        from apps.core.shortcuts import default_sport

        Sport.objects.create(name="Крикет", slug="cricket", sort_order=5)
        Sport.objects.create(name="Лапта", slug="lapta", sort_order=-1)

        assert default_sport().slug == "lapta"

    def test_inactive_sport_is_skipped(self, baseball):
        from apps.core.models import Sport
        from apps.core.shortcuts import default_sport

        baseball.is_active = False
        baseball.save(update_fields=["is_active"])
        Sport.objects.create(name="Крикет", slug="cricket", sort_order=5)

        assert default_sport().slug == "cricket"

    def test_returns_none_without_active_sports(self, baseball):
        from apps.core.shortcuts import default_sport

        baseball.is_active = False
        baseball.save(update_fields=["is_active"])

        assert default_sport() is None

    def test_returns_none_without_any_sports(self, db):
        from apps.core.models import Sport
        from apps.core.shortcuts import default_sport

        # Бейсбол создаётся data-миграцией, поэтому таблицу чистим явно.
        Sport.objects.all().delete()

        assert default_sport() is None
