"""Тег вывода микроразметки Schema.org (JSON-LD).

Словарь собирает `apps/core/schema.py`, тег только сериализует его и
оборачивает в `<script type="application/ld+json">`.
"""

import json

from django import template
from django.core.serializers.json import DjangoJSONEncoder
from django.utils.safestring import mark_safe

register = template.Library()

# Символы, опасные внутри `<script>`: закрывающий тег и начало комментария
# разорвали бы разметку, а U+2028/U+2029 валидны в JavaScript, но ломают
# разбор JSON. Замена на `\uXXXX` безопасна: JSON понимает такой escape
# и значение не меняется.
ESCAPES = (
    ("<", "\\u003C"),
    (">", "\\u003E"),
    ("&", "\\u0026"),
    ("\u2028", "\\u2028"),
    ("\u2029", "\\u2029"),
)


@register.simple_tag
def jsonld(data):
    """Сериализовать блок разметки; пустой блок даёт пустую строку."""
    if not data:
        return ""
    text = json.dumps(data, ensure_ascii=False, cls=DjangoJSONEncoder)
    for char, escaped in ESCAPES:
        text = text.replace(char, escaped)
    return mark_safe(f'<script type="application/ld+json">{text}</script>')
