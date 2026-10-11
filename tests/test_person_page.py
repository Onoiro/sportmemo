"""Тесты страницы человека.

Проверяем поведение, а не вёрстку: кто видит страницу, что попадает
в фотографии, команды и варианты имён. Отдельное внимание — приватности:
фамилия несовершеннолетних не должна встречаться в ответе нигде.
"""

from urllib.parse import quote

import pytest
from django.urls import reverse

from apps.catalog.models import (
    NameKind,
    NameVisibility,
    Person,
    PersonName,
    Team,
    TeamMembership,
)
from apps.core.models import Status
from tests.factories import PhotoFactory
from tests.utils import publish_with_attributes

pytestmark = pytest.mark.django_db


def make_person(user, first_name="Иван", last_name="Смирнов", **kwargs):
    """Человек с предсказуемым именем; по умолчанию — опубликован."""
    kwargs.setdefault("status", Status.PUBLISHED)
    return Person.objects.create(
        first_name=first_name,
        last_name=last_name,
        created_by=user,
        **kwargs,
    )


def make_team(baseball, user, name="Соколы", **kwargs):
    kwargs.setdefault("status", Status.PUBLISHED)
    return Team.objects.create(
        sport=baseball,
        name=name,
        created_by=user,
        **kwargs,
    )


@pytest.fixture
def person(user):
    """Опубликованный человек: большинству тестов нужна открытая страница."""
    return make_person(user)


@pytest.fixture
def draft_person(user):
    """Человек-черновик: страницу видят только автор и модератор."""
    return make_person(user, status=Status.DRAFT)


class TestPersonDetailAccess:
    def test_opens_anonymously_for_published(self, client, person):
        response = client.get(person.get_absolute_url())

        assert response.status_code == 200
        assert person.public_name in response.content.decode()

    def test_draft_is_404_for_anonymous(self, client, draft_person):
        assert client.get(draft_person.get_absolute_url()).status_code == 404

    def test_draft_is_404_for_stranger(self, client, draft_person, other_user):
        client.force_login(other_user)

        assert client.get(draft_person.get_absolute_url()).status_code == 404

    def test_draft_is_shown_to_author(self, client, draft_person, user):
        client.force_login(user)

        html = client.get(draft_person.get_absolute_url()).content.decode()

        assert "Не опубликовано" in html

    def test_draft_is_shown_to_moderator(self, client, draft_person, moderator):
        client.force_login(moderator)

        assert client.get(draft_person.get_absolute_url()).status_code == 200

    def test_hidden_is_404_for_anonymous(self, client, person):
        person.hide()

        assert client.get(person.get_absolute_url()).status_code == 404

    def test_deleted_is_404_for_author(self, client, person, user):
        person.soft_delete()
        client.force_login(user)

        assert client.get(person.get_absolute_url()).status_code == 404

    def test_merged_is_404(self, client, user):
        target = make_person(user, "Пётр", "Канонов")
        source = make_person(user, "Пётр", "Дубликатов")
        source.merge_into(target)

        assert client.get(source.get_absolute_url()).status_code == 404

    def test_unknown_slug_is_404(self, client):
        url = reverse("catalog:person_detail", args=["нет-такой"])

        assert client.get(url).status_code == 404

    def test_rejects_post(self, client, person):
        assert client.post(person.get_absolute_url()).status_code == 405

    def test_cyrillic_slug_opens(self, client, user):
        person = make_person(user, "Иван", "Смирнов", slug="иван-смирнов")

        response = client.get(person.get_absolute_url())

        assert response.status_code == 200
        assert person.public_name in response.content.decode()


class TestPersonNamePrivacy:
    """Фамилия несовершеннолетних не публикуется ни в одном месте страницы."""

    def test_full_name_is_shown(self, client, user):
        person = make_person(user, "Алексей", "Громов")

        html = client.get(person.get_absolute_url()).content.decode()

        assert "Громов" in html

    def test_first_name_only_hides_last_name_everywhere(self, client, user):
        person = make_person(
            user,
            "Кирилл",
            "Прохоров",
            name_visibility=NameVisibility.FIRST_NAME_ONLY,
        )

        html = client.get(person.get_absolute_url()).content.decode()

        assert "Кирилл" in html
        assert "Прохоров" not in html

    def test_hidden_name_shows_neutral_label(self, client, user):
        person = make_person(
            user,
            "Станислав",
            "Юдин",
            name_visibility=NameVisibility.HIDDEN,
        )

        html = client.get(person.get_absolute_url()).content.decode()

        assert "Имя не указано" in html
        assert "Станислав" not in html
        assert "Юдин" not in html

    def test_hidden_person_page_still_opens_with_photos(self, client, user):
        """Скрытое имя не скрывает связи: фото и команды на месте."""
        person = make_person(
            user,
            "Станислав",
            "Юдин",
            name_visibility=NameVisibility.HIDDEN,
        )
        photo = PhotoFactory(title="Кадр со скрытым человеком")
        photo.photo_persons.create(person=person)

        response = client.get(person.get_absolute_url())

        assert response.status_code == 200
        assert photo.title in response.content.decode()

    def test_display_name_is_not_leaked(self, client, user):
        """`display_name` содержит полную фамилию: в разметку он не идёт."""
        person = make_person(
            user,
            "Кирилл",
            "Прохоров",
            name_visibility=NameVisibility.FIRST_NAME_ONLY,
        )

        assert person.display_name == "Кирилл Прохоров"

        html = client.get(person.get_absolute_url()).content.decode()

        assert "Прохоров" not in html


