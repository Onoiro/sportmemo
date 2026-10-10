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
        """Может ли пользователь принимать модераторские решения."""
        return self.role in {UserRole.MODERATOR, UserRole.ADMIN} or self.is_superuser

    @property
    def has_consents(self) -> bool:
        """Заполнены ли все обязательные согласия."""
        return all(
            [self.terms_accepted_at, self.privacy_accepted_at, self.consent_pd_at]
        )
