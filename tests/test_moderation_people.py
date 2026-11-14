"""Тесты очереди черновиков людей: отбор, карточки и публикация с фото.

Люди стоят отдельно от общей очереди: у `Person` нет вида спорта, и общий
`pending_queryset()` падал бы на нём `FieldError`. Поэтому проверяется
именно это правило: список и действие считают «ждёт решения» одной
выборкой, своего человека не решает никто, а карточка не раскрывает
скрытую фамилию.

Второй риск — побочный эффект публикации снимка: модератор публикует фото
вместе с людьми, и каждый человек обязан получить запись журнала. Иначе
инвариант «всё значимое логируется» держался бы на молчаливой записи
статуса.
"""

from urllib.parse import quote

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.catalog.models import NameVisibility
from apps.core.models import Status
from apps.moderation import people
from apps.moderation.forms import QueueRejectForm
from apps.moderation.models import ModerationAction, ModerationActionType
from tests.factories import (
    PersonFactory,
    PhotoFactory,
    PhotoPersonFactory,
    TeamFactory,
    TeamMembershipFactory,
)

pytestmark = pytest.mark.django_db

PEOPLE_URL = reverse("moderation:people")


def decide_url(pk):
    return reverse("moderation:person_decide", args=[pk])


def page_cards(user, page=""):
    """Карточки страницы очереди людей без обращения к представлению."""
    request = RequestFactory().get(f"/moderation/people/{page}")
    return people.people_page(user, request)[1]


def card_pks(user):
    return [card.pk for card in page_cards(user)]


def actions_for(person):
    return ModerationAction.objects.filter(
        content_type=ContentType.objects.get_for_model(person),
        object_id=person.pk,
    )


class TestComposition:
    """В список попадает только то, что ждёт решения."""

    def test_draft_person_is_listed(self, moderator):
        person = PersonFactory()

        assert person.pk in card_pks(moderator)

    def test_published_person_is_hidden(self, moderator):
        person = PersonFactory(status=Status.PUBLISHED)

        assert person.pk not in card_pks(moderator)

    def test_deleted_person_is_hidden(self, moderator):
        person = PersonFactory()
        person.soft_delete()

        assert person.pk not in card_pks(moderator)

    def test_merged_person_is_hidden(self, moderator):
        canonical = PersonFactory(status=Status.PUBLISHED)
        merged = PersonFactory()
        merged.merged_into = canonical
        merged.save()
        merged.soft_delete()

        assert merged.pk not in card_pks(moderator)

    def test_oldest_draft_goes_first(self, moderator):
        older = PersonFactory()
        newer = PersonFactory()

        pks = card_pks(moderator)

        assert pks.index(older.pk) < pks.index(newer.pk)

    def test_is_pending_person_matches_the_list(self, moderator):
        draft = PersonFactory()
        published = PersonFactory(status=Status.PUBLISHED)

        assert people.is_pending_person(draft) is True
        assert people.is_pending_person(published) is False


class TestOwnPerson:
    """Своего человека решает другой модератор."""

    def test_own_person_is_not_listed(self, moderator):
        own = PersonFactory(created_by=moderator)

        assert own.pk not in card_pks(moderator)

    def test_other_person_is_listed(self, moderator, other_user):
        foreign = PersonFactory(created_by=other_user)

        assert foreign.pk in card_pks(moderator)

    def test_person_without_author_is_listed(self, moderator):
        """`exclude(created_by=user)` выбросил бы и людей без автора."""
        orphan = PersonFactory(created_by=None)

        assert orphan.pk in card_pks(moderator)

    def test_is_own_person_is_false_without_user(self, moderator):
        person = PersonFactory(created_by=moderator)

        assert people.is_own_person(person, None) is False

    def test_is_own_person_is_false_for_person_without_author(self, moderator):
        person = PersonFactory(created_by=None)

        assert people.is_own_person(person, moderator) is False


