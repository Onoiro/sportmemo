"""Тесты маршрутизации хранилищ и настроек кеша.

Правило подэтапа 5.2: оригинал и его производная лежат в приватном
хранилище, всё, что видит человек, — в публичном. Ошибка здесь отдаёт
оригинал с EXIF и GPS в публичный бакет, поэтому маршрут проверяется
на уровне полей модели, а не представлений.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.files.storage import default_storage, storages

from apps.catalog.models import Team
from apps.core.storage import originals_storage
from apps.photos.models import Photo

pytestmark = pytest.mark.django_db

BASE_DIR = Path(__file__).resolve().parent.parent

# Значения подставляются тестом, а не берутся из `.env` разработчика:
# проверяется, что настройки читают именно эти переменные окружения.
S3_ENV = {
    "USE_S3": "True",
    "S3_ACCESS_KEY": "test-access-key",
    "S3_SECRET_KEY": "test-secret-key",
    "S3_ENDPOINT": "http://minio:9000",
    "S3_BUCKET": "public-bucket",
    "S3_PRIVATE_BUCKET": "private-bucket",
    "CACHE_URL": "redis://localhost:6379/1",
}

SNAPSHOT_SCRIPT = """
import json
import django

django.setup()

from django.conf import settings
from apps.catalog.models import Team
from apps.photos.models import Photo


def bucket(field):
    storage = field.storage
    return getattr(storage, "bucket_name", None)


print(json.dumps({
    "storages": settings.STORAGES,
    "caches": settings.CACHES,
    "strategy": settings.IMAGEKIT_DEFAULT_CACHEFILE_STRATEGY,
    "buckets": {
        "original": bucket(Photo._meta.get_field("original")),
        "processed": bucket(Photo._meta.get_field("processed")),
        "display": bucket(Photo._meta.get_field("display")),
        "thumbnail": Photo().thumbnail.storage.bucket_name,
        "preview": Photo().preview.storage.bucket_name,
        "logo": bucket(Team._meta.get_field("logo")),
    },
}))
"""


def _settings_snapshot(**overrides):
    """Прочитать настройки и маршрут файлов в отдельном процессе.

    `STORAGES` и `CACHES` вычисляются при импорте `config.settings`, а
    фикстура тестов подменяет `STORAGES`; в текущем процессе подмена
    `USE_S3` уже ничего не пересчитает. Отдельный процесс читает файл
    настроек как есть — без сети и без обращения к boto.
    """
    env = {**os.environ, **S3_ENV, "DJANGO_SETTINGS_MODULE": "config.settings"}
    env.update(overrides)
    result = subprocess.run(
        [sys.executable, "-c", SNAPSHOT_SCRIPT],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


class TestStorageRouting:
    """Маршрут задаёт поле модели, а не представление."""

    def test_original_uses_the_private_alias(self):
        field = Photo._meta.get_field("original")

        assert field._storage_callable is originals_storage
        assert originals_storage() is storages["originals"]

    def test_processed_uses_the_private_alias(self):
        field = Photo._meta.get_field("processed")

        assert field._storage_callable is originals_storage

    def test_public_fields_keep_the_default_storage(self):
        photo = Photo()

        # Явное `storage=` в поле значит «не публичное хранилище»:
        # у `display` и `logo` его быть не должно.
        assert "storage" not in Photo._meta.get_field("display").deconstruct()[3]
        assert "storage" not in Team._meta.get_field("logo").deconstruct()[3]
        # Поле без явного `storage` держит ленивую обёртку `default_storage`,
        # а не сам экземпляр из `storages`: сравнивать с `storages["default"]`
        # по `is` бессмысленно.
        assert photo.display.storage is default_storage
        assert photo.thumbnail.storage is storages["default"]
        assert photo.preview.storage is storages["default"]

    def test_original_and_display_go_to_different_buckets(self):
        buckets = _settings_snapshot()["buckets"]

        assert buckets["original"] == "private-bucket"
        assert buckets["processed"] == "private-bucket"
        assert buckets["display"] == "public-bucket"
        assert buckets["thumbnail"] == "public-bucket"
        assert buckets["preview"] == "public-bucket"
        assert buckets["logo"] == "public-bucket"

    def test_preview_url_is_plain_in_dev(self):
        """В dev подписи нет вовсе: адрес превью должен быть стабильным."""
        photo = Photo(display="photos/display/2026/11/photo.jpg")

        assert photo.preview.url.startswith("/media/")
        assert "X-Amz-Signature" not in photo.preview.url

    def test_page_markup_has_no_signature(self, client, photo):
        """`og:image` строится из превью: подпись в нём ломала карточку."""
        html = client.get(photo.get_absolute_url()).content.decode()

        assert "X-Amz-Signature" not in html


class TestS3StorageOptions:
    """Правило подписи живёт в `OPTIONS` алиасов, а не в общей настройке."""

    def test_public_bucket_serves_unsigned_urls(self):
        options = _settings_snapshot()["storages"]["default"]["OPTIONS"]

        assert options["querystring_auth"] is False
        assert options["bucket_name"] == "public-bucket"

    def test_private_bucket_signs_urls(self):
        options = _settings_snapshot()["storages"]["originals"]["OPTIONS"]

        assert options["querystring_auth"] is True
        assert options["querystring_expire"] == 3600
        assert options["bucket_name"] == "private-bucket"

    def test_originals_never_overwrite_a_file(self):
        options = _settings_snapshot()["storages"]["originals"]["OPTIONS"]

        assert options["file_overwrite"] is False


class TestCacheSettings:
    def test_production_uses_redis_base_one(self):
        """База `/0` занята брокером Celery: общая база стёрла бы задачи."""
        caches = _settings_snapshot(DEBUG="False")["caches"]["default"]

        assert caches["BACKEND"] == "django.core.cache.backends.redis.RedisCache"
        assert caches["LOCATION"].endswith("/1")
        # Состояние превью не должно истекать: иначе imagekit снова начнёт
        # проверять существование файла в S3 при каждом показе.
        assert caches["TIMEOUT"] is None

    def test_dev_does_not_need_redis(self):
        caches = _settings_snapshot(DEBUG="True")["caches"]["default"]

        assert caches["BACKEND"] == ("django.core.cache.backends.locmem.LocMemCache")


class TestPreviewStrategy:
    def test_previews_are_built_on_source_save(self):
        """`Optimistic` не делает HEAD в S3 при каждом показе превью."""
        strategy = _settings_snapshot()["strategy"]

        assert strategy == "imagekit.cachefiles.strategies.Optimistic"
