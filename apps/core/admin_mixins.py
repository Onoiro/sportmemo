"""Миксины для админок SportMemo.

Модераторские действия одинаковы для всех содержательных сущностей, поэтому
вынесены сюда: регистрация модели должна оставаться декларативной.
"""

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from simple_history.admin import SimpleHistoryAdmin

from apps.moderation import services
from apps.moderation.forms import ModerationRejectForm
from apps.moderation.models import ModerationActionType

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
    """Действия «опубликовать / скрыть / отклонить» + журнал решений.

    Само решение принимает сервис (`apps.moderation.services`): он
    проверяет право модератора по области и пишет журнал. Миксин только
    переводит действие списка в вызовы сервиса — своя запись журнала
    здесь стала бы вторым правилом рядом с сервисом.

    Наследники должны иметь поля `status`, `moderated_by`, `moderated_at`,
    `moderation_reason` (миксин `StatusModel`) и `is_deleted` (миксин
    `SoftDeleteModel`).
    """

    @admin.action(description=_("Опубликовать выбранные"))
    def action_publish(self, request, queryset):
        updated, denied = self._decide(
            request, queryset, lambda obj: services.publish_object(obj, request.user)
        )
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
        self._report_denied(request, denied)

    @admin.action(description=_("Скрыть выбранные"))
    def action_hide(self, request, queryset):
        updated, denied = self._decide(
            request, queryset, lambda obj: services.hide_object(obj, request.user)
        )
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
        self._report_denied(request, denied)

    @admin.action(description=_("Отклонить выбранные"))
    def action_reject(self, request, queryset):
        """Отклонение собирает причину на отдельной странице.

        Причину увидит автор, поэтому молчаливое отклонение из списка
        действий противоречило бы решению владельца, а причина-заглушка
        скрыла бы настоящую. Отдельная страница, а не модальное окно:
        админка должна работать без JavaScript.
        """
        url = reverse(
            f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_reject"
        )
        ids = ",".join(str(pk) for pk in queryset.values_list("pk", flat=True))
        return redirect(f"{url}?ids={ids}")

    @admin.action(description=_("Мягко удалить выбранные"))
    def action_soft_delete(self, request, queryset):
        reason = _("удалено модератором")
        updated, denied = self._decide(
            request,
            queryset,
            lambda obj: services.soft_delete_object(obj, request.user, reason),
        )
        self.message_user(
            request,
            ngettext(
                "%(count)d объект удалён.",
                "%(count)d объектов удалено.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )
        self._report_denied(request, denied)

    @admin.action(description=_("Восстановить выбранные"))
    def action_restore(self, request, queryset):
        reason = _("восстановлено модератором")
        updated, denied = self._decide(
            request,
            queryset,
            lambda obj: services.restore_object(obj, request.user, reason),
        )
        self.message_user(
            request,
            ngettext(
                "%(count)d объект восстановлен.",
                "%(count)d объектов восстановлено.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )
        self._report_denied(request, denied)

    def _decide(self, request, queryset, decide):
        """Выполнить решение по каждому объекту и посчитать итоги.

        Один чужой объект в выборке не должен ронять всё действие: нет
        области модерации — объект пропускается, о пропуске сообщают числом.
        """
        updated = 0
        denied = 0
        for obj in queryset:
            try:
                if decide(obj):
                    updated += 1
            except PermissionDenied:
                denied += 1
        return updated, denied

    def _report_denied(self, request, denied):
        if not denied:
            return
        self.message_user(
            request,
            ngettext(
                "%(count)d объект пропущен: нет области модерации.",
                "%(count)d объектов пропущено: нет области модерации.",
                denied,
            )
            % {"count": denied},
            messages.WARNING,
        )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "reject/",
                self.admin_site.admin_view(self.reject_view),
                name=(
                    f"{self.model._meta.app_label}_{self.model._meta.model_name}_reject"
                ),
            ),
        ]
        return custom + urls

    def reject_view(self, request):
        """Страница причины отклонения для отмеченных объектов."""
        if not self.has_change_permission(request):
            raise PermissionDenied

        model = self.model
        ids = [
            i
            for i in request.POST.get("ids", request.GET.get("ids", "")).split(",")
            if i.isdigit()
        ]
        # Не `public`: удалённый объект должен попасть в список и быть
        # пропущенным с сообщением, а не исчезнуть молча.
        sources = list(model.objects.filter(pk__in=ids))

        if request.method == "POST":
            form = ModerationRejectForm(request.POST)
            if form.is_valid():
                reason = form.cleaned_data["reason"]
                rejected, denied = self._decide(
                    request,
                    sources,
                    lambda obj: services.reject_object(obj, request.user, reason),
                )
                self.message_user(
                    request,
                    ngettext(
                        "%(count)d объект отклонён.",
                        "%(count)d объектов отклонено.",
                        rejected,
                    )
                    % {"count": rejected},
                    messages.SUCCESS,
                )
                self._report_denied(request, denied)
                return redirect(
                    reverse(
                        f"admin:{model._meta.app_label}"
                        f"_{model._meta.model_name}_changelist"
                    )
                )
        else:
            form = ModerationRejectForm()

        context = {
            **self.admin_site.each_context(request),
            "title": _("Отклонить с причиной"),
            "opts": model._meta,
            "sources": sources,
            "form": form,
            "ids": ",".join(str(pk) for pk in ids),
        }
        return render(request, "admin/moderation/reject_form.html", context)


class MergeActionsMixin:
    """Действие «слить в выбранный объект» для моделей с `merge_into`.

    Действие открывает отдельную страницу: цель выбирается из списка, а не
    из тех же строк, что отмечены для слияния, — иначе легко слить объект
    сам с собой.
    """

    @admin.action(description=_("Слить дубликаты в один объект"))
    def action_merge(self, request, queryset):
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

            merged = 0
            for source in sources:
                if source.pk == target.pk:
                    continue
                source.merge_into(target, reason=_("слито модератором"))
                # Журнал пишет сервис: своя запись здесь стала бы вторым
                # правилом рядом с ним.
                services.log_action(
                    source,
                    request.user,
                    ModerationActionType.MERGE,
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
