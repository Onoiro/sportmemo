"""Выборка видимых комментариев и контекст блока.

Правило видимости живёт здесь, а не в шаблонах: его используют три страницы,
форма ответа и htmx-ответ. Разъедутся — комментарий исчезнет с одной страницы
и останется на другой.
"""

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q

from apps.comments.forms import CommentForm
from apps.comments.models import Comment
from apps.comments.targets import target_key
from apps.core.models import Status


def visible_comments_q(user):
    """Условие «этот комментарий виден этому пользователю».

    Опубликованное видно всем, свой черновик — дополнительно автору (пока
    премодерация включена). Скрытое, отклонённое и удалённое не видит никто,
    включая автора: это решение модератора, а не черновик.
    """
    query = Q(status=Status.PUBLISHED)
    if user is not None and user.is_authenticated:
        query |= Q(status=Status.DRAFT, author=user)
    return query


def comments_for(target_obj, user):
    """Корневые комментарии объекта, у каждого — список видимых ответов.

    Ответ, чей корень скрыт, не показывается: он висел бы без контекста,
    а разговор читался бы обрывками. Ответы лежат в атрибуте
    `visible_replies` — шаблону не нужно делать запрос на каждый корень.
    """
    content_type = ContentType.objects.get_for_model(target_obj)
    queryset = (
        Comment.public.filter(content_type=content_type, object_id=target_obj.pk)
        .filter(visible_comments_q(user))
        .select_related("author")
    )

    roots = list(queryset.filter(parent__isnull=True))
    if not roots:
        return roots

    replies = queryset.filter(parent__in=roots).order_by("created_at")
    by_parent = {}
    for reply in replies:
        by_parent.setdefault(reply.parent_id, []).append(reply)
    for root in roots:
        root.visible_replies = by_parent.get(root.pk, [])
    return roots


def comments_total(roots):
    """Сколько комментариев видит пользователь: корни и ответы."""
    return sum(1 + len(root.visible_replies) for root in roots)


def comments_context(request, target_obj, form=None, notice="", next_url=""):
    """Контекст блока комментариев для страницы и для htmx-ответа.

    Собирается в одном месте: страница и ответ обязаны показывать одно
    и то же, иначе после отправки список «потеряет» часть комментариев.
    Живёт в сервисе, а не в представлении: блок встраивают три приложения,
    и импортировать представление из представления — лишняя связь.

    `next_url` приходит из представления при POST: `request.get_full_path()`
    там равен адресу обработчика, а не странице объекта.
    """
    comments = comments_for(target_obj, request.user)
    return {
        "comments": comments,
        "comments_total": comments_total(comments),
        "comment_target": target_obj,
        "comment_target_key": target_key(target_obj),
        "comment_form": form
        or CommentForm(
            target_obj=target_obj,
            user=request.user,
            initial={"target": target_key(target_obj), "object_id": target_obj.pk},
        ),
        "comments_next": next_url or request.get_full_path(),
        # htmx-ответ отдаёт только список, и `messages` в него не попадут:
        # текст пояснения едет в контексте партиала.
        "comment_notice": notice,
    }
