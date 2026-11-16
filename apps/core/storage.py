"""Хранилища файлов: приватные оригиналы и публичные превью."""

from django.core.files.storage import storages


def originals_storage():
    """Приватное хранилище оригиналов.

    Callable, а не экземпляр: `FileField.deconstruct()` кладёт в миграцию
    ссылку на функцию, а экземпляр хранилища сериализовать нельзя.
    """
    return storages["originals"]