class TestModeratorScope:
    """Области у человека нет: очередь общая для всех модераторов."""

    def test_moderator_without_sport_scope_sees_the_queue(self, moderator):
        moderator.moderated_sports.clear()
        person = PersonFactory()

        assert person.pk in card_pks(moderator)


class TestCard:
    """Карточка печатает готовые поля и не раскрывает скрытое имя."""

    def test_card_without_photo_has_no_link(self, moderator):
        PersonFactory()

        card = page_cards(moderator)[0]

        assert card.photo_url is None
        assert card.photo_note

    def test_deleted_photo_gives_a_note_instead_of_a_link(self, moderator):
        photo = PhotoFactory()
        photo.soft_delete()
        PhotoPersonFactory(photo=photo, person=PersonFactory())

        card = page_cards(moderator)[0]

        assert card.photo_url is None
        assert card.photo_note

    def test_live_photo_gives_a_link(self, moderator):
        photo = PhotoFactory()
        PhotoPersonFactory(photo=photo, person=PersonFactory())

        card = page_cards(moderator)[0]

        assert card.photo_url == photo.get_absolute_url()
        assert card.photo_title == photo.title

    def test_card_prints_public_name_not_display_name(self, moderator):
        person = PersonFactory(
            first_name="Алексей",
            last_name="Петров",
            name_visibility=NameVisibility.FIRST_NAME_ONLY,
        )

        card = page_cards(moderator)[0]

        assert card.name == "Алексей"
        assert "Петров" not in card.name
        assert person.display_name != card.name

    def test_card_shows_visibility_label(self, moderator):
        PersonFactory(name_visibility=NameVisibility.HIDDEN)

        card = page_cards(moderator)[0]

        assert card.visibility_label

    def test_card_shows_year_and_team(self, moderator):
        team = TeamFactory(name="Русстар")
        person = PersonFactory(birth_year=None)
        TeamMembershipFactory(person=person, team=team, year_from=1994, year_to=1998)

        card = page_cards(moderator)[0]

        assert card.year == 1998
        assert card.team == "Русстар"

    def test_card_falls_back_to_birth_year(self, moderator):
        team = TeamFactory()
        person = PersonFactory(birth_year=1971)
        TeamMembershipFactory(person=person, team=team, year_from=None, year_to=None)

        card = page_cards(moderator)[0]

        assert card.year == 1971

    def test_card_without_author_says_so(self, moderator):
        PersonFactory(created_by=None)

        card = page_cards(moderator)[0]

        assert card.author


class TestQueryCount:
    """Число запросов не растёт вместе с порцией."""

    def test_query_count_does_not_grow_with_the_page(self, moderator):
        PhotoPersonFactory(photo=PhotoFactory(), person=PersonFactory())
        request = RequestFactory().get("/moderation/people/")
        with CaptureQueriesContext(connection) as one:
            people.people_page(moderator, request)

        for _ in range(4):
            PhotoPersonFactory(photo=PhotoFactory(), person=PersonFactory())

        with CaptureQueriesContext(connection) as five:
            cards = people.people_page(moderator, request)[1]

        assert len(cards) == 5
        assert len(five) == len(one)


