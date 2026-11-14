"""Маршруты фотографий: загрузка, правка данных, кабинет, страница фото."""

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
    # `edit` стоит до `photo_detail`: иначе тот перехватил бы адрес
    # и отдал бы страницу фото вместо формы. Уборка из кабинета — там же:
    # свои действия должны стоять рядом и до общего адреса объекта.
    path("photos/<int:pk>/edit/", views.photo_edit, name="photo_edit"),
    # Люди на фото — часть правки данных о снимке, поэтому адреса лежат
    # рядом с `edit` и тоже до `photos/<int:pk>/`.
    path(
        "photos/<int:pk>/people/new/",
        views.photo_person_create,
        name="photo_person_create",
    ),
    path(
        "photos/<int:pk>/people/add/",
        views.photo_person_add,
        name="photo_person_add",
    ),
    path(
        "photos/<int:pk>/people/<int:link_pk>/remove/",
        views.photo_person_remove,
        name="photo_person_remove",
    ),
    path("photos/<int:pk>/remove/", views.photo_remove, name="photo_remove"),
    path("photos/<int:pk>/restore/", views.photo_restore, name="photo_restore"),
    path("photos/<int:pk>/", views.photo_detail, name="photo_detail"),
]