class TestPersonNameVariants:
    def test_variants_are_shown_for_full_name(self, client, user):
        person = make_person(user, "Игорь", "Веселов")
        PersonName.objects.create(
            person=person, value="Весельчак", kind=NameKind.NICKNAME
        )

        html = client.get(person.get_absolute_url()).content.decode()

        assert "Весельчак" in html
        assert "прозвище" in html

    @pytest.mark.parametrize(
        "visibility", [NameVisibility.FIRST_NAME_ONLY, NameVisibility.HIDDEN]
    )
    def test_variants_are_hidden_for_narrow_names(self, client, user, visibility):
        person = make_person(user, "Кирилл", "Прохоров", name_visibility=visibility)
        PersonName.objects.create(
            person=person, value="Секретное прозвище", kind=NameKind.NICKNAME
        )

        response = client.get(person.get_absolute_url())
        html = response.content.decode()

        assert response.context["name_variants"] == []
        assert "Секретное прозвище" not in html
        assert "Также известен как" not in html

    def test_no_variants_section_when_empty(self, client, user):
        person = make_person(user, "Игорь", "Веселов")

        html = client.get(person.get_absolute_url()).content.decode()

        assert "Также известен как" not in html


class TestPersonHeroPhoto:
    def test_visible_avatar_is_shown(self, client, user):
        person = make_person(user)
        avatar = PhotoFactory(title="Главное фото человека")
        person.avatar = avatar
        person.save()

        response = client.get(person.get_absolute_url())

        assert response.context["hero_photo"] == avatar

    def test_avatar_without_rights_falls_back_to_first_photo(self, client, user):
        person = make_person(user)
        person.avatar = PhotoFactory(rights_confirmed=False)
        person.save()
        photo = PhotoFactory(title="Первое видимое фото")
        photo.photo_persons.create(person=person)

        response = client.get(person.get_absolute_url())

        assert response.context["hero_photo"] == photo

    def test_draft_avatar_falls_back_to_first_photo(self, client, user):
        person = make_person(user)
        person.avatar = PhotoFactory(status=Status.DRAFT)
        person.save()
        photo = PhotoFactory(title="Видимое фото вместо черновика")
        photo.photo_persons.create(person=person)

        response = client.get(person.get_absolute_url())

        assert response.context["hero_photo"] == photo

    def test_no_photos_gives_placeholder(self, client, user):
        person = make_person(user)

        response = client.get(person.get_absolute_url())
        html = response.content.decode()

        assert response.context["hero_photo"] is None
        assert "person-header__placeholder" in html


class TestPersonPhotos:
    def test_shows_only_visible_photos(self, client, person):
        shown = PhotoFactory(title="Видимое фото человека")
        shown.photo_persons.create(person=person)
        without_rights = PhotoFactory(title="Без прав", rights_confirmed=False)
        without_rights.photo_persons.create(person=person)
        draft = PhotoFactory(title="Черновик фото", status=Status.DRAFT)
        draft.photo_persons.create(person=person)
        deleted = PhotoFactory(title="Удалённое фото человека")
        deleted.photo_persons.create(person=person)
        deleted.soft_delete()
        PhotoFactory(title="Чужое фото")

        html = client.get(person.get_absolute_url()).content.decode()

        assert shown.title in html
        for title in (
            "Без прав",
            "Черновик фото",
            "Удалённое фото человека",
            "Чужое фото",
        ):
            assert title not in html

    def test_gallery_keeps_person_filter_in_more_link(self, client, person):
        for _ in range(25):
            photo = PhotoFactory()
            photo.photo_persons.create(person=person)

        html = client.get(person.get_absolute_url()).content.decode()

        assert f"person={quote(person.slug)}" in html

    def test_no_photo_section_when_no_photos(self, client, person):
        html = client.get(person.get_absolute_url()).content.decode()

        assert "Фотографии" not in html


