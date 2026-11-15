"""Тесты письма заявителю о решении жалобы.

Заявитель узнаёт решение только из письма: страницы «мои жалобы» нет
(отказ владельца). Поэтому проверяется не факт вызова функции, а письмо
в `mail.outbox`: адресат, отправитель, текст, и отсутствие письма там,
где писать некуда или незачем.

`on_commit` в тестах сам не выполняется: тест обёрнут в транзакцию,
и колбэк дождался бы её конца уже после проверок. Поэтому решения
оборачиваются в `django_capture_on_commit_callbacks(execute=True)`.
"""

import io

import pytest
from django.conf import settings
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.core.management import call_command
from django.urls import reverse

from apps.core.emails import notify_moderators, send_user_email
from apps.moderation import services
from apps.moderation.models import (
    ModerationAction,
    ModerationActionType,
    Report,
    ReportStatus,
)
from tests.factories import ModeratorFactory, PhotoFactory, UserFactory

pytestmark = pytest.mark.django_db

RESOLUTION = "Фамилию исправили по составу команды 1994 года"


def report_for(obj, **kwargs):
    """Жалоба на объект: тип цели и вид спорта берутся из самого объекта."""
    data = {
        "content_type": ContentType.objects.get_for_model(obj),
        "object_id": obj.pk,
        "sport": getattr(obj, "sport", None),
    }
    data.update(kwargs)
    return Report.objects.create(**data)


def decide_url(pk):
    return reverse("moderation:report_decide", args=[pk])


def actions_for(report):
    """Записи журнала по жалобе: их пишет только сервис решений."""
    return ModerationAction.objects.filter(
        content_type=ContentType.objects.get_for_model(Report),
        object_id=report.pk,
    )


def grant_report_permissions(user):
    """Права админки для жалоб: роль модератора их не заменяет."""
    permissions = Permission.objects.filter(
        content_type=ContentType.objects.get_for_model(Report),
        codename__in=("view_report", "change_report"),
    )
    user.user_permissions.add(*permissions)
    return user


class TestServiceDecisionEmail:
    """Письмо ставит сервис решений, а не представление."""

    def test_resolve_sends_letter_to_reporter(
        self, user, moderator, photo, django_capture_on_commit_callbacks
    ):
        report = report_for(photo, reporter=user)

        with django_capture_on_commit_callbacks(execute=True):
            assert services.resolve_report(report, moderator, RESOLUTION)

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [user.email]

    def test_reject_sends_letter_to_reporter(
        self, user, moderator, photo, django_capture_on_commit_callbacks
    ):
        report = report_for(photo, reporter=user)

        with django_capture_on_commit_callbacks(execute=True):
            assert services.reject_report(report, moderator, RESOLUTION)

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [user.email]

    def test_letter_has_resolution_and_target(
        self, user, moderator, photo, django_capture_on_commit_callbacks
    ):
        report = report_for(photo, reporter=user)

        with django_capture_on_commit_callbacks(execute=True):
            services.resolve_report(report, moderator, RESOLUTION)

        body = mail.outbox[0].body
        assert RESOLUTION in body
        assert str(photo) in body

    def test_letter_does_not_blame_reporter(
        self, user, moderator, photo, django_capture_on_commit_callbacks
    ):
        report = report_for(photo, reporter=user)

        with django_capture_on_commit_callbacks(execute=True):
            services.reject_report(report, moderator, RESOLUTION)

        body = mail.outbox[0].body
        assert "пожаловались" not in body
        assert moderator.email not in body

    def test_subject_has_reason(
        self, user, moderator, photo, django_capture_on_commit_callbacks
    ):
        report = report_for(photo, reporter=user, reason="wrong_data")

        with django_capture_on_commit_callbacks(execute=True):
            services.resolve_report(report, moderator, RESOLUTION)

        assert "неверные данные" in mail.outbox[0].subject

    def test_sender_is_default_from_email(
        self, user, moderator, photo, django_capture_on_commit_callbacks
    ):
        """Сравнение с настройкой, а не с литералом.

        `.env` разработчика может переопределить адрес, и тест не должен
        падать из-за этого.
        """
        report = report_for(photo, reporter=user)

        with django_capture_on_commit_callbacks(execute=True):
            services.resolve_report(report, moderator, RESOLUTION)

        assert mail.outbox[0].from_email == settings.DEFAULT_FROM_EMAIL

    def test_anonymous_report_sends_nothing(
        self, moderator, photo, django_capture_on_commit_callbacks
    ):
        report = report_for(photo, reporter=None)

        with django_capture_on_commit_callbacks(execute=True):
            assert services.resolve_report(report, moderator, RESOLUTION)

        assert mail.outbox == []
        report.refresh_from_db()
        assert report.status == ReportStatus.RESOLVED

    def test_inactive_reporter_sends_nothing(
        self, moderator, photo, django_capture_on_commit_callbacks
    ):
        """Так выглядит анонимизированный аккаунт после 5.6."""
        reporter = UserFactory(email="deleted@invalid", is_active=False)
        report = report_for(photo, reporter=reporter)

        with django_capture_on_commit_callbacks(execute=True):
            services.resolve_report(report, moderator, RESOLUTION)

        assert mail.outbox == []

    def test_take_report_sends_nothing(
        self, user, moderator, photo, django_capture_on_commit_callbacks
    ):
        report = report_for(photo, reporter=user)

        with django_capture_on_commit_callbacks(execute=True):
            assert services.take_report(report, moderator)

        assert mail.outbox == []

    def test_second_decision_sends_no_letter(
        self, client, user, moderator, photo, django_capture_on_commit_callbacks
    ):
        """Гонка: второй модератор получает отказ, второго письма нет."""
        report = report_for(photo, reporter=user)
        client.force_login(moderator)
        payload = {"action": "resolve", "resolution": RESOLUTION}

        with django_capture_on_commit_callbacks(execute=True):
            client.post(decide_url(report.pk), payload)
        assert len(mail.outbox) == 1

        response = client.post(decide_url(report.pk), payload, follow=True)

        assert "Уже обработано" in response.content.decode()
        assert len(mail.outbox) == 1


