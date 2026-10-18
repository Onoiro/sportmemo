"""Админка комментариев и настройки премодерации."""

from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.comments.models import Comment, CommentSettings
from apps.core.admin_mixins import (
    MODERATION_ACTIONS,
    BaseHistoryAdmin,
    ModerationActionsMixin,
)


@admin.register(Comment)
class CommentAdmin(ModerationActionsMixin, BaseHistoryAdmin):
    list_display = (
        "__str__",
        "author",
        "content_type",
        "object_id",
        "status",
        "created_at",
        "deleted_at",
    )
    list_filter = ("status", "content_type", "deleted_at", "created_at")
    search_fields = ("text", "author__email")
    autocomplete_fields = ("author", "parent")
    readonly_fields = (
        "content_type",
        "object_id",
        "created_at",
        "updated_at",
        "deleted_at",
    )
    actions = MODERATION_ACTIONS
    fieldsets = (
        (None, {"fields": ("author", "text", "parent")}),
        (_("Объект"), {"fields": ("content_type", "object_id")}),
        (
            _("Публикация"),
            {"fields": ("status", "moderated_by", "moderated_at", "moderation_reason")},
        ),
        (_("Служебное"), {"fields": ("deleted_at", "created_at", "updated_at")}),
    )


@admin.register(CommentSettings)
class CommentSettingsAdmin(admin.ModelAdmin):
    """Настройка премодерации: одна строка, править — только её.

    Добавлять и удалять записи нельзя: две строки настроек означали бы, что
    неизвестно, какая из них действует. Список сразу ведёт на форму правки —
    модератор увидел бы список из одной записи и не понял, что делать.
    """

    fields = ("premoderation",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        settings_obj = CommentSettings.objects.order_by("pk").first()
        if settings_obj is None:
            # Строку создаёт data-миграция, но база могла её потерять —
            # падать из-за этого админка не должна.
            settings_obj = CommentSettings.objects.create()
        return HttpResponseRedirect(
            reverse("admin:comments_commentsettings_change", args=[settings_obj.pk])
        )
