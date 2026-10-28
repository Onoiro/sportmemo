"""Бэкфилл производных полей фотографий.

Фото, загруженные до появления обработки, не имеют `display`, размеров
и перцептивного хеша. Команда прогоняет их через тот же
`services.process_photo()`, что и обычная обработка: второй реализации
пайплайна быть не должно.

Ошибка одного фото не прерывает проход — иначе один битый файл остановил бы
весь архив. Недоступное хранилище тоже не прерывает проход: фото остаётся
необработанным, а следующая попытка — за человеком.

Флаг `--rehash` пересчитывает хеши у фото, которые уже обработаны. Он нужен
при смене алгоритма или его битности: хеши разной длины несравнимы
(`hamming_distance()` считает их бесконечно далёкими), поэтому после такой
смены пересчёт обязателен, иначе ни одно старое фото не найдёт дубликат.
"""

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.photos.models import Photo, ProcessingStatus
from apps.photos.services import StorageUnavailable, process_photo


class Command(BaseCommand):
    help = (
        "Обработать фото без версии для показа и перцептивного хеша "
        "(--rehash пересчитывает все)"
    )

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
            "--rehash",
            action="store_true",
            help=(
                "Пересчитать производные поля у всех фото с `display`, "
                "включая те, у кого хеш уже есть. Нужен после смены "
                "алгоритма или битности хеша"
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Обработать не больше N фотографий",
        )

    def _queryset(self, only_missing, rehash):
        # `display` и `phash` — два независимых признака необработанного
        # фото. По умолчанию берём те, у кого нет хотя бы одного.
        if rehash:
            # Хеши старого формата несравнимы с новыми, поэтому нужен
            # проход по всему архиву, а не только по необработанным.
            return Photo.objects.exclude(display="")
        if only_missing:
            return Photo.objects.filter(display="")
        return Photo.objects.filter(Q(display="") | Q(phash=""))

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        photos = self._queryset(options["only_missing"], options["rehash"]).order_by(
            "pk"
        )
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
