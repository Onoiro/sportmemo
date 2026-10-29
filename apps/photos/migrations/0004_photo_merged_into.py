"""Слияние дубликатов: `merged_into` вместо самодельного `duplicate_of`.

Поле добавляется рядом со старым, данные переносятся, и только после этого
старое поле удаляется (миграция 0005). Так перенос не зависит от порядка
операций в одной миграции и откатывается шаг за шагом.
"""

import django.db.models.deletion
from django.db import migrations, models


def copy_duplicates(apps, schema_editor):
    """Перенести `duplicate_of` в `merged_into` и пометить дубликаты удалёнными.

    Без пометки об удалении слитые фото остались бы в публичной выдаче:
    `public` фильтрует по `deleted_at`, а не по `merged_into`. Причина
    проставляется та же, что у `MergeableModel.merge_into()`.
    """
    Photo = apps.get_model("photos", "Photo")
    HistoricalPhoto = apps.get_model("photos", "HistoricalPhoto")

    Photo.objects.filter(duplicate_of__isnull=False).update(
        merged_into_id=models.F("duplicate_of_id")
    )
    HistoricalPhoto.objects.filter(duplicate_of__isnull=False).update(
        merged_into_id=models.F("duplicate_of_id")
    )

    Photo.objects.filter(merged_into__isnull=False, deleted_at__isnull=True).update(
        deleted_at=models.F("updated_at"),
        deleted_reason="слито с дубликатом",
    )


def restore_duplicates(apps, schema_editor):
    """Вернуть данные в `duplicate_of`.

    `deleted_at` не откатывается: восстановление удалённого объекта —
    решение человека, а не миграции.
    """
    Photo = apps.get_model("photos", "Photo")
    HistoricalPhoto = apps.get_model("photos", "HistoricalPhoto")

    Photo.objects.filter(merged_into__isnull=False).update(
        duplicate_of_id=models.F("merged_into_id")
    )
    HistoricalPhoto.objects.filter(merged_into__isnull=False).update(
        duplicate_of_id=models.F("merged_into_id")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("photos", "0003_photo_processing_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="historicalphoto",
            name="merged_into",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="photos.photo",
                verbose_name="слит в",
            ),
        ),
        migrations.AddField(
            model_name="photo",
            name="merged_into",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="merged_from",
                to="photos.photo",
                verbose_name="слит в",
            ),
        ),
        migrations.RunPython(copy_duplicates, restore_duplicates),
    ]
