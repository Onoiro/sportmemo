"""Тесты создания и правки события пользователем.

Проверяем поведение, а не вёрстку: кто может создать событие, в каком
статусе оно появляется, кто его видит, что происходит при совпадении
названия и года и как работает возврат в форму загрузки фото.
"""

import pytest
from django.core import mail
from django.urls import reverse

from apps.catalog.forms import MAX_DESCRIPTION_LENGTH, MIN_FOUNDED_YEAR, EventForm
from apps.catalog.models import Event
from apps.core.models import Status

pytestmark = pytest.mark.django_db

CREATE_URL = "/events/new/"
MINE_URL = "/events/mine/"
LOGIN_URL = "/accounts/login/"
UPLOAD_URL = "/photos/upload/"


def log_in(client, user):
    client.force_login(user)


def update_url(event):
    return reverse("catalog:event_update", args=[event.slug])


def form_data(**overrides):
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


class TestEventSlug:
    """Slug события уникален в пределах вида спорта."""

    def test_slug_generated_from_name(self, baseball, user):
        event = Event.objects.create(
            sport=baseball, name="Кубок России", created_by=user
        )

        assert event.slug == "кубок-россии"

    def test_same_name_gets_numeric_suffix(self, baseball, user):
        """«Кубок России» проводится каждый год: без суффикса упадёт база."""
        Event.objects.create(sport=baseball, name="Кубок России", created_by=user)
        second = Event.objects.create(
            sport=baseball, name="Кубок России", year=1995, created_by=user
        )

        assert second.slug == "кубок-россии-2"

    def test_deleted_event_still_blocks_slug(self, baseball, user):
        """`UniqueConstraint` про `deleted_at` не знает."""
        first = Event.objects.create(
            sport=baseball, name="Кубок России", created_by=user
        )
        first.soft_delete()
        second = Event.objects.create(
            sport=baseball, name="Кубок России", created_by=user
        )

        assert second.slug == "кубок-россии-2"

    def test_same_slug_allowed_in_other_sport(self, baseball, user):
        from apps.core.models import Sport

        cricket = Sport.objects.create(name="Крикет", slug="cricket")
        first = Event.objects.create(
            sport=baseball, name="Кубок России", created_by=user
        )
        other = Event.objects.create(
            sport=cricket, name="Кубок России", created_by=user
        )

        assert other.slug == first.slug


class TestEventCreateAccess:
    def test_anonymous_is_redirected_to_login(self, client):
        response = client.get(CREATE_URL)

        assert response.status_code == 302
        assert response["Location"] == f"{LOGIN_URL}?next={CREATE_URL}"

    def test_logged_in_user_sees_form(self, client, user, baseball):
        log_in(client, user)

        response = client.get(CREATE_URL)

        assert response.status_code == 200
        assert "Новое событие" in response.content.decode()

    def test_my_events_requires_login(self, client):
        response = client.get(MINE_URL)

        assert response.status_code == 302
        assert response["Location"] == f"{LOGIN_URL}?next={MINE_URL}"

    def test_create_rejects_get_only_verbs(self, client, user, baseball):
        log_in(client, user)

        assert client.put(CREATE_URL).status_code == 405
        assert client.delete(CREATE_URL).status_code == 405

    def test_no_active_sport_shows_message(self, client, user, baseball):
        baseball.is_active = False
        baseball.save(update_fields=["is_active"])
        log_in(client, user)

        response = client.get(CREATE_URL)

        assert "Создание событий пока недоступно" in response.content.decode()


class TestEventCreate:
    def test_creates_draft_owned_by_user(self, client, user, baseball):
        log_in(client, user)

        response = client.post(CREATE_URL, form_data())

        event = Event.objects.get(name="Кубок России")
        assert response.status_code == 302
        assert response["Location"] == event.get_absolute_url()
        assert event.status == Status.DRAFT
        assert event.created_by == user
        assert event.sport == baseball

    def test_year_is_filled_from_start_date(self, client, user, baseball):
        """Числа и месяцы в архиве известны редко, а год — почти всегда."""
        log_in(client, user)

        client.post(CREATE_URL, form_data(year="", date_from="1994-05-01"))

        assert Event.objects.get(name="Кубок России").year == 1994

    def test_notification_letter_is_sent(self, client, user, moderator, baseball):
        log_in(client, user)

        client.post(CREATE_URL, form_data())

        assert len(mail.outbox) == 1
        assert moderator.email in mail.outbox[0].to
        assert "Кубок России" in mail.outbox[0].body

    def test_letter_is_not_sent_without_moderators(self, client, user, baseball):
        log_in(client, user)

        client.post(CREATE_URL, form_data())

        assert mail.outbox == []

    def test_smtp_failure_does_not_break_creation(
        self, client, user, moderator, baseball, monkeypatch
    ):
        log_in(client, user)

        def broken_send_mail(*args, **kwargs):
            raise OSError("SMTP недоступен")

        monkeypatch.setattr("apps.core.emails.send_mail", broken_send_mail)

        response = client.post(CREATE_URL, form_data())

        assert response.status_code == 302
        assert Event.objects.filter(name="Кубок России").exists()

    def test_name_query_prefills_field(self, client, user, baseball):
        log_in(client, user)

        html = client.get(CREATE_URL, {"name": "Кубок России"}).content.decode()

        assert 'value="Кубок России"' in html

    def test_name_query_is_escaped(self, client, user, baseball):
        log_in(client, user)

        html = client.get(CREATE_URL, {"name": '"><script>x</script>'}).content.decode()

        assert "<script>x</script>" not in html


