"""Модели комментариев: сам комментарий и настройка премодерации."""

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import models
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import SoftDeleteModel, StatusModel, TimeStampedModel

# Ключ кеша настройки. Премодерация читается на каждом новом комментарии,
# и запрос к базе на каждый из них — лишняя работа.
SETTINGS_CACHE_KEY = "comments:settings"


class CommentSettings(TimeStampedModel):
    """Настройки комментариев. Одна строка на весь проект.

    Singleton, а не набор констант в `settings.py`: переключать премодерацию
    должен модератор из админки, без правки кода и деплоя. Строка создаётся
    data-миграцией; если её нет (например, тестовая база без миграций),
    `load()` отдаёт объект с дефолтом, а не падает.
    """

    premoderation = models.BooleanField(
        _("премодерация комментариев"),
        default=False,
        help_text=_(
            "Комментарий публикуется сразу. Включите, если поток потребует "
            "проверки перед публикацией."
        ),
    )

    class Meta:
        verbose_name = _("настройки комментариев")
        verbose_name_plural = _("настройки комментариев")

    def __str__(self):
        return str(_("Настройки комментариев"))

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        cache.delete(SETTINGS_CACHE_KEY)

    @classmethod
    def load(cls):
        """Текущая настройка, с кешем.

        Настройки нет — возвращаем несохранённый объект с дефолтом: правило
        «премодерация выключена» должно работать даже на базе без миграции.
        """
        cached = cache.get(SETTINGS_CACHE_KEY)
        if cached is not None:
            return cached
        instance = cls.objects.order_by("pk").first() or cls()
        cache.set(SETTINGS_CACHE_KEY, instance)
        return instance


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
