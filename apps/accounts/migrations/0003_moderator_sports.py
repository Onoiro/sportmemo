"""Область модератора: виды спорта, которыми он вправе заниматься.

Поле добавлено отдельной миграцией от `is_trusted` и бэкфилла: связь M2M
нужна раньше, чем бэкфилл сможет её заполнить, а порядок операций должен
читаться в истории миграций, а не угадываться.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_add_person_link"),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="moderated_sports",
            field=models.ManyToManyField(
                blank=True,
                help_text=(
                    "Пустой список — модератор не видит чужих объектов. "
                    "Администраторы и суперадмины видят все виды спорта."
                ),
                related_name="moderators",
                to="core.sport",
                verbose_name="виды спорта модератора",
            ),
        ),
    ]
