"""Сборка выдачи страницы поиска: группы результатов и подсказки.

Страница показывает четыре группы (команды, события, люди, фотографии)
и, если ничего не нашлось, блок «возможно, вы искали».

**Правило приватности.** Полнотекстовый индекс скрытую фамилию не содержит
(`apps/search/indexing.py`), но триграммный индекс хранит её «сырой» — это
зафиксировано тестом `test_trigram_indexes_raw_last_name_by_design`. Поэтому
подсказки сравнивают только безопасные поля: имя человека — всегда, фамилия
и варианты имён — только при полной видимости имени, а люди со скрытым именем
исключены вовсе.

Модели импортируются внутри функций: на уровне модуля это дало бы
зависимость `search` → `catalog`/`photos` — то же правило, что
в `apps/core/shortcuts.py`.
"""

from dataclasses import dataclass

from django.contrib.postgres.search import SearchRank, TrigramSimilarity
from django.db.models import Case, F, FloatField, Value, When
from django.db.models.functions import Greatest, Lower
from django.utils.translation import gettext_lazy as _

from apps.core.shortcuts import photos_count, visible_photos

# Сколько элементов показываем в группе. Пагинации на странице результатов
# нет: она обзорная, а не список.
GROUP_LIMIT = 20
# Сколько подсказок «возможно, вы искали» показываем.
SUGGESTION_LIMIT = 5
# Порог похожести триграмм: ниже него совпадение выглядит случайным.
SIMILARITY_THRESHOLD = 0.3
# Длина запроса до сборки `tsquery`: длинный ввод породил бы гигантский
# запрос к базе.
MAX_QUERY_LENGTH = 100

# Имя шаблона карточки для каждой группы: партиал группы не ветвится по типу.
TEAM_CARD = "catalog/partials/team_card.html"
EVENT_CARD = "catalog/partials/event_card.html"
PERSON_CARD = "catalog/partials/person_card.html"
PHOTO_CARD = "photos/partials/card.html"


@dataclass(frozen=True)
class SearchGroup:
    """Одна группа выдачи: карточки, общий счётчик и имя шаблона карточки."""

    key: str
    title: str
    template: str
    items: list
    total: int

    @property
    def truncated(self) -> bool:
        """Показана ли только часть группы."""
        return self.total > len(self.items)


@dataclass(frozen=True)
class Suggestion:
    """Готовая строка «возможно, вы искали».

    Подсказки приходят из трёх разных моделей, и шаблон не должен знать,
    из какой: адрес и подпись типа собираются здесь.
    """

    label: str
    url: str
    kind: str


@dataclass(frozen=True)
class SearchResult:
    """Выдача страницы: группы, общий счётчик и подсказки."""

    groups: list
    total: int
    suggestions: list


def normalize_query(raw) -> str:
    """Пользовательский ввод до сборки `tsquery`: обрезка и `strip()`."""
    return (raw or "").strip()[:MAX_QUERY_LENGTH]


def search_page(query, text) -> SearchResult:
    """Выдача страницы поиска.

    `query` — готовый `tsquery`, `text` — исходный ввод, по нему собираются
    подсказки. `query is None` значит, что в вводе не осталось слов (`!!!`):
    это состояние «ничего не найдено» **без** подсказок — сравнивать
    триграммами мусор нечего. Подсказки считаются только при полностью
    пустой выдаче: триграммные запросы дороже полнотекстовых.
    """
    if query is None:
        return SearchResult(groups=[], total=0, suggestions=[])

    groups = [
        _group(
            "teams",
            _("Команды"),
            TEAM_CARD,
            _teams(query),
        ),
        _group(
            "events",
            _("События"),
            EVENT_CARD,
            _events(query),
        ),
        _group(
            "people",
            _("Люди"),
            PERSON_CARD,
            _people(query),
        ),
        _group(
            "photos",
            _("Фотографии"),
            PHOTO_CARD,
            _photos(query),
        ),
    ]
    total = sum(group.total for group in groups)
    return SearchResult(
        groups=groups,
        total=total,
        suggestions=suggestions(text) if not total else [],
    )


