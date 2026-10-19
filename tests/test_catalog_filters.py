"""Тесты фильтров каталога: команды и события.

Проверяем смысл, а не вёрстку: какие объекты попадают в выдачу, что происходит
с невалидным параметром, как считаются справочники вариантов и какой адрес
получает `canonical`.
"""

import html as html_module
import re
from urllib.parse import urlencode

import pytest
from django.urls import reverse

from apps.catalog.filters import (
    city_choices,
    decade_bounds,
    decade_choices,
    normalize_city,
    type_choices,
    year_choices,
)
from apps.catalog.models import Event, EventType, Team
from apps.core.models import Status
from tests.factories import PhotoFactory

pytestmark = pytest.mark.django_db


def canonical_href(html):
    match = re.search(r'<link rel="canonical" href="([^"]*)"', html)
    return html_module.unescape(match.group(1)) if match else None


def robots_content(html):
    match = re.search(r'<meta name="robots" content="([^"]*)"', html)
    return match.group(1) if match else None


def make_team(sport, user, name, **kwargs):
    kwargs.setdefault("status", Status.PUBLISHED)
    kwargs.setdefault("city", "Москва")
    return Team.objects.create(sport=sport, name=name, created_by=user, **kwargs)


def make_event(sport, user, name, **kwargs):
    kwargs.setdefault("status", Status.PUBLISHED)
    return Event.objects.create(sport=sport, name=name, created_by=user, **kwargs)


def listed_names(response):
    return [obj.name for obj in response.context["page_obj"].object_list]


# --- чистая логика ------------------------------------------------------


class TestHelpers:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("Москва", "москва"),
            (" москва ", "москва"),
            ("Санкт-Петербург", "санкт-петербург"),
            ("", ""),
        ],
    )
    def test_normalize_city(self, value, expected):
        assert normalize_city(value) == expected

    def test_decade_bounds(self):
        assert decade_bounds(1980) == (1980, 1989)

    def test_city_choices_merge_case_variants(self, baseball, user):
        make_team(baseball, user, "Первая", city="Москва")
        make_team(baseball, user, "Вторая", city="москва")
        make_team(baseball, user, "Третья", city="Казань")

        assert [value for value, _ in team_filters_choices()] == [
            "Казань",
            "Москва",
        ]

    def test_city_choices_skip_empty(self, baseball, user):
        make_team(baseball, user, "Без города", city="")
        make_team(baseball, user, "С городом", city="Казань")

        assert [value for value, _ in team_filters_choices()] == ["Казань"]

    def test_decade_choices_from_new_to_old(self, baseball, user):
        make_team(baseball, user, "А", founded_year=1985)
        make_team(baseball, user, "Б", founded_year=2003)
        make_team(baseball, user, "В", founded_year=1989)

        starts = [value for value, _ in decade_choices(Team.public.published())]

        assert starts == ["2000", "1980"]

    def test_year_choices_from_new_to_old(self, baseball, user):
        make_event(baseball, user, "А", year=1985)
        make_event(baseball, user, "Б", year=2003)

        assert [value for value, _ in year_choices(Event.public.published())] == [
            "2003",
            "1985",
        ]

    def test_type_choices_only_present(self, baseball, user):
        make_event(baseball, user, "Турнир", event_type=EventType.TOURNAMENT)

        values = [value for value, _ in type_choices(Event.public.published())]

        assert values == [EventType.TOURNAMENT]

    def test_unpublished_objects_do_not_create_options(self, baseball, user):
        make_team(baseball, user, "Черновик", city="Казань", status=Status.DRAFT)
        make_team(baseball, user, "Скрытая", city="Тула", status=Status.HIDDEN)

        assert team_filters_choices() == []


def team_filters_choices():
    """Города из справочника фильтров — без обращения к представлению."""
    return city_choices(Team.public.published())


# --- фильтры списка команд ---------------------------------------------


