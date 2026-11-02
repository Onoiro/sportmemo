"""Письма модерации: уведомление о новой жалобе.

О новых комментариях не пишем: при выключенной премодерации это поток писем.
Жалоба — другое дело: её приходит мало, и она требует решения человека.
"""

from django.contrib.sites.models import Site
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.core.emails import notify_moderators


def _reports_url(request=None):
    """Абсолютный адрес списка жалоб.

    Почтовый клиент не открывает относительные адреса, поэтому адрес
    собирается целиком. Без запроса домен берётся из `django.contrib.sites`,
    а не из `request.get_host()`: за сайтом стоит nginx, и адрес в письме
    зависел бы от настроек прокси. Протокол — `https`, как у боевого сайта;
    в dev письма печатаются в консоль, и адрес там всё равно не открывают.
    """
    path = reverse("moderation:reports")
    if request is not None:
        return request.build_absolute_uri(path)
    return f"https://{Site.objects.get_current().domain}{path}"


def notify_moderators_new_report(report, request=None):
    """Сообщить модераторам о новой жалобе.

    Ссылка ведёт на страницу разбора в интерфейсе, а не в админку:
    модераторы проекта работают с телефона, и админка на маленьком
    экране неудобна.

    Никогда не поднимает исключение: падение почты не отменяет создание
    жалобы — она уже записана, а модератор увидит её в списке.
    """
    return notify_moderators(
        subject=_("Жалоба на объект: %(reason)s")
        % {"reason": report.get_reason_display()},
        template="moderation/email/new_report_notification.txt",
        context={
            "report": report,
            "reason": report.get_reason_display(),
            "target": report.content_object,
            "reports_url": _reports_url(request),
        },
        # Вид спорта у жалобы может быть пустым: тогда письмо уходит всем,
        # и это правильный исход — жалоба не должна потеряться.
        sport=report.sport,
    )
