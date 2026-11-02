"""Тесты разбора жалоб: состав, область, действия и гонка.

Адрес решения угадывается по номеру жалобы, поэтому «пункта нет в списке»
мало: своя жалоба, чужой вид спорта и уже разобранная жалоба обязаны
получить отказ и на прямой POST. Иначе модератор обошёл бы проверку одним
запросом, а журнал записал бы решение, которого нет.

Второй риск — гонка двух модераторов: второй не должен отменить решение
первого, иначе по одной жалобе в журнале останутся два противоположных
решения. Третий — число запросов: цели жалоб грузятся пачкой, иначе
generic-FK дал бы запрос на каждую карточку.
"""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import UserRole
from apps.moderation.emails import notify_moderators_new_report
from apps.moderation.forms import ReportResolutionForm
from apps.moderation.models import (
    ModerationAction,
    ModerationActionType,
    Report,
    ReportStatus,
)
from tests.factories import ModeratorFactory, PhotoFactory, UserFactory

pytestmark = pytest.mark.django_db


def reports_url():
    return reverse("moderation:reports")


def decide_url(pk):
    return reverse("moderation:report_decide", args=[pk])


def report_for(obj, **kwargs):
    """Жалоба на объект: тип цели и вид спорта берутся из самого объекта."""
    data = {
        "content_type": ContentType.objects.get_for_model(obj),
        "object_id": obj.pk,
        "sport": getattr(obj, "sport", None),
    }
    data.update(kwargs)
    return Report.objects.create(**data)


def actions_for(report):
    """Записи журнала по жалобе: их пишет только сервис решений."""
    return ModerationAction.objects.filter(
        content_type=ContentType.objects.get_for_model(Report),
        object_id=report.pk,
    )


def card_ids(response):
    """Номера жалоб на странице — без подписей и дат."""
    return [card.report_id for card in response.context["cards"]]


def baseball_moderator(baseball, email="reports-moderator@example.com"):
    """Модератор ровно одного вида спорта."""
    return ModeratorFactory(email=email, moderated_sports=[baseball])


class TestComposition:
    """В список попадает только то, что ждёт решения."""

    def test_open_reports_are_listed(self, client, moderator, photo):
        new = report_for(photo, status=ReportStatus.NEW)
        in_review = report_for(photo, status=ReportStatus.IN_REVIEW)
        client.force_login(moderator)

        ids = card_ids(client.get(reports_url()))

        assert set(ids) == {new.pk, in_review.pk}

    def test_decided_reports_are_hidden(self, client, moderator, photo):
        resolved = report_for(photo, status=ReportStatus.RESOLVED)
        rejected = report_for(photo, status=ReportStatus.REJECTED)
        client.force_login(moderator)

        ids = card_ids(client.get(reports_url()))

        assert resolved.pk not in ids
        assert rejected.pk not in ids

    def test_fresh_report_goes_first(self, client, moderator, photo):
        older = report_for(photo)
        newer = report_for(photo)
        client.force_login(moderator)

        ids = card_ids(client.get(reports_url()))

        # Свежая жалоба важнее: в отличие от очереди черновиков, где
        # старое ждёт дольше, претензию о правах нельзя откладывать.
        assert ids.index(newer.pk) < ids.index(older.pk)


class TestSportScope:
    """Область модератора: чужой вид спорта в список не попадает."""

    def test_other_sport_report_is_hidden(self, client, baseball, softball):
        moderator = baseball_moderator(baseball)
        own = report_for(PhotoFactory(sport=baseball))
        foreign = report_for(PhotoFactory(sport=softball))
        client.force_login(moderator)

        ids = card_ids(client.get(reports_url()))

        assert own.pk in ids
        assert foreign.pk not in ids

    def test_admin_role_and_superuser_see_everything(self, client, baseball, softball):
        baseball_report = report_for(PhotoFactory(sport=baseball))
        softball_report = report_for(PhotoFactory(sport=softball))

        for user in (
            UserFactory(email="role-admin@example.com", role=UserRole.ADMIN),
            UserFactory(email="super@example.com", is_superuser=True),
        ):
            client.force_login(user)

            ids = card_ids(client.get(reports_url()))

            assert {baseball_report.pk, softball_report.pk} <= set(ids)


