"""Правила модерации: статус нового объекта, права и журнал решений.

Правила общие для фото, команды и события, поэтому живут здесь, а не
в `photos/services.py` или `catalog/views.py`: разойдись они по
представлениям, один путь создания публиковал бы сразу, а другой нет,
и журнал решений вёл бы себя по-разному в админке и на сайте.

Жалобы разбираются здесь же: решение по жалобе — тоже решение модератора.
Журнал пишется по самой жалобе, а не по её цели: при разборе претензии
объект не меняется, и запись «скрыть» по нему была бы ложью в журнале.

Модели каталога и фото модуль не импортирует: сервис принимает любой
объект с нужными полями, иначе получился бы цикл приложений.
"""

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.core.models import Status
from apps.moderation.models import (
    ModerationAction,
    ModerationActionType,
    ReportStatus,
)


def log_action(obj, moderator, action, reason=""):
    """Записать решение модератора в журнал.

    Единственная точка записи: админка и публичный интерфейс обязаны
    писать журнал одинаково, а вторая копия правила разошлась бы
    с первой при первом же новом действии.
    """
    return ModerationAction.objects.create(
        moderator=moderator,
        action=action,
        content_type=ContentType.objects.get_for_model(obj),
        object_id=obj.pk,
        reason=reason or "",
    )


def initial_status_for(user) -> Status:
    """Статус, с которым новый объект попадает в архив.

    Доверенный автор (`User.is_trusted`) публикует своё сразу, остальные
    ждут модератора. Вид спорта не участвует: доверие выдаётся человеку,
    а не области, и `is_moderator` — другая способность.

    `getattr` вместо прямого обращения к полю: проверку вызывают и с
    заглушками вместо пользователя, а падение на отсутствующем атрибуте
    было бы шумом, а не находкой.
    """
    if user is not None and getattr(user, "is_trusted", False):
        return Status.PUBLISHED
    return Status.DRAFT


def _require_rights(obj, user):
    """Право на решение проверяет сервис, а не представление.

    Объект передаётся в `can_moderate()`: там уже есть и роль, и область
    по видам спорта, и «админ видит всё». Отдельная проверка роли завела
    бы второе правило рядом с первым.

    `getattr` вместо прямого вызова: у `AnonymousUser` метода нет,
    и отказом должен быть `PermissionDenied`, а не `AttributeError`.
    """
    can_moderate = getattr(user, "can_moderate", None)
    if can_moderate is None or not can_moderate(obj):
        raise PermissionDenied


def _is_decidable(obj) -> bool:
    """Решение по удалённому объекту записало бы действие, которого нет.

    Удалённый объект не показывается никому, кроме админки, и модератор
    не мог его выбрать осознанно. Исключение — восстановление: оно как
    раз про удалённый объект.

    `getattr` вместо прямого поля: сервис вызывают и модели без
    `SoftDeleteModel`.
    """
    return not getattr(obj, "is_deleted", False)


def publish_object(obj, user, reason="") -> bool:
    """Опубликовать объект.

    `False` — объект уже опубликован или удалён: это не ошибка, а гонка
    двух модераторов, и вызывающий сам решает, что сказать человеку.
    """
    _require_rights(obj, user)
    if not _is_decidable(obj) or obj.status == Status.PUBLISHED:
        return False
    obj.publish(moderator=user, reason=reason)
    log_action(obj, user, ModerationActionType.PUBLISH, reason)
    return True


def hide_object(obj, user, reason="") -> bool:
    """Скрыть объект. `False` — он уже скрыт или удалён."""
    _require_rights(obj, user)
    if not _is_decidable(obj) or obj.status == Status.HIDDEN:
        return False
    obj.hide(moderator=user, reason=reason)
    log_action(obj, user, ModerationActionType.HIDE, reason)
    return True


def reject_object(obj, user, reason) -> bool:
    """Отклонить объект. Причина обязательна, её увидит автор.

    Пустая причина даёт `ValueError`: формы проверяют то же заранее, но
    правило живёт здесь, иначе админка и сайт отвечали бы по-разному.
    Права проверяются раньше причины — посторонний не должен узнать
    по ответу, существует ли объект.
    """
    _require_rights(obj, user)
    if not (reason or "").strip():
        raise ValueError("Причина отклонения обязательна")
    if not _is_decidable(obj) or obj.status == Status.REJECTED:
        return False
    obj.reject(moderator=user, reason=reason)
    log_action(obj, user, ModerationActionType.REJECT, reason)
    return True


def soft_delete_object(obj, user, reason="") -> bool:
    """Мягко удалить объект. `False` — он уже удалён."""
    _require_rights(obj, user)
    if not _is_decidable(obj):
        return False
    obj.soft_delete(reason=reason)
    log_action(obj, user, ModerationActionType.DELETE, reason)
    return True


def restore_object(obj, user, reason="") -> bool:
    """Восстановить удалённый объект. `False` — он не удалён."""
    _require_rights(obj, user)
    if not getattr(obj, "is_deleted", False):
        return False
    obj.restore()
    log_action(obj, user, ModerationActionType.RESTORE, reason)
    return True


def _is_open_report(report) -> bool:
    """Разобрана ли жалоба уже.

    Решённую и отклонённую жалобу не трогаем: второе решение переписало бы
    первое, а `take_report` вернул бы разобранную жалобу в работу.
    """
    return report.status in (ReportStatus.NEW, ReportStatus.IN_REVIEW)


def take_report(report, user) -> bool:
    """Взять жалобу в работу. `False` — она уже не новая.

    Журнал не пишет: «взял в работу» — не решение, а служебный шаг.
    След остаётся в истории жалобы, а журнал решений размывать отметками
    о прочтении нельзя.
    """
    _require_rights(report, user)
    if report.status != ReportStatus.NEW:
        return False
    report.status = ReportStatus.IN_REVIEW
    report.save(update_fields=["status", "updated_at"])
    return True


def resolve_report(report, user, resolution) -> bool:
    """Отметить жалобу решённой с текстом решения.

    Пустой текст даёт `ValueError`: форма проверяет то же заранее, но
    правило живёт здесь, иначе админка и сайт отвечали бы по-разному.
    `False` — жалоба уже разобрана: это гонка двух модераторов, а не
    ошибка, и вызывающий сам решает, что сказать человеку.
    """
    _require_rights(report, user)
    if not (resolution or "").strip():
        raise ValueError("Текст решения обязателен")
    if not _is_open_report(report):
        return False
    report.status = ReportStatus.RESOLVED
    report.resolution = resolution
    report.resolved_by = user
    report.resolved_at = timezone.now()
    report.save(
        update_fields=[
            "status",
            "resolution",
            "resolved_by",
            "resolved_at",
            "updated_at",
        ]
    )
    log_action(report, user, ModerationActionType.REPORT_RESOLVED, resolution)
    return True


def reject_report(report, user, resolution) -> bool:
    """Отклонить жалобу с текстом решения. `False` — уже разобрана."""
    _require_rights(report, user)
    if not (resolution or "").strip():
        raise ValueError("Текст решения обязателен")
    if not _is_open_report(report):
        return False
    report.status = ReportStatus.REJECTED
    report.resolution = resolution
    report.resolved_by = user
    report.resolved_at = timezone.now()
    report.save(
        update_fields=[
            "status",
            "resolution",
            "resolved_by",
            "resolved_at",
            "updated_at",
        ]
    )
    log_action(report, user, ModerationActionType.REPORT_REJECTED, resolution)
    return True
