"""Пересборка превью фотографий.

Стратегия `Optimistic` строит превью по сигналу сохранения `display`
и не проверяет их существование при показе: потерянный файл превью сам
не вернётся, а страница покажет битую картинку. Эта команда — способ
вернуть файлы.

Штатная `generateimages` для этого не годится: она вызывает `generate()`
без `force`, а состояние «превью есть» живёт в кеше, поэтому на фото,
у которого файл пропал, команда ничего не делает.

Ошибка одного фото не прерывает проход: иначе одно битое фото остановило
бы пересборку всего архива.
"""

from django.core.management.base import BaseCommand

from apps.photos.models import Photo

# Имена `ImageSpecField` модели. Список один на проверку и на пересборку:
# разойдись они — команда считала бы фото готовым, не пересобрав половину.
PREVIEW_FIELDS = ("thumbnail", "preview")


class Command(BaseCommand):
    help = "Пересобрать превью фотографий (по умолчанию — только отсутствующие)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Пересобрать все превью, даже существующие",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Показать, что будет сделано, ничего не записывая",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Обработать не больше N фотографий",
        )

    def _missing(self, photo):
        """Превью, файла которых нет в хранилище.

        Смотрится хранилище, а не `bool(preview)`: при стратегии
        `Optimistic` `bool()` на файл не смотрит и всегда отвечает «есть».
        """
        missing = []
        for name in PREVIEW_FIELDS:
            image = getattr(photo, name)
            if not image.name or not image.storage.exists(image.name):
                missing.append(name)
        return missing

    def handle(self, *args, **options):
        rebuild_all = options["all"]
        dry_run = options["dry_run"]
        photos = Photo.objects.only("pk", "display").order_by("pk")
        limit = options["limit"]
        if limit is not None:
            photos = photos[:limit]

        rebuilt = skipped = failed = 0
        for photo in photos:
            label = f"#{photo.pk} {photo.display.name or '(нет display)'}"
            if not photo.display:
                # Фото без источника превью не имеет: это не ошибка, а
                # необработанный или битый снимок.
                skipped += 1
                self.stdout.write(f"пропущено (нет display): {label}")
                continue
            try:
                names = PREVIEW_FIELDS if rebuild_all else self._missing(photo)
                if not names:
                    skipped += 1
                    continue
                if dry_run:
                    self.stdout.write(f"будет пересобрано: {label}")
                    rebuilt += 1
                    continue
                for name in names:
                    image = getattr(photo, name)
                    # Старый файл удаляется до записи: хранилище не
                    # перезаписывает, а сохраняет рядом с суффиксом, и
                    # страница продолжила бы показывать прежнюю картинку.
                    # Превью — производная, `display` не трогается.
                    if image.name and image.storage.exists(image.name):
                        image.storage.delete(image.name)
                    # `force=True` нужен и для отсутствующего файла: кеш мог
                    # сохранить «превью есть» с прошлого прогона.
                    image.generate(force=True)
            except Exception as exc:  # одно фото не должно рушить проход
                failed += 1
                self.stdout.write(self.style.WARNING(f"ошибка: {label} — {exc}"))
                continue
            rebuilt += 1
            self.stdout.write(f"пересобрано: {label}")

        if dry_run:
            self.stdout.write(f"К пересборке: {rebuilt}")
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"Пересобрано: {rebuilt}, пропущено: {skipped}, ошибок: {failed}"
            )
        )
