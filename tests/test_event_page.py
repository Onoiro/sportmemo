"""Тесты страницы события и подписи дат `Event.date_label`.

Проверяем поведение, а не вёрстку: кто видит страницу, какие команды
и фотографии на неё попадают, как форматируются даты.
"""

from datetime import date
from urllib.parse import quote

import pytest
from django.urls import reverse
from django.utils import translation

from apps.catalog.models import Event, EventTeam, EventType
from apps.core.models import Status
from tests.factories import PhotoFactory
from tests.utils import publish_with_attributes

pytestmark = pytest.mark.django_db


def make_event(baseball, user, name="Чемпионат России", **kwargs):
    return Event.objects.create(
        sport=baseball,
        name=name,
        created_by=user,
        status=Status.PUBLISHED,
        **kwargs,
    )


class TestDateLabel:
    """Подпись дат: только месяцы и годы, без чисел дня."""

    def test_same_month_and_year(self):
        event = Event(date_from=date(1980, 7, 12), date_to=date(1980, 7, 20))

        assert event.date_label == "Июль 1980"

    def test_different_months_of_one_year(self):
        event = Event(date_from=date(1988, 6, 4), date_to=date(1988, 7, 12))

        assert event.date_label.replace("\u00a0", " ") == "Июнь — Июль 1988"

    def test_different_years(self):
        event = Event(date_from=date(1988, 7, 4), date_to=date(1989, 8, 12))

        assert event.date_label.replace("\u00a0", " ") == "Июль 1988 — Август 1989"

    def test_only_date_from(self):
        event = Event(date_from=date(1980, 7, 12))

        assert event.date_label == "Июль 1980"

    def test_only_year(self):
        event = Event(year=1980)

        assert event.date_label == "1980"

    def test_no_dates_at_all(self):
        event = Event()

        assert event.date_label == "дата не указана"

    def test_only_date_to(self):
        event = Event(date_to=date(1980, 7, 12))

        assert event.date_label == "Июль 1980"

    def test_dates_as_strings(self):
        """Даты из data-миграций приходят строками."""
        event = Event(date_from="1980-07-12", date_to="1980-07-20")

        assert event.date_label == "Июль 1980"

    def test_english_locale(self):
        event = Event(date_from=date(1980, 7, 12), date_to=date(1980, 7, 20))

        with translation.override("en"):
            assert event.date_label == "July 1980"

    def test_english_locale_for_range(self):
        event = Event(date_from=date(1988, 6, 4), date_to=date(1988, 7, 12))

        with translation.override("en"):
            assert event.date_label.replace("\u00a0", " ") == "June — July 1988"


class TestEventDetailAccess:
    def test_opens_anonymously_for_published(self, client, event):
        event.publish()

        response = client.get(event.get_absolute_url())

        assert response.status_code == 200
        assert event.name in response.content.decode()

    def test_draft_is_404_for_anonymous(self, client, event):
        assert client.get(event.get_absolute_url()).status_code == 404

    def test_draft_is_404_for_stranger(self, client, event, other_user):
        client.force_login(other_user)

        assert client.get(event.get_absolute_url()).status_code == 404

    def test_draft_is_shown_to_author(self, client, event, user):
        client.force_login(user)

        assert client.get(event.get_absolute_url()).status_code == 200

    def test_draft_is_shown_to_moderator(self, client, event, moderator):
        client.force_login(moderator)

        assert client.get(event.get_absolute_url()).status_code == 200

    def test_hidden_is_404_for_anonymous(self, client, event):
        event.status = Status.HIDDEN
        event.save()

        assert client.get(event.get_absolute_url()).status_code == 404

    def test_deleted_is_404_for_author(self, client, event, user):
        event.publish()
        event.soft_delete()
        client.force_login(user)

        assert client.get(event.get_absolute_url()).status_code == 404

    def test_merged_is_404(self, client, baseball, user):
        target = make_event(baseball, user, "Канон")
        source = make_event(baseball, user, "Дубликат")
        source.merge_into(target)

        assert client.get(source.get_absolute_url()).status_code == 404

    def test_unknown_slug_is_404(self, client):
        url = reverse("catalog:event_detail", args=["нет-такого"])

        assert client.get(url).status_code == 404

    def test_rejects_post(self, client, event):
        event.publish()

        assert client.post(event.get_absolute_url()).status_code == 405

    def test_cyrillic_slug_opens(self, client, baseball, user):
        event = make_event(baseball, user, "Чемпионат", slug="чемпионат")

        response = client.get(event.get_absolute_url())

        assert response.status_code == 200
        assert event.name in response.content.decode()

    def test_event_without_year_and_dates_opens(self, client, baseball, user):
        event = make_event(baseball, user, "Без даты")

        response = client.get(event.get_absolute_url())

        assert response.status_code == 200
        assert "дата не указана" in response.content.decode()


