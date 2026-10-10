"""Адаптеры django-allauth."""

from allauth.account.adapter import DefaultAccountAdapter


class AccountAdapter(DefaultAccountAdapter):
    """Адаптер проекта.

    Отличие от стандартного: при регистрации пользователю проставляется
    роль по умолчанию (обычный пользователь), чтобы роль никогда не
    оставалась пустой.
    """

    def save_user(self, request, user, form, commit=True):
        user = super().save_user(request, user, form, commit=False)
        from apps.accounts.models import UserRole

        if not user.role:
            user.role = UserRole.USER
        if commit:
            user.save()
        return user
