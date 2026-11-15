"""Админка модерации."""

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from apps.core.admin_mixins import BaseHistoryAdmin
from apps.moderation import services
from apps.moderation.forms import ReportResolutionForm
from apps.moderation.models import ModerationAction, Report


@admin.register(Report)
class ReportAdmin(BaseHistoryAdmin):
    list_display = (
        "__str__",
        "reporter",
        "reason",
        "sport",
        "status",
        "content_type",
        "object_id",
        "created_at",
    )
    list_filter = ("status", "reason", "sport", "content_type", "created_at")
    search_fields = ("comment", "resolution", "reporter__email")
    autocomplete_fields = ("reporter", "resolved_by")
    readonly_fields = (
        "content_type",
        "object_id",
        # Вид спорта производный: заполняется из цели при подаче жалобы.
        "sport",
        "created_at",
        "updated_at",
        # Решение принимает сервис: он пишет журнал и ставит письмо
        # заявителю. Правка этих полей руками обошла бы и то, и другое.
        "status",
        "resolution",
        "resolved_by",
        "resolved_at",
    )
    actions = ("action_decide",)

    @admin.action(description=_("Решить с текстом"))
    def action_decide(self, request, queryset):
        """Решение собирает текст на отдельной странице.

        Текст решения читает заявитель, поэтому молчаливое действие списка
        отправило бы ему письмо без причины. Отдельная страница, а не
        модальное окно: админка должна работать без JavaScript.
        """
        url = reverse(
            f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_decide"
        )
        ids = ",".join(str(pk) for pk in queryset.values_list("pk", flat=True))
        return redirect(f"{url}?ids={ids}")

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "decide/",
                self.admin_site.admin_view(self.decide_view),
                name=(
                    f"{self.model._meta.app_label}_{self.model._meta.model_name}_decide"
                ),
            ),
        ]
        return custom + urls

    def decide_view(self, request):
        """Страница решения по отмеченным жалобам.

        Решает сервис (`apps.moderation.services`): он проверяет область
        модератора, пишет журнал и ставит письмо заявителю. Своя запись
        статуса здесь стала бы вторым правилом рядом с ним и обошла бы
        письмо — ровно тот дефект, ради которого этот путь и заведён.
        """
        if not self.has_change_permission(request):
            raise PermissionDenied

        model = self.model
        ids = [
            i
            for i in request.POST.get("ids", request.GET.get("ids", "")).split(",")
            if i.isdigit()
        ]
        reports = list(model.objects.filter(pk__in=ids))

        if request.method == "POST":
            form = ReportResolutionForm(request.POST)
            action = request.POST.get("action")
            if form.is_valid() and action in ("resolve", "reject"):
                resolution = form.cleaned_data["resolution"]
                decide = (
                    services.resolve_report
                    if action == "resolve"
                    else services.reject_report
                )
                decided = 0
                denied = 0
                stale = 0
                for report in reports:
                    try:
                        if decide(report, request.user, resolution):
                            decided += 1
                        else:
                            stale += 1
                    except PermissionDenied:
                        denied += 1
                self.message_user(
                    request,
                    ngettext(
                        "%(count)d жалоба разобрана.",
                        "%(count)d жалоб разобрано.",
                        decided,
                    )
                    % {"count": decided},
                    messages.SUCCESS,
                )
                if stale:
                    self.message_user(
                        request,
                        ngettext(
                            "%(count)d жалоба уже была разобрана.",
                            "%(count)d жалоб уже были разобраны.",
                            stale,
                        )
                        % {"count": stale},
                        messages.INFO,
                    )
                if denied:
                    self.message_user(
                        request,
                        ngettext(
                            "%(count)d жалоба пропущена: нет области модерации.",
                            "%(count)d жалоб пропущено: нет области модерации.",
                            denied,
                        )
                        % {"count": denied},
                        messages.WARNING,
                    )
                return redirect(
                    reverse(
                        f"admin:{model._meta.app_label}"
                        f"_{model._meta.model_name}_changelist"
                    )
                )
        else:
            form = ReportResolutionForm()

        context = {
            **self.admin_site.each_context(request),
            "title": _("Решить с текстом"),
            "opts": model._meta,
            "reports": reports,
            "form": form,
            "ids": ",".join(str(pk) for pk in ids),
        }
        return render(request, "admin/moderation/report_decide.html", context)


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
