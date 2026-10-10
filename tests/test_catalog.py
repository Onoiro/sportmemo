"""Тесты каталога: команды, люди, события и их инварианты."""

import pytest
from django.db import IntegrityError

from apps.catalog.models import (
    Event,
    NameVisibility,
    Person,
    PersonName,
    Role,
    Team,
    TeamMembership,
)
from apps.core.models import Status

pytestmark = pytest.mark.django_db


class TestTeamSlug:
    def test_slug_generated_from_name(self, baseball, user):
        team = Team.objects.create(
            sport=baseball, name="Сборная России", created_by=user
        )

        assert team.slug == "сборная-россии"

    def test_slug_unique_within_sport(self, team, baseball, user):
        with pytest.raises(IntegrityError):
            Team.objects.create(
                sport=baseball, name="Динамо", slug=team.slug, created_by=user
            )

    def test_same_slug_allowed_in_other_sport(self, team, user):
        from apps.core.models import Sport

        cricket = Sport.objects.create(name="Крикет", slug="cricket")
        other = Team.objects.create(
            sport=cricket, name="Динамо", slug=team.slug, created_by=user
        )

        assert other.slug == team.slug
        assert other.sport != team.sport


class TestTeamMerge:
    def test_merge_moves_memberships_photos_and_event_teams(
        self, team, person, event, user
    ):
        from apps.catalog.models import EventTeam
        from apps.photos.models import Photo

        target = Team.objects.create(sport=team.sport, name="Канон", created_by=user)
        membership = TeamMembership.objects.create(
            team=team, person=person, year_from=1994
        )
        event_team = EventTeam.objects.create(event=event, team=team)
        photo = Photo.objects.create(
            sport=team.sport,
            team=team,
            title="Фото",
            original="photos/original/test.jpg",
            uploaded_by=user,
        )

        team.merge_into(target)

        assert TeamMembership.objects.get(pk=membership.pk).team == target
        assert EventTeam.objects.get(pk=event_team.pk).team == target
        assert Photo.objects.get(pk=photo.pk).team == target

    def test_merge_records_history(self, team, baseball, user):
        target = Team.objects.create(sport=baseball, name="Канон", created_by=user)
        team.merge_into(target)

        assert team.history.count() >= 2


class TestTeamMembership:
    def test_unique_per_person_role_and_year(self, team, person):
        TeamMembership.objects.create(
            team=team, person=person, role=Role.PLAYER, year_from=1994
        )

        with pytest.raises(IntegrityError):
            TeamMembership.objects.create(
                team=team, person=person, role=Role.PLAYER, year_from=1994
            )

    def test_same_person_can_be_player_and_coach(self, team, person):
        TeamMembership.objects.create(
            team=team, person=person, role=Role.PLAYER, year_from=1994
        )
        TeamMembership.objects.create(
            team=team, person=person, role=Role.COACH, year_from=2000
        )

        assert team.memberships.count() == 2

    def test_str_shows_years(self, team, person):
        membership = TeamMembership.objects.create(
            team=team, person=person, year_from=1994, year_to=1998
        )

        assert "1994–1998" in str(membership)


class TestPersonPublicName:
    def test_full_name_by_default(self):
        person = Person(first_name="Иван", middle_name="Петрович", last_name="Смирнов")

        assert person.full_name == "Иван Петрович Смирнов"
        assert person.public_name == "Иван Петрович Смирнов"

    def test_first_name_only_hides_last_name(self):
        person = Person(
            first_name="Ваня",
            last_name="Смирнов",
            name_visibility=NameVisibility.FIRST_NAME_ONLY,
        )

        assert person.public_name == "Ваня"
        assert "Смирнов" not in person.public_name

    def test_hidden_returns_placeholder(self):
        person = Person(
            first_name="Ваня",
            last_name="Смирнов",
            name_visibility=NameVisibility.HIDDEN,
        )

        assert person.public_name == "Имя не указано"

    def test_public_name_used_in_str(self, person):
        person.name_visibility = NameVisibility.FIRST_NAME_ONLY
        person.save()

        assert str(person) == "Иван"

    def test_display_name_filled_on_save(self, user):
        person = Person.objects.create(
            first_name="Иван", last_name="Смирнов", created_by=user
        )

        assert person.display_name == "Иван Смирнов"


class TestPersonSlug:
    def test_slug_is_transliterated_from_name(self, user):
        person = Person.objects.create(
            first_name="Иван", last_name="Смирнов", created_by=user
        )

        assert person.slug
        assert Person.objects.filter(slug=person.slug).count() == 1

    def test_collision_gets_numeric_suffix(self, user):
        first = Person.objects.create(
            first_name="Иван", last_name="Смирнов", created_by=user
        )
        second = Person.objects.create(
            first_name="Иван", last_name="Смирнов", created_by=user
        )

        assert first.slug != second.slug
        assert second.slug.startswith(first.slug)
        assert second.slug.endswith("-2")


