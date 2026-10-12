"""Тесты создания и правки команды пользователем.

Проверяем поведение, а не вёрстку: кто может создать команду, в каком статусе
она появляется, кто её видит, что происходит при совпадении названий и как
работают ограничения полей.
"""

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.catalog.forms import (
    ALLOWED_LOGO_EXTENSIONS,
    MAX_DESCRIPTION_LENGTH,
    MAX_TEAM_LOGO_SIZE,
    MIN_FOUNDED_YEAR,
    TeamForm,
)
from apps.catalog.models import Team
from apps.core.models import Status
from tests.utils import make_image_file

pytestmark = pytest.mark.django_db

CREATE_URL = "/teams/new/"
MINE_URL = "/teams/mine/"
LOGIN_URL = "/accounts/login/"


def log_in(client, user):
    """Войти без подтверждения адреса: тестам важен факт входа."""
    client.force_login(user)


def update_url(team):
    return reverse("catalog:team_update", args=[team.slug])


def form_data(**overrides):
    """Минимальный набор полей формы команды."""
    data = {
        "name": "Русстар",
        "city": "Москва",
        "founded_year": 1990,
        "description": "Клуб из Москвы.",
    }
    data.update(overrides)
    return data


class TestTeamCreateAccess:
    def test_anonymous_is_redirected_to_login(self, client):
        response = client.get(CREATE_URL)

        assert response.status_code == 302
        assert response["Location"] == f"{LOGIN_URL}?next={CREATE_URL}"

    def test_logged_in_user_sees_form(self, client, user):
        log_in(client, user)

        response = client.get(CREATE_URL)

        assert response.status_code == 200
        assert "Новая команда" in response.content.decode()

    def test_my_teams_requires_login(self, client):
        response = client.get(MINE_URL)

        assert response.status_code == 302
        assert response["Location"] == f"{LOGIN_URL}?next={MINE_URL}"

    def test_create_rejects_get_only_verbs(self, client, user):
        log_in(client, user)

        assert client.put(CREATE_URL).status_code == 405
        assert client.delete(CREATE_URL).status_code == 405


class TestTeamCreate:
    def test_creates_draft_owned_by_user(self, client, user, baseball):
        log_in(client, user)

        response = client.post(CREATE_URL, form_data())

        team = Team.objects.get(name="Русстар")
        assert response.status_code == 302
        assert response["Location"] == team.get_absolute_url()
        assert team.status == Status.DRAFT
        assert team.created_by == user
        assert team.sport == baseball

    def test_sport_comes_from_default(self, client, user, baseball):
        log_in(client, user)

        client.post(CREATE_URL, form_data(name="Соколы"))

        assert Team.objects.get(name="Соколы").sport == baseball

    def test_notification_letter_is_sent(self, client, user, moderator):
        log_in(client, user)

        client.post(CREATE_URL, form_data())

        assert len(mail.outbox) == 1
        assert moderator.email in mail.outbox[0].to
        assert "Русстар" in mail.outbox[0].body

    def test_letter_is_not_sent_without_moderators(self, client, user, baseball):
        """Модератора ещё нет — это норма: команда всё равно создаётся."""
        log_in(client, user)

        response = client.post(CREATE_URL, form_data())

        assert response.status_code == 302
        assert Team.objects.filter(name="Русстар").exists()
        assert mail.outbox == []

    def test_smtp_failure_does_not_break_creation(
        self, client, user, moderator, monkeypatch
    ):
        log_in(client, user)

        def broken_send_mail(*args, **kwargs):
            raise OSError("SMTP недоступен")

        monkeypatch.setattr("apps.catalog.emails.send_mail", broken_send_mail)

        response = client.post(CREATE_URL, form_data())

        assert response.status_code == 302
        assert Team.objects.filter(name="Русстар").exists()

    def test_no_active_sport_shows_message(self, client, user, baseball):
        baseball.is_active = False
        baseball.save(update_fields=["is_active"])
        log_in(client, user)

        response = client.get(CREATE_URL)

        html = response.content.decode()
        assert response.status_code == 200
        assert "Создание команд пока недоступно" in html

    def test_no_active_sport_does_not_create(self, client, user, baseball):
        baseball.is_active = False
        baseball.save(update_fields=["is_active"])
        log_in(client, user)

        response = client.post(CREATE_URL, form_data())

        assert response.status_code == 200
        assert not Team.objects.filter(name="Русстар").exists()


