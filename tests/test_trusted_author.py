"""Пост-модерация доверенного автора: кто публикует сразу, кто ждёт.

Проверяем поведение, а не вёрстку: статус в трёх точках создания, честный
текст письма модераторам и независимость двух способностей — «доверенный
автор» и «модератор».
"""

import pytest
from django.core import mail
from django.urls import reverse

from apps.catalog.models import Event, Team
from apps.core.models import Status
from apps.core.shortcuts import visible_photos
from apps.moderation.services import initial_status_for
from apps.photos.models import Photo
from tests.factories import PhotoFactory
from tests.utils import make_upload

pytestmark = pytest.mark.django_db

UPLOAD_URL = "/photos/upload/"
TEAM_CREATE_URL = "/teams/new/"
EVENT_CREATE_URL = "/events/new/"
MY_PHOTOS_URL = "/photos/mine/"
MY_TEAMS_URL = "/teams/mine/"
MY_EVENTS_URL = "/events/mine/"
RULES_URL = "/rules/"


def team_data(**overrides):
    """Минимальный набор полей формы команды."""
    data = {
        "name": "Русстар",
        "city": "Москва",
        "founded_year": 1990,
        "description": "Клуб из Москвы.",
    }
    data.update(overrides)
    return data


def event_data(**overrides):
    """Минимальный набор полей формы события."""
    data = {
        "name": "Кубок России",
        "event_type": "tournament",
        "year": 1994,
        "city": "Москва",
        "description": "Финальная серия.",
    }
    data.update(overrides)
    return data


class TestStatusRule:
    """Правило статуса: доверие — про человека, а не про роль."""

    def test_trusted_author_publishes_right_away(self, trusted_user):
        assert initial_status_for(trusted_user) == Status.PUBLISHED

    def test_plain_user_waits_for_a_moderator(self, user):
        assert initial_status_for(user) == Status.DRAFT

    def test_moderator_without_trust_waits(self, moderator):
        # Две способности независимы: модератор решает по чужому,
        # а своё публикует сразу только по флагу доверия.
        assert moderator.is_trusted is False
        assert initial_status_for(moderator) == Status.DRAFT

    def test_admin_without_trust_waits(self, admin_user):
        assert initial_status_for(admin_user) == Status.DRAFT

    def test_none_gives_draft(self):
        # Заглушка вместо пользователя не должна ронять проверку.
        assert initial_status_for(None) == Status.DRAFT


class TestPhotoStatus:
    """Первая точка создания: загрузка фото."""

    def test_trusted_upload_is_published_without_moderator(
        self, client, trusted_user, baseball
    ):
        client.force_login(trusted_user)

        client.post(UPLOAD_URL, {"original": make_upload()})

        photo = Photo.objects.get()
        assert photo.status == Status.PUBLISHED
        # Пустые поля модерации — и есть признак «без проверки»: `publish()`
        # здесь не вызывается, иначе появилась бы дата решения без решения.
        assert photo.moderated_by_id is None
        assert photo.moderated_at is None
        assert photo in visible_photos()

    def test_plain_upload_stays_draft(self, client, user, baseball):
        client.force_login(user)

        client.post(UPLOAD_URL, {"original": make_upload()})

        photo = Photo.objects.get()
        assert photo.status == Status.DRAFT
        assert photo not in visible_photos()


class TestTeamStatus:
    """Вторая точка создания: команда."""

    def test_trusted_author_gets_a_published_team(self, client, trusted_user, baseball):
        client.force_login(trusted_user)

        client.post(TEAM_CREATE_URL, team_data())

        team = Team.objects.get(name="Русстар")
        assert team.status == Status.PUBLISHED
        assert team.moderated_by_id is None
        assert team.is_auto_published is True

    def test_plain_author_gets_a_draft(self, client, user, baseball):
        client.force_login(user)

        client.post(TEAM_CREATE_URL, team_data())

        team = Team.objects.get(name="Русстар")
        assert team.status == Status.DRAFT
        assert team.is_auto_published is False

    def test_message_tells_the_trusted_author_about_publication(
        self, client, trusted_user, baseball
    ):
        client.force_login(trusted_user)

        response = client.post(TEAM_CREATE_URL, team_data(), follow=True)

        assert "Команда создана и опубликована." in response.content.decode()

    def test_message_does_not_promise_publication_to_others(
        self, client, user, baseball
    ):
        client.force_login(user)

        response = client.post(TEAM_CREATE_URL, team_data(), follow=True)

        html = response.content.decode()
        assert "Команда создана и отправлена на проверку модератору." in html
        assert "Команда создана и опубликована." not in html


