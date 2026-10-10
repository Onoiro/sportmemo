"""Админка пользователей."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = (
        "email",
        "display_name",
        "role",
        "person",
        "is_active",
        "is_staff",
        "date_joined",
    )
    list_filter = ("role", "is_active", "is_staff", "is_superuser")
    search_fields = ("email", "display_name", "person__last_name", "person__first_name")
    ordering = ("-date_joined",)
    readonly_fields = (
        "date_joined",
        "last_login",
        "terms_accepted_at",
        "privacy_accepted_at",
        "consent_pd_at",
    )

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Профиль"), {"fields": ("display_name", "role", "person")}),
        (
            _("Согласия"),
            {
                "fields": (
                    "terms_accepted_at",
                    "privacy_accepted_at",
                    "consent_pd_at",
                    "consent_pd_version",
                ),
            },
        ),
        (
            _("Права"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (_("Даты"), {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "display_name", "password1", "password2"),
            },
        ),
    )
