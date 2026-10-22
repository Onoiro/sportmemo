"""Чистые функции обработки изображений.

Модуль не знает ни о базе, ни о хранилище, ни о Celery: принимает
`PIL.Image` и параметры, возвращает изображение и метаданные. Это даёт
быстрые unit-тесты без базы и одну реализацию для команды бэкфилла,
воркера и формы.

Порядок операций фиксирован и нарушать его нельзя:
`exif_transpose` → поворот пользователя → кроп → подготовка `display`.
Координаты кропа приходят в системе «как видит пользователь», то есть
после EXIF-ориентации; применённые к «сырым» пикселям, они вырежут
не тот участок.
"""

import io
import logging

import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# Лимиты загрузки. Живут здесь, а не в настройках: их проверяет и форма,
# и команда, и воркер, и все они работают с изображением, а не с Django.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MIN_SIDE_PX = 300
MAX_SIDE_PX = 10_000

# Рабочая копия: 48 Мпикс в RGB — это ~144 МБ на декодирование, и полный
# оригинал в пайплайне роняет воркер по памяти. `display` всё равно
# не больше 1280 px, а оригинал при этом не теряется.
WORK_COPY_MAX_SIDE = 2500
DISPLAY_MAX_SIDE = 1280

DISPLAY_QUALITY = 85

# Меньше этого кроп — не кроп, а случайное дрожание курсора.
MIN_CROP_SIDE = 100

# Единственная версия формата `crop_data`, которую понимает сервер.
CROP_VERSION = 1

# Размер матрицы DCT для перцептивного хеша и размер итоговой сетки.
_PHASH_SIZE = 32
_PHASH_GRID = 8


def fit_max_side(image: Image.Image, max_side: int) -> Image.Image:
    """Уменьшить по длинной стороне, не растягивая.

    Апскейл запрещён: увеличенное фото теряет детали и весит больше
    исходного, а «улучшить» скан увеличением нельзя.
    """
    if max(image.size) <= max_side:
        return image
    copy = image.copy()
    copy.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return copy


def normalize_orientation(image: Image.Image) -> Image.Image:
    """Развернуть изображение по EXIF `Orientation`.

    Телефон записывает «снимал боком» в метаданные, а не в пиксели.
    Без этого шага кроп из браузера вырежет не тот участок.
    """
    return ImageOps.exif_transpose(image)


def apply_rotation(image: Image.Image, degrees) -> Image.Image:
    """Повернуть на 0/90/180/270 градусов по часовой стрелке.

    Произвольный угол запрещён: он оставляет пустые треугольники по краям
    и требует решения «чем заполнять». Кнопки в интерфейсе — «влево/вправо»,
    то есть шаг 90 градусов.
    """
    try:
        degrees = int(degrees)
    except (TypeError, ValueError):
        degrees = 0
    if degrees % 90 != 0:
        logger.warning("Поворот на %s градусов не поддержан, пропущен", degrees)
        return image
    degrees %= 360
    if degrees == 0:
        return image
    # `PIL.rotate` крутит против часовой стрелки, пользователь ждёт
    # по часовой: отсюда минус.
    return image.rotate(-degrees, expand=True)


def _crop_box(image: Image.Image, crop_data: dict):
    """Координаты кропа после клампинга по границам изображения."""
    width, height = image.size
    try:
        left = int(crop_data["x"])
        top = int(crop_data["y"])
        crop_width = int(crop_data["width"])
        crop_height = int(crop_data["height"])
    except (KeyError, TypeError, ValueError):
        return None

    left = max(0, min(left, width))
    top = max(0, min(top, height))
    right = max(0, min(left + crop_width, width))
    bottom = max(0, min(top + crop_height, height))
    if right - left < MIN_CROP_SIDE or bottom - top < MIN_CROP_SIDE:
        return None
    return (left, top, right, bottom)


def apply_crop(image: Image.Image, crop_data) -> Image.Image:
    """Вырезать участок по `crop_data`.

    Мусор в данных (не словарь, чужая версия, отсутствующие координаты,
    кроп меньше 100 px) — это «без кропа» с предупреждением в лог.
    Сервис обязан не падать: данные приходят из браузера, а форма
    проверит их только в 3.4.
    """
    if not crop_data:
        return image
    if not isinstance(crop_data, dict):
        logger.warning("crop_data не словарь, кроп пропущен")
        return image
    version = crop_data.get("version")
    if version != CROP_VERSION:
        logger.warning("crop_data версии %r, кроп пропущен", version)
        return image

    box = _crop_box(image, crop_data)
    if box is None:
        logger.warning("crop_data вне границ изображения, кроп пропущен")
        return image
    return image.crop(box)


def make_display(image: Image.Image) -> Image.Image:
    """Подготовить версию для показа: RGB не длиннее 1280 px.

    Перевод в RGB обязателен: PNG с прозрачностью и палитровые изображения
    в JPEG не сохраняются. EXIF сюда не переносится — это делает сервис,
    сохраняя файл без `exif=`.
    """
    if image.mode != "RGB":
        image = image.convert("RGB")
    return fit_max_side(image, DISPLAY_MAX_SIDE)


def encode_display(image: Image.Image) -> bytes:
    """Сохранить `display` в JPEG без метаданных.

    Ни `exif=`, ни `icc_profile=` не передаются: в `display` не должно быть
    ни GPS, ни модели камеры — он отдаётся публично.
    """
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=DISPLAY_QUALITY, optimize=True)
    return buffer.getvalue()


def _dct_matrix(size: int) -> np.ndarray:
    """Матрица DCT-II: косинусы заранее, без `scipy` и `imagehash`."""
    index = np.arange(size)
    angles = (2 * index[None, :] + 1) * index[:, None] * np.pi / (2 * size)
    matrix = np.cos(angles)
    matrix[0, :] *= np.sqrt(1 / size)
    matrix[1:, :] *= np.sqrt(2 / size)
    return matrix


_DCT = _dct_matrix(_PHASH_SIZE)


def perceptual_hash(image: Image.Image) -> str:
    """Перцептивный хеш 64 бита в hex (16 символов).

    Считается от `display`, а не от оригинала: дубликаты ищутся по тому,
    что видит человек. Схема классическая (pHash): уменьшить до 32×32,
    перевести в оттенки серого, взять DCT, оставить левый верхний блок
    8×8 (низкие частоты), сравнить каждое значение с медианой блока.
    """
    small = image.convert("L").resize(
        (_PHASH_SIZE, _PHASH_SIZE), Image.Resampling.LANCZOS
    )
    pixels = np.asarray(small, dtype=np.float64)
    dct = _DCT @ pixels @ _DCT.T
    low = dct[:_PHASH_GRID, :_PHASH_GRID].flatten()
    bits = low > np.median(low)

    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def hamming_distance(first: str, second: str) -> int:
    """Сколько бит различается у двух hex-хешей.

    Нечитаемый хеш считается «бесконечно далёким»: пустая строка не должна
    выглядеть как идеальное совпадение с другим пустым хешем.
    """
    try:
        return (int(first, 16) ^ int(second, 16)).bit_count()
    except (TypeError, ValueError):
        return 64


def image_info(image: Image.Image) -> dict:
    """Размеры готового `display` — их вёрстка резервирует под картинку."""
    width, height = image.size
    return {"width": width, "height": height}
