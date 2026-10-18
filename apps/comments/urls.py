"""Маршруты комментариев."""

from django.urls import path

from apps.comments import views

app_name = "comments"

urlpatterns = [
    path("comments/add/", views.add, name="add"),
]
