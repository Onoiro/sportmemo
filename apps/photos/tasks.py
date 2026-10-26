"""Celery-задачи обработки фотографий.

Единственная точка постановки задачи — `enqueue_photo_processing()`:
представление загрузки и действие админки не должны решать, как ставить
задачу. Иначе один путь обработки стал бы синхронным, а другой фоновым,
и поведение зависело бы от того, откуда пришёл запрос.

Ретраи ограничены `StorageUnavailable`: битый файл или превышение пикселей
повтор не исправит. Остальные ошибки сразу превращаются в `failed`
с коротким текстом — исключение наружу не выходит, иначе задача ушла бы
в повтор, который ничего не исправит.
"""

import logging

from celery import shared_task
from django.utils.translation import gettext

from apps.photos.models import Photo
from apps.photos.services import (
    StorageUnavailable,
    fail_photo,
    process_photo,
)

logger = logging.getLogger(__name__)

# Пауза перед повтором удваивается: 60, 120, 240 секунд. Хранилище
# (MinIO/S3) обычно возвращается за это время, а частый повтор только
# занял бы очередь воркера ожиданием.
RETRY_BACKOFF = 60


@shared_task(bind=True, max_retries=3)
def process_photo_task(self, photo_id):
    """Обработать фото в фоне: `display`, размеры, перцептивный хеш.

    `bind=True` нужен ради `self.retry`: без него задача не знает номер
    попытки и повторялась бы бесконечно.
    """
    photo = Photo.objects.filter(pk=photo_id, deleted_at__isnull=True).first()
    if photo is None:
        # Фото могли удалить, пока задача ждала в очереди. Работать не с чем,
        # и падать из-за этого незачем.
        logger.info("Фото #%s пропущено: записи нет или она удалена", photo_id)
        return

    logger.info("Обработка фото #%s начата", photo_id)
    try:
        process_photo(photo)
    except StorageUnavailable as exc:
        if self.request.retries >= self.max_retries:
            logger.exception("Хранилище недоступно для фото #%s", photo_id)
            fail_photo(photo, gettext("Хранилище недоступно, попробуйте позже"))
            return
        raise self.retry(
            exc=exc, countdown=RETRY_BACKOFF * 2**self.request.retries
        ) from exc
    except Exception as exc:
        logger.exception("Не удалось обработать фото #%s", photo_id)
        fail_photo(
            photo,
            gettext("Ошибка обработки: %(error)s") % {"error": type(exc).__name__},
        )
        return

    logger.info("Обработка фото #%s закончена", photo_id)


def enqueue_photo_processing(photo) -> None:
    """Поставить обработку фото в очередь.

    При `CELERY_TASK_ALWAYS_EAGER` (dev и тесты) задача выполняется сразу
    в текущем процессе: Redis для разработки не обязателен, а поведение
    остаётся тем же.
    """
    process_photo_task.delay(photo.pk)