class TestEventStatus:
    """Третья точка создания: событие."""

    def test_trusted_author_gets_a_published_event(
        self, client, trusted_user, baseball
    ):
        client.force_login(trusted_user)

        client.post(EVENT_CREATE_URL, event_data())

        event = Event.objects.get(name="Кубок России")
        assert event.status == Status.PUBLISHED
        assert event.moderated_by_id is None
        assert event.is_auto_published is True

    def test_plain_author_gets_a_draft(self, client, user, baseball):
        client.force_login(user)

        client.post(EVENT_CREATE_URL, event_data())

        event = Event.objects.get(name="Кубок России")
        assert event.status == Status.DRAFT

    def test_message_tells_the_trusted_author_about_publication(
        self, client, trusted_user, baseball
    ):
        client.force_login(trusted_user)

        response = client.post(EVENT_CREATE_URL, event_data(), follow=True)

        assert "Событие создано и опубликовано." in response.content.decode()

    def test_message_does_not_promise_publication_to_others(
        self, client, user, baseball
    ):
        client.force_login(user)

        response = client.post(EVENT_CREATE_URL, event_data(), follow=True)

        html = response.content.decode()
        assert "Событие создано и отправлено на проверку модератору." in html
        assert "Событие создано и опубликовано." not in html


class TestModeratorLetters:
    """Письмо уходит всегда, но текст говорит правду о статусе."""

    def test_trusted_team_letter_says_it_is_published(
        self, client, trusted_user, moderator
    ):
        client.force_login(trusted_user)

        client.post(TEAM_CREATE_URL, team_data())

        assert len(mail.outbox) == 1
        body = mail.outbox[0].body
        assert "опубликована сразу" in body
        # Пост-модерация работает, только если письмо не врёт: модератор
        # иначе искал бы в очереди объект, которого там нет.
        assert "ждёт проверки" not in body

    def test_plain_team_letter_says_it_waits_for_a_check(self, client, user, moderator):
        client.force_login(user)

        client.post(TEAM_CREATE_URL, team_data())

        assert len(mail.outbox) == 1
        body = mail.outbox[0].body
        assert "ждёт проверки" in body
        assert "опубликована сразу" not in body

    def test_trusted_event_letter_says_it_is_published(
        self, client, trusted_user, moderator
    ):
        client.force_login(trusted_user)

        client.post(EVENT_CREATE_URL, event_data())

        assert len(mail.outbox) == 1
        body = mail.outbox[0].body
        assert "опубликовано сразу" in body
        assert "ждёт проверки" not in body


class TestTwoIndependentAbilities:
    """Доверие не даёт прав модератора, а роль модератора не даёт доверия."""

    def test_trusted_author_gets_404_on_someone_elses_team_draft(
        self, client, trusted_user, user, baseball
    ):
        team = Team.objects.create(
            sport=baseball, name="Соколы", created_by=user, status=Status.DRAFT
        )
        client.force_login(trusted_user)

        response = client.get(team.get_absolute_url())

        assert response.status_code == 404

    def test_trusted_author_cannot_edit_someone_elses_team(
        self, client, trusted_user, user, baseball
    ):
        team = Team.objects.create(
            sport=baseball, name="Соколы", created_by=user, status=Status.DRAFT
        )
        client.force_login(trusted_user)

        response = client.get(reverse("catalog:team_update", args=[team.slug]))

        assert response.status_code == 404

    def test_trusted_author_gets_404_on_someone_elses_photo_draft(
        self, client, trusted_user, user
    ):
        photo = PhotoFactory(uploaded_by=user, status=Status.DRAFT)
        client.force_login(trusted_user)

        assert client.get(f"/photos/{photo.pk}/").status_code == 404
        assert client.get(f"/photos/{photo.pk}/edit/").status_code == 404

    def test_moderator_without_trust_creates_a_draft(self, client, moderator, baseball):
        client.force_login(moderator)

        client.post(TEAM_CREATE_URL, team_data())

        assert Team.objects.get(name="Русстар").status == Status.DRAFT


class TestAutoPublishedFlag:
    """Признак пост-модерации читается по решению, а не по дате."""

    def test_published_without_moderator(self):
        photo = PhotoFactory(status=Status.PUBLISHED, moderated_by=None)

        assert photo.is_auto_published is True

    def test_published_by_a_moderator(self, moderator):
        photo = PhotoFactory(status=Status.PUBLISHED)

        photo.publish(moderator=moderator)

        assert photo.is_auto_published is False

    def test_draft_is_not_auto_published(self):
        photo = PhotoFactory(status=Status.DRAFT)

        assert photo.is_auto_published is False

    def test_deleted_published_photo(self, moderator):
        # Удаление — отдельная ось (`deleted_at`), и публичную выдачу
        # отсекает она, а не признак решения.
        photo = PhotoFactory(status=Status.PUBLISHED)
        photo.publish(moderator=moderator)
        photo.soft_delete()

        assert photo.is_auto_published is False
        assert photo not in visible_photos()


class TestWording:
    """Тексты не обещают проверку каждому автору."""

    def test_cabinets_talk_about_selective_review(self, client, user):
        client.force_login(user)

        for url in (MY_PHOTOS_URL, MY_TEAMS_URL, MY_EVENTS_URL):
            html = client.get(url).content.decode()
            assert "Публикует" not in html
            assert "доверяем" in html

    def test_rules_page_talks_about_selective_review(self, client):
        html = client.get(RULES_URL).content.decode()

        assert "Проверка выборочная" in html
