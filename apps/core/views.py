"""Служебные представления приложения core."""

from django.db import connection
from django.db.models import Count
from django.http import Http404, JsonResponse, QueryDict
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.core.shortcuts import (
    PHOTO_ORDERING,
    PHOTO_PAGE_SIZE,
    archive_stats,
    paginate,
    visible_photos,
)

HOME_PHOTO_LIMIT = 12
# Размер порции галереи года: общий с htmx-фрагментом, иначе «показать ещё»
# подмешает фото, которых на странице не было.
YEAR_PHOTO_LIMIT = PHOTO_PAGE_SIZE


@require_GET
@never_cache
def healthz(request):
    """Проверка работоспособности: приложение и соединение с БД."""
    checks = {"app": "ok"}
    status = 200

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001 — healthcheck не должен падать
        checks["db"] = f"error: {exc.__class__.__name__}"
        status = 503

    return JsonResponse(checks, status=status)


def home(request):
    """Главная страница: поиск, статистика и последние фото."""
    context = {
        "stats": archive_stats(),
        "latest_photos": (
            visible_photos()
            .select_related("team", "event")
            .order_by("-created_at")[:HOME_PHOTO_LIMIT]
        ),
    }
    return render(request, "core/home.html", context)


def placeholder(request, *, heading, note=""):
    """Временное представление для разделов, которых ещё нет.

    Осталось только для поиска: он появляется в подэтапе 2.13.
    """
    return render(request, "core/placeholder.html", {"heading": heading, "note": note})


def year_list(request):
    """Годы архива: только те, в которых есть видимые фото.

    Источник тот же, что у счётчика «лет в архиве» на главной: иначе числа
    на двух страницах разойдутся.
    """
    years = (
        visible_photos()
        .exclude(year__isnull=True)
        .values("year")
        .annotate(total=Count("pk"))
        .order_by("-year")
    )
    context = {
        "years": years,
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"label": _("Годы")},
        ],
    }
    return render(request, "core/year_list.html", context)


def year_detail(request, year):
    """Фотографии одного года.

    Года без видимых фото в архиве не существует: отдаём 404, а не пустую
    страницу — иначе поисковики проиндексируют бесконечные пустые годы.
    """
    photos = visible_photos().filter(year=year).select_related("team", "event")
    if not photos.exists():
        raise Http404("Год не найден в архиве")

    context = {
        "year": year,
        # Порядок и размер порции обязаны совпадать с htmx-фрагментом:
        # вторая порция подгружается отдельным запросом.
        "page_obj": paginate(
            request, photos.order_by(*PHOTO_ORDERING), PHOTO_PAGE_SIZE
        ),
        # Год не лежит в `request.GET`, поэтому фильтр для кнопки
        # «показать ещё» передаётся в галерею явно.
        "gallery_query": QueryDict(f"year={year}"),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"url": reverse("core:year_list"), "label": _("Годы")},
            {"label": _("%(year)s год") % {"year": year}},
        ],
    }
    return render(request, "core/year_detail.html", context)


def search(request):
    return placeholder(
        request,
        heading=_("Поиск"),
        note=_("Здесь появится поиск по командам, событиям, людям и фотографиям."),
    )
