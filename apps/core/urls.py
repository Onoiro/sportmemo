"""Маршруты публичных страниц: главная и годы."""

from django.urls import path

from apps.core import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("rules/", views.rules, name="rules"),
    path("years/", views.year_list, name="year_list"),
    path("years/<int:year>/", views.year_detail, name="year_detail"),
]
