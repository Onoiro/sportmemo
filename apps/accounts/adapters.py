"""Адаптеры django-allauth."""

from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings


class AccountAdapter(DefaultAccountAdapter):
    """Адаптер проекта.

    Отличие от стандартного: при регистрации пользователю проставляется
    роль по умолчанию (обычный пользователь), чтобы роль никогда не
    оставалась пустой.
    """

    def is_open_for_signup(self, request):
        """Открыта ли регистрация.

        В allauth 65 настройки `ACCOUNT_ALLOW_REGISTRATION` нет: точка
        выключения — этот метод (`allauth/account/mixins.py`). Своё имя
        настройки не конфликтует с пространством `ACCOUNT_*` пакета.
        """
        return settings.SPORTMEMO_SIGNUP_OPEN

    def save_user(self, request, user, form, commit=True):
        user = super().save_user(request, user, form, commit=False)
        from apps.accounts.models import UserRole

        if not user.role:
            user.role = UserRole.USER
        if commit:
            user.save()
        return user
