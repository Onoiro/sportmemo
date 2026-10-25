"""Модели каталога: команды, люди, события.

Все сущности мультиспортные: общие поля (Team/Person/Event) + специфика вида
спорта в `attributes` (JSONB) там, где она нужна. Бейсбольной специфики в
структуре таблиц нет.
"""

from datetime import date

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.utils.dateparse import parse_date
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import (
    MergeableModel,
    SlugModel,
    SoftDeleteModel,
    StatusModel,
    TimeStampedModel,
)


def _as_date(value):
    """Дата из поля, которое может прийти строкой.

    Строки приходят из data-миграций, фикстур и тестов: у них нет `.year`
    и `.month`, поэтому перед разбором значения его нужно привести к дате.
    """
    if value is None or isinstance(value, date):
        return value
    return parse_date(value)


def _month(value, pattern="F"):
    """Название месяца или месяц с годом из активной локали.

    `date_format` берёт форму из каталога переводов Django: «Июль 1980»
    для русского и «July 1980» для английского. Отдельные строки для
    названий месяцев не нужны.
    """
    return date_format(value, pattern)


def _date_range(start, end):
    """Диапазон через длинное тире с неразрывными пробелами."""
    return f"{start}\u00a0—\u00a0{end}"


def _unique_slug(instance, base_slug, queryset, fallback="item"):
    """Подобрать свободный slug с числовым суффиксом при коллизии.

    Используется там, где slug уникален не глобально: люди с одинаковыми
    именами и команды с одинаковыми названиями в одном виде спорта — норма,
    а не ошибка. `queryset` задаёт область уникальности.
    """
    from django.utils.text import slugify

    slug = base_slug or slugify(instance.full_name) or fallback
    candidate = slug
    counter = 2
    while queryset.filter(slug=candidate).exclude(pk=instance.pk).exists():
        candidate = f"{slug}-{counter}"
        counter += 1
    return candidate


class Role(models.TextChoices):
    """Роль человека в команде."""

    PLAYER = "player", _("игрок")
    COACH = "coach", _("тренер")
    MANAGER = "manager", _("руководитель")
    STAFF = "staff", _("персонал")


