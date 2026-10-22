"""Разбор EXIF по белому списку.

Метаданные файла хранятся в JSONB и показываются модератору как подсказка:
кто снимал, чем, когда. Целиком EXIF не сохраняется — в нём бывает GPS,
то есть домашний адрес человека на фото.

Правило приватности: GPS IFD не читается вовсе, а не вырезается после
чтения. Отбросить проще, чем забыть отбросить.
"""

import logging
from datetime import date

from PIL import ExifTags

logger = logging.getLogger(__name__)

# Теги IFD0: техника, автор, описание, ориентация.
WHITELIST_IFD0 = frozenset(
    {
        "Make",
        "Model",
        "Software",
        "Artist",
        "Copyright",
        "ImageDescription",
        "XResolution",
        "YResolution",
        "ResolutionUnit",
        "Orientation",
    }
)

# Теги Exif IFD: параметры съёмки и даты.
WHITELIST_EXIF = frozenset(
    {
        "DateTimeOriginal",
        "DateTimeDigitized",
        "ExposureTime",
        "FNumber",
        "ISOSpeedRatings",
        "FocalLength",
    }
)

# Раньше этого года снимков в архиве быть не может. Верхняя граница —
# текущий год: дата из будущего почти всегда означает сбитые часы камеры.
MIN_TAKEN_YEAR = 1850


def _normalize(value):
    """Привести значение EXIF к типу, который переживёт JSONB.

    `IFDRational` (выдержка, диафрагма) не сериализуется, `bytes` бывают
    с непечатными символами. Всё непонятное отбрасывается: подсказка
    модератору не стоит падения обработки.
    """
    from PIL.TiffImagePlugin import IFDRational

    if isinstance(value, IFDRational):
        return float(value)
    if isinstance(value, bytes):
        try:
            return value.decode("ascii").strip("\x00").strip()
        except UnicodeDecodeError:
            return None
    if isinstance(value, (tuple, list)):
        items = [_normalize(item) for item in value]
        return [item for item in items if item is not None]
    if isinstance(value, str):
        # Pillow декодирует ASCII-теги сам, но хвостовые нули оставляет.
        return value.strip("\x00").strip()
    if isinstance(value, (int, float)):
        return value
    return None


def _collect(exif_ifd, whitelist) -> dict:
    """Отобрать разрешённые теги из одного IFD и нормализовать значения."""
    collected = {}
    for number, value in exif_ifd.items():
        name = ExifTags.TAGS.get(number)
        if name not in whitelist:
            continue
        normalized = _normalize(value)
        if normalized is not None:
            collected[name] = normalized
    return collected


def extract_exif(image) -> dict:
    """Метаданные изображения по белому списку.

    GPS IFD (0x8825) не читается: координаты съёмки — это адрес, а не
    атрибут архивного снимка. Пустой словарь — нормальный результат:
    у скана EXIF может не быть вовсе.
    """
    try:
        exif = image.getexif()
    except (AttributeError, OSError, ValueError):
        # Битый блок EXIF не повод отказываться от самого изображения.
        logger.warning("Не удалось прочитать EXIF", exc_info=True)
        return {}

    result = _collect(exif, WHITELIST_IFD0)
    try:
        exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
    except (AttributeError, KeyError, OSError, ValueError):
        exif_ifd = {}
    result.update(_collect(exif_ifd, WHITELIST_EXIF))
    return result


def taken_year(exif: dict) -> int | None:
    """Год съёмки из `DateTimeOriginal` или `None`.

    Только `DateTimeOriginal`: у скана бумажного снимка EXIF принадлежит
    сканеру, и `DateTime` дал бы текущий год всему архиву. Даже верная
    цифровая дата может врать (снимок 1985-го, снятый цифромыльницей
    в 2004-м), поэтому значение идёт в поле формы, а не в базу само.
    """
    raw = exif.get("DateTimeOriginal")
    if not isinstance(raw, str):
        return None
    # Формат EXIF: «YYYY:MM:DD HH:MM:SS».
    parts = raw.split(" ", 1)[0].split(":")
    if len(parts) != 3:
        return None
    try:
        year, month, day = (int(part) for part in parts)
        date(year, month, day)
    except ValueError:
        return None
    if not MIN_TAKEN_YEAR <= year <= date.today().year:
        return None
    return year
