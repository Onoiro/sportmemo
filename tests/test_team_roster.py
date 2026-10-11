"""Тесты фильтра состава команды по годам.

Проверяем поведение, а не вёрстку: какие годы попадают в фильтр, кто виден
в выбранном году и что черновики людей в выдачу не просачиваются.
"""

import pytest
from django.urls import reverse

from apps.catalog.models import (
    NameVisibility,
    Person,
    Role,
    Team,
    TeamMembership,
)
from apps.catalog.roster import (
    membership_bounds,
    membership_years,
    memberships_in_year,
    roster_years,
)
from apps.core.models import Status
from tests.factories import EventFactory

pytestmark = pytest.mark.django_db


def make_team(baseball, user, name="Соколы", **kwargs):
    kwargs.setdefault("status", Status.PUBLISHED)
    return Team.objects.create(
        sport=baseball,
        name=name,
        created_by=user,
        **kwargs,
    )


def make_person(user, first_name="Иван", last_name="Смирнов", **kwargs):
    kwargs.setdefault("status", Status.PUBLISHED)
    return Person.objects.create(
        first_name=first_name,
        last_name=last_name,
        created_by=user,
        **kwargs,
    )


def make_membership(team, person, **kwargs):
    return TeamMembership.objects.create(team=team, person=person, **kwargs)


@pytest.fixture
def published_team(baseball, user):
    return make_team(baseball, user)


# --- логика периодов: без запросов к базе ------------------------------


class TestMembershipBounds:
    def test_closed_period(self):
        membership = TeamMembership(year_from=1994, year_to=1998)

        assert membership_bounds(membership) == (1994, 1998)

    def test_open_period_ends_with_fallback_year(self):
        membership = TeamMembership(year_from=1992)

        assert membership_bounds(membership, 1998) == (1992, 1998)

    def test_open_period_without_fallback_ends_with_start_year(self):
        membership = TeamMembership(year_from=1992)

        assert membership_bounds(membership) == (1992, 1992)

    def test_only_end_year_gives_one_sided_period(self):
        """Один `year_to` не даёт права утверждать, что играл раньше."""
        membership = TeamMembership(year_to=1998)

        assert membership_bounds(membership) == (None, 1998)

    def test_no_years_gives_empty_bounds(self):
        assert membership_bounds(TeamMembership()) == (None, None)


class TestMembershipYears:
    def test_closed_period_covers_every_year(self):
        membership = TeamMembership(year_from=1994, year_to=1997)

        assert membership_years(membership) == [1994, 1995, 1996, 1997]

    def test_open_period_stops_at_fallback_year(self):
        membership = TeamMembership(year_from=1996)

        assert membership_years(membership, 1998) == [1996, 1997, 1998]

    def test_one_sided_period_gives_single_year(self):
        membership = TeamMembership(year_to=1998)

        assert membership_years(membership) == [1998]

    def test_no_years_gives_nothing(self):
        assert membership_years(TeamMembership()) == []


class TestRosterYears:
    def test_years_go_from_new_to_old(self):
        memberships = [
            TeamMembership(year_from=1994, year_to=1996),
            TeamMembership(year_from=1990, year_to=1991),
        ]

        assert roster_years(memberships) == [1996, 1995, 1994, 1991, 1990]

    def test_gap_year_is_not_included(self):
        """Год, в который не известен ни один человек, не обещаем."""
        memberships = [
            TeamMembership(year_from=1990, year_to=1991),
            TeamMembership(year_from=1994, year_to=1994),
        ]

        assert roster_years(memberships) == [1994, 1991, 1990]

    def test_people_without_years_add_no_years(self):
        memberships = [TeamMembership(), TeamMembership(year_to=None)]

        assert roster_years(memberships) == []

    def test_fallback_year_extends_open_period(self):
        memberships = [TeamMembership(year_from=1996)]

        assert roster_years(memberships, 1998) == [1998, 1997, 1996]


