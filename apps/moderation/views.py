"""Представление жалобы на ошибку в данных."""

from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.comments.targets import resolve_target, target_key
from apps.core.seo import page_meta
from apps.moderation.emails import notify_moderators_new_report
from apps.moderation.forms import ReportForm
from apps.moderation.models import Report

# Сутки: повторная жалоба того же пользователя на тот же объект с той же
# причиной считается случайным дублем. Анонимов не проверяем — их не за что
# зацепить, лимиты по IP отложены (P2-17).
DUPLICATE_WINDOW = timezone.timedelta(days=1)


def _safe_next(request, target_obj):
    """Куда вернуться после отправки.

    Адрес из формы проверяется: открытый редирект на чужой сайт —
    известная уязвимость фишинга. Небезопасный заменяется страницей объекта.
    """
    value = request.POST.get("next", "") or request.GET.get("next", "")
    if value and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return value
    return target_obj.get_absolute_url()


def _is_duplicate(user, target_obj, reason):
    """Жалоба того же пользователя на тот же объект с той же причиной."""
    if user is None or not user.is_authenticated:
        return False
    return Report.objects.filter(
        reporter=user,
        content_type=ContentType.objects.get_for_model(target_obj),
        object_id=target_obj.pk,
        reason=reason,
        created_at__gte=timezone.now() - DUPLICATE_WINDOW,
    ).exists()


@require_http_methods(["GET", "POST"])
def report_add(request):
    """Страница жалобы: доступна анонимно.

    Отдельная страница, а не модальное окно: работает без JavaScript,
    и ссылку можно переслать. После отправки — благодарность и возврат
    на страницу объекта.
    """
    target_obj = resolve_target(
        request.POST.get("target") or request.GET.get("target", ""),
        request.POST.get("object_id") or request.GET.get("object_id"),
        request,
    )
    next_url = _safe_next(request, target_obj)

    if request.method == "POST":
        form = ReportForm(request.POST)
        if form.is_valid():
            if form.is_bot():
                # Приманка заполнена: отвечаем как обычно, но ничего
                # не записываем — иначе бот узнает, что его раскусили.
                messages.success(request, _("Спасибо! Модератор проверит."))
                return redirect(next_url)

            reason = form.cleaned_data["reason"]
            if _is_duplicate(request.user, target_obj, reason):
                messages.info(request, _("Жалоба на этот объект уже отправлена."))
                return redirect(next_url)

            report = Report.objects.create(
                reporter=request.user if request.user.is_authenticated else None,
                content_type=ContentType.objects.get_for_model(target_obj),
                object_id=target_obj.pk,
                reason=reason,
                comment=form.cleaned_data["comment"],
            )
            notify_moderators_new_report(report)
            messages.success(request, _("Спасибо! Модератор проверит."))
            return redirect(next_url)
    else:
        form = ReportForm(
            initial={
                "target": target_key(target_obj),
                "object_id": target_obj.pk,
                "next": request.GET.get("next", ""),
            }
        )

    context = {
        "form": form,
        "target_obj": target_obj,
        "target_key": target_key(target_obj),
        "next_url": next_url,
        "meta": page_meta(
            request,
            title=_("Сообщить об ошибке"),
            noindex=True,
            nofollow=True,
        ),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"url": target_obj.get_absolute_url(), "label": str(target_obj)},
            {"label": _("Сообщить об ошибке")},
        ],
    }
    return render(request, "moderation/report_form.html", context)
