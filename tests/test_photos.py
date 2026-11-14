"""Тесты фотографий: видимость, дубликаты, превью, люди на фото."""

import pytest
from django.core.exceptions import ValidationError

from apps.core.models import Status
from apps.photos.models import (
    Photo,
    PhotoPerson,
    ProcessingStatus,
    Source,
    SourceKind,
    SourceOrigin,
)
from tests.factories import PhotoFactory, SourceFactory, UserFactory
from tests.utils import make_image_file

pytestmark = pytest.mark.django_db


class TestSource:
    def test_str_uses_title(self, baseball, user):
        source = Source.objects.create(
            kind=SourceKind.NEWSPAPER, title="Советский спорт"
        )

        assert str(source) == "Советский спорт"

    def test_str_falls_back_to_kind(self):
        source = Source.objects.create(kind=SourceKind.NEGATIVE)

        assert str(source) == "негатив"

    def test_soft_delete_hides_from_public(self, baseball, user):
        source = Source.objects.create(title="Газета")
        source.soft_delete()

        assert not Source.public.filter(pk=source.pk).exists()


class TestPhotoVisibility:
    def test_photo_without_rights_is_public(self, baseball, user):
        """Права проверяет автор, а не сервер (решение 2.1):
        исторический флаг на видимость не влияет.
        """
        photo = Photo.objects.create(
            sport=baseball,
            title="Без прав",
            original=make_image_file(),
            uploaded_by=user,
            status=Status.PUBLISHED,
            rights_confirmed=False,
        )

        assert photo.is_public

    def test_photo_with_rights_and_published_is_public(self, photo):
        assert photo.is_public

    def test_draft_photo_is_not_public(self, photo):
        photo.status = Status.DRAFT

        assert not photo.is_public

    def test_deleted_photo_is_not_public(self, photo):
        photo.soft_delete()

        assert not photo.is_public

    def test_original_is_never_overwritten(self, photo):
        """Оригинал и обработанная версия — разные поля,
        оригинал не перезаписывается.
        """
        original_name = photo.original.name

        photo.processed = "photos/processed/test.jpg"
        photo.processing_status = ProcessingStatus.PROCESSED
        photo.save()

        photo.refresh_from_db()
        assert photo.original.name == original_name
        assert photo.processed.name == "photos/processed/test.jpg"


class TestPhotoDuplicate:
    def test_merged_into_cannot_reference_self(self, photo):
        photo.merged_into = photo

        with pytest.raises(ValidationError):
            photo.full_clean()

    def test_database_rejects_self_merge(self, photo):
        """Constraint из миграции, а не только `full_clean`: админка и
        скрипты сохраняют в обход валидации.
        """
        from django.db import IntegrityError, transaction

        with pytest.raises(IntegrityError), transaction.atomic():
            Photo.objects.filter(pk=photo.pk).update(merged_into_id=photo.pk)

    def test_merged_into_can_reference_other_photo(self, photo, baseball, user):
        other = Photo.objects.create(
            sport=baseball,
            title="Дубль",
            original=make_image_file("dup.jpg"),
            uploaded_by=user,
            merged_into=photo,
        )

        assert other.merged_into == photo
        assert other.is_merged
        assert photo.merged_from.count() == 1

    def test_mergeable_canonical_is_itself(self, photo):
        assert photo.get_canonical() == photo


class TestPhotoYear:
    def test_year_filled_from_taken_date(self, baseball, user):
        photo = Photo.objects.create(
            sport=baseball,
            title="Фото",
            original=make_image_file(),
            uploaded_by=user,
            taken_date="1994-05-01",
        )

        assert photo.year == 1994

    def test_explicit_year_kept(self, baseball, user):
        photo = Photo.objects.create(
            sport=baseball,
            title="Фото",
            original=make_image_file(),
            uploaded_by=user,
            taken_date="1994-05-01",
            year=1990,
        )

        assert photo.year == 1990


class TestPhotoProcessingStatus:
    def test_default_is_uploaded(self, photo):
        assert photo.processing_status == ProcessingStatus.UPLOADED

    def test_processing_is_separate_from_publication(self, photo):
        photo.processing_status = ProcessingStatus.FAILED
        photo.processing_error = "не удалось открыть файл"
        photo.save()

        photo.refresh_from_db()
        assert photo.processing_status == ProcessingStatus.FAILED
        assert photo.status == Status.PUBLISHED
        assert photo.processing_error == "не удалось открыть файл"


