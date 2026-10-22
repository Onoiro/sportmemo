"""Тесты публичных списков: команды, события, годы.

Проверяем отсев непубличного, пагинацию и пустые состояния. Вёрстку
не проверяем: разметка меняется чаще, чем смысл страницы.
"""

import pytest
from django.urls import reverse

from apps.catalog.models import Event, Team
from apps.core.models import Status
from apps.core.views import YEAR_PHOTO_LIMIT
from tests.factories import EventFactory, PhotoFactory, TeamFactory

pytestmark = pytest.mark.django_db

TEAM_PAGE_SIZE = 24


def publish_team(sport, user, name, **kwargs):
    team = Team.objects.create(
        sport=sport, name=name, created_by=user, status=Status.PUBLISHED, **kwargs
    )
    return team


class TestTeamList:
    def test_opens_anonymously(self, client, team):
        team.publish()

        response = client.get(reverse("catalog:team_list"))

        assert response.status_code == 200
        assert "Команды" in response.content.decode()

    def test_shows_published_team(self, client, team):
        team.publish()

        html = client.get(reverse("catalog:team_list")).content.decode()

        assert team.name in html

    def test_hides_draft_hidden_rejected_and_deleted(self, client, baseball, user):
        shown = publish_team(baseball, user, "Видимая")
        draft = TeamFactory(sport=baseball, created_by=user, name="Черновик")
        hidden = TeamFactory(
            sport=baseball, created_by=user, name="Скрытая", status=Status.HIDDEN
        )
        rejected = TeamFactory(
            sport=baseball, created_by=user, name="Отклонённая", status=Status.REJECTED
        )
        deleted = publish_team(baseball, user, "Удалённая")
        deleted.soft_delete()

        html = client.get(reverse("catalog:team_list")).content.decode()

        assert shown.name in html
        for name in (draft.name, hidden.name, rejected.name, deleted.name):
            assert name not in html

    def test_merged_team_is_not_shown(self, client, baseball, user):
        target = publish_team(baseball, user, "Канон")
        source = publish_team(baseball, user, "Дубликат")
        source.merge_into(target)

        html = client.get(reverse("catalog:team_list")).content.decode()

        assert "Канон" in html
        assert "Дубликат" not in html

    def test_sorted_by_name(self, client, baseball, user):
        publish_team(baseball, user, "Ястребы")
        publish_team(baseball, user, "Акулы")

        response = client.get(reverse("catalog:team_list"))
        names = [team.name for team in response.context["page_obj"].object_list]

        assert names == ["Акулы", "Ястребы"]

    def test_photos_count_ignores_drafts(self, client, team, user):
        team.publish()
        PhotoFactory(team=team, status=Status.PUBLISHED)
        PhotoFactory(team=team, status=Status.DRAFT)

        response = client.get(reverse("catalog:team_list"))
        listed = response.context["page_obj"].object_list[0]

        assert listed.photos_total == 1

    def test_photos_count_includes_photo_without_rights(self, client, team, user):
        """Права проверяет автор: счётчик их не учитывает."""
        team.publish()
        PhotoFactory(team=team, status=Status.PUBLISHED)
        PhotoFactory(team=team, status=Status.PUBLISHED, rights_confirmed=False)

        response = client.get(reverse("catalog:team_list"))
        listed = response.context["page_obj"].object_list[0]

        assert listed.photos_total == 2

    def test_pagination_splits_long_list(self, client, baseball, user):
        for index in range(TEAM_PAGE_SIZE + 1):
            publish_team(baseball, user, f"Команда {index:02d}")

        first = client.get(reverse("catalog:team_list"))
        second = client.get(reverse("catalog:team_list"), {"page": 2})

        assert first.context["page_obj"].paginator.num_pages == 2
        assert len(first.context["page_obj"].object_list) == TEAM_PAGE_SIZE
        assert len(second.context["page_obj"].object_list) == 1

    def test_pagination_keeps_other_query_params(self, client, baseball, user):
        for index in range(TEAM_PAGE_SIZE + 1):
            publish_team(baseball, user, f"Команда {index:02d}")

        response = client.get(reverse("catalog:team_list"), {"sort": "name"})
        html = response.content.decode()

        assert "page=2" in html
        assert "sort=name" in html

    @pytest.mark.parametrize("value", ["abc", "999", "-1", ""])
    def test_broken_page_number_does_not_crash(self, client, team, value):
        team.publish()

        response = client.get(reverse("catalog:team_list"), {"page": value})

        assert response.status_code == 200

    def test_empty_state(self, client):
        html = client.get(reverse("catalog:team_list")).content.decode()

        assert "В архиве пока нет команд" in html

    def test_no_pagination_block_on_single_page(self, client, team):
        team.publish()

        html = client.get(reverse("catalog:team_list")).content.decode()

        assert "Постраничная навигация" not in html


