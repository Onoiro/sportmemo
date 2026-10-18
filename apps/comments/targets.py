"""Белый список объектов для комментариев и жалоб.

`GenericForeignKey` сам по себе цель не ограничивает: без списка через форму
можно было бы создать комментарий к пользователю, к другому комментарию или
к жалобе. Список и разрешение объекта живут в одном месте — их используют
и комментарии, и жалобы.
"""

from django.apps import apps
from django.http import Http404

from apps.core.shortcuts import get_visible_or_404

# Ключ в форме → метка модели. Строка, а не класс: модель импортируется
# внутри функции, чтобы `comments` не зависел от `catalog` и `photos`
# на этапе загрузки приложений — то же правило, что в `apps/core/shortcuts.py`.
TARGET_MODELS = {
    "photo": "photos.Photo",
    "team": "catalog.Team",
    "event": "catalog.Event",
}


def get_target_model(key):
    """Модель по ключу цели. Неизвестный ключ — 404."""
    label = TARGET_MODELS.get(key)
    if label is None:
        raise Http404("Объект не найден")
    return apps.get_model(label)


def target_key(obj):
    """Ключ цели для объекта: `"photo"`, `"team"` или `"event"`."""
    label = obj._meta.label_lower
    for key, model_label in TARGET_MODELS.items():
        if model_label.lower() == label:
            return key
    raise ValueError(f"Объект {label} не входит в список комментируемых")


def resolve_target(key, object_id, request=None):
    """Объект по ключу и идентификатору, доступный этому пользователю.

    Через `get_visible_or_404`: черновик и неопубликованное постороннему дают
    404, то есть комментарий нельзя «прицепить» к тому, чего пользователь
    не видит.
    """
    model = get_target_model(key)
    try:
        pk = int(object_id)
    except (TypeError, ValueError) as exc:
        raise Http404("Объект не найден") from exc
    return get_visible_or_404(model, request=request, pk=pk)
