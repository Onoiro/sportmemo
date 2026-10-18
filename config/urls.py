"""Корневая маршрутизация SportMemo."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps import views as sitemap_views
from django.urls import include, path

from apps.core.sitemaps import (
    EventSitemap,
    PersonSitemap,
    PhotoSitemap,
    StaticSitemap,
    TeamSitemap,
)
from apps.core.views import healthz, robots_txt

# Секции карты сайта. Имена маршрутов задаёт Django: индекс строит ссылки
# на секции через `reverse()` по фиксированным именам, и переименование
# «по-своему» дало бы битые ссылки в `sitemap.xml`.
SITEMAPS = {
    "pages": StaticSitemap,
    "teams": TeamSitemap,
    "events": EventSitemap,
    "people": PersonSitemap,
    "photos": PhotoSitemap,
}

urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("robots.txt", robots_txt, name="robots_txt"),
    path(
        "sitemap.xml",
        sitemap_views.index,
        {"sitemaps": SITEMAPS},
        name="django.contrib.sitemaps.views.index",
    ),
    path(
        "sitemap-<section>.xml",
        sitemap_views.sitemap,
        {"sitemaps": SITEMAPS},
        name="django.contrib.sitemaps.views.sitemap",
    ),
    path("", include("apps.core.urls")),
    path("", include("apps.catalog.urls")),
    path("", include("apps.photos.urls")),
    path("", include("apps.search.urls")),
    path("", include("apps.comments.urls")),
    path("", include("apps.moderation.urls")),
    path("admin/", admin.site.urls),
    # Наши страницы аккаунта объявлены до allauth: адреса не пересекаются,
    # но так видно, что `/accounts/profile/` — наша страница, а не пакета.
    path("accounts/", include("apps.accounts.urls")),
    path("accounts/", include("allauth.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
