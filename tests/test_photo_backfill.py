"""Тесты сервиса обработки и команды бэкфилла."""

import io

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.photos.models import Photo, ProcessingStatus
from apps.photos.processing import CROP_VERSION
from apps.photos.services import StorageUnavailable, can_edit_photo, process_photo
from tests.utils import QUADRANT_COLORS, make_image_file, make_quadrant_image

pytestmark = pytest.mark.django_db

RED = QUADRANT_COLORS["top_left"]
GREEN = QUADRANT_COLORS["top_right"]


def _upload(image, name="quadrant.jpg", exif=None) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    options = {}
    if exif is not None:
        options["exif"] = exif
    image.save(buffer, format="JPEG", **options)
    buffer.seek(0)
    return SimpleUploadedFile(name, buffer.read(), content_type="image/jpeg")


def _photo(baseball, user, upload=None, **fields):
    return Photo.objects.create(
        sport=baseball,
        title="Фото",
        original=upload or make_image_file(),
        uploaded_by=user,
        **fields,
    )


def _display_image(photo) -> Image.Image:
    with photo.display.storage.open(photo.display.name, "rb") as handle:
        return Image.open(io.BytesIO(handle.read()))


def _close(actual, expected, tolerance=8) -> bool:
    """Сравнить цвета с допуском: JPEG сжимает с потерями."""
    return all(abs(a - b) <= tolerance for a, b in zip(actual, expected, strict=True))


class TestProcessPhotoSuccess:
    def test_display_width_height_and_hash_are_filled(self, baseball, user):
        photo = _photo(baseball, user)

        result = process_photo(photo)

        assert result.processing_status == ProcessingStatus.PROCESSED
        assert result.display
        assert result.phash
        display = _display_image(result)
        assert (result.width, result.height) == display.size

    def test_orientation_from_exif_is_applied(self, baseball, user):
        """Файл 200×100 с `Orientation=6` показывается как 100×200."""
        exif = Image.Exif()
        exif[274] = 6
        photo = _photo(
            baseball, user, upload=_upload(make_quadrant_image((200, 100)), exif=exif)
        )

        result = process_photo(photo)

        assert result.processing_status == ProcessingStatus.PROCESSED
        assert (result.width, result.height) == (100, 200)

    def test_crop_data_is_applied(self, baseball, user):
        photo = _photo(
            baseball,
            user,
            upload=_upload(make_quadrant_image()),
            crop_data={
                "version": CROP_VERSION,
                "x": 100,
                "y": 0,
                "width": 100,
                "height": 100,
            },
        )

        result = process_photo(photo)

        assert (result.width, result.height) == (100, 100)
        display = _display_image(result)
        # JPEG сжимает с потерями: каналы расходятся на единицы.
        assert _close(display.getpixel((50, 50)), GREEN)

    def test_display_has_no_exif(self, baseball, user):
        exif = Image.Exif()
        exif[271] = "Canon"
        exif[274] = 1
        photo = _photo(baseball, user, upload=_upload(make_quadrant_image(), exif=exif))

        result = process_photo(photo)

        assert result.exif["Make"] == "Canon"
        assert dict(_display_image(result).getexif()) == {}

    def test_large_original_is_shrunk_to_display_limit(self, baseball, user):
        photo = _photo(
            baseball,
            user,
            upload=_upload(Image.new("RGB", (4000, 2000), "red")),
        )

        result = process_photo(photo)

        assert result.width == 1280

    def test_repeated_call_recalculates_display(self, baseball, user):
        photo = _photo(baseball, user, upload=_upload(make_quadrant_image()))
        process_photo(photo)
        first_display = photo.display.name

        photo.crop_data = {
            "version": CROP_VERSION,
            "x": 0,
            "y": 0,
            "width": 100,
            "height": 100,
        }
        result = process_photo(photo)

        assert (result.width, result.height) == (100, 100)
        # Старый файл остаётся: сборка мусора отложена до P3-11.
        assert result.display.name != first_display


class TestCanEditPhoto:
    def test_author_can_edit(self, photo, user):
        assert can_edit_photo(user, photo)

    def test_moderator_can_edit_foreign_photo(self, photo, moderator):
        assert can_edit_photo(moderator, photo)

    def test_stranger_cannot_edit(self, photo, other_user):
        assert not can_edit_photo(other_user, photo)

    def test_anonymous_cannot_edit(self, photo):
        assert not can_edit_photo(AnonymousUser(), photo)


class TestProcessPhotoFailure:
    def test_broken_file_gives_failed_status(self, baseball, user):
        broken = SimpleUploadedFile(
            "broken.jpg", b"not an image", content_type="image/jpeg"
        )
        photo = _photo(baseball, user, upload=broken)

        result = process_photo(photo)

        assert result.processing_status == ProcessingStatus.FAILED
        assert result.processing_error
        assert "Traceback" not in result.processing_error
        assert not result.display

    def test_missing_original_gives_failed_status(self, baseball, user):
        photo = _photo(baseball, user)
        name = photo.original.name
        photo.original.storage.delete(name)

        result = process_photo(photo)

        assert result.processing_status == ProcessingStatus.FAILED
        assert "не найден" in result.processing_error

    def test_storage_error_is_raised_for_retry(self, baseball, user, monkeypatch):
        """Недоступное хранилище — не отказ обработки, а повод повторить."""
        photo = _photo(baseball, user)

        def broken_open(*args, **kwargs):
            raise OSError("connection refused")

        monkeypatch.setattr(photo.original.storage, "open", broken_open)

        with pytest.raises(StorageUnavailable):
            process_photo(photo)

        # Статус «в обработке» уже записан: фото не выглядит обработанным.
        photo.refresh_from_db()
        assert photo.processing_status == ProcessingStatus.PROCESSING

    def test_garbage_crop_data_does_not_break_processing(self, baseball, user):
        photo = _photo(
            baseball,
            user,
            upload=_upload(make_quadrant_image()),
            crop_data={"x": "мусор"},
        )

        result = process_photo(photo)

        assert result.processing_status == ProcessingStatus.PROCESSED
        assert (result.width, result.height) == (200, 200)

    def test_history_gets_single_record(self, baseball, user):
        """Обработка пишет одну запись в историю, а не по строке на поле."""
        photo = _photo(baseball, user)
        before = photo.history.count()

        process_photo(photo)

        assert photo.history.count() == before + 1


