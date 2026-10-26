"""Тесты Celery-задачи обработки фото и точки постановки в очередь."""

import pytest
from celery.app.task import Task
from celery.exceptions import Retry
from django.urls import reverse

from apps.photos import services
from apps.photos.models import Photo, ProcessingStatus
from apps.photos.processing import CROP_VERSION
from apps.photos.services import StorageUnavailable
from apps.photos.tasks import enqueue_photo_processing, process_photo_task
from tests.utils import make_broken_upload, make_image_file

pytestmark = pytest.mark.django_db


def _photo(baseball, user, **fields):
    """Черновик с настоящим файлом: задача работает на нём."""
    fields.setdefault("original", make_image_file())
    return Photo.objects.create(sport=baseball, uploaded_by=user, **fields)


def _retry_spy(monkeypatch):
    """Записать вызовы `retry`, не давая задаче уйти в повтор.

    Настоящий `Task.retry` в eager-режиме сразу запускает задачу заново:
    без подмены тест проверял бы пачку вызовов, а не решение «повторить».
    """
    calls = []

    def fake_retry(self, exc=None, countdown=None, **kwargs):
        calls.append(countdown)
        raise Retry()

    monkeypatch.setattr(Task, "retry", fake_retry)
    return calls


class TestProcessPhotoTask:
    def test_task_fills_display_size_and_hash(self, baseball, user):
        photo = _photo(baseball, user)

        process_photo_task.delay(photo.pk)

        photo.refresh_from_db()
        assert photo.processing_status == ProcessingStatus.PROCESSED
        assert photo.display
        assert photo.width and photo.height
        assert photo.phash

    def test_status_goes_through_processing(self, baseball, user, monkeypatch):
        """Промежуточный статус виден в базе: иначе падение воркера
        не отличить от идущей обработки.
        """
        photo = _photo(baseball, user)
        assert photo.processing_status == ProcessingStatus.UPLOADED
        seen = []
        real_hash = services.perceptual_hash

        def spy(display):
            # Хеш считается после записи «в обработке» и до финального
            # сохранения — единственная точка внутри пайплайна.
            seen.append(Photo.objects.get(pk=photo.pk).processing_status)
            return real_hash(display)

        monkeypatch.setattr(services, "perceptual_hash", spy)

        process_photo_task.delay(photo.pk)

        assert seen == [ProcessingStatus.PROCESSING]
        photo.refresh_from_db()
        assert photo.processing_status == ProcessingStatus.PROCESSED

    def test_broken_file_fails_without_retry(self, baseball, user, monkeypatch):
        """Битый файл повтор не исправит: сразу `failed` с текстом."""
        calls = _retry_spy(monkeypatch)
        photo = _photo(baseball, user, original=make_broken_upload())

        process_photo_task.delay(photo.pk)

        photo.refresh_from_db()
        assert photo.processing_status == ProcessingStatus.FAILED
        assert photo.processing_error
        assert calls == []

    def test_deleted_photo_is_skipped_silently(self, baseball, user):
        photo = _photo(baseball, user)
        photo.soft_delete()

        process_photo_task.delay(photo.pk)

        photo.refresh_from_db()
        assert photo.processing_status == ProcessingStatus.UPLOADED
        assert not photo.display

    def test_missing_photo_is_skipped_silently(self, baseball, user):
        """Фото могли удалить, пока задача ждала в очереди."""
        process_photo_task.delay(999_999)

    def test_rerun_recalculates_display(self, baseball, user):
        """Действие админки «Обработать заново» идёт тем же путём."""
        photo = _photo(baseball, user, original=make_image_file(size=(400, 400)))
        process_photo_task.delay(photo.pk)
        photo.refresh_from_db()
        first_display = photo.display.name

        photo.crop_data = {
            "version": CROP_VERSION,
            "x": 0,
            "y": 0,
            "width": 100,
            "height": 100,
        }
        photo.save(update_fields=["crop_data"])
        process_photo_task.delay(photo.pk)

        photo.refresh_from_db()
        assert (photo.width, photo.height) == (100, 100)
        assert photo.display.name != first_display


class TestStorageRetries:
    @pytest.mark.parametrize(("retries", "countdown"), [(0, 60), (1, 120), (2, 240)])
    def test_retry_backoff_doubles(
        self, baseball, user, monkeypatch, retries, countdown
    ):
        photo = _photo(baseball, user)
        calls = _retry_spy(monkeypatch)

        def unavailable(photo):
            raise StorageUnavailable("connection refused")

        monkeypatch.setattr("apps.photos.tasks.process_photo", unavailable)

        # `throw=False`: подменённый `retry` поднимает `Retry`, и в eager
        # режиме он вышел бы наружу как ошибка теста.
        process_photo_task.apply(args=[photo.pk], retries=retries, throw=False)

        assert calls == [countdown]

    def test_exhausted_retries_mark_photo_failed(self, baseball, user, monkeypatch):
        """Молча упавшая задача оставила бы фото в «Обрабатывается»."""
        photo = _photo(baseball, user)

        def unavailable(photo):
            raise StorageUnavailable("connection refused")

        monkeypatch.setattr("apps.photos.tasks.process_photo", unavailable)

        process_photo_task.apply(
            args=[photo.pk],
            retries=process_photo_task.max_retries,
            throw=False,
        )

        photo.refresh_from_db()
        assert photo.processing_status == ProcessingStatus.FAILED
        assert "Хранилище недоступно" in photo.processing_error


class TestEnqueue:
    def test_enqueue_runs_without_broker(self, baseball, user, settings):
        """Тесты и dev не требуют Redis: задача идёт в том же процессе."""
        assert settings.CELERY_TASK_ALWAYS_EAGER
        photo = _photo(baseball, user)

        enqueue_photo_processing(photo)

        photo.refresh_from_db()
        assert photo.processing_status == ProcessingStatus.PROCESSED

    def test_admin_action_queues_live_photos(
        self, client, admin_user, baseball, user, monkeypatch
    ):
        live = _photo(baseball, user)
        deleted = _photo(baseball, user)
        deleted.soft_delete()
        queued = []
        monkeypatch.setattr(
            "apps.photos.admin.enqueue_photo_processing",
            lambda photo: queued.append(photo.pk),
        )
        client.force_login(admin_user)

        response = client.post(
            reverse("admin:photos_photo_changelist"),
            {
                "action": "action_reprocess",
                "_selected_action": [str(live.pk), str(deleted.pk)],
            },
            follow=True,
        )

        assert queued == [live.pk]
        assert "Поставлено в обработку: 1." in response.content.decode()
