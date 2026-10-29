"""Админка фотографий."""

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from apps.core.admin_mixins import (
    MERGE_ACTIONS,
    MODERATION_ACTIONS,
    BaseHistoryAdmin,
    MergeActionsMixin,
    ModerationActionsMixin,
)
from apps.photos import similar
from apps.photos.models import Photo, PhotoPerson, Source
from apps.photos.processing import PHASH_HEX_LENGTH, normalize_crop_data
from apps.photos.tasks import enqueue_photo_processing

# Ориентации EXIF, при которых кадр зеркалится. Браузер отражает картинку
# сам, а проценты рамки считаются от транспонированного кадра: стороны
# совпадают, а отражение — нет.
MIRRORED_ORIENTATIONS = frozenset({2, 4, 5, 7})


def _percent(value, total) -> float:
    """Доля от стороны кадра в процентах, обрезанная по 0–100."""
    if not total:
        return 0.0
    return round(min(max(value / total * 100, 0.0), 100.0), 2)


def _crop_box_in_frame(crop_data: dict):
    """Координаты кропа в системе исходного кадра и его размер.

    `crop_data` хранит рамку в системе «как видит человек» — то есть уже
    повёрнутой, если человек нажал «повернуть». В админке показывается
    оригинал, который браузер разворачивает только по EXIF, а не по нашему
    повороту. Без обратного пересчёта рамка на повёрнутом фото стояла бы
    поперёк кадра и врала модератору.

    `source_width`/`source_height` описывают повёрнутый кадр, поэтому для
    поворота на 90/270 стороны меняются местами ещё раз.
    """
    frame_width = crop_data.get("source_width")
    frame_height = crop_data.get("source_height")
    if not frame_width or not frame_height:
        return None
    rotate = crop_data.get("rotate", 0)
    if rotate % 180:
        frame_width, frame_height = frame_height, frame_width

    left = crop_data["x"]
    top = crop_data["y"]
    width = crop_data["width"]
    height = crop_data["height"]
    # Обратный поворот по часовой: точка (x, y) повёрнутого кадра пришла
    # из (y, H - x) исходного.
    if rotate == 90:
        left, top, width, height = top, frame_height - left - width, height, width
    elif rotate == 180:
        left = frame_width - left - width
        top = frame_height - top - height
    elif rotate == 270:
        left, top, width, height = frame_width - top - height, left, height, width
    return left, top, width, height, frame_width, frame_height


def _mirrored(obj) -> bool:
    """Кадр зеркалится EXIF-ориентацией: проценты рамки тогда неточны."""
    try:
        orientation = int(obj.exif.get("Orientation", 1))
    except (AttributeError, TypeError, ValueError):
        return False
    return orientation in MIRRORED_ORIENTATIONS


class PhotoPersonInline(admin.TabularInline):
    model = PhotoPerson
    extra = 0
    autocomplete_fields = ("person",)
    fields = ("person", "role", "position", "note")


@admin.register(Source)
class SourceAdmin(BaseHistoryAdmin):
    list_display = (
        "title",
        "kind",
        "publication",
        "date",
        "holder",
        "owner",
        "deleted_at",
    )
    list_filter = ("kind", "date", "deleted_at")
    search_fields = ("title", "publication", "holder", "notes", "url")
    autocomplete_fields = ("owner",)
    readonly_fields = ("created_at", "updated_at", "deleted_at")


