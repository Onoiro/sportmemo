"""Оригинал и обработанная версия уезжают в приватное хранилище.

Превью строятся от `display`, а оригинал хранит EXIF и GPS. Политика
бакета одна на всё, поэтому приватность задаётся отдельным бакетом,
а не отсутствием ссылки в разметке.
"""

import apps.core.storage
import apps.photos.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("photos", "0007_photo_source_origin"),
    ]

    operations = [
        migrations.AlterField(
            model_name="photo",
            name="original",
            field=models.ImageField(
                max_length=255,
                storage=apps.core.storage.originals_storage,
                upload_to=apps.photos.models.photo_original_path,
                verbose_name="оригинал",
            ),
        ),
        migrations.AlterField(
            model_name="photo",
            name="processed",
            field=models.ImageField(
                blank=True,
                max_length=255,
                null=True,
                storage=apps.core.storage.originals_storage,
                upload_to=apps.photos.models.photo_processed_path,
                verbose_name="обработанная версия",
            ),
        ),
    ]