def suggestions(text) -> list[Suggestion]:
    """Подсказки «возможно, вы искали» по похожим названиям.

    Регистр приводится к нижнему с двух сторон: `pg_trgm` регистрозависима,
    и «громов» не нашёл бы «Громов». Кандидаты трёх источников дедуплицируются
    по объекту: у человека подсказка может прийти и по имени, и по варианту
    имени — остаётся наиболее похожий вариант.
    """
    best: dict = {}

    def remember(key, similarity, suggestion):
        current = best.get(key)
        if current is None or similarity > current[0]:
            best[key] = (similarity, suggestion)

    for team in _similar_teams(text):
        remember(
            ("team", team.pk),
            team.similarity,
            Suggestion(team.name, team.get_absolute_url(), _("Команда")),
        )
    for event in _similar_events(text):
        remember(
            ("event", event.pk),
            event.similarity,
            Suggestion(event.name, event.get_absolute_url(), _("Событие")),
        )
    for person in _similar_people(text):
        remember(
            ("person", person.pk),
            person.similarity,
            Suggestion(person.public_name, person.get_absolute_url(), _("Человек")),
        )
    for variant in _similar_name_variants(text):
        remember(
            ("person", variant.person_id),
            variant.similarity,
            Suggestion(variant.value, variant.person.get_absolute_url(), _("Человек")),
        )

    ranked = sorted(best.values(), key=lambda item: item[0], reverse=True)
    return [suggestion for _, suggestion in ranked[:SUGGESTION_LIMIT]]


def _group(key, title, template, queryset) -> SearchGroup:
    """Собрать группу: счётчик считается по всей выборке, карточки — срезом."""
    return SearchGroup(
        key=key,
        title=str(title),
        template=template,
        items=list(queryset[:GROUP_LIMIT]),
        total=queryset.count(),
    )


def _teams(query):
    from apps.catalog.models import Team

    return (
        Team.public.published()
        .filter(search_vector=query)
        .annotate(
            rank=SearchRank(F("search_vector"), query),
            photos_total=photos_count(),
        )
        .order_by("-rank", "name")
    )


def _events(query):
    from apps.catalog.models import Event

    return (
        Event.public.published()
        .filter(search_vector=query)
        .annotate(
            rank=SearchRank(F("search_vector"), query),
            photos_total=photos_count(),
        )
        .order_by("-rank", F("year").desc(nulls_last=True), "name")
    )


def _people(query):
    """Люди в выдаче: без скрытых имён и без черновиков.

    `distinct()` не нужен: текст лежит в `search_vector`, join'ов нет —
    варианты имён входят в текст человека при сборке индекса.
    """
    from apps.catalog.models import NameVisibility, Person

    return (
        Person.public.published()
        .exclude(name_visibility=NameVisibility.HIDDEN)
        .filter(search_vector=query)
        .annotate(rank=SearchRank(F("search_vector"), query))
        .order_by("-rank", "last_name", "first_name")
    )


def _photos(query):
    return (
        visible_photos()
        .filter(search_vector=query)
        .select_related("team", "event")
        .annotate(rank=SearchRank(F("search_vector"), query))
        .order_by("-rank", "-created_at")
    )


def _similar_teams(text):
    from apps.catalog.models import Team

    return (
        Team.public.published()
        .annotate(
            similarity=Greatest(
                TrigramSimilarity(Lower("name"), text.lower()),
                TrigramSimilarity(Lower("city"), text.lower()),
            )
        )
        .filter(similarity__gte=SIMILARITY_THRESHOLD)
    )


def _similar_events(text):
    from apps.catalog.models import Event

    return (
        Event.public.published()
        .annotate(
            similarity=Greatest(
                TrigramSimilarity(Lower("name"), text.lower()),
                TrigramSimilarity(Lower("city"), text.lower()),
            )
        )
        .filter(similarity__gte=SIMILARITY_THRESHOLD)
    )


def _similar_people(text):
    """Похожие люди: имя сравнивается всегда, фамилия — только при `full`.

    Фамилия скрывается условием `When`, а не фильтром: у человека
    с `first_name_only` подсказка по имени должна остаться.
    """
    from apps.catalog.models import NameVisibility, Person

    return (
        Person.public.published()
        .exclude(name_visibility=NameVisibility.HIDDEN)
        .annotate(
            similarity=Greatest(
                TrigramSimilarity(Lower("first_name"), text.lower()),
                Case(
                    When(
                        name_visibility=NameVisibility.FULL,
                        then=TrigramSimilarity(Lower("last_name"), text.lower()),
                    ),
                    default=Value(0.0),
                    output_field=FloatField(),
                ),
            )
        )
        .filter(similarity__gte=SIMILARITY_THRESHOLD)
    )


def _similar_name_variants(text):
    """Варианты имён: только у людей с полной видимостью имени.

    Прозвище или фамилия при рождении раскрыли бы скрытую фамилию.
    """
    from apps.catalog.models import NameVisibility, Person, PersonName

    public = Person.public.published().filter(name_visibility=NameVisibility.FULL)
    return (
        PersonName.objects.filter(person__in=public)
        .select_related("person")
        .annotate(similarity=TrigramSimilarity(Lower("value"), text.lower()))
        .filter(similarity__gte=SIMILARITY_THRESHOLD)
    )
