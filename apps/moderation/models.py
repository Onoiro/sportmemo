"""Модели модерации: жалобы и журнал решений."""

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import TimeStampedModel


class ReportReason(models.TextChoices):
    """Причина жалобы."""

    WRONG_PERSON = "wrong_person", _("неверно указан человек")
    WRONG_DATA = "wrong_data", _("неверные данные")
    COPYRIGHT = "copyright", _("нарушение авторских прав")
    INAPPROPRIATE = "inappropriate", _("неприемлемое содержание")
    PRIVACY = "privacy", _("нарушение приватности")
    OTHER = "other", _("другое")


class ReportStatus(models.TextChoices):
    """Состояние жалобы."""

    NEW = "new", _("новая")
    IN_REVIEW = "in_review", _("на рассмотрении")
    RESOLVED = "resolved", _("решена")
    REJECTED = "rejected", _("отклонена")


class Report(TimeStampedModel):
    """Жалоба на объект. Возможна анонимная."""

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("заявитель"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reports",
    )
    content_type = models.ForeignKey(
        ContentType,
        verbose_name=_("тип объекта"),
        on_delete=models.CASCADE,
    )
    object_id = models.PositiveBigIntegerField(_("id объекта"))
    content_object = GenericForeignKey("content_type", "object_id")

    reason = models.CharField(
        _("причина"),
        max_length=20,
        choices=ReportReason.choices,
        default=ReportReason.OTHER,
        db_index=True,
    )
    comment = models.TextField(_("комментарий"), blank=True)
    status = models.CharField(
        _("статус"),
        max_length=20,
        choices=ReportStatus.choices,
        default=ReportStatus.NEW,
        db_index=True,
    )
    resolution = models.TextField(_("решение"), blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("решил"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resolved_reports",
    )
    resolved_at = models.DateTimeField(_("дата решения"), null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("жалоба")
        verbose_name_plural = _("жалобы")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"Жалоба #{self.pk} ({self.get_reason_display()})"


class ModerationActionType(models.TextChoices):
    """Что сделал модератор."""

    HIDE = "hide", _("скрыть")
    PUBLISH = "publish", _("опубликовать")
    REJECT = "reject", _("отклонить")
    MERGE = "merge", _("слить")
    DELETE = "delete", _("удалить")
    RESTORE = "restore", _("восстановить")


class ModerationAction(TimeStampedModel):
    """Журнал решений модератора.

    Дополняет `simple-history`: история фиксирует изменения полей, журнал —
    сами решения (кто, что, почему).
    """

    moderator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("модератор"),
        on_delete=models.PROTECT,
        related_name="moderation_actions",
    )
    action = models.CharField(
        _("действие"),
        max_length=20,
        choices=ModerationActionType.choices,
        db_index=True,
    )
    content_type = models.ForeignKey(
        ContentType,
        verbose_name=_("тип объекта"),
        on_delete=models.CASCADE,
    )
    object_id = models.PositiveBigIntegerField(_("id объекта"))
    content_object = GenericForeignKey("content_type", "object_id")
    reason = models.TextField(_("причина"), blank=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("решение модератора")
        verbose_name_plural = _("решения модераторов")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["action"]),
        ]

    def __str__(self):
        return f"{self.get_action_display()} #{self.object_id} — {self.moderator}"
