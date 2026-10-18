"""Тесты блока комментариев.

Проверяем поведение, а не вёрстку: кто может комментировать, в каком статусе
появляется комментарий, кто его видит, как работают ответы и защита
от дублей. Разметка меняется чаще, чем смысл страницы.
"""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from apps.comments.forms import MAX_COMMENT_LENGTH
from apps.comments.models import Comment, CommentSettings
from apps.core.models import Status
from tests.factories import CommentFactory, EventFactory, PhotoFactory, TeamFactory

pytestmark = pytest.mark.django_db

ADD_URL = "/comments/add/"
LOGIN_URL = "/accounts/login/"


def add_url():
    return reverse("comments:add")


def comment_data(target_obj, key, **overrides):
    data = {
        "target": key,
        "object_id": target_obj.pk,
        "text": "Хорошее фото!",
    }
    data.update(overrides)
    return data


def enable_premoderation():
    settings_obj = CommentSettings.load()
    settings_obj.premoderation = True
    settings_obj.save()


class TestTargets:
    """Белый список целей: generic FK сам по себе ничего не ограничивает."""

    def test_unknown_target_is_not_found(self, client, user):
        client.force_login(user)

        response = client.post(
            add_url(), {"target": "user", "object_id": user.pk, "text": "Текст"}
        )

        assert response.status_code == 404

    def test_comment_is_attached_to_photo(self, client, user):
        photo = PhotoFactory()
        client.force_login(user)

        client.post(add_url(), comment_data(photo, "photo"))

        assert Comment.objects.filter(object_id=photo.pk).exists()

    def test_comment_is_attached_to_team(self, client, user):
        team = TeamFactory()
        team.publish()
        client.force_login(user)

        client.post(add_url(), comment_data(team, "team"))

        assert Comment.objects.filter(object_id=team.pk).exists()

    def test_comment_is_attached_to_event(self, client, user):
        event = EventFactory()
        event.publish()
        client.force_login(user)

        client.post(add_url(), comment_data(event, "event"))

        assert Comment.objects.filter(object_id=event.pk).exists()

    def test_draft_target_is_not_found(self, client, user):
        team = TeamFactory(status=Status.DRAFT)
        client.force_login(user)

        response = client.post(add_url(), comment_data(team, "team"))

        assert response.status_code == 404

    def test_author_may_comment_on_own_draft(self, client, user):
        team = TeamFactory(status=Status.DRAFT, created_by=user)
        client.force_login(user)

        response = client.post(add_url(), comment_data(team, "team"))

        assert response.status_code == 302


class TestCommentAccess:
    def test_anonymous_is_redirected_to_login(self, client, photo):
        response = client.post(add_url(), comment_data(photo, "photo"))

        assert response.status_code == 302
        assert response["Location"].startswith(LOGIN_URL)

    def test_get_is_not_allowed(self, client, user):
        client.force_login(user)

        response = client.get(add_url())

        assert response.status_code == 405


class TestCommentPublication:
    def test_comment_is_published_when_premoderation_is_off(self, client, user, photo):
        client.force_login(user)

        client.post(add_url(), comment_data(photo, "photo"))

        comment = Comment.objects.get()
        assert comment.status == Status.PUBLISHED

    def test_comment_is_draft_when_premoderation_is_on(self, client, user, photo):
        enable_premoderation()
        client.force_login(user)

        client.post(add_url(), comment_data(photo, "photo"))

        comment = Comment.objects.get()
        assert comment.status == Status.DRAFT

    def test_premoderation_toggle_takes_effect_at_once(self, client, user, photo):
        """Кеш настройки сбрасывается при сохранении."""
        client.force_login(user)
        client.post(add_url(), comment_data(photo, "photo", text="Первый"))

        enable_premoderation()
        client.post(add_url(), comment_data(photo, "photo", text="Второй"))

        statuses = list(
            Comment.objects.order_by("created_at").values_list("status", flat=True)
        )
        assert statuses == [Status.PUBLISHED, Status.DRAFT]

    def test_success_message_is_shown(self, client, user, photo):
        client.force_login(user)

        response = client.post(add_url(), comment_data(photo, "photo"), follow=True)

        assert "Комментарий добавлен" in response.content.decode()

    def test_redirect_goes_to_next(self, client, user, photo):
        client.force_login(user)

        response = client.post(
            add_url(),
            comment_data(photo, "photo", next=photo.get_absolute_url()),
        )

        assert response["Location"] == photo.get_absolute_url()

    def test_foreign_next_is_replaced_by_target_page(self, client, user, photo):
        """Открытый редирект на чужой сайт — известная уязвимость фишинга."""
        client.force_login(user)

        response = client.post(
            add_url(),
            comment_data(photo, "photo", next="https://evil.example.com/"),
        )

        assert response["Location"] == photo.get_absolute_url()


