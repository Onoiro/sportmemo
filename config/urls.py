"""Корневая маршрутизация SportMemo."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core.views import healthz

urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("", include("apps.core.urls")),
    path("", include("apps.catalog.urls")),
    path("", include("apps.photos.urls")),
    path("admin/", admin.site.urls),
    # Наши страницы аккаунта объявлены до allauth: адреса не пересекаются,
    # но так видно, что `/accounts/profile/` — наша страница, а не пакета.
    path("accounts/", include("apps.accounts.urls")),
    path("accounts/", include("allauth.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
