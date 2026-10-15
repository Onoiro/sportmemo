"""Тесты поисковой инфраструктуры (подэтап 2.12).

Проверяем смысл, а не разметку: индекс заполняется при сохранении объекта,
полнотекстовый поиск находит по части слова, триграммы — по опечатке,
а скрытая фамилия не находится никаким способом.

Тесты требуют PostgreSQL: `tsvector` и `pg_trgm` есть только там.
На других СУБД модуль пропускается целиком — иначе вместо понятной
причины была бы ошибка «type tsvector does not exist».
"""

import pytest
from django.contrib.postgres.search import TrigramSimilarity
from django.db import connection

from apps.catalog.models import Event, NameVisibility, Person, PersonName, Team
from apps.core.models import Status
from apps.photos.models import Photo
from apps.search.indexing import (
    SEARCH_CONFIG,
    build_search_query,
    build_search_text,
    reindex_queryset,
)
from tests.factories import EventFactory, PersonFactory, PhotoFactory, TeamFactory

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="Поиск работает только на PostgreSQL (tsvector, pg_trgm)",
    ),
]


def search(model, value, **filters):
    """Найти объекты модели полнотекстовым поиском по вводу пользователя."""
    query = build_search_query(value)
    assert query is not None, f"Запрос {value!r} не должен быть пустым"
    return list(model.objects.filter(search_vector=query, **filters))


