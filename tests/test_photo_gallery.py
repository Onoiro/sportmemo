"""Тесты галереи и страницы фото.

Проверяем поведение, а не вёрстку: кто видит фото, что отдаёт фрагмент,
растут ли просмотры. Разметка меняется чаще, чем смысл страницы.
"""

import pytest
from django.urls import reverse

from apps.catalog.models import NameVisibility
from apps.core.models import Status
from apps.core.shortcuts import PHOTO_PAGE_SIZE
from apps.photos.models import Photo, PhotoPerson
from tests.factories import PhotoFactory, SourceFactory
from tests.utils import make_image_file

pytestmark = pytest.mark.django_db


class TestPhotoAbsoluteUrl:
    def test_points_to_photo_detail(self, photo):
        assert photo.get_absolute_url() == reverse(
            "photos:photo_detail", args=[photo.pk]
        )


class TestGridFragment:
    def test_opens_anonymously(self, client, photo):
        response = client.get(reverse("photos:grid"))

        assert response.status_code == 200

    def test_is_a_fragment_without_html_tag(self, client, photo):
        html = client.get(reverse("photos:grid")).content.decode()

        assert "<html" not in html
        assert "<!DOCTYPE" not in html

    def test_shows_only_visible_photos(self, client):
        shown = PhotoFactory(title="Видимое фото")
        PhotoFactory(title="Черновик", status=Status.DRAFT)
        deleted = PhotoFactory(title="Удалённое фото")
        deleted.soft_delete()

        html = client.get(reverse("photos:grid")).content.decode()

        assert shown.title in html
        for title in ("Черновик", "Удалённое фото"):
            assert title not in html

    def test_photo_without_rights_is_shown(self, client):
        """Права проверяет автор: исторический флаг видимость не меняет."""
        photo = PhotoFactory(title="Без прав", rights_confirmed=False)

        html = client.get(reverse("photos:grid")).content.decode()

        assert photo.title in html

    def test_filters_by_year(self, client):
        shown = PhotoFactory(year=1994, title="Фото 1994")
        other = PhotoFactory(year=2003, title="Фото 2003")

        html = client.get(reverse("photos:grid"), {"year": 1994}).content.decode()

        assert shown.title in html
        assert other.title not in html

    def test_filters_by_person(self, client, person):
        linked = PhotoFactory(title="С Петром")
        PhotoPerson.objects.create(photo=linked, person=person)
        other = PhotoFactory(title="Без Петра")

        html = client.get(
            reverse("photos:grid"), {"person": person.slug}
        ).content.decode()

        assert linked.title in html
        assert other.title not in html

    def test_filters_by_team(self, client, team):
        shown = PhotoFactory(team=team, title="Фото команды")
        other = PhotoFactory(title="Без команды")

        html = client.get(reverse("photos:grid"), {"team": team.slug}).content.decode()

        assert shown.title in html
        assert other.title not in html

    def test_filters_by_event(self, client, event):
        shown = PhotoFactory(event=event, title="Фото события")
        other = PhotoFactory(title="Без события")

        html = client.get(
            reverse("photos:grid"), {"event": event.slug}
        ).content.decode()

        assert shown.title in html
        assert other.title not in html

    def test_pagination_splits_fragment(self, client):
        for _ in range(PHOTO_PAGE_SIZE + 1):
            PhotoFactory()

        first = client.get(reverse("photos:grid")).content.decode()
        second = client.get(reverse("photos:grid"), {"page": 2}).content.decode()

        assert first.count('class="photo-card"') == PHOTO_PAGE_SIZE
        assert second.count('class="photo-card"') == 1

    def test_last_page_has_no_more_button(self, client):
        PhotoFactory()

        html = client.get(reverse("photos:grid")).content.decode()

        assert "Показать ещё" not in html

    def test_first_page_has_more_button(self, client):
        for _ in range(PHOTO_PAGE_SIZE + 1):
            PhotoFactory()

        html = client.get(reverse("photos:grid")).content.decode()

        assert "Показать ещё" in html

    def test_more_container_is_swapped_out_of_band(self, client):
        for _ in range(PHOTO_PAGE_SIZE + 1):
            PhotoFactory()

        html = client.get(reverse("photos:grid")).content.decode()

        assert 'hx-swap-oob="outerHTML:#gallery-more"' in html

    def test_keeps_filters_in_more_link(self, client):
        for _ in range(PHOTO_PAGE_SIZE + 1):
            PhotoFactory(year=1994)

        html = client.get(reverse("photos:grid"), {"year": 1994}).content.decode()

        assert "year=1994" in html

    @pytest.mark.parametrize("value", ["abc", "999", "-1", ""])
    def test_broken_year_does_not_crash(self, client, photo, value):
        response = client.get(reverse("photos:grid"), {"year": value})

        assert response.status_code == 200

    def test_broken_year_gives_empty_result(self, client, photo):
        html = client.get(reverse("photos:grid"), {"year": "abc"}).content.decode()

        assert 'class="photo-card"' not in html

    @pytest.mark.parametrize("param", ["team", "event", "person"])
    def test_unknown_slug_gives_empty_result(self, client, photo, param):
        html = client.get(
            reverse("photos:grid"), {param: "нет-такого"}
        ).content.decode()

        assert 'class="photo-card"' not in html

    def test_rejects_post(self, client):
        assert client.post(reverse("photos:grid")).status_code == 405


