"""Пересчёт поискового индекса при сохранении объектов.

Индекс — производное поле, и держать его в актуальном состоянии должен код,
а не человек: иначе после правки названия поиск находил бы старое значение.

`PersonName` обрабатывается отдельно: у вариантов имён своего индекса нет,
они входят в текст человека, поэтому сохранение или удаление варианта
пересчитывает индекс родителя.
"""

from django.db.models.signals import post_delete, post_save

from apps.catalog.models import Event, Person, PersonName, Team
from apps.photos.models import Photo
from apps.search.indexing import reindex_instance

# Модели со своим полем `search_vector`.
INDEXED_MODELS = (Team, Person, Event, Photo)


def _reindex(sender, instance, raw=False, **kwargs):
    """Пересчитать индекс сохранённого объекта.

    `raw=True` приходит при загрузке фикстур: там сигналы не должны
    вмешиваться, потому что значения полей записываются как есть.
    """
    if raw:
        return
    reindex_instance(instance)


def _reindex_person(sender, instance, **kwargs):
    """Пересчитать индекс человека после изменения варианта имени."""
    person = Person.objects.filter(pk=instance.person_id).first()
    if person is not None:
        reindex_instance(person)


def connect():
    """Подключить обработчики сигналов.

    Вызывается из `SearchConfig.ready()`: модели к этому моменту загружены.
    `dispatch_uid` защищает от повторного подключения при повторной
    загрузке приложения.
    """
    for model in INDEXED_MODELS:
        post_save.connect(
            _reindex,
            sender=model,
            dispatch_uid=f"search_reindex_{model._meta.label_lower}",
        )
    post_save.connect(
        _reindex_person,
        sender=PersonName,
        dispatch_uid="search_reindex_personname_save",
    )
    post_delete.connect(
        _reindex_person,
        sender=PersonName,
        dispatch_uid="search_reindex_personname_delete",
    )