class TestEventCreateVisibility:
    def test_draft_is_hidden_from_public_list(self, client, user, baseball):
        log_in(client, user)
        client.post(CREATE_URL, form_data())

        response = client.get(reverse("catalog:event_list"))

        assert "Кубок России" not in response.content.decode()

    def test_draft_is_not_visible_to_stranger(self, client, user, other_user, baseball):
        log_in(client, user)
        client.post(CREATE_URL, form_data())
        event = Event.objects.get(name="Кубок России")
        client.force_login(other_user)

        assert client.get(event.get_absolute_url()).status_code == 404

    def test_author_sees_draft_with_notice(self, client, user, baseball):
        log_in(client, user)
        client.post(CREATE_URL, form_data())
        event = Event.objects.get(name="Кубок России")

        response = client.get(event.get_absolute_url())

        assert response.status_code == 200
        assert "Не опубликовано" in response.content.decode()

    def test_moderator_sees_draft(self, client, user, moderator, baseball):
        log_in(client, user)
        client.post(CREATE_URL, form_data())
        event = Event.objects.get(name="Кубок России")
        client.force_login(moderator)

        assert client.get(event.get_absolute_url()).status_code == 200


class TestDuplicateEvent:
    """Дубликат — то же название **и** тот же год."""

    def _make(self, baseball, user, **kwargs):
        data = {"sport": baseball, "name": "Кубок России", "created_by": user}
        data.update(kwargs)
        return Event.objects.create(**data)

    def test_same_name_and_year_warns(self, baseball, user):
        self._make(baseball, user, year=1994)

        form = EventForm(data=form_data(year=1994), sport=baseball)

        assert not form.is_valid()
        assert form.duplicates.count() == 1
        assert "уже есть в архиве" in str(form.errors["name"][0])

    def test_same_name_other_year_is_not_a_duplicate(self, baseball, user):
        """Ежегодный турнир — нормальные разные записи."""
        self._make(baseball, user, year=1994)

        form = EventForm(data=form_data(year=1995), sport=baseball)

        assert form.is_valid(), form.errors
        assert form.duplicates.count() == 0

    def test_year_from_date_counts_for_duplicates(self, baseball, user):
        self._make(baseball, user, year=1994)

        form = EventForm(
            data=form_data(year="", date_from="1994-05-01"), sport=baseball
        )

        assert not form.is_valid()
        assert form.duplicates.count() == 1

    def test_post_with_confirmation_creates(self, client, user, baseball):
        self._make(baseball, user, year=1994)
        log_in(client, user)

        response = client.post(CREATE_URL, form_data(year=1994, confirm_duplicate="on"))

        assert response.status_code == 302
        assert Event.objects.filter(name="Кубок России").count() == 2

    def test_post_without_confirmation_does_not_create(self, client, user, baseball):
        self._make(baseball, user, year=1994)
        log_in(client, user)

        response = client.post(CREATE_URL, form_data(year=1994))

        assert response.status_code == 200
        assert Event.objects.filter(name="Кубок России").count() == 1

    def test_own_event_is_not_a_duplicate(self, client, baseball, user):
        event = self._make(baseball, user, year=1994)
        log_in(client, user)

        form = EventForm(data=form_data(year=1994), instance=event, sport=baseball)

        assert form.is_valid(), form.errors

    def test_deleted_event_does_not_warn(self, baseball, user):
        event = self._make(baseball, user, year=1994)
        event.soft_delete()

        form = EventForm(data=form_data(year=1994), sport=baseball)

        assert form.is_valid(), form.errors


class TestEventFormValidation:
    def test_year_out_of_range_is_rejected(self, baseball):
        form = EventForm(data=form_data(year=MIN_FOUNDED_YEAR - 1), sport=baseball)

        assert not form.is_valid()
        assert "Год должен быть между" in str(form.errors["year"][0])

    def test_end_date_before_start_is_rejected(self, baseball):
        form = EventForm(
            data=form_data(date_from="1994-05-01", date_to="1994-04-01"),
            sport=baseball,
        )

        assert not form.is_valid()
        assert "Дата окончания раньше" in str(form.errors["date_to"][0])

    def test_too_long_description_is_rejected(self, baseball):
        form = EventForm(
            data=form_data(description="я" * (MAX_DESCRIPTION_LENGTH + 1)),
            sport=baseball,
        )

        assert not form.is_valid()
        assert "не должно быть длиннее" in str(form.errors["description"][0])

    def test_name_help_text_explains_year_rule(self, baseball):
        form = EventForm(sport=baseball)

        assert "отдельные" in str(form.fields["name"].help_text)


