"""Тесты дубликатов: похожие фото, слияние, выдача, админка."""

from datetime import date

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.urls import reverse
from PIL import Image

from apps.catalog.models import Team
from apps.core.models import Sport, Status
from apps.core.shortcuts import photos_count
from apps.moderation.models import ModerationAction, ModerationActionType
from apps.photos import similar
from apps.photos.models import Photo, PhotoPerson
from apps.photos.processing import PHASH_HEX_LENGTH, hamming_distance
from apps.photos.processing import perceptual_hash as image_hash
from apps.photos.similar import (
    LIKELY_DUPLICATE_DISTANCE,
    SIMILAR_DISTANCE_MAX,
    SIMILAR_LIMIT,
    SimilarPhoto,
    candidate_pool,
    similar_photos,
    similarity_label,
)
from tests.factories import (
    CommentFactory,
    EventFactory,
    ModerationActionFactory,
    PersonFactory,
    PhotoFactory,
    PhotoPersonFactory,
    ReportFactory,
    SourceFactory,
    TeamFactory,
)

pytestmark = pytest.mark.django_db


def _noise(seed, size=(256, 256)) -> Image.Image:
    """Псевдослучайная картинка: у разных seed — разный узор."""
    import numpy as np

    rng = np.random.default_rng(seed)
    data = rng.integers(0, 256, size=(size[1], size[0], 3), dtype=np.uint8)
    return Image.fromarray(data, mode="RGB")


def _hash(image) -> str:
    return image_hash(image)


def _flipped(base: str, bits: int) -> str:
    """Тот же хеш с перевёрнутыми младшими битами.

    Точное расстояние нужно там, где важны границы порогов: подобрать
    настоящие картинки с расстоянием ровно 33 или 49 нельзя.
    """
    value = int(base, 16) ^ ((1 << bits) - 1)
    return f"{value:0{PHASH_HEX_LENGTH}x}"


def _photo(sport, phash="", **fields) -> Photo:
    """Фото без файлов: проверяется отбор, а не пайплайн."""
    return Photo.objects.create(
        sport=sport, original="", phash=phash, title="Фото", **fields
    )


@pytest.fixture
def source_photo(baseball):
    return _photo(baseball, _hash(_noise(1)), year=1994)


