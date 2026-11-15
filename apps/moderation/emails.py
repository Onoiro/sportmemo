"""Письма модерации: новая жалоба и решение по ней.

О новых комментариях не пишем: при выключенной премодерации это поток писем.
Жалоба — другое дело: её приходит мало, и она требует решения человека.

Два письма и две разные обработки ошибок: письмо модераторам сбой глотает,
письмо заявителю — нет. Разница объясняется в docstring каждой функции.
"""

from django.conf import settings
from django.contrib.sites.models import Site
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.core.emails import notify_moderators, send_user_email


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


def notify_reporter_decision(report):
    """Письмо заявителю о решении жалобы. Возвращает число отправленных.

    Только активный пользователь: после анонимизации (5.6) адрес
    становится `deleted-…@invalid`, а `is_active=False`, и наивный код
    отправлял бы письмо в никуда на каждое решение. Анонимной жалобе
    писать некуда — возвращаем 0. Проверка живёт здесь, а не в задаче:
    она про данные, а не про очередь.

    Ошибку не глотает: `send_user_email` устроен иначе, чем письмо
    модераторам, и повтор попытки делает задача. Письмо — единственный
    канал, которым заявитель узнаёт решение, и тихо потерять его нельзя.

    Текст нейтральный: ни «на вас пожаловались», ни имени модератора.
    Отклонение жалобы — тоже решение, и его текст объясняет причину.
    """
    reporter = report.reporter
    if reporter is None or not reporter.is_active or not reporter.email:
        return 0
    reason = report.get_reason_display()
    return send_user_email(
        subject=_("Решение по вашей жалобе: %(reason)s") % {"reason": reason},
        template="moderation/email/report_decision.txt",
        context={
            "target": report.content_object or "—",
            "resolution": report.resolution,
            "contact_email": settings.SPORTMEMO_CONTACT_EMAIL,
        },
        recipient=reporter.email,
    )
