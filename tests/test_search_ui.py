"""Тесты страницы поиска (подэтап 2.13).

Проверяем смысл: что находится, что не выдаётся никому и когда появляются
подсказки. Поиск работает только на PostgreSQL (`tsvector`, `pg_trgm`) —
на других СУБД модуль пропускается целиком.
"""

import pytest
from django.db import connection
from django.urls import reverse

from apps.catalog.models import (
    Event,
    NameVisibility,
    Person,
    PersonName,
    Team,
    TeamMembership,
)
from apps.core.models import Status
from apps.search.services import (
    GROUP_LIMIT,
    MAX_QUERY_LENGTH,
    SUGGESTION_LIMIT,
    normalize_query,
)
from tests.factories import PhotoFactory

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="Поиск работает только на PostgreSQL (tsvector, pg_trgm)",
    ),
]

SEARCH_URL = reverse("search:index")


def make_team(baseball, user, name, **kwargs):
    kwargs.setdefault("status", Status.PUBLISHED)
    return Team.objects.create(sport=baseball, name=name, created_by=user, **kwargs)


def make_event(baseball, user, name, **kwargs):
    kwargs.setdefault("status", Status.PUBLISHED)
    return Event.objects.create(sport=baseball, name=name, created_by=user, **kwargs)


def make_person(user, first_name="Иван", last_name="Громов", **kwargs):
    kwargs.setdefault("status", Status.PUBLISHED)
    return Person.objects.create(
        first_name=first_name, last_name=last_name, created_by=user, **kwargs
    )


def search(client, value, **params):
    return client.get(SEARCH_URL, {"q": value, **params})


def group(result, key):
    return next(item for item in result.groups if item.key == key)


class TestEmptyQuery:
    """Пустой запрос: поиск не выполняется, страница объясняет, что искать."""

    def test_opens_anonymously(self, client):
        response = client.get(SEARCH_URL)

        assert response.status_code == 200
        assert "Поиск" in response.content.decode()

    def test_reverse_url(self):
        assert SEARCH_URL == "/search/"

    def test_does_not_search(self, client, baseball, user):
        make_team(baseball, user, "Соколы")

        response = client.get(SEARCH_URL)

        assert response.context["result"] is None

    def test_shows_form_and_hint(self, client):
        html = client.get(SEARCH_URL).content.decode()

        assert 'action="/search/"' in html
        assert "Что можно найти" in html

    def test_whitespace_only_query_is_empty(self, client):
        response = client.get(SEARCH_URL, {"q": "   "})

        assert response.context["result"] is None

    def test_page_is_noindex(self, client):
        html = client.get(SEARCH_URL).content.decode()

        assert "noindex, follow" in html


class TestResults:
    """Что находит поиск."""

    def test_team_is_found_by_partial_word(self, client, baseball, user):
        team = make_team(baseball, user, "Громовцы")

        response = search(client, "гром")

        assert list(group(response.context["result"], "teams").items) == [team]

    def test_team_is_found_by_city(self, client, baseball, user):
        team = make_team(baseball, user, "Соколы", city="Мурманск")

        response = search(client, "мурманск")

        assert list(group(response.context["result"], "teams").items) == [team]

    def test_event_is_found_by_description(self, client, baseball, user):
        event = make_event(
            baseball, user, "Кубок России", description="Финальный матч в Твери"
        )

        response = search(client, "финальный")

        assert list(group(response.context["result"], "events").items) == [event]

    def test_person_is_found_by_first_name(self, client, user):
        person = make_person(user)

        response = search(client, "иван")

        assert list(group(response.context["result"], "people").items) == [person]

    def test_person_is_found_by_last_name(self, client, user):
        person = make_person(user)

        response = search(client, "громов")

        assert list(group(response.context["result"], "people").items) == [person]

    def test_person_is_found_by_name_variant(self, client, user):
        person = make_person(user)
        PersonName.objects.create(person=person, value="Громыч")

        response = search(client, "громыч")

        assert list(group(response.context["result"], "people").items) == [person]

    def test_photo_is_found_by_caption(self, client):
        photo = PhotoFactory(caption="Победа в финале первенства")

        response = search(client, "первенств")

        assert list(group(response.context["result"], "photos").items) == [photo]

    def test_groups_have_counters(self, client, baseball, user):
        make_team(baseball, user, "Громовцы")
        make_team(baseball, user, "Громовцы-2")
        make_person(user)

        result = search(client, "громов").context["result"]

        assert group(result, "teams").total == 2
        assert group(result, "people").total == 1
        assert result.total == 3

    def test_groups_are_in_fixed_order(self, client, baseball, user):
        make_team(baseball, user, "Громовцы")

        result = search(client, "громов").context["result"]

        assert [item.key for item in result.groups] == [
            "teams",
            "events",
            "people",
            "photos",
        ]

    def test_cards_link_to_object_pages(self, client, baseball, user):
        team = make_team(baseball, user, "Громовцы")

        html = search(client, "громов").content.decode()

        assert team.get_absolute_url() in html

    def test_total_is_shown_in_html(self, client, baseball, user):
        make_team(baseball, user, "Громовцы")

        html = search(client, "громов").content.decode()

        assert "Найден 1 результат" in html


