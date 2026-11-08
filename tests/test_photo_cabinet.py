"""Кабинет автора: вкладки, уборка из списка и права на неё.

Главная проверка — паритет вкладок с `Photo.upload_state`: раскладка
состояний описана в двух местах (модель считает состояние, `cabinet.py`
собирает по нему выборку), и разойтись они могут молча. Один
параметризованный тест закрывает весь класс расхождений: фото не попадает
ни в одну вкладку или попадает сразу в две.

Уборка из списка — действие автора, а не решение модератора, поэтому
тесты держат их порознь: `deleted_at` автор не ставит, квота не
возвращается, публичная выдача не меняется.
"""

import pytest
from django.urls import reverse

from apps.core.models import Status
from apps.core.shortcuts import PHOTO_PAGE_SIZE, visible_photos
from apps.photos import cabinet, services
from apps.photos.models import ProcessingStatus
from tests.factories import PhotoFactory
from tests.utils import make_image_file

MY_PHOTOS_URL = "/photos/mine/"
LOGIN_URL = "/accounts/login/"

# Поля фабрики для каждого состояния плашки кабинета. Ключи — значения
# `Photo.upload_state`, и тест паритета сверяет их с вкладками.
STATE_FIELDS = {
    "review": {
        "status": Status.DRAFT,
        "processing_status": ProcessingStatus.PROCESSED,
    },
    "processing": {
        "status": Status.DRAFT,
        "processing_status": ProcessingStatus.PROCESSING,
    },
    "published": {
        "status": Status.PUBLISHED,
        "processing_status": ProcessingStatus.PROCESSED,
    },
    "hidden": {
        "status": Status.HIDDEN,
        "processing_status": ProcessingStatus.PROCESSED,
    },
    "rejected": {
        "status": Status.REJECTED,
        "processing_status": ProcessingStatus.PROCESSED,
    },
    "failed": {
        "status": Status.DRAFT,
        "processing_status": ProcessingStatus.FAILED,
    },
    "deleted": {
        "status": Status.DRAFT,
        "processing_status": ProcessingStatus.PROCESSED,
    },
}

# Раскладка из плана (таблица 2): «Не опубликованы» собирает всё, что
# не опубликовано и не ждёт проверки.
STATE_TAB = {
    "published": cabinet.TAB_PUBLISHED,
    "review": cabinet.TAB_REVIEW,
    "processing": cabinet.TAB_REVIEW,
    "rejected": cabinet.TAB_UNPUBLISHED,
    "hidden": cabinet.TAB_UNPUBLISHED,
    "failed": cabinet.TAB_UNPUBLISHED,
    "deleted": cabinet.TAB_UNPUBLISHED,
}


def _photo(user, **kwargs):
    """Фото автора в нужном состоянии.

    Обработанное фото — норма кабинета; тесты, которым нужна другая
    стадия, переопределяют `processing_status` явно.
    """
    kwargs.setdefault("processing_status", ProcessingStatus.PROCESSED)
    return PhotoFactory(
        uploaded_by=user, original=make_image_file("photo.jpg"), **kwargs
    )


def _state_photo(user, state):
    """Фото в заданном состоянии плашки."""
    photo = _photo(user, **STATE_FIELDS[state])
    if state == "deleted":
        photo.soft_delete(reason="Не по теме архива")
    return photo


def _tabs_with(user, photo):
    """Вкладки, в которых фото видно."""
    return {
        tab
        for tab in cabinet.TAB_ORDER
        if cabinet.cabinet_photos(user, tab).filter(pk=photo.pk).exists()
    }


class TestTabsMatchUploadState:
    """Раскладка состояний по вкладкам: одна проверка на весь класс."""

    @pytest.mark.django_db
    @pytest.mark.parametrize("state", sorted(STATE_TAB))
    def test_state_belongs_to_its_tab(self, user, state):
        photo = _state_photo(user, state)

        # Состояние собирается по порядку проверок в модели; если тест
        # построил не то фото, паритет проверял бы не то.
        assert photo.upload_state == state
        assert _tabs_with(user, photo) == {cabinet.TAB_ALL, STATE_TAB[state]}

    @pytest.mark.django_db
    def test_three_tabs_add_up_to_all(self, user):
        """Сумма вкладок — не дисциплина, а арифметика выборки."""
        for state in STATE_TAB:
            _state_photo(user, state)

        counts = cabinet.cabinet_counts(user)

        assert (
            counts[cabinet.TAB_PUBLISHED]
            + counts[cabinet.TAB_REVIEW]
            + counts[cabinet.TAB_UNPUBLISHED]
            == counts[cabinet.TAB_ALL]
        )

    @pytest.mark.django_db
    def test_panel_counts_match_the_lists(self, user):
        for state in STATE_TAB:
            _state_photo(user, state)

        tabs = cabinet.cabinet_tabs(user, cabinet.TAB_ALL)

        assert [tab.key for tab in tabs] == list(cabinet.TAB_ORDER)
        for tab in tabs:
            assert tab.count == cabinet.cabinet_photos(user, tab.key).count()

    @pytest.mark.django_db
    def test_removed_photo_lives_only_in_removed_tab(self, user):
        photo = _photo(user)
        photo.remove_from_cabinet()

        for tab in cabinet.TAB_ORDER:
            if tab == cabinet.TAB_REMOVED:
                continue
            assert tab not in _tabs_with(user, photo)

        assert _tabs_with(user, photo) == {cabinet.TAB_REMOVED}
        counts = cabinet.cabinet_counts(user)
        assert counts[cabinet.TAB_REMOVED] == 1
        assert counts[cabinet.TAB_ALL] == 0

    @pytest.mark.django_db
    def test_invalid_tab_falls_back_to_all(self, client, user):
        """Опечатка в ссылке не должна давать пустую страницу."""
        _photo(user, title="Финал")
        client.force_login(user)

        response = client.get(MY_PHOTOS_URL, {"tab": "zzz"})

        assert response.status_code == 200
        assert response.context["selected_tab"] == cabinet.TAB_ALL
        assert len(response.context["photos"]) == 1