class TestExtension:
    def test_pg_trgm_is_installed(self):
        """Расширение создаётся миграцией `apps/search/0001_pg_trgm.py`."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")

            assert cursor.fetchone() is not None

    def test_indexes_exist(self):
        """Индексы поиска созданы миграциями полей."""
        expected = {
            "team_search_vector_gin",
            "team_name_trgm",
            "team_city_trgm",
            "person_search_vector_gin",
            "person_first_name_trgm",
            "person_last_name_trgm",
            "personname_value_trgm",
            "event_search_vector_gin",
            "event_name_trgm",
            "event_city_trgm",
            "photo_search_vector_gin",
            "photo_title_trgm",
        }
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexname FROM pg_indexes WHERE indexname = ANY(%s)",
                [list(expected)],
            )
            found = {row[0] for row in cursor.fetchall()}

        assert found == expected


class TestSearchText:
    """Сборка поискового текста — правила приватности живут здесь."""

    def test_team_text_includes_name_city_description(self, baseball, user):
        team = Team.objects.create(
            sport=baseball,
            name="Соколы",
            city="Москва",
            description="Детская команда",
            created_by=user,
        )

        text = build_search_text(team)

        assert "Соколы" in text
        assert "Москва" in text
        assert "Детская команда" in text

    def test_team_attributes_are_indexed_without_demo_flag(self, baseball, user):
        team = Team.objects.create(
            sport=baseball,
            name="Соколы",
            created_by=user,
            attributes={"stage": "финал", "demo": True},
        )

        text = build_search_text(team)

        assert "финал" in text
        assert "demo" not in text

    def test_event_text_includes_type(self, baseball, user):
        event = Event.objects.create(
            sport=baseball,
            name="Кубок России",
            event_type="tournament",
            city="Тверь",
            created_by=user,
        )

        text = build_search_text(event)

        assert "Кубок России" in text
        assert "Тверь" in text

    def test_photo_text_includes_title_and_caption(self, baseball, user):
        photo = PhotoFactory(title="Финал 1994", caption="Победа в финале")

        text = build_search_text(photo)

        assert "Финал 1994" in text
        assert "Победа в финале" in text

    def test_person_full_name_is_indexed(self, user):
        person = Person.objects.create(
            first_name="Иван", last_name="Громов", bio="Питчер", created_by=user
        )

        text = build_search_text(person)

        assert "Громов" in text
        assert "Питчер" in text

    def test_first_name_only_hides_last_name(self, user):
        """При `first_name_only` фамилия в индекс не попадает."""
        person = Person.objects.create(
            first_name="Иван",
            last_name="Громов",
            name_visibility=NameVisibility.FIRST_NAME_ONLY,
            created_by=user,
        )

        text = build_search_text(person)

        assert "Иван" in text
        assert "Громов" not in text

    def test_hidden_name_gives_empty_text(self, user):
        """При `hidden` текст пустой: «Имя не указано» не индексируется."""
        person = Person.objects.create(
            first_name="Иван",
            last_name="Громов",
            bio="Питчер",
            name_visibility=NameVisibility.HIDDEN,
            created_by=user,
        )

        assert build_search_text(person) == ""

    def test_name_variants_are_indexed_only_for_full_visibility(self, user):
        person = Person.objects.create(
            first_name="Иван", last_name="Громов", created_by=user
        )
        PersonName.objects.create(person=person, value="Громыч")

        assert "Громыч" in build_search_text(person)

        person.name_visibility = NameVisibility.FIRST_NAME_ONLY
        person.save()

        assert "Громыч" not in build_search_text(person)

    def test_unknown_model_is_rejected(self, baseball):
        with pytest.raises(TypeError):
            build_search_text(baseball)


class TestSearchQuery:
    """Сборка `tsquery` из пользовательского ввода."""

    def test_empty_input_gives_no_query(self):
        assert build_search_query("") is None
        assert build_search_query("   ") is None
        assert build_search_query(None) is None

    def test_special_characters_are_ignored(self):
        """`&`, `|`, `!`, `:` — часть языка `tsquery`, ввод их не ломает."""
        assert build_search_query("!!! &&& |||") is None
        query = build_search_query("гром & | !")
        assert query is not None
        assert query.config is not None

    def test_query_uses_prefix_for_partial_word(self, baseball, user):
        Team.objects.create(sport=baseball, name="Громовцы", created_by=user)

        assert search(Team, "гром") != []
        assert search(Team, "громов") != []

    def test_multiple_words_are_combined_with_and(self, baseball, user):
        Team.objects.create(sport=baseball, name="Громовцы", city="Москва")
        Team.objects.create(sport=baseball, name="Громовцы", city="Тверь")

        found = search(Team, "громовцы тверь")

        assert [team.city for team in found] == ["Тверь"]

    def test_search_config_is_russian(self):
        assert SEARCH_CONFIG == "russian"


class TestIndexing:
    """Индекс заполняется сигналами при сохранении."""

    def test_team_index_is_filled_on_save(self, baseball, user):
        team = Team.objects.create(sport=baseball, name="Соколы", created_by=user)

        team.refresh_from_db()

        assert team.search_vector is not None

    def test_index_updates_after_name_change(self, baseball, user):
        team = Team.objects.create(sport=baseball, name="Соколы", created_by=user)

        team.name = "Громовцы"
        team.save()
        team.refresh_from_db()

        assert search(Team, "сокол") == []
        assert search(Team, "гром") == [team]

    def test_index_is_filled_on_event_save(self, baseball, user):
        event = Event.objects.create(
            sport=baseball, name="Кубок России", created_by=user
        )

        event.refresh_from_db()

        assert search(Event, "кубок") == [event]

    def test_index_is_filled_on_photo_save(self):
        photo = PhotoFactory(title="Финал 1994")

        photo.refresh_from_db()

        assert search(Photo, "финал") == [photo]

    def test_person_name_save_reindexes_person(self, user):
        """Вариант имени входит в текст человека, поэтому его сохранение
        пересчитывает индекс родителя.
        """
        person = Person.objects.create(
            first_name="Иван", last_name="Громов", created_by=user
        )

        PersonName.objects.create(person=person, value="Громыч")

        assert search(Person, "громыч") == [person]

    def test_person_name_delete_reindexes_person(self, user):
        person = Person.objects.create(
            first_name="Иван", last_name="Громов", created_by=user
        )
        variant = PersonName.objects.create(person=person, value="Громыч")

        variant.delete()

        assert search(Person, "громыч") == []


class TestPrivacy:
    """Скрытая фамилия не находится ни одним из двух механизмов."""

    def test_first_name_only_is_not_found_by_full_text(self, user):
        person = Person.objects.create(
            first_name="Иван",
            last_name="Громов",
            name_visibility=NameVisibility.FIRST_NAME_ONLY,
            created_by=user,
        )

        assert search(Person, "громов") == []
        assert search(Person, "иван") == [person]

    def test_hidden_person_is_not_found_at_all(self, user):
        Person.objects.create(
            first_name="Иван",
            last_name="Громов",
            name_visibility=NameVisibility.HIDDEN,
            created_by=user,
        )

        assert search(Person, "громов") == []
        assert search(Person, "иван") == []

    def test_display_name_is_not_indexed(self, user):
        """`display_name` содержит полную фамилию и в индекс не попадает."""
        person = Person.objects.create(
            first_name="Иван",
            name_visibility=NameVisibility.FIRST_NAME_ONLY,
            created_by=user,
        )
        person.display_name = "Иван Громов"
        person.save(update_fields=["display_name"])

        assert search(Person, "громов") == []

    def test_trigram_indexes_raw_last_name_by_design(self, user):
        """Триграммный индекс содержит «сырую» фамилию — так и задумано.

        Скрытие обеспечивает фильтр запроса, а не индекс: `TrigramSimilarity`
        по `last_name` фамилию найдёт, поэтому в 2.13 выдача обязана идти
        через `public.published()` и учитывать `name_visibility`.
        Полнотекстовый индекс, в отличие от триграммного, скрытую фамилию
        не содержит вовсе.
        """
        Person.objects.create(
            first_name="Иван",
            last_name="Громов",
            name_visibility=NameVisibility.HIDDEN,
            created_by=user,
        )

        naive = Person.objects.annotate(
            similarity=TrigramSimilarity("last_name", "Громов")
        ).filter(similarity__gt=0.8)

        assert naive.count() == 1
        # А полнотекстовый поиск ту же фамилию не находит.
        assert search(Person, "громов") == []

    def test_first_name_only_trigram_respects_public_name(self, user):
        """Поиск по `public_name` скрытую фамилию не находит."""
        Person.objects.create(
            first_name="Иван",
            last_name="Громов",
            name_visibility=NameVisibility.FIRST_NAME_ONLY,
            created_by=user,
        )

        found = Person.objects.annotate(
            similarity=TrigramSimilarity("first_name", "Громов")
        ).filter(similarity__gt=0.5)

        assert list(found) == []


class TestTrigrams:
    def test_typo_is_found_by_similarity(self, baseball, user):
        """Триграммы — второй компонент поиска: находят по опечатке."""
        Team.objects.create(sport=baseball, name="Громовцы", created_by=user)
        Team.objects.create(sport=baseball, name="Соколы", created_by=user)

        found = (
            Team.objects.annotate(similarity=TrigramSimilarity("name", "Громовци"))
            .filter(similarity__gt=0.4)
            .order_by("-similarity")
        )

        assert [team.name for team in found] == ["Громовцы"]

    def test_trigram_lookup_works(self, baseball, user):
        Team.objects.create(sport=baseball, name="Громовцы", created_by=user)

        assert list(Team.objects.filter(name__trigram_similar="Громовци")) != []

    def test_person_name_variant_is_found_by_similarity(self, user):
        person = PersonFactory(first_name="Иван", last_name="Громов")
        PersonName.objects.create(person=person, value="Громыч")

        found = PersonName.objects.annotate(
            similarity=TrigramSimilarity("value", "Громычч")
        ).filter(similarity__gt=0.4)

        assert [name.person_id for name in found] == [person.pk]


class TestReindexCommand:
    """Переиндексация: команда `search_reindex`."""

    def test_fills_empty_index(self, baseball, user):
        """Объекты старше миграции индекса не имеют — их заполняет команда."""
        team = Team.objects.create(sport=baseball, name="Соколы", created_by=user)
        Team.objects.filter(pk=team.pk).update(search_vector=None)

        count = reindex_queryset(Team)

        team.refresh_from_db()
        assert count == Team.objects.count()
        assert team.search_vector is not None
        assert search(Team, "сокол") == [team]

    def test_does_not_write_history(self, baseball, user):
        team = Team.objects.create(sport=baseball, name="Соколы", created_by=user)
        before = team.history.count()

        reindex_queryset(Team)

        assert team.history.count() == before

    def test_does_not_move_updated_at(self, baseball, user):
        team = Team.objects.create(sport=baseball, name="Соколы", created_by=user)
        team.refresh_from_db()
        before = team.updated_at

        reindex_queryset(Team)

        team.refresh_from_db()
        assert team.updated_at == before

    def test_is_idempotent(self, baseball, user):
        Team.objects.create(sport=baseball, name="Соколы", created_by=user)

        reindex_queryset(Team)
        first = list(Team.objects.values_list("search_vector", flat=True))
        reindex_queryset(Team)
        second = list(Team.objects.values_list("search_vector", flat=True))

        assert first == second

    def test_command_runs_for_all_models(self, baseball, user):
        """Команда проходит по всем четырём моделям и не падает."""
        from io import StringIO

        from django.core.management import call_command

        TeamFactory(sport=baseball)
        PersonFactory()
        EventFactory(sport=baseball)
        PhotoFactory()

        out = StringIO()
        call_command("search_reindex", stdout=out)

        assert "Всего пересчитано" in out.getvalue()

    def test_command_accepts_model_option(self, baseball, user):
        from io import StringIO

        from django.core.management import call_command

        TeamFactory(sport=baseball, name="Соколы")
        out = StringIO()

        call_command("search_reindex", "--model", "team", stdout=out)

        assert "команды" in out.getvalue()
        assert "люди" not in out.getvalue()


class TestSearchAcrossModels:
    """Поиск находит объект по данным разных полей."""

    def test_team_is_found_by_city(self, baseball, user):
        team = Team.objects.create(
            sport=baseball, name="Соколы", city="Москва", created_by=user
        )

        assert search(Team, "москва") == [team]

    def test_event_is_found_by_description(self, baseball, user):
        event = Event.objects.create(
            sport=baseball,
            name="Кубок России",
            description="Финальный матч в Твери",
            created_by=user,
        )

        assert search(Event, "финальный") == [event]

    def test_photo_is_found_by_caption(self):
        photo = PhotoFactory(caption="Победа в финале первенства")

        assert search(Photo, "первенств") == [photo]

    def test_draft_object_is_still_indexed(self, baseball, user):
        """Черновик в индексе есть: публичность отсекает выборка."""
        team = Team.objects.create(
            sport=baseball, name="Соколы", created_by=user, status=Status.DRAFT
        )

        assert search(Team, "сокол") == [team]
        assert search(Team, "сокол", status=Status.PUBLISHED) == []

    def test_unconfirmed_photo_is_still_indexed(self):
        photo = PhotoFactory(rights_confirmed=False, title="Финал 1994")

        assert search(Photo, "финал") == [photo]
        assert search(Photo, "финал", rights_confirmed=True) == []
