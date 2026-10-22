"""Тесты публичных выборок: `published()` и хелперы.

Правило проекта: наружу отдаём только опубликованное и не удалённое.
Права на фото проверяет автор, а не сервер (решение 2.1): `rights_confirmed`
на видимость не влияет.
"""

import pytest

from apps.catalog.models import Team
from apps.core.models import Status
from apps.core.shortcuts import archive_stats, visible_photos
from apps.photos.models import Photo
from tests.factories import PhotoFactory, TeamFactory

pytestmark = pytest.mark.django_db


class TestPublished:
    def test_keeps_only_published_teams(self, baseball, user):
        published = TeamFactory(status=Status.PUBLISHED)
        TeamFactory(status=Status.DRAFT)
        TeamFactory(status=Status.HIDDEN)
        TeamFactory(status=Status.REJECTED)

        found = Team.public.published()

        assert list(found) == [published]

    def test_draft_is_not_published(self, team):
        assert team.status == Status.DRAFT
        assert not Team.public.published().filter(pk=team.pk).exists()

    def test_deleted_published_team_is_not_in_public(self, team):
        team.publish()
        team.soft_delete()

        assert not Team.public.published().filter(pk=team.pk).exists()
        assert Team.objects.published().filter(pk=team.pk).exists()

    def test_merged_team_is_not_in_public(self, team, baseball, user):
        target = Team.objects.create(sport=baseball, name="Канон", created_by=user)
        target.publish()
        team.publish()

        team.merge_into(target)

        found = Team.public.published()
        assert list(found) == [target]

    def test_works_for_photos_too(self, photo):
        assert Photo.public.published().filter(pk=photo.pk).exists()


class TestPhotoVisibility:
    def test_photo_without_rights_is_visible(self, photo):
        """Исторический флаг не влияет: фото опубликовано — значит видно."""
        without_rights = PhotoFactory(rights_confirmed=False)

        found = Photo.public.published()

        assert set(found) == {photo, without_rights}

    def test_draft_photo_is_not_visible(self, photo):
        photo.status = Status.DRAFT
        photo.save(update_fields=["status"])

        assert not Photo.public.published().filter(pk=photo.pk).exists()

    def test_hidden_photo_is_not_visible(self, photo):
        photo.hide()

        assert not Photo.public.published().filter(pk=photo.pk).exists()

    def test_deleted_photo_is_not_visible(self, photo):
        photo.soft_delete()

        assert not Photo.public.published().filter(pk=photo.pk).exists()


class TestShortcutQueries:
    def test_visible_photos_is_the_public_chain(self, photo):
        PhotoFactory(status=Status.DRAFT)

        assert list(visible_photos()) == [photo]

    def test_visible_photos_keeps_photo_without_rights(self, photo):
        without_rights = PhotoFactory(rights_confirmed=False)

        assert set(visible_photos()) == {photo, without_rights}

    def test_archive_stats_count_only_public_objects(self, baseball, user, photo):
        TeamFactory(sport=baseball, created_by=user, status=Status.PUBLISHED)
        TeamFactory(sport=baseball, created_by=user, status=Status.DRAFT)
        PhotoFactory(year=1994, status=Status.DRAFT)

        stats = archive_stats()

        assert stats["teams"] == 1
        assert stats["photos"] == 1
        assert stats["years"] == 0

    def test_archive_stats_years_match_year_list_source(self, photo):
        photo.year = 1994
        photo.save(update_fields=["year"])

        assert (
            archive_stats()["years"]
            == visible_photos()
            .exclude(year__isnull=True)
            .values("year")
            .distinct()
            .count()
        )
