"""Убрать `duplicate_of` и заменить constraint «не на себя».

Поле `merged_into` уже заполнено миграцией 0004, поэтому удаление старого
поля не теряет данные.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("photos", "0004_photo_merged_into"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="photo",
            name="photo_duplicate_of_not_self",
        ),
        migrations.RemoveField(
            model_name="historicalphoto",
            name="duplicate_of",
        ),
        migrations.RemoveField(
            model_name="photo",
            name="duplicate_of",
        ),
        migrations.AddConstraint(
            model_name="photo",
            constraint=models.CheckConstraint(
                condition=models.Q(merged_into__isnull=True)
                | ~models.Q(pk=models.F("merged_into")),
                name="photo_merged_into_not_self",
            ),
        ),
    ]
