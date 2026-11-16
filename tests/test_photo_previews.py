"""Тесты команды `photos_previews`.

Стратегия `Optimistic` строит превью только при сохранении источника,
поэтому потерянный файл сам не вернётся. Команда — единственный способ
его вернуть, и проверяется она на настоящем хранилище: файл удаляется
и должен появиться снова.
"""

import io
import os

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from PIL import Image

from apps.core.models import Status
from apps.photos.models import Photo
from tests.utils import make_image_file

pytestmark = pytest.mark.django_db


def _photo(baseball, user, **fields):
    fields.setdefault("original", make_image_file())
    return Photo.objects.create(
        sport=baseball,
        title="Фото",
        uploaded_by=user,
        status=Status.PUBLISHED,
        **fields,
    )


def _delete_previews(photo):
    """Удалить файлы превью, оставив `display` на месте."""
    for name in ("thumbnail", "preview"):
        image = getattr(photo, name)
        if image.name and image.storage.exists(image.name):
            image.storage.delete(image.name)


def _exists(photo, name):
    image = getattr(photo, name)
    return bool(image.name) and image.storage.exists(image.name)


def _run(*args):
    out = io.StringIO()
    call_command("photos_previews", *args, stdout=out)
    return out.getvalue()


class TestMissingPreviews:
    def test_returns_deleted_preview(self, baseball, user):
        photo = _photo(baseball, user, display=make_image_file("display.jpg"))
        _delete_previews(photo)
        assert not _exists(photo, "thumbnail")

        _run()

        assert _exists(photo, "thumbnail")
        assert _exists(photo, "preview")

    def test_rebuilt_file_is_a_valid_image(self, baseball, user):
        photo = _photo(baseball, user, display=make_image_file("display.jpg"))
        _delete_previews(photo)

        _run()

        thumbnail = photo.thumbnail
        with thumbnail.storage.open(thumbnail.name, "rb") as handle:
            assert Image.open(handle).size[0] <= 400

    def test_existing_previews_are_skipped(self, baseball, user):
        photo = _photo(baseball, user, display=make_image_file("display.jpg"))
        assert _exists(photo, "thumbnail")

        out = _run()

        assert "пропущено" in out
        assert _exists(photo, "thumbnail")


class TestRebuildAll:
    def test_all_rebuilds_existing_preview(self, baseball, user):
        photo = _photo(baseball, user, display=make_image_file("display.jpg"))
        thumbnail = photo.thumbnail
        # Метка времени в прошлом: пересборка обязана её сдвинуть, иначе
        # тест прошёл бы и на «ничего не сделал».
        past = 1_000_000_000
        os.utime(thumbnail.storage.path(thumbnail.name), (past, past))

        _run("--all")

        assert os.stat(thumbnail.storage.path(thumbnail.name)).st_mtime > past


class TestDryRun:
    def test_dry_run_creates_nothing(self, baseball, user):
        photo = _photo(baseball, user, display=make_image_file("display.jpg"))
        _delete_previews(photo)

        out = _run("--dry-run")

        assert not _exists(photo, "thumbnail")
        assert not _exists(photo, "preview")
        assert "К пересборке: 1" in out


class TestLimits:
    def test_limit_limits_the_pass(self, baseball, user):
        first = _photo(baseball, user, display=make_image_file("display.jpg"))
        second = _photo(baseball, user, display=make_image_file("display.jpg"))
        _delete_previews(first)
        _delete_previews(second)

        _run("--limit", "1")

        assert _exists(first, "thumbnail")
        assert not _exists(second, "thumbnail")


class TestPhotoWithoutDisplay:
    def test_skipped_without_error(self, baseball, user):
        photo = _photo(baseball, user)

        out = _run()

        assert "нет display" in out
        assert "ошибок: 0" in out
        assert not photo.thumbnail.name

    def test_broken_photo_does_not_stop_the_pass(self, baseball, user):
        """Одно битое фото не должно останавливать пересборку архива."""
        broken = _photo(baseball, user)
        # `display` указывает на файл, которого нет: генерация упадёт.
        Photo.objects.filter(pk=broken.pk).update(
            display="photos/display/2026/11/gone.jpg"
        )
        good = _photo(baseball, user, display=make_image_file("display.jpg"))
        _delete_previews(good)

        out = _run()

        assert _exists(good, "thumbnail")
        assert "ошибок: 1" in out


def test_command_needs_no_arguments():
    """Пустая база — не ошибка: команда просто ничего не делает."""
    assert "Пересобрано: 0" in _run()


def test_bad_limit_is_rejected():
    with pytest.raises(CommandError):
        call_command("photos_previews", "--limit", "abc", stdout=io.StringIO())
