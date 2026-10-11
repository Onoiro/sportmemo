"""Конвертеры URL, которых нет в Django.

`SlugConverter` из Django пропускает только ASCII (`[-a-zA-Z0-9_]+`), а
slug'и SportMemo строятся с `allow_unicode=True`: «соколы», «медведи».
Со стандартным конвертером такие адреса не разбирались бы, а `reverse()`
падал бы с `NoReverseMatch`.
"""

from django.urls.converters import StringConverter


class UnicodeSlugConverter(StringConverter):
    """Slug, допускающий буквы кириллицы.

    `\\w` в Python 3 юникодный, поэтому шаблон совпадает с тем, что оставляет
    после себя `slugify(..., allow_unicode=True)`.
    """

    regex = r"[-\w]+"
