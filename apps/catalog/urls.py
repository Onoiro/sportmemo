"""Маршруты каталога: команды, события и люди."""

from django.urls import path

from apps.catalog import views

app_name = "catalog"

urlpatterns = [
    # Списки объявлены до детальных маршрутов: иначе `<uslug:slug>`
    # перехватил бы адрес `teams/` и `events/`. Конвертер `uslug`
    # (apps/core/converters.py) вместо стандартного `<slug:...>`:
    # slug'и хранятся с кириллицей.
    path("teams/", views.team_list, name="team_list"),
    # `new` и `mine` объявлены до `<uslug:slug>`: конвертер `uslug`
    # пропускает любые буквы и дефисы, поэтому перехватил бы оба адреса
    # и увёл их в `team_detail`.
    path("teams/new/", views.team_create, name="team_create"),
    path("teams/mine/", views.my_teams, name="my_teams"),
    path("teams/<uslug:slug>/", views.team_detail, name="team_detail"),
    path("teams/<uslug:slug>/edit/", views.team_update, name="team_update"),
    path("events/", views.event_list, name="event_list"),
    # `new` и `mine` объявлены до `<uslug:slug>` — по тому же правилу,
    # что у команд: конвертер пропускает любые буквы и дефисы.
    path("events/new/", views.event_create, name="event_create"),
    path("events/mine/", views.my_events, name="my_events"),
    path("events/<uslug:slug>/", views.event_detail, name="event_detail"),
    path("events/<uslug:slug>/edit/", views.event_update, name="event_update"),
    # Список людей не создаём: попасть на страницу человека можно только
    # с фото или из состава команды (решение мастер-плана).
    path("people/<uslug:slug>/", views.person_detail, name="person_detail"),
]
