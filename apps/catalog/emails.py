"""Письма каталога: уведомления модераторам.

Общая часть (список адресатов и отправка) живёт в `apps/core/emails.py`:
те же адресаты нужны жалобам. Здесь остаётся только текст о новой команде.
"""

from django.urls import reverse
from django.utils.translation import gettext as _

from apps.core.emails import notify_moderators


def notify_moderators_new_team(team):
    """Сообщить модераторам о новой команде.

    Никогда не поднимает исключение: падение почты не должно отменять
    создание команды — она уже сохранена, а модератор увидит её в админке.
    """
    return notify_moderators(
        subject=_("Новая команда: %(name)s") % {"name": team.name},
        template="catalog/email/new_team_notification.txt",
        context={
            "team": team,
            "team_url": team.get_absolute_url(),
            "admin_url": reverse("admin:catalog_team_change", args=[team.pk]),
        },
    )
