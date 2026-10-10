"""Модели фотографий: источник, фото, люди на фото."""

from django.conf import settings
from django.db import models
from django.utils.dateparse import parse_date
from django.utils.translation import gettext_lazy as _
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFit
from simple_history.models import HistoricalRecords

from apps.core.models import (
    SoftDeleteModel,
    StatusModel,
    TimeStampedModel,
)


class SourceKind(models.TextChoices):
    """Откуда фото родом."""

    PAPER_PHOTO = "paper_photo", _("бумажное фото")
    NEGATIVE = "negative", _("негатив")
    SCAN = "scan", _("скан")
    DIGITAL = "digital", _("цифровое фото")
    NEWSPAPER = "newspaper", _("газета")
    MAGAZINE = "magazine", _("журнал")
    PROGRAM = "program", _("программа")
    TICKET = "ticket", _("билет")
    OTHER = "other", _("другое")


class Source(TimeStampedModel, SoftDeleteModel):
    """Источник: откуда фото родом.

    Отделено от `Photo`, потому что одна газетная страница = несколько фото,
    а одно фото = один источник.
    """

    kind = models.CharField(
        _("тип источника"),
        max_length=20,
        choices=SourceKind.choices,
        default=SourceKind.PAPER_PHOTO,
        db_index=True,
    )
    title = models.CharField(_("название"), max_length=300, blank=True)
    publication = models.CharField(_("издание"), max_length=200, blank=True)
    date = models.DateField(_("дата"), null=True, blank=True)
    page = models.CharField(_("страница"), max_length=50, blank=True)
    url = models.URLField(_("ссылка"), max_length=500, blank=True)
    holder = models.CharField(
        _("владелец оригинала"),
        max_length=200,
        blank=True,
        help_text=_("Например: «семейный архив Ивановых»"),
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("владелец записи"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sources",
    )
    notes = models.TextField(_("примечания"), blank=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("источник")
        verbose_name_plural = _("источники")
        ordering = ["-date", "title"]

    def __str__(self):
        return self.title or self.get_kind_display()


class ProcessingStatus(models.TextChoices):
    """Стадия обработки — отдельно от статуса публикации."""

    UPLOADED = "uploaded", _("загружено")
    PROCESSING = "processing", _("в обработке")
    PROCESSED = "processed", _("обработано")
    FAILED = "failed", _("ошибка обработки")


class Photo(TimeStampedModel, StatusModel, SoftDeleteModel):
    """Фотография.

    Оригинал никогда не перезаписывается: обработанная версия — производная
    в отдельном поле. Дубликаты не удаляются, а помечаются `duplicate_of`.
    """

    sport = models.ForeignKey(
        "core.Sport",
        verbose_name=_("вид спорта"),
        on_delete=models.PROTECT,
        related_name="photos",
    )
    source = models.ForeignKey(
        Source,
        verbose_name=_("источник"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="photos",
    )
    event = models.ForeignKey(
        "catalog.Event",
        verbose_name=_("событие"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="photos",
    )
    team = models.ForeignKey(
        "catalog.Team",
        verbose_name=_("команда"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="photos",
    )
    year = models.SmallIntegerField(_("год"), null=True, blank=True, db_index=True)
    taken_date = models.DateField(_("дата съёмки"), null=True, blank=True)
    title = models.CharField(_("заголовок"), max_length=300, blank=True)
    caption = models.TextField(_("подпись"), blank=True)

    original = models.ImageField(_("оригинал"), upload_to="photos/original/%Y/%m/")
    processed = models.ImageField(
        _("обработанная версия"),
        upload_to="photos/processed/%Y/%m/",
        null=True,
        blank=True,
    )
    display = models.ImageField(
        _("версия для показа"), upload_to="photos/display/%Y/%m/", null=True, blank=True
    )

    width = models.PositiveIntegerField(_("ширина"), null=True, blank=True)
    height = models.PositiveIntegerField(_("высота"), null=True, blank=True)
    phash = models.CharField(
        _("перцептивный хеш"), max_length=64, blank=True, db_index=True
    )

    duplicate_of = models.ForeignKey(
        "self",
        verbose_name=_("дубликат фото"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="duplicates",
    )

    processing_status = models.CharField(
        _("статус обработки"),
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.UPLOADED,
        db_index=True,
    )
    processing_error = models.TextField(_("ошибка обработки"), blank=True)

    rights_confirmed = models.BooleanField(_("права подтверждены"), default=False)
    rights_confirmed_at = models.DateTimeField(
        _("дата подтверждения прав"), null=True, blank=True
    )
    exif = models.JSONField(_("EXIF"), default=dict, blank=True)
    views_count = models.PositiveIntegerField(_("просмотров"), default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("загрузил"),
        on_delete=models.PROTECT,
        related_name="uploaded_photos",
    )

    # Превью генерируются лениво и кэшируются в хранилище (MinIO).
    thumbnail = ImageSpecField(
        source="original",
        processors=[ResizeToFit(width=400, height=400)],
        format="JPEG",
        options={"quality": 82},
    )
    preview = ImageSpecField(
        source="original",
        processors=[ResizeToFit(width=1280, height=1280)],
        format="JPEG",
        options={"quality": 85},
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("фотография")
        verbose_name_plural = _("фотографии")
        ordering = ["-year", "-created_at"]
        indexes = [
            models.Index(fields=["sport", "year"]),
            models.Index(fields=["sport", "status"]),
            models.Index(fields=["team", "year"]),
            models.Index(fields=["event"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(duplicate_of__isnull=True)
                | ~models.Q(pk=models.F("duplicate_of")),
                name="photo_duplicate_of_not_self",
            ),
        ]

    def __str__(self):
        return self.title or f"Фото #{self.pk}"

    @property
    def is_public(self) -> bool:
        """Фото публикуется только при подтверждённых правах."""
        return (
            self.status == "published" and self.rights_confirmed and not self.is_deleted
        )

    def save(self, *args, **kwargs):
        if not self.year and self.taken_date:
            taken_date = (
                parse_date(self.taken_date)
                if isinstance(self.taken_date, str)
                else self.taken_date
            )
            if taken_date:
                self.year = taken_date.year
        super().save(*args, **kwargs)


class PhotoPersonRole(models.TextChoices):
    """Роль человека на фото."""

    PLAYER = "player", _("игрок")
    COACH = "coach", _("тренер")
    UMPIRE = "umpire", _("судья")
    SPECTATOR = "spectator", _("зритель")
    PHOTOGRAPHER = "photographer", _("фотограф")
    OTHER = "other", _("другое")


class PhotoPerson(TimeStampedModel):
    """Человек на фото.

    Роль важна: на фото есть не только игроки, но и тренеры, судьи, зрители,
    сам фотограф.
    """

    photo = models.ForeignKey(
        Photo,
        verbose_name=_("фотография"),
        on_delete=models.CASCADE,
        related_name="photo_persons",
    )
    person = models.ForeignKey(
        "catalog.Person",
        verbose_name=_("человек"),
        on_delete=models.CASCADE,
        related_name="photo_persons",
    )
    role = models.CharField(
        _("роль"),
        max_length=20,
        choices=PhotoPersonRole.choices,
        default=PhotoPersonRole.PLAYER,
    )
    position = models.CharField(
        _("позиция в кадре"),
        max_length=100,
        blank=True,
        help_text=_("Например: «слева второй»"),
    )
    note = models.TextField(_("примечание"), blank=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("добавил"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    added_at = models.DateTimeField(_("дата добавления"), auto_now_add=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("человек на фото")
        verbose_name_plural = _("люди на фото")
        ordering = ["photo", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["photo", "person"], name="unique_photo_person"
            ),
        ]
        indexes = [models.Index(fields=["person"])]

    def __str__(self):
        return f"{self.person} — {self.photo}"