class TestPublishForPhoto:
    """Публикация людей вместе со снимком: чужих публикуем, своих нет."""

    def test_publishes_other_people_and_writes_the_log(self, moderator, other_user):
        photo = PhotoFactory()
        first = PersonFactory(created_by=other_user)
        second = PersonFactory(created_by=other_user)
        PhotoPersonFactory(photo=photo, person=first)
        PhotoPersonFactory(photo=photo, person=second)

        published, skipped = people.publish_people_for_photo(photo, moderator)

        assert (published, skipped) == (2, 0)
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.status == Status.PUBLISHED
        assert first.moderated_by == moderator
        assert (
            actions_for(first).filter(action=ModerationActionType.PUBLISH).count() == 1
        )
        assert (
            actions_for(second).filter(action=ModerationActionType.PUBLISH).count() == 1
        )

    def test_skips_own_person(self, moderator, other_user):
        photo = PhotoFactory(uploaded_by=other_user)
        own = PersonFactory(created_by=moderator)
        foreign = PersonFactory(created_by=other_user)
        PhotoPersonFactory(photo=photo, person=own)
        PhotoPersonFactory(photo=photo, person=foreign)

        published, skipped = people.publish_people_for_photo(photo, moderator)

        assert (published, skipped) == (1, 1)
        own.refresh_from_db()
        assert own.status == Status.DRAFT
        assert not actions_for(own).exists()

    def test_does_not_touch_people_of_other_photos(self, moderator, other_user):
        photo = PhotoFactory()
        elsewhere = PersonFactory(created_by=other_user)
        PhotoPersonFactory(photo=PhotoFactory(), person=elsewhere)

        published, skipped = people.publish_people_for_photo(photo, moderator)

        assert (published, skipped) == (0, 0)
        elsewhere.refresh_from_db()
        assert elsewhere.status == Status.DRAFT

    def test_ignores_published_people(self, moderator, other_user):
        photo = PhotoFactory()
        PhotoPersonFactory(
            photo=photo,
            person=PersonFactory(created_by=other_user, status=Status.PUBLISHED),
        )

        published, skipped = people.publish_people_for_photo(photo, moderator)

        assert (published, skipped) == (0, 0)


class TestDraftCounts:
    """Счётчик черновиков на карточке фото — один запрос на страницу."""

    def test_counts_only_drafts(self, moderator):
        photo = PhotoFactory()
        PhotoPersonFactory(photo=photo, person=PersonFactory())
        PhotoPersonFactory(photo=photo, person=PersonFactory(status=Status.PUBLISHED))

        assert people.draft_people_counts([photo.pk]) == {photo.pk: 1}

    def test_empty_list_does_not_query(self):
        with CaptureQueriesContext(connection) as queries:
            assert people.draft_people_counts([]) == {}

        assert len(queries) == 0

    def test_photo_without_drafts_is_absent(self, moderator):
        photo = PhotoFactory()

        assert people.draft_people_counts([photo.pk]) == {}


class TestPageAccess:
    """Страница очереди людей: вход, роль и метатеги."""

    def test_anonymous_is_redirected_to_login(self, client):
        response = client.get(PEOPLE_URL)

        assert response.status_code == 302
        assert reverse("account_login") in response["Location"]

    def test_plain_user_gets_403(self, client, user):
        client.force_login(user)

        assert client.get(PEOPLE_URL).status_code == 403

    def test_moderator_opens_page(self, client, moderator):
        person = PersonFactory()
        client.force_login(moderator)

        response = client.get(PEOPLE_URL)

        assert response.status_code == 200
        assert person.pk in [card.pk for card in response.context["cards"]]

    def test_page_is_closed_from_indexing(self, client, moderator):
        client.force_login(moderator)

        html = client.get(PEOPLE_URL).content.decode()

        assert "noindex" in html
        assert "nofollow" in html

    def test_moderator_without_scope_sees_the_page(self, client, moderator):
        """Области у человека нет: модератор без видов спорта видит очередь."""
        moderator.moderated_sports.clear()
        person = PersonFactory()
        client.force_login(moderator)

        response = client.get(PEOPLE_URL)

        assert response.status_code == 200
        assert person.pk in [card.pk for card in response.context["cards"]]


class TestDecidePublish:
    """Публикация: статус, автор решения и одна запись в журнале."""

    def test_publish_publishes_and_logs(self, client, moderator, other_user):
        person = PersonFactory(created_by=other_user)
        client.force_login(moderator)

        response = client.post(
            decide_url(person.pk), {"action": "publish"}, follow=True
        )

        person.refresh_from_db()
        assert response.status_code == 200
        assert person.status == Status.PUBLISHED
        assert person.moderated_by_id == moderator.pk
        assert list(actions_for(person).values_list("action", flat=True)) == [
            ModerationActionType.PUBLISH
        ]
        assert "Человек опубликован." in response.content.decode()