class TestGroupLimit:
    def test_group_is_cut_but_total_is_full(self, client, baseball, user):
        for index in range(GROUP_LIMIT + 5):
            make_team(baseball, user, f"Громовцы {index:02d}")

        result = search(client, "громовцы").context["result"]
        teams = group(result, "teams")

        assert len(teams.items) == GROUP_LIMIT
        assert teams.total == GROUP_LIMIT + 5
        assert teams.truncated is True

    def test_truncation_note_is_shown(self, client, baseball, user):
        for index in range(GROUP_LIMIT + 5):
            make_team(baseball, user, f"Громовцы {index:02d}")

        html = search(client, "громовцы").content.decode()

        assert f"Показаны первые {GROUP_LIMIT} из {GROUP_LIMIT + 5}" in html

    def test_small_group_is_not_truncated(self, client, baseball, user):
        make_team(baseball, user, "Громовцы")

        teams = group(search(client, "громовцы").context["result"], "teams")

        assert teams.truncated is False


class TestPrivacy:
    """Правила видимости имени и публикации."""

    def test_hidden_person_is_not_found(self, client, user):
        make_person(user, name_visibility=NameVisibility.HIDDEN)

        result = search(client, "громов").context["result"]

        assert result.total == 0

    def test_first_name_only_person_is_found_by_first_name(self, client, user):
        person = make_person(user, name_visibility=NameVisibility.FIRST_NAME_ONLY)

        result = search(client, "иван").context["result"]

        assert list(group(result, "people").items) == [person]

    def test_first_name_only_person_is_not_found_by_last_name(self, client, user):
        make_person(user, name_visibility=NameVisibility.FIRST_NAME_ONLY)

        result = search(client, "громов").context["result"]

        assert result.total == 0

    def test_first_name_only_person_is_not_found_by_name_variant(self, client, user):
        person = make_person(user, name_visibility=NameVisibility.FIRST_NAME_ONLY)
        PersonName.objects.create(person=person, value="Громыч")

        result = search(client, "громыч").context["result"]

        assert result.total == 0

    @pytest.mark.parametrize(
        "status",
        [Status.DRAFT, Status.HIDDEN, Status.REJECTED],
    )
    def test_unpublished_objects_are_not_found(self, client, baseball, user, status):
        make_team(baseball, user, "Громовцы", status=status)
        make_person(user, status=status)
        make_event(baseball, user, "Громовский кубок", status=status)

        result = search(client, "громов").context["result"]

        assert result.total == 0

    def test_author_does_not_see_own_draft(self, client, baseball, user):
        """Поиск — публичная выдача: черновик не видит даже автор."""
        make_team(baseball, user, "Громовцы", status=Status.DRAFT)
        client.force_login(user)

        result = search(client, "громов").context["result"]

        assert result.total == 0

    def test_moderator_does_not_see_draft(self, client, baseball, user, moderator):
        make_team(baseball, user, "Громовцы", status=Status.DRAFT)
        client.force_login(moderator)

        result = search(client, "громов").context["result"]

        assert result.total == 0

    def test_deleted_and_merged_are_not_found(self, client, baseball, user):
        deleted = make_team(baseball, user, "Громовцы")
        deleted.soft_delete()
        target = make_team(baseball, user, "Громовский клуб")
        source = make_team(baseball, user, "Громовцы-дубликат")
        source.merge_into(target)

        result = search(client, "громов").context["result"]

        assert list(group(result, "teams").items) == [target]

    def test_photo_without_rights_is_found(self, client):
        """Права проверяет автор: поиск их не учитывает."""
        PhotoFactory(title="Громовский финал", rights_confirmed=False)

        result = search(client, "громовский").context["result"]

        assert result.total == 1

    def test_draft_photo_is_not_found(self, client):
        PhotoFactory(title="Громовский финал", status=Status.DRAFT)

        result = search(client, "громовский").context["result"]

        assert result.total == 0

    def test_draft_person_is_not_found(self, client, user):
        make_person(user, status=Status.DRAFT)

        result = search(client, "громов").context["result"]

        assert result.total == 0

    def test_membership_does_not_leak_draft_person(self, client, baseball, user):
        """Человек-черновик не попадает в выдачу, даже если он в команде."""
        team = make_team(baseball, user, "Громовцы")
        person = make_person(user, status=Status.DRAFT)
        TeamMembership.objects.create(team=team, person=person)

        result = search(client, "громов").context["result"]

        assert list(group(result, "people").items) == []