class Team(TimeStampedModel, SlugModel, StatusModel, SoftDeleteModel, MergeableModel):
    """Команда.

    Создаётся пользователем в статусе `draft` и публикуется модератором.
    """

    url_name = "catalog:team_detail"

    sport = models.ForeignKey(
        "core.Sport",
        verbose_name=_("вид спорта"),
        on_delete=models.PROTECT,
        related_name="teams",
    )
    name = models.CharField(_("название"), max_length=200)
    city = models.CharField(_("город"), max_length=100, blank=True)
    founded_year = models.SmallIntegerField(_("год основания"), null=True, blank=True)
    description = models.TextField(_("описание"), blank=True)
    logo = models.ImageField(
        _("эмблема"), upload_to="teams/logos/", null=True, blank=True
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("создатель"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_teams",
    )
    attributes = models.JSONField(
        _("дополнительные атрибуты"), default=dict, blank=True
    )
    # Полнотекстовый индекс. Заполняется сигналом (`apps/search/signals.py`)
    # и командой `search_reindex`; вручную не редактируется. Поле в истории
    # остаётся пустым: пересчёт идёт через `update()`, а не через `save()`.
    search_vector = SearchVectorField(_("поисковый индекс"), null=True, editable=False)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("команда")
        verbose_name_plural = _("команды")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["sport", "slug"], name="unique_team_slug_per_sport"
            ),
        ]
        indexes = [
            models.Index(fields=["sport", "status"]),
            models.Index(fields=["city"]),
            GinIndex(fields=["search_vector"], name="team_search_vector_gin"),
            GinIndex(
                fields=["name"],
                name="team_name_trgm",
                opclasses=["gin_trgm_ops"],
            ),
            GinIndex(
                fields=["city"],
                name="team_city_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.city})" if self.city else self.name

    @property
    def full_name(self) -> str:
        return f"{self.name} ({self.city})" if self.city else self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify

            base_slug = slugify(self.name, allow_unicode=True)[:200]
            # Уникальность slug — в пределах вида спорта (`UniqueConstraint`).
            # Менеджер по умолчанию, а не `public`: constraint про `deleted_at`
            # не знает, и «свободный» по мнению `public` slug всё равно упал бы
            # на уровне базы.
            self.slug = _unique_slug(
                self, base_slug, Team.objects.filter(sport=self.sport)
            )
        super().save(*args, **kwargs)

    def _merge_relations(self, target):
        """Перенести состав и фото в целевую команду."""
        self.memberships.update(team=target)
        self.photos.update(team=target)
        self.event_teams.update(team=target)


class TeamMembership(TimeStampedModel):
    """Человек в команде — основа навигации «состав по годам»."""

    team = models.ForeignKey(
        Team,
        verbose_name=_("команда"),
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    person = models.ForeignKey(
        "catalog.Person",
        verbose_name=_("человек"),
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    year_from = models.SmallIntegerField(_("с года"), null=True, blank=True)
    year_to = models.SmallIntegerField(_("по год"), null=True, blank=True)
    role = models.CharField(
        _("роль"), max_length=20, choices=Role.choices, default=Role.PLAYER
    )
    number = models.CharField(_("номер"), max_length=10, blank=True)
    position = models.CharField(_("амплуа"), max_length=100, blank=True)
    note = models.TextField(_("примечание"), blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("создатель"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("членство в команде")
        verbose_name_plural = _("членство в командах")
        ordering = ["-year_from", "person__last_name", "person__first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["team", "person", "role", "year_from"],
                name="unique_membership",
            ),
        ]
        indexes = [
            models.Index(fields=["team", "year_from"]),
            models.Index(fields=["person", "year_from"]),
        ]

    def __str__(self):
        years = ""
        if self.year_from or self.year_to:
            years = f" ({self.year_from or '?'}–{self.year_to or '?'})"
        return f"{self.person} — {self.team}{years}"

    @property
    def years_label(self) -> str:
        """Годы в команде подписью: «1994 — 1998», «с 1994», «до 1998».

        Живёт в модели: тем же свойством пользуются страница человека
        и состав команды, а форматов подписи не должно быть два.
        """
        if self.year_from and self.year_to:
            return f"{self.year_from} — {self.year_to}"
        if self.year_from:
            return str(_("с %(year)s") % {"year": self.year_from})
        if self.year_to:
            return str(_("до %(year)s") % {"year": self.year_to})
        return str(_("год не указан"))


class NameVisibility(models.TextChoices):
    """Публичность имени: защита несовершеннолетних."""

    FULL = "full", _("полное имя")
    FIRST_NAME_ONLY = "first_name_only", _("только имя")
    HIDDEN = "hidden", _("скрыто")


class Person(TimeStampedModel, SlugModel, StatusModel, SoftDeleteModel, MergeableModel):
    """Человек на фото — публичная сущность, не требующая аккаунта.

    Не привязан к виду спорта: один и тот же человек может быть в бейсболе
    и крикете. Связь с фото — через `PhotoPerson`, с командой — через
    `TeamMembership`.
    """

    url_name = "catalog:person_detail"

    first_name = models.CharField(_("имя"), max_length=100)
    last_name = models.CharField(_("фамилия"), max_length=100, blank=True)
    middle_name = models.CharField(_("отчество"), max_length=100, blank=True)
    display_name = models.CharField(_("отображаемое имя"), max_length=200, blank=True)
    birth_year = models.SmallIntegerField(_("год рождения"), null=True, blank=True)
    death_year = models.SmallIntegerField(_("год смерти"), null=True, blank=True)
    name_visibility = models.CharField(
        _("видимость имени"),
        max_length=20,
        choices=NameVisibility.choices,
        default=NameVisibility.FULL,
        db_index=True,
    )
    bio = models.TextField(_("биография"), blank=True)
    avatar = models.ForeignKey(
        "photos.Photo",
        verbose_name=_("главное фото"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    is_claimed = models.BooleanField(_("«это я» подтверждено"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("создатель"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_persons",
    )
    attributes = models.JSONField(
        _("дополнительные атрибуты"), default=dict, blank=True
    )
    # Полнотекстовый индекс: `public_name`, биография, имя и отчество,
    # а фамилия и варианты имён — только при полной видимости имени.
    # Правило живёт в `apps/search/indexing.py`, здесь только поле.
    search_vector = SearchVectorField(_("поисковый индекс"), null=True, editable=False)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("человек")
        verbose_name_plural = _("люди")
        ordering = ["last_name", "first_name", "middle_name"]
        constraints = [
            models.UniqueConstraint(fields=["slug"], name="unique_person_slug"),
        ]
        indexes = [
            models.Index(fields=["last_name", "first_name"]),
            models.Index(fields=["status"]),
            GinIndex(fields=["search_vector"], name="person_search_vector_gin"),
            GinIndex(
                fields=["first_name"],
                name="person_first_name_trgm",
                opclasses=["gin_trgm_ops"],
            ),
            GinIndex(
                fields=["last_name"],
                name="person_last_name_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ]

    def __str__(self):
        return self.public_name

    @property
    def full_name(self) -> str:
        """Полное имя в естественном порядке: имя, отчество, фамилия."""
        return " ".join(
            part for part in [self.first_name, self.middle_name, self.last_name] if part
        )

    @property
    def public_name(self) -> str:
        """Имя для публичного контекста с учётом `name_visibility`.

        Фамилии несовершеннолетних не публикуются: при `first_name_only`
        возвращается только имя, при `hidden` — нейтральная подпись.
        """
        if self.name_visibility == NameVisibility.HIDDEN:
            return str(_("Имя не указано"))
        if self.name_visibility == NameVisibility.FIRST_NAME_ONLY:
            return self.first_name
        return self.display_name or self.full_name or self.first_name

    @property
    def is_full_name_public(self) -> bool:
        """Можно ли раскрывать полное имя и варианты имён.

        Варианты имён (прозвище, фамилия при рождении) раскрыли бы скрытую
        фамилию, поэтому правило живёт рядом с `public_name`, а не сравнением
        строк в разметке.
        """
        return self.name_visibility == NameVisibility.FULL

    def save(self, *args, **kwargs):
        if not self.display_name:
            self.display_name = self.full_name
        if not self.slug:
            self.slug = _unique_slug(self, "", Person.objects.all(), fallback="person")
        super().save(*args, **kwargs)

    def _merge_relations(self, target):
        """Перенести связи и варианты имён в целевой объект."""
        self.photo_persons.update(person=target)
        self.memberships.update(person=target)
        self.names.update(person=target)
        # Аватар переносим только если у цели его нет:
        # чужое главное фото не перетираем.
        if self.avatar_id and not target.avatar_id:
            target.avatar = self.avatar
            target.save(update_fields=["avatar", "updated_at"])


class NameKind(models.TextChoices):
    """Вид варианта имени."""

    BIRTH_NAME = "birth_name", _("фамилия при рождении")
    MARRIED_NAME = "married_name", _("фамилия в браке")
    NICKNAME = "nickname", _("прозвище")
    ALT_SPELLING = "alt_spelling", _("другое написание")


class PersonName(TimeStampedModel):
    """Варианты имён — материал для поиска и слияния дубликатов."""

    person = models.ForeignKey(
        Person,
        verbose_name=_("человек"),
        on_delete=models.CASCADE,
        related_name="names",
    )
    value = models.CharField(_("вариант имени"), max_length=200, db_index=True)
    kind = models.CharField(
        _("вид варианта"),
        max_length=20,
        choices=NameKind.choices,
        default=NameKind.ALT_SPELLING,
    )
    note = models.CharField(_("примечание"), max_length=200, blank=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("вариант имени")
        verbose_name_plural = _("варианты имён")
        ordering = ["person", "value"]
        constraints = [
            models.UniqueConstraint(
                fields=["person", "value"], name="unique_person_name"
            ),
        ]
        indexes = [
            # Триграммы по варианту имени: поиск по прозвищу и «похоже на».
            GinIndex(
                fields=["value"],
                name="personname_value_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ]

    def __str__(self):
        return f"{self.value} ({self.get_kind_display()})"


class EventType(models.TextChoices):
    """Тип события — навигация и фильтры."""

    TOURNAMENT = "tournament", _("турнир")
    SERIES = "series", _("серия")
    FRIENDLY = "friendly", _("товарищеская встреча")
    TRAINING = "training", _("тренировка")
    TRIP = "trip", _("поездка")
    CEREMONY = "ceremony", _("церемония")
    OTHER = "other", _("другое")


class Event(TimeStampedModel, SlugModel, StatusModel, SoftDeleteModel, MergeableModel):
    """Событие: турнир, серия, товарищеская встреча.

    Отдельной сущности `Season` нет: навигация «по годам» строится на
    `Event.year` и `Photo.year`.
    """

    url_name = "catalog:event_detail"

    sport = models.ForeignKey(
        "core.Sport",
        verbose_name=_("вид спорта"),
        on_delete=models.PROTECT,
        related_name="events",
    )
    name = models.CharField(_("название"), max_length=300)
    event_type = models.CharField(
        _("тип события"),
        max_length=20,
        choices=EventType.choices,
        default=EventType.TOURNAMENT,
        db_index=True,
    )
    date_from = models.DateField(_("дата начала"), null=True, blank=True)
    date_to = models.DateField(_("дата окончания"), null=True, blank=True)
    year = models.SmallIntegerField(_("год"), null=True, blank=True, db_index=True)
    city = models.CharField(_("город"), max_length=100, blank=True)
    country = models.CharField(_("страна"), max_length=100, blank=True)
    attributes = models.JSONField(
        _("дополнительные атрибуты"), default=dict, blank=True
    )
    description = models.TextField(_("описание"), blank=True)
    # Полнотекстовый индекс: название, место, тип, описание и атрибуты.
    search_vector = SearchVectorField(_("поисковый индекс"), null=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("создатель"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_events",
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("событие")
        verbose_name_plural = _("события")
        ordering = ["-year", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["sport", "slug"], name="unique_event_slug_per_sport"
            ),
        ]
        indexes = [
            models.Index(fields=["sport", "year"]),
            models.Index(fields=["sport", "status"]),
            GinIndex(fields=["search_vector"], name="event_search_vector_gin"),
            GinIndex(
                fields=["name"],
                name="event_name_trgm",
                opclasses=["gin_trgm_ops"],
            ),
            GinIndex(
                fields=["city"],
                name="event_city_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.year})" if self.year else self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify

            # Уникальность slug — в пределах вида спорта (`UniqueConstraint`).
            # Одинаковые названия событий — норма: «Кубок России» проводится
            # каждый год. Менеджер по умолчанию, а не `public`: constraint
            # про `deleted_at` не знает.
            self.slug = _unique_slug(
                self,
                slugify(self.name, allow_unicode=True)[:200] or "event",
                Event.objects.filter(sport=self.sport),
            )
        # Год — денормализация ради навигации: заполняется из date_from,
        # если не задан явно. date_from может прийти строкой
        # (data-миграции, тесты), а строки не имеют .year.
        if not self.year:
            date_from = _as_date(self.date_from)
            if date_from:
                self.year = date_from.year
        super().save(*args, **kwargs)

    @property
    def date_label(self) -> str:
        """Даты события подписью: только месяцы и годы, без чисел дня.

        День съёмки в архиве чаще всего неизвестен, поэтому подпись строится
        из месяцев: «Июль 1980», «Июнь — Июль 1988», «Июль 1988 — Август 1989».
        Если дат нет, но известен год — выводится год. Название месяца берёт
        `date_format` из активной локали, поэтому отдельных строк для месяцев
        не нужно. Живёт в модели: тем же свойством пользуются карточки
        событий и фильтры каталога.
        """
        start = _as_date(self.date_from) or _as_date(self.date_to)
        end = _as_date(self.date_to) or start

        if start and end:
            if (start.year, start.month) == (end.year, end.month):
                return _month(start, "F Y")
            if start.year == end.year:
                return _date_range(_month(start), _month(end, "F Y"))
            return _date_range(_month(start, "F Y"), _month(end, "F Y"))
        if self.year:
            return str(self.year)
        return str(_("дата не указана"))

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()
        if self.date_from and self.date_to and self.date_to < self.date_from:
            raise ValidationError({"date_to": _("Дата окончания раньше даты начала")})

    def _merge_relations(self, target):
        """Перенести фото и участие команд в целевое событие."""
        self.photos.update(event=target)
        self.event_teams.update(event=target)


class EventTeam(TimeStampedModel):
    """Участие команды в событии — навигация «все турниры команды X»."""

    event = models.ForeignKey(
        Event,
        verbose_name=_("событие"),
        on_delete=models.CASCADE,
        related_name="event_teams",
    )
    team = models.ForeignKey(
        Team,
        verbose_name=_("команда"),
        on_delete=models.CASCADE,
        related_name="event_teams",
    )
    result_note = models.CharField(_("результат"), max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("создатель"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("участие команды")
        verbose_name_plural = _("участие команд")
        ordering = ["event", "team"]
        constraints = [
            models.UniqueConstraint(fields=["event", "team"], name="unique_event_team"),
        ]

    def __str__(self):
        return f"{self.team} — {self.event}"
