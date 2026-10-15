"""Представления приложения accounts: страница профиля."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.accounts.forms import ProfileForm
from apps.core.seo import page_meta


@login_required
@require_http_methods(["GET", "POST"])
def profile(request):
    """Профиль: свои данные, согласия и ссылки на смену пароля и email.

    После сохранения — редирект на себя: обновление страницы не должно
    повторять отправку формы.
    """
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, _("Профиль обновлён"))
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=request.user)

    context = {
        "profile_form": form,
        "person": request.user.person,
        # Личный кабинет в поиске не нужен: он доступен только владельцу,
        # и в выдаче выглядел бы как ошибка.
        "meta": page_meta(request, title=_("Профиль"), noindex=True, nofollow=True),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"label": _("Профиль")},
        ],
    }
    return render(request, "accounts/profile.html", context)