class TestSuggestions:
    """«Возможно, вы искали» — только при пустой выдаче.

    Запросы в этих тестах подобраны так, чтобы полнотекстовый поиск молчал,
    а триграммы видели похожее: «Громовцыы» словарь не приводит к «громовц»,
    поэтому находится только по похожести.
    """

    def test_typo_gives_suggestion(self, client, baseball, user):
        make_team(baseball, user, "Громовцы")

        result = search(client, "Громовцыы").context["result"]

        assert result.total == 0
        assert [item.label for item in result.suggestions] == ["Громовцы"]

    def test_suggestion_links_to_page(self, client, baseball, user):
        team = make_team(baseball, user, "Громовцы")

        result = search(client, "Громовцыы").context["result"]

        assert result.suggestions[0].url == team.get_absolute_url()

    def test_suggestion_shows_kind(self, client, baseball, user):
        make_team(baseball, user, "Громовцы")

        result = search(client, "Громовцыы").context["result"]

        assert result.suggestions[0].kind == "Команда"

    def test_suggestions_are_not_shown_when_something_found(
        self, client, baseball, user
    ):
        make_team(baseball, user, "Громовцы")

        result = search(client, "громовцы").context["result"]

        assert result.total == 1
        assert result.suggestions == []

    def test_person_suggestion_uses_public_name(self, client, user):
        make_person(user, name_visibility=NameVisibility.FIRST_NAME_ONLY)

        result = search(client, "Иванов").context["result"]

        labels = [item.label for item in result.suggestions]

        assert "Иван" in labels
        assert "Иван Громов" not in labels

    def test_hidden_person_is_not_suggested(self, client, user):
        make_person(user, name_visibility=NameVisibility.HIDDEN)

        result = search(client, "Иванов").context["result"]

        assert result.suggestions == []

    def test_first_name_only_person_is_not_suggested_by_last_name(self, client, user):
        make_person(user, name_visibility=NameVisibility.FIRST_NAME_ONLY)

        result = search(client, "Громово").context["result"]

        assert result.suggestions == []

    def test_name_variant_is_suggested_for_full_visibility(self, client, user):
        person = make_person(user)
        PersonName.objects.create(person=person, value="Громыч")

        result = search(client, "Громычч").context["result"]

        assert [item.label for item in result.suggestions] == ["Громыч"]

    def test_name_variant_is_not_suggested_for_first_name_only(self, client, user):
        person = make_person(user, name_visibility=NameVisibility.FIRST_NAME_ONLY)
        PersonName.objects.create(person=person, value="Громыч")

        result = search(client, "Громычч").context["result"]

        assert result.suggestions == []

    def test_draft_object_is_not_suggested(self, client, baseball, user):
        make_team(baseball, user, "Громовцы", status=Status.DRAFT)

        result = search(client, "Громовцыы").context["result"]

        assert result.suggestions == []

    def test_suggestions_are_limited(self, client, baseball, user):
        for index in range(SUGGESTION_LIMIT + 3):
            make_team(baseball, user, f"Громовц{index:02d}")

        result = search(client, "Громовццы").context["result"]

        assert result.total == 0
        assert len(result.suggestions) == SUGGESTION_LIMIT

    def test_suggestions_are_sorted_by_similarity(self, client, baseball, user):
        make_team(baseball, user, "Громовцы")
        make_team(baseball, user, "Гром")

        result = search(client, "Громовцыы").context["result"]

        assert [item.label for item in result.suggestions] == ["Громовцы", "Гром"]

    def test_suggestion_block_is_shown_in_html(self, client, baseball, user):
        make_team(baseball, user, "Громовцы")

        html = search(client, "Громовцыы").content.decode()

        assert "Возможно, вы искали" in html
        assert "Ничего не найдено" in html

    def test_person_is_suggested_once_for_name_and_variant(self, client, user):
        """Один человек — одна подсказка, даже если похожи и имя, и вариант."""
        person = make_person(user, first_name="Гром", last_name="Громов")
        PersonName.objects.create(person=person, value="Громыч")

        result = search(client, "Громычч").context["result"]

        assert [item.url for item in result.suggestions] == [person.get_absolute_url()]