class TestTeamListFilters:
    def test_city_filter(self, client, baseball, user):
        make_team(baseball, user, "Московская", city="Москва")
        make_team(baseball, user, "Казанская", city="Казань")

        response = client.get(reverse("catalog:team_list"), {"city": "Казань"})

        assert listed_names(response) == ["Казанская"]

    def test_city_filter_ignores_case(self, client, baseball, user):
        make_team(baseball, user, "Московская", city="Москва")

        response = client.get(reverse("catalog:team_list"), {"city": "москва"})

        assert listed_names(response) == ["Московская"]

    def test_decade_filter_uses_whole_decade(self, client, baseball, user):
        make_team(baseball, user, "Начало", founded_year=1980)
        make_team(baseball, user, "Середина", founded_year=1985)
        make_team(baseball, user, "Конец", founded_year=1989)
        make_team(baseball, user, "Следующее", founded_year=1990)

        response = client.get(reverse("catalog:team_list"), {"decade": "1980"})

        assert listed_names(response) == ["Конец", "Начало", "Середина"]

    def test_combined_filters(self, client, baseball, user):
        make_team(baseball, user, "Москва 1985", city="Москва", founded_year=1985)
        make_team(baseball, user, "Москва 1995", city="Москва", founded_year=1995)
        make_team(baseball, user, "Казань 1985", city="Казань", founded_year=1985)

        response = client.get(
            reverse("catalog:team_list"), {"city": "Москва", "decade": "1980"}
        )

        assert listed_names(response) == ["Москва 1985"]

    @pytest.mark.parametrize("value", ["abc", "1985", "-1", "", "9999"])
    def test_broken_values_are_ignored(self, client, baseball, user, value):
        make_team(baseball, user, "Московская", city="Москва", founded_year=1985)

        response = client.get(reverse("catalog:team_list"), {"decade": value})

        assert response.status_code == 200
        assert listed_names(response) == ["Московская"]

    def test_unknown_city_is_ignored(self, client, baseball, user):
        make_team(baseball, user, "Московская", city="Москва")

        response = client.get(reverse("catalog:team_list"), {"city": "Омск"})

        assert listed_names(response) == ["Московская"]
        assert response.context["filters"].has_active is False

    def test_sort_by_photos(self, client, baseball, user):
        many = make_team(baseball, user, "Много", founded_year=1990)
        few = make_team(baseball, user, "Мало", founded_year=1990)
        PhotoFactory(team=many)
        PhotoFactory(team=many)

        response = client.get(reverse("catalog:team_list"), {"sort": "photos"})

        assert listed_names(response) == [many.name, few.name]

    def test_unknown_sort_falls_back_to_name(self, client, baseball, user):
        make_team(baseball, user, "Ястребы", founded_year=1990)
        make_team(baseball, user, "Акулы", founded_year=1990)

        response = client.get(reverse("catalog:team_list"), {"sort": "photos_total"})

        assert listed_names(response) == ["Акулы", "Ястребы"]

    def test_filters_do_not_leak_unpublished(self, client, baseball, user):
        make_team(baseball, user, "Видимая", city="Москва")
        make_team(baseball, user, "Черновик", city="Москва", status=Status.DRAFT)
        deleted = make_team(baseball, user, "Удалённая", city="Москва")
        deleted.soft_delete()
        target = make_team(baseball, user, "Канон", city="Москва")
        source = make_team(baseball, user, "Дубликат", city="Москва")
        source.merge_into(target)

        response = client.get(reverse("catalog:team_list"), {"city": "Москва"})

        assert listed_names(response) == ["Видимая", "Канон"]

    def test_filter_kept_in_pagination(self, client, baseball, user):
        for index in range(25):
            make_team(
                baseball, user, f"Москва {index:02d}", city="Москва", founded_year=1985
            )
        make_team(baseball, user, "Казань", city="Казань", founded_year=1985)

        response = client.get(reverse("catalog:team_list"), {"city": "Москва"})
        html = response.content.decode()

        assert response.context["page_obj"].paginator.num_pages == 2
        assert "city=" in html
        assert "page=2" in html

    def test_empty_result_with_active_filters(self, client, baseball, user):
        """Оба условия валидны по отдельности, вместе — пусто.

        Значение, которого нет в справочнике, считается неприменённым
        фильтром: опечатка не должна давать «ничего не найдено».
        """
        make_team(baseball, user, "Москва 1995", city="Москва", founded_year=1995)
        make_team(baseball, user, "Казань 1985", city="Казань", founded_year=1985)

        html = client.get(
            reverse("catalog:team_list"), {"city": "Москва", "decade": "1980"}
        ).content.decode()

        assert "По выбранным условиям ничего не найдено" in html
        assert "В архиве пока нет команд" not in html

    def test_empty_archive_message(self, client):
        html = client.get(reverse("catalog:team_list")).content.decode()

        assert "В архиве пока нет команд" in html
        assert "По выбранным условиям ничего не найдено" not in html

    def test_panel_has_options(self, client, baseball, user):
        make_team(baseball, user, "Московская", city="Москва", founded_year=1985)

        response = client.get(reverse("catalog:team_list"))
        panel = response.context["filters"]
        fields = {field.name: field for field in panel.fields}

        assert set(fields) == {"city", "decade"}
        assert [option.value for option in fields["city"].options] == ["Москва"]
        assert [option.value for option in fields["decade"].options] == ["1980"]
        assert panel.sort.name == "sort"

    def test_sorting_is_not_an_active_filter(self, client, baseball, user):
        make_team(baseball, user, "Московская", city="Москва")

        response = client.get(reverse("catalog:team_list"), {"sort": "photos"})

        assert response.context["filters"].has_active is False

    def test_reset_url_keeps_sort(self, client, baseball, user):
        make_team(baseball, user, "Московская", city="Москва")

        response = client.get(
            reverse("catalog:team_list"), {"city": "Москва", "sort": "photos"}
        )

        assert response.context["filters"].reset_url == (
            f"{reverse('catalog:team_list')}?sort=photos"
        )