class TestEventModerationNotice:
    def test_notice_is_shown_to_author_of_draft(self, client, event, user):
        client.force_login(user)

        html = client.get(event.get_absolute_url()).content.decode()

        assert "Не опубликовано" in html
        assert "Видно только вам и модератору." in html

    def test_notice_is_not_shown_for_published(self, client, event):
        event.publish()

        html = client.get(event.get_absolute_url()).content.decode()

        assert "Не опубликовано" not in html


class TestEventParticipants:
    def test_shows_participating_teams(self, client, event, team):
        event.publish()
        team.publish()
        EventTeam.objects.create(event=event, team=team, result_note="1 место")

        html = client.get(event.get_absolute_url()).content.decode()

        assert team.name in html
        assert "1 место" in html

    def test_published_team_is_a_link(self, client, event, team):
        event.publish()
        team.publish()
        EventTeam.objects.create(event=event, team=team)

        html = client.get(event.get_absolute_url()).content.decode()

        assert team.get_absolute_url() in html

    def test_draft_team_is_not_a_link(self, client, event, team):
        event.publish()
        EventTeam.objects.create(event=event, team=team)

        html = client.get(event.get_absolute_url()).content.decode()

        assert team.name in html
        assert f'href="/teams/{team.slug}/"' not in html

    def test_no_participants_section_when_empty(self, client, event):
        event.publish()

        html = client.get(event.get_absolute_url()).content.decode()

        assert "Команды-участники" not in html


class TestEventPhotos:
    def test_shows_only_visible_photos(self, client, event):
        event.publish()
        shown = PhotoFactory(event=event, title="Видимое фото события")
        PhotoFactory(event=event, title="Черновик фото", status=Status.DRAFT)
        deleted = PhotoFactory(event=event, title="Удалённое фото события")
        deleted.soft_delete()
        PhotoFactory(title="Чужое фото")

        html = client.get(event.get_absolute_url()).content.decode()

        assert shown.title in html
        for title in (
            "Черновик фото",
            "Удалённое фото события",
            "Чужое фото",
        ):
            assert title not in html

    def test_photo_without_rights_is_shown(self, client, event):
        """Права проверяет автор: исторический флаг видимость не меняет."""
        event.publish()
        photo = PhotoFactory(event=event, title="Без прав", rights_confirmed=False)

        html = client.get(event.get_absolute_url()).content.decode()

        assert photo.title in html

    def test_gallery_keeps_event_filter_in_more_link(self, client, event):
        event.publish()
        for _ in range(25):
            PhotoFactory(event=event)

        html = client.get(event.get_absolute_url()).content.decode()

        assert f"event={quote(event.slug)}" in html

    def test_no_photo_section_when_no_photos(self, client, event):
        event.publish()

        html = client.get(event.get_absolute_url()).content.decode()

        assert "Фотографии события" not in html


class TestEventAttributes:
    def test_known_keys_are_translated(self, client, event):
        publish_with_attributes(
            event, {"stage": "финал", "result": "1 место", "teams": 6}
        )

        response = client.get(event.get_absolute_url())
        items = {
            item["label"]: item["value"] for item in response.context["attribute_items"]
        }

        assert items == {"Этап": "финал", "Результат": "1 место", "Команд": 6}

    def test_service_key_is_hidden(self, client, event):
        publish_with_attributes(event, {"demo": True})

        response = client.get(event.get_absolute_url())

        assert response.context["attribute_items"] == []

    def test_unknown_key_is_shown_as_is(self, client, event):
        publish_with_attributes(event, {"custom": "значение"})

        response = client.get(event.get_absolute_url())

        assert response.context["attribute_items"] == [
            {"label": "custom", "value": "значение"}
        ]

    def test_no_attributes_section_when_empty(self, client, event):
        event.publish()

        html = client.get(event.get_absolute_url()).content.decode()

        assert "Итоги" not in html