class TestCommentValidation:
    def test_empty_text_is_rejected(self, client, user, photo):
        client.force_login(user)

        client.post(add_url(), comment_data(photo, "photo", text=""))

        assert not Comment.objects.exists()

    def test_whitespace_text_is_rejected(self, client, user, photo):
        client.force_login(user)

        client.post(add_url(), comment_data(photo, "photo", text="   "))

        assert not Comment.objects.exists()

    def test_too_long_text_is_rejected(self, client, user, photo):
        client.force_login(user)

        client.post(
            add_url(), comment_data(photo, "photo", text="а" * (MAX_COMMENT_LENGTH + 1))
        )

        assert not Comment.objects.exists()

    def test_text_is_trimmed(self, client, user, photo):
        client.force_login(user)

        client.post(add_url(), comment_data(photo, "photo", text="  С пробелами  "))

        assert Comment.objects.get().text == "С пробелами"


class TestDuplicateProtection:
    def test_same_text_twice_creates_one_comment(self, client, user, photo):
        client.force_login(user)

        client.post(add_url(), comment_data(photo, "photo"))
        client.post(add_url(), comment_data(photo, "photo"))

        assert Comment.objects.count() == 1

    def test_different_text_creates_two_comments(self, client, user, photo):
        client.force_login(user)

        client.post(add_url(), comment_data(photo, "photo", text="Первый"))
        client.post(add_url(), comment_data(photo, "photo", text="Второй"))

        assert Comment.objects.count() == 2

    def test_same_text_on_different_targets_is_allowed(self, client, user, photo):
        other = PhotoFactory()
        client.force_login(user)

        client.post(add_url(), comment_data(photo, "photo"))
        client.post(add_url(), comment_data(other, "photo"))

        assert Comment.objects.count() == 2

    def test_old_comment_does_not_block_new_one(self, client, user, photo):
        """Дубль считается только в пределах окна."""
        from django.utils import timezone

        client.force_login(user)
        comment = CommentFactory(
            author=user,
            object_id=photo.pk,
            text="Хорошее фото!",
            status=Status.PUBLISHED,
        )
        Comment.objects.filter(pk=comment.pk).update(
            created_at=timezone.now() - timezone.timedelta(hours=1)
        )

        client.post(add_url(), comment_data(photo, "photo"))

        assert Comment.objects.count() == 2