class TestMembershipsInYear:
    def test_returns_only_people_of_this_year(self):
        inside = TeamMembership(
            year_from=1994, year_to=1996, person=Person(first_name="Иван")
        )
        outside = TeamMembership(
            year_from=1990, year_to=1992, person=Person(first_name="Пётр")
        )

        assert memberships_in_year([inside, outside], 1994) == [inside]

    def test_open_period_is_matched_up_to_fallback_year(self):
        membership = TeamMembership(year_from=1996, person=Person(first_name="Иван"))

        assert memberships_in_year([membership], 1998, 1998) == [membership]
        assert memberships_in_year([membership], 1999, 1998) == []

    def test_one_sided_period_is_matched_only_by_end_year(self):
        membership = TeamMembership(year_to=1998, person=Person(first_name="Иван"))

        assert memberships_in_year([membership], 1998) == [membership]
        assert memberships_in_year([membership], 1994) == []

    def test_coaches_go_before_players(self):
        player = TeamMembership(
            year_from=1994, role=Role.PLAYER, person=Person(first_name="Иван")
        )
        coach = TeamMembership(
            year_from=1994, role=Role.COACH, person=Person(first_name="Пётр")
        )

        assert memberships_in_year([player, coach], 1994) == [coach, player]

    def test_people_are_sorted_by_last_name(self):
        first = TeamMembership(
            year_from=1994, person=Person(first_name="Иван", last_name="Абрамов")
        )
        second = TeamMembership(
            year_from=1994, person=Person(first_name="Пётр", last_name="Яблоков")
        )

        assert memberships_in_year([second, first], 1994) == [first, second]

    def test_two_memberships_of_one_person_give_two_rows(self):
        """Игрок и тренер одного человека — две строки: роли различаются."""
        person = Person(first_name="Иван", last_name="Смирнов")
        player = TeamMembership(year_from=1994, person=person, role=Role.PLAYER)
        coach = TeamMembership(year_from=1994, person=person, role=Role.COACH)

        assert memberships_in_year([player, coach], 1994) == [coach, player]


class TestYearsLabel:
    @pytest.mark.parametrize(
        "kwargs,expected",
        [
            ({"year_from": 1994, "year_to": 1998}, "1994 — 1998"),
            ({"year_from": 1994}, "с 1994"),
            ({"year_to": 1998}, "до 1998"),
            ({}, "год не указан"),
        ],
    )
    def test_labels(self, kwargs, expected):
        assert TeamMembership(**kwargs).years_label == expected


# --- страница команды: доступ ------------------------------------------


class TestRosterAccess:
    def test_published_team_opens_anonymously(self, client, published_team):
        response = client.get(published_team.get_absolute_url())

        assert response.status_code == 200

    def test_draft_team_is_404_for_anonymous(self, client, team):
        assert client.get(team.get_absolute_url()).status_code == 404

    def test_draft_team_is_shown_to_author(self, client, team, user):
        client.force_login(user)

        assert client.get(team.get_absolute_url()).status_code == 200

    def test_draft_team_is_shown_to_moderator(self, client, team, moderator):
        client.force_login(moderator)

        assert client.get(team.get_absolute_url()).status_code == 200

    def test_rejects_post(self, client, published_team):
        assert client.post(published_team.get_absolute_url()).status_code == 405


# --- страница команды: фильтр ------------------------------------------


