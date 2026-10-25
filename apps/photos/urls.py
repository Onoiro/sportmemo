"""Маршруты фотографий: загрузка, кабинет автора, страница фото."""

from django.urls import path

from apps.photos import views

app_name = "photos"

urlpatterns = [
    # `upload`, `mine` и `grid` объявлены до `photos/<int:pk>/`: порядок здесь
    # для читателя, но он же делает приоритет наших страниц явным — по тому же
    # правилу, что `teams/new/` в каталоге.
    path("photos/upload/", views.photo_upload, name="photo_upload"),
    path("photos/mine/", views.my_photos, name="my_photos"),
    path("photos/grid/", views.grid, name="grid"),
    path("photos/<int:pk>/", views.photo_detail, name="photo_detail"),
]
