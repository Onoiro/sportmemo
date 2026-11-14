"""Происхождение снимка: автор отвечает двумя простыми вопросами.

`source` остаётся справочником модератора и автоматически не создаётся,
поэтому у фото появляется собственный простой ответ автора. Пустая строка
значит «не указано» и отличается от осознанного «не знаю».
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("photos", "0006_photo_cabinet_removed_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="historicalphoto",
            name="source_note",
            field=models.CharField(
                blank=True, max_length=200, verbose_name="уточнение"
            ),
        ),
        migrations.AddField(
            model_name="historicalphoto",
            name="source_origin",
            field=models.CharField(
                blank=True,
                choices=[
                    ("personal", "из моего личного архива"),
                    ("other_archive", "из архива другого человека"),
                    ("press", "газета, журнал, книга"),
                    ("internet", "из интернета"),
                    ("unknown", "не знаю"),
                ],
                db_index=True,
                default="",
                max_length=20,
                verbose_name="происхождение снимка",
            ),
        ),
        migrations.AddField(
            model_name="photo",
            name="source_note",
            field=models.CharField(
                blank=True, max_length=200, verbose_name="уточнение"
            ),
        ),
        migrations.AddField(
            model_name="photo",
            name="source_origin",
            field=models.CharField(
                blank=True,
                choices=[
                    ("personal", "из моего личного архива"),
                    ("other_archive", "из архива другого человека"),
                    ("press", "газета, журнал, книга"),
                    ("internet", "из интернета"),
                    ("unknown", "не знаю"),
                ],
                db_index=True,
                default="",
                max_length=20,
                verbose_name="происхождение снимка",
            ),
        ),
    ]