class TestEventList:
    def test_opens_anonymously(self, client, event):
        event.publish()

        response = client.get(reverse("catalog:event_list"))

        assert response.status_code == 200

    def test_shows_published_event(self, client, event):
        event.publish()

        html = client.get(reverse("catalog:event_list")).content.decode()

        assert event.name in html

    def test_hides_unpublished_events(self, client, baseball, user):
        draft = EventFactory(sport=baseball, created_by=user, name="Черновик")
        hidden = EventFactory(
            sport=baseball, created_by=user, name="Скрытое", status=Status.HIDDEN
        )
        deleted = EventFactory(
            sport=baseball, created_by=user, name="Удалённое", status=Status.PUBLISHED
        )
        deleted.soft_delete()

        html = client.get(reverse("catalog:event_list")).content.decode()

        for name in (draft.name, hidden.name, deleted.name):
            assert name not in html

    def test_sorted_from_new_to_old(self, client, baseball, user):
        for year in (1985, 2003, 1994):
            Event.objects.create(
                sport=baseball,
                name=f"Турнир {year}",
                year=year,
                created_by=user,
                status=Status.PUBLISHED,
            )

        response = client.get(reverse("catalog:event_list"))
        years = [event.year for event in response.context["page_obj"].object_list]

        assert years == [2003, 1994, 1985]

    def test_event_without_year_goes_last(self, client, baseball, user):
        Event.objects.create(
            sport=baseball,
            name="С годом",
            year=1994,
            created_by=user,
            status=Status.PUBLISHED,
        )
        Event.objects.create(
            sport=baseball,
            name="Без года",
            year=None,
            created_by=user,
            status=Status.PUBLISHED,
        )

        response = client.get(reverse("catalog:event_list"))
        names = [event.name for event in response.context["page_obj"].object_list]

        assert names == ["С годом", "Без года"]

    def test_empty_state(self, client):
        html = client.get(reverse("catalog:event_list")).content.decode()

        assert "В архиве пока нет событий" in html


class TestYearList:
    def test_opens_anonymously(self, client, photo):
        response = client.get(reverse("core:year_list"))

        assert response.status_code == 200

    def test_shows_year_and_photo_count(self, client):
        PhotoFactory(year=1994)
        PhotoFactory(year=1994)
        PhotoFactory(year=2003)

        response = client.get(reverse("core:year_list"))
        years = {item["year"]: item["total"] for item in response.context["years"]}

        assert years == {1994: 2, 2003: 1}

    def test_sorted_from_new_to_old(self, client):
        PhotoFactory(year=1985)
        PhotoFactory(year=2003)
        PhotoFactory(year=1994)

        response = client.get(reverse("core:year_list"))
        years = [item["year"] for item in response.context["years"]]

        assert years == [2003, 1994, 1985]

    def test_drafts_do_not_create_year(self, client):
        PhotoFactory(year=1994, status=Status.DRAFT)
        PhotoFactory(year=1994)

        response = client.get(reverse("core:year_list"))
        years = [item["year"] for item in response.context["years"]]

        assert years == [1994]

    def test_photo_without_rights_creates_year(self, client):
        """Права проверяет автор: год с одним таким фото — нормальный."""
        PhotoFactory(year=1994, rights_confirmed=False)

        response = client.get(reverse("core:year_list"))
        years = [item["year"] for item in response.context["years"]]

        assert years == [1994]

    def test_photo_without_year_is_not_listed(self, client):
        PhotoFactory(year=None)

        response = client.get(reverse("core:year_list"))
        years = [item["year"] for item in response.context["years"]]

        assert years == []

    def test_empty_state(self, client):
        html = client.get(reverse("core:year_list")).content.decode()

        assert "В архиве пока нет фотографий" in html

    def test_links_to_year_detail(self, client):
        PhotoFactory(year=1994)

        html = client.get(reverse("core:year_list")).content.decode()

        assert reverse("core:year_detail", args=[1994]) in html


