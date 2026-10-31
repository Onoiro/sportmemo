"""Админка пользователей."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User, UserRole


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = (
        "email",
        "display_name",
        "role",
        "sport_scope",
        "is_trusted",
        "person",
        "is_active",
        "is_staff",
        "date_joined",
    )
    list_filter = ("role", "is_trusted", "moderated_sports", "is_active", "is_staff")
    search_fields = ("email", "display_name", "person__last_name", "person__first_name")
    ordering = ("-date_joined",)
    filter_horizontal = ("moderated_sports", "groups", "user_permissions")
    readonly_fields = (
        "date_joined",
        "last_login",
        "terms_accepted_at",
        "privacy_accepted_at",
        "consent_pd_at",
    )

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Профиль"), {"fields": ("display_name", "person")}),
        (
            _("Модерация"),
            {"fields": ("role", "is_trusted", "moderated_sports")},
        ),
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

    @admin.display(description=_("виды спорта"))
    def sport_scope(self, obj):
        """Область модератора для списка.

        M2M в `list_display` напрямую не вывести, а «все» и «—» читаются
        быстрее перечисления: админ и суперадмин видят всё, у остальных
        перечислены названия назначенных видов спорта.
        """
        if obj.is_superuser or obj.role == UserRole.ADMIN:
            return _("все")
        names = [sport.name for sport in obj.moderated_sports.all()]
        return ", ".join(names) if names else "—"

    # Роль, доверие и область — это права доступа. Модератор со `is_staff`
    # иначе назначил бы себе вид спорта и увидел чужие черновики, поэтому
    # раздел пользователей открыт только суперадмину.
    def has_module_permission(self, request):
        return request is not None and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request is not None and request.user.is_superuser

    def has_add_permission(self, request):
        return request is not None and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request is not None and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request is not None and request.user.is_superuser