class TestSimilarPhotos:
    def test_same_image_in_other_scale_is_found(self, baseball, source_photo):
        """Тот же снимок в другом масштабе — кандидат с малым расстоянием."""
        scaled = _noise(1).resize((1000, 1000), Image.Resampling.LANCZOS)
        candidate = _photo(baseball, _hash(scaled), year=1994)

        result = similar_photos(source_photo)

        assert [item.photo.pk for item in result] == [candidate.pk]
        assert result[0].distance <= LIKELY_DUPLICATE_DISTANCE

    def test_different_photo_is_not_found(self, baseball, source_photo):
        _photo(baseball, _hash(_noise(2)), year=1994)

        assert similar_photos(source_photo) == []

    def test_photo_of_other_sport_is_not_found(self, source_photo):
        other = Sport.objects.create(name="Хоккей", slug="hockey")
        _photo(other, source_photo.phash, year=1994)

        assert similar_photos(source_photo) == []

    def test_deleted_photo_is_not_found(self, baseball, source_photo):
        from django.utils import timezone

        _photo(
            baseball,
            source_photo.phash,
            year=1994,
            deleted_at=timezone.now(),
        )

        assert similar_photos(source_photo) == []

    def test_merged_photo_is_not_a_candidate(self, baseball, source_photo):
        """Слитое фото поглощено: предлагать его снова — тянуть по кругу."""
        canon = _photo(baseball, source_photo.phash, year=1994)
        merged = _photo(baseball, source_photo.phash, year=1994)
        merged.merged_into = canon
        merged.save()

        pool = candidate_pool(source_photo)

        assert [item.pk for item in pool] == [canon.pk]

    def test_source_photo_is_excluded(self, baseball, source_photo):
        pool = candidate_pool(source_photo)

        assert source_photo.pk not in [item.pk for item in pool]

    def test_photo_without_hash_is_not_a_candidate(self, baseball, source_photo):
        _photo(baseball, "", year=1994)

        assert similar_photos(source_photo) == []

    def test_old_short_hash_is_not_a_candidate(self, baseball, source_photo):
        """16-символьный хеш старого алгоритма с 256-битным несравним."""
        _photo(baseball, "9191cece3b393686", year=1994)

        assert similar_photos(source_photo) == []

    def test_candidate_without_year_is_found(self, baseball, source_photo):
        candidate = _photo(baseball, source_photo.phash)

        result = similar_photos(source_photo)

        assert [item.photo.pk for item in result] == [candidate.pk]

    def test_candidate_outside_year_window_is_not_found(self, baseball, source_photo):
        _photo(baseball, source_photo.phash, year=2005)

        assert similar_photos(source_photo) == []

    def test_photo_without_hash_gives_empty_list(self, baseball):
        photo = _photo(baseball, "")

        assert similar_photos(photo) == []

    def test_photo_with_old_short_hash_gives_empty_list(self, baseball):
        photo = _photo(baseball, "9191cece3b393686")

        assert similar_photos(photo) == []

    def test_result_is_sorted_by_distance(self, baseball):
        source = _photo(baseball, "f" * PHASH_HEX_LENGTH)
        far = _photo(baseball, _flipped(source.phash, 40))
        near = _photo(baseball, _flipped(source.phash, 5))
        middle = _photo(baseball, _flipped(source.phash, 20))

        result = similar_photos(source)

        assert [item.photo.pk for item in result] == [
            near.pk,
            middle.pk,
            far.pk,
        ]

    def test_result_is_limited(self, baseball):
        source = _photo(baseball, "f" * PHASH_HEX_LENGTH)
        for _ in range(SIMILAR_LIMIT + 5):
            _photo(baseball, source.phash)

        assert len(similar_photos(source)) == SIMILAR_LIMIT

    def test_pool_is_limited(self, baseball, monkeypatch):
        """Пул не тянет весь архив: сверх предела фото не проверяются."""
        monkeypatch.setattr(similar, "CANDIDATE_POOL_LIMIT", 2)
        source = _photo(baseball, "f" * PHASH_HEX_LENGTH)
        for _ in range(4):
            _photo(baseball, source.phash)

        # Строка сверх предела нужна, чтобы отличить «ровно предел»
        # от «урезано»: по ней `similar_photos()` пишет предупреждение.
        assert len(candidate_pool(source)) == 3

    def test_limited_pool_is_reported_in_log(self, baseball, monkeypatch, caplog):
        monkeypatch.setattr(similar, "CANDIDATE_POOL_LIMIT", 2)
        source = _photo(baseball, "f" * PHASH_HEX_LENGTH)
        for _ in range(4):
            _photo(baseball, source.phash)

        with caplog.at_level("WARNING"):
            similar_photos(source)

        assert "урезан" in caplog.text

    def test_single_query_for_pool(
        self, baseball, source_photo, django_assert_num_queries
    ):
        """Команда и фото кандидата не должны давать запрос на строку."""
        team = TeamFactory(sport=baseball)
        for _ in range(3):
            _photo(baseball, source_photo.phash, team=team, year=1994)

        with django_assert_num_queries(1):
            result = similar_photos(source_photo)
            for item in result:
                str(item.photo.team)

        assert len(result) == 3

    def test_no_query_for_photo_without_hash(self, baseball, django_assert_num_queries):
        photo = _photo(baseball, "")

        with django_assert_num_queries(0):
            similar_photos(photo)


