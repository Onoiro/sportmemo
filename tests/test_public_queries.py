"""Тесты публичных выборок: `published()` и `visible()`.

Правило проекта: наружу отдаём только опубликованное, без удалённых
и слитых объектов, а фото — только с подтверждёнными правами.
"""

import pytest

from apps.catalog.models import Team
from apps.core.models import Status
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


class TestVisible:
    def test_keeps_only_photos_with_confirmed_rights(self, photo):
        without_rights = PhotoFactory(rights_confirmed=False)

        found = Photo.public.published().visible()

        assert list(found) == [photo]
        assert not found.filter(pk=without_rights.pk).exists()

    def test_draft_photo_with_rights_is_not_visible(self, photo):
        photo.status = Status.DRAFT
        photo.save(update_fields=["status"])

        assert not Photo.public.published().visible().filter(pk=photo.pk).exists()

    def test_hidden_photo_with_rights_is_not_visible(self, photo):
        photo.hide()

        assert not Photo.public.published().visible().filter(pk=photo.pk).exists()

    def test_deleted_photo_with_rights_is_not_visible(self, photo):
        photo.soft_delete()

        assert not Photo.public.published().visible().filter(pk=photo.pk).exists()