class TestDecideReject:
    """Отклонение: причина доходит до автора, журнала и истории."""

    def test_reject_keeps_reason(self, client, moderator, other_user):
        person = PersonFactory(created_by=other_user)
        client.force_login(moderator)

        response = client.post(
            decide_url(person.pk),
            {"action": "reject", "reason": "  не тот человек  "},
            follow=True,
        )

        person.refresh_from_db()
        assert response.status_code == 200
        assert person.status == Status.REJECTED
        # Причина обрезается формой: автор не должен читать лишние пробелы.
        assert person.moderation_reason == "не тот человек"
        action = actions_for(person).get()
        assert action.action == ModerationActionType.REJECT
        assert action.reason == "не тот человек"
        assert person.history.first().status == Status.REJECTED

    @pytest.mark.parametrize("reason", ["", "   "])
    def test_empty_reason_changes_nothing(self, client, moderator, other_user, reason):
        person = PersonFactory(created_by=other_user)
        client.force_login(moderator)

        response = client.post(
            decide_url(person.pk), {"action": "reject", "reason": reason}
        )

        person.refresh_from_db()
        assert response.status_code == 302
        assert person.status == Status.DRAFT
        assert not actions_for(person).exists()

    def test_empty_reason_shows_form_error(self, client, moderator, other_user):
        person = PersonFactory(created_by=other_user)
        client.force_login(moderator)

        response = client.post(
            decide_url(person.pk),
            {"action": "reject", "reason": "   "},
            follow=True,
        )

        # Текст ошибки берёт форма: правило «причина обязательна» живёт
        # в `ModerationRejectForm`, а не в представлении.
        expected = str(QueueRejectForm({"reason": "   "}).errors["reason"][0])
        assert expected in response.content.decode()


class TestDecideOwnPerson:
    """Своего человека решает другой модератор, в том числе по прямому адресу.

    Проверка в списке дыру не закрывает: номер человека угадывается,
    и без `is_own_person` в действии модератор опубликовал бы себя сам.
    """

    @pytest.mark.parametrize("action", ["publish", "reject"])
    def test_own_person_cannot_be_decided(self, client, moderator, action):
        person = PersonFactory(created_by=moderator)
        client.force_login(moderator)

        response = client.post(
            decide_url(person.pk), {"action": action, "reason": "своё"}
        )

        person.refresh_from_db()
        assert response.status_code == 404
        assert person.status == Status.DRAFT
        assert not actions_for(person).exists()


class TestDecideRace:
    """Человек уже решён другим модератором."""

    def test_reject_after_publish_is_refused(self, client, moderator, other_user):
        person = PersonFactory(created_by=other_user, status=Status.PUBLISHED)
        client.force_login(moderator)

        response = client.post(
            decide_url(person.pk),
            {"action": "reject", "reason": "передумал"},
            follow=True,
        )

        person.refresh_from_db()
        assert person.status == Status.PUBLISHED
        assert person.moderation_reason == ""
        assert not actions_for(person).exists()
        assert "Уже обработано" in response.content.decode()

    def test_second_publish_is_refused(self, client, moderator, other_user):
        person = PersonFactory(created_by=other_user, status=Status.PUBLISHED)
        client.force_login(moderator)

        response = client.post(
            decide_url(person.pk), {"action": "publish"}, follow=True
        )

        assert not actions_for(person).exists()
        assert "Уже обработано" in response.content.decode()

    def test_service_false_shows_already_handled(
        self, client, moderator, other_user, monkeypatch
    ):
        """Сверхузкая гонка: проверка прошла, решение принял другой."""
        person = PersonFactory(created_by=other_user)
        client.force_login(moderator)
        monkeypatch.setattr(
            "apps.moderation.views.services.publish_object",
            lambda *args, **kwargs: False,
        )

        response = client.post(
            decide_url(person.pk), {"action": "publish"}, follow=True
        )

        html = response.content.decode()
        person.refresh_from_db()
        assert "Уже обработано" in html
        assert "Человек опубликован." not in html
        assert person.status == Status.DRAFT
        assert not actions_for(person).exists()


