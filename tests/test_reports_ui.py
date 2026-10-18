"""Тесты страницы жалобы на ошибку в данных.

Проверяем поведение: кто может пожаловаться, что записывается, как работает
защита от ботов и дублей, кому уходит письмо. Разметка меняется чаще,
чем смысл страницы.
"""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.urls import reverse

from apps.core.models import Status
from apps.moderation.forms import MAX_REPORT_COMMENT_LENGTH
from apps.moderation.models import Report, ReportReason, ReportStatus
from tests.factories import PhotoFactory, ReportFactory

pytestmark = pytest.mark.django_db

REPORT_URL = "/reports/add/"


def report_url():
    return reverse("moderation:report_add")


def report_data(target_obj, key, **overrides):
    data = {
        "target": key,
        "object_id": target_obj.pk,
        "reason": ReportReason.WRONG_DATA,
        "comment": "Год указан неверно.",
    }
    data.update(overrides)
    return data


class TestReportAccess:
    def test_page_opens_for_anonymous(self, client, photo):
        response = client.get(report_url(), {"target": "photo", "object_id": photo.pk})

        assert response.status_code == 200
        assert "Сообщить об ошибке" in response.content.decode()

    def test_anonymous_report_is_created(self, client, photo):
        client.post(report_url(), report_data(photo, "photo"))

        report = Report.objects.get()
        assert report.reporter is None

    def test_logged_in_reporter_is_recorded(self, client, user, photo):
        client.force_login(user)

        client.post(report_url(), report_data(photo, "photo"))

        assert Report.objects.get().reporter == user

    def test_unknown_target_is_not_found(self, client, photo):
        response = client.get(report_url(), {"target": "user", "object_id": photo.pk})

        assert response.status_code == 404

    def test_draft_target_is_not_found(self, client, user):
        photo = PhotoFactory(status=Status.DRAFT)
        client.force_login(user)

        response = client.get(report_url(), {"target": "photo", "object_id": photo.pk})

        assert response.status_code == 404

    def test_missing_target_is_not_found(self, client):
        response = client.get(report_url())

        assert response.status_code == 404

    def test_page_is_closed_from_indexing(self, client, photo):
        response = client.get(report_url(), {"target": "photo", "object_id": photo.pk})

        html = response.content.decode()
        assert 'name="robots" content="noindex' in html

    def test_robots_txt_disallows_reports(self, client):
        body = client.get("/robots.txt").content.decode()

        assert "Disallow: /reports/add/" in body


class TestReportCreation:
    def test_report_is_created(self, client, photo):
        client.post(report_url(), report_data(photo, "photo"))

        report = Report.objects.get()
        assert report.reason == ReportReason.WRONG_DATA
        assert report.comment == "Год указан неверно."
        assert report.status == ReportStatus.NEW

    def test_content_type_matches_target(self, client, photo):
        client.post(report_url(), report_data(photo, "photo"))

        assert Report.objects.get().content_type == ContentType.objects.get_for_model(
            photo
        )

    def test_team_report_is_created(self, client, team):
        team.publish()

        client.post(report_url(), report_data(team, "team"))

        assert Report.objects.filter(object_id=team.pk).exists()

    def test_event_report_is_created(self, client, event):
        event.publish()

        client.post(report_url(), report_data(event, "event"))

        assert Report.objects.filter(object_id=event.pk).exists()

    def test_success_message_is_shown(self, client, photo):
        response = client.post(report_url(), report_data(photo, "photo"), follow=True)

        assert "Модератор проверит" in response.content.decode()

    def test_redirect_goes_to_target_page(self, client, photo):
        response = client.post(report_url(), report_data(photo, "photo"))

        assert response["Location"] == photo.get_absolute_url()

    def test_redirect_goes_to_next(self, client, photo):
        response = client.post(
            report_url(),
            report_data(photo, "photo", next=reverse("core:home")),
        )

        assert response["Location"] == reverse("core:home")

    def test_foreign_next_is_replaced(self, client, photo):
        response = client.post(
            report_url(),
            report_data(photo, "photo", next="https://evil.example.com/"),
        )

        assert response["Location"] == photo.get_absolute_url()

    def test_comment_is_optional(self, client, photo):
        client.post(report_url(), report_data(photo, "photo", comment=""))

        assert Report.objects.get().comment == ""


class TestReportValidation:
    def test_invalid_reason_is_rejected(self, client, photo):
        client.post(report_url(), report_data(photo, "photo", reason="nonsense"))

        assert not Report.objects.exists()

    def test_too_long_comment_is_rejected(self, client, photo):
        client.post(
            report_url(),
            report_data(photo, "photo", comment="а" * (MAX_REPORT_COMMENT_LENGTH + 1)),
        )

        assert not Report.objects.exists()

    def test_comment_is_trimmed(self, client, photo):
        client.post(report_url(), report_data(photo, "photo", comment="  Текст  "))

        assert Report.objects.get().comment == "Текст"


