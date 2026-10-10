"""Начальные виды спорта.

Схема мультиспортная, продукт одномспортный: на старте только бейсбол,
UI выбора вида спорта не показывает.
"""

from django.db import migrations

SPORTS = [
    {"name": "Бейсбол", "slug": "baseball", "sort_order": 0},
]


def create_sports(apps, schema_editor):
    Sport = apps.get_model("core", "Sport")
    for item in SPORTS:
        Sport.objects.update_or_create(
            slug=item["slug"],
            defaults={
                "name": item["name"],
                "sort_order": item["sort_order"],
                "is_active": True,
            },
        )


def delete_sports(apps, schema_editor):
    Sport = apps.get_model("core", "Sport")
    Sport.objects.filter(slug__in=[item["slug"] for item in SPORTS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_sports, delete_sports),
    ]
