"""Представления модерации: жалоба, очередь черновиков и разбор жалоб."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.comments.targets import resolve_target, target_key
from apps.core.seo import page_meta
from apps.moderation import queue, reports, services
from apps.moderation.emails import notify_moderators_new_report
from apps.moderation.forms import (
    QueueRejectForm,
    ReportForm,
    ReportResolutionForm,
)
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
                # Денормализация: жалоба связана generic-FK, и по области
                # модератора её иначе не отфильтровать.
                sport=getattr(target_obj, "sport", None),
                reason=reason,
                comment=form.cleaned_data["comment"],
            )
            notify_moderators_new_report(report, request=request)
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


@login_required
@never_cache
@require_GET
def queue_list(request):
    """Очередь черновиков: старые первыми, порция 12.

    Страница для телефона: у модераторов проекта часто нет компьютера,
    а админка на маленьком экране неудобна и опасна.

    Не кэшируется: статус меняет другой модератор, и копия из кэша показала
    бы уже разобранный черновик — то же обоснование, что у `/photos/mine/`.
    """
    # Служебная страница, а не объект архива: её адрес ничего не раскрывает,
    # поэтому 403 честнее 404 — человеку видно, почему его не пускают.
    if not request.user.is_moderator:
        raise PermissionDenied

    sport_ids = request.user.moderated_sport_ids()
    # Пустая область — не «всё разобрано»: без плашки человек решил бы,
    # что работы нет, и не пошёл бы к администратору.
    scope_missing = sport_ids == []
    page_obj, cards = (
        (None, []) if scope_missing else queue.queue_page(request.user, request)
    )

    context = {
        "page_obj": page_obj,
        "cards": cards,
        "scope_missing": scope_missing,
        # Одна форма на страницу: карточки берут из неё подпись и виджет,
        # а `id` поля собирают сами с номером объекта.
        "reject_form": QueueRejectForm(),
        "meta": page_meta(
            request,
            title=_("Очередь черновиков"),
            noindex=True,
            nofollow=True,
        ),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"label": _("Очередь черновиков")},
        ],
    }
    return render(request, "moderation/queue.html", context)


def _queue_next(request):
    """Куда вернуться после решения.

    Адрес приходит скрытым полем из карточки — очередь с `?page=N`.
    Небезопасный или пустой заменяется первой страницей очереди:
    открытый редирект на чужой сайт известен как приём фишинга.
    """
    value = request.POST.get("next", "")
    if value and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return value
    return reverse("moderation:queue")


def _queue_denied():
    """404 на всё, чего этому человеку не существует.

    Чужой черновик, свой черновик и несуществующий номер отвечают
    одинаково: иначе по коду ответа можно перебрать номера и узнать,
    что лежит в архиве (то же правило, что `can_edit`).
    """
    raise Http404("Объект не найден")


@login_required
@require_POST
def queue_decide(request, kind, pk):
    """Решение по черновику: опубликовать или отклонить с причиной.

    Только POST: решение меняет чужие данные, и ссылка-«одобрить»
    в адресной строке открывалась бы превью браузера и роботом.

    Объект ищется в `objects`, а не в `public` и не в очереди: удалённый
    или уже решённый объект должен получить понятный ответ, а не 404.

    `PermissionDenied` из сервиса отдельно не ловим: Django отвечает
    на него 403, а `transaction.atomic()` откатывает уже начатую запись.
    """
    if not request.user.is_moderator:
        raise PermissionDenied

    # Неизвестный ключ из адреса даёт 404 внутри `get_queue_model`, поэтому
    # обращение к реестру ниже безопасно.
    model = queue.get_queue_model(kind)
    spec = queue.QUEUE_MODELS[kind]
    next_url = _queue_next(request)

    # Блокировка строки: два модератора могут открыть очередь одновременно.
    # Без неё оба прошли бы проверку «ещё черновик» и второй отменил бы
    # решение первого — в журнале остались бы два разных решения.
    with transaction.atomic():
        obj = model.objects.select_for_update().filter(pk=pk).first()
        if obj is None:
            _queue_denied()
        if not request.user.can_moderate(obj):
            _queue_denied()
        # Свой черновик решает другой человек. Проверка стоит до
        # `is_pending`: иначе своё уже опубликованное ответило бы
        # «Уже обработано» и подсказало, что адрес существует.
        if queue.is_own(obj, spec, request.user):
            _queue_denied()

        # «Всё ещё черновик» проверяет представление, а не сервис:
        # `reject_object` на опубликованном вернул бы `True` и снова
        # отклонил — второй модератор отменил бы первого.
        if not queue.is_pending(obj):
            messages.info(request, _("Уже обработано"))
            return redirect(next_url)

        action = request.POST.get("action")
        if action == "publish":
            decided = services.publish_object(obj, request.user)
            success = _("%(kind)s: опубликовано.") % {"kind": spec.label}
        elif action == "reject":
            form = QueueRejectForm(request.POST)
            if not form.is_valid():
                messages.error(request, form.errors["reason"][0])
                return redirect(next_url)
            decided = services.reject_object(
                obj, request.user, form.cleaned_data["reason"]
            )
            success = _("%(kind)s: отклонено, автор увидит причину.") % {
                "kind": spec.label
            }
        else:
            return HttpResponseBadRequest(_("Неизвестное действие"))

    # Сверхузкая гонка: оба модератора прошли проверку, а решение принял
    # первый. Молчание выглядело бы как успех, поэтому отвечаем честно.
    if not decided:
        messages.info(request, _("Уже обработано"))
    else:
        messages.success(request, success)
    return redirect(next_url)


@login_required
@never_cache
@require_GET
def reports_list(request):
    """Открытые жалобы области модератора: свежие сверху, порция 20.

    Разбор жалоб на телефоне важнее разбора черновиков: жалоба о нарушении
    прав или приватности не должна ждать, пока модератор доберётся
    до компьютера.

    Не кэшируется: жалобу может разобрать другой модератор, и копия
    из кэша показала бы уже решённую.
    """
    # Служебная страница, а не объект архива: 403 честнее 404, как в очереди.
    if not request.user.is_moderator:
        raise PermissionDenied

    # Пустая область не значит «жалоб нет»: жалобы без вида спорта видны
    # всем, поэтому список строится всегда, а плашка объясняет остальное.
    scope_missing = request.user.moderated_sport_ids() == []
    page_obj, cards = reports.reports_page(request.user, request)

    context = {
        "page_obj": page_obj,
        "cards": cards,
        "scope_missing": scope_missing,
        # Одна форма на страницу: карточки берут из неё подпись и виджет,
        # а `id` поля собирают сами с номером жалобы.
        "resolution_form": ReportResolutionForm(),
        "meta": page_meta(
            request,
            title=_("Жалобы"),
            noindex=True,
            nofollow=True,
        ),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"label": _("Жалобы")},
        ],
    }
    return render(request, "moderation/report_list.html", context)


def _reports_next(request):
    """Куда вернуться после решения по жалобе.

    Адрес приходит скрытым полем из карточки — список с `?page=N`.
    Небезопасный или пустой заменяется первой страницей списка: открытый
    редирект на чужой сайт известен как приём фишинга.
    """
    value = request.POST.get("next", "")
    if value and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return value
    return reverse("moderation:reports")


def _report_denied():
    """404 на всё, чего этому человеку не существует.

    Чужая область, своя жалоба и несуществующий номер отвечают одинаково:
    иначе по коду ответа можно перебрать номера и узнать, о чём жалуются
    в чужом виде спорта (то же правило, что `queue_decide`).
    """
    raise Http404("Объект не найден")


@login_required
@require_POST
def report_decide(request, pk):
    """Решение по жалобе: взять в работу, решить или отклонить.

    Один адрес с полем `action`, а не три маршрута: решение по жалобе —
    одно событие с тремя исходами, и три адреса трижды повторяли бы поиск
    объекта, проверку области и проверку «ещё открыта».

    Только POST: решение меняет чужие данные, а ссылка-«решить» в адресной
    строке открывалась бы превью браузера и роботом.

    `PermissionDenied` из сервиса отдельно не ловим: Django отвечает на него
    403, а `transaction.atomic()` откатывает уже начатую запись.
    """
    if not request.user.is_moderator:
        raise PermissionDenied

    next_url = _reports_next(request)

    # Блокировка строки: два модератора могут разбирать одну жалобу
    # одновременно. Без неё оба прошли бы проверку «ещё открыта», и второй
    # переписал бы решение первого — в журнале остались бы два решения.
    with transaction.atomic():
        # `select_related("sport")` здесь нельзя: вид спорта у жалобы может
        # быть пустым, а PostgreSQL отказывает в `FOR UPDATE` на внешней
        # стороне outer join. Право читает `sport_id`, и подгрузка строки
        # вида спорта всё равно не нужна.
        report = Report.objects.select_for_update().filter(pk=pk).first()
        if report is None:
            _report_denied()
        if not request.user.can_moderate(report):
            _report_denied()
        # Свою жалобу разбирает другой человек. Проверка стоит до `is_open`:
        # иначе своя уже решённая ответила бы «Уже обработано» и подсказала
        # бы, что адрес существует.
        if reports.is_own_report(report, request.user):
            _report_denied()

        # «Ещё открыта» проверяет представление, а не сервис: `take_report`
        # на решённой вернул бы жалобу в работу и отменил решение первого
        # модератора.
        if not reports.is_open(report):
            messages.info(request, _("Уже обработано"))
            return redirect(next_url)

        action = request.POST.get("action")
        if action == "take":
            decided = services.take_report(report, request.user)
            success = _("Жалоба взята в работу")
            stale = _("Жалоба уже в работе")
        elif action in ("resolve", "reject"):
            form = ReportResolutionForm(request.POST)
            if not form.is_valid():
                messages.error(request, form.errors["resolution"][0])
                return redirect(next_url)
            resolution = form.cleaned_data["resolution"]
            if action == "resolve":
                decided = services.resolve_report(report, request.user, resolution)
                success = _("Жалоба решена")
            else:
                decided = services.reject_report(report, request.user, resolution)
                success = _("Жалоба отклонена")
            stale = _("Уже обработано")
        else:
            return HttpResponseBadRequest(_("Неизвестное действие"))

    # Сверхузкая гонка: оба модератора прошли проверку, а решение принял
    # первый. Молчание выглядело бы как успех, поэтому отвечаем честно.
    if not decided:
        messages.info(request, stale)
    else:
        messages.success(request, success)
    return redirect(next_url)