class TestHoneypot:
    def test_filled_trap_creates_no_report(self, client, photo):
        client.post(
            report_url(), report_data(photo, "photo", website="http://spam.example")
        )

        assert not Report.objects.exists()

    def test_filled_trap_still_thanks_the_user(self, client, photo):
        """Отказ подсказал бы боту, что его раскусили."""
        response = client.post(
            report_url(),
            report_data(photo, "photo", website="http://spam.example"),
            follow=True,
        )

        assert "Модератор проверит" in response.content.decode()

    def test_empty_trap_creates_report(self, client, photo):
        client.post(report_url(), report_data(photo, "photo", website=""))

        assert Report.objects.count() == 1


class TestDuplicateReport:
    def test_same_report_twice_creates_one_record(self, client, user, photo):
        client.force_login(user)

        client.post(report_url(), report_data(photo, "photo"))
        client.post(report_url(), report_data(photo, "photo"))

        assert Report.objects.count() == 1

    def test_other_reason_creates_second_record(self, client, user, photo):
        client.force_login(user)

        client.post(report_url(), report_data(photo, "photo"))
        client.post(
            report_url(),
            report_data(photo, "photo", reason=ReportReason.COPYRIGHT),
        )

        assert Report.objects.count() == 2

    def test_anonymous_reports_are_not_checked(self, client, photo):
        """Анонима не за что зацепить: лимиты по IP отложены (P2-17)."""
        client.post(report_url(), report_data(photo, "photo"))
        client.post(report_url(), report_data(photo, "photo"))

        assert Report.objects.count() == 2

    def test_old_report_does_not_block_new_one(self, client, user, photo):
        from django.utils import timezone

        client.force_login(user)
        report = ReportFactory(
            reporter=user, object_id=photo.pk, reason=ReportReason.WRONG_DATA
        )
        Report.objects.filter(pk=report.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=2)
        )

        client.post(report_url(), report_data(photo, "photo"))

        assert Report.objects.count() == 2


class TestReportEmail:
    def test_moderator_gets_letter(self, client, moderator, photo):
        client.post(report_url(), report_data(photo, "photo"))

        assert len(mail.outbox) == 1
        assert moderator.email in mail.outbox[0].to

    def test_letter_mentions_reason(self, client, moderator, photo):
        client.post(report_url(), report_data(photo, "photo"))

        body = mail.outbox[0].body
        assert "неверные данные" in body
        assert "Год указан неверно." in body

    def test_no_moderators_means_no_letter(self, client, photo):
        client.post(report_url(), report_data(photo, "photo"))

        assert not mail.outbox

    def test_report_is_created_even_if_mail_fails(
        self, client, moderator, photo, monkeypatch
    ):
        """Падение почты не отменяет жалобу: она уже записана."""

        def broken(*args, **kwargs):
            raise OSError("SMTP недоступен")

        monkeypatch.setattr("apps.core.emails.send_mail", broken)

        client.post(report_url(), report_data(photo, "photo"))

        assert Report.objects.count() == 1
        assert not mail.outbox

    def test_no_letter_for_bot(self, client, moderator, photo):
        client.post(report_url(), report_data(photo, "photo", website="spam"))

        assert not mail.outbox


class TestReportFormPage:
    def test_page_shows_target_link(self, client, photo):
        html = client.get(
            report_url(), {"target": "photo", "object_id": photo.pk}
        ).content.decode()

        assert photo.get_absolute_url() in html

    def test_page_has_reason_choices(self, client, photo):
        html = client.get(
            report_url(), {"target": "photo", "object_id": photo.pk}
        ).content.decode()

        assert "неверно указан человек" in html

    def test_trap_field_is_hidden_by_class(self, client, photo):
        html = client.get(
            report_url(), {"target": "photo", "object_id": photo.pk}
        ).content.decode()

        assert "report-form__trap" in html

    def test_target_page_has_report_link(self, client, photo):
        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Сообщить об ошибке" in html

    def test_team_page_has_report_link(self, client, team):
        team.publish()

        html = client.get(team.get_absolute_url()).content.decode()

        assert "Сообщить об ошибке" in html

    def test_event_page_has_report_link(self, client, event):
        event.publish()

        html = client.get(event.get_absolute_url()).content.decode()

        assert "Сообщить об ошибке" in html

    def test_person_page_has_no_report_link(self, client, person):
        person.publish()

        html = client.get(person.get_absolute_url()).content.decode()

        assert "Сообщить об ошибке" not in html
