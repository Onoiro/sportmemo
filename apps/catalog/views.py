"""Представления публичного каталога.

Пока здесь только заглушки разделов: настоящие списки команд и событий
появляются в подэтапе 2.3.
"""

from django.utils.translation import gettext_lazy as _

from apps.core.views import placeholder


def team_list(request):
    """Заглушка `/teams/`; заменяется списком команд в подэтапе 2.3."""
    return placeholder(
        request,
        heading=_("Команды"),
        note=_("Здесь появится список команд архива с фильтрами по городу и году."),
    )


def event_list(request):
    """Заглушка `/events/`; заменяется списком событий в подэтапе 2.3."""
    return placeholder(
        request,
        heading=_("События"),
        note=_("Здесь появится список турниров, серий и товарищеских встреч."),
    )
