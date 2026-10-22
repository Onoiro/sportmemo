"""Вспомогательные функции тестов."""

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

# Цвета квадрантов эталонной картинки. Проверка «какой участок вырезан»
# идёт по ним: сравнивать пиксели проще, чем координаты.
QUADRANT_COLORS = {
    "top_left": (255, 0, 0),
    "top_right": (0, 255, 0),
    "bottom_left": (0, 0, 255),
    "bottom_right": (255, 255, 0),
}


def make_image_file(name="test.jpg", size=(120, 80), color="red", exif=None):
    """Сгенерировать настоящее изображение — Pillow нужен для ImageField.

    `exif` — словарь тегов PIL (`{274: 6}`), записывается в файл как есть.
    """
    buffer = io.BytesIO()
    image = Image.new("RGB", size, color)
    options = {}
    if exif:
        options["exif"] = Image.Exif()
        for tag, value in exif.items():
            options["exif"][tag] = value
    image.save(buffer, format="JPEG", **options)
    buffer.seek(0)
    return SimpleUploadedFile(name, buffer.read(), content_type="image/jpeg")


def make_quadrant_image(size=(200, 200)):
    """Картинка из четырёх цветных квадрантов.

    По цвету углов проверяется, какой участок вырезан и как повёрнуто
    изображение: одни и те же числа координат при разных поворотах дают
    разные цвета.
    """
    image = Image.new("RGB", size)
    width, height = size
    half_width, half_height = width // 2, height // 2
    boxes = {
        "top_left": (0, 0, half_width, half_height),
        "top_right": (half_width, 0, width, half_height),
        "bottom_left": (0, half_height, half_width, height),
        "bottom_right": (half_width, half_height, width, height),
    }
    for name, box in boxes.items():
        image.paste(
            Image.new("RGB", (box[2] - box[0], box[3] - box[1]), QUADRANT_COLORS[name]),
            box,
        )
    return image


def make_image_with_exif_orientation(orientation=6, size=(200, 100)):
    """Изображение с записанным тегом `Orientation` (274).

    Телефон пишет «снимал боком» в метаданные, а не в пиксели: файл
    остаётся 200×100, а показывать его надо повёрнутым. Картинка
    прогоняется через JPEG, иначе `getexif()` у свежесозданного объекта
    пуст: тег живёт в файле, а не в памяти.
    """
    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[274] = orientation
    make_quadrant_image(size=size).save(buffer, format="JPEG", exif=exif)
    buffer.seek(0)
    return Image.open(buffer)


def image_pixel(image, x, y):
    """Цвет пикселя в виде кортежа RGB."""
    return image.convert("RGB").getpixel((x, y))


def publish_with_attributes(obj, attributes):
    """Записать `attributes` и опубликовать объект.

    `publish()` сохраняет только поля модерации (`update_fields`), поэтому
    атрибуты нужно записать отдельным `save()` — иначе они не попадут в базу.
    """
    obj.attributes = attributes
    obj.save(update_fields=["attributes", "updated_at"])
    obj.publish()
    return obj