class TestSimilarityLabel:
    @pytest.mark.parametrize(
        ("distance", "expected"),
        [
            (0, "вероятный дубликат"),
            (LIKELY_DUPLICATE_DISTANCE, "вероятный дубликат"),
            (LIKELY_DUPLICATE_DISTANCE + 1, "похоже"),
            (SIMILAR_DISTANCE_MAX, "похоже"),
            (SIMILAR_DISTANCE_MAX + 1, ""),
        ],
    )
    def test_labels_on_boundaries(self, distance, expected):
        assert similarity_label(distance) == expected

    def test_similar_photo_label_and_flag(self, baseball):
        photo = PhotoFactory(sport=baseball)

        duplicate = SimilarPhoto(photo, LIKELY_DUPLICATE_DISTANCE)
        similar = SimilarPhoto(photo, SIMILAR_DISTANCE_MAX)

        assert duplicate.is_likely_duplicate
        assert duplicate.label == "вероятный дубликат"
        assert not similar.is_likely_duplicate
        assert similar.label == "похоже"


class TestDistanceSanity:
    def test_flipped_hash_has_expected_distance(self):
        base = "f" * PHASH_HEX_LENGTH

        assert hamming_distance(base, _flipped(base, 33)) == 33

    def test_published_and_draft_are_both_candidates(self, baseball, source_photo):
        """Публичность — про выдачу, а не про модерацию: черновик
        тоже может быть дубликатом."""
        draft = _photo(baseball, source_photo.phash, status=Status.DRAFT)
        published = _photo(baseball, source_photo.phash, status=Status.PUBLISHED)

        found = {item.photo.pk for item in similar_photos(source_photo)}

        assert found == {draft.pk, published.pk}


def _similar_url(photo):
    return reverse("admin:photos_photo_similar", args=[photo.pk])


@pytest.fixture
def admin_logged_in(client, admin_user):
    client.force_login(admin_user)
    return client


class TestSimilarPhotoAdminPage:
    """Страница «Похожие фото»: показывает и ничего не меняет."""

    def test_page_lists_candidate_with_distance(self, admin_logged_in, baseball):
        source = PhotoFactory(sport=baseball, phash="f" * PHASH_HEX_LENGTH, year=1994)
        candidate = PhotoFactory(
            sport=baseball, phash=_flipped(source.phash, 10), year=1994
        )

        response = admin_logged_in.get(_similar_url(source))

        assert response.status_code == 200
        assert [item.photo.pk for item in response.context["similar"]] == [candidate.pk]
        assert response.context["similar"][0].distance == 10
        assert candidate.title in response.content.decode()

    def test_user_without_permission_is_redirected(self, client, user, baseball):
        source = PhotoFactory(sport=baseball, phash="f" * PHASH_HEX_LENGTH)
        client.force_login(user)

        response = client.get(_similar_url(source))

        assert response.status_code == 302
        assert reverse("admin:login") in response.url

    def test_photo_without_hash_shows_note(self, admin_logged_in, baseball):
        photo = PhotoFactory(sport=baseball, phash="")

        response = admin_logged_in.get(_similar_url(photo))

        assert response.status_code == 200
        assert response.context["hash_missing"]
        assert "Хеш ещё не посчитан" in response.content.decode()

    def test_empty_result_shows_note(self, admin_logged_in, baseball):
        photo = PhotoFactory(sport=baseball, phash="f" * PHASH_HEX_LENGTH)

        response = admin_logged_in.get(_similar_url(photo))

        assert response.status_code == 200
        assert "Похожих не найдено" in response.content.decode()

    def test_change_page_links_to_similar_page(self, admin_logged_in, baseball):
        photo = PhotoFactory(sport=baseball, phash="f" * PHASH_HEX_LENGTH)

        response = admin_logged_in.get(
            reverse("admin:photos_photo_change", args=[photo.pk])
        )

        assert _similar_url(photo) in response.content.decode()

    def test_add_page_works(self, admin_logged_in):
        response = admin_logged_in.get(reverse("admin:photos_photo_add"))

        assert response.status_code == 200

    def test_missing_photo_gives_404(self, admin_logged_in, baseball):
        photo = PhotoFactory(sport=baseball)

        response = admin_logged_in.get(
            reverse("admin:photos_photo_similar", args=[photo.pk + 1000])
        )

        assert response.status_code == 404

    def test_only_thumbnail_is_shown_not_original(self, admin_logged_in, baseball):
        """Оригинал не отдаётся никому: на странице только превью."""
        source = PhotoFactory(sport=baseball, phash="f" * PHASH_HEX_LENGTH)
        candidate = PhotoFactory(sport=baseball, phash=source.phash)

        content = admin_logged_in.get(_similar_url(source)).content.decode()

        assert candidate.thumbnail.url in content
        assert candidate.original.url not in content


