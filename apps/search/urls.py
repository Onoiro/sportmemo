"""Маршруты поиска."""

from django.urls import path

from apps.search import views

app_name = "search"

urlpatterns = [
    path("search/", views.index, name="index"),
]