class TestRosterYearFilter:
    def test_year_shows_only_people_of_that_year(self, client, published_team, user):
        inside = make_person(user, "Пётр", "Кузнецов")
        outside = make_person(user, "Лев", "Барсуков")
        make_membership(published_team, inside, year_from=1994, year_to=1996)
        make_membership(published_team, outside, year_from=1990, year_to=1992)

        html = client.get(
            published_team.get_absolute_url(), {"year": 1994}
        ).content.decode()

        assert inside.public_name in html
        assert outside.public_name not in html

    def test_selected_year_is_shown_in_context(self, client, published_team, user):
        person = make_person(user)
        make_membership(published_team, person, year_from=1994)

        response = client.get(published_team.get_absolute_url(), {"year": 1994})

        assert response.context["selected_year"] == 1994

    def test_selected_year_is_not_limited_by_preview(
        self, client, published_team, user
    ):
        """Выбранный год показывается целиком: иначе фильтр бессмыслен."""
        for index in range(13):
            person = make_person(user, f"Имя{index}", f"Фамилия{index}")
            make_membership(published_team, person, year_from=1994)

        response = client.get(published_team.get_absolute_url(), {"year": 1994})

        assert len(response.context["roster"]) == 13

    def test_all_years_mode_is_limited_by_preview(self, client, published_team, user):
        for index in range(13):
            person = make_person(user, f"Имя{index}", f"Фамилия{index}")
            make_membership(published_team, person, year_from=1994)

        response = client.get(published_team.get_absolute_url())

        assert len(response.context["roster"]) == 12

    def test_unknown_year_shows_all_years(self, client, published_team, user):
        person = make_person(user)
        make_membership(published_team, person, year_from=1994)

        response = client.get(published_team.get_absolute_url(), {"year": 1800})

        assert response.context["selected_year"] is None

    def test_non_numeric_year_shows_all_years(self, client, published_team, user):
        person = make_person(user)
        make_membership(published_team, person, year_from=1994)

        response = client.get(published_team.get_absolute_url(), {"year": "abc"})

        assert response.context["selected_year"] is None

    def test_year_without_members_is_not_offered(self, client, published_team, user):
        person = make_person(user)
        make_membership(published_team, person, year_from=1994, year_to=1994)

        response = client.get(published_team.get_absolute_url(), {"year": 1993})

        assert response.context["roster_years"] == [1994]
        assert response.context["selected_year"] is None

    def test_total_does_not_depend_on_filter(self, client, published_team, user):
        for index in range(3):
            person = make_person(user, f"Имя{index}", f"Фамилия{index}")
            make_membership(published_team, person, year_from=1994 + index)

        response = client.get(published_team.get_absolute_url(), {"year": 1994})

        assert response.context["roster_total"] == 3

    def test_open_period_ends_with_last_known_team_year(
        self, client, published_team, user
    ):
        """Без верхней границы человек «размазался» бы на все годы."""
        person = make_person(user)
        make_membership(published_team, person, year_from=1996)
        other = make_person(user, "Пётр", "Кузнецов")
        make_membership(published_team, other, year_from=1992, year_to=1998)

        response = client.get(published_team.get_absolute_url(), {"year": 1998})
        roster = [membership.person for membership in response.context["roster"]]

        assert person in roster
        assert response.context["roster_years"][0] == 1998

    def test_team_event_year_extends_open_period(self, client, published_team, user):
        person = make_person(user)
        make_membership(published_team, person, year_from=1996)
        event = EventFactory(
            sport=published_team.sport,
            year=2001,
            status=Status.PUBLISHED,
            created_by=user,
        )
        event.event_teams.create(team=published_team)

        response = client.get(published_team.get_absolute_url(), {"year": 2001})
        roster = [membership.person for membership in response.context["roster"]]

        assert response.context["roster_years"][0] == 2001
        assert person in roster