def _photo_content_type():
    return ContentType.objects.get_for_model(Photo)


class TestPhotoMergeRelations:
    """Что переезжает в канон, а что остаётся у слитого фото (решение 2.1)."""

    def test_photo_persons_move_to_canonical(self, baseball):
        canon = PhotoFactory(sport=baseball)
        source = PhotoFactory(sport=baseball)
        first = PhotoPersonFactory(photo=source)
        second = PhotoPersonFactory(photo=source)

        source.merge_into(canon)

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.photo == canon
        assert second.photo == canon

    def test_person_already_on_canonical_is_not_duplicated(self, baseball):
        """`UniqueConstraint` иначе уронил бы перенос целиком."""
        person = PersonFactory()
        canon = PhotoFactory(sport=baseball)
        source = PhotoFactory(sport=baseball)
        PhotoPersonFactory(photo=canon, person=person, note="канон")
        PhotoPersonFactory(photo=source, person=person, note="дубликат")

        source.merge_into(canon)

        links = PhotoPerson.objects.filter(photo=canon, person=person)
        assert links.count() == 1
        assert links.get().note == "канон"

    def test_comments_move_to_canonical(self, baseball):
        canon = PhotoFactory(sport=baseball)
        source = PhotoFactory(sport=baseball)
        comment = CommentFactory(
            content_type=_photo_content_type(), object_id=source.pk
        )

        source.merge_into(canon)

        comment.refresh_from_db()
        assert comment.object_id == canon.pk

    def test_reports_move_to_canonical(self, baseball):
        """Жалоба «это я» должна остаться в карточке канона."""
        canon = PhotoFactory(sport=baseball)
        source = PhotoFactory(sport=baseball)
        report = ReportFactory(content_type=_photo_content_type(), object_id=source.pk)

        source.merge_into(canon)

        report.refresh_from_db()
        assert report.object_id == canon.pk

    def test_moderation_journal_stays_with_merged_photo(self, baseball):
        """Журнал отвечает «что сделали с #N», его не переписываем."""
        canon = PhotoFactory(sport=baseball)
        source = PhotoFactory(sport=baseball)
        action = ModerationActionFactory(
            content_type=_photo_content_type(), object_id=source.pk
        )

        source.merge_into(canon)

        action.refresh_from_db()
        assert action.object_id == source.pk

    def test_views_count_is_summed(self, baseball):
        canon = PhotoFactory(sport=baseball, views_count=7)
        source = PhotoFactory(sport=baseball, views_count=5)

        source.merge_into(canon)

        canon.refresh_from_db()
        assert canon.views_count == 12

    def test_person_avatar_moves_to_canonical(self, baseball):
        """Иначе главное фото человека стало бы битой ссылкой."""
        canon = PhotoFactory(sport=baseball)
        source = PhotoFactory(sport=baseball)
        person = PersonFactory(avatar=source)

        source.merge_into(canon)

        person.refresh_from_db()
        assert person.avatar == canon

    def test_empty_canonical_fields_are_filled(self, baseball, user):
        canon = PhotoFactory(sport=baseball)
        source = PhotoFactory(sport=baseball)
        source.source = SourceFactory()
        source.year = 1994
        source.taken_date = date(1994, 5, 1)
        source.team = TeamFactory(sport=baseball)
        source.event = EventFactory(sport=baseball)
        source.save()

        source.merge_into(canon)

        canon.refresh_from_db()
        assert canon.source == source.source
        assert canon.year == 1994
        assert canon.taken_date == date(1994, 5, 1)
        assert canon.team == source.team
        assert canon.event == source.event

    def test_filled_canonical_fields_are_not_overwritten(self, baseball):
        canon_team = TeamFactory(sport=baseball)
        canon = PhotoFactory(sport=baseball, year=1980, team=canon_team)
        source = PhotoFactory(
            sport=baseball, year=1994, team=TeamFactory(sport=baseball)
        )

        source.merge_into(canon)

        canon.refresh_from_db()
        assert canon.year == 1980
        assert canon.team == canon_team

    def test_canonical_text_is_kept(self, baseball):
        canon = PhotoFactory(sport=baseball, title="Канон", caption="Подпись канона")
        source = PhotoFactory(sport=baseball, title="Дубль", caption="Подпись дубля")

        source.merge_into(canon)

        canon.refresh_from_db()
        assert canon.title == "Канон"
        assert canon.caption == "Подпись канона"

    def test_derived_fields_are_not_moved(self, baseball):
        """`display` и хеш описывают свой оригинал, подменять их нельзя."""
        canon = PhotoFactory(sport=baseball, phash="", crop_data={})
        source = PhotoFactory(sport=baseball, phash="f" * PHASH_HEX_LENGTH)
        source.crop_data = {"version": 1, "x": 0, "y": 0, "width": 100, "height": 100}
        source.save()

        source.merge_into(canon)

        canon.refresh_from_db()
        assert canon.phash == ""
        assert canon.crop_data == {}

    def test_merged_photo_is_marked_as_duplicate(self, baseball):
        canon = PhotoFactory(sport=baseball)
        source = PhotoFactory(sport=baseball)

        source.merge_into(canon)

        source.refresh_from_db()
        assert source.merged_into == canon
        assert source.is_deleted
        assert source.deleted_reason == "слито с дубликатом"

    def test_merge_into_merged_target_lands_on_canonical(self, baseball):
        canon = PhotoFactory(sport=baseball)
        middle = PhotoFactory(sport=baseball)
        source = PhotoFactory(sport=baseball)
        middle.merge_into(canon)
        comment = CommentFactory(
            content_type=_photo_content_type(), object_id=source.pk
        )

        source.merge_into(middle)

        source.refresh_from_db()
        comment.refresh_from_db()
        assert source.merged_into == canon
        assert comment.object_id == canon.pk


