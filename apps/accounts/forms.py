"""Формы приложения accounts."""

from allauth.account.forms import SignupForm as AllauthSignupForm
from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class SignupForm(AllauthSignupForm):
    """Регистрация: email + пароль, плюс обязательные согласия.

    Согласия фиксируются датой в момент регистрации: их наличие — юридическая
    защита проекта, а не UX-украшение, поэтому поля обязательные и не имеют
    значения по умолчанию.
    """

    display_name = forms.CharField(
        label=_("Как к вам обращаться"),
        max_length=150,
        required=False,
    )
    terms_accepted = forms.BooleanField(
        label=_("Я принимаю пользовательское соглашение"),
        required=True,
    )
    privacy_accepted = forms.BooleanField(
        label=_("Я принимаю политику конфиденциальности"),
        required=True,
    )
    consent_pd = forms.BooleanField(
        label=_("Я согласен на обработку персональных данных"),
        required=True,
    )

    field_order = [
        "email",
        "display_name",
        "password1",
        "password2",
        "terms_accepted",
        "privacy_accepted",
        "consent_pd",
    ]

    def save(self, request):
        user = super().save(request)
        now = timezone.now()

        user.display_name = self.cleaned_data.get("display_name") or ""
        user.terms_accepted_at = now
        user.privacy_accepted_at = now
        user.consent_pd_at = now
        user.consent_pd_version = _current_consent_version()
        user.save(
            update_fields=[
                "display_name",
                "terms_accepted_at",
                "privacy_accepted_at",
                "consent_pd_at",
                "consent_pd_version",
            ]
        )
        return user


def _current_consent_version() -> str:
    from django.conf import settings

    return settings.SPORTMEMO_CONSENT_PD_VERSION
