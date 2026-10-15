"""Расширение `pg_trgm` для поиска по триграммам.

Триграммы — второй компонент поиска: полнотекстовый (`tsvector`) находит
по части слова, а `pg_trgm` — по опечатке и «похоже на». Этим же механизмом
на этапе 3 будет пользоваться слияние дубликатов.

Расширение создаётся отдельной миграцией, потому что индексы
с `gin_trgm_ops` в `apps/catalog` без него не существуют. Зависимость
задана в `catalog/0003_search_indexes.py`.

`CREATE EXTENSION` требует прав: в dev и CI пользователь базы —
суперпользователь. На боевом сервере это отдельный пункт чек-листа деплоя.
"""

from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        TrigramExtension(),
    ]