class TestReplies:
    def test_reply_is_attached_to_root(self, client, user, photo):
        client.force_login(user)
        root = CommentFactory(author=user, object_id=photo.pk, status=Status.PUBLISHED)

        client.post(
            add_url(), comment_data(photo, "photo", parent=root.pk, text="Ответ")
        )

        reply = Comment.objects.exclude(pk=root.pk).get()
        assert reply.parent_id == root.pk

    def test_reply_to_reply_is_attached_to_root(self, client, user, photo):
        """Ответы одноуровневые: дерево в разметке читается хуже списка."""
        client.force_login(user)
        root = CommentFactory(author=user, object_id=photo.pk, status=Status.PUBLISHED)
        reply = CommentFactory(
            author=user,
            object_id=photo.pk,
            parent=root,
            status=Status.PUBLISHED,
        )

        client.post(
            add_url(), comment_data(photo, "photo", parent=reply.pk, text="Ответ")
        )

        nested = Comment.objects.exclude(pk__in=[root.pk, reply.pk]).get()
        assert nested.parent_id == root.pk

    def test_reply_to_foreign_target_is_rejected(self, client, user, photo):
        other = PhotoFactory()
        client.force_login(user)
        root = CommentFactory(author=user, object_id=other.pk, status=Status.PUBLISHED)

        client.post(
            add_url(), comment_data(photo, "photo", parent=root.pk, text="Ответ")
        )

        assert Comment.objects.count() == 1

    def test_reply_to_hidden_comment_is_rejected(self, client, user, photo):
        client.force_login(user)
        root = CommentFactory(author=user, object_id=photo.pk, status=Status.HIDDEN)

        client.post(
            add_url(), comment_data(photo, "photo", parent=root.pk, text="Ответ")
        )

        assert Comment.objects.count() == 1

    def test_reply_to_unknown_comment_is_rejected(self, client, user, photo):
        client.force_login(user)

        client.post(
            add_url(), comment_data(photo, "photo", parent=999999, text="Ответ")
        )

        assert not Comment.objects.exists()


