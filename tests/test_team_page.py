"""Тесты страницы команды.

Проверяем поведение, а не вёрстку: кто видит страницу, что попадает
в фотографии, события и состав. Разметка меняется чаще, чем смысл страницы.
"""

from urllib.parse import quote

import pytest
from django.urls import reverse

from apps.catalog.models import NameVisibility, Person, Team, TeamMembership
from apps.core.models import Status
from tests.factories import EventFactory, PhotoFactory
from tests.utils import publish_with_attributes

pytestmark = pytest.mark.django_db


def make_team(baseball, user, name="Соколы", **kwargs):
    """Опубликованная команда с предсказуемым именем."""
    return Team.objects.create(
        sport=baseball,
        name=name,
        created_by=user,
        status=Status.PUBLISHED,
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


class TestTeamDetailAccess:
    def test_opens_anonymously_for_published(self, client, team):
        team.publish()

        response = client.get(team.get_absolute_url())

        assert response.status_code == 200
        assert team.name in response.content.decode()

    def test_draft_is_404_for_anonymous(self, client, team):
        assert client.get(team.get_absolute_url()).status_code == 404

    def test_draft_is_404_for_stranger(self, client, team, other_user):
        client.force_login(other_user)

        assert client.get(team.get_absolute_url()).status_code == 404

    def test_draft_is_shown_to_author(self, client, team, user):
        client.force_login(user)

        assert client.get(team.get_absolute_url()).status_code == 200

    def test_draft_is_shown_to_moderator(self, client, team, moderator):
        client.force_login(moderator)

        assert client.get(team.get_absolute_url()).status_code == 200

    def test_hidden_is_404_for_anonymous(self, client, team):
        team.status = Status.HIDDEN
        team.save()

        assert client.get(team.get_absolute_url()).status_code == 404

    def test_deleted_is_404_for_author(self, client, team, user):
        team.publish()
        team.soft_delete()
        client.force_login(user)

        assert client.get(team.get_absolute_url()).status_code == 404

    def test_merged_is_404(self, client, baseball, user):
        target = make_team(baseball, user, "Канон")
        source = make_team(baseball, user, "Дубликат")
        source.merge_into(target)

        assert client.get(source.get_absolute_url()).status_code == 404

    def test_unknown_slug_is_404(self, client):
        url = reverse("catalog:team_detail", args=["нет-такой"])

        assert client.get(url).status_code == 404

    def test_rejects_post(self, client, team):
        team.publish()

        assert client.post(team.get_absolute_url()).status_code == 405

    def test_cyrillic_slug_opens(self, client, baseball, user):
        team = make_team(baseball, user, "Соколы", slug="соколы")

        response = client.get(team.get_absolute_url())

        assert response.status_code == 200
        assert team.name in response.content.decode()


class TestTeamModerationNotice:
    def test_notice_is_shown_to_author_of_draft(self, client, team, user):
        client.force_login(user)

        html = client.get(team.get_absolute_url()).content.decode()

        assert "Не опубликовано" in html
        assert "Видно только вам и модератору." in html

    def test_notice_is_not_shown_for_published(self, client, team):
        team.publish()

        html = client.get(team.get_absolute_url()).content.decode()

        assert "Не опубликовано" not in html


class TestTeamPhotos:
    def test_shows_only_visible_photos(self, client, team):
        team.publish()
        shown = PhotoFactory(team=team, title="Видимое фото команды")
        PhotoFactory(team=team, title="Черновик фото", status=Status.DRAFT)
        deleted = PhotoFactory(team=team, title="Удалённое фото команды")
        deleted.soft_delete()
        PhotoFactory(title="Чужое фото")

        html = client.get(team.get_absolute_url()).content.decode()

        assert shown.title in html
        for title in (
            "Черновик фото",
            "Удалённое фото команды",
            "Чужое фото",
        ):
            assert title not in html

    def test_photo_without_rights_is_shown(self, client, team):
        """Права проверяет автор: исторический флаг видимость не меняет."""
        team.publish()
        photo = PhotoFactory(team=team, title="Без прав", rights_confirmed=False)

        html = client.get(team.get_absolute_url()).content.decode()

        assert photo.title in html

    def test_gallery_keeps_team_filter_in_more_link(self, client, team):
        team.publish()
        for _ in range(25):
            PhotoFactory(team=team)

        html = client.get(team.get_absolute_url()).content.decode()

        assert f"team={quote(team.slug)}" in html

    def test_no_photo_section_when_no_photos(self, client, team):
        team.publish()

        html = client.get(team.get_absolute_url()).content.decode()

        assert "Фотографии команды" not in html


class TestTeamEvents:
    def test_shows_only_published_events(self, client, team):
        team.publish()
        shown = EventFactory(
            sport=team.sport,
            name="Опубликованное событие",
            status=Status.PUBLISHED,
            created_by=team.created_by,
        )
        shown.event_teams.create(team=team)
        draft = EventFactory(
            sport=team.sport,
            name="Черновик события",
            created_by=team.created_by,
        )
        draft.event_teams.create(team=team)

        html = client.get(team.get_absolute_url()).content.decode()

        assert shown.name in html
        assert draft.name not in html

    def test_no_event_section_when_no_events(self, client, team):
        team.publish()

        html = client.get(team.get_absolute_url()).content.decode()

        assert "События команды" not in html

    def test_event_card_links_to_event(self, client, team):
        team.publish()
        event = EventFactory(
            sport=team.sport,
            name="Чемпионат",
            status=Status.PUBLISHED,
            created_by=team.created_by,
        )
        event.event_teams.create(team=team)

        html = client.get(team.get_absolute_url()).content.decode()

        assert event.get_absolute_url() in html


class TestTeamRoster:
    def test_shows_memberships(self, client, team, user):
        team.publish()
        person = make_person(user, "Пётр", "Кузнецов")
        TeamMembership.objects.create(team=team, person=person, year_from=1994)

        html = client.get(team.get_absolute_url()).content.decode()

        assert person.public_name in html

    def test_hides_draft_persons(self, client, team, user):
        team.publish()
        person = make_person(user, "Лев", "Барсуков", status=Status.DRAFT)
        TeamMembership.objects.create(team=team, person=person, year_from=1994)

        html = client.get(team.get_absolute_url()).content.decode()

        assert "Барсуков" not in html

    def test_hides_hidden_last_name(self, client, team, user):
        team.publish()
        person = make_person(
            user,
            "Станислав",
            "Секретный",
            name_visibility=NameVisibility.HIDDEN,
        )
        TeamMembership.objects.create(team=team, person=person, year_from=1994)

        html = client.get(team.get_absolute_url()).content.decode()

        assert "Секретный" not in html

    def test_shows_first_name_only_without_last_name(self, client, team, user):
        team.publish()
        person = make_person(
            user,
            "Кирилл",
            "Прохоров",
            name_visibility=NameVisibility.FIRST_NAME_ONLY,
        )
        TeamMembership.objects.create(team=team, person=person, year_from=1994)

        html = client.get(team.get_absolute_url()).content.decode()

        assert "Кирилл" in html
        assert "Прохоров" not in html

    def test_roster_is_limited(self, client, team, user):
        team.publish()
        for index in range(13):
            person = make_person(user, f"Имя{index}", f"Фамилия{index}")
            TeamMembership.objects.create(
                team=team, person=person, year_from=1990 + index
            )

        response = client.get(team.get_absolute_url())

        assert len(response.context["roster"]) == 12

    def test_roster_names_link_to_person_pages(self, client, team, user):
        """Человек в составе уже отфильтрован по публичности: ссылка есть."""
        team.publish()
        person = make_person(user)
        TeamMembership.objects.create(team=team, person=person, year_from=1994)

        html = client.get(team.get_absolute_url()).content.decode()

        assert person.get_absolute_url() in html

    def test_no_roster_section_when_empty(self, client, team):
        team.publish()

        html = client.get(team.get_absolute_url()).content.decode()

        assert "Состав" not in html

    def test_year_filter_and_total_are_shown(self, client, team, user):
        """Фильтр по годам живёт на этой странице: см. test_team_roster.py."""
        team.publish()
        for index in range(3):
            person = make_person(user, f"Имя{index}", f"Фамилия{index}")
            TeamMembership.objects.create(
                team=team, person=person, year_from=1994 + index
            )

        response = client.get(team.get_absolute_url())

        assert response.context["roster_total"] == 3
        assert response.context["roster_years"] == [1996, 1995, 1994]
        assert 'name="year"' in response.content.decode()

    def test_year_filter_is_hidden_when_roster_is_empty(self, client, team):
        team.publish()

        html = client.get(team.get_absolute_url()).content.decode()

        assert 'name="year"' not in html


class TestTeamAttributes:
    def test_known_key_is_translated(self, client, team):
        publish_with_attributes(team, {"stage": "финал"})

        response = client.get(team.get_absolute_url())

        assert response.context["attribute_items"] == [
            {"label": "Этап", "value": "финал"}
        ]

    def test_service_key_is_hidden(self, client, team):
        publish_with_attributes(team, {"demo": True, "stage": "финал"})

        response = client.get(team.get_absolute_url())
        labels = [item["label"] for item in response.context["attribute_items"]]

        assert "demo" not in labels
        assert labels == ["Этап"]

    def test_unknown_key_is_shown_as_is(self, client, team):
        publish_with_attributes(team, {"custom_field": "значение"})

        response = client.get(team.get_absolute_url())

        assert response.context["attribute_items"] == [
            {"label": "custom_field", "value": "значение"}
        ]

    def test_list_value_is_joined(self, client, team):
        publish_with_attributes(team, {"teams": ["Соколы", "Медведи"]})

        response = client.get(team.get_absolute_url())

        assert response.context["attribute_items"] == [
            {"label": "Команд", "value": "Соколы, Медведи"}
        ]

    def test_no_attributes_section_when_empty(self, client, team):
        team.publish()

        html = client.get(team.get_absolute_url()).content.decode()

        assert "Дополнительно" not in html


class TestTeamPageLayout:
    def test_breadcrumbs(self, client, team):
        team.publish()

        html = client.get(team.get_absolute_url()).content.decode()

        assert "Главная" in html
        assert reverse("catalog:team_list") in html
        assert team.name in html

    def test_minimal_team_opens(self, client, baseball, user):
        team = make_team(baseball, user, "Пустая команда")

        response = client.get(team.get_absolute_url())

        assert response.status_code == 200

    def test_minimal_team_has_no_empty_sections(self, client, baseball, user):
        team = make_team(baseball, user, "Пустая команда")

        html = client.get(team.get_absolute_url()).content.decode()

        for heading in (
            "Фотографии команды",
            "События команды",
            "Состав",
            "Дополнительно",
        ):
            assert heading not in html

    def test_shows_city_founded_year_and_sport(self, client, baseball, user):
        team = make_team(baseball, user, "Соколы", city="Москва", founded_year=1985)

        html = client.get(team.get_absolute_url()).content.decode()

        assert "Москва" in html
        assert "1985" in html
        assert baseball.name in html