class TestMergedPhotoDisappears:
    """Слитое фото уходит из всех публичных выборок сразу и целиком."""

    def test_not_in_gallery(self, client, baseball):
        canon = PhotoFactory(sport=baseball, title="Канон")
        source = PhotoFactory(sport=baseball, title="Дубликат")
        source.merge_into(canon)

        html = client.get(reverse("photos:grid")).content.decode()

        assert "Канон" in html
        assert "Дубликат" not in html

    def test_not_in_sitemap(self, client, baseball):
        canon = PhotoFactory(sport=baseball)
        source = PhotoFactory(sport=baseball)
        source.merge_into(canon)

        content = client.get("/sitemap-photos.xml").content.decode()

        assert canon.get_absolute_url() in content
        assert source.get_absolute_url() not in content

    def test_not_counted_in_photos_count(self, baseball):
        team = TeamFactory(sport=baseball)
        canon = PhotoFactory(sport=baseball, team=team)
        source = PhotoFactory(sport=baseball, team=team)
        source.merge_into(canon)

        counted = (
            Team.objects.filter(pk=team.pk)
            .annotate(photos_total=photos_count())
            .get()
            .photos_total
        )

        assert counted == 1

    @pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="Поиск работает только на PostgreSQL (tsvector, pg_trgm)",
    )
    def test_not_in_search(self, client, baseball):
        canon = PhotoFactory(sport=baseball, title="Громовский финал")
        source = PhotoFactory(sport=baseball, title="Громовский финал (дубль)")
        source.merge_into(canon)

        response = client.get(reverse("search:index"), {"q": "громовский"})

        photos = next(
            item for item in response.context["result"].groups if item.key == "photos"
        )
        assert [item.pk for item in photos.items] == [canon.pk]

    def test_cabinet_shows_it_as_deleted_with_reason(self, client, user, baseball):
        """Слитое фото не исчезает из кабинета молча: автор видит причину."""
        canon = PhotoFactory(sport=baseball, uploaded_by=user)
        source = PhotoFactory(sport=baseball, uploaded_by=user, title="Дубликат")
        source.merge_into(canon)
        client.force_login(user)

        html = client.get(reverse("photos:my_photos")).content.decode()

        assert "Дубликат" in html
        assert "Удалено" in html
        assert "слито с дубликатом" in html


