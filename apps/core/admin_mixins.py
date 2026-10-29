"""Миксины для админок SportMemo.

Модераторские действия одинаковы для всех содержательных сущностей, поэтому
вынесены сюда: регистрация модели должна оставаться декларативной.
"""

from django.contrib import admin, messages
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from simple_history.admin import SimpleHistoryAdmin

from apps.core.models import Status
from apps.moderation.models import ModerationAction, ModerationActionType

# Наборы действий для `actions = ...` в наследниках. Держим их константами,
# а не атрибутами миксинов: миксины не должны перетирать `actions` друг друга.
MODERATION_ACTIONS = (
    "action_publish",
    "action_hide",
    "action_reject",
    "action_soft_delete",
    "action_restore",
)
MERGE_ACTIONS = ("action_merge",)


class ModerationActionsMixin:
    """Действия «опубликовать / скрыть / отклонить» + запись в журнал решений.

    Наследники должны иметь поля `status`, `moderated_by`, `moderated_at`,
    `moderation_reason` (миксин `StatusModel`) и `is_deleted` (миксин
    `SoftDeleteModel`).
    """

    @admin.action(description=_("Опубликовать выбранные"))
    def action_publish(self, request, queryset):
        self._apply_status(
            request, queryset, Status.PUBLISHED, ModerationActionType.PUBLISH
        )

    @admin.action(description=_("Скрыть выбранные"))
    def action_hide(self, request, queryset):
        self._apply_status(request, queryset, Status.HIDDEN, ModerationActionType.HIDE)

    @admin.action(description=_("Отклонить выбранные"))
    def action_reject(self, request, queryset):
        self._apply_status(
            request, queryset, Status.REJECTED, ModerationActionType.REJECT
        )

    @admin.action(description=_("Мягко удалить выбранные"))
    def action_soft_delete(self, request, queryset):
        content_type = ContentType.objects.get_for_model(queryset.model)
        count = 0
        for obj in queryset:
            if obj.is_deleted:
                continue
            obj.soft_delete(reason=_("удалено модератором"))
            ModerationAction.objects.create(
                moderator=request.user,
                action=ModerationActionType.DELETE,
                content_type=content_type,
                object_id=obj.pk,
                reason=_("удалено модератором"),
            )
            count += 1
        self.message_user(
            request,
            ngettext("%(count)d объект удалён.", "%(count)d объектов удалено.", count)
            % {"count": count},
            messages.SUCCESS,
        )

    @admin.action(description=_("Восстановить выбранные"))
    def action_restore(self, request, queryset):
        content_type = ContentType.objects.get_for_model(queryset.model)
        count = 0
        for obj in queryset:
            if not obj.is_deleted:
                continue
            obj.restore()
            ModerationAction.objects.create(
                moderator=request.user,
                action=ModerationActionType.RESTORE,
                content_type=content_type,
                object_id=obj.pk,
                reason=_("восстановлено модератором"),
            )
            count += 1
        self.message_user(
            request,
            ngettext(
                "%(count)d объект восстановлен.",
                "%(count)d объектов восстановлено.",
                count,
            )
            % {"count": count},
            messages.SUCCESS,
        )

    def _apply_status(self, request, queryset, status, action):
        content_type = ContentType.objects.get_for_model(queryset.model)
        updated = 0
        for obj in queryset:
            if obj.status == status:
                continue
            if status == Status.PUBLISHED:
                obj.publish(moderator=request.user)
            elif status == Status.HIDDEN:
                obj.hide(moderator=request.user)
            else:
                obj.reject(moderator=request.user)
            ModerationAction.objects.create(
                moderator=request.user,
                action=action,
                content_type=content_type,
                object_id=obj.pk,
                reason=obj.moderation_reason,
            )
            updated += 1
        self.message_user(
            request,
            ngettext(
                "%(count)d объект обновлён.",
                "%(count)d объектов обновлено.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )


class MergeActionsMixin:
    """Действие «слить в выбранный объект» для моделей с `merge_into`.

    Действие открывает отдельную страницу: цель выбирается из списка, а не
    из тех же строк, что отмечены для слияния, — иначе легко слить объект
    сам с собой.
    """

    @admin.action(description=_("Слить дубликаты в один объект"))
    def action_merge(self, request, queryset):
        from django.shortcuts import redirect
        from django.urls import reverse

        url = reverse(
            f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_merge"
        )
        ids = ",".join(str(pk) for pk in queryset.values_list("pk", flat=True))
        return redirect(f"{url}?ids={ids}")

    def merge_candidates(self, queryset, sources):
        """Цели слияния: не слитые и не удалённые объекты.

        Слитый объект каноном быть не может: цепочки запрещены, и связи
        уехали бы в запись, которой нет в публичной выдаче. Удалённый
        канон дал бы ссылку в никуда. Хук, а не фильтр в `merge_view`:
        наследник сужает список своими признаками, а POST обязан
        проверяться тем же queryset, что увидел человек.
        """
        return queryset.filter(merged_into__isnull=True, deleted_at__isnull=True)

    def get_urls(self):
        from django.urls import path

        urls = super().get_urls()
        custom = [
            path(
                "merge/",
                self.admin_site.admin_view(self.merge_view),
                name=f"{self.model._meta.app_label}_{self.model._meta.model_name}_merge",
            ),
        ]
        return custom + urls

    def merge_view(self, request):
        """Страница выбора целевого объекта для слияния."""
        from django.core.exceptions import PermissionDenied
        from django.shortcuts import redirect, render

        if not self.has_change_permission(request):
            raise PermissionDenied

        model = self.model
        ids = [
            i
            for i in request.POST.get("ids", request.GET.get("ids", "")).split(",")
            if i.isdigit()
        ]
        sources = list(model.objects.filter(pk__in=ids))
        source_pks = [s.pk for s in sources]
        candidates = self.merge_candidates(
            model.objects.exclude(pk__in=source_pks), sources
        )

        if request.method == "POST":
            target_id = request.POST.get("target", "")
            # Цель ищется в том же queryset, что увидел человек: иначе
            # подделанный POST выбрал бы слитое или чужое фото каноном.
            target = (
                candidates.filter(pk=target_id).first() if target_id.isdigit() else None
            )
            if target is None:
                self.message_user(
                    request, _("Выберите целевой объект из списка"), messages.ERROR
                )
                return redirect(request.get_full_path())

            content_type = ContentType.objects.get_for_model(model)
            merged = 0
            for source in sources:
                if source.pk == target.pk:
                    continue
                source.merge_into(target, reason=_("слито модератором"))
                ModerationAction.objects.create(
                    moderator=request.user,
                    action=ModerationActionType.MERGE,
                    content_type=content_type,
                    object_id=source.pk,
                    reason=_("Слито в объект #%(target)d") % {"target": target.pk},
                )
                merged += 1

            self.message_user(
                request,
                ngettext(
                    "%(count)d объект слит в «%(target)s».",
                    "%(count)d объектов слито в «%(target)s».",
                    merged,
                )
                % {"count": merged, "target": target},
                messages.SUCCESS,
            )
            return redirect(f"../{target.pk}/change/")

        context = {
            **self.admin_site.each_context(request),
            "title": _("Слияние дубликатов"),
            "opts": model._meta,
            "sources": sources,
            "candidates": candidates,
            "ids": ",".join(str(pk) for pk in source_pks),
        }
        return render(request, "admin/core/merge_form.html", context)


class BaseHistoryAdmin(SimpleHistoryAdmin):
    """Админка с историей и откатом для моделей с `HistoricalRecords`."""

    history_list_display = ["status"]
