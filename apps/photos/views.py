"""Представления фотографий: страница фото и фрагмент галереи."""

from django.db.models import F
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET

from apps.core.seo import page_meta
from apps.core.shortcuts import (
    PHOTO_ORDERING,
    PHOTO_PAGE_SIZE,
    get_visible_or_404,
    paginate,
    visible_photos,
)
from apps.photos.models import Photo


@require_GET
def photo_detail(request, pk):
    """Страница фотографии: крупный просмотр и метаданные.

    Фото без подтверждённых прав и черновик видны только автору и модератору:
    отсев делает `get_visible_or_404`, страница об этом не думает.
    """
    photo = get_visible_or_404(Photo, request=request, pk=pk)

    # Хелпер отдаёт объект без предвыборки связей — перечитываем один раз
    # с `select_related`, чтобы шаблон не делал запрос на каждый источник.
    photo = (
        Photo.public.select_related("source", "team", "event")
        .prefetch_related("photo_persons__person")
        .get(pk=photo.pk)
    )

    # Просмотры растут атомарным `update`, а не `save`: иначе каждое открытие
    # страницы добавляло бы запись в `photo.history`. Число в памяти правим
    # руками — `update` объект не обновляет.
    Photo.objects.filter(pk=photo.pk).update(views_count=F("views_count") + 1)
    photo.views_count += 1

    breadcrumbs = [{"url": reverse("core:home"), "label": _("Главная")}]
    if photo.year:
        breadcrumbs += [
            {"url": reverse("core:year_list"), "label": _("Годы")},
            {
                "url": reverse("core:year_detail", args=[photo.year]),
                "label": _("%(year)s год") % {"year": photo.year},
            },
        ]
    breadcrumbs.append({"label": photo.title or _("Фотография")})

    # Заголовок и подпись фото — авторский текст, он и попадает в карточку.
    # Обрезку до 160 символов и абсолютные адреса делает `page_meta`.
    title = photo.title or str(_("Фотография"))
    context = {
        "photo": photo,
        "photo_persons": photo.photo_persons.all(),
        "meta": page_meta(
            request,
            title=title,
            description=photo.caption
            or photo.title
            or _("Фотография из архива SportMemo"),
            image=photo.preview if photo.preview else photo.original,
            image_alt=title,
            og_type="article",
            # Черновик и фото без подтверждённых прав открываются только
            # автору и модератору — в поиске им делать нечего.
            noindex=not photo.is_public,
        ),
        "breadcrumbs": breadcrumbs,
    }
    return render(request, "photos/photo_detail.html", context)


@require_GET
def grid(request):
    """Фрагмент галереи для htmx: следующая порция карточек.

    Отдаёт только карточки и контейнер кнопки, без `<html>`: htmx дописывает
    их в конец сетки. Без JS тот же адрес открывается как обычная страница
    с одной порцией фото — ссылка на кнопке ведёт сюда же.
    """
    photos = visible_photos().select_related("team", "event")

    year = request.GET.get("year")
    if year:
        try:
            photos = photos.filter(year=int(year))
        except ValueError:
            # Опечатка в ссылке не должна отдавать весь архив.
            photos = photos.none()

    team = request.GET.get("team")
    if team:
        photos = photos.filter(team__slug=team)

    event = request.GET.get("event")
    if event:
        photos = photos.filter(event__slug=event)

    person = request.GET.get("person")
    if person:
        photos = photos.filter(photo_persons__person__slug=person)

    page_obj = paginate(request, photos.order_by(*PHOTO_ORDERING), PHOTO_PAGE_SIZE)
    context = {
        "photos": page_obj.object_list,
        "page_obj": page_obj,
        # Фильтры фрагмента — те же, что в адресе: их несёт `request.GET`,
        # и партиал сам возьмёт их для следующей ссылки.
        "gallery_query": request.GET,
        # Фрагмент подменяет контейнер кнопки целиком — так она исчезает,
        # когда фотографии закончились.
        "oob": True,
    }
    return render(request, "photos/partials/grid_items.html", context)
