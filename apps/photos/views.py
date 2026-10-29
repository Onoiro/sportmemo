"""Представления фотографий: загрузка, правка данных, кабинет, страница."""

from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from apps.catalog.models import Event, Team
from apps.comments.services import comments_context
from apps.core.models import Status
from apps.core.schema import compact, photo_schema
from apps.core.seo import page_meta
from apps.core.shortcuts import (
    PHOTO_ORDERING,
    PHOTO_PAGE_SIZE,
    RedirectToCanonical,
    default_sport,
    get_visible_or_404,
    paginate,
    visible_photos,
)
from apps.photos import services
from apps.photos.exif import taken_year
from apps.photos.forms import PhotoDetailsForm, PhotoFileForm
from apps.photos.models import Photo
from apps.photos.tasks import enqueue_photo_processing


def _create_photo(form, user, sport):
    """Собрать фото из формы файла и поставить обработку в очередь.

    Форма — не `ModelForm`: статус и автора нельзя брать из POST. Данные
    о снимке заполняются следующим шагом: файл не переживает переход,
    а метаданные переживают.

    Задача ставится сразу после сохранения: пока воркер считает `display`,
    человек заполняет данные, и ожидание не складывается из двух шагов.
    """
    data = form.cleaned_data
    upload = data["original"]
    photo = Photo(
        sport=sport,
        original=upload,
        status=services.initial_status_for(user),
        uploaded_by=user,
        # Имя без пути: браузер присылает его как `C:\...\photo.jpg`,
        # а поле хранится для поиска в админке.
        original_filename=Path(upload.name).name[:255],
        exif=data.get("exif", {}),
        # Инструкция обработки, а не новый файл: оригинал остаётся целым
        # (инвариант № 4). Пустой словарь — «без кропа», обычный путь.
        crop_data=data.get("crop_data", {}),
    )
    photo.save()
    enqueue_photo_processing(photo)
    return photo


def _photo_edit_initial(request, photo):
    """Начальные значения второго шага: год из EXIF и вернувшиеся привязки.

    Год из файла — только подсказка (решение 2.6 мастер-плана): он попадает
    в поле, а не в базу, и человек его подтверждает или исправляет.

    `?team=` и `?event=` приходят из формы создания команды или события:
    человек заводит запись прямо перед загрузкой фото в неё и не должен
    вводить название заново. Чужой вид спорта не подставляется — ошибку
    формы здесь показывать не за что.
    """
    initial = {}
    if not photo.year:
        year = taken_year(photo.exif or {})
        if year is not None:
            initial["year"] = year

    for param, model, field in (
        ("team", Team, "team_name"),
        ("event", Event, "event_name"),
    ):
        value = request.GET.get(param)
        if not value or not value.isdigit():
            continue
        obj = model.objects.filter(
            pk=value, sport=photo.sport, deleted_at__isnull=True
        ).first()
        if obj is not None:
            initial[field] = obj.full_name if model is Team else str(obj)
    return initial


def _get_editable_photo_or_404(request, pk):
    """Фото для правки или 404.

    Объект берётся из `Photo.public`: удалённое фото не правит никто, включая
    автора. Посторонним — 404, а не 403: не раскрываем существование чужого
    черновика (то же правило, что `can_edit` в каталоге).
    """
    try:
        photo = Photo.public.select_related("team", "event", "sport").get(pk=pk)
    except Photo.DoesNotExist as exc:
        raise Http404("Фотография не найдена") from exc

    if not services.can_edit_photo(request.user, photo):
        raise Http404("Фотография не найдена")
    return photo


