"""Служебные представления приложения core."""

from django.contrib.sites.shortcuts import get_current_site
from django.db import connection
from django.db.models import Count, F
from django.http import Http404, JsonResponse, QueryDict
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.core.schema import compact, website_schema
from apps.core.seo import SITE_NAME, page_meta
from apps.core.shortcuts import (
    PHOTO_ORDERING,
    PHOTO_PAGE_SIZE,
    archive_stats,
    paginate,
    photos_count,
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


@require_GET
def robots_txt(request):
    """`robots.txt` отдаёт Django, а не nginx.

    Так адрес карты сайта и список закрытых разделов лежат рядом с кодом
    и попадают в репозиторий. Домен берётся из `django.contrib.sites`,
    а не из `request.get_host()`: за сайтом стоит nginx, и адрес зависел бы
    от настроек прокси, а не от проекта.

    Это вежливая просьба, а не защита: страницу можно открыть по прямой
    ссылке. Черновики защищает `get_visible_or_404`.
    """
    site = get_current_site(request)
    sitemap_url = (
        f"{request.scheme}://{site.domain}"
        f"{reverse('django.contrib.sitemaps.views.index')}"
    )
    return render(
        request,
        "robots.txt",
        {"sitemap_url": sitemap_url},
        content_type="text/plain",
    )


def home(request):
    """Главная страница: поиск, статистика и последние фото."""
    context = {
        "stats": archive_stats(),
        "latest_photos": (
            visible_photos()
            .select_related("team", "event")
            .order_by("-created_at")[:HOME_PHOTO_LIMIT]
        ),
        # Имя сайта на главной стоит первым, а не суффиксом: «SportMemo —
        # архив спортивных фотографий» читается как название, а не как
        # страница сайта SportMemo. Описание берётся общее, из `page_meta`.
        "meta": page_meta(
            request,
            title=f"{SITE_NAME} — {_('архив спортивных фотографий')}",
            append_site_name=False,
        ),
        "jsonld_blocks": compact(website_schema(request)),
    }
    return render(request, "core/home.html", context)


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
        "meta": page_meta(
            request,
            title=_("Годы"),
            description=_(
                "Хронология архива SportMemo: годы и фотографии каждого года."
            ),
        ),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"label": _("Годы")},
        ],
    }
    return render(request, "core/year_list.html", context)


def year_detail(request, year):
    """Фотографии одного года и события этого года.

    Года без видимых фото в архиве не существует: отдаём 404, а не пустую
    страницу — иначе поисковики проиндексируют бесконечные пустые годы.
    """
    photos = visible_photos().filter(year=year).select_related("team", "event")
    if not photos.exists():
        raise Http404("Год не найден в архиве")

    # Модель импортируется внутри функции: на уровне модуля это дало бы
    # зависимость `core` → `catalog` и риск цикла (правило `shortcuts`).
    from apps.catalog.models import Event

    # Событий в году обычно немного, поэтому блок выводится без пагинации.
    events = (
        Event.public.published()
        .filter(year=year)
        .select_related("sport")
        .annotate(photos_total=photos_count())
        .order_by(F("year").desc(nulls_last=True), "name")
    )

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
        "events": events,
        "meta": page_meta(
            request,
            title=_("%(year)s год") % {"year": year},
            description=_("Фотографии %(year)s года в архиве SportMemo.")
            % {"year": year},
        ),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"url": reverse("core:year_list"), "label": _("Годы")},
            {"label": _("%(year)s год") % {"year": year}},
        ],
    }
    return render(request, "core/year_detail.html", context)