class TestYearDetail:
    def test_opens_anonymously(self, client):
        PhotoFactory(year=1994)

        response = client.get(reverse("core:year_detail", args=[1994]))

        assert response.status_code == 200
        assert "1994 год" in response.content.decode()

    def test_shows_only_photos_of_the_year(self, client):
        shown = PhotoFactory(year=1994, title="Фото 1994")
        other = PhotoFactory(year=2003, title="Фото 2003")

        response = client.get(reverse("core:year_detail", args=[1994]))
        photos = list(response.context["page_obj"].object_list)

        assert shown in photos
        assert other not in photos

    def test_hides_unpublished_photos(self, client):
        shown = PhotoFactory(year=1994)
        PhotoFactory(year=1994, status=Status.DRAFT)
        deleted = PhotoFactory(year=1994)
        deleted.soft_delete()

        response = client.get(reverse("core:year_detail", args=[1994]))
        photos = list(response.context["page_obj"].object_list)

        assert photos == [shown]

    def test_shows_photo_without_rights(self, client):
        """Права проверяет автор: исторический флаг видимость не меняет."""
        photo = PhotoFactory(year=1994, rights_confirmed=False)

        response = client.get(reverse("core:year_detail", args=[1994]))

        assert list(response.context["page_obj"].object_list) == [photo]

    def test_year_without_visible_photos_is_404(self, client):
        PhotoFactory(year=1994, status=Status.DRAFT)

        response = client.get(reverse("core:year_detail", args=[1994]))

        assert response.status_code == 404

    def test_unknown_year_is_404(self, client):
        assert client.get(reverse("core:year_detail", args=[1800])).status_code == 404

    def test_pagination(self, client):
        for _ in range(YEAR_PHOTO_LIMIT + 1):
            PhotoFactory(year=1994)

        first = client.get(reverse("core:year_detail", args=[1994]))
        second = client.get(reverse("core:year_detail", args=[1994]), {"page": 2})

        assert first.context["page_obj"].paginator.num_pages == 2
        assert len(first.context["page_obj"].object_list) == YEAR_PHOTO_LIMIT
        assert len(second.context["page_obj"].object_list) == 1

    def test_breadcrumbs(self, client):
        PhotoFactory(year=1994)

        html = client.get(reverse("core:year_detail", args=[1994])).content.decode()

        assert "Главная" in html
        assert "Годы" in html
        assert "1994 год" in html

    def test_empty_year_shows_404_not_empty_page(self, client):
        response = client.get(reverse("core:year_detail", args=[1994]))

        assert response.status_code == 404


class TestNavbarLinksStillWork:
    @pytest.mark.parametrize(
        "name",
        ["catalog:team_list", "catalog:event_list", "core:year_list"],
    )
    def test_reverse_names_unchanged(self, client, name):
        assert client.get(reverse(name)).status_code == 200


class TestEventPagesAreReal:
    """Страницы-заглушки заменены настоящими представлениями."""

    def test_team_list_is_not_a_placeholder(self, client):
        html = client.get(reverse("catalog:team_list")).content.decode()

        assert "Раздел в разработке" not in html

    def test_event_list_is_not_a_placeholder(self, client):
        html = client.get(reverse("catalog:event_list")).content.decode()

        assert "Раздел в разработке" not in html

    def test_year_list_is_not_a_placeholder(self, client):
        html = client.get(reverse("core:year_list")).content.decode()

        assert "Раздел в разработке" not in html

    def test_search_is_not_a_placeholder(self, client):
        html = client.get(reverse("search:index")).content.decode()

        assert "Раздел в разработке" not in html
