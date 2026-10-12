"""Письма каталога: уведомления модераторам."""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _

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


def notify_moderators_new_team(team):
    """Сообщить модераторам о новой команде.

    Никогда не поднимает исключение: падение почты не должно отменять
    создание команды — она уже сохранена, а модератор увидит её в админке.
    """
    recipients = moderator_emails()
    if not recipients:
        # Модератора ещё нет — это нормальное состояние на старте проекта,
        # но знать о нём нужно: письмо не ушло, команда ждёт в админке.
        logger.warning(
            "Новая команда %s без модераторов: письмо не отправлено", team.pk
        )
        return 0

    body = render_to_string(
        "catalog/email/new_team_notification.txt",
        {
            "team": team,
            "team_url": team.get_absolute_url(),
            "admin_url": reverse("admin:catalog_team_change", args=[team.pk]),
        },
    )
    try:
        return send_mail(
            subject=_("Новая команда: %(name)s") % {"name": team.name},
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=False,
        )
    except Exception:  # noqa: BLE001 — почта не должна ронять создание команды
        logger.exception("Не удалось отправить письмо о новой команде %s", team.pk)
        return 0
