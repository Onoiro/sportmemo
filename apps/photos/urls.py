"""Маршруты фотографий: страница фото и фрагмент галереи."""

from django.urls import path

from apps.photos import views

app_name = "photos"

urlpatterns = [
    # `grid` объявлен первым: сначала фрагмент, потом объект. Строку
    # `photos/<int:pk>/` маршрут `photos/grid/` не перехватит — порядок
    # здесь для читателя, а не для разрешения URL.
    path("photos/grid/", views.grid, name="grid"),
    path("photos/<int:pk>/", views.photo_detail, name="photo_detail"),
]
