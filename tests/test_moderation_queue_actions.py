"""Тесты действий очереди: решение по черновику и его границы.

Адрес решения угадывается по номеру объекта, поэтому «пункта нет
в списке» мало: свой черновик, чужой вид спорта и уже решённый объект
обязаны получить отказ и на прямой POST. Иначе модератор обошёл бы
проверку одним запросом, а журнал записал бы решение, которого нет.

Второй риск — гонка двух модераторов: второй не должен отменить решение
первого, иначе по одному объекту в журнале останутся два
противоположных решения.
"""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from apps.accounts.models import UserRole
from apps.core.models import Status
from apps.moderation.forms import QueueRejectForm
from apps.moderation.models import ModerationAction, ModerationActionType
from apps.photos.models import ProcessingStatus
from tests.factories import (
    EventFactory,
    ModeratorFactory,
    PhotoFactory,
    TeamFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db

QUEUE_URL = reverse("moderation:queue")


def decide_url(kind, pk):
    return reverse("moderation:queue_decide", args=[kind, pk])


def actions_for(obj):
    """Записи журнала по объекту: их пишет только сервис решений."""
    return ModerationAction.objects.filter(
        content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk
    )


def baseball_moderator(baseball):
    """Модератор ровно одного вида спорта."""
    return ModeratorFactory(
        email="decide-moderator@example.com", moderated_sports=[baseball]
    )


def draft_photo(sport, author=None):
    """Черновик фото, готовый к решению."""
    return PhotoFactory(
        sport=sport,
        status=Status.DRAFT,
        processing_status=ProcessingStatus.PROCESSED,
        uploaded_by=author,
    )


def own_object(kind, sport, author):
    """Объект, созданный самим модератором: решать его должен другой."""
    if kind == "team":
        return TeamFactory(sport=sport, created_by=author)
    if kind == "event":
        return EventFactory(sport=sport, created_by=author)
    return draft_photo(sport, author=author)


class TestPublish:
    """Одобрение: статус, автор решения и одна запись в журнале."""

    def test_publish_publishes_and_logs(self, client, baseball):
        moderator = baseball_moderator(baseball)
        team = TeamFactory(sport=baseball)
        client.force_login(moderator)

        response = client.post(decide_url("team", team.pk), {"action": "publish"})

        team.refresh_from_db()
        assert response.status_code == 302
        assert team.status == Status.PUBLISHED
        assert team.moderated_by_id == moderator.pk
        assert list(actions_for(team).values_list("action", flat=True)) == [
            ModerationActionType.PUBLISH
        ]


class TestReject:
    """Отклонение: причина доходит до автора и до журнала."""

    def test_reject_keeps_reason(self, client, baseball):
        moderator = baseball_moderator(baseball)
        team = TeamFactory(sport=baseball)
        client.force_login(moderator)

        response = client.post(
            decide_url("team", team.pk),
            {"action": "reject", "reason": "  не та команда  "},
        )

        team.refresh_from_db()
        assert response.status_code == 302
        assert team.status == Status.REJECTED
        # Причина обрезается формой: автор не должен читать лишние пробелы.
        assert team.moderation_reason == "не та команда"
        action = actions_for(team).get()
        assert action.action == ModerationActionType.REJECT
        assert action.reason == "не та команда"

    @pytest.mark.parametrize("reason", ["", "   "])
    def test_empty_reason_changes_nothing(self, client, baseball, reason):
        moderator = baseball_moderator(baseball)
        team = TeamFactory(sport=baseball)
        client.force_login(moderator)

        response = client.post(
            decide_url("team", team.pk), {"action": "reject", "reason": reason}
        )

        team.refresh_from_db()
        assert response.status_code == 302
        assert team.status == Status.DRAFT
        assert not actions_for(team).exists()

    def test_empty_reason_shows_form_error(self, client, baseball):
        moderator = baseball_moderator(baseball)
        team = TeamFactory(sport=baseball)
        client.force_login(moderator)

        response = client.post(
            decide_url("team", team.pk),
            {"action": "reject", "reason": "   "},
            follow=True,
        )

        # Текст ошибки берётся из формы, а не пишется в представлении:
        # правило «причина обязательна» живёт в `ModerationRejectForm`.
        expected = str(QueueRejectForm({"reason": "   "}).errors["reason"][0])
        assert expected in response.content.decode()


class TestSelfApproval:
    """Своё решает другой человек, в том числе по прямому адресу.

    Проверка в списке дыру не закрывает: номер объекта угадывается,
    и без `is_own` в действии модератор опубликовал бы себя сам.
    """

    @pytest.mark.parametrize("action", ["publish", "reject"])
    @pytest.mark.parametrize("kind", ["team", "event", "photo"])
    def test_own_object_cannot_be_decided(self, client, baseball, kind, action):
        moderator = baseball_moderator(baseball)
        obj = own_object(kind, baseball, moderator)
        client.force_login(moderator)

        response = client.post(
            decide_url(kind, obj.pk), {"action": action, "reason": "своё"}
        )

        obj.refresh_from_db()
        assert response.status_code == 404
        assert obj.status == Status.DRAFT
        assert not actions_for(obj).exists()


class TestRace:
    """Объект уже решён другим модератором."""

    def test_reject_after_publish_is_refused(self, client, baseball):
        moderator = baseball_moderator(baseball)
        team = TeamFactory(sport=baseball, status=Status.PUBLISHED)
        client.force_login(moderator)

        response = client.post(
            decide_url("team", team.pk),
            {"action": "reject", "reason": "передумал"},
            follow=True,
        )

        team.refresh_from_db()
        assert team.status == Status.PUBLISHED
        assert team.moderation_reason == ""
        assert not actions_for(team).exists()
        assert "Уже обработано" in response.content.decode()

    def test_second_publish_is_refused(self, client, baseball):
        moderator = baseball_moderator(baseball)
        team = TeamFactory(sport=baseball, status=Status.PUBLISHED)
        client.force_login(moderator)

        response = client.post(
            decide_url("team", team.pk), {"action": "publish"}, follow=True
        )

        assert not actions_for(team).exists()
        assert "Уже обработано" in response.content.decode()

    def test_service_false_shows_already_handled(self, client, baseball, monkeypatch):
        """Сверхузкая гонка: проверка прошла, решение принял другой.

        Молчание выглядело бы как успех, поэтому `False` от сервиса
        превращается в честное «Уже обработано».
        """
        moderator = baseball_moderator(baseball)
        team = TeamFactory(sport=baseball)
        client.force_login(moderator)
        monkeypatch.setattr(
            "apps.moderation.views.services.publish_object",
            lambda *args, **kwargs: False,
        )

        response = client.post(
            decide_url("team", team.pk), {"action": "publish"}, follow=True
        )

        html = response.content.decode()
        team.refresh_from_db()
        assert "Уже обработано" in html
        assert "Команда: опубликовано." not in html
        assert team.status == Status.DRAFT
        assert not actions_for(team).exists()


class TestRights:
    """Кто вообще может нажать кнопку."""

    def test_other_sport_moderator_gets_404(self, client, baseball, softball):
        moderator = ModeratorFactory(
            email="softball-moderator@example.com", moderated_sports=[softball]
        )
        team = TeamFactory(sport=baseball)
        client.force_login(moderator)

        response = client.post(decide_url("team", team.pk), {"action": "publish"})

        team.refresh_from_db()
        assert response.status_code == 404
        assert team.status == Status.DRAFT
        assert not actions_for(team).exists()

    def test_plain_user_gets_403(self, client, user, baseball):
        team = TeamFactory(sport=baseball)
        client.force_login(user)

        response = client.post(decide_url("team", team.pk), {"action": "publish"})

        team.refresh_from_db()
        assert response.status_code == 403
        assert team.status == Status.DRAFT

    def test_anonymous_is_redirected_to_login(self, client, baseball):
        team = TeamFactory(sport=baseball)

        response = client.post(decide_url("team", team.pk), {"action": "publish"})

        assert response.status_code == 302
        assert reverse("account_login") in response["Location"]

    def test_admin_role_can_decide_any_sport(self, client, baseball, softball):
        admin = UserFactory(role=UserRole.ADMIN)
        team = TeamFactory(sport=softball)
        client.force_login(admin)

        response = client.post(decide_url("team", team.pk), {"action": "publish"})

        team.refresh_from_db()
        assert response.status_code == 302
        assert team.status == Status.PUBLISHED


class TestBadRequest:
    """Подделанный адрес и подделанное поле действия."""

    def test_get_is_not_allowed(self, client, baseball):
        moderator = baseball_moderator(baseball)
        team = TeamFactory(sport=baseball)
        client.force_login(moderator)

        response = client.get(decide_url("team", team.pk))

        assert response.status_code == 405
        team.refresh_from_db()
        assert team.status == Status.DRAFT

    def test_unknown_kind_is_404(self, client, baseball):
        moderator = baseball_moderator(baseball)
        client.force_login(moderator)

        response = client.post(
            reverse("moderation:queue_decide", args=["unknown", 1]),
            {"action": "publish"},
        )

        assert response.status_code == 404

    def test_missing_object_is_404(self, client, baseball):
        moderator = baseball_moderator(baseball)
        client.force_login(moderator)

        response = client.post(decide_url("team", 999999), {"action": "publish"})

        assert response.status_code == 404

    def test_unknown_action_is_400(self, client, baseball):
        moderator = baseball_moderator(baseball)
        team = TeamFactory(sport=baseball)
        client.force_login(moderator)

        response = client.post(decide_url("team", team.pk), {"action": "hide"})

        team.refresh_from_db()
        assert response.status_code == 400
        assert team.status == Status.DRAFT
        assert not actions_for(team).exists()


class TestNext:
    """Возврат в очередь и защита от открытого редиректа."""

    def test_safe_next_is_kept(self, client, baseball):
        moderator = baseball_moderator(baseball)
        team = TeamFactory(sport=baseball)
        client.force_login(moderator)

        response = client.post(
            decide_url("team", team.pk),
            {"action": "publish", "next": f"{QUEUE_URL}?page=2"},
        )

        assert response.status_code == 302
        assert response["Location"] == f"{QUEUE_URL}?page=2"

    def test_foreign_next_falls_back_to_queue(self, client, baseball):
        moderator = baseball_moderator(baseball)
        team = TeamFactory(sport=baseball)
        client.force_login(moderator)

        response = client.post(
            decide_url("team", team.pk),
            {"action": "publish", "next": "https://evil.example/"},
        )

        assert response.status_code == 302
        assert response["Location"] == QUEUE_URL


class TestNoJavaScript:
    """Формы карточки работают обычным POST, без скриптов."""

    def test_card_has_plain_forms(self, client, baseball):
        moderator = baseball_moderator(baseball)
        TeamFactory(sport=baseball)
        client.force_login(moderator)

        html = client.get(QUEUE_URL).content.decode()

        assert 'method="post"' in html
        assert 'name="action" value="publish"' in html
        assert 'name="action" value="reject"' in html
        assert "<details" in html
        assert 'name="reason"' in html