@pytest.fixture
def admin_client_logged_in(client, admin_user):
    client.force_login(admin_user)
    return client


class TestAdminDecision:
    """Решение из админки идёт через сервис: журнал и письмо."""

    def test_decide_sends_letter_and_writes_journal(
        self, admin_client_logged_in, user, photo, django_capture_on_commit_callbacks
    ):
        report = report_for(photo, reporter=user)

        with django_capture_on_commit_callbacks(execute=True):
            response = admin_client_logged_in.post(
                reverse("admin:moderation_report_decide"),
                {
                    "ids": str(report.pk),
                    "action": "resolve",
                    "resolution": RESOLUTION,
                },
            )

        assert response.status_code == 302
        report.refresh_from_db()
        assert report.status == ReportStatus.RESOLVED
        action = actions_for(report).get()
        assert action.action == ModerationActionType.REPORT_RESOLVED
        assert action.reason == RESOLUTION
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [user.email]

    def test_empty_resolution_changes_nothing(
        self, admin_client_logged_in, user, photo, django_capture_on_commit_callbacks
    ):
        report = report_for(photo, reporter=user)

        with django_capture_on_commit_callbacks(execute=True):
            response = admin_client_logged_in.post(
                reverse("admin:moderation_report_decide"),
                {"ids": str(report.pk), "action": "resolve", "resolution": "   "},
            )

        # Форма возвращается с ошибкой: страница, а не редирект в список.
        assert response.status_code == 200
        report.refresh_from_db()
        assert report.status == ReportStatus.NEW
        assert not actions_for(report).exists()
        assert mail.outbox == []

    def test_moderator_without_scope_is_skipped(
        self, client, baseball, softball, django_capture_on_commit_callbacks
    ):
        """Модератор с `is_staff` не решает жалобу чужого вида спорта."""
        moderator = grant_report_permissions(
            ModeratorFactory(
                email="scoped-reports@example.com",
                is_staff=True,
                moderated_sports=[baseball],
            )
        )
        foreign_photo = PhotoFactory(sport=softball)
        report = report_for(foreign_photo, reporter=UserFactory())
        client.force_login(moderator)

        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(
                reverse("admin:moderation_report_decide"),
                {
                    "ids": str(report.pk),
                    "action": "resolve",
                    "resolution": RESOLUTION,
                },
                follow=True,
            )

        report.refresh_from_db()
        assert report.status == ReportStatus.NEW
        assert "нет области модерации" in response.content.decode()
        assert not actions_for(report).exists()
        assert mail.outbox == []


class TestEmailHelpers:
    """Два правила рядом: человеку — исключение, модераторам — ноль."""

    def test_send_user_email_raises(self, user, monkeypatch):
        def broken_send_mail(*args, **kwargs):
            raise OSError("smtp is down")

        monkeypatch.setattr("apps.core.emails.send_mail", broken_send_mail)

        with pytest.raises(OSError):
            send_user_email(
                subject="Тема",
                template="moderation/email/report_decision.txt",
                context={
                    "target": "—",
                    "resolution": "текст",
                    "contact_email": "info@sportmemo.ru",
                },
                recipient=user.email,
            )

    def test_notify_moderators_swallows_the_same_error(self, moderator, monkeypatch):
        def broken_send_mail(*args, **kwargs):
            raise OSError("smtp is down")

        monkeypatch.setattr("apps.core.emails.send_mail", broken_send_mail)

        sent = notify_moderators(
            subject="Тема",
            template="moderation/email/report_decision.txt",
            context={
                "target": "—",
                "resolution": "текст",
                "contact_email": "info@sportmemo.ru",
            },
        )

        assert sent == 0
        assert mail.outbox == []


class TestMailCheckCommand:
    """Команда проверки почты: печатает настройки и умеет отправить."""

    def test_prints_settings_without_sending(self):
        out = io.StringIO()

        call_command("mail_check", stdout=out)

        text = out.getvalue()
        assert settings.EMAIL_HOST in text
        assert settings.DEFAULT_FROM_EMAIL in text
        assert mail.outbox == []

    def test_sends_probe_when_address_is_given(self):
        out = io.StringIO()

        call_command("mail_check", "probe@example.com", stdout=out)

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["probe@example.com"]
        assert "Письмо отправлено" in out.getvalue()

    def test_password_never_printed(self, settings):
        """Команду запускают на сервере: вывод попадает в логи."""
        settings.EMAIL_HOST_PASSWORD = "super-secret-value"
        out = io.StringIO()

        call_command("mail_check", stdout=out)

        assert "super-secret-value" not in out.getvalue()
        assert "задан" in out.getvalue()


class TestEnvExample:
    """Пример окружения — документация, которую надо проверять тестом."""

    def read_example(self):
        return (settings.BASE_DIR / ".env.example").read_text(encoding="utf-8")

    def test_from_address_is_info(self):
        assert "DEFAULT_FROM_EMAIL=SportMemo <info@sportmemo.ru>" in self.read_example()

    def test_api_key_user_is_empty(self):
        """Дефект «в примере снова noreply» должен ловиться тестом.

        `noreply@` ищется только в значении переменной: комментарий
        объясняет, почему ящика нет, и упоминание там законно.
        """
        text = self.read_example()

        assert "DEFAULT_FROM_EMAIL=noreply@" not in text
        assert "\nEMAIL_HOST_USER=\n" in text
