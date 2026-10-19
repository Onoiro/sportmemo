"""Сборка SEO-метатегов страниц: title, description, canonical, Open Graph.

Метатеги собираются в представлениях, а не в шаблонах. Причина простая:
картинка карточки зависит от данных страницы (у фото одно поле, у команды
другое, у человека третье), а правило приватности имени («скрытую фамилию
не показываем») должно жить в одном месте. Логика, размазанная по шаблонам,
рано или поздно дала бы утечку фамилии или битую картинку на одной странице.

Представление передаёт в контекст готовый словарь `meta`, а партиал
`partials/meta.html` только выводит значения.
"""

from django.conf import settings
from django.http import HttpRequest
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

SITE_NAME = "SportMemo"
DEFAULT_DESCRIPTION = _("Публичный архив спортивных фотографий: команды, события, люди")
# Заглушка для страниц без своей картинки: списков, годов, главной.
OG_DEFAULT_IMAGE = "img/og-default.png"
OG_DEFAULT_IMAGE_SIZE = (1200, 630)
DESCRIPTION_LIMIT = 160

# `og:locale` требует полную форму с регионом, а `LANGUAGE_CODE` — короткую.
OG_LOCALES = {"ru": "ru_RU", "en": "en_US"}


def absolute(request: HttpRequest, url: str) -> str:
    """Абсолютный URL: мессенджеры и поисковики относительные не понимают."""
    if url.startswith(("http://", "https://")):
        return url
    return request.build_absolute_uri(url)


def canonical_url(request: HttpRequest, query=None) -> str:
    """Адрес страницы без параметра `page`.

    Значимые фильтры (`?year=1994`) сохраняются: у отфильтрованной выдачи
    свой адрес. Номер страницы выбрасывается: `canonical` всегда указывает
    на первую страницу — иначе каждая страница пагинации объявила бы себя
    главной, и поисковик не смог бы выбрать между ними.

    `query` передают страницы, которые сами решают, какие параметры значимы
    (списки каталога): мусор вида `?year=abc` не должен создавать отдельный
    адрес одной и той же выдачи. `None` — прежнее поведение: весь
    `request.GET`.
    """
    query = (request.GET if query is None else query).copy()
    query.pop("page", None)
    if not query:
        return request.path
    return f"{request.path}?{query.urlencode()}"


def is_paginated_beyond_first(request: HttpRequest) -> bool:
    """Признак «это не первая страница списка»."""
    try:
        return int(request.GET.get("page", 1)) > 1
    except (TypeError, ValueError):
        return False


def _clip(text: str) -> str:
    """Описание одной строкой, не длиннее `DESCRIPTION_LIMIT`.

    Переносы строк и двойные пробелы схлопываются: в атрибуте `content`
    они выглядят как случайный мусор.
    """
    text = " ".join(str(text).split())
    if len(text) <= DESCRIPTION_LIMIT:
        return text
    return text[: DESCRIPTION_LIMIT - 1].rstrip() + "…"


def image_meta(request: HttpRequest, image, alt: str = "") -> dict | None:
    """Картинка карточки: абсолютный URL и размеры, или `None`.

    Работает и с `ImageField`, и с `ImageSpecField`: у обоих есть `url`,
    `width` и `height`. Недоступный или битый файл даёт `None`, и вызывающая
    сторона подставит заглушку — карточка без картинки лучше падения
    страницы.
    """
    if not image:
        return None
    try:
        return {
            "url": absolute(request, image.url),
            "alt": str(alt),
            "width": image.width,
            "height": image.height,
        }
    except (OSError, ValueError):
        return None


def _default_image_meta(request: HttpRequest) -> dict:
    """Заглушка `og:image` для страниц без своей картинки.

    Адрес собирается из `STATIC_URL`, а не через `{% static %}`: тег отдаёт
    имя с хэшем содержимого, и после каждого деплоя адрес картинки менялся бы.
    Мессенджеры держат карточку ссылки в кэше и ходят за картинкой по
    сохранённому адресу — стабильный путь работает лучше хэшированного.
    """
    return {
        "url": absolute(
            request, f"/{settings.STATIC_URL.lstrip('/')}{OG_DEFAULT_IMAGE}"
        ),
        "alt": str(_("архив спортивных фотографий")),
        "width": OG_DEFAULT_IMAGE_SIZE[0],
        "height": OG_DEFAULT_IMAGE_SIZE[1],
    }


def og_locale() -> str:
    """Локаль карточки в формате `og:locale`."""
    code = (get_language() or "").split("-")[0]
    return OG_LOCALES.get(code, "ru_RU")


def page_meta(
    request: HttpRequest,
    *,
    title: str = "",
    description: str = "",
    image=None,
    image_alt: str = "",
    og_type: str = "website",
    noindex: bool = False,
    nofollow: bool = False,
    append_site_name: bool = True,
    canonical_query=None,
) -> dict:
    """Полный словарь метатегов страницы.

    Словарь всегда полный: партиал не умеет добирать недостающее, а пустой
    `og:title` в разметке выглядит хуже, чем отсутствие блока. Поэтому
    вызывающая сторона передаёт только смысловые значения, а заголовок,
    описание, картинку и `canonical` досчитывает эта функция.

    `append_site_name=False` нужен главной: там имя сайта стоит первым
    («SportMemo — архив спортивных фотографий»), а не в конце.
    `canonical_query` — нормализованные параметры для страниц, которые
    отбрасывают мусор из адреса (списки каталога).
    """
    if not title:
        title = SITE_NAME
    elif append_site_name:
        title = f"{title} — {SITE_NAME}"

    description = _clip(description) if description else str(DEFAULT_DESCRIPTION)
    # Карточка ведёт на первую страницу списка: страницы пагинации
    # закрыты от индексации.
    canonical = absolute(request, canonical_url(request, query=canonical_query))
    if is_paginated_beyond_first(request):
        noindex = True

    image_data = image_meta(request, image, alt=image_alt) or _default_image_meta(
        request
    )

    return {
        "title": title,
        "description": description,
        "canonical": canonical,
        "noindex": noindex,
        "nofollow": nofollow,
        "og_site_name": SITE_NAME,
        "og_locale": og_locale(),
        "og_type": og_type,
        "og_title": title,
        "og_description": description,
        "og_url": canonical,
        "og_image": image_data["url"],
        "og_image_alt": image_data["alt"],
        "og_image_width": image_data["width"],
        "og_image_height": image_data["height"],
    }