class TestPhotoDetail:
    def test_opens_anonymously(self, client, photo):
        response = client.get(photo.get_absolute_url())

        assert response.status_code == 200

    def test_shows_title_caption_and_year(self, client, photo):
        photo.caption = "Финал первенства"
        photo.year = 1994
        photo.save()

        html = client.get(photo.get_absolute_url()).content.decode()

        assert photo.title in html
        assert "Финал первенства" in html
        assert "1994" in html

    def test_shows_source_and_people(self, client, photo, person):
        source = SourceFactory(publication="Советский спорт")
        photo.source = source
        photo.save()
        PhotoPerson.objects.create(photo=photo, person=person, role="coach")

        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Советский спорт" in html
        assert person.public_name in html

    def test_photo_without_metadata_opens(self, client, baseball, user):
        minimal = Photo.objects.create(
            sport=baseball,
            original=make_image_file(),
            uploaded_by=user,
            rights_confirmed=True,
            status=Status.PUBLISHED,
        )

        response = client.get(minimal.get_absolute_url())

        assert response.status_code == 200

    def test_draft_is_404_for_anonymous(self, client, photo):
        photo.status = Status.DRAFT
        photo.save()

        assert client.get(photo.get_absolute_url()).status_code == 404

    def test_draft_is_404_for_stranger(self, client, photo, other_user):
        photo.status = Status.DRAFT
        photo.save()
        client.force_login(other_user)

        assert client.get(photo.get_absolute_url()).status_code == 404

    def test_draft_is_shown_to_uploader(self, client, photo, user):
        photo.status = Status.DRAFT
        photo.save()
        client.force_login(user)

        assert client.get(photo.get_absolute_url()).status_code == 200

    def test_draft_is_shown_to_moderator(self, client, photo, moderator):
        photo.status = Status.DRAFT
        photo.save()
        client.force_login(moderator)

        assert client.get(photo.get_absolute_url()).status_code == 200

    def test_photo_without_rights_opens_anonymously(self, client, photo):
        """Исторический флаг видимость не меняет: фото опубликовано."""
        photo.rights_confirmed = False
        photo.save()

        assert client.get(photo.get_absolute_url()).status_code == 200

    def test_photo_without_display_shows_placeholder(self, client, photo):
        """Фото без `display` не падает: превью нет — картинка-заглушка."""
        photo.display.delete(save=True)

        response = client.get(photo.get_absolute_url())
        html = response.content.decode()

        assert response.status_code == 200
        assert "og-default.png" in html
        assert "photos/original/" not in html

    def test_deleted_is_404_for_uploader(self, client, photo, user):
        photo.soft_delete()
        client.force_login(user)

        assert client.get(photo.get_absolute_url()).status_code == 404

    def test_unknown_photo_is_404(self, client):
        url = reverse("photos:photo_detail", args=[99999])

        assert client.get(url).status_code == 404

    def test_rejects_post(self, client, photo):
        assert client.post(photo.get_absolute_url()).status_code == 405

    def test_breadcrumbs_include_year(self, client, photo):
        photo.year = 1994
        photo.save()

        html = client.get(photo.get_absolute_url()).content.decode()

        assert reverse("core:year_detail", args=[1994]) in html

    def test_hidden_person_name_is_not_shown(self, client, photo, person):
        person.last_name = "Секретная"
        person.name_visibility = NameVisibility.HIDDEN
        person.save()
        PhotoPerson.objects.create(photo=photo, person=person)

        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Секретная" not in html

    def test_published_person_name_links_to_person_page(self, client, photo, person):
        person.publish()
        PhotoPerson.objects.create(photo=photo, person=person)

        html = client.get(photo.get_absolute_url()).content.decode()

        assert person.get_absolute_url() in html

    def test_draft_person_name_is_shown_without_link(self, client, photo, person):
        """Имя черновика на фото уже показано: ссылки на страницу нет."""
        PhotoPerson.objects.create(photo=photo, person=person)

        html = client.get(photo.get_absolute_url()).content.decode()

        assert person.public_name in html
        assert person.get_absolute_url() not in html


