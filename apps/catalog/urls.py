"""Маршруты каталога: команды и события."""

from django.urls import path

from apps.catalog import views

app_name = "catalog"

urlpatterns = [
    path("teams/", views.team_list, name="team_list"),
    path("events/", views.event_list, name="event_list"),
]
