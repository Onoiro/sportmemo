"""Домен сайта для абсолютных ссылок.

Django создаёт Site с доменом example.com. Письма allauth, og:url и sitemap
строят абсолютные адреса по этому домену, поэтому его нужно заменить на
боевой — иначе письма будут вести на example.com.
"""

from django.conf import settings
from django.db import migrations

DOMAIN = "sportmemo.ru"
NAME = "SportMemo"
DEFAULT_DOMAIN = "example.com"
DEFAULT_NAME = "example.com"


def set_domain(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    Site.objects.update_or_create(
        pk=settings.SITE_ID,
        defaults={"domain": DOMAIN, "name": NAME},
    )


def reset_domain(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    Site.objects.update_or_create(
        pk=settings.SITE_ID,
        defaults={"domain": DEFAULT_DOMAIN, "name": DEFAULT_NAME},
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_seed_sports"),
        ("sites", "0002_alter_domain_unique"),
    ]

    operations = [
        migrations.RunPython(set_domain, reset_domain),
    ]