class TestReportWithoutSport:
    """Жалоба без вида спорта видна всем: иначе она теряется в списке."""

    def test_visible_to_sport_moderator(self, client, baseball):
        moderator = baseball_moderator(baseball)
        report = report_for(PhotoFactory(sport=baseball), sport=None)
        client.force_login(moderator)

        assert report.pk in card_ids(client.get(reports_url()))

    def test_visible_to_moderator_without_scope(self, client, baseball):
        moderator = UserFactory(email="no-scope@example.com", role=UserRole.MODERATOR)
        report = report_for(PhotoFactory(sport=baseball), sport=None)
        client.force_login(moderator)

        response = client.get(reports_url())

        html = response.content.decode()
        # Пустая область — плашка, а не пустая страница: жалобы без вида
        # спорта видны всем, и «жалоб нет» соврало бы.
        assert response.context["scope_missing"] is True
        assert "Вам не назначены виды спорта" in html
        assert report.pk in card_ids(response)


class TestOwnReport:
    """Своё разбирает другой человек, в том числе по прямому адресу."""

    def test_own_report_is_not_listed(self, client, baseball):
        moderator = baseball_moderator(baseball)
        own = report_for(PhotoFactory(sport=baseball), reporter=moderator)
        foreign = report_for(PhotoFactory(sport=baseball))
        client.force_login(moderator)

        ids = card_ids(client.get(reports_url()))

        assert own.pk not in ids
        assert foreign.pk in ids

    def test_anonymous_report_stays_listed(self, client, baseball):
        """`exclude(reporter=user)` выбросил бы и анонимные жалобы."""
        moderator = baseball_moderator(baseball)
        anonymous = report_for(PhotoFactory(sport=baseball), reporter=None)
        client.force_login(moderator)

        assert anonymous.pk in card_ids(client.get(reports_url()))

    @pytest.mark.parametrize(
        "payload",
        [
            {"action": "take"},
            {"action": "resolve", "resolution": "решено"},
            {"action": "reject", "resolution": "отклонено"},
        ],
    )
    def test_own_report_cannot_be_decided(self, client, baseball, payload):
        moderator = baseball_moderator(baseball)
        report = report_for(PhotoFactory(sport=baseball), reporter=moderator)
        client.force_login(moderator)

        response = client.post(decide_url(report.pk), payload)

        report.refresh_from_db()
        assert response.status_code == 404
        assert report.status == ReportStatus.NEW
        assert not actions_for(report).exists()


class TestTake:
    """«Взять в работу» — служебный шаг, а не решение."""

    def test_take_marks_in_review_without_journal(self, client, moderator, photo):
        report = report_for(photo)
        client.force_login(moderator)

        response = client.post(decide_url(report.pk), {"action": "take"})

        report.refresh_from_db()
        assert response.status_code == 302
        assert report.status == ReportStatus.IN_REVIEW
        assert not actions_for(report).exists()


class TestResolve:
    """Решение с текстом: поля заполнены, журнал — по жалобе."""

    def test_resolve_fills_fields_and_logs(self, client, moderator, photo):
        report = report_for(photo)
        client.force_login(moderator)

        response = client.post(
            decide_url(report.pk),
            {"action": "resolve", "resolution": "  год исправлен  "},
        )

        report.refresh_from_db()
        assert response.status_code == 302
        assert report.status == ReportStatus.RESOLVED
        # Текст обрезает форма: заявителю незачем читать лишние пробелы.
        assert report.resolution == "год исправлен"
        assert report.resolved_by == moderator
        assert report.resolved_at is not None
        action = actions_for(report).get()
        assert action.action == ModerationActionType.REPORT_RESOLVED
        assert action.reason == "год исправлен"


class TestReject:
    """Отклонение жалобы пишется своим типом журнала."""

    def test_reject_fills_fields_and_logs(self, client, moderator, photo):
        report = report_for(photo)
        client.force_login(moderator)

        response = client.post(
            decide_url(report.pk),
            {"action": "reject", "resolution": "претензия не обоснована"},
        )

        report.refresh_from_db()
        assert response.status_code == 302
        assert report.status == ReportStatus.REJECTED
        assert report.resolution == "претензия не обоснована"
        assert report.resolved_by == moderator
        action = actions_for(report).get()
        assert action.action == ModerationActionType.REPORT_REJECTED