class TestDecideAccess:
    """Кто вообще может нажать кнопку."""

    def test_plain_user_gets_403(self, client, user):
        person = PersonFactory()
        client.force_login(user)

        response = client.post(decide_url(person.pk), {"action": "publish"})

        person.refresh_from_db()
        assert response.status_code == 403
        assert person.status == Status.DRAFT
        assert not actions_for(person).exists()

    def test_anonymous_is_redirected_to_login(self, client):
        person = PersonFactory()

        response = client.post(decide_url(person.pk), {"action": "publish"})

        assert response.status_code == 302
        assert reverse("account_login") in response["Location"]

    def test_anonymous_returns_to_people_not_to_post_address(self, client):
        """`next` обязан открываться GET-ом: иначе петля входа."""
        person = PersonFactory()

        response = client.post(
            decide_url(person.pk), {"action": "publish", "next": PEOPLE_URL}
        )

        assert response.status_code == 302
        location = response["Location"]
        assert reverse("account_login") in location
        assert "next=" + quote(PEOPLE_URL) in location
        assert decide_url(person.pk) not in location

    def test_anonymous_post_without_next_returns_to_people(self, client):
        """Пустой `next` заменяется очередью людей, а не адресом решения."""
        person = PersonFactory()

        response = client.post(decide_url(person.pk), {"action": "publish"})

        assert "next=" + quote(PEOPLE_URL) in response["Location"]

    def test_anonymous_post_ignores_foreign_next(self, client):
        """Открытый редирект на чужой сайт не проходит."""
        person = PersonFactory()

        response = client.post(
            decide_url(person.pk),
            {"action": "publish", "next": "https://evil.example/steal"},
        )

        assert "evil.example" not in response["Location"]
        assert "next=" + quote(PEOPLE_URL) in response["Location"]

    def test_next_opens_after_login(self, client, moderator, other_user):
        """Сквозной сценарий петли: анонимный POST → вход → `next` GET-ом."""
        person = PersonFactory(created_by=other_user)

        anonymous = client.post(
            decide_url(person.pk), {"action": "publish", "next": PEOPLE_URL}
        )
        next_url = anonymous["Location"].split("next=")[1]

        client.force_login(moderator)
        response = client.get(next_url)

        assert response.status_code == 200


class TestDecideBadRequest:
    """Метод, адрес и поле действия проверяются до сервиса."""

    def test_get_is_not_allowed(self, client, moderator):
        person = PersonFactory()
        client.force_login(moderator)

        response = client.get(decide_url(person.pk))

        person.refresh_from_db()
        assert response.status_code == 405
        assert person.status == Status.DRAFT

    def test_missing_person_is_404(self, client, moderator):
        client.force_login(moderator)

        response = client.post(decide_url(999999), {"action": "publish"})

        assert response.status_code == 404

    def test_unknown_action_is_400(self, client, moderator, other_user):
        person = PersonFactory(created_by=other_user)
        client.force_login(moderator)

        response = client.post(decide_url(person.pk), {"action": "hide"})

        person.refresh_from_db()
        assert response.status_code == 400
        assert person.status == Status.DRAFT
        assert not actions_for(person).exists()


class TestDecideNext:
    """Возврат в очередь людей и защита от открытого редиректа."""

    def test_safe_next_is_kept(self, client, moderator, other_user):
        person = PersonFactory(created_by=other_user)
        client.force_login(moderator)

        response = client.post(
            decide_url(person.pk),
            {"action": "publish", "next": f"{PEOPLE_URL}?page=2"},
        )

        assert response.status_code == 302
        assert response["Location"] == f"{PEOPLE_URL}?page=2"

    def test_foreign_next_falls_back_to_people(self, client, moderator, other_user):
        person = PersonFactory(created_by=other_user)
        client.force_login(moderator)

        response = client.post(
            decide_url(person.pk),
            {"action": "publish", "next": "https://evil.example/"},
        )

        assert response.status_code == 302
        assert response["Location"] == PEOPLE_URL


