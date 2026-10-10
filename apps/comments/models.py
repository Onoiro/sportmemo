"""Модель комментариев."""

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import SoftDeleteModel, StatusModel, TimeStampedModel


class Comment(TimeStampedModel, StatusModel, SoftDeleteModel):
    """Комментарий к любой содержательной сущности.

    GenericForeignKey позволяет комментировать фото, события, команды и людей
    без отдельных таблиц на каждый тип.
    """

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("автор"),
        on_delete=models.PROTECT,
        related_name="comments",
    )
    text = models.TextField(_("текст"))
    parent = models.ForeignKey(
        "self",
        verbose_name=_("ответ на"),
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="replies",
    )

    content_type = models.ForeignKey(
        ContentType,
        verbose_name=_("тип объекта"),
        on_delete=models.CASCADE,
    )
    object_id = models.PositiveBigIntegerField(_("id объекта"))
    content_object = GenericForeignKey("content_type", "object_id")

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("комментарий")
        verbose_name_plural = _("комментарии")
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"Комментарий #{self.pk} от {self.author}"

    @property
    def is_reply(self) -> bool:
        return self.parent_id is not None
