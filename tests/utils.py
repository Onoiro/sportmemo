"""Вспомогательные функции тестов."""

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import ExifTags, Image

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


def make_upload(name="photo.jpg", size=(400, 400), image_format="JPEG", taken_at=None):
    """Файл для формы загрузки — в нужном формате и с датой съёмки.

    `taken_at` пишется в `DateTimeOriginal` (Exif IFD): тест проверяет, что
    год из файла не попадает в базу сам. HEIF собирается через `pillow_heif`:
    Pillow без плагина этот формат не сохраняет, а импорт держим внутри
    функции — пакет необязательный.
    """
    buffer = io.BytesIO()
    image = Image.new("RGB", size, "red")
    if image_format == "HEIF":
        import pillow_heif

        pillow_heif.from_pillow(image).save(buffer, quality=90)
    else:
        options = {}
        if taken_at is not None:
            exif = Image.Exif()
            exif[ExifTags.IFD.Exif] = {ExifTags.Base.DateTimeOriginal: taken_at}
            options["exif"] = exif
        image.save(buffer, format=image_format, **options)
    buffer.seek(0)
    content_type = "image/heic" if image_format == "HEIF" else "image/jpeg"
    return SimpleUploadedFile(name, buffer.read(), content_type=content_type)


def make_broken_upload(name="broken.jpg"):
    """Файл, который Pillow не откроет: битый оригинал в хранилище.

    Нужен тестам задачи: обработка обязана поставить `failed`, а не уйти
    в бесконечный повтор.
    """
    return SimpleUploadedFile(name, b"not an image", content_type="image/jpeg")


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
