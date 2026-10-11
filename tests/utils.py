"""Вспомогательные функции тестов."""

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image


def make_image_file(name="test.jpg", size=(120, 80), color="red"):
    """Сгенерировать настоящее изображение — Pillow нужен для ImageField."""
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    buffer.seek(0)
    return SimpleUploadedFile(name, buffer.read(), content_type="image/jpeg")


def publish_with_attributes(obj, attributes):
    """Записать `attributes` и опубликовать объект.

    `publish()` сохраняет только поля модерации (`update_fields`), поэтому
    атрибуты нужно записать отдельным `save()` — иначе они не попадут в базу.
    """
    obj.attributes = attributes
    obj.save(update_fields=["attributes", "updated_at"])
    obj.publish()
    return obj