class TestEventPageLayout:
    def test_breadcrumbs(self, client, event):
        event.publish()

        html = client.get(event.get_absolute_url()).content.decode()

        assert "Главная" in html
        assert reverse("catalog:event_list") in html

    def test_shows_type_dates_and_place(self, client, baseball, user):
        event = make_event(
            baseball,
            user,
            "Чемпионат России",
            event_type=EventType.TOURNAMENT,
            date_from=date(1994, 5, 1),
            date_to=date(1994, 5, 9),
            city="Екатеринбург",
            country="Россия",
        )

        html = client.get(event.get_absolute_url()).content.decode()

        assert "турнир" in html
        assert "Май 1994" in html
        assert "Екатеринбург" in html

    def test_minimal_event_has_no_empty_sections(self, client, baseball, user):
        event = make_event(baseball, user, "Пустое событие")

        html = client.get(event.get_absolute_url()).content.decode()

        for heading in ("Итоги", "Команды-участники", "Фотографии события"):
            assert heading not in html


class TestTeamListLinksToDetail:
    """Проверка карточек в списках — часть долга 2.3, закрытого в 2.5."""

    def test_team_card_links_to_team_page(self, client, team):
        team.publish()

        html = client.get(reverse("catalog:team_list")).content.decode()

        assert team.get_absolute_url() in html


class TestEventListLinksToDetail:
    def test_event_card_links_to_event_page(self, client, event):
        event.publish()

        html = client.get(reverse("catalog:event_list")).content.decode()

        assert event.get_absolute_url() in html


class TestPhotoDetailLinks:
    def test_links_to_published_team_and_event(self, client, photo, team, event):
        team.publish()
        event.publish()
        photo.team = team
        photo.event = event
        photo.save()

        html = client.get(photo.get_absolute_url()).content.decode()

        assert team.get_absolute_url() in html
        assert event.get_absolute_url() in html

    def test_draft_team_and_event_are_not_links(self, client, photo, team, event):
        photo.team = team
        photo.event = event
        photo.save()

        html = client.get(photo.get_absolute_url()).content.decode()

        assert team.name in html
        assert event.name in html
        assert f'href="/teams/{team.slug}/"' not in html
        assert f'href="/events/{event.slug}/"' not in html

    def test_draft_photo_shows_moderation_notice_to_author(self, client, photo, user):
        photo.status = Status.DRAFT
        photo.save()
        client.force_login(user)

        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Не опубликовано" in html

    def test_published_photo_has_no_moderation_notice(self, client, photo):
        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Не опубликовано" not in html


class TestYearDetailEvents:
    def test_shows_published_events_of_the_year(self, client, baseball, user):
        PhotoFactory(year=1994)
        shown = make_event(baseball, user, "Чемпионат 1994", year=1994)
        draft = Event.objects.create(
            sport=baseball,
            name="Черновик 1994",
            year=1994,
            created_by=user,
        )
        make_event(baseball, user, "Чемпионат 2003", year=2003)

        html = client.get(reverse("core:year_detail", args=[1994])).content.decode()

        assert shown.name in html
        assert draft.name not in html
        assert "Чемпионат 2003" not in html

    def test_no_event_block_when_no_events(self, client):
        PhotoFactory(year=1994)

        html = client.get(reverse("core:year_detail", args=[1994])).content.decode()

        assert "События года" not in html

    def test_event_block_has_no_pagination(self, client, baseball, user):
        PhotoFactory(year=1994)
        for index in range(3):
            make_event(baseball, user, f"Событие {index}", year=1994)

        html = client.get(reverse("core:year_detail", args=[1994])).content.decode()

        assert "Постраничная навигация" not in html

    def test_event_card_links_to_event_page(self, client, baseball, user):
        PhotoFactory(year=1994)
        event = make_event(baseball, user, "Чемпионат 1994", year=1994)

        html = client.get(reverse("core:year_detail", args=[1994])).content.decode()

        assert event.get_absolute_url() in html