class TestRosterFilterForm:
    def test_form_is_shown_when_there_are_years(self, client, published_team, user):
        person = make_person(user)
        make_membership(published_team, person, year_from=1994)

        html = client.get(published_team.get_absolute_url()).content.decode()

        assert 'name="year"' in html
        assert "Все годы" in html

    def test_form_lists_years_from_new_to_old(self, client, published_team, user):
        for year in (1990, 1994):
            person = make_person(user, f"Имя{year}", f"Фамилия{year}")
            make_membership(published_team, person, year_from=year, year_to=year)

        html = client.get(published_team.get_absolute_url()).content.decode()

        assert html.index(">1994<") < html.index(">1990<")

    def test_selected_year_is_marked_in_form(self, client, published_team, user):
        person = make_person(user)
        make_membership(published_team, person, year_from=1994)

        html = client.get(
            published_team.get_absolute_url(), {"year": 1994}
        ).content.decode()

        assert "1994 год" in html
        assert "Показать все годы" in html

    def test_form_is_hidden_when_no_members_have_years(
        self, client, published_team, user
    ):
        person = make_person(user)
        make_membership(published_team, person)

        html = client.get(published_team.get_absolute_url()).content.decode()

        assert 'name="year"' not in html
        assert person.public_name in html

    def test_form_is_hidden_when_roster_is_empty(self, client, published_team):
        html = client.get(published_team.get_absolute_url()).content.decode()

        assert 'name="year"' not in html

    def test_hint_about_incomplete_archive_is_shown(self, client, published_team, user):
        person = make_person(user)
        make_membership(published_team, person, year_from=1994)

        html = client.get(published_team.get_absolute_url()).content.decode()

        assert "В архиве указаны не все, кто играл" in html

    def test_english_labels(self, client, published_team, user):
        person = make_person(user)
        make_membership(published_team, person, year_from=1994)

        html = client.get(
            published_team.get_absolute_url(), headers={"accept-language": "en"}
        ).content.decode()

        assert "All years" in html
        assert "The archive does not list everyone who played." in html


class TestRosterPrivacy:
    def test_draft_person_is_hidden_in_filtered_list(
        self, client, published_team, user
    ):
        draft = make_person(user, "Лев", "Барсуков", status=Status.DRAFT)
        make_membership(published_team, draft, year_from=1994)

        response = client.get(published_team.get_absolute_url(), {"year": 1994})

        assert response.context["roster"] == []
        assert response.context["roster_total"] == 0
        assert "Барсуков" not in response.content.decode()

    def test_hidden_person_last_name_is_not_shown(self, client, published_team, user):
        person = make_person(
            user,
            "Станислав",
            "Секретный",
            name_visibility=NameVisibility.HIDDEN,
        )
        make_membership(published_team, person, year_from=1994)

        html = client.get(
            published_team.get_absolute_url(), {"year": 1994}
        ).content.decode()

        assert "Секретный" not in html

    def test_first_name_only_person_hides_last_name(self, client, published_team, user):
        person = make_person(
            user,
            "Кирилл",
            "Прохоров",
            name_visibility=NameVisibility.FIRST_NAME_ONLY,
        )
        make_membership(published_team, person, year_from=1994)

        html = client.get(
            published_team.get_absolute_url(), {"year": 1994}
        ).content.decode()

        assert "Кирилл" in html
        assert "Прохоров" not in html

    def test_person_names_link_to_person_pages(self, client, published_team, user):
        person = make_person(user)
        make_membership(published_team, person, year_from=1994)

        html = client.get(
            published_team.get_absolute_url(), {"year": 1994}
        ).content.decode()

        assert person.get_absolute_url() in html

    def test_role_and_years_are_shown(self, client, published_team, user):
        person = make_person(user)
        make_membership(
            published_team,
            person,
            year_from=1994,
            year_to=1998,
            role=Role.COACH,
            position="тренер",
            number="7",
        )

        html = client.get(
            published_team.get_absolute_url(), {"year": 1994}
        ).content.decode()

        assert "тренер" in html
        assert "1994 — 1998" in html
        assert "№ 7" in html

    def test_cyrillic_team_slug_opens_with_filter(self, client, baseball, user):
        team = make_team(baseball, user, "Соколы", slug="соколы")
        person = make_person(user)
        make_membership(team, person, year_from=1994)

        url = reverse("catalog:team_detail", args=["соколы"])
        response = client.get(url, {"year": 1994})

        assert response.status_code == 200
        assert response.context["selected_year"] == 1994
