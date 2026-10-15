"""Переиндексация поискового индекса.

Нужна в трёх случаях:

- после появления поля `search_vector` — объекты, созданные раньше, индекса
  не имеют;
- после массовых правок, которые идут мимо сигналов (`queryset.update()`,
  загрузка фикстур);
- после изменения правил сборки текста в `apps/search/indexing.py`.

Команда идемпотентна: текст собирается из полей объекта, поэтому повторный
запуск даёт те же значения. Записи в историю и сдвига `updated_at` не будет:
пересчёт идёт через `update()` (см. `indexing.py`).
"""

from django.core.management.base import BaseCommand

from apps.catalog.models import Event, Person, Team
from apps.photos.models import Photo
from apps.search.indexing import reindex_queryset

# Порядок вывода. `Person` берётся с `prefetch_related`: варианты имён
# входят в текст человека, и без предзагрузки на каждого человека шёл бы
# отдельный запрос.
MODELS = {
    "team": (Team, "команды", Team.objects.all()),
    "person": (Person, "люди", Person.objects.prefetch_related("names")),
    "event": (Event, "события", Event.objects.all()),
    "photo": (Photo, "фотографии", Photo.objects.all()),
}


class Command(BaseCommand):
    help = "Пересчитать поисковый индекс архива"

    def add_arguments(self, parser):
        parser.add_argument(
            "--model",
            choices=sorted(MODELS),
            help="Пересчитать только одну модель (по умолчанию — все)",
        )

    def handle(self, *args, **options):
        names = [options["model"]] if options["model"] else sorted(MODELS)
        total = 0
        for name in names:
            model, label, queryset = MODELS[name]
            count = reindex_queryset(model, queryset)
            total += count
            self.stdout.write(f"{label}: {count}")
        self.stdout.write(self.style.SUCCESS(f"Всего пересчитано: {total}"))