class TestRemoveFromCabinet:
    """Уборка из списка и возврат: обратимо, без удаления."""

    @pytest.mark.django_db
    def test_post_marks_photo_and_reports_success(self, client, user):
        photo = _photo(user)
        client.force_login(user)

        response = client.post(
            reverse("photos:photo_remove", args=[photo.pk]), follow=True
        )

        photo.refresh_from_db()
        assert photo.cabinet_removed_at is not None
        assert photo.deleted_at is None
        assert "Фото убрано из списка" in response.content.decode()

    @pytest.mark.django_db
    def test_second_post_keeps_the_timestamp(self, client, user):
        """Повтор — гонка двух вкладок, а не новое решение автора."""
        photo = _photo(user)
        client.force_login(user)
        url = reverse("photos:photo_remove", args=[photo.pk])
        client.post(url)
        photo.refresh_from_db()
        first_removal = photo.cabinet_removed_at

        response = client.post(url, follow=True)

        photo.refresh_from_db()
        assert photo.cabinet_removed_at == first_removal
        assert "Фото уже убрано из списка" in response.content.decode()

    @pytest.mark.django_db
    def test_restore_returns_photo_to_its_tab(self, client, user):
        photo = _photo(user)
        photo.remove_from_cabinet()
        client.force_login(user)

        response = client.post(
            reverse("photos:photo_restore", args=[photo.pk]), follow=True
        )

        photo.refresh_from_db()
        assert photo.cabinet_removed_at is None
        assert "Фото возвращено в список" in response.content.decode()
        assert (
            cabinet.cabinet_photos(user, cabinet.TAB_PUBLISHED)
            .filter(pk=photo.pk)
            .exists()
        )

    @pytest.mark.django_db
    def test_second_restore_says_it_is_already_in_the_list(self, client, user):
        photo = _photo(user)
        client.force_login(user)

        response = client.post(
            reverse("photos:photo_restore", args=[photo.pk]), follow=True
        )

        assert "Фото уже в списке" in response.content.decode()


class TestCabinetRights:
    """Убирает только автор: чужой кабинет не дело модератора."""

    @pytest.mark.django_db
    def test_foreign_photo_gives_404(self, client, user, other_user):
        photo = _photo(user)
        client.force_login(other_user)

        response = client.post(reverse("photos:photo_remove", args=[photo.pk]))

        assert response.status_code == 404
        photo.refresh_from_db()
        assert photo.cabinet_removed_at is None

    @pytest.mark.django_db
    def test_moderator_cannot_remove_foreign_photo(self, client, user, moderator):
        photo = _photo(user)
        client.force_login(moderator)

        response = client.post(reverse("photos:photo_remove", args=[photo.pk]))

        assert response.status_code == 404
        photo.refresh_from_db()
        assert photo.cabinet_removed_at is None

    @pytest.mark.django_db
    def test_author_removes_deleted_photo(self, client, user):
        """Удалённое модератором фото — как раз то, что мешает в списке."""
        photo = _photo(user)
        photo.soft_delete(reason="Не по теме архива")
        client.force_login(user)

        response = client.post(reverse("photos:photo_remove", args=[photo.pk]))

        assert response.status_code == 302
        photo.refresh_from_db()
        assert photo.cabinet_removed_at is not None

    def test_anonymous_is_sent_to_login_with_the_list(self, client, user):
        """`next` — адрес списка, а не самого POST: иначе петля 405."""
        photo = _photo(user)

        response = client.post(reverse("photos:photo_remove", args=[photo.pk]))

        assert response.status_code == 302
        assert response["Location"] == f"{LOGIN_URL}?next={MY_PHOTOS_URL}"

    @pytest.mark.django_db
    def test_get_is_not_allowed(self, client, user):
        photo = _photo(user)
        client.force_login(user)

        response = client.get(reverse("photos:photo_remove", args=[photo.pk]))

        assert response.status_code == 405

    def test_anonymous_get_is_not_allowed(self, client, user):
        photo = _photo(user)

        response = client.get(reverse("photos:photo_remove", args=[photo.pk]))

        assert response.status_code == 405


