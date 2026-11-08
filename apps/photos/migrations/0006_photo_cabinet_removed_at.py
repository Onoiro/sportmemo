"""Поле `cabinet_removed_at`: автор убирает фото из своего списка.

Отдельное поле, а не `deleted_at`: `deleted_at` значит «скрыто решением
модератора», и смешение двух смыслов стёрло бы разницу в админке и истории.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("photos", "0005_remove_photo_duplicate_of"),
    ]

    operations = [
        migrations.AddField(
            model_name="historicalphoto",
            name="cabinet_removed_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name="убрано из кабинета",
            ),
        ),
        migrations.AddField(
            model_name="photo",
            name="cabinet_removed_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name="убрано из кабинета",
            ),
        ),
    ]
