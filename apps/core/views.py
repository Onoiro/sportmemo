"""Служебные представления приложения core."""

from django.db import connection
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.catalog.models import Team
from apps.photos.models import Photo

HOME_PHOTO_LIMIT = 12


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


def visible_photos():
    """Фото, разрешённые к публичному показу.

    Публичные выборки идут через `public`, а не через менеджер по умолчанию:
    иначе в выдачу попадут удалённые и слитые объекты.
    """
    return Photo.public.published().visible()


def archive_stats():
    """Счётчики архива для главной.

    «Лет» — число уникальных годов среди видимых фото, а не диапазон:
    в архиве есть провалы, и диапазон их скрывает.
    """
    return {
        "teams": Team.public.published().count(),
        "photos": visible_photos().count(),
        "years": visible_photos()
        .exclude(year__isnull=True)
        .values("year")
        .annotate(total=Count("year"))
        .count(),
    }


def home(request):
    """Главная страница: поиск, статистика и последние фото."""
    context = {
        "stats": archive_stats(),
        "latest_photos": visible_photos().order_by("-created_at")[:HOME_PHOTO_LIMIT],
    }
    return render(request, "core/home.html", context)


def placeholder(request, *, heading, note=""):
    """Временное представление для разделов, которых ещё нет.

    Заменяется настоящими представлениями: `catalog:team_list` и
    `catalog:event_list` — в подэтапе 2.3, `core:year_list` — там же,
    `core:search` — в подэтапе 2.13.
    """
    return render(request, "core/placeholder.html", {"heading": heading, "note": note})


def year_list(request):
    return placeholder(
        request,
        heading=_("Годы"),
        note=_(
            "Здесь появится навигация по архиву: все годы и фотографии каждого года."
        ),
    )


def search(request):
    return placeholder(
        request,
        heading=_("Поиск"),
        note=_("Здесь появится поиск по командам, событиям, людям и фотографиям."),
    )
