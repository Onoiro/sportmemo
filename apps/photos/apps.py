"""Регистрация форматов, которых нет в Pillow из коробки."""

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class PhotosConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.photos"
    verbose_name = "Фотографии"

    def ready(self):
        # iPhone снимает в HEIC по умолчанию, и архив без него был бы неполным.
        # Пакет необязательный: без него остальные форматы работают,
        # а HEIC просто попадёт в «ошибка обработки».
        try:
            import pillow_heif
        except ImportError:
            logger.warning("pillow-heif не установлен: HEIC не будет открываться")
        else:
            pillow_heif.register_heif_opener()
