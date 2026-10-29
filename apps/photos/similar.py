"""Поиск похожих фотографий по перцептивному хешу.

Модуль отвечает на один вопрос — «какие фото архива похожи на это» —
и только подсказывает: дубликат это или два разных кадра, решает человек.
Отбор кандидатов, расчёт расстояния, ранжирование и подписи живут здесь,
а не в админке: страница получает готовый список, а будущая проверка
при загрузке пойдёт через ту же `similar_photos()`. Второй реализации
отбора быть не должно — иначе страница и загрузка отвечали бы по-разному.

Выборка сужается до расчёта, потому что расстояние Хэмминга не выразить
в SQL: хеш лежит hex-строкой, XOR считается в Python. Без сужения
страница тянула бы весь архив на каждое открытие.

`Photo.public` здесь не используется: черновик и скрытое фото — законные
кандидаты. Дубликат могли загрузить дважды, и оба остались черновиками;
публичность нужна публичной выдаче, а не модерации. Статус кандидата
показывает страница.
"""

import logging
from dataclasses import dataclass

from django.db.models import Q
from django.db.models.functions import Length
from django.utils.translation import gettext

from apps.photos.models import Photo
from apps.photos.processing import PHASH_HEX_LENGTH, hamming_distance

logger = logging.getLogger(__name__)

# Пороги по замерам на демо-архиве и синтетике (план подэтапа, 2.2).
# То же фото в другом масштабе, повторе JPEG, с другой яркостью или
# контрастом укладывается в 40 бит из 256, кроп 1 % — в 16; разные фото
# начинаются с медианы 104. Порог — подсказка, а не приговор: страница
# всегда показывает расстояние числом, решает человек.
LIKELY_DUPLICATE_DISTANCE = 32
SIMILAR_DISTANCE_MAX = 48

# Сколько кандидатов показываем и сколько фото просматриваем в Python.
# Пул ограничен, потому что расстояние считается в памяти, а архив растёт.
SIMILAR_LIMIT = 20
CANDIDATE_POOL_LIMIT = 5000

# Дубликат почти всегда снят в те же годы. Окно широкое, а фото без года
# попадают в пул всегда: год заполнен далеко не у всех, и дубликат без
# года иначе не нашёлся бы вовсе.
YEAR_WINDOW = 5


@dataclass(frozen=True)
class SimilarPhoto:
    """Кандидат в дубликаты: фото, расстояние и готовая подпись."""

    photo: Photo
    distance: int

    @property
    def is_likely_duplicate(self) -> bool:
        """Расстояние в пределах порога «вероятный дубликат»."""
        return self.distance <= LIKELY_DUPLICATE_DISTANCE

    @property
    def label(self) -> str:
        """Подпись близости для шаблона: числа сравнивает модуль."""
        return similarity_label(self.distance)


def similarity_label(distance: int) -> str:
    """Подпись близости по расстоянию Хэмминга.

    Строки переводятся в момент вызова, а не лениво: подпись вычисляется
    для каждого кандидата в рантайме, и `gettext_lazy` здесь не подходит.
    Дальше `SIMILAR_DISTANCE_MAX` подписи нет: такие фото в выдачу
    не попадают, а пустая строка честнее слова «похоже».
    """
    if distance <= LIKELY_DUPLICATE_DISTANCE:
        return gettext("вероятный дубликат")
    if distance <= SIMILAR_DISTANCE_MAX:
        return gettext("похоже")
    return ""


def candidate_pool(photo):
    """Кандидаты до расчёта расстояния: сужение по дешёвым полям.

    Возвращает не больше `CANDIDATE_POOL_LIMIT` последних по `created_at`
    фото и одну строку сверх предела: по ней `similar_photos()` видит,
    что пул урезан, и пишет предупреждение в лог. Считать пул отдельным
    запросом ради этой проверки незачем.
    """
    pool = (
        Photo.objects.filter(sport_id=photo.sport_id, deleted_at__isnull=True)
        # Слитое фото уже поглощено каноном: предлагать его к слиянию
        # повторно значит тянуть модератора по кругу.
        .filter(merged_into__isnull=True)
        .exclude(pk=photo.pk)
        .exclude(phash="")
        # Хеш старого формата с новым несравним: `hamming_distance()`
        # вернёт для такой пары «бесконечно далеко», и в выдачу она
        # не попадёт. Отсекаем раньше, чтобы не считать заведомый мусор.
        .annotate(hash_length=Length("phash"))
        .filter(hash_length=PHASH_HEX_LENGTH)
        .select_related("team")
    )
    if photo.year:
        pool = pool.filter(
            Q(year__range=(photo.year - YEAR_WINDOW, photo.year + YEAR_WINDOW))
            | Q(year__isnull=True)
        )
    return pool.order_by("-created_at")[: CANDIDATE_POOL_LIMIT + 1]


def similar_photos(photo, limit=SIMILAR_LIMIT) -> list[SimilarPhoto]:
    """Похожие фото, отсортированные по расстоянию.

    Фото без хеша даёт пустой список без запроса к пулу: считать нечего,
    и это не ошибка — обработка могла ещё не закончиться. Хеш старого
    формата тоже даёт пустой список: с 256-битными он несравним, и любой
    ответ был бы случайным числом.

    Сортировка — по расстоянию, при равенстве по первичному ключу: иначе
    порядок зависел бы от порядка строк в базе и менялся между запросами.
    """
    if len(photo.phash or "") != PHASH_HEX_LENGTH:
        return []

    candidates = list(candidate_pool(photo))
    if len(candidates) > CANDIDATE_POOL_LIMIT:
        logger.warning(
            "Пул кандидатов для фото #%s урезан до %s: часть архива не проверена",
            photo.pk,
            CANDIDATE_POOL_LIMIT,
        )
        candidates = candidates[:CANDIDATE_POOL_LIMIT]

    matches = []
    for candidate in candidates:
        distance = hamming_distance(photo.phash, candidate.phash)
        if distance <= SIMILAR_DISTANCE_MAX:
            matches.append(SimilarPhoto(candidate, distance))
    matches.sort(key=lambda item: (item.distance, item.photo.pk))
    return matches[:limit]