class TestPhotosBackfill:
    def test_dry_run_changes_nothing(self, baseball, user):
        photo = _photo(baseball, user)

        from django.core.management import call_command

        call_command("photos_backfill", "--dry-run", stdout=io.StringIO())

        photo.refresh_from_db()
        assert not photo.display
        assert not photo.phash
        assert photo.processing_status == ProcessingStatus.UPLOADED

    def test_backfill_fills_derived_fields(self, baseball, user):
        from django.core.management import call_command

        photo = _photo(baseball, user)

        call_command("photos_backfill", stdout=io.StringIO())

        photo.refresh_from_db()
        assert photo.display
        assert photo.width and photo.height
        assert photo.phash
        assert photo.processing_status == ProcessingStatus.PROCESSED

    def test_only_missing_skips_photos_without_hash(self, baseball, user):
        from django.core.management import call_command

        photo = _photo(baseball, user)
        process_photo(photo)
        photo.phash = ""
        photo.save(update_fields=["phash"])

        call_command("photos_backfill", "--only-missing", stdout=io.StringIO())

        photo.refresh_from_db()
        assert photo.phash == ""

    def test_limit_limits_the_pass(self, baseball, user):
        from django.core.management import call_command

        first = _photo(baseball, user)
        second = _photo(baseball, user)

        call_command("photos_backfill", "--limit", "1", stdout=io.StringIO())

        first.refresh_from_db()
        second.refresh_from_db()
        assert bool(first.display) != bool(second.display)

    def test_broken_photo_does_not_stop_the_pass(self, baseball, user):
        from django.core.management import call_command

        broken = _photo(
            baseball,
            user,
            upload=SimpleUploadedFile("broken.jpg", b"junk", content_type="image/jpeg"),
        )
        good = _photo(baseball, user)

        out = io.StringIO()
        call_command("photos_backfill", stdout=out)

        broken.refresh_from_db()
        good.refresh_from_db()
        assert broken.processing_status == ProcessingStatus.FAILED
        assert good.processing_status == ProcessingStatus.PROCESSED
        assert "ошибок: 1" in out.getvalue()

    def test_processed_photos_are_skipped(self, baseball, user):
        from django.core.management import call_command

        photo = _photo(baseball, user)
        process_photo(photo)

        out = io.StringIO()
        call_command("photos_backfill", stdout=out)

        assert "Обработано: 0" in out.getvalue()

    def test_photo_with_hash_is_kept_without_rehash(self, baseball, user, monkeypatch):
        from django.core.management import call_command

        photo = _photo(baseball, user)
        process_photo(photo)
        old_hash = photo.phash

        monkeypatch.setattr(
            "apps.photos.services.perceptual_hash", lambda image: "новый-хеш"
        )
        call_command("photos_backfill", stdout=io.StringIO())

        photo.refresh_from_db()
        assert photo.phash == old_hash

    def test_rehash_recalculates_hash(self, baseball, user, monkeypatch):
        from django.core.management import call_command

        photo = _photo(baseball, user)
        process_photo(photo)
        old_hash = photo.phash
        new_hash = "f" * 64

        monkeypatch.setattr(
            "apps.photos.services.perceptual_hash", lambda image: new_hash
        )
        call_command("photos_backfill", "--rehash", stdout=io.StringIO())

        photo.refresh_from_db()
        assert photo.phash == new_hash
        assert photo.phash != old_hash

    def test_rehash_dry_run_changes_nothing(self, baseball, user, monkeypatch):
        from django.core.management import call_command

        photo = _photo(baseball, user)
        process_photo(photo)
        old_hash = photo.phash

        monkeypatch.setattr(
            "apps.photos.services.perceptual_hash", lambda image: "f" * 64
        )
        out = io.StringIO()
        call_command("photos_backfill", "--rehash", "--dry-run", stdout=out)

        photo.refresh_from_db()
        assert photo.phash == old_hash
        assert "будет обработано" in out.getvalue()

    def test_storage_unavailable_does_not_stop_the_pass(
        self, baseball, user, monkeypatch
    ):
        """Один недоступный объект не должен останавливать весь архив."""
        from django.core.management import call_command

        from apps.photos import services

        unavailable = _photo(baseball, user)
        good = _photo(baseball, user)
        original = services.process_photo

        def flaky(photo):
            if photo.pk == unavailable.pk:
                raise StorageUnavailable("connection refused")
            return original(photo)

        monkeypatch.setattr(
            "apps.photos.management.commands.photos_backfill.process_photo", flaky
        )

        out = io.StringIO()
        call_command("photos_backfill", stdout=out)

        unavailable.refresh_from_db()
        good.refresh_from_db()
        # Недоступное фото не помечено ошибкой: повтор ещё возможен.
        assert unavailable.processing_status != ProcessingStatus.FAILED
        assert good.processing_status == ProcessingStatus.PROCESSED
        assert "хранилище недоступно: 1" in out.getvalue()