@login_required
@require_http_methods(["GET", "POST"])
def photo_upload(request):
    """Первый шаг загрузки: только файл.

    Вид спорта берётся из `default_sport()`: выбора в интерфейсе нет, схема
    мультиспортная. Активных видов нет — форма недоступна: показать
    сообщение честнее, чем падать на пустом `sport` при сохранении.

    После сохранения человек попадает на второй шаг, а не в кабинет: без
    данных о снимке фото в архиве бесполезно, а человек уже настроен их
    заполнить.
    """
    sport = default_sport()

    if sport is None:
        form = None
    elif request.method == "POST":
        form = PhotoFileForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            photo = _create_photo(form, request.user, sport)
            messages.info(
                request,
                _(
                    "Файл сохранён, фотография обрабатывается. "
                    "Заполните данные о снимке — их можно изменить позже."
                ),
            )
            return redirect("photos:photo_edit", pk=photo.pk)
    else:
        form = PhotoFileForm(user=request.user)

    context = {
        "form": form,
        "sport": sport,
        "meta": page_meta(
            request,
            title=_("Загрузка фото"),
            noindex=True,
            nofollow=True,
        ),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"label": _("Загрузка фото")},
        ],
    }
    return render(request, "photos/photo_form.html", context)


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def photo_edit(request, pk):
    """Второй шаг загрузки и правка данных о снимке.

    Одна страница на обе роли: два маршрута с одинаковой формой разошлись бы
    в правилах доступа (уточнение 5 плана).

    Статус публикации правка не сбрасывает: аудит ведёт история, а модератор
    проверяет релевантность и качество заполнения, не текст (P3-14).
    `display` при правке не пересчитывается — данные о снимке не влияют
    на картинку, пересчёт появится в 3.4 вместе с кропом.
    """
    photo = _get_editable_photo_or_404(request, pk)

    if request.method == "POST":
        form = PhotoDetailsForm(request.POST, instance=photo, user=request.user)
        if form.is_valid():
            form.save()
            if photo.status == Status.PUBLISHED:
                messages.success(
                    request,
                    _("Изменения сохранены. Фото остаётся в архиве."),
                )
            else:
                messages.success(request, _("Изменения сохранены."))
            return redirect("photos:my_photos")
    else:
        form = PhotoDetailsForm(
            instance=photo,
            user=request.user,
            initial=_photo_edit_initial(request, photo),
        )

    context = {
        "photo": photo,
        "form": form,
        # Адрес возврата из форм команды и события: после создания записи
        # человек вернётся сюда же, на второй шаг.
        "edit_url": reverse("photos:photo_edit", args=[photo.pk]),
        "meta": page_meta(
            request,
            title=_("Данные фотографии"),
            noindex=True,
            nofollow=True,
        ),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"url": reverse("photos:my_photos"), "label": _("Мои фото")},
            {"label": _("Данные фотографии")},
        ],
    }
    return render(request, "photos/photo_details.html", context)


@login_required
@never_cache
@require_GET
def my_photos(request):
    """Свои фото: любые статусы, включая мягко удалённые.

    Менеджер по умолчанию, а не `public`: фото, убранное модератором,
    не должно исчезать из кабинета молча — человек увидит плашку и причину.
    Страница не кэшируется: статус меняет модератор, и устаревшая копия
    показывала бы «На проверке» после публикации.
    """
    photos = (
        Photo.objects.filter(uploaded_by=request.user)
        .select_related("team", "event")
        .order_by("-created_at")
    )
    page_obj = paginate(request, photos, PHOTO_PAGE_SIZE)
    context = {
        "photos": page_obj.object_list,
        "page_obj": page_obj,
        "meta": page_meta(
            request,
            title=_("Мои фото"),
            noindex=True,
            nofollow=True,
        ),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"label": _("Мои фото")},
        ],
    }
    return render(request, "photos/my_photos.html", context)


@require_GET
def photo_detail(request, pk):
    """Страница фотографии: крупный просмотр и метаданные.

    Фото без подтверждённых прав и черновик видны только автору и модератору:
    отсев делает `get_visible_or_404`, страница об этом не думает.

    Слитый дубликат отдаёт 301 на канон, а не 404: фотографиями делятся
    в мессенджерах, и старая ссылка не должна вести на мёртвую страницу.
    Редирект поднимается до счётчика просмотров — просмотр засчитывается
    канону, человек увидел именно его.
    """
    try:
        photo = get_visible_or_404(Photo, request=request, follow_merged=True, pk=pk)
    except RedirectToCanonical as exc:
        return redirect(exc.url, permanent=True)

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
            # Только превью: откат на оригинал означал бы, что в `og:image`
            # уходит файл с EXIF и GPS. Нет превью — общая заглушка сайта.
            image=photo.preview,
            image_alt=title,
            og_type="article",
            # Черновик открывается только автору и модератору —
            # в поиске ему делать нечего.
            noindex=not photo.is_public,
        ),
        # Разметка только у публичного фото: черновик открывается лишь
        # автору и модератору.
        "jsonld_blocks": compact(
            photo_schema(request, photo) if photo.is_public else None
        ),
        "breadcrumbs": breadcrumbs,
        **comments_context(request, photo),
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