class TestPhotoViewsCounter:
    def test_views_grow_by_one_per_open(self, client, photo):
        client.get(photo.get_absolute_url())
        client.get(photo.get_absolute_url())

        photo.refresh_from_db()
        assert photo.views_count == 2

    def test_views_do_not_create_history_records(self, client, photo):
        before = photo.history.count()

        client.get(photo.get_absolute_url())

        assert photo.history.count() == before

    def test_views_are_shown_on_the_page(self, client, photo):
        photo.views_count = 7
        photo.save()

        html = client.get(photo.get_absolute_url()).content.decode()

        assert "8" in html


class TestGalleryOnPages:
    def test_home_uses_gallery_component(self, client, photo):
        html = client.get(reverse("core:home")).content.decode()

        assert 'id="photo-grid"' in html
        assert 'id="photo-gallery"' in html

    def test_home_cards_link_to_photo(self, client, photo):
        html = client.get(reverse("core:home")).content.decode()

        assert photo.get_absolute_url() in html

    def test_home_has_no_more_button(self, client, photo):
        html = client.get(reverse("core:home")).content.decode()

        assert "Показать ещё" not in html

    def test_year_page_uses_gallery_component(self, client):
        PhotoFactory(year=1994)

        html = client.get(reverse("core:year_detail", args=[1994])).content.decode()

        assert 'id="photo-grid"' in html

    def test_year_page_cards_link_to_photo(self, client):
        photo = PhotoFactory(year=1994)

        html = client.get(reverse("core:year_detail", args=[1994])).content.decode()

        assert photo.get_absolute_url() in html

    def test_year_page_more_button_has_fallback_link(self, client):
        for _ in range(PHOTO_PAGE_SIZE + 1):
            PhotoFactory(year=1994)

        html = client.get(reverse("core:year_detail", args=[1994])).content.decode()

        assert 'href="?page=2"' in html

    def test_year_page_more_button_targets_fragment(self, client):
        for _ in range(PHOTO_PAGE_SIZE + 1):
            PhotoFactory(year=1994)

        html = client.get(reverse("core:year_detail", args=[1994])).content.decode()

        assert reverse("photos:grid") + "?year=1994" in html

    def test_year_page_has_no_pagination_block(self, client):
        for _ in range(PHOTO_PAGE_SIZE + 1):
            PhotoFactory(year=1994)

        html = client.get(reverse("core:year_detail", args=[1994])).content.decode()

        assert "Постраничная навигация" not in html

    def test_year_page_keeps_page_obj_in_context(self, client):
        PhotoFactory(year=1994)

        response = client.get(reverse("core:year_detail", args=[1994]))

        assert response.context["page_obj"].paginator.count == 1

    def test_year_page_shows_single_page_of_photos(self, client):
        for _ in range(PHOTO_PAGE_SIZE + 1):
            PhotoFactory(year=1994)

        html = client.get(reverse("core:year_detail", args=[1994])).content.decode()

        assert html.count('class="photo-card"') == PHOTO_PAGE_SIZE

    def test_second_page_opens_as_plain_page_without_js(self, client):
        for _ in range(PHOTO_PAGE_SIZE + 1):
            PhotoFactory(year=1994)

        response = client.get(reverse("core:year_detail", args=[1994]), {"page": 2})
        html = response.content.decode()

        assert response.status_code == 200
        assert html.count('class="photo-card"') == 1