@admin.register(Photo)
class PhotoAdmin(MergeActionsMixin, ModerationActionsMixin, BaseHistoryAdmin):
    list_display = (
        "title",
        "year",
        "team",
        "event",
        "status",
        "processing_status",
        "rights_confirmed",
        "uploaded_by",
    )
    list_filter = (
        "sport",
        "status",
        "processing_status",
        "rights_confirmed",
        "year",
        "deleted_at",
    )
    search_fields = ("title", "caption", "phash", "team__name", "event__name")
    autocomplete_fields = (
        "sport",
        "source",
        "event",
        "team",
        "merged_into",
        "uploaded_by",
    )
    readonly_fields = (
        "moderated_by",
        "moderated_at",
        "created_at",
        "updated_at",
        "deleted_at",
        "views_count",
        "width",
        "height",
        "similar_photos",
        "phash",
        "crop_overlay",
        "crop_data",
        "merged_into",
    )
    inlines = (PhotoPersonInline,)
    actions = (*MERGE_ACTIONS, *MODERATION_ACTIONS, "action_reprocess")
    date_hierarchy = "created_at"
    fieldsets = (
        (None, {"fields": ("sport", "title", "caption", "original")}),
        (_("Привязка"), {"fields": ("source", "event", "team", "year", "taken_date")}),
        (
            _("Производные"),
            {
                "fields": (
                    "processed",
                    "display",
                    "crop_overlay",
                    "crop_data",
                    "width",
                    "height",
                    "similar_photos",
                    "phash",
                )
            },
        ),
        (
            _("Обработка"),
            {"fields": ("processing_status", "processing_error")},
        ),
        (
            _("Права"),
            {"fields": ("rights_confirmed", "rights_confirmed_at", "uploaded_by")},
        ),
        (
            _("Публикация"),
            {"fields": ("status", "moderated_by", "moderated_at", "moderation_reason")},
        ),
        (
            _("Служебное"),
            {
                "fields": (
                    "exif",
                    "original_filename",
                    "views_count",
                    "merged_into",
                    "deleted_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def merge_candidates(self, queryset, sources):
        """Цели слияния — только фото того же вида спорта.

        Слияние фото разных видов спорта сделало бы канон чужим для
        команды и события: вид спорта в схеме мультиспортный, а карточка
        показывает связи своего вида. Хук вызывается и при отрисовке
        страницы, и при проверке POST.
        """
        sport_ids = {source.sport_id for source in sources}
        return (
            super().merge_candidates(queryset, sources).filter(sport_id__in=sport_ids)
        )

    @admin.display(description=_("Похожие фото"))
    def similar_photos(self, obj):
        """Ссылка на страницу поиска дубликатов.

        Счётчика «найдено N» здесь нет осознанно: он стоил бы прохода
        по пулу кандидатов при каждом открытии карточки, а карточку
        модератор открывает часто. Хочет узнать — нажимает ссылку.
        """
        if obj is None or obj.pk is None:
            # Страница добавления: у несуществующего фото ссылки нет.
            return ""
        url = reverse("admin:photos_photo_similar", args=[obj.pk])
        return format_html('<a href="{}">{}</a>', url, _("Показать похожие фото"))

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "similar/<int:photo_id>/",
                self.admin_site.admin_view(self.similar_view),
                name="photos_photo_similar",
            ),
        ]
        # Свои адреса идут первыми: иначе `similar/` перехватил бы
        # стандартный маршрут карточки `<path:object_id>/change/`.
        return custom + urls

    def similar_view(self, request, photo_id):
        """Страница «Похожие фото»: только показывает, ничего не меняет."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        photo = self.get_object(request, photo_id)
        if photo is None:
            raise Http404
        context = {
            **self.admin_site.each_context(request),
            "title": _("Похожие фото"),
            "opts": self.model._meta,
            "original": photo,
            "similar": similar.similar_photos(photo),
            "hash_missing": len(photo.phash or "") != PHASH_HEX_LENGTH,
        }
        return TemplateResponse(request, "admin/photos/photo/similar.html", context)

    @admin.display(description=_("Рамка кропа"))
    def crop_overlay(self, obj):
        """Показать, какой участок оригинала попал в `display`.

        Рамка рисуется поверх оригинала: модератору нужно видеть, что
        осталось за кадром, а `display` этого не показывает — там уже
        вырезанный участок.

        Браузер сам применяет EXIF-ориентацию к `<img>`, поэтому проценты
        считаются от транспонированного кадра. При зеркальной ориентации
        (2/4/5/7) рамка может быть неточной: отражение по процентам
        не выразить — об этом стоит сказать прямо, а не показывать
        неверную картинку молча.
        """
        if obj is None:
            # Страница добавления: показывать нечего, а `obj.original`
            # у несуществующего фото нет.
            return ""
        if not obj.original:
            return _("Оригинал не загружен")
        # Правило «что считается кропом» одно — то же, что у формы и сервиса:
        # мусор в поле не должен ломать страницу админки.
        crop_data = normalize_crop_data(obj.crop_data)
        if not crop_data:
            return _("Без кропа: показывается весь кадр")

        box = _crop_box_in_frame(crop_data)
        if box is None:
            # `source_width`/`source_height` появились в версии 1 формата:
            # у старых записей их нет, и процент посчитать не от чего.
            return _("Рамка недоступна: в данных кропа нет размера кадра")
        left, top, width, height, frame_width, frame_height = box

        frame_style = format_html(
            "position:absolute;"
            "left:{}%;top:{}%;width:{}%;height:{}%;"
            "border:2px solid var(--body-quiet-color, #666);"
            "box-shadow:0 0 0 9999px rgba(0, 0, 0, 0.45);"
            "pointer-events:none;",
            _percent(left, frame_width),
            _percent(top, frame_height),
            _percent(width, frame_width),
            _percent(height, frame_height),
        )
        notes = [_("Затемнено то, что осталось за кадром.")]
        if crop_data.get("rotate"):
            notes.append(
                _("Кадр повёрнут на %(degrees)s°.") % {"degrees": crop_data["rotate"]}
            )
        if _mirrored(obj):
            notes.append(_("Зеркальная ориентация: рамка может быть неточной."))

        return format_html(
            '<div style="position:relative;display:inline-block;'
            'max-width:40rem;overflow:hidden;line-height:0;">'
            '<img src="{}" alt="" style="max-width:100%;height:auto;">'
            '<div style="{}"></div>'
            "</div>"
            '<p style="margin:0.5rem 0 0;">{}</p>',
            obj.original.url,
            frame_style,
            format_html_join(" ", "{}", ((note,) for note in notes)),
        )

    @admin.action(description=_("Обработать заново"))
    def action_reprocess(self, request, queryset):
        """Пересчитать производные поля выбранных фото.

        Единственный способ починить фото, у которого обработка упала или
        «залипла» после падения воркера: в кабинете автора такой кнопки нет
        осознанно, а автоматическая проверка залипших задач появится вместе
        с beat.
        """
        queued = 0
        for photo in queryset:
            # Удалённое фото задача пропустила бы, и человек решил бы,
            # что действие сработало.
            if photo.is_deleted:
                continue
            enqueue_photo_processing(photo)
            queued += 1
        self.message_user(
            request,
            _("Поставлено в обработку: %(count)d.") % {"count": queued},
            messages.SUCCESS,
        )


@admin.register(PhotoPerson)
class PhotoPersonAdmin(BaseHistoryAdmin):
    list_display = ("person", "photo", "role", "position", "added_by", "added_at")
    list_filter = ("role",)
    search_fields = (
        "person__last_name",
        "person__first_name",
        "photo__title",
        "position",
    )
    autocomplete_fields = ("photo", "person", "added_by")