# --- фильтры списка событий --------------------------------------------


class TestEventListFilters:
    def test_year_filter(self, client, baseball, user):
        make_event(baseball, user, "Старое", year=1985)
        make_event(baseball, user, "Новое", year=2003)

        response = client.get(reverse("catalog:event_list"), {"year": "2003"})

        assert listed_names(response) == ["Новое"]

    def test_type_filter(self, client, baseball, user):
        make_event(baseball, user, "Турнир", event_type=EventType.TOURNAMENT)
        make_event(baseball, user, "Встреча", event_type=EventType.FRIENDLY)

        response = client.get(
            reverse("catalog:event_list"), {"type": EventType.FRIENDLY}
        )

        assert listed_names(response) == ["Встреча"]

    def test_city_filter(self, client, baseball, user):
        make_event(baseball, user, "Московское", city="Москва")
        make_event(baseball, user, "Казанское", city="Казань")

        response = client.get(reverse("catalog:event_list"), {"city": "Казань"})

        assert listed_names(response) == ["Казанское"]

    def test_combined_filters(self, client, baseball, user):
        make_event(
            baseball,
            user,
            "Нужное",
            year=1994,
            city="Москва",
            event_type=EventType.TOURNAMENT,
        )
        make_event(
            baseball,
            user,
            "Другой тип",
            year=1994,
            city="Москва",
            event_type=EventType.SERIES,
        )
        make_event(
            baseball,
            user,
            "Другой год",
            year=2003,
            city="Москва",
            event_type=EventType.TOURNAMENT,
        )

        response = client.get(
            reverse("catalog:event_list"),
            {"year": "1994", "city": "Москва", "type": EventType.TOURNAMENT},
        )

        assert listed_names(response) == ["Нужное"]

    def test_default_sort_is_newest_first(self, client, baseball, user):
        make_event(baseball, user, "Старое", year=1985)
        make_event(baseball, user, "Новое", year=2003)

        response = client.get(reverse("catalog:event_list"))

        assert listed_names(response) == ["Новое", "Старое"]

    def test_sort_oldest_first(self, client, baseball, user):
        make_event(baseball, user, "Старое", year=1985)
        make_event(baseball, user, "Новое", year=2003)

        response = client.get(reverse("catalog:event_list"), {"sort": "old"})

        assert listed_names(response) == ["Старое", "Новое"]

    def test_events_without_year_go_last_in_both_orders(self, client, baseball, user):
        make_event(baseball, user, "Без года", year=None)
        make_event(baseball, user, "С годом", year=1994)

        newest = client.get(reverse("catalog:event_list"), {"sort": "new"})
        oldest = client.get(reverse("catalog:event_list"), {"sort": "old"})

        assert listed_names(newest) == ["С годом", "Без года"]
        assert listed_names(oldest) == ["С годом", "Без года"]

    def test_filters_do_not_leak_unpublished(self, client, baseball, user):
        make_event(baseball, user, "Видимое", year=1994)
        make_event(baseball, user, "Черновик", year=1994, status=Status.DRAFT)
        make_event(baseball, user, "Скрытое", year=1994, status=Status.HIDDEN)
        deleted = make_event(baseball, user, "Удалённое", year=1994)
        deleted.soft_delete()

        response = client.get(reverse("catalog:event_list"), {"year": "1994"})

        assert listed_names(response) == ["Видимое"]

    def test_panel_lists_only_present_values(self, client, baseball, user):
        make_event(
            baseball,
            user,
            "Турнир",
            year=1994,
            city="Москва",
            event_type=EventType.TOURNAMENT,
        )

        response = client.get(reverse("catalog:event_list"))
        fields = {field.name: field for field in response.context["filters"].fields}

        assert [option.value for option in fields["year"].options] == ["1994"]
        assert [option.value for option in fields["type"].options] == [
            EventType.TOURNAMENT
        ]
        assert [option.value for option in fields["city"].options] == ["Москва"]

    def test_empty_result_with_active_filters(self, client, baseball, user):
        make_event(baseball, user, "Турнир", year=1994, city="Москва")
        make_event(baseball, user, "Серия", year=2003, city="Казань")

        html = client.get(
            reverse("catalog:event_list"), {"year": "1994", "city": "Казань"}
        ).content.decode()

        assert "По выбранным условиям ничего не найдено" in html
        assert "В архиве пока нет событий" not in html


