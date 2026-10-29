"""Тесты хелпера `get_visible_or_404`.

Правило: неопубликованный объект отдаётся только автору и модератору,
остальным — 404. Удалённые и слитые объекты не видны никому.
Исключение — фото с `follow_merged=True`: слитое фото отдаёт 301 на канон.
"""

import pytest
from django.http import Http404
from django.urls import reverse

from apps.catalog.models import Event, Team
from apps.core.models import Status
from apps.core.shortcuts import RedirectToCanonical, get_visible_or_404
from apps.photos.models import Photo
from tests.factories import PhotoFactory

pytestmark = pytest.mark.django_db


class _Request:
    """Минимальная замена запроса: хелперу нужен только `request.user`."""

    def __init__(self, user=None):
        self.user = user


class _Anonymous:
    is_authenticated = False
    is_moderator = False


ANONYMOUS = _Request(_Anonymous())


class TestPublishedObject:
    def test_returns_published_team_to_anonymous(self, team):
        team.publish()

        found = get_visible_or_404(Team, request=ANONYMOUS, slug=team.slug)

        assert found == team

    def test_returns_published_team_without_request(self, team):
        team.publish()

        assert get_visible_or_404(Team, slug=team.slug) == team

    def test_missing_object_raises_404(self, team):
        with pytest.raises(Http404):
            get_visible_or_404(Team, request=ANONYMOUS, slug="нет-такой-команды")


class TestDraftObject:
    def test_draft_is_hidden_from_anonymous(self, team, user):
        assert team.status == Status.DRAFT

        with pytest.raises(Http404):
            get_visible_or_404(Team, request=ANONYMOUS, slug=team.slug)

    def test_draft_is_hidden_from_stranger(self, team, other_user):
        with pytest.raises(Http404):
            get_visible_or_404(Team, request=_Request(other_user), slug=team.slug)

    def test_draft_is_shown_to_author(self, team, user):
        found = get_visible_or_404(Team, request=_Request(user), slug=team.slug)

        assert found == team

    def test_draft_is_shown_to_moderator(self, team, moderator):
        found = get_visible_or_404(Team, request=_Request(moderator), slug=team.slug)

        assert found == team

    def test_hidden_and_rejected_are_hidden_from_anonymous(self, team, moderator):
        team.hide(moderator=moderator)
        with pytest.raises(Http404):
            get_visible_or_404(Team, request=ANONYMOUS, slug=team.slug)

        team.reject(moderator=moderator)
        with pytest.raises(Http404):
            get_visible_or_404(Team, request=ANONYMOUS, slug=team.slug)


class TestPhotoVisibility:
    def test_photo_without_rights_is_shown_to_anonymous(self, photo):
        """`rights_confirmed` — исторический флаг, видимость не меняет."""
        photo.rights_confirmed = False
        photo.save(update_fields=["rights_confirmed"])

        found = get_visible_or_404(Photo, request=ANONYMOUS, pk=photo.pk)

        assert found == photo

    def test_draft_photo_is_hidden_from_anonymous(self, photo):
        photo.status = Status.DRAFT
        photo.save(update_fields=["status"])

        with pytest.raises(Http404):
            get_visible_or_404(Photo, request=ANONYMOUS, pk=photo.pk)

    def test_draft_photo_is_shown_to_uploader(self, photo, user):
        photo.status = Status.DRAFT
        photo.save(update_fields=["status"])

        found = get_visible_or_404(Photo, request=_Request(user), pk=photo.pk)

        assert found == photo

    def test_draft_photo_is_shown_to_moderator(self, photo, moderator):
        photo.status = Status.DRAFT
        photo.save(update_fields=["status"])

        found = get_visible_or_404(Photo, request=_Request(moderator), pk=photo.pk)

        assert found == photo


class TestDeletedAndMerged:
    def test_deleted_object_is_hidden_even_from_moderator(self, team, moderator):
        team.publish()
        team.soft_delete()

        with pytest.raises(Http404):
            get_visible_or_404(Team, request=_Request(moderator), slug=team.slug)

    def test_merged_object_is_hidden_even_from_author(self, team, user, baseball):
        team.publish()
        target = Team.objects.create(sport=baseball, name="Канон", created_by=user)
        target.publish()

        team.merge_into(target)

        with pytest.raises(Http404):
            get_visible_or_404(Team, request=_Request(user), slug=team.slug)

    def test_deleted_photo_is_hidden_from_uploader(self, photo, user):
        photo.soft_delete()

        with pytest.raises(Http404):
            get_visible_or_404(Photo, request=_Request(user), pk=photo.pk)


class TestEventLookup:
    def test_works_with_other_models(self, event):
        event.publish()

        found = get_visible_or_404(Event, request=ANONYMOUS, slug=event.slug)

        assert found == event

    def test_draft_event_is_hidden(self, event):
        with pytest.raises(Http404):
            get_visible_or_404(Event, request=ANONYMOUS, slug=event.slug)


