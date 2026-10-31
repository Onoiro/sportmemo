"""Письма модераторам: общие для каталога и модерации.

Адресаты нужны в двух приложениях, поэтому функция живёт в `core`, а не
в `catalog`: `moderation` тянул бы `catalog` только ради списка адресов.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db.models import Q
from django.template.loader import render_to_string

from apps.accounts.models import UserRole

logger = logging.getLogger(__name__)


def moderator_emails(sport=None):
    """Адреса активных модераторов и администраторов.

    Берём из базы, а не из настройки: список модераторов меняется, и адрес
    в `.env` разошёлся бы с реальностью.

    `sport=None` — все модераторы: так письмо уходит, когда вид спорта
    у объекта неизвестен, и поведение до появления области не меняется.
    Иначе адресатами становятся администраторы, суперадмины и модераторы
    этого вида спорта: админ должен получать всё, иначе он не узнает
    о проблеме в чужой области.
    """
    User = get_user_model()
    recipients = User.objects.filter(is_active=True).exclude(email="")

    if sport is None:
        recipients = recipients.filter(role__in=[UserRole.MODERATOR, UserRole.ADMIN])
    else:
        recipients = recipients.filter(
            Q(role=UserRole.ADMIN) | Q(is_superuser=True) | Q(moderated_sports=sport)
        )

    return list(recipients.distinct().values_list("email", flat=True))


def notify_moderators(subject, template, context, sport=None):
    """Письмо модераторам по шаблону. Никогда не поднимает исключение.

    Падение почты не должно отменять действие пользователя: команда уже
    создана, жалоба уже записана, модератор увидит их в админке. Поэтому
    ошибка логируется и превращается в ноль отправленных писем.

    `sport` — вид спорта объекта: письмо уходит модераторам этой области.
    `None` значит «всем», и это правильный исход для объекта без вида спорта.
    """
    recipients = moderator_emails(sport)
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