class TestPhotoThumbnails:
    def test_thumbnail_is_generated(self, photo):
        thumbnail = photo.thumbnail

        assert thumbnail.name
        assert thumbnail.width <= 400
        assert thumbnail.height <= 400

    def test_preview_is_generated(self, photo):
        preview = photo.preview

        assert preview.name
        assert preview.width <= 1280

    def test_previews_are_built_from_display(self, baseball, user):
        """Пользователь кроит кадр — в галерее он видит результат, а не
        исходный файл: превью строятся от `display`.
        """
        photo = Photo.objects.create(
            sport=baseball,
            title="Кроп",
            original=make_image_file("original.jpg", size=(120, 80)),
            display=make_image_file("display.jpg", size=(60, 40)),
            uploaded_by=user,
            status=Status.PUBLISHED,
        )

        assert "photos/display/" in photo.thumbnail.name
        assert "photos/display/" in photo.preview.name

    def test_photo_without_display_has_no_preview(self, baseball, user):
        """Фото без `display` не падает: шаблон покажет заглушку."""
        photo = Photo.objects.create(
            sport=baseball,
            title="Без версии для показа",
            original=make_image_file(),
            uploaded_by=user,
            status=Status.PUBLISHED,
        )

        assert not photo.thumbnail
        assert not photo.preview


class TestPhotoPerson:
    def test_unique_person_per_photo(self, photo, person):
        PhotoPerson.objects.create(photo=photo, person=person)

        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            PhotoPerson.objects.create(photo=photo, person=person)

    def test_role_and_position_are_stored(self, photo, person):
        link = PhotoPerson.objects.create(
            photo=photo,
            person=person,
            role="coach",
            position="слева второй",
        )

        link.refresh_from_db()
        assert link.role == "coach"
        assert link.position == "слева второй"

    def test_str(self, photo, person):
        link = PhotoPerson.objects.create(photo=photo, person=person)

        assert str(person) in str(link)


class TestSourceOriginFields:
    def test_defaults_are_empty(self, baseball):
        photo = Photo.objects.create(sport=baseball)

        assert photo.source_origin == ""
        assert photo.source_note == ""

    def test_origin_and_note_are_stored(self, baseball):
        photo = Photo.objects.create(
            sport=baseball,
            source_origin=SourceOrigin.PRESS,
            source_note="Советский спорт, 1994",
        )

        photo.refresh_from_db()
        assert photo.source_origin == SourceOrigin.PRESS
        assert photo.source_note == "Советский спорт, 1994"

    def test_empty_is_not_unknown(self, baseball):
        """«Не указано» и «не знаю» — разные ответы: по пустому полю
        видно, что человек ещё не возвращался к фото.
        """
        empty = Photo.objects.create(sport=baseball)
        unknown = Photo.objects.create(sport=baseball, source_origin="unknown")

        assert empty.source_origin == ""
        assert unknown.source_origin == SourceOrigin.UNKNOWN

    def test_history_keeps_new_fields(self, baseball):
        photo = Photo.objects.create(
            sport=baseball, source_origin=SourceOrigin.PERSONAL
        )

        assert photo.history.first().source_origin == SourceOrigin.PERSONAL


class TestPhotoDetailSource:
    """Страница фото печатает готовый блок происхождения или ничего."""

    def test_personal_origin_shows_author_name(self, client, user):
        author = UserFactory(display_name="Сергей Иванов")
        photo = PhotoFactory(
            uploaded_by=author,
            source_origin=SourceOrigin.PERSONAL,
            source_note="архив тренера",
        )

        html = client.get(photo.get_absolute_url()).content.decode()

        assert "из моего личного архива" in html
        assert "Сергей Иванов" in html
        assert "архив тренера" in html

    def test_bound_source_wins_over_origin(self, client):
        """Подробный блок справочника и упрощённый рядом — два ответа
        на один вопрос, и человек не поймёт, какой из них правда.
        """
        photo = PhotoFactory(
            source=SourceFactory(publication="Советский спорт"),
            source_origin=SourceOrigin.INTERNET,
        )

        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Советский спорт" in html
        assert "из интернета" not in html

    def test_empty_origin_hides_the_block(self, client):
        photo = PhotoFactory(source_origin="")

        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Откуда" not in html
        assert "Предоставил(а)" not in html

    def test_note_without_holder_for_press(self, client):
        photo = PhotoFactory(
            source_origin=SourceOrigin.PRESS,
            source_note="Советский спорт, 1994",
        )

        html = client.get(photo.get_absolute_url()).content.decode()

        assert "газета, журнал, книга" in html
        assert "Советский спорт, 1994" in html
        assert "Предоставил(а)" not in html

    def test_edit_page_shows_both_fields(self, client, user):
        photo = PhotoFactory(uploaded_by=user, status=Status.DRAFT)
        client.force_login(user)

        html = client.get(f"/photos/{photo.pk}/edit/").content.decode()

        assert 'name="source_origin"' in html
        assert 'name="source_note"' in html
        assert "Откуда у вас этот снимок" in html


class TestPhotoMergeOnTeamMerge:
    def test_photo_moves_to_canonical_team(self, photo, team, baseball, user):
        from apps.catalog.models import Team

        target = Team.objects.create(sport=baseball, name="Канон", created_by=user)
        photo.team = team
        photo.save()

        team.merge_into(target)

        photo.refresh_from_db()
        assert photo.team == target