class TestFollowMerged:
    """Слитое фото отдаёт 301 на канон, а не 404: ссылку могли разослать."""

    def test_merged_photo_raises_redirect_to_canonical(self, photo, baseball):
        canonical = PhotoFactory(sport=baseball)
        photo.merge_into(canonical)

        with pytest.raises(RedirectToCanonical) as exc_info:
            get_visible_or_404(
                Photo, request=ANONYMOUS, follow_merged=True, pk=photo.pk
            )

        assert exc_info.value.url == canonical.get_absolute_url()

    def test_flag_off_keeps_404(self, photo, baseball):
        canonical = PhotoFactory(sport=baseball)
        photo.merge_into(canonical)

        with pytest.raises(Http404):
            get_visible_or_404(Photo, request=ANONYMOUS, pk=photo.pk)

    def test_chain_redirects_straight_to_final_canonical(self, photo, baseball):
        middle = PhotoFactory(sport=baseball)
        canon = PhotoFactory(sport=baseball)
        photo.merge_into(middle)
        middle.merge_into(canon)

        with pytest.raises(RedirectToCanonical) as exc_info:
            get_visible_or_404(
                Photo, request=ANONYMOUS, follow_merged=True, pk=photo.pk
            )

        assert exc_info.value.url == canon.get_absolute_url()

    def test_draft_canonical_gives_404(self, photo, baseball):
        canonical = PhotoFactory(sport=baseball, status=Status.DRAFT)
        photo.merge_into(canonical)

        with pytest.raises(Http404):
            get_visible_or_404(
                Photo, request=ANONYMOUS, follow_merged=True, pk=photo.pk
            )

    def test_deleted_canonical_gives_404(self, photo, baseball):
        canonical = PhotoFactory(sport=baseball)
        photo.merge_into(canonical)
        canonical.soft_delete()

        with pytest.raises(Http404):
            get_visible_or_404(
                Photo, request=ANONYMOUS, follow_merged=True, pk=photo.pk
            )

    def test_deleted_photo_is_not_redirected(self, photo, baseball):
        PhotoFactory(sport=baseball)
        photo.soft_delete()

        with pytest.raises(Http404):
            get_visible_or_404(
                Photo, request=ANONYMOUS, follow_merged=True, pk=photo.pk
            )

    def test_broken_chain_gives_404(self, photo, baseball):
        """Цикл слияний — битые данные, редиректить некуда.

        Слитое фото всегда ещё и удалено, поэтому цикл выставляется
        `update()`: `merge_into()` такую пару создать не даст.
        """
        other = PhotoFactory(sport=baseball)
        photo.soft_delete()
        Photo.objects.filter(pk=photo.pk).update(merged_into_id=other.pk)
        Photo.objects.filter(pk=other.pk).update(merged_into_id=photo.pk)

        with pytest.raises(Http404):
            get_visible_or_404(
                Photo, request=ANONYMOUS, follow_merged=True, pk=photo.pk
            )

    def test_missing_photo_gives_404(self, photo):
        with pytest.raises(Http404):
            get_visible_or_404(
                Photo, request=ANONYMOUS, follow_merged=True, pk=photo.pk + 1000
            )

    def test_merged_team_still_gives_404(self, team, baseball, user):
        """Без флага поведение команд не меняется."""
        team.publish()
        target = Team.objects.create(sport=baseball, name="Канон", created_by=user)
        target.publish()
        team.merge_into(target)

        with pytest.raises(Http404):
            get_visible_or_404(Team, request=ANONYMOUS, slug=team.slug)


class TestPhotoDetailRedirect:
    """Страница фото сама отдаёт 301 и не считает просмотр дубликату."""

    def test_merged_photo_page_redirects_permanently(self, client, photo, baseball):
        canonical = PhotoFactory(sport=baseball)
        photo.merge_into(canonical)

        response = client.get(reverse("photos:photo_detail", args=[photo.pk]))

        assert response.status_code == 301
        assert response.url == canonical.get_absolute_url()

    def test_merged_photo_gets_no_view_count(self, client, photo, baseball):
        """Редирект поднимается до счётчика: дубликату просмотр не идёт."""
        canonical = PhotoFactory(sport=baseball)
        photo.merge_into(canonical)

        client.get(reverse("photos:photo_detail", args=[photo.pk]))

        photo.refresh_from_db()
        canonical.refresh_from_db()
        assert photo.views_count == 0
        assert canonical.views_count == 0

    def test_view_count_goes_to_canonical(self, client, photo, baseball):
        canonical = PhotoFactory(sport=baseball)
        photo.merge_into(canonical)

        client.get(reverse("photos:photo_detail", args=[photo.pk]), follow=True)

        photo.refresh_from_db()
        canonical.refresh_from_db()
        assert canonical.views_count == 1
        assert photo.views_count == 0
