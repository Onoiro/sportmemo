"""Маршруты приложения accounts: страница профиля.

Подключаются перед `allauth.urls`: пути не пересекаются, но порядок
делает приоритет наших страниц явным.
"""

from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("profile/", views.profile, name="profile"),
]