class TestTeamCreateVisibility:
    def test_draft_is_hidden_from_public_list(self, client, user):
        log_in(client, user)
        client.post(CREATE_URL, form_data())

        response = client.get(reverse("catalog:team_list"))

        assert "Русстар" not in response.content.decode()

    def test_draft_is_not_visible_to_stranger(self, client, user, other_user):
        log_in(client, user)
        client.post(CREATE_URL, form_data())
        team = Team.objects.get(name="Русстар")
        client.force_login(other_user)

        response = client.get(team.get_absolute_url())

        assert response.status_code == 404

    def test_author_sees_draft_with_notice(self, client, user):
        log_in(client, user)
        client.post(CREATE_URL, form_data())
        team = Team.objects.get(name="Русстар")

        response = client.get(team.get_absolute_url())

        assert response.status_code == 200
        assert "Не опубликовано" in response.content.decode()

    def test_moderator_sees_draft(self, client, user, moderator):
        log_in(client, user)
        client.post(CREATE_URL, form_data())
        team = Team.objects.get(name="Русстар")
        client.force_login(moderator)

        response = client.get(team.get_absolute_url())

        assert response.status_code == 200


class TestDuplicateName:
    def test_form_finds_existing_team(self, team):
        """Форма находит команду с тем же названием в том же виде спорта."""
        form = TeamForm(data=form_data(name="Динамо"), sport=team.sport)

        assert not form.is_valid()
        assert form.duplicates.count() == 1

    def test_form_ignores_other_sport(self, team):
        from apps.core.models import Sport

        cricket = Sport.objects.create(name="Крикет", slug="cricket")
        form = TeamForm(data=form_data(name="Динамо"), sport=cricket)

        assert form.is_valid()
        assert form.duplicates.count() == 0

    def test_post_without_confirmation_does_not_create(self, client, user, team):
        log_in(client, user)

        response = client.post(CREATE_URL, form_data(name="Динамо"))

        assert response.status_code == 200
        assert Team.objects.filter(name="Динамо").count() == 1
        assert "уже есть в архиве" in response.content.decode()

    def test_post_with_confirmation_creates(self, client, user, team):
        log_in(client, user)

        response = client.post(
            CREATE_URL, form_data(name="Динамо", confirm_duplicate="on")
        )

        assert response.status_code == 302
        assert Team.objects.filter(name="Динамо").count() == 2

    def test_draft_of_another_user_also_warns(self, client, user, other_user, baseball):
        """Чужой черновик тоже повод предупредить: иначе будут дубликаты."""
        Team.objects.create(sport=baseball, name="Соколы", created_by=other_user)
        log_in(client, user)

        response = client.post(CREATE_URL, form_data(name="Соколы"))

        assert response.status_code == 200
        assert "ожидает проверки модератором" in response.content.decode()

    def test_deleted_team_does_not_warn(self, client, user, team):
        team.soft_delete(reason="проверка")
        log_in(client, user)

        response = client.post(
            CREATE_URL, form_data(name="Динамо", confirm_duplicate="on")
        )

        assert response.status_code == 302

    def test_published_duplicate_is_a_link(self, client, user, team):
        team.publish()
        log_in(client, user)

        response = client.post(CREATE_URL, form_data(name="Динамо"))

        assert team.get_absolute_url() in response.content.decode()

    def test_own_team_is_not_a_duplicate(self, client, team):
        """Своя команда в списке дубликатов не появляется."""
        log_in(client, team.created_by)

        response = client.get(update_url(team))

        assert response.status_code == 200
        assert "уже есть в архиве" not in response.content.decode()


class TestTeamFormValidation:
    def test_founded_year_before_minimum_is_rejected(self, baseball):
        form = TeamForm(
            data=form_data(founded_year=MIN_FOUNDED_YEAR - 1), sport=baseball
        )

        assert not form.is_valid()
        assert "founded_year" in form.errors

    def test_founded_year_in_future_is_rejected(self, baseball):
        form = TeamForm(
            data=form_data(founded_year=timezone.localdate().year + 1), sport=baseball
        )

        assert not form.is_valid()
        assert "founded_year" in form.errors

    def test_founded_year_can_be_empty(self, baseball):
        form = TeamForm(data=form_data(founded_year=""), sport=baseball)

        assert form.is_valid()

    def test_description_limit(self, baseball):
        form = TeamForm(
            data=form_data(description="а" * (MAX_DESCRIPTION_LENGTH + 1)),
            sport=baseball,
        )

        assert not form.is_valid()
        assert "description" in form.errors

    def test_logo_size_limit(self, baseball):
        from django.core.files.uploadedfile import SimpleUploadedFile

        big = SimpleUploadedFile(
            "logo.jpg", b"x" * (MAX_TEAM_LOGO_SIZE + 1), content_type="image/jpeg"
        )
        form = TeamForm(data=form_data(), files={"logo": big}, sport=baseball)

        assert not form.is_valid()
        assert "logo" in form.errors

    def test_logo_extension_limit(self, baseball):
        from django.core.files.uploadedfile import SimpleUploadedFile

        wrong = SimpleUploadedFile(
            "logo.pdf", b"%PDF-1.4", content_type="application/pdf"
        )
        form = TeamForm(data=form_data(), files={"logo": wrong}, sport=baseball)

        assert not form.is_valid()
        assert "logo" in form.errors

    def test_logo_accepts_supported_format(self, baseball):
        form = TeamForm(
            data=form_data(),
            files={"logo": make_image_file("logo.png")},
            sport=baseball,
        )

        assert form.is_valid(), form.errors

    def test_allowed_extensions_include_expected(self):
        assert set(ALLOWED_LOGO_EXTENSIONS) == {"jpg", "jpeg", "png", "webp"}


