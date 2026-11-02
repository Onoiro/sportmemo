"""Два новых типа журнала: решение по жалобе.

Разбор жалобы не меняет объект, поэтому писать в журнал «скрыть» или
«опубликовать» по цели было бы неправдой (инвариант «всё значимое
логируется» требует правды, а не отметки). Решение записывается по самой
жалобе, и для него нужны свои типы.

Схема не меняется: правится только список допустимых значений `action`
у журнала и его исторической модели. Обе операции лежат в одной миграции,
иначе историческая модель на время отстала бы от основной.
"""

from django.db import migrations, models

ACTIONS = [
    ("hide", "скрыть"),
    ("publish", "опубликовать"),
    ("reject", "отклонить"),
    ("merge", "слить"),
    ("delete", "удалить"),
    ("restore", "восстановить"),
    ("report_resolved", "жалоба решена"),
    ("report_rejected", "жалоба отклонена"),
]


def _action_field(choices):
    return models.CharField(
        choices=choices,
        db_index=True,
        max_length=20,
        verbose_name="действие",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("moderation", "0002_report_sport"),
    ]

    operations = [
        migrations.AlterField(
            model_name="historicalmoderationaction",
            name="action",
            field=_action_field(ACTIONS),
        ),
        migrations.AlterField(
            model_name="moderationaction",
            name="action",
            field=_action_field(ACTIONS),
        ),
    ]