class TestPersonMerge:
    def test_merge_moves_names_memberships_and_photos(self, person, team, user):
        from apps.photos.models import Photo, PhotoPerson

        target = Person.objects.create(
            first_name="Иван", last_name="Смирнов", created_by=user
        )
        name = PersonName.objects.create(person=person, value="Смит")
        membership = TeamMembership.objects.create(
            team=team, person=person, year_from=1994
        )
        photo = Photo.objects.create(
            sport=team.sport,
            title="Фото",
            original="photos/original/test.jpg",
            uploaded_by=user,
        )
        photo_person = PhotoPerson.objects.create(photo=photo, person=person)

        person.merge_into(target)

        assert PersonName.objects.get(pk=name.pk).person == target
        assert TeamMembership.objects.get(pk=membership.pk).person == target
        assert PhotoPerson.objects.get(pk=photo_person.pk).person == target

    def test_merge_keeps_target_avatar(self, person, user, baseball):
        from apps.photos.models import Photo

        target = Person.objects.create(
            first_name="Пётр", last_name="Иванов", created_by=user
        )
        target_photo = Photo.objects.create(
            sport=baseball,
            title="Фото цели",
            original="photos/original/a.jpg",
            uploaded_by=user,
        )
        target.avatar = target_photo
        target.save()

        person.merge_into(target)

        target.refresh_from_db()
        assert target.avatar == target_photo


class TestPersonName:
    def test_unique_value_per_person(self, person):
        PersonName.objects.create(person=person, value="Смит")

        with pytest.raises(IntegrityError):
            PersonName.objects.create(person=person, value="Смит")

    def test_same_value_for_different_persons(self, person, user):
        other = Person.objects.create(first_name="Пётр", created_by=user)
        PersonName.objects.create(person=person, value="Смит")
        PersonName.objects.create(person=other, value="Смит")

        assert PersonName.objects.count() == 2


class TestEvent:
    def test_year_filled_from_date_from(self, baseball, user):
        event = Event.objects.create(
            sport=baseball,
            name="Первенство",
            date_from="1994-05-01",
            created_by=user,
        )

        assert event.year == 1994

    def test_explicit_year_is_not_overwritten(self, baseball, user):
        event = Event.objects.create(
            sport=baseball,
            name="Первенство",
            date_from="1994-05-01",
            year=1995,
            created_by=user,
        )

        assert event.year == 1995

    def test_date_to_before_date_from_is_invalid(self, baseball, user):
        from django.core.exceptions import ValidationError

        event = Event(
            sport=baseball,
            name="Турнир",
            date_from="1994-05-01",
            date_to="1994-04-01",
            created_by=user,
        )

        with pytest.raises(ValidationError, match="раньше даты начала"):
            event.full_clean()

    def test_slug_unique_within_sport(self, event, baseball, user):
        with pytest.raises(IntegrityError):
            Event.objects.create(
                sport=baseball,
                name="Другое",
                slug=event.slug,
                created_by=user,
            )

    def test_attributes_stored_as_json(self, baseball, user):
        event = Event.objects.create(
            sport=baseball,
            name="Турнир",
            attributes={"score": "5:3", "stage": "финал"},
            created_by=user,
        )

        event.refresh_from_db()
        assert event.attributes["score"] == "5:3"


class TestEventMerge:
    def test_merge_moves_photos_and_event_teams(self, event, team, user):
        from apps.catalog.models import EventTeam
        from apps.photos.models import Photo

        target = Event.objects.create(sport=event.sport, name="Канон", created_by=user)
        event_team = EventTeam.objects.create(event=event, team=team)
        photo = Photo.objects.create(
            sport=event.sport,
            event=event,
            title="Фото",
            original="photos/original/test.jpg",
            uploaded_by=user,
        )

        event.merge_into(target)

        assert EventTeam.objects.get(pk=event_team.pk).event == target
        assert Photo.objects.get(pk=photo.pk).event == target


class TestEventTeam:
    def test_unique_team_per_event(self, event, team):
        from apps.catalog.models import EventTeam

        EventTeam.objects.create(event=event, team=team)

        with pytest.raises(IntegrityError):
            EventTeam.objects.create(event=event, team=team)

    def test_result_note_is_free_text(self, event, team):
        from apps.catalog.models import EventTeam

        event_team = EventTeam.objects.create(
            event=event, team=team, result_note="проиграли в финале"
        )

        assert event_team.result_note == "проиграли в финале"


class TestHistory:
    def test_team_change_is_recorded(self, team, moderator):
        team.publish(moderator=moderator)

        assert team.history.count() >= 2
        assert team.history.first().status == Status.PUBLISHED
