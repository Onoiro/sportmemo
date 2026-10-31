"""Вид спорта у жалобы: денормализация для области модератора.

Жалоба связана с целью через generic-FK, поэтому фильтр «жалобы моего вида
спорта» без собственного поля невозможен. Существующие жалобы заполняются
по цели одним словарём на модель, а не запросом на строку.

Индекс («sport», «status») добавлен сразу: список жалоб фильтрует по обоим
полям, и второй миграции под индекс не понадобится.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

# Цели жалоб — модели белого списка `apps/comments/targets.py`.
TARGETS = (
    ("photos", "photo", "photos.Photo"),
    ("catalog", "team", "catalog.Team"),
    ("catalog", "event", "catalog.Event"),
)


def backfill_report_sport(apps, schema_editor):
    """Заполнить `sport` у существующих жалоб по их цели.

    Вид спорта собирается одним словарём на модель: запрос на каждую жалобу
    при прогоне миграции был бы лишним.
    """
    ContentType = apps.get_model("contenttypes", "ContentType")
    Report = apps.get_model("moderation", "Report")

    for app_label, model, model_label in TARGETS:
        content_type = ContentType.objects.filter(
            app_label=app_label, model=model
        ).first()
        if content_type is None:
            continue

        Target = apps.get_model(model_label)
        sport_by_pk = dict(Target.objects.values_list("pk", "sport_id"))

        reports = Report.objects.filter(
            content_type=content_type, sport__isnull=True
        )
        for report in reports.iterator():
            sport_id = sport_by_pk.get(report.object_id)
            if sport_id is not None:
                report.sport_id = sport_id
                report.save(update_fields=["sport"])


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0003_site_domain"),
        ("catalog", "0002_initial"),
        ("photos", "0001_initial"),
        ("moderation", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="historicalreport",
            name="sport",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="core.sport",
                verbose_name="вид спорта",
            ),
        ),
        migrations.AddField(
            model_name="report",
            name="sport",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="reports",
                to="core.sport",
                verbose_name="вид спорта",
            ),
        ),
        migrations.AddIndex(
            model_name="report",
            index=models.Index(
                fields=["sport", "status"], name="moderation__sport_i_88f1aa_idx"
            ),
        ),
        migrations.RunPython(backfill_report_sport, migrations.RunPython.noop),
    ]
