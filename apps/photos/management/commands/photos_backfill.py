"""Бэкфилл производных полей фотографий.

Фото, загруженные до появления обработки, не имеют `display`, размеров
и перцептивного хеша. Команда прогоняет их через тот же
`services.process_photo()`, что и обычная обработка: второй реализации
пайплайна быть не должно.

Ошибка одного фото не прерывает проход — иначе один битый файл остановил бы
весь архив. Недоступное хранилище тоже не прерывает проход: фото остаётся
необработанным, а следующая попытка — за человеком.
"""

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.photos.models import Photo, ProcessingStatus
from apps.photos.services import StorageUnavailable, process_photo


class Command(BaseCommand):
    help = "Обработать фото без версии для показа и перцептивного хеша"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Показать, что будет сделано, ничего не записывая",
        )
        parser.add_argument(
            "--only-missing",
            action="store_true",
            help="Только фото без `display` (не трогать те, у кого нет хеша)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Обработать не больше N фотографий",
        )

    def _queryset(self, only_missing):
        # `display` и `phash` — два независимых признака необработанного
        # фото. По умолчанию берём те, у кого нет хотя бы одного.
        if only_missing:
            return Photo.objects.filter(display="")
        return Photo.objects.filter(Q(display="") | Q(phash=""))

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        photos = self._queryset(options["only_missing"]).order_by("pk")
        limit = options["limit"]
        if limit is not None:
            photos = photos[:limit]

        processed = skipped = failed = unavailable = 0
        for photo in photos:
            label = f"#{photo.pk} {photo.original.name or '(нет файла)'}"
            if dry_run:
                self.stdout.write(f"будет обработано: {label}")
                processed += 1
                continue
            if not photo.original:
                # Строка без файла — не ошибка обработки, а мусор в базе.
                skipped += 1
                self.stdout.write(f"пропущено (нет оригинала): {label}")
                continue

            try:
                process_photo(photo)
            except StorageUnavailable as exc:
                # Статус не трогаем: повтор возможен, когда хранилище
                # вернётся, и фото не должно читаться как «ошибка».
                unavailable += 1
                self.stdout.write(
                    self.style.WARNING(f"хранилище недоступно: {label} — {exc}")
                )
                continue
            if photo.processing_status == ProcessingStatus.PROCESSED:
                processed += 1
                self.stdout.write(f"обработано: {label}")
            else:
                failed += 1
                self.stdout.write(
                    self.style.WARNING(f"ошибка: {label} — {photo.processing_error}")
                )

        if dry_run:
            self.stdout.write(f"К обработке: {processed}")
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"Обработано: {processed}, пропущено: {skipped}, "
                f"ошибок: {failed}, хранилище недоступно: {unavailable}"
            )
        )