class TestEventUpdate:
    def test_author_can_edit(self, client, event):
        log_in(client, event.created_by)

        response = client.get(update_url(event))

        assert response.status_code == 200
        assert "Редактирование события" in response.content.decode()

    def test_moderator_can_edit(self, client, event, moderator):
        log_in(client, moderator)

        assert client.get(update_url(event)).status_code == 200

    def test_stranger_gets_404(self, client, event, other_user):
        log_in(client, other_user)

        assert client.get(update_url(event)).status_code == 404

    def test_anonymous_is_redirected_to_login(self, client, event):
        response = client.get(update_url(event))

        assert response.status_code == 302
        assert response["Location"].startswith(LOGIN_URL)

    def test_post_saves_changes(self, client, event):
        log_in(client, event.created_by)

        response = client.post(
            update_url(event),
            form_data(name="Кубок России 1994", year=1994),
        )

        event.refresh_from_db()
        assert response.status_code == 302
        assert event.name == "Кубок России 1994"

    def test_publication_status_is_kept(self, client, event):
        """Правка автора не должна снимать событие с сайта."""
        event.publish()
        log_in(client, event.created_by)

        client.post(update_url(event), form_data(name="Кубок России", year=1994))

        event.refresh_from_db()
        assert event.status == Status.PUBLISHED

    def test_renaming_keeps_existing_slug(self, client, event):
        slug = event.slug
        log_in(client, event.created_by)

        client.post(update_url(event), form_data(name="Новое название", year=1994))

        event.refresh_from_db()
        assert event.slug == slug

    def test_deleted_event_cannot_be_edited_by_author(self, client, event):
        event.soft_delete()
        log_in(client, event.created_by)

        assert client.get(update_url(event)).status_code == 404

    def test_page_is_noindex(self, client, event):
        log_in(client, event.created_by)

        html = client.get(update_url(event)).content.decode()

        assert 'content="noindex, nofollow"' in html


class TestMyEvents:
    def test_shows_only_own_events(self, client, user, other_user, baseball):
        Event.objects.create(
            sport=baseball, name="Моё событие", created_by=user, year=1994
        )
        Event.objects.create(
            sport=baseball, name="Чужое событие", created_by=other_user, year=1995
        )
        log_in(client, user)

        html = client.get(MINE_URL).content.decode()

        assert "Моё событие" in html
        assert "Чужое событие" not in html

    def test_deleted_event_stays_in_list(self, client, user, baseball):
        event = Event.objects.create(
            sport=baseball, name="Удалённое событие", created_by=user, year=1994
        )
        event.soft_delete()
        log_in(client, user)

        html = client.get(MINE_URL).content.decode()

        assert "Удалённое событие" in html

    def test_empty_list_offers_creation(self, client, user, baseball):
        log_in(client, user)

        html = client.get(MINE_URL).content.decode()

        assert "Вы ещё не создавали событий" in html
        assert CREATE_URL in html

    def test_page_is_noindex(self, client, user, baseball):
        log_in(client, user)

        html = client.get(MINE_URL).content.decode()

        assert 'content="noindex, nofollow"' in html


class TestEventCreateFromPhotoForm:
    """Событие заводят прямо из формы загрузки фото — как команду."""

    def test_returns_to_photo_form(self, client, user, baseball):
        log_in(client, user)

        response = client.post(CREATE_URL, form_data(next=UPLOAD_URL))

        event = Event.objects.get(name="Кубок России")
        assert response.status_code == 302
        assert response["Location"] == f"{UPLOAD_URL}?event={event.pk}"

    def test_photo_form_prefills_created_event(self, client, user, baseball):
        log_in(client, user)
        response = client.post(CREATE_URL, form_data(next=UPLOAD_URL))

        page = client.get(response["Location"])

        assert page.status_code == 200
        assert 'value="Кубок России (1994)"' in page.content.decode()

    def test_photo_form_links_to_event_create(self, client, user, baseball):
        log_in(client, user)

        html = client.get(UPLOAD_URL).content.decode()

        assert "Создать событие" in html
        assert f"{CREATE_URL}?next={UPLOAD_URL}" in html

    def test_event_list_offers_creation_to_logged_in(self, client, user, baseball):
        log_in(client, user)

        html = client.get(reverse("catalog:event_list")).content.decode()

        assert "Создать событие" in html

    def test_event_list_hides_creation_from_anonymous(self, client, baseball):
        html = client.get(reverse("catalog:event_list")).content.decode()

        assert "Создать событие" not in html

    def test_unknown_event_query_is_ignored(self, client, user, baseball):
        log_in(client, user)

        response = client.get(UPLOAD_URL, {"event": "999"})

        assert response.status_code == 200
