"""Хелперы публичных выборок и пагинации.

Правило проекта: наружу отдаём только опубликованное, без удалённых и слитых
объектов. Здесь живут общие точки входа в это правило, чтобы каждая страница
не собирала цепочку `public.published()` по-своему.
"""

from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404

from apps.core.models import Sport, Status

# Размер порции галереи и порядок карточек. Общие для страницы года
# и htmx-фрагмента: разойдутся — «показать ещё» начнёт пропускать
# или повторять фотографии.
PHOTO_PAGE_SIZE = 24
PHOTO_ORDERING = ("-created_at",)


def photos_count():
    """Аннотация «сколько видимых фото привязано к объекту».

    Статус проверяется в самой аннотации: счётчик не должен учитывать
    черновики. `distinct=True` обязателен — без него `Count` по обратной
    связи размножает строки. Живёт здесь, а не в `catalog/views.py`: ту же
    аннотацию использует страница года, и общая точка не даёт двум счётчикам
    разойтись.
    """
    return Count(
        "photos",
        filter=Q(
            photos__status=Status.PUBLISHED,
            photos__deleted_at__isnull=True,
        ),
        distinct=True,
    )


def visible_photos():
    """Фото, разрешённые к публичному показу.

    Публичные выборки идут через `public`, а не через менеджер по умолчанию:
    иначе в выдачу попадут удалённые и слитые объекты.

    Модель импортируется внутри функции: `shortcuts` не должен тянуть модели
    приложений на уровне модуля, иначе легко получить цикл `core` ↔ `photos`.
    """
    from apps.photos.models import Photo

    return Photo.public.published()


def archive_stats():
    """Счётчики архива для главной и списка годов.

    «Лет» — число уникальных годов среди видимых фото, а не диапазон:
    в архиве есть провалы, и диапазон их скрывает.
    """
    from apps.catalog.models import Team

    return {
        "teams": Team.public.published().count(),
        "photos": visible_photos().count(),
        "years": visible_photos()
        .exclude(year__isnull=True)
        .values("year")
        .annotate(total=Count("year"))
        .count(),
    }


def default_sport():
    """Вид спорта по умолчанию для объектов, создаваемых пользователем.

    Продукт односпортный: выбора вида спорта в интерфейсе нет, но схема
    мультиспортная, и поле `sport` обязательное. Пока спортов несколько,
    «по умолчанию» — первый активный по порядку сортировки.

    Возвращает `None`, если активных видов спорта нет: вызывающая сторона
    должна показать понятное сообщение, а не падать на пустом значении.
    """
    return Sport.objects.filter(is_active=True).order_by("sort_order", "name").first()


def paginate(request, queryset, per_page):
    """Единая точка пагинации.

    Нечисловой или выходящий за границы номер страницы не роняет страницу:
    `Paginator.get_page` отдаёт первую или последнюю.
    """
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))


def _is_publicly_visible(obj):
    """Опубликован ли объект.

    Права на фото здесь не проверяются: ответственность за контент несёт
    автор (решение 2.1), а `rights_confirmed` — исторический флаг для
    админки.
    """
    return bool(getattr(obj, "is_published", False))


def _is_owner(obj, user):
    """Создал ли объект этот пользователь (`created_by` / `uploaded_by`)."""
    for field in ("created_by", "uploaded_by"):
        owner_id = getattr(obj, f"{field}_id", None)
        if owner_id is not None and owner_id == user.pk:
            return True
    return False


class RedirectToCanonical(Exception):
    """Объект слит с дубликатом: страница должна отдать 301 на канон.

    Исключение, а не пара «объект и адрес»: страниц, которые умеют
    редиректить слитое, пока одна, и остальные про этот исход знать
    не должны.
    """

    def __init__(self, url):
        super().__init__(url)
        self.url = url


def _canonical_or_404(model, **lookup):
    """Канон слитого объекта или `Http404`.

    Поиск идёт через `objects`, а не `public`: `merge_into()` помечает
    источник удалённым, и в `public` его уже нет. Канон проверяется тем же
    правилом, что публичная выдача, — иначе 301 вёл бы на 404. Объект без
    слияния и битая цепочка тоже дают 404: редиректить некуда.
    """
    try:
        obj = model.objects.get(**lookup)
    except model.DoesNotExist as exc:
        raise Http404("Объект не найден") from exc

    canonical = obj.get_canonical()
    if canonical is None or canonical.pk == obj.pk:
        raise Http404("Объект не найден")

    if not _is_publicly_visible(canonical) or getattr(canonical, "is_deleted", False):
        raise Http404("Объект не найден")

    return canonical


def get_visible_or_404(model, *, request=None, follow_merged=False, **lookup):
    """Отдать объект из `public` или поднять `Http404`.

    Неопубликованный объект показывается только автору и модератору: остальным
    он отдаёт 404, чтобы черновик не утекал по прямой ссылке. Модель приходит
    параметром — так `shortcuts` не импортирует модели приложений и не создаёт
    циклов между `core` и `catalog`/`photos`.

    `follow_merged=True` — для страниц, ссылку на которые могли разослать:
    слитый дубликат не исчезает, а отдаёт `RedirectToCanonical`. Флаг выключен
    по умолчанию: команда и событие по-прежнему отдают 404.
    """
    try:
        obj = model.public.get(**lookup)
    except model.DoesNotExist as exc:
        if follow_merged:
            raise RedirectToCanonical(
                _canonical_or_404(model, **lookup).get_absolute_url()
            ) from exc
        raise Http404("Объект не найден") from exc

    if _is_publicly_visible(obj):
        return obj

    user = getattr(request, "user", None)
    if (
        user is not None
        and user.is_authenticated
        and (getattr(user, "is_moderator", False) or _is_owner(obj, user))
    ):
        return obj

    raise Http404("Объект не найден")
