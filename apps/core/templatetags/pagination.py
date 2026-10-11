"""Тег пагинации.

`Paginator.get_elided_page_range` требует номер текущей страницы, а шаблон
не умеет вызывать методы с аргументами — поэтому окно страниц готовит тег,
а не сам партиал.
"""

from django import template

register = template.Library()


@register.inclusion_tag("partials/pagination.html", takes_context=True)
def pagination(context, page_obj):
    """Блок постраничной навигации: окно страниц вокруг текущей."""
    return {
        "request": context.get("request"),
        "page_obj": page_obj,
        "page_range": page_obj.paginator.get_elided_page_range(page_obj.number),
    }
