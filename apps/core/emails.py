"""Письма модераторам: общие для каталога и модерации.

Адресаты нужны в двух приложениях, поэтому функция живёт в `core`, а не
в `catalog`: `moderation` тянул бы `catalog` только ради списка адресов.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.template.loader import render_to_string

from apps.accounts.models import UserRole

logger = logging.getLogger(__name__)


def moderator_emails():
    """Адреса активных модераторов и администраторов.

    Берём из базы, а не из настройки: список модераторов меняется, и адрес
    в `.env` разошёлся бы с реальностью.
    """
    User = get_user_model()
    return list(
        User.objects.filter(
            role__in=[UserRole.MODERATOR, UserRole.ADMIN], is_active=True
        )
        .exclude(email="")
        .values_list("email", flat=True)
    )


def notify_moderators(subject, template, context):
    """Письмо модераторам по шаблону. Никогда не поднимает исключение.

    Падение почты не должно отменять действие пользователя: команда уже
    создана, жалоба уже записана, модератор увидит их в админке. Поэтому
    ошибка логируется и превращается в ноль отправленных писем.
    """
    recipients = moderator_emails()
    if not recipients:
        # Модератора ещё нет — это нормальное состояние на старте проекта,
        # но знать о нём нужно: письмо не ушло.
        logger.warning("Письмо «%s» без модераторов: не отправлено", subject)
        return 0

    body = render_to_string(template, context)
    try:
        return send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=False,
        )
    except Exception:  # noqa: BLE001 — почта не должна ронять запрос
        logger.exception("Не удалось отправить письмо «%s»", subject)
        return 0
