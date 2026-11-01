"""Модель пользователя SportMemo."""

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.managers import UserManager


class UserRole(models.TextChoices):
    USER = "user", _("пользователь")
    MODERATOR = "moderator", _("модератор")
    ADMIN = "admin", _("администратор")


class User(AbstractBaseUser, PermissionsMixin):
    """Пользователь: приватный аккаунт с входом по email.

    Отличается от `Person` (публичной сущности «человек на фото»). Связь
    «это я» добавляется отдельной миграцией после появления `catalog.Person`.
    """

    email = models.EmailField(_("email"), unique=True)
    display_name = models.CharField(_("как обращаться"), max_length=150, blank=True)
    role = models.CharField(
        _("роль"),
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.USER,
        db_index=True,
    )
    # Доверенный автор публикует своё сразу (правило статуса — подэтап 4.2),
    # очередь черновиков он не видит. Способность независима от `role`:
    # модератор может быть и автором, и наоборот.
    is_trusted = models.BooleanField(
        _("доверенный автор"), default=False, db_index=True
    )
    # Область модератора. Пустой список — модератор не видит чужих объектов;
    # своё он видит как автор. Администраторы и суперадмины видят всё.
    moderated_sports = models.ManyToManyField(
        "core.Sport",
        verbose_name=_("виды спорта модератора"),
        blank=True,
        related_name="moderators",
        help_text=_(
            "Пустой список — модератор не видит чужих объектов. "
            "Администраторы и суперадмины видят все виды спорта."
        ),
    )

    # «Это я»: связь с публичной сущностью «человек на фото». Добавлена
    # отдельной миграцией после появления catalog.Person, чтобы не было цикла.
    person = models.OneToOneField(
        "catalog.Person",
        verbose_name=_("профиль человека"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="user",
    )

    # --- Согласия (152-ФЗ и пользовательские документы) ---
    terms_accepted_at = models.DateTimeField(
        _("согласие с соглашением"), null=True, blank=True
    )
    privacy_accepted_at = models.DateTimeField(
        _("согласие с политикой"), null=True, blank=True
    )
    consent_pd_at = models.DateTimeField(
        _("согласие на обработку ПД"), null=True, blank=True
    )
    consent_pd_version = models.CharField(
        _("версия согласия на ПД"), max_length=20, blank=True
    )

    is_active = models.BooleanField(_("активен"), default=True)
    is_staff = models.BooleanField(_("доступ в админку"), default=False)
    date_joined = models.DateTimeField(_("дата регистрации"), default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        verbose_name = _("пользователь")
        verbose_name_plural = _("пользователи")
        ordering = ["-date_joined"]

    def __str__(self):
        return self.display_name or self.email

    @property
    def is_moderator(self) -> bool:
        """Есть ли у пользователя модераторская роль.

        Роль без области: область по видам спорта проверяет `can_moderate()`.
        Свойство остаётся для профиля («кто я») и не заменяет проверку прав.
        """
        return self.role in {UserRole.MODERATOR, UserRole.ADMIN} or self.is_superuser

    def moderated_sport_ids(self) -> list[int] | None:
        """Виды спорта области модератора или `None`, если фильтр не нужен.

        `None` — «все виды спорта»: суперадмин и роль `ADMIN`. Пустой список —
        модератор без области: он не видит чужого ничего, и фильтр
        `sport_id__in=[]` честно отдаёт пустую выборку.

        Нужен выборкам: очереди черновиков мало ответа про один объект.
        Своя копия правила в представлении разошлась бы с `can_moderate()`,
        и объект был бы виден в списке, но недоступен по прямой ссылке.
        """
        if self.is_superuser or self.role == UserRole.ADMIN:
            return None
        return list(self.moderated_sports.values_list("pk", flat=True))

    def can_moderate(self, obj=None) -> bool:
        """Может ли пользователь принимать решения по объекту.

        Единственная точка проверки области: страница объекта, форма правки
        и форма фото спрашивают её, а не роль напрямую. Область берётся
        из `moderated_sport_ids()`, поэтому правило годится и для одного
        объекта, и для фильтра выборок.

        `obj=None` — проверка «модератор ли вообще» без области. Объект без
        вида спорта (`Person`) доступен всем модераторам: придумывать человеку
        область значило бы нарушить инвариант «архитектура мультиспортная».

        Пустой `moderated_sports` значит «модератор не видит чужого ничего»:
        ошибка в сторону «не видит» исправляется щелчком в админке, ошибка
        в сторону «видит всё» — утечкой чужого черновика.
        """
        if not self.is_moderator:
            return False
        sport_ids = self.moderated_sport_ids()
        if sport_ids is None:
            return True
        sport_id = getattr(obj, "sport_id", None)
        return True if sport_id is None else sport_id in sport_ids

    @property
    def has_consents(self) -> bool:
        """Заполнены ли все обязательные согласия."""
        return all(
            [self.terms_accepted_at, self.privacy_accepted_at, self.consent_pd_at]
        )
