"""Доверенный автор и бэкфилл области модераторов.

До этого подэтапа модератор видел и решал всё. После появления области
поведение на проде не должно измениться: каждому существующему модератору
и администратору назначаются все виды спорта из базы. Суперадмин сузит
область осознанно, когда это понадобится.

Зависимость от `core/0002_seed_sports` обязательна: иначе на свежей базе
бэкфилл отработал бы до появления «Бейсбола» и назначил пустой список —
модератор потерял бы доступ к своим же черновикам.
"""

from django.db import migrations, models

ROLES_WITH_FULL_SCOPE = ["moderator", "admin"]


def assign_all_sports(apps, schema_editor):
    """Назначить все виды спорта модераторам и администраторам.

    Именованная функция модуля, а не замыкание внутри `Migration`: её
    вызывает тест бэкфилла. Вставка массовая, а не цикл `set()`: идиома
    должна быть одна, даже если модераторов пока десяток.
    """
    User = apps.get_model("accounts", "User")
    Sport = apps.get_model("core", "Sport")

    sport_ids = list(Sport.objects.values_list("pk", flat=True))
    user_ids = list(
        User.objects.filter(role__in=ROLES_WITH_FULL_SCOPE).values_list(
            "pk", flat=True
        )
    )
    if not sport_ids or not user_ids:
        return

    Through = User.moderated_sports.through
    Through.objects.bulk_create(
        [
            Through(user_id=user_id, sport_id=sport_id)
            for user_id in user_ids
            for sport_id in sport_ids
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_moderator_sports"),
        ("core", "0002_seed_sports"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_trusted",
            field=models.BooleanField(
                db_index=True, default=False, verbose_name="доверенный автор"
            ),
        ),
        # Откат не снимает области: после деплоя суперадмин мог выставить их
        # вручную, и обратная миграция не должна это затирать.
        migrations.RunPython(assign_all_sports, migrations.RunPython.noop),
    ]
