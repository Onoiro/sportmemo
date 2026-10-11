"""Представления публичного каталога: списки команд и событий."""

from django.db.models import Count, F, Q
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Event, Team
from apps.core.models import Status
from apps.core.shortcuts import paginate

TEAM_PAGE_SIZE = 24
EVENT_PAGE_SIZE = 20


def _photos_count():
    """Сколько видимых фото привязано к объекту.

    Права и статус проверяются в самой аннотации: счётчик не должен
    учитывать черновики и фото без подтверждённых прав. `distinct=True`
    обязателен — без него `Count` по обратной связи размножает строки.
    """
    return Count(
        "photos",
        filter=Q(
            photos__status=Status.PUBLISHED,
            photos__rights_confirmed=True,
            photos__deleted_at__isnull=True,
        ),
        distinct=True,
    )


def team_list(request):
    """Список опубликованных команд, по алфавиту."""
    teams = (
        Team.public.published()
        .select_related("sport")
        .annotate(photos_total=_photos_count())
        .order_by("name")
    )
    context = {
        "page_obj": paginate(request, teams, TEAM_PAGE_SIZE),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"label": _("Команды")},
        ],
    }
    return render(request, "catalog/team_list.html", context)


def event_list(request):
    """Список опубликованных событий: от новых к старым.

    `nulls_last=True` обязателен: в PostgreSQL при `DESC` события без года
    иначе оказались бы первыми.
    """
    events = (
        Event.public.published()
        .select_related("sport")
        .annotate(photos_total=_photos_count())
        .order_by(F("year").desc(nulls_last=True), "name")
    )
    context = {
        "page_obj": paginate(request, events, EVENT_PAGE_SIZE),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"label": _("События")},
        ],
    }
    return render(request, "catalog/event_list.html", context)
