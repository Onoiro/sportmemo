"""Письма модерации: уведомление о новой жалобе.

О новых комментариях не пишем: при выключенной премодерации это поток писем.
Жалоба — другое дело: её приходит мало, и она требует решения человека.
"""

from django.urls import reverse
from django.utils.translation import gettext as _

from apps.core.emails import notify_moderators


def notify_moderators_new_report(report):
    """Сообщить модераторам о новой жалобе.

    Никогда не поднимает исключение: падение почты не отменяет создание
    жалобы — она уже записана, а модератор увидит её в админке.
    """
    return notify_moderators(
        subject=_("Жалоба на объект: %(reason)s")
        % {"reason": report.get_reason_display()},
        template="moderation/email/new_report_notification.txt",
        context={
            "report": report,
            "reason": report.get_reason_display(),
            "target": report.content_object,
            "admin_url": reverse("admin:moderation_report_change", args=[report.pk]),
        },
    )
