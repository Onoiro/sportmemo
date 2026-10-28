"""Тесты поиска похожих фото: отбор кандидатов, ранжирование, страница."""

import pytest
from django.urls import reverse
from PIL import Image

from apps.core.models import Sport, Status
from apps.photos import similar
from apps.photos.models import Photo
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
from tests.factories import PhotoFactory, TeamFactory

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