class TestSimilarPhotoMergeButton:
    """Кнопка «Слить в это фото»: кандидат уходит в открытое фото."""

    def test_each_candidate_has_merge_form(self, admin_logged_in, baseball):
        source = PhotoFactory(sport=baseball, phash="f" * PHASH_HEX_LENGTH, year=1994)
        first = PhotoFactory(sport=baseball, phash=_flipped(source.phash, 5), year=1994)
        second = PhotoFactory(
            sport=baseball, phash=_flipped(source.phash, 10), year=1994
        )

        content = admin_logged_in.get(_similar_url(source)).content.decode()

        merge_url = reverse("admin:photos_photo_merge")
        assert content.count(f'action="{merge_url}"') == 2
        for candidate in (first, second):
            assert f'name="ids" value="{candidate.pk}"' in content
        assert content.count(f'name="target" value="{source.pk}"') == 2
        assert "Слить в это фото" in content

    def test_merge_form_is_post_with_csrf(self, admin_logged_in, baseball):
        source = PhotoFactory(sport=baseball, phash="f" * PHASH_HEX_LENGTH, year=1994)
        PhotoFactory(sport=baseball, phash=source.phash, year=1994)

        content = admin_logged_in.get(_similar_url(source)).content.decode()

        assert 'method="post"' in content
        assert "csrfmiddlewaretoken" in content

    def test_post_merges_candidate_into_current_photo(self, admin_logged_in, baseball):
        source = PhotoFactory(sport=baseball, phash="f" * PHASH_HEX_LENGTH, year=1994)
        candidate = PhotoFactory(sport=baseball, phash=source.phash, year=1994)

        response = admin_logged_in.post(
            reverse("admin:photos_photo_merge"),
            {"ids": str(candidate.pk), "target": str(source.pk)},
        )

        candidate.refresh_from_db()
        assert candidate.merged_into == source
        assert candidate.is_deleted
        assert response.status_code == 302
        # Админка отдаёт относительный адрес (`../<pk>/change/`):
        # важно, что он ведёт на карточку канона, а не на слитое фото.
        assert response.url.endswith(f"{source.pk}/change/")


class TestPhotoMergeThroughAdmin:
    """Слияние из админки: то же, что кнопкой, но с проверками формы."""

    def test_merge_moves_relations_and_logs(
        self, admin_logged_in, admin_user, baseball
    ):
        canon = PhotoFactory(sport=baseball)
        source = PhotoFactory(sport=baseball)
        link = PhotoPersonFactory(photo=source)
        comment = CommentFactory(
            content_type=_photo_content_type(), object_id=source.pk
        )

        admin_logged_in.post(
            reverse("admin:photos_photo_merge"),
            {"ids": str(source.pk), "target": str(canon.pk)},
            follow=True,
        )

        link.refresh_from_db()
        comment.refresh_from_db()
        assert link.photo == canon
        assert comment.object_id == canon.pk
        assert ModerationAction.objects.filter(
            object_id=source.pk,
            action=ModerationActionType.MERGE,
            moderator=admin_user,
        ).exists()
