"""Тесты очереди черновиков: состав, область, порядок и паритет.

Очередь — страница для модератора с телефона. Главный дефект, который
здесь ловится, — расхождение правил: объект виден в списке, но не
решается (или наоборот). Поэтому правило «ждёт решения» проверяется
тремя путями: функцией `is_pending`, выборкой `pending_queryset` и прямым
POST на адрес решения.
"""

from datetime import timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserRole
from apps.core.models import Status
from apps.moderation import queue
from apps.moderation.models import ModerationAction
from apps.photos.models import ProcessingStatus
from tests.factories import (
    EventFactory,
    ModeratorFactory,
    PersonFactory,
    PhotoFactory,
    PhotoPersonFactory,
    TeamFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db

QUEUE_URL = reverse("moderation:queue")


def decide_url(kind, pk):
    return reverse("moderation:queue_decide", args=[kind, pk])


def queue_keys(user):
    """Индекс очереди как множество `(kind, pk)` — без дат и порядка."""
    return {(kind, pk) for _created_at, kind, pk in queue.queue_entries(user)}


def actions_for(obj):
    """Записи журнала по объекту: их пишет только сервис решений."""
    return ModerationAction.objects.filter(
        content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk
    )


def baseball_moderator(baseball):
    """Модератор ровно одного вида спорта."""
    return ModeratorFactory(
        email="queue-moderator@example.com", moderated_sports=[baseball]
    )


class TestQueueComposition:
    """В очередь попадает только то, что ждёт решения."""

    def test_draft_team_is_listed(self, moderator, baseball):
        team = TeamFactory(sport=baseball)

        assert ("team", team.pk) in queue_keys(moderator)

    def test_published_team_is_not_listed(self, moderator, baseball):
        team = TeamFactory(sport=baseball, status=Status.PUBLISHED)

        assert ("team", team.pk) not in queue_keys(moderator)

    def test_deleted_team_is_not_listed(self, moderator, baseball):
        team = TeamFactory(sport=baseball)
        team.soft_delete(reason="мусор")

        assert ("team", team.pk) not in queue_keys(moderator)

    def test_merged_team_is_not_listed(self, moderator, baseball):
        team = TeamFactory(sport=baseball)
        target = TeamFactory(sport=baseball)
        team.merge_into(target)

        assert ("team", team.pk) not in queue_keys(moderator)

    def test_draft_event_is_listed(self, moderator, baseball):
        event = EventFactory(sport=baseball)

        assert ("event", event.pk) in queue_keys(moderator)

    def test_draft_photo_is_listed(self, moderator, baseball):
        photo = PhotoFactory(
            sport=baseball,
            status=Status.DRAFT,
            processing_status=ProcessingStatus.PROCESSED,
        )

        assert ("photo", photo.pk) in queue_keys(moderator)


class TestPhotoProcessing:
    """Судить по необработанному фото нельзя: модератор увидел бы заглушку."""

    def test_processed_photo_is_listed(self, moderator, baseball):
        photo = PhotoFactory(
            sport=baseball,
            status=Status.DRAFT,
            processing_status=ProcessingStatus.PROCESSED,
        )

        assert ("photo", photo.pk) in queue_keys(moderator)

    @pytest.mark.parametrize(
        "processing_status",
        [
            ProcessingStatus.UPLOADED,
            ProcessingStatus.PROCESSING,
            ProcessingStatus.FAILED,
        ],
    )
    def test_unprocessed_photo_is_not_listed(
        self, moderator, baseball, processing_status
    ):
        photo = PhotoFactory(
            sport=baseball, status=Status.DRAFT, processing_status=processing_status
        )

        assert ("photo", photo.pk) not in queue_keys(moderator)


class TestPendingRuleParity:
    """`is_pending` и `pending_queryset` отвечают одинаково.

    Разойдись они — черновик был бы виден в списке и не решался бы,
    а это худший вид дефекта: кнопка нажимается, ничего не происходит.
    """

    def test_function_and_queryset_agree(self, baseball):
        draft = TeamFactory(sport=baseball)
        published = TeamFactory(sport=baseball, status=Status.PUBLISHED)
        deleted = TeamFactory(sport=baseball)
        deleted.soft_delete()
        photo = PhotoFactory(sport=baseball, status=Status.DRAFT)

        spec = queue.QUEUE_MODELS["team"]
        for obj in (draft, published, deleted):
            in_queryset = queue.pending_queryset(spec).filter(pk=obj.pk).exists()
            assert queue.is_pending(obj) == in_queryset

        assert not queue.is_pending(photo)


class TestListAndActionParity:
    """«Виден в очереди» ⇔ «POST на адрес решения действительно решает».

    Один тест закрывает весь класс расхождений между списком и действием:
    собственный черновик, чужой вид спорта, уже решённое, удалённое,
    слитое и необработанное фото проверяются одним правилом.
    """

    def test_list_and_action_agree(self, client, baseball, softball):
        moderator = baseball_moderator(baseball)
        client.force_login(moderator)

        own = TeamFactory(sport=baseball, created_by=moderator)
        foreign = TeamFactory(sport=baseball)
        other_sport = TeamFactory(sport=softball)
        published = TeamFactory(sport=baseball, status=Status.PUBLISHED)
        deleted = TeamFactory(sport=baseball)
        deleted.soft_delete(reason="мусор")
        merged = TeamFactory(sport=baseball)
        merged.merge_into(TeamFactory(sport=baseball))
        photo = PhotoFactory(sport=baseball, status=Status.DRAFT)

        cases = [
            ("team", own),
            ("team", foreign),
            ("team", other_sport),
            ("team", published),
            ("team", deleted),
            ("team", merged),
            ("photo", photo),
        ]
        # Видимость и исходные статусы считаются до решений: POST их меняет.
        visible = {(kind, obj.pk) for kind, obj in cases} & queue_keys(moderator)
        before = {(kind, obj.pk): obj.status for kind, obj in cases}

        for kind, obj in cases:
            client.post(decide_url(kind, obj.pk), {"action": "publish"})
            obj.refresh_from_db()

            key = (kind, obj.pk)
            decided = actions_for(obj).exists()
            assert decided == (key in visible), key
            # Решённый объект опубликован, нерешённый остался как был:
            # расхождение списка и действия видно и по статусу, и по журналу.
            expected = Status.PUBLISHED if key in visible else before[key]
            assert obj.status == expected, key


class TestSportScope:
    """Область модератора: чужой вид спорта в очередь не попадает."""

    def test_other_sport_draft_is_hidden(self, baseball, softball):
        moderator = baseball_moderator(baseball)
        team = TeamFactory(sport=softball)

        assert ("team", team.pk) not in queue_keys(moderator)

    def test_own_sport_draft_is_shown(self, baseball, softball):
        moderator = baseball_moderator(baseball)
        team = TeamFactory(sport=baseball)

        assert ("team", team.pk) in queue_keys(moderator)

    def test_admin_role_and_superuser_see_everything(self, baseball, softball):
        team = TeamFactory(sport=softball)
        admin = UserFactory(role=UserRole.ADMIN)
        superuser = UserFactory(is_superuser=True)

        assert ("team", team.pk) in queue_keys(admin)
        assert ("team", team.pk) in queue_keys(superuser)

    def test_empty_scope_shows_notice(self, client):
        moderator = UserFactory(role=UserRole.MODERATOR)
        TeamFactory()
        client.force_login(moderator)

        response = client.get(QUEUE_URL)

        assert response.status_code == 200
        assert response.context["scope_missing"] is True
        assert "Вам не назначены виды спорта" in response.content.decode()
        assert response.context["cards"] == []


class TestOwnDraft:
    """Своё решает другой человек: свой черновик в очередь не попадает."""

    def test_own_team_is_not_listed(self, baseball):
        moderator = baseball_moderator(baseball)
        team = TeamFactory(sport=baseball, created_by=moderator)

        assert ("team", team.pk) not in queue_keys(moderator)

    def test_own_event_is_not_listed(self, baseball):
        moderator = baseball_moderator(baseball)
        event = EventFactory(sport=baseball, created_by=moderator)

        assert ("event", event.pk) not in queue_keys(moderator)

    def test_own_photo_is_not_listed(self, baseball):
        moderator = baseball_moderator(baseball)
        photo = PhotoFactory(
            sport=baseball,
            status=Status.DRAFT,
            processing_status=ProcessingStatus.PROCESSED,
            uploaded_by=moderator,
        )

        assert ("photo", photo.pk) not in queue_keys(moderator)

    def test_own_draft_is_hidden_but_others_are_shown(self, baseball):
        moderator = baseball_moderator(baseball)
        own = TeamFactory(sport=baseball, created_by=moderator)
        foreign = TeamFactory(sport=baseball)

        keys = queue_keys(moderator)

        assert ("team", own.pk) not in keys
        assert ("team", foreign.pk) in keys


class TestAccess:
    """Вход, роль и код ответа."""

    def test_anonymous_is_redirected_to_login(self, client, baseball):
        TeamFactory(sport=baseball)

        response = client.get(QUEUE_URL)

        assert response.status_code == 302
        assert reverse("account_login") in response["Location"]

    def test_plain_user_gets_403(self, client, user, baseball):
        TeamFactory(sport=baseball)
        client.force_login(user)

        response = client.get(QUEUE_URL)

        assert response.status_code == 403

    def test_moderator_opens_queue(self, client, baseball):
        moderator = baseball_moderator(baseball)
        team = TeamFactory(sport=baseball)
        client.force_login(moderator)

        response = client.get(QUEUE_URL)

        assert response.status_code == 200
        assert [card.pk for card in response.context["cards"]] == [team.pk]


class TestOrderAndPaging:
    """Старые первыми и порция 12 — без потерь и повторов."""

    def test_old_draft_goes_first(self, client, baseball):
        moderator = baseball_moderator(baseball)
        newer = TeamFactory(sport=baseball)
        older = TeamFactory(sport=baseball)
        old_stamp = timezone.now() - timedelta(days=5)
        type(older).objects.filter(pk=older.pk).update(created_at=old_stamp)
        client.force_login(moderator)

        response = client.get(QUEUE_URL)

        pks = [card.pk for card in response.context["cards"]]
        assert pks.index(older.pk) < pks.index(newer.pk)

    def test_second_page_holds_the_last_card(self, client, baseball):
        moderator = baseball_moderator(baseball)
        for _ in range(13):
            TeamFactory(sport=baseball)
        client.force_login(moderator)

        first = client.get(QUEUE_URL)
        second = client.get(QUEUE_URL, {"page": 2})

        assert len(first.context["cards"]) == queue.QUEUE_PAGE_SIZE
        assert len(second.context["cards"]) == 1
        assert second.context["page_obj"].number == 2

    def test_bad_page_number_does_not_break(self, client, baseball):
        moderator = baseball_moderator(baseball)
        TeamFactory(sport=baseball)
        client.force_login(moderator)

        response = client.get(QUEUE_URL, {"page": "abc"})

        assert response.status_code == 200
        assert len(response.context["cards"]) == 1


class TestMeta:
    """Служебная страница закрыта от индексации."""

    def test_queue_is_noindex(self, client, baseball):
        moderator = baseball_moderator(baseball)
        client.force_login(moderator)

        html = client.get(QUEUE_URL).content.decode()

        assert "noindex" in html
        assert "nofollow" in html


class TestPeopleCheckbox:
    """Чекбокс «Опубликовать и людей на фото» на карточке снимка.

    Людей публикует тот же модератор, который одобрил снимок: имена —
    часть той же проверки, и второй заход в отдельную очередь был бы
    лишней работой. Без черновиков флажка нет: он обещал бы действие,
    которого нет.
    """

    def test_checkbox_is_checked_by_default(self, client, baseball, other_user):
        moderator = baseball_moderator(baseball)
        photo = PhotoFactory(
            sport=baseball,
            status=Status.DRAFT,
            processing_status=ProcessingStatus.PROCESSED,
        )
        PhotoPersonFactory(photo=photo, person=PersonFactory(created_by=other_user))
        client.force_login(moderator)

        html = client.get(QUEUE_URL).content.decode()

        assert 'name="publish_people"' in html
        assert f'id="publish-people-{photo.pk}"' in html
        assert "checked" in html
        assert "Опубликовать человека на фото (1)" in html

    def test_plural_label_counts_people(self, client, baseball, other_user):
        moderator = baseball_moderator(baseball)
        photo = PhotoFactory(
            sport=baseball,
            status=Status.DRAFT,
            processing_status=ProcessingStatus.PROCESSED,
        )
        for _ in range(3):
            PhotoPersonFactory(photo=photo, person=PersonFactory(created_by=other_user))
        client.force_login(moderator)

        html = client.get(QUEUE_URL).content.decode()

        assert "Опубликовать людей на фото (3)" in html

    def test_no_checkbox_without_drafts(self, client, baseball):
        moderator = baseball_moderator(baseball)
        PhotoFactory(
            sport=baseball,
            status=Status.DRAFT,
            processing_status=ProcessingStatus.PROCESSED,
        )
        client.force_login(moderator)

        html = client.get(QUEUE_URL).content.decode()

        assert 'name="publish_people"' not in html

    def test_only_photo_card_gets_checkbox(self, client, baseball, other_user):
        """Счётчик считается только по фото: у команды людей на фото нет."""
        moderator = baseball_moderator(baseball)
        photo = PhotoFactory(
            sport=baseball,
            status=Status.DRAFT,
            processing_status=ProcessingStatus.PROCESSED,
        )
        PhotoPersonFactory(photo=photo, person=PersonFactory(created_by=other_user))
        TeamFactory(sport=baseball)
        client.force_login(moderator)

        html = client.get(QUEUE_URL).content.decode()

        assert html.count('name="publish_people"') == 1

    def test_query_count_does_not_grow_with_photos(self, client, baseball, other_user):
        """Счётчик черновиков — один запрос на страницу, а не на карточку."""
        moderator = baseball_moderator(baseball)

        def count_queries(photos):
            for _ in range(photos):
                photo = PhotoFactory(
                    sport=baseball,
                    status=Status.DRAFT,
                    processing_status=ProcessingStatus.PROCESSED,
                )
                PhotoPersonFactory(
                    photo=photo, person=PersonFactory(created_by=other_user)
                )
            client.force_login(moderator)
            with CaptureQueriesContext(connection) as queries:
                client.get(QUEUE_URL)
            return len(queries)

        one = count_queries(1)
        five = count_queries(4)

        assert five == one
