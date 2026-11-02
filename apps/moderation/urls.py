"""Маршруты модерации: жалоба на объект, очередь черновиков и разбор."""

from django.urls import path

from apps.moderation import views

app_name = "moderation"

urlpatterns = [
    # Список объявлен до маршрута решения: `<str:kind>` перехватил бы
    # `queue/` как ключ модели (то же правило, что `teams/new/` в каталоге).
    path("moderation/queue/", views.queue_list, name="queue"),
    path(
        "moderation/queue/<str:kind>/<int:pk>/",
        views.queue_decide,
        name="queue_decide",
    ),
    # Список жалоб — тоже до своего маршрута решения: приоритет должен быть
    # явным, а не зависеть от того, что префиксы пока разные.
    path("moderation/reports/", views.reports_list, name="reports"),
    path(
        "moderation/reports/<int:pk>/",
        views.report_decide,
        name="report_decide",
    ),
    path("reports/add/", views.report_add, name="report_add"),
]