# --- canonical и индексация -------------------------------------------


class TestCanonical:
    def test_canonical_keeps_valid_filters(self, client, baseball, user):
        make_team(baseball, user, "Московская", city="Москва", founded_year=1985)

        html = client.get(
            reverse("catalog:team_list"), {"city": "Москва", "decade": "1980"}
        ).content.decode()

        query = urlencode({"city": "Москва", "decade": "1980"})
        assert canonical_href(html) == f"http://testserver/teams/?{query}"

    def test_canonical_drops_junk_and_page(self, client, baseball, user):
        make_team(baseball, user, "Московская", city="Москва")

        html = client.get(
            reverse("catalog:team_list"),
            {"city": "Москва", "foo": "bar", "page": 2, "sort": "photos_total"},
        ).content.decode()

        expected = f"http://testserver/teams/?{urlencode({'city': 'Москва'})}"
        assert canonical_href(html) == expected

    def test_canonical_normalizes_city_case(self, client, baseball, user):
        make_team(baseball, user, "Московская", city="Москва")

        html = client.get(
            reverse("catalog:team_list"), {"city": "москва"}
        ).content.decode()

        expected = f"http://testserver/teams/?{urlencode({'city': 'Москва'})}"
        assert canonical_href(html) == expected

    def test_canonical_keeps_sort(self, client, baseball, user):
        make_team(baseball, user, "Московская", city="Москва")

        html = client.get(
            reverse("catalog:team_list"), {"sort": "photos"}
        ).content.decode()

        assert canonical_href(html) == "http://testserver/teams/?sort=photos"

    def test_second_page_is_noindex(self, client, baseball, user):
        for index in range(25):
            make_team(baseball, user, f"Москва {index:02d}", city="Москва")

        html = client.get(
            reverse("catalog:team_list"), {"city": "Москва", "page": 2}
        ).content.decode()

        assert robots_content(html) == "noindex, follow"


# --- форма фильтров ----------------------------------------------------


class TestFilterForm:
    def test_form_is_plain_get(self, client, baseball, user):
        make_team(baseball, user, "Московская", city="Москва")

        html = client.get(reverse("catalog:team_list")).content.decode()

        assert 'class="filter-bar"' in html
        assert 'method="get"' in html
        assert 'name="city"' in html
        assert 'name="sort"' in html

    def test_reset_link_only_when_filters_active(self, client, baseball, user):
        make_team(baseball, user, "Московская", city="Москва")

        without = client.get(reverse("catalog:team_list")).content.decode()
        with_filter = client.get(
            reverse("catalog:team_list"), {"city": "Москва"}
        ).content.decode()

        assert "Сбросить фильтры" not in without
        assert "Сбросить фильтры" in with_filter

    def test_selected_option_is_marked(self, client, baseball, user):
        make_team(baseball, user, "Московская", city="Москва")

        html = client.get(
            reverse("catalog:team_list"), {"city": "Москва"}
        ).content.decode()

        assert re.search(r'<option value="Москва"\s+selected', html)
