"""Абстрактные базовые модели и общие сущности SportMemo."""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    """Единый аудит времени создания и изменения."""

    created_at = models.DateTimeField(_("создано"), auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(_("изменено"), auto_now=True)

    class Meta:
        abstract = True


class SlugModel(models.Model):
    """Человекочитаемый URL.

    Подклассы задают `url_name` — имя маршрута для `get_absolute_url()`.
    """

    slug = models.SlugField(_("slug"), max_length=200, allow_unicode=True)
    url_name: str | None = None

    class Meta:
        abstract = True

    def get_absolute_url(self):
        if not self.url_name:
            raise NotImplementedError("Подкласс SlugModel должен задать url_name")
        from django.urls import reverse

        return reverse(self.url_name, kwargs={"slug": self.slug})


class Status(models.TextChoices):
    """Статус публикации содержательной сущности."""

    DRAFT = "draft", _("черновик")
    PUBLISHED = "published", _("опубликовано")
    HIDDEN = "hidden", _("скрыто")
    REJECTED = "rejected", _("отклонено")


class StatusModel(models.Model):
    """Публикация и модерация: одинаково для всех содержательных сущностей."""

    status = models.CharField(
        _("статус"),
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("модератор"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    moderated_at = models.DateTimeField(_("дата модерации"), null=True, blank=True)
    moderation_reason = models.TextField(_("причина решения"), blank=True)

    class Meta:
        abstract = True

    @property
    def is_published(self) -> bool:
        return self.status == Status.PUBLISHED

    def publish(self, moderator=None, reason=""):
        self.status = Status.PUBLISHED
        self.moderated_by = moderator
        self.moderated_at = timezone.now()
        self.moderation_reason = reason
        self.save(
            update_fields=[
                "status",
                "moderated_by",
                "moderated_at",
                "moderation_reason",
                "updated_at",
            ]
        )

    def hide(self, moderator=None, reason=""):
        self.status = Status.HIDDEN
        self.moderated_by = moderator
        self.moderated_at = timezone.now()
        self.moderation_reason = reason
        self.save(
            update_fields=[
                "status",
                "moderated_by",
                "moderated_at",
                "moderation_reason",
                "updated_at",
            ]
        )

    def reject(self, moderator=None, reason=""):
        self.status = Status.REJECTED
        self.moderated_by = moderator
        self.moderated_at = timezone.now()
        self.moderation_reason = reason
        self.save(
            update_fields=[
                "status",
                "moderated_by",
                "moderated_at",
                "moderation_reason",
                "updated_at",
            ]
        )


class SoftDeleteQuerySet(models.QuerySet):
    """Выборки с учётом «мягкого удаления»."""

    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def deleted(self):
        return self.filter(deleted_at__isnull=False)


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    """Менеджер по умолчанию: отдаёт все объекты, включая удалённые.

    Публичные выборки используют `public`, чтобы удалённые и слитые объекты
    не попадали в выдачу. Менеджер по умолчанию намеренно не фильтрует:
    админка, история и слияние дубликатов должны видеть всё.
    """


class PublicManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    """Менеджер для публичных выборок: без удалённых объектов.

    Слитые дубликаты сюда попадают автоматически: `merge_into` помечает
    источник как удалённый, поэтому фильтра по `merged_into` не требуется.
    """

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class SoftDeleteModel(models.Model):
    """«Удаление» по требованию без потери связей и истории."""

    deleted_at = models.DateTimeField(
        _("удалено"), null=True, blank=True, db_index=True
    )
    deleted_reason = models.TextField(_("причина удаления"), blank=True)

    objects = SoftDeleteManager()
    public = PublicManager()

    class Meta:
        abstract = True

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self, reason=""):
        self.deleted_at = timezone.now()
        self.deleted_reason = reason
        self.save(update_fields=["deleted_at", "deleted_reason", "updated_at"])

    def restore(self):
        self.deleted_at = None
        self.deleted_reason = ""
        self.save(update_fields=["deleted_at", "deleted_reason", "updated_at"])


class MergeableModel(models.Model):
    """Слияние дубликатов вместо удаления.

    `merge_into(target)` помечает текущий объект как поглощённый и переносит
    связи. Перенос специфичен для каждой модели и реализуется в
    `_merge_relations(target)`. Повторный вызов идемпотентен.
    """

    merged_into = models.ForeignKey(
        "self",
        verbose_name=_("слит в"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="merged_from",
    )

    class Meta:
        abstract = True

    @property
    def is_merged(self) -> bool:
        return self.merged_into_id is not None

    def merge_into(self, target, reason=""):
        """Перенести связи в `target` и пометить текущий объект
        как поглощённый.
        """
        if self.pk == target.pk:
            raise ValueError("Нельзя слить объект с самим собой")
        if self.merged_into_id == target.pk:
            return target  # идемпотентность

        self._merge_relations(target)

        self.merged_into = target
        if not self.moderation_reason and reason:
            self.moderation_reason = reason
        self.save()

        if hasattr(self, "soft_delete") and not self.is_deleted:
            self.soft_delete(reason=reason or _("слито с дубликатом"))
        return target

    def _merge_relations(self, target):
        """Точка расширения: перенос связей в подклассах."""
        raise NotImplementedError(
            "Подкласс MergeableModel должен реализовать _merge_relations()"
        )


class Sport(TimeStampedModel):
    """Вид спорта.

    Схема мультиспортная, продукт одномспортный: на старте одна запись —
    «Бейсбол», UI выбора вида спорта не показывает.
    """

    name = models.CharField(_("название"), max_length=100)
    slug = models.SlugField(_("slug"), max_length=100, unique=True, allow_unicode=True)
    sort_order = models.SmallIntegerField(_("порядок сортировки"), default=0)
    is_active = models.BooleanField(_("активен"), default=True)

    class Meta:
        verbose_name = _("вид спорта")
        verbose_name_plural = _("виды спорта")
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name