class TestPeopleTemplate:
    """Страница очереди людей: карточка, кнопки и пустое состояние."""

    def test_page_shows_name_visibility_and_photo_link(self, client, moderator):
        photo = PhotoFactory(title="Финал 1994")
        person = PersonFactory(
            first_name="Алексей",
            last_name="Петров",
            name_visibility=NameVisibility.FIRST_NAME_ONLY,
        )
        PhotoPersonFactory(photo=photo, person=person)
        client.force_login(moderator)

        html = client.get(PEOPLE_URL).content.decode()

        assert person.public_name in html
        assert person.get_name_visibility_display() in html
        assert photo.get_absolute_url() in html
        # Скрытая фамилия не должна попасть на служебную страницу.
        assert "Петров" not in html

    def test_card_actions_are_plain_post_forms(self, client, moderator):
        person = PersonFactory()
        client.force_login(moderator)

        html = client.get(PEOPLE_URL).content.decode()

        assert f'action="{decide_url(person.pk)}"' in html
        assert 'name="action" value="publish"' in html
        assert "<details" in html
        assert f'id="reason-person-{person.pk}"' in html

    def test_person_without_photo_has_no_photo_link(self, client, moderator):
        PersonFactory()
        client.force_login(moderator)

        html = client.get(PEOPLE_URL).content.decode()

        assert "снимок не указан" in html

    def test_deleted_photo_shows_a_note_not_a_link(self, client, moderator):
        photo = PhotoFactory()
        photo.soft_delete()
        PhotoPersonFactory(photo=photo, person=PersonFactory())
        client.force_login(moderator)

        html = client.get(PEOPLE_URL).content.decode()

        assert photo.get_absolute_url() not in html
        assert "снимок удалён" in html

    def test_empty_queue_has_its_own_state(self, client, moderator):
        """Пустая очередь — не «всё разобрано» без объяснения."""
        client.force_login(moderator)

        html = client.get(PEOPLE_URL).content.decode()

        assert "Очередь пуста" in html

    def test_page_has_no_scope_warning(self, client, moderator):
        """Плашки про область нет: она бы соврала — очередь общая."""
        moderator.moderated_sports.clear()
        PersonFactory()
        client.force_login(moderator)

        html = client.get(PEOPLE_URL).content.decode()

        assert "Вам не назначены виды спорта" not in html


class TestNavbar:
    """Пункт меню «Люди» виден только модератору."""

    def test_moderator_sees_the_link(self, client, moderator):
        client.force_login(moderator)

        html = client.get(PEOPLE_URL).content.decode()

        assert reverse("moderation:people") in html

    def test_plain_user_does_not_see_the_link(self, client, user):
        client.force_login(user)

        html = client.get(reverse("core:home")).content.decode()

        assert reverse("moderation:people") not in html


class TestPeopleBlockStatus:
    """Блок людей на правке фото печатает статус и причину отклонения."""

    def test_rejected_person_shows_reason(self, client, user, baseball):
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        person = PersonFactory(
            first_name="Сергей",
            last_name="Иванов",
            created_by=user,
            status=Status.REJECTED,
            moderation_reason="не тот человек",
        )
        PhotoPersonFactory(photo=photo, person=person)
        client.force_login(user)

        html = client.get(f"/photos/{photo.pk}/edit/").content.decode()

        assert person.get_status_display() in html
        assert "не тот человек" in html
        # Отклонённый человек — не черновик: литерал соврал бы.
        assert "не опубликован" not in html
        assert person.get_absolute_url() not in html
