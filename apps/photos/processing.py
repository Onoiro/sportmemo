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

# Формат проверяется по содержимому файла, а не по расширению: переименованный
# `.pdf` не должен пройти, а HEIC без расширения — пройти. HEIF открывает
# `pillow-heif`, который регистрируется в `PhotosConfig.ready()`.
ALLOWED_UPLOAD_FORMATS = ("JPEG", "PNG", "WEBP", "HEIF")

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

# Размер матрицы DCT для перцептивного хеша и размер итоговой сетки:
# 16 × 16 бит = 256 бит = 64 hex-символа. Формат хеша знают снаружи
# (поиск похожих, валидация в админке), поэтому длина вынесена в константу.
_PHASH_DCT_SIZE = 64
_PHASH_GRID = 16
PHASH_HEX_LENGTH = _PHASH_GRID * _PHASH_GRID // 4


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


def _as_int(value):
    """Привести значение из JSON к int: браузер присылает float."""
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _normalize_rotate(value) -> int:
    """Свести угол к 0/90/180/270: других поворотов сервер не умеет."""
    degrees = _as_int(value)
    if degrees is None or degrees % 90 != 0:
        return 0
    return degrees % 360


def _crop_box(crop_data: dict, size=None):
    """Координаты кропа после клампинга по границам кадра.

    Без `size` координаты только проверяются: размер полного кадра
    известен не всем вызывающим, а формат проверить нужно всем.
    """
    left = _as_int(crop_data.get("x"))
    top = _as_int(crop_data.get("y"))
    width = _as_int(crop_data.get("width"))
    height = _as_int(crop_data.get("height"))
    if left is None or top is None or width is None or height is None:
        logger.warning("crop_data без числовых координат, кроп пропущен")
        return None

    # Отрицательные координаты — мусор в любом случае: `Image.crop`
    # заполнил бы вышедшую за кадр часть чёрным.
    left = max(0, left)
    top = max(0, top)
    if size is not None:
        frame_width, frame_height = size
        left = min(left, frame_width)
        top = min(top, frame_height)
        width = max(0, min(left + width, frame_width)) - left
        height = max(0, min(top + height, frame_height)) - top

    if width < MIN_CROP_SIDE or height < MIN_CROP_SIDE:
        logger.warning("crop_data меньше %s px, кроп пропущен", MIN_CROP_SIDE)
        return None
    return (left, top, width, height)


def normalize_crop_data(crop_data, size=None) -> dict:
    """Единственное правило «что считается кропом».

    Форма отдаёт сюда данные из браузера, сервис — то, что лежит в базе:
    правило должно быть одно, иначе форма принимала бы кроп, который
    сервис молча выбрасывает. Мусор (не словарь, чужая версия,
    нечисловые координаты, кроп меньше 100 px) — «без кропа»
    с предупреждением в лог, а не исключение: данные приходят из браузера.
    """
    if not crop_data:
        return {}
    if not isinstance(crop_data, dict):
        logger.warning("crop_data не словарь, кроп пропущен")
        return {}
    version = crop_data.get("version")
    if version != CROP_VERSION:
        logger.warning("crop_data версии %r, кроп пропущен", version)
        return {}

    box = _crop_box(crop_data, size)
    if box is None:
        return {}

    left, top, width, height = box
    result = {
        "version": CROP_VERSION,
        "x": left,
        "y": top,
        "width": width,
        "height": height,
        "rotate": _normalize_rotate(crop_data.get("rotate")),
    }
    # Размер полного кадра нужен только рамке в админке: сервер считает
    # от реального файла, а присланному числу не доверяет.
    for key in ("source_width", "source_height"):
        value = _as_int(crop_data.get(key))
        if value is not None:
            result[key] = value
    return result


def scale_crop_data(crop_data, factor, size=None) -> dict:
    """Пересчитать координаты кропа в другую систему координат.

    `crop_data` хранит координаты полного кадра «как видит человек»:
    сервер кроит рабочую копию, и без масштаба кроп больше 2500 px
    вырезал бы не тот участок. `MIN_CROP_SIDE` проверяется уже после
    масштаба: кроп в 150 px полного кадра на копии — мусор.
    """
    normalized = normalize_crop_data(crop_data)
    if not normalized:
        return {}
    try:
        factor = float(factor)
    except (TypeError, ValueError, OverflowError):
        logger.warning("Масштаб %r не число, кроп пропущен", factor)
        return {}
    if factor <= 0:
        logger.warning("Масштаб %r не положителен, кроп пропущен", factor)
        return {}

    scaled = dict(normalized)
    for key in ("x", "y", "width", "height"):
        scaled[key] = int(round(normalized[key] * factor))
    return normalize_crop_data(scaled, size=size)