class TestHtmxResponse:
    def test_htmx_request_gets_partial(self, client, user, photo):
        client.force_login(user)

        response = client.post(
            add_url(),
            comment_data(photo, "photo"),
            headers={"HX-Request": "true"},
        )

        html = response.content.decode()
        assert response.status_code == 200
        assert "<html" not in html
        assert "Хорошее фото!" in html

    def test_htmx_response_clears_form(self, client, user, photo):
        client.force_login(user)

        response = client.post(
            add_url(),
            comment_data(photo, "photo"),
            headers={"HX-Request": "true"},
        )

        assert (
            "Хорошее фото!"
            not in response.content.decode().split('id="comment-text"')[-1]
        )

    def test_htmx_error_returns_form_with_errors(self, client, user, photo):
        client.force_login(user)

        response = client.post(
            add_url(),
            comment_data(photo, "photo", text=""),
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 200
        assert "Напишите текст комментария" in response.content.decode()


class TestCommentListOnPages:
    def test_photo_page_shows_published_comment(self, client, photo):
        CommentFactory(
            object_id=photo.pk, text="Видимый комментарий", status=Status.PUBLISHED
        )

        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Видимый комментарий" in html

    def test_photo_page_hides_draft_from_anonymous(self, client, photo):
        CommentFactory(
            object_id=photo.pk, text="Черновик модерации", status=Status.DRAFT
        )

        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Черновик модерации" not in html

    def test_author_sees_own_draft(self, client, user, photo):
        CommentFactory(
            author=user,
            object_id=photo.pk,
            text="Мой черновик",
            status=Status.DRAFT,
        )
        client.force_login(user)

        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Мой черновик" in html
        assert "на проверке" in html

    def test_author_does_not_see_hidden_comment(self, client, user, photo):
        """Скрытое — решение модератора, а не черновик: автору его не видно."""
        CommentFactory(
            author=user,
            object_id=photo.pk,
            text="Скрытое модератором",
            status=Status.HIDDEN,
        )
        client.force_login(user)

        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Скрытое модератором" not in html

    def test_author_does_not_see_rejected_comment(self, client, user, photo):
        CommentFactory(
            author=user,
            object_id=photo.pk,
            text="Отклонённое",
            status=Status.REJECTED,
        )
        client.force_login(user)

        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Отклонённое" not in html

    def test_deleted_comment_is_not_shown(self, client, photo):
        comment = CommentFactory(
            object_id=photo.pk, text="Удалённое", status=Status.PUBLISHED
        )
        comment.soft_delete()

        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Удалённое" not in html

    def test_reply_is_shown_under_root(self, client, photo):
        root = CommentFactory(
            object_id=photo.pk, text="Корневой", status=Status.PUBLISHED
        )
        CommentFactory(
            object_id=photo.pk,
            text="Ответ",
            parent=root,
            status=Status.PUBLISHED,
        )

        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Корневой" in html
        assert "Ответ" in html

    def test_orphan_reply_is_not_shown(self, client, photo):
        """Ответ скрытого корня висел бы без контекста."""
        root = CommentFactory(
            object_id=photo.pk, text="Скрытый корень", status=Status.HIDDEN
        )
        CommentFactory(
            object_id=photo.pk,
            text="Висячий ответ",
            parent=root,
            status=Status.PUBLISHED,
        )

        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Висячий ответ" not in html

    def test_team_page_shows_comments(self, client, team):
        team.publish()
        CommentFactory(
            object_id=team.pk,
            text="Комментарий к команде",
            status=Status.PUBLISHED,
            content_type=ContentType.objects.get_for_model(team),
        )

        html = client.get(team.get_absolute_url()).content.decode()

        assert "Комментарий к команде" in html

    def test_event_page_shows_comments(self, client, event):
        event.publish()
        CommentFactory(
            object_id=event.pk,
            text="Комментарий к событию",
            status=Status.PUBLISHED,
            content_type=ContentType.objects.get_for_model(event),
        )

        html = client.get(event.get_absolute_url()).content.decode()

        assert "Комментарий к событию" in html

    def test_person_page_has_no_comments(self, client, person):
        person.publish()

        html = client.get(person.get_absolute_url()).content.decode()

        assert 'id="comments-block"' not in html

    def test_comment_of_other_object_is_not_shown(self, client, photo):
        other = PhotoFactory()
        CommentFactory(
            object_id=other.pk, text="Чужой комментарий", status=Status.PUBLISHED
        )

        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Чужой комментарий" not in html


class TestCommentBlockForAnonymous:
    def test_anonymous_sees_login_invitation(self, client, photo):
        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Войти и написать" in html

    def test_anonymous_has_no_form(self, client, photo):
        html = client.get(photo.get_absolute_url()).content.decode()

        assert 'id="comment-text"' not in html

    def test_logged_in_user_sees_form(self, client, user, photo):
        client.force_login(user)

        html = client.get(photo.get_absolute_url()).content.decode()

        assert 'id="comment-text"' in html

    def test_empty_state_is_shown(self, client, photo):
        html = client.get(photo.get_absolute_url()).content.decode()

        assert "Комментариев пока нет" in html


class TestSettingsModel:
    def test_default_is_off(self, db):
        assert CommentSettings.load().premoderation is False

    def test_settings_row_is_created_by_migration(self, db):
        assert CommentSettings.objects.count() == 1

    def test_load_returns_default_without_row(self, db):
        CommentSettings.objects.all().delete()
        from django.core.cache import cache

        cache.clear()

        assert CommentSettings.load().premoderation is False

    def test_cache_is_reset_on_save(self, db):
        settings_obj = CommentSettings.load()
        settings_obj.premoderation = True
        settings_obj.save()

        assert CommentSettings.load().premoderation is True


class TestSettingsAdmin:
    """Настройка — одна строка: список ведёт на форму, добавить нельзя."""

    def test_changelist_redirects_to_change_form(self, client, admin_user):
        client.force_login(admin_user)
        settings_obj = CommentSettings.load()

        response = client.get(reverse("admin:comments_commentsettings_changelist"))

        assert response.status_code == 302
        assert response["Location"] == reverse(
            "admin:comments_commentsettings_change", args=[settings_obj.pk]
        )

    def test_add_page_is_forbidden(self, client, admin_user):
        client.force_login(admin_user)

        response = client.get(reverse("admin:comments_commentsettings_add"))

        assert response.status_code == 403

    def test_delete_is_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        settings_obj = CommentSettings.load()

        response = client.get(
            reverse("admin:comments_commentsettings_delete", args=[settings_obj.pk])
        )

        assert response.status_code == 403

    def test_toggle_through_admin_form(self, client, admin_user):
        client.force_login(admin_user)
        settings_obj = CommentSettings.load()

        client.post(
            reverse("admin:comments_commentsettings_change", args=[settings_obj.pk]),
            {"premoderation": "on"},
        )

        assert CommentSettings.load().premoderation is True
