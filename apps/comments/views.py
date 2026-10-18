"""Представление добавления комментария."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.comments.forms import CommentForm
from apps.comments.models import Comment, CommentSettings
from apps.comments.services import comments_context
from apps.comments.targets import resolve_target
from apps.core.models import Status

# Окно, в котором тот же текст тому же объекту считается дублем: двойной
# клик и «отправил, обновил страницу» — частый случай, а не спам.
DUPLICATE_WINDOW = timezone.timedelta(minutes=10)


def _safe_next(request, target_obj):
    """Куда вернуться после отправки.

    Значение из формы проверяется: открытый редирект на чужой сайт —
    известная уязвимость фишинга. Небезопасный адрес заменяется страницей
    объекта.
    """
    value = request.POST.get("next", "")
    if value and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return value
    return target_obj.get_absolute_url()


def _is_duplicate(user, target_obj, text):
    """Тот же автор, цель и текст в пределах `DUPLICATE_WINDOW`."""
    content_type = ContentType.objects.get_for_model(target_obj)
    return Comment.objects.filter(
        author=user,
        content_type=content_type,
        object_id=target_obj.pk,
        text=text,
        created_at__gte=timezone.now() - DUPLICATE_WINDOW,
    ).exists()


def render_comments(request, target_obj, form=None, notice="", next_url=""):
    """Блок комментариев: ответ htmx и фрагмент страницы."""
    return render(
        request,
        "comments/partials/list.html",
        comments_context(request, target_obj, form, notice, next_url),
    )


@login_required
@require_POST
def add(request):
    """Добавить комментарий или ответ.

    Статус зависит от настройки `CommentSettings`: при включённой
    премодерации комментарий ждёт модератора (`draft`), иначе публикуется
    сразу. Правило живёт здесь, а не в модели: `Comment` не знает про
    настройку, и админка может опубликовать любой комментарий руками.
    """
    target_obj = resolve_target(
        request.POST.get("target", ""), request.POST.get("object_id"), request
    )
    form = CommentForm(request.POST, target_obj=target_obj, user=request.user)
    next_url = _safe_next(request, target_obj)
    is_htmx = request.headers.get("HX-Request") == "true"

    if not form.is_valid():
        if is_htmx:
            return render_comments(request, target_obj, form, next_url=next_url)
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect(next_url)

    text = form.cleaned_data["text"]
    if _is_duplicate(request.user, target_obj, text):
        notice = _("Такой комментарий вы уже отправляли.")
        if is_htmx:
            return render_comments(
                request, target_obj, notice=notice, next_url=next_url
            )
        messages.info(request, notice)
        return redirect(next_url)

    premoderation = CommentSettings.load().premoderation
    Comment.objects.create(
        author=request.user,
        text=text,
        parent=form.parent_comment,
        content_type=ContentType.objects.get_for_model(target_obj),
        object_id=target_obj.pk,
        status=Status.DRAFT if premoderation else Status.PUBLISHED,
    )

    if is_htmx:
        # Форма отдаётся заново: так очищается поле ввода, а список
        # показывает новый комментарий.
        return render_comments(request, target_obj, next_url=next_url)

    if premoderation:
        messages.success(request, _("Комментарий отправлен на проверку модератору."))
    else:
        messages.success(request, _("Комментарий добавлен."))
    return HttpResponseRedirect(next_url)