def oriented_size(image: Image.Image) -> tuple[int, int]:
    """Размер кадра «как видит человек» — после EXIF-ориентации.

    `exif_transpose` дал бы тот же ответ, но на 60 Мпикс он копирует
    всё изображение: для размера достаточно прочитать один тег.
    """
    width, height = image.size
    try:
        raw = image.getexif().get(274)
    except (AttributeError, OSError) as exc:
        logger.warning("EXIF не читается (%s), размер берём как есть", exc)
        return width, height
    if raw is None:
        return width, height
    orientation = _as_int(raw)
    if orientation is None:
        logger.warning("Битый EXIF Orientation %r, размер берём как есть", raw)
        return width, height
    # 5/6/7/8 — поворот на 90 или 270 градусов: стороны меняются местами.
    if orientation in (5, 6, 7, 8):
        return height, width
    return width, height


def apply_crop(image: Image.Image, crop_data) -> Image.Image:
    """Вырезать участок по `crop_data`.

    Правила «что считается кропом» живут в `normalize_crop_data()`:
    второй набор в этой функции разошёлся бы с формой.
    """
    data = normalize_crop_data(crop_data, size=image.size)
    if not data:
        return image
    box = (
        data["x"],
        data["y"],
        data["x"] + data["width"],
        data["y"] + data["height"],
    )
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


_DCT = _dct_matrix(_PHASH_DCT_SIZE)


def perceptual_hash(image: Image.Image) -> str:
    """Перцептивный хеш 256 бит в hex (64 символа).

    Считается от `display`, а не от оригинала: дубликаты ищутся по тому,
    что видит человек. Схема классическая (pHash): уменьшить до 64×64,
    перевести в оттенки серого, взять DCT, оставить левый верхний блок
    16×16 (низкие частоты), сравнить каждое значение с медианой блока.

    Битность поднята с 64 до 256 по замерам на демо-архиве: медиана
    расстояния между разными фото выросла с 37 % до 41 %, а однотонное
    красное и однотонное синее стали ближе по доле бит (9 % → 6 %).

    **Известное ограничение:** монотонные цветовые заливки алгоритм
    не различает. У такой картинки энергия DCT есть только в нулевой
    частоте, остальные биты получаются из шума округления, и красное
    с синим дают 16 из 256 — меньше порога «вероятный дубликат».
    Порог снижать нельзя: пережатый JPEG-дубликат того же снимка даёт
    до 40 из 256 и перестал бы находиться. Для архива ограничение
    не важно — заливок в нём нет, а решение всё равно принимает человек.
    """
    small = image.convert("L").resize(
        (_PHASH_DCT_SIZE, _PHASH_DCT_SIZE), Image.Resampling.LANCZOS
    )
    pixels = np.asarray(small, dtype=np.float64)
    dct = _DCT @ pixels @ _DCT.T
    low = dct[:_PHASH_GRID, :_PHASH_GRID].flatten()
    bits = low > np.median(low)

    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:0{PHASH_HEX_LENGTH}x}"


def hamming_distance(first: str, second: str) -> int:
    """Сколько бит различается у двух hex-хешей.

    Нечитаемый хеш считается «бесконечно далёким»: пустая строка не должна
    выглядеть как идеальное совпадение с другим пустым хешем.

    Хеши разной длины несравнимы: 16-символьный хеш старого алгоритма
    и 64-символьный новый дают случайное число бит, а не «сколько бит
    из 256». Поэтому такой пары считаем максимально далёкой — длину
    большего хеша в битах. Без этого правила страница похожих показала бы
    мусор, и заметить это было бы нечем.
    """
    try:
        first_length = len(first)
        second_length = len(second)
    except TypeError:
        return PHASH_HEX_LENGTH * 4
    if first_length != second_length:
        logger.warning(
            "Хеши разной длины (%s и %s), сравнение невозможно",
            first_length,
            second_length,
        )
        return max(first_length, second_length) * 4
    try:
        return (int(first, 16) ^ int(second, 16)).bit_count()
    except (TypeError, ValueError):
        return PHASH_HEX_LENGTH * 4


def image_info(image: Image.Image) -> dict:
    """Размеры готового `display` — их вёрстка резервирует под картинку."""
    width, height = image.size
    return {"width": width, "height": height}
