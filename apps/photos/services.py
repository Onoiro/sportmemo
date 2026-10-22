"""Пайплайн обработки фотографии: от оригинала до версии для показа.

Сервис знает о модели и хранилище, но вся работа с пикселями лежит
в `apps/photos/processing.py`. Здесь — порядок шагов, запись полей
и превращение любой ошибки в статус `failed`: исключение наружу
не выходит, иначе один битый файл уронил бы проход бэкфилла.
"""

import io
import logging

from django.core.files.base import ContentFile
from PIL import Image, UnidentifiedImageError

from apps.photos.exif import extract_exif
from apps.photos.models import Photo, ProcessingStatus
from apps.photos.processing import (
    WORK_COPY_MAX_SIDE,
    apply_crop,
    apply_rotation,
    encode_display,
    fit_max_side,
    make_display,
    normalize_orientation,
    perceptual_hash,
)

logger = logging.getLogger(__name__)

# Поля, которые обновляет обработка. Список нужен для `update_fields`:
# одна запись в истории вместо двух, а `updated_at` попадает в `lastmod`
# карты сайта.
PROCESSED_FIELDS = (
    "display",
    "width",
    "height",
    "phash",
    "exif",
    "processing_status",
    "processing_error",
    "updated_at",
)


def _fail(photo, message: str) -> Photo:
    """Записать ошибку обработки и вернуть объект.

    В `processing_error` идёт короткий текст, а не трейсбек: поле
    показывается человеку, а стек лежит в логе.
    """
    photo.processing_status = ProcessingStatus.FAILED
    photo.processing_error = message
    photo.save(update_fields=list(PROCESSED_FIELDS))
    return photo


def _load_original(photo) -> Image.Image:
    """Прочитать оригинал в память и полностью декодировать.

    `Image.open` ленив: битый файл падает на первом обращении к пикселям,
    а не на открытии. Поэтому `load()` вызывается здесь, внутри общего
    `try` — иначе ошибка декодирования всплыла бы на ресайзе.
    """
    with photo.original.storage.open(photo.original.name, "rb") as handle:
        data = handle.read()
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def process_photo(photo: Photo) -> Photo:
    """Нормализовать оригинал и заполнить производные поля.

    Повторный вызов пересчитывает `display` заново (например, после правки
    `crop_data`). Старый файл остаётся в хранилище: сборка мусора отложена
    до P3-11, а удалять файл, на который могла попасть ссылка из кэша
    мессенджера, хуже, чем оставить его лежать.
    """
    if not photo.original:
        return _fail(photo, "Оригинал не загружен")

    try:
        image = _load_original(photo)
        # EXIF читается до транспонирования: `exif_transpose` возвращает
        # копию без тега `Orientation`, и ориентация потерялась бы.
        exif = extract_exif(image)

        working = normalize_orientation(image)
        # Рабочая копия до 2500 px: дальше вся работа идёт с ней,
        # а `display` всё равно не больше 1280 px.
        working = fit_max_side(working, WORK_COPY_MAX_SIDE)
        crop_data = photo.crop_data if isinstance(photo.crop_data, dict) else {}
        working = apply_rotation(working, crop_data.get("rotate", 0))
        working = apply_crop(working, crop_data)

        display = make_display(working)
        payload = encode_display(display)
    except Image.DecompressionBombError:
        logger.exception("Фото #%s больше допустимого размера", photo.pk)
        return _fail(photo, "Слишком большое изображение")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.exception("Не удалось обработать фото #%s", photo.pk)
        return _fail(photo, f"Не удалось прочитать файл: {type(exc).__name__}")

    # Старый файл `display` не удаляем: см. docstring.
    photo.display.save("display.jpg", ContentFile(payload), save=False)
    photo.width, photo.height = display.size
    photo.phash = perceptual_hash(display)
    photo.exif = exif
    photo.processing_status = ProcessingStatus.PROCESSED
    photo.processing_error = ""
    photo.save(update_fields=list(PROCESSED_FIELDS))
    logger.info("Фото #%s обработано: %s×%s", photo.pk, photo.width, photo.height)
    return photo