class TestEmptyResolution:
    """Пустой текст не проходит: заявителю нечего было бы прочитать."""

    @pytest.mark.parametrize("action", ["resolve", "reject"])
    @pytest.mark.parametrize("resolution", ["", "   "])
    def test_empty_text_changes_nothing(
        self, client, moderator, photo, action, resolution
    ):
        report = report_for(photo)
        client.force_login(moderator)

        response = client.post(
            decide_url(report.pk),
            {"action": action, "resolution": resolution},
        )

        report.refresh_from_db()
        assert response.status_code == 302
        assert report.status == ReportStatus.NEW
        assert report.resolution == ""
        assert not actions_for(report).exists()

    def test_empty_text_shows_form_error(self, client, moderator, photo):
        report = report_for(photo)
        client.force_login(moderator)

        response = client.post(
            decide_url(report.pk),
            {"action": "resolve", "resolution": "   "},
            follow=True,
        )

        # Текст ошибки берётся из формы, а не пишется в представлении:
        # правило «текст обязателен» живёт в `ReportResolutionForm`.
        form = ReportResolutionForm({"resolution": "   "})
        expected = str(form.errors["resolution"][0])
        assert expected in response.content.decode()


class TestRace:
    """Жалоба уже разобрана другим модератором."""

    def test_second_reject_is_refused(self, client, moderator, photo):
        report = report_for(
            photo, status=ReportStatus.RESOLVED, resolution="год исправлен"
        )
        client.force_login(moderator)

        response = client.post(
            decide_url(report.pk),
            {"action": "reject", "resolution": "передумал"},
            follow=True,
        )

        report.refresh_from_db()
        assert report.status == ReportStatus.RESOLVED
        assert report.resolution == "год исправлен"
        assert not actions_for(report).exists()
        assert "Уже обработано" in response.content.decode()

    def test_take_does_not_reopen_resolved_report(self, client, moderator, photo):
        """`take_report` на решённой вернул бы жалобу в работу."""
        report = report_for(photo, status=ReportStatus.RESOLVED)
        client.force_login(moderator)

        response = client.post(decide_url(report.pk), {"action": "take"}, follow=True)

        report.refresh_from_db()
        assert report.status == ReportStatus.RESOLVED
        assert "Уже обработано" in response.content.decode()

    def test_service_false_shows_already_handled(
        self, client, moderator, photo, monkeypatch
    ):
        """Сверхузкая гонка: проверка прошла, решение принял другой.

        Молчание выглядело бы как успех, поэтому `False` от сервиса
        превращается в честное «Уже обработано».
        """
        report = report_for(photo)
        client.force_login(moderator)
        monkeypatch.setattr(
            "apps.moderation.views.services.resolve_report",
            lambda *args, **kwargs: False,
        )

        response = client.post(
            decide_url(report.pk),
            {"action": "resolve", "resolution": "решено"},
            follow=True,
        )

        html = response.content.decode()
        report.refresh_from_db()
        assert "Уже обработано" in html
        assert "Жалоба решена" not in html
        assert report.status == ReportStatus.NEW
        assert not actions_for(report).exists()


class TestQueryCount:
    """Цели грузятся пачкой: число запросов не растёт с порцией."""

    def test_five_reports_cost_the_same_as_one(self, client, moderator, baseball):
        client.force_login(moderator)
        report_for(PhotoFactory(sport=baseball))

        with CaptureQueriesContext(connection) as one:
            client.get(reports_url())

        for _ in range(4):
            report_for(PhotoFactory(sport=baseball))

        with CaptureQueriesContext(connection) as five:
            response = client.get(reports_url())

        assert len(card_ids(response)) == 5
        assert len(five) == len(one)


class TestAccess:
    """Вход, роль и код ответа."""

    def test_anonymous_is_redirected_to_login(self, client, photo):
        report_for(photo)

        response = client.get(reports_url())

        assert response.status_code == 302
        assert reverse("account_login") in response["Location"]

    def test_plain_user_gets_403(self, client, user, photo):
        report_for(photo)
        client.force_login(user)

        assert client.get(reports_url()).status_code == 403

    def test_moderator_opens_page(self, client, moderator, photo):
        report = report_for(photo)
        client.force_login(moderator)

        response = client.get(reports_url())

        assert response.status_code == 200
        assert card_ids(response) == [report.pk]

    def test_page_is_closed_from_indexing(self, client, moderator, photo):
        report_for(photo)
        client.force_login(moderator)

        html = client.get(reports_url()).content.decode()

        assert "noindex" in html
        assert "nofollow" in html


