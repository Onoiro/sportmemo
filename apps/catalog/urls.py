"""Маршруты каталога: команды и события."""

from django.urls import path

from apps.catalog import views

app_name = "catalog"

urlpatterns = [
    # Списки объявлены до детальных маршрутов: иначе `<uslug:slug>`
    # перехватил бы адрес `teams/` и `events/`. Конвертер `uslug`
    # (apps/core/converters.py) вместо стандартного `<slug:...>`:
    # slug'и хранятся с кириллицей.
    path("teams/", views.team_list, name="team_list"),
    path("teams/<uslug:slug>/", views.team_detail, name="team_detail"),
    path("events/", views.event_list, name="event_list"),
    path("events/<uslug:slug>/", views.event_detail, name="event_detail"),
]
