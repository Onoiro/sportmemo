"""Общие приёмы форм, не привязанные к конкретному приложению."""

from django import forms


def apply_bootstrap_classes(fields):
    """Bootstrap-классы виджетам.

    Нужно там, где шаблон рендерит виджет как есть и класс из разметки
    не добавить: так устроены шаблоны allauth.
    """
    for field in fields.values():
        widget = field.widget
        if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect)):
            css_class = "form-check-input"
        elif isinstance(widget, forms.Select):
            css_class = "form-select"
        else:
            css_class = "form-control"
        existing = widget.attrs.get("class", "")
        widget.attrs["class"] = f"{existing} {css_class}".strip()


class BootstrapFormMixin:
    """Ставит Bootstrap-классы полям после инициализации формы.

    Порядок важен: allauth добавляет и удаляет поля в своём `__init__`,
    поэтому классы навешиваются только после `super().__init__()`.

    Живёт в `core`, а не в `accounts`: формы команд и событий тоже должны
    получать оформленные поля, а тянуть их из `accounts` — лишняя связь
    между приложениями.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_bootstrap_classes(self.fields)
