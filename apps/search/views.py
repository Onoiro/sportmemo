"""Страница поиска по архиву.

Страница живёт в `apps.search`, а не в `core`: `core` в проекте — нижний
слой, и ссылка из него на модели каталога дала бы обратную зависимость.
"""

from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET

from apps.core.seo import page_meta
from apps.search.indexing import build_search_query
from apps.search.services import normalize_query, search_page


@require_GET
def index(request):
    """Результаты поиска: группы карточек и подсказки.

    Черновики не показываются никому, включая автора: поиск — публичная
    выдача, и `get_visible_or_404` здесь не применяется. Иначе автор увидел бы
    свой черновик по случайному запросу и не понял, почему у остальных его нет.

    Пустой запрос поиск не выполняет: страница показывает форму и подсказку,
    что вообще можно искать. Мусорный ввод (`!!!`) даёт состояние «ничего
    не найдено» без блока подсказок: сравнивать триграммами нечего.
    """
    text = normalize_query(request.GET.get("q"))
    # Пустой запрос поиск не выполняет: страница показывает форму и подсказку,
    # что вообще можно искать. Мусорный ввод (`!!!`) доходит до `search_page`
    # и даёт состояние «ничего не найдено» без блока подсказок.
    context = {
        "q": text,
        "result": search_page(build_search_query(text), text) if text else None,
        # Страница результатов — обзорная и закрыта от индексации: адресов
        # с произвольным запросом в поиске быть не должно.
        "meta": page_meta(request, title=_("Поиск"), noindex=True),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"label": _("Поиск")},
        ],
    }
    return render(request, "search/index.html", context)
