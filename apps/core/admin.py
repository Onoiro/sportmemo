"""Админка ядра."""

from django.contrib import admin

from apps.core.models import Sport


@admin.register(Sport)
class SportAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "sort_order", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("sort_order", "name")
