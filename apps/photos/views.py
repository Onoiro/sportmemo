"""Представления фотографий: загрузка, кабинет автора, страница фото."""

from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from apps.catalog.models import Team
from apps.comments.services import comments_context
from apps.core.schema import compact, photo_schema
from apps.core.seo import page_meta
from apps.core.shortcuts import (
    PHOTO_ORDERING,
    PHOTO_PAGE_SIZE,
    default_sport,
    get_visible_or_404,
    paginate,
    visible_photos,
)
from apps.photos import services
from apps.photos.forms import PhotoUploadForm
from apps.photos.models import Photo


def _upload_initial(request, sport):
    """Предзаполнение формы командой из `?team=<pk>`.

    Адрес нужен для возврата из формы создания команды: человек заводит
    команду прямо перед загрузкой фото в неё и не должен вводить название
    заново. Неизвестный или чужой вид спорта просто не подставляет поле —
    ошибку формы здесь показывать не за что.
    """
    team_id = request.GET.get("team")
    if not team_id or sport is None or not team_id.isdigit():
        return {}
    team = Team.objects.filter(pk=team_id, sport=sport, deleted_at__isnull=True).first()
    return {"team_name": team.full_name} if team else {}


def _save_uploaded_photo(form, user, sport):
    """Собрать фото из формы и сразу обработать его.

    Форма — не `ModelForm`: статус и автора нельзя брать из POST. Обработка
    синхронная — в 3.3 она станет задачей Celery, но точка вызова останется
    одна, и пайплайн не раздвоится.
    """
    data = form.cleaned_data
    upload = data["original"]
    photo = Photo(
        sport=sport,
        original=upload,
        title=data["title"],
        caption=data["caption"],
        year=data["year"],
        taken_date=data["taken_date"],
        team=data["team"],
        event=data["event"],
        status=services.initial_status_for(user),
        uploaded_by=user,
        # Имя без пути: браузер присылает его как `C:\...\photo.jpg`,
        # а поле хранится для поиска в админке.
        original_filename=Path(upload.name).name[:255],
    )
    photo.save()
    services.process_photo(photo)
    return photo


@login_required
@require_http_methods(["GET", "POST"])
def photo_upload(request):
    """Загрузка фото: черновик, который публикует модератор.

    Вид спорта берётся из `default_sport()`: выбора в интерфейсе нет, схема
    мультиспортная. Активных видов нет — форма недоступна: показать
    сообщение честнее, чем падать на пустом `sport` при сохранении.
    """
    sport = default_sport()

    if sport is None:
        form = None
    elif request.method == "POST":
        form = PhotoUploadForm(
            request.POST, request.FILES, user=request.user, sport=sport
        )
        if form.is_valid():
            _save_uploaded_photo(form, request.user, sport)
            messages.success(
                request,
                _(
                    "Спасибо! Фотография успешно загружена. Она появится "
                    "в общем архиве после быстрой проверки модератором."
                ),
            )
            return redirect("photos:my_photos")
    else:
        form = PhotoUploadForm(
            user=request.user, sport=sport, initial=_upload_initial(request, sport)
        )

    context = {
        "form": form,
        "sport": sport,
        # Адрес этой же формы — адрес возврата из формы команды.
        "upload_url": reverse("photos:photo_upload"),
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