class TestPersonMemberships:
    def test_shows_team_role_and_years(self, client, person, baseball, user):
        team = make_team(baseball, user, "Соколы")
        TeamMembership.objects.create(
            team=team,
            person=person,
            role="coach",
            year_from=1983,
            year_to=1989,
            position="тренер",
        )

        html = client.get(person.get_absolute_url()).content.decode()

        assert "Соколы" in html
        assert "тренер" in html
        assert "1983" in html
        assert "1989" in html

    def test_published_team_is_linked(self, client, person, baseball, user):
        team = make_team(baseball, user, "Соколы")
        TeamMembership.objects.create(team=team, person=person, year_from=1983)

        html = client.get(person.get_absolute_url()).content.decode()

        assert team.get_absolute_url() in html

    def test_draft_team_is_shown_without_link(self, client, person, baseball, user):
        team = make_team(baseball, user, "Степные орлы", status=Status.DRAFT)
        TeamMembership.objects.create(team=team, person=person, year_from=2005)

        html = client.get(person.get_absolute_url()).content.decode()

        assert "Степные орлы" in html
        assert team.get_absolute_url() not in html

    @pytest.mark.parametrize(
        "kwargs,expected",
        [
            ({"year_from": 1994, "year_to": 1998}, "1994 — 1998"),
            ({"year_from": 1994}, "с 1994"),
            ({"year_to": 1998}, "до 1998"),
            ({}, "год не указан"),
        ],
    )
    def test_year_labels(self, client, person, baseball, user, kwargs, expected):
        team = make_team(baseball, user, "Соколы")
        TeamMembership.objects.create(team=team, person=person, **kwargs)

        html = client.get(person.get_absolute_url()).content.decode()

        assert expected in html

    def test_no_team_section_when_empty(self, client, person):
        html = client.get(person.get_absolute_url()).content.decode()

        assert "Команды и роли" not in html


class TestPersonBioAndAttributes:
    def test_bio_is_shown(self, client, user):
        person = make_person(user, bio="Тренер и играющий капитан «Соколов».")

        html = client.get(person.get_absolute_url()).content.decode()

        assert "Тренер и играющий капитан" in html

    def test_known_attribute_key_is_translated(self, client, user):
        person = publish_with_attributes(make_person(user), {"stage": "финал"})

        response = client.get(person.get_absolute_url())

        assert response.context["attribute_items"] == [
            {"label": "Этап", "value": "финал"}
        ]

    def test_service_key_is_hidden(self, client, user):
        person = publish_with_attributes(
            make_person(user), {"demo": True, "stage": "финал"}
        )

        response = client.get(person.get_absolute_url())
        labels = [item["label"] for item in response.context["attribute_items"]]

        assert labels == ["Этап"]

    def test_no_attributes_section_when_empty(self, client, person):
        html = client.get(person.get_absolute_url()).content.decode()

        assert "Дополнительно" not in html

    def test_no_bio_section_when_empty(self, client, person):
        html = client.get(person.get_absolute_url()).content.decode()

        assert "Биография" not in html


class TestPersonPageLayout:
    def test_breadcrumbs_use_public_name(self, client, user):
        person = make_person(user, "Алексей", "Громов")

        html = client.get(person.get_absolute_url()).content.decode()

        assert "Главная" in html
        assert person.public_name in html

    def test_breadcrumbs_hide_last_name(self, client, user):
        person = make_person(
            user,
            "Кирилл",
            "Прохоров",
            name_visibility=NameVisibility.FIRST_NAME_ONLY,
        )

        html = client.get(person.get_absolute_url()).content.decode()

        assert "Прохоров" not in html

    def test_minimal_person_opens(self, client, user):
        person = make_person(user, "Пустой", "Человек")

        response = client.get(person.get_absolute_url())

        assert response.status_code == 200

    def test_minimal_person_has_no_empty_sections(self, client, user):
        person = make_person(user, "Пустой", "Человек")

        html = client.get(person.get_absolute_url()).content.decode()

        for heading in (
            "Биография",
            "Команды и роли",
            "Фотографии",
            "Также известен как",
            "Дополнительно",
        ):
            assert heading not in html

    @pytest.mark.parametrize(
        "kwargs,expected",
        [
            ({"birth_year": 1955, "death_year": 2019}, "1955 — 2019"),
            ({"birth_year": 1958}, "род. 1958"),
            ({"death_year": 2008}, "ум. 2008"),
        ],
    )
    def test_lifespan_labels(self, client, user, kwargs, expected):
        person = make_person(user, "Юрий", "Савельев", **kwargs)

        html = client.get(person.get_absolute_url()).content.decode()

        assert expected in html

    def test_no_lifespan_without_years(self, client, user):
        person = make_person(user, "Юрий", "Савельев")

        html = client.get(person.get_absolute_url()).content.decode()

        assert "род." not in html
        assert "ум." not in html

    def test_english_locale_uses_english_labels(self, client, user):
        person = make_person(user, "Юрий", "Савельев", birth_year=1958)

        response = client.get(
            person.get_absolute_url(), headers={"accept-language": "en"}
        )
        html = response.content.decode()

        assert "born 1958" in html


class TestIsFullNamePublic:
    @pytest.mark.parametrize(
        "visibility,expected",
        [
            (NameVisibility.FULL, True),
            (NameVisibility.FIRST_NAME_ONLY, False),
            (NameVisibility.HIDDEN, False),
        ],
    )
    def test_matches_name_visibility(self, visibility, expected):
        person = Person(
            first_name="Иван", last_name="Смирнов", name_visibility=visibility
        )

        assert person.is_full_name_public is expected
