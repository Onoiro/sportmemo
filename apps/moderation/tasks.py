"""Celery-задачи модерации: письмо заявителю о решении жалобы.

Единственная точка постановки — `enqueue_report_decision_email()`: сервис
решений не должен решать, как ставить задачу. Иначе один путь отправки
стал бы синхронным, а другой фоновым, и поведение зависело бы от того,
откуда пришёл запрос.

`on_commit` живёт на стороне сервиса, а не здесь: `report_decide` держит
`select_for_update`, а SMTP-соединение занимает секунды. Кроме того, откат
транзакции не должен оставить письмо о решении, которого нет.

Три попытки и лог после них. Исключение наружу не выходит: решение уже
принято и записано в журнал, отменять его нельзя, а бесконечный повтор
только занял бы очередь воркера. Дубликат письма при потере ответа SMTP
принимается осознанно: два письма лучше, чем ни одного.
"""

import logging

from celery import shared_task

from apps.moderation.emails import notify_reporter_decision
from apps.moderation.models import Report

logger = logging.getLogger(__name__)

# Пауза перед повтором удваивается: 60, 120, 240 секунд. Почтовый сервис
# обычно возвращается за это время, а частый повтор только занял бы очередь.
RETRY_BACKOFF = 60


@shared_task(bind=True, max_retries=3)
def send_report_decision_email_task(self, report_id):
    """Отправить заявителю письмо о решении жалобы.

    `bind=True` нужен ради `self.retry`: без него задача не знает номер
    попытки и повторялась бы бесконечно.
    """
    report = Report.objects.select_related("reporter").filter(pk=report_id).first()
    if report is None:
        # Жалобу могли удалить, пока задача ждала в очереди. Работать
        # не с чем, и падать из-за этого незачем.
        logger.info("Письмо по жалобе #%s пропущено: записи нет", report_id)
        return

    try:
        notify_reporter_decision(report)
    except Exception as exc:  # noqa: BLE001 — письмо не должно ронять решение
        if self.request.retries >= self.max_retries:
            logger.exception("Письмо заявителю по жалобе #%s не ушло", report_id)
            return
        raise self.retry(
            exc=exc, countdown=RETRY_BACKOFF * 2**self.request.retries
        ) from exc

    logger.info("Письмо заявителю по жалобе #%s отправлено", report_id)


def enqueue_report_decision_email(report) -> None:
    """Поставить письмо заявителю в очередь.

    При `CELERY_TASK_ALWAYS_EAGER` (dev и тесты) задача выполняется сразу
    в текущем процессе: Redis для разработки не обязателен, а поведение
    остаётся тем же.
    """
    send_report_decision_email_task.delay(report.pk)
