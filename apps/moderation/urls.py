"""Маршруты модерации: жалоба на объект."""

from django.urls import path

from apps.moderation import views

app_name = "moderation"

urlpatterns = [
    path("reports/add/", views.report_add, name="report_add"),
]
