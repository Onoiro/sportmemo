"""Админка модерации."""

from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.admin_mixins import BaseHistoryAdmin
from apps.moderation.models import ModerationAction, Report, ReportStatus


@admin.register(Report)
class ReportAdmin(BaseHistoryAdmin):
    list_display = (
        "__str__",
        "reporter",
        "reason",
        "status",
        "content_type",
        "object_id",
        "created_at",
    )
    list_filter = ("status", "reason", "content_type", "created_at")
    search_fields = ("comment", "resolution", "reporter__email")
    autocomplete_fields = ("reporter", "resolved_by")
    readonly_fields = (
        "content_type",
        "object_id",
        "created_at",
        "updated_at",
        "resolved_at",
        "resolved_by",
    )
    actions = ("action_resolve", "action_reject_report")

    @admin.action(description=_("Отметить решёнными"))
    def action_resolve(self, request, queryset):
        updated = queryset.update(
            status=ReportStatus.RESOLVED,
            resolved_by=request.user,
            resolved_at=timezone.now(),
        )
        self.message_user(request, _("Решено жалоб: %(count)d") % {"count": updated})

    @admin.action(description=_("Отклонить жалобы"))
    def action_reject_report(self, request, queryset):
        updated = queryset.update(
            status=ReportStatus.REJECTED,
            resolved_by=request.user,
            resolved_at=timezone.now(),
        )
        self.message_user(request, _("Отклонено жалоб: %(count)d") % {"count": updated})


@admin.register(ModerationAction)
class ModerationActionAdmin(BaseHistoryAdmin):
    list_display = ("moderator", "action", "content_type", "object_id", "created_at")
    list_filter = ("action", "content_type", "created_at", "moderator")
    search_fields = ("reason", "moderator__email")
    autocomplete_fields = ("moderator",)
    readonly_fields = (
        "moderator",
        "action",
        "content_type",
        "object_id",
        "reason",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        # Журнал заполняется действиями модераторов, а не вручную.
        return False
