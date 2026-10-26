"""Пайплайн обработки фотографии и правила загрузки.

Сервис знает о модели и хранилище, но вся работа с пикселями лежит
в `apps/photos/processing.py`. Здесь — порядок шагов, запись полей
и превращение ошибки в статус `failed`: иначе один битый файл уронил бы
проход бэкфилла. Наружу выходит только `StorageUnavailable` — временная
недоступность хранилища, единственный случай, который имеет смысл
повторить.

Здесь же правила загрузки: начальный статус нового фото, суточный лимит,
право на правку метаданных и резолв названия команды или события,
введённого текстом. Они общие для формы и представления, поэтому живут
в сервисе, а не в каждом из них по отдельности.
"""

import io
import logging
from datetime import timedelta

from django.core.files.base import ContentFile
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from apps.core.models import Status
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

# Сколько фото человек может загрузить за сутки. Ограничение защищает
# и сервер, и очередь модерации от одного разошедшегося альбома.
DAILY_UPLOAD_LIMIT = 50


class StorageUnavailable(Exception):
    """Хранилище временно недоступно (MinIO/S3).

    Единственная ошибка обработки, которую имеет смысл повторить: битый
    файл или превышение пикселей от повтора не исправятся, а недоступное
    хранилище — может быть. Наружу выходит только она: остальные ошибки
    сервис превращает в `failed`.
    """


def can_edit_photo(user, photo) -> bool:
    """Может ли пользователь править метаданные фотографии.

    Правит автор и модератор. Посторонним — 404, а не 403: не раскрываем
    существование чужого черновика (то же правило, что `can_edit`
    в каталоге). Удалённое фото не правит никто, включая автора, поэтому
    объект для проверки берётся из `Photo.public`.
    """
    if user is None or not user.is_authenticated:
        return False
    is_moderator = bool(getattr(user, "is_moderator", False))
    return is_moderator or photo.uploaded_by_id == user.pk


def initial_status_for(user) -> Status:
    """Статус, с которым новое фото попадает в архив.

    Одна точка правила: доверенным авторам (`User.is_trusted`, P3-10)
    понадобится другой статус, и менять его будет один подэтап, а не форма
    и представление по отдельности. `user` пока не используется — параметр
    стоит здесь ради этой точки.
    """
    return Status.DRAFT


def uploads_today(user) -> int:
    """Сколько фото автор загрузил за последние 24 часа.

    Окно скользящее, а не календарное: «в сутки» для человека означает
    «за последние сутки». Менеджер по умолчанию, а не `public`: удалённое
    модератором фото квоту не возвращает, иначе лимит обходился бы
    удалением.
    """
    since = timezone.now() - timedelta(hours=24)
    return Photo.objects.filter(uploaded_by=user, created_at__gte=since).count()


def upload_limit_reached(user) -> bool:
    """Исчерпан ли суточный лимит загрузок."""
    return uploads_today(user) >= DAILY_UPLOAD_LIMIT


def resolve_by_name(model, value, queryset, label):
    """Найти объект по названию, введённому человеком текстом.

    Названия команд не уникальны («Соколы» есть в разных городах), поэтому
    одного совпадения по `name` мало: если вхождений несколько, решает
    полная подпись объекта. Человек может ввести и полную подпись сразу —
    тогда совпадений по `name` нет вовсе, и подпись сравнивается
    с выборкой целиком. Неоднозначное название не угадывается: лучше пустое
    поле, чем фото не на той команде.
    """
    value = (value or "").strip()
    if not value:
        return None

    folded = value.casefold()
    matches = list(queryset.filter(name__iexact=value))
    if len(matches) == 1:
        return matches[0]

    # Сравнение подписей идёт в Python: `label` — свойство модели, а не поле,
    # и в базе его нет. Список команд вида спорта невелик, а резолв
    # выполняется один раз на отправку формы.
    candidates = matches or list(queryset)
    exact = [obj for obj in candidates if label(obj).casefold() == folded]
    if len(exact) == 1:
        return exact[0]
    if matches:
        logger.debug(
            "Название %r неоднозначно для %s: совпадений %s",
            value,
            model._meta.model_name,
            len(matches),
        )
    return None


def fail_photo(photo, message: str) -> Photo:
    """Записать ошибку обработки и вернуть объект.

    В `processing_error` идёт короткий текст, а не трейсбек: поле
    показывается человеку, а стек лежит в логе. Функция публичная, потому
    что задаче нужно оставить след после исчерпания ретраев: молча
    упавшая задача оставила бы фото в «Обрабатывается» навсегда.
    """
    photo.processing_status = ProcessingStatus.FAILED
    photo.processing_error = message
    photo.save(update_fields=list(PROCESSED_FIELDS))
    return photo


class _OriginalMissing(Exception):
    """Файла оригинала нет в хранилище — повтор не поможет."""


def _read_original_bytes(photo) -> bytes:
    """Прочитать файл оригинала из хранилища.

    `FileNotFoundError` — не временная ошибка: файла нет, и повтор его
    не создаст. Поэтому он превращается в обычный отказ обработки.
    Остальные `OSError` приходят от недоступного хранилища — их повторяют.
    """
    try:
        with photo.original.storage.open(photo.original.name, "rb") as handle:
            return handle.read()
    except FileNotFoundError as exc:
        logger.warning("Оригинал фото #%s не найден в хранилище", photo.pk)
        raise _OriginalMissing from exc
    except OSError as exc:
        logger.warning("Хранилище недоступно для фото #%s: %s", photo.pk, exc)
        raise StorageUnavailable(str(exc)) from exc


def _decode_original(data: bytes) -> Image.Image:
    """Полностью декодировать оригинал.

    `Image.open` ленив: битый файл падает на первом обращении к пикселям,
    а не на открытии. Поэтому `load()` вызывается здесь — иначе ошибка
    декодирования всплыла бы на ресайзе.
    """
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def process_photo(photo: Photo) -> Photo:
    """Нормализовать оригинал и заполнить производные поля.

    Повторный вызов пересчитывает `display` заново (например, после правки
    `crop_data`). Старый файл остаётся в хранилище: сборка мусора отложена
    до P3-11, а удалять файл, на который могла попасть ссылка из кэша
    мессенджера, хуже, чем оставить его лежать.

    `StorageUnavailable` не ловится: повтор — решение вызывающего, а
    команда бэкфилла пропускает такое фото и идёт дальше.
    """
    if not photo.original:
        return fail_photo(photo, "Оригинал не загружен")

    # Статус «в обработке» виден и в кабинете, и в админке. Без него фото
    # осталось бы в «Обрабатывается» и после падения воркера — не отличить
    # работающую обработку от брошенной. Пишем через `update()`: служебная
    # пометка не должна попадать в журнал истории и сдвигать `updated_at`
    # (он идёт в `lastmod` карты сайта).
    if photo.processing_status != ProcessingStatus.PROCESSING:
        photo.processing_status = ProcessingStatus.PROCESSING
        Photo.objects.filter(pk=photo.pk).update(
            processing_status=ProcessingStatus.PROCESSING
        )

    try:
        data = _read_original_bytes(photo)
        image = _decode_original(data)
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
    except _OriginalMissing:
        return fail_photo(photo, "Оригинал не найден в хранилище")
    except Image.DecompressionBombError:
        logger.exception("Фото #%s больше допустимого размера", photo.pk)
        return fail_photo(photo, "Слишком большое изображение")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.exception("Не удалось обработать фото #%s", photo.pk)
        return fail_photo(photo, f"Не удалось прочитать файл: {type(exc).__name__}")

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