class TestArchiveIsNotAffected:
    """Уборка из списка — не удаление: архив, ссылка и квота те же."""

    @pytest.mark.django_db
    def test_removed_photo_stays_in_public_archive(self, client, user):
        photo = _photo(user, status=Status.PUBLISHED)
        photo.remove_from_cabinet()
        client.force_login(user)

        assert photo in visible_photos()
        assert client.get(photo.get_absolute_url()).status_code == 200
        photo.refresh_from_db()
        assert photo.deleted_at is None

    @pytest.mark.django_db
    def test_quota_is_not_returned(self, user):
        """Иначе лимит обходился бы уборкой."""
        photo = _photo(user, status=Status.DRAFT)
        before = services.uploads_today(user)

        photo.remove_from_cabinet()

        assert services.uploads_today(user) == before


class TestCardLinks:
    """Ссылка стоит только там, где страница откроется."""

    @pytest.mark.django_db
    def test_deleted_photo_has_no_link(self, client, user):
        photo = _photo(user)
        photo.soft_delete(reason="Не по теме архива")
        client.force_login(user)

        html = client.get(
            MY_PHOTOS_URL, {"tab": cabinet.TAB_UNPUBLISHED}
        ).content.decode()

        # Ищем именно ссылку: адрес фото — префикс адреса уборки,
        # и проверка «подстроки» прошла бы на форме удаления.
        assert f'href="{photo.get_absolute_url}"' not in html
        assert "upload-card__media" in html

    @pytest.mark.django_db
    def test_merged_photo_links_to_canonical(self, client, user):
        """Слитый дубликат отдаёт 301, поэтому ссылка работает."""
        canon = _photo(user, title="Канон", status=Status.PUBLISHED)
        source = _photo(user, title="Дубликат", status=Status.PUBLISHED)
        source.merge_into(canon)
        client.force_login(user)

        source_url = source.get_absolute_url()
        html = client.get(
            MY_PHOTOS_URL, {"tab": cabinet.TAB_UNPUBLISHED}
        ).content.decode()

        assert f'href="{source_url}"' in html
        assert client.get(source_url).status_code == 301


class TestReturnAddress:
    """`next` возвращает на ту же вкладку, но не на чужой сайт."""

    @pytest.mark.django_db
    def test_next_keeps_the_tab(self, client, user):
        photo = _photo(user)
        client.force_login(user)

        response = client.post(
            reverse("photos:photo_remove", args=[photo.pk]),
            {"next": f"{MY_PHOTOS_URL}?tab=removed"},
        )

        assert response.status_code == 302
        assert response["Location"] == f"{MY_PHOTOS_URL}?tab=removed"

    @pytest.mark.django_db
    def test_foreign_next_is_replaced(self, client, user):
        photo = _photo(user)
        client.force_login(user)

        response = client.post(
            reverse("photos:photo_remove", args=[photo.pk]),
            {"next": "https://evil.example/"},
        )

        assert response.status_code == 302
        assert response["Location"] == MY_PHOTOS_URL


class TestPaginationKeepsTab:
    """Вкладка не теряется при переходе на вторую страницу."""

    @pytest.mark.django_db
    def test_tab_survives_page_links(self, client, user):
        for index in range(PHOTO_PAGE_SIZE + 1):
            _photo(user, title=f"Фото {index}", status=Status.PUBLISHED)
        client.force_login(user)

        html = client.get(
            MY_PHOTOS_URL, {"tab": cabinet.TAB_PUBLISHED}
        ).content.decode()

        assert f"tab={cabinet.TAB_PUBLISHED}" in html
        assert "page=2" in html


class TestWorksWithoutJavaScript:
    """Формы карточки — обычный POST: модератор бывает на медленной сети."""

    @pytest.mark.django_db
    def test_cards_use_plain_post_forms(self, client, user):
        _photo(user, title="Опубликовано")
        removed = _photo(user, title="Убранное")
        removed.remove_from_cabinet()
        client.force_login(user)

        html = client.get(MY_PHOTOS_URL).content.decode()

        assert 'method="post"' in html
        assert "csrfmiddlewaretoken" in html
        assert "Убрать из списка" in html

        removed_html = client.get(
            MY_PHOTOS_URL, {"tab": cabinet.TAB_REMOVED}
        ).content.decode()

        assert "Вернуть в список" in removed_html