class TestQueryHygiene:
    """Длинный и мусорный ввод не роняет страницу."""

    def test_normalize_trims_and_clips(self):
        assert normalize_query("  гром  ") == "гром"
        assert normalize_query("а" * 500) == "а" * MAX_QUERY_LENGTH
        assert normalize_query(None) == ""

    def test_long_query_does_not_crash(self, client, baseball, user):
        make_team(baseball, user, "Громовцы")

        response = search(client, "громовцы " * 50)

        assert response.status_code == 200

    def test_junk_query_gives_nothing_found_without_suggestions(self, client):
        response = search(client, "!!! &&&")

        assert response.status_code == 200
        assert response.context["result"].total == 0
        assert response.context["result"].suggestions == []
        assert "Ничего не найдено" in response.content.decode()

    def test_script_tag_is_escaped_in_form(self, client):
        html = search(client, "<script>alert(1)</script>").content.decode()

        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_query_is_kept_in_form_value(self, client):
        html = search(client, "громовцы").content.decode()

        assert 'value="громовцы"' in html

    def test_empty_result_has_no_suggestion_block(self, client):
        html = search(client, "щщщщщ").content.decode()

        assert "Ничего не найдено" in html
        assert "Возможно, вы искали" not in html


class TestLinks:
    """Ссылки на поиск ведут на новый маршрут."""

    def test_navbar_links_to_search(self, client):
        html = client.get(reverse("core:home")).content.decode()

        assert f'action="{SEARCH_URL}"' in html

    def test_footer_links_to_search(self, client):
        html = client.get(reverse("core:home")).content.decode()

        assert f'href="{SEARCH_URL}"' in html

    def test_home_search_action_points_to_search(self, client):
        html = client.get(reverse("core:home")).content.decode()

        assert f'"http://testserver{SEARCH_URL}?q={{search_term_string}}"' in html

    def test_breadcrumbs_lead_home(self, client):
        html = client.get(SEARCH_URL).content.decode()

        assert "Главная" in html
        assert reverse("core:home") in html

    def test_search_page_links_to_team_list(self, client, baseball, user):
        """Карточки в группах — обычные ссылки, как в списках."""
        make_team(baseball, user, "Громовцы")

        html = search(client, "громовцы").content.decode()

        assert reverse("catalog:team_list") in html


class TestServicesAreUsedByView:
    """Представление отдаёт в шаблон собранный `SearchResult`."""

    def test_result_has_all_groups(self, client, baseball, user):
        make_team(baseball, user, "Громовцы")

        result = search(client, "громов").context["result"]

        assert [item.key for item in result.groups] == [
            "teams",
            "events",
            "people",
            "photos",
        ]

    def test_group_templates_point_to_cards(self, client, baseball, user):
        make_team(baseball, user, "Громовцы")

        result = search(client, "громов").context["result"]
        templates = {item.key: item.template for item in result.groups}

        assert templates["teams"] == "catalog/partials/team_card.html"
        assert templates["people"] == "catalog/partials/person_card.html"
        assert templates["photos"] == "photos/partials/card.html"

    def test_person_card_hides_display_name(self, client, user):
        """Карточка человека строится только из `public_name`."""
        person = make_person(user, name_visibility=NameVisibility.FIRST_NAME_ONLY)

        html = search(client, "иван").content.decode()

        assert person.public_name in html
        assert "Громов" not in html

    def test_team_card_shows_city(self, client, baseball, user):
        make_team(baseball, user, "Громовцы", city="Тверь")

        html = search(client, "громовцы").content.decode()

        assert "Тверь" in html

    def test_photo_card_shows_title(self, client):
        PhotoFactory(title="Громовский финал")

        html = search(client, "громовский").content.decode()

        assert "Громовский финал" in html