class TestBadRequest:
    """Метод, адрес и поле действия проверяются до сервиса."""

    def test_get_is_not_allowed(self, client, moderator, photo):
        report = report_for(photo)
        client.force_login(moderator)

        response = client.get(decide_url(report.pk))

        report.refresh_from_db()
        assert response.status_code == 405
        assert report.status == ReportStatus.NEW

    def test_missing_report_is_404(self, client, moderator):
        client.force_login(moderator)

        response = client.post(decide_url(999999), {"action": "take"})

        assert response.status_code == 404

    def test_other_sport_moderator_gets_404(self, client, baseball, softball):
        moderator = ModeratorFactory(
            email="foreign-reports@example.com", moderated_sports=[softball]
        )
        report = report_for(PhotoFactory(sport=baseball))
        client.force_login(moderator)

        response = client.post(decide_url(report.pk), {"action": "take"})

        report.refresh_from_db()
        assert response.status_code == 404
        assert report.status == ReportStatus.NEW

    def test_unknown_action_is_400(self, client, moderator, photo):
        report = report_for(photo)
        client.force_login(moderator)

        response = client.post(decide_url(report.pk), {"action": "hide"})

        report.refresh_from_db()
        assert response.status_code == 400
        assert report.status == ReportStatus.NEW
        assert not actions_for(report).exists()


class TestNext:
    """Возврат в список и защита от открытого редиректа."""

    def test_safe_next_is_kept(self, client, moderator, photo):
        report = report_for(photo)
        client.force_login(moderator)

        response = client.post(
            decide_url(report.pk),
            {"action": "take", "next": f"{reports_url()}?page=2"},
        )

        assert response.status_code == 302
        assert response["Location"] == f"{reports_url()}?page=2"

    def test_foreign_next_falls_back_to_list(self, client, moderator, photo):
        report = report_for(photo)
        client.force_login(moderator)

        response = client.post(
            decide_url(report.pk),
            {"action": "take", "next": "https://evil.example/"},
        )

        assert response.status_code == 302
        assert response["Location"] == reports_url()


class TestNoJavaScript:
    """Формы карточки работают обычным POST, без скриптов."""

    def test_card_has_plain_forms(self, client, moderator, photo):
        report_for(photo)
        client.force_login(moderator)

        html = client.get(reports_url()).content.decode()

        assert 'method="post"' in html
        assert 'name="action" value="take"' in html
        assert 'name="action" value="resolve"' in html
        assert 'name="action" value="reject"' in html
        assert "<details" in html
        assert 'name="resolution"' in html


class TestTargetCard:
    """Карточка ведёт на живой объект и молчит про удалённый."""

    def test_alive_target_is_linked(self, client, moderator, team):
        team.publish()
        report_for(team)
        client.force_login(moderator)

        html = client.get(reports_url()).content.decode()

        assert f'href="{team.get_absolute_url()}"' in html

    def test_deleted_target_has_no_link(self, client, moderator, baseball):
        photo = PhotoFactory(sport=baseball)
        url = photo.get_absolute_url()
        report_for(photo)
        photo.soft_delete(reason="удалено модератором")
        client.force_login(moderator)

        html = client.get(reports_url()).content.decode()

        assert url not in html
        assert "объект удалён" in html


class TestReportEmail:
    """Письмо о жалобе ведёт в интерфейс, а не в админку."""

    def test_address_is_absolute_without_request(self, moderator, photo):
        report = report_for(photo)

        notify_moderators_new_report(report)

        body = mail.outbox[-1].body
        assert f"https://sportmemo.ru{reports_url()}" in body

    def test_address_uses_request_host(self, moderator, photo, rf):
        report = report_for(photo)
        request = rf.get(reports_url())

        notify_moderators_new_report(report, request=request)

        body = mail.outbox[-1].body
        assert f"http://testserver{reports_url()}" in body


class TestTeamTarget:
    """Жалоба на команду тоже попадает в список."""

    def test_team_report_is_listed(self, client, moderator, team):
        team.publish()
        report = report_for(team)
        client.force_login(moderator)

        assert report.pk in card_ids(client.get(reports_url()))

    def test_team_title_comes_from_str(self, client, moderator, team):
        team.publish()
        report_for(team)
        client.force_login(moderator)

        html = client.get(reports_url()).content.decode()

        assert str(team) in html
