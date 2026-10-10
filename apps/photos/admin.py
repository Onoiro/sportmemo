"""Админка фотографий."""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.core.admin_mixins import (
    MODERATION_ACTIONS,
    BaseHistoryAdmin,
    ModerationActionsMixin,
)
from apps.photos.models import Photo, PhotoPerson, Source


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
class PhotoAdmin(ModerationActionsMixin, BaseHistoryAdmin):
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
        "duplicate_of",
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
        "phash",
    )
    inlines = (PhotoPersonInline,)
    actions = MODERATION_ACTIONS
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
                    "width",
                    "height",
                    "phash",
                    "duplicate_of",
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
                    "views_count",
                    "deleted_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
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
