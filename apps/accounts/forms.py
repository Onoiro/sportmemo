"""Формы приложения accounts."""

from allauth.account import forms as allauth_forms
from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User


def apply_bootstrap_classes(fields):
    """Bootstrap-классы виджетам: шаблоны allauth рендерят поля как есть."""
    for field in fields.values():
        widget = field.widget
        if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect)):
            css_class = "form-check-input"
        elif isinstance(widget, forms.Select):
            css_class = "form-select"
        else:
            css_class = "form-control"
        existing = widget.attrs.get("class", "")
        widget.attrs["class"] = f"{existing} {css_class}".strip()


class BootstrapFormMixin:
    """Ставит Bootstrap-классы полям после инициализации формы.

    Порядок важен: allauth добавляет и удаляет поля в своём `__init__`,
    поэтому классы навешиваются только после `super().__init__()`.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_bootstrap_classes(self.fields)


class ProfileForm(BootstrapFormMixin, forms.ModelForm):
    """Профиль: меняется только «как к вам обращаться».

    Email и пароль меняются на своих страницах allauth: поле в профиле
    создало бы второе место, где то же значение правится иначе.
    """

    class Meta:
        model = User
        fields = ["display_name"]
        labels = {"display_name": _("Как к вам обращаться")}
        widgets = {"display_name": forms.TextInput(attrs={"autocomplete": "name"})}


class LoginForm(BootstrapFormMixin, allauth_forms.LoginForm):
    pass


class ResetPasswordForm(BootstrapFormMixin, allauth_forms.ResetPasswordForm):
    pass


class ResetPasswordKeyForm(BootstrapFormMixin, allauth_forms.ResetPasswordKeyForm):
    pass


class ChangePasswordForm(BootstrapFormMixin, allauth_forms.ChangePasswordForm):
    pass


class SetPasswordForm(BootstrapFormMixin, allauth_forms.SetPasswordForm):
    pass


class AddEmailForm(BootstrapFormMixin, allauth_forms.AddEmailForm):
    pass


class ReauthenticateForm(BootstrapFormMixin, allauth_forms.ReauthenticateForm):
    pass


class SignupForm(BootstrapFormMixin, allauth_forms.SignupForm):
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
