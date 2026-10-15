"""Сборка поискового текста и пересчёт индекса.

Поиск двухкомпонентный: полнотекстовый (`tsvector`, конфигурация `russian`)
находит по части слова, триграммы (`pg_trgm`) — по опечатке и «похоже на».
Этот модуль отвечает за первый компонент: превращает объект в текст, а текст —
в `tsvector`.

**Правило приватности.** Индекс не выдаёт данных, которых нет в таблице,
но и не должен содержать того, что нельзя найти: фамилия человека со скрытым
именем в текст не попадает. Сборка живёт в одной функции `build_search_text()`,
чтобы правило не разошлось по сигналам и команде переиндексации.

**Почему запись идёт через `update()`, а не `save()`.** `save()` двигал бы
`updated_at` (`auto_now`) и писал запись в `django-simple-history` при каждом
пересчёте: «объект изменился» и «индекс пересчитан» — разные события,
а `updated_at` попадает в `lastmod` карты сайта. `update()` сигналов не шлёт,
поэтому рекурсии из сигналов тоже не возникает.
"""

import re

from django.contrib.postgres.search import SearchQuery, SearchVector
from django.db import transaction
from django.db.models import Value

# Конфигурация словаря PostgreSQL. Настраивать нечего: латиница и опечатки
# закрываются триграммами, а не вторым словарём.
SEARCH_CONFIG = "russian"
# Служебные ключи `attributes`: в `demo` лежит признак демо-объекта,
# искать по нему нечего.
SKIP_ATTRIBUTE_KEYS = frozenset({"demo"})
# Размер порции переиндексации: одна транзакция на порцию, чтобы полный
# проход по архиву не держал транзакцию целиком.
REINDEX_CHUNK_SIZE = 500
# Слово запроса: буквы, цифры, дефис. Всё остальное — разделители.
# Спецсимволы `tsquery` (`&`, `|`, `!`, `:`, `(`, `)`, `*`) отбрасываются,
# иначе пользовательский ввод ломал бы запрос.
_WORD_RE = re.compile(r"[\w-]+", re.UNICODE)


def _join(parts) -> str:
    """Склеить непустые части текста в одну строку."""
    return " ".join(str(part).strip() for part in parts if part and str(part).strip())


def _attribute_values(attributes) -> list[str]:
    """Значения `attributes` без служебных ключей.

    Специфика вида спорта лежит в JSONB (`stage`, `score`, `result`),
    и по ней тоже ищут: «финал» должен находить событие с этапом «финал».
    """
    if not attributes:
        return []
    values = []
    for key, value in attributes.items():
        if key in SKIP_ATTRIBUTE_KEYS:
            continue
        if isinstance(value, dict):
            values.extend(str(item) for item in value.values())
        elif isinstance(value, (list, tuple)):
            values.extend(str(item) for item in value)
        else:
            values.append(str(value))
    return values


def _team_text(team) -> str:
    return _join(
        [team.name, team.city, team.description, *_attribute_values(team.attributes)]
    )


def _event_text(event) -> str:
    return _join(
        [
            event.name,
            event.city,
            event.country,
            event.get_event_type_display(),
            event.description,
            *_attribute_values(event.attributes),
        ]
    )


def _person_text(person) -> str:
    """Текст человека с учётом видимости имени.

    При `hidden` текст пустой: индексировать подпись «Имя не указано» значит
    находить всех скрытых людей по слову «Имя». При `first_name_only` в текст
    не попадают ни фамилия, ни варианты имён — прозвище или фамилия
    при рождении раскрыли бы скрытую фамилию.
    """
    from apps.catalog.models import NameVisibility

    if person.name_visibility == NameVisibility.HIDDEN:
        return ""

    parts = [person.public_name, person.first_name, person.middle_name, person.bio]
    if person.is_full_name_public:
        parts.append(person.last_name)
        parts.extend(name.value for name in person.names.all())
    return _join(parts)


def _photo_text(photo) -> str:
    return _join([photo.title, photo.caption])


def build_search_text(obj) -> str:
    """Поисковый текст объекта.

    Модели импортируются внутри функции: на уровне модуля это дало бы
    зависимость `search` → `catalog`/`photos` и риск цикла — то же правило,
    что в `apps/core/shortcuts.py`.
    """
    from apps.catalog.models import Event, Person, Team
    from apps.photos.models import Photo

    if isinstance(obj, Team):
        return _team_text(obj)
    if isinstance(obj, Event):
        return _event_text(obj)
    if isinstance(obj, Person):
        return _person_text(obj)
    if isinstance(obj, Photo):
        return _photo_text(obj)
    raise TypeError(f"Нет правил индексации для {type(obj).__name__}")


def build_search_vector(obj):
    """Выражение `tsvector` для объекта — записывается прямо в поле."""
    return SearchVector(Value(build_search_text(obj)), config=SEARCH_CONFIG)


def build_search_query(value) -> SearchQuery | None:
    """Запрос `tsquery` для пользовательского ввода или `None`.

    Строится в режиме `raw` и с префиксом на каждое слово: русский словарь
    приводит «Громовцы» к «громовц», а `plainto_tsquery('гром')` даёт «гром» —
    они не совпадают, и без префикса частичное слово ничего не находит.
    `to_tsquery('russian', 'гром:*')` находит и «Громов», и «Громовцы».

    Слова фильтруются регулярным выражением: символы `&`, `|`, `!`, `:`
    в `raw`-режиме — часть языка запросов, и пользовательский ввод с ними
    уронил бы страницу ошибкой `syntax error in tsquery`.
    """
    words = _WORD_RE.findall(value or "")
    if not words:
        return None
    return SearchQuery(
        " & ".join(f"{word}:*" for word in words),
        config=SEARCH_CONFIG,
        search_type="raw",
    )


def reindex_instance(obj) -> None:
    """Пересчитать индекс одного объекта.

    Вызывается из сигналов. `update()` вместо `save()`: без записи в историю,
    без сдвига `updated_at` и без рекурсии из сигналов.
    """
    obj.__class__.objects.filter(pk=obj.pk).update(
        search_vector=build_search_vector(obj)
    )


def reindex_queryset(model, queryset=None, chunk_size=REINDEX_CHUNK_SIZE) -> int:
    """Пересчитать индекс всех объектов модели.

    Нужна после появления поля (объекты старше миграции) и после массовых
    правок в админке, которые идут мимо сигналов. Текст собирается
    в Python, поэтому на каждый объект идёт свой `UPDATE` — но порциями
    в транзакции, чтобы полный проход не держал одну длинную транзакцию.
    """
    if queryset is None:
        queryset = model.objects.all()

    total = 0
    batch: list = []
    for obj in queryset.iterator(chunk_size=chunk_size):
        batch.append(obj)
        if len(batch) >= chunk_size:
            total += _reindex_batch(batch)
            batch = []
    total += _reindex_batch(batch)
    return total


def _reindex_batch(objects) -> int:
    if not objects:
        return 0
    with transaction.atomic():
        for obj in objects:
            reindex_instance(obj)
    return len(objects)