class TestTeamUpdate:
    def test_author_can_edit(self, client, team):
        log_in(client, team.created_by)

        response = client.post(update_url(team), form_data(name="Динамо Москва"))

        team.refresh_from_db()
        assert response.status_code == 302
        assert team.name == "Динамо Москва"

    def test_stranger_gets_404(self, client, team, other_user):
        client.force_login(other_user)

        assert client.get(update_url(team)).status_code == 404

    def test_stranger_cannot_save(self, client, team, other_user):
        client.force_login(other_user)

        response = client.post(update_url(team), form_data(name="Взлом"))

        team.refresh_from_db()
        assert response.status_code == 404
        assert team.name == "Динамо"

    def test_moderator_can_edit(self, client, team, moderator):
        client.force_login(moderator)

        assert client.get(update_url(team)).status_code == 200

    def test_anonymous_is_redirected(self, client, team):
        response = client.get(update_url(team))

        assert response.status_code == 302
        assert LOGIN_URL in response["Location"]

    def test_editing_published_team_keeps_status(self, client, team):
        team.publish()
        log_in(client, team.created_by)

        client.post(update_url(team), form_data(city="Тверь"))

        team.refresh_from_db()
        assert team.status == Status.PUBLISHED
        assert team.city == "Тверь"

    def test_editing_writes_history(self, client, team):
        log_in(client, team.created_by)
        before = team.history.count()

        client.post(update_url(team), form_data(city="Тверь"))

        assert team.history.count() > before

    def test_deleted_team_cannot_be_edited(self, client, team):
        team.soft_delete(reason="проверка")
        log_in(client, team.created_by)

        assert client.get(update_url(team)).status_code == 404

    def test_slug_is_kept_after_rename(self, client, team):
        slug = team.slug
        log_in(client, team.created_by)

        client.post(update_url(team), form_data(name="Совсем другое название"))

        team.refresh_from_db()
        assert team.slug == slug


class TestMyTeams:
    def test_shows_only_own_teams(self, client, user, other_user, baseball):
        Team.objects.create(sport=baseball, name="Моя", created_by=user)
        Team.objects.create(sport=baseball, name="Чужая", created_by=other_user)
        log_in(client, user)

        html = client.get(MINE_URL).content.decode()

        assert "Моя" in html
        assert "Чужая" not in html

    def test_shows_status_and_edit_link(self, client, team):
        log_in(client, team.created_by)

        html = client.get(MINE_URL).content.decode()

        assert team.get_status_display() in html
        assert update_url(team) in html

    def test_empty_state_has_create_button(self, client, user):
        log_in(client, user)

        html = client.get(MINE_URL).content.decode()

        assert "Вы ещё не создавали команд" in html
        assert CREATE_URL in html

    def test_deleted_team_is_still_listed(self, client, team):
        """Убранную модератором команду автор должен видеть, а не терять."""
        team.soft_delete(reason="проверка")
        log_in(client, team.created_by)

        html = client.get(MINE_URL).content.decode()

        assert team.name in html


class TestNavigationLinks:
    def test_logged_in_menu_has_my_teams(self, client, user):
        log_in(client, user)

        html = client.get(reverse("core:home")).content.decode()

        assert MINE_URL in html

    def test_anonymous_menu_has_no_my_teams(self, client):
        html = client.get(reverse("core:home")).content.decode()

        assert MINE_URL not in html

    def test_team_list_has_create_button_for_logged_in(self, client, user):
        log_in(client, user)

        html = client.get(reverse("catalog:team_list")).content.decode()

        assert CREATE_URL in html

    def test_team_list_has_no_create_button_for_anonymous(self, client):
        html = client.get(reverse("catalog:team_list")).content.decode()

        assert CREATE_URL not in html

    def test_team_page_has_edit_button_for_author(self, client, team):
        log_in(client, team.created_by)

        html = client.get(team.get_absolute_url()).content.decode()

        assert update_url(team) in html

    def test_team_page_has_no_edit_button_for_stranger(self, client, team, other_user):
        team.publish()
        client.force_login(other_user)

        html = client.get(team.get_absolute_url()).content.decode()

        assert update_url(team) not in html
