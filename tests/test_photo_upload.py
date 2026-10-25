"""Правила загрузки фото: статус, суточный лимит, резолв названий, форма."""

import io
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.utils import timezone
from PIL import Image

from apps.catalog.models import Event, Team
from apps.core.models import Status
from apps.core.shortcuts import PHOTO_PAGE_SIZE, default_sport, visible_photos
from apps.photos import services
from apps.photos.forms import PhotoUploadForm
from apps.photos.models import Photo, ProcessingStatus
from apps.photos.processing import MAX_SIDE_PX, MAX_UPLOAD_BYTES, MIN_SIDE_PX
from tests.factories import PhotoFactory, TeamFactory
from tests.utils import make_image_file, make_upload


def _make_photos(user, count, **kwargs):
    """Создать `count` фото автора без обработки и без файлов на диске."""
    photos = []
    for index in range(count):
        photos.append(
            PhotoFactory(
                uploaded_by=user,
                status=Status.DRAFT,
                original=make_image_file(f"photo-{index}.jpg"),
                **kwargs,
            )
        )
    return photos


@pytest.mark.django_db
def test_initial_status_is_draft(user):
    assert services.initial_status_for(user) == Status.DRAFT


@pytest.mark.django_db
def test_daily_limit_allows_fiftieth_upload(user):
    _make_photos(user, services.DAILY_UPLOAD_LIMIT - 1)

    assert services.uploads_today(user) == services.DAILY_UPLOAD_LIMIT - 1
    assert services.upload_limit_reached(user) is False


@pytest.mark.django_db
def test_daily_limit_blocks_fifty_first_upload(user):
    _make_photos(user, services.DAILY_UPLOAD_LIMIT)

    assert services.uploads_today(user) == services.DAILY_UPLOAD_LIMIT
    assert services.upload_limit_reached(user) is True


@pytest.mark.django_db
def test_daily_limit_counts_only_last_24_hours(user):
    photos = _make_photos(user, 2)
    Photo.objects.filter(pk=photos[0].pk).update(
        created_at=timezone.now() - timedelta(hours=25)
    )

    assert services.uploads_today(user) == 1


@pytest.mark.django_db
def test_daily_limit_keeps_deleted_photos_in_quota(user):
    photos = _make_photos(user, 1)
    photos[0].soft_delete()

    assert services.uploads_today(user) == 1


@pytest.mark.django_db
def test_daily_limit_does_not_mix_authors(user, other_user):
    # Фото без автора не должны попадать в чужой лимит: `uploaded_by=None`
    # у архивных записей — норма, а не «все анонимы один человек».
    PhotoFactory(uploaded_by=None, original=make_image_file("anon.jpg"))
    _make_photos(other_user, 3)

    assert services.uploads_today(user) == 0
    assert services.upload_limit_reached(user) is False


@pytest.mark.django_db
def test_resolve_team_by_exact_name(baseball):
    team = TeamFactory(name="Соколы", city="Москва")

    resolved = services.resolve_by_name(
        Team, "Соколы", Team.objects.all(), lambda obj: obj.full_name
    )

    assert resolved == team


@pytest.mark.django_db
def test_resolve_team_by_name_with_city(baseball):
    TeamFactory(name="Соколы", city="Москва")
    team = TeamFactory(name="Соколы", city="Тула")

    resolved = services.resolve_by_name(
        Team, "Соколы (Тула)", Team.objects.all(), lambda obj: obj.full_name
    )

    assert resolved == team


@pytest.mark.django_db
def test_resolve_ambiguous_team_name_gives_none(baseball):
    TeamFactory(name="Соколы", city="Москва")
    TeamFactory(name="Соколы", city="Тула")

    assert (
        services.resolve_by_name(
            Team, "Соколы", Team.objects.all(), lambda obj: obj.full_name
        )
        is None
    )


@pytest.mark.django_db
def test_resolve_empty_value_gives_none(baseball):
    TeamFactory(name="Соколы", city="Москва")

    assert (
        services.resolve_by_name(
            Team, "   ", Team.objects.all(), lambda obj: obj.full_name
        )
        is None
    )
    assert (
        services.resolve_by_name(
            Team, None, Team.objects.all(), lambda obj: obj.full_name
        )
        is None
    )


@pytest.mark.django_db
def test_resolve_event_by_name(baseball):
    event = Event.objects.create(sport=baseball, name="Первенство России")

    resolved = services.resolve_by_name(
        Event, "первенство россии", Event.objects.all(), str
    )

    assert resolved == event


@pytest.mark.django_db
def test_resolve_respects_given_queryset(baseball):
    """Резолв смотрит только ту выборку, что передали: черновик чужого
    автора форма не увидит, если выборка публичная.
    """
    TeamFactory(name="Соколы", city="Москва")

    resolved = services.resolve_by_name(
        Team, "Соколы", Team.public.published(), lambda obj: obj.full_name
    )

    assert resolved is None


class TestUploadState:
    """Состояние плашки в кабинете: ключ и подпись."""

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        ("status", "processing", "expected"),
        [
            (Status.DRAFT, ProcessingStatus.UPLOADED, "processing"),
            (Status.DRAFT, ProcessingStatus.PROCESSING, "processing"),
            (Status.DRAFT, ProcessingStatus.PROCESSED, "review"),
            (Status.DRAFT, ProcessingStatus.FAILED, "failed"),
            (Status.PUBLISHED, ProcessingStatus.PROCESSED, "published"),
            (Status.HIDDEN, ProcessingStatus.PROCESSED, "hidden"),
            (Status.REJECTED, ProcessingStatus.PROCESSED, "rejected"),
        ],
    )
    def test_state_per_status(self, status, processing, expected):
        photo = PhotoFactory(status=status, processing_status=processing)

        assert photo.upload_state == expected

    @pytest.mark.django_db
    def test_deleted_state(self):
        photo = PhotoFactory(status=Status.DRAFT)
        photo.soft_delete()

        assert photo.upload_state == "deleted"

    @pytest.mark.django_db
    def test_deleted_wins_over_published(self):
        # Фото могло быть опубликовано, а потом удалено модератором.
        # Плашка «Опубликовано» здесь соврала бы о судьбе снимка.
        photo = PhotoFactory(status=Status.PUBLISHED)
        photo.soft_delete()

        assert photo.upload_state == "deleted"

    @pytest.mark.django_db
    def test_failed_wins_over_published(self):
        photo = PhotoFactory(
            status=Status.PUBLISHED, processing_status=ProcessingStatus.FAILED
        )

        assert photo.upload_state == "failed"

    @pytest.mark.django_db
    def test_label_is_translated(self):
        photo = PhotoFactory(
            status=Status.DRAFT, processing_status=ProcessingStatus.PROCESSED
        )

        assert photo.upload_state_label == "На проверке"

    @pytest.mark.django_db
    def test_every_state_has_a_label(self):
        """Ключ без подписи дал бы `KeyError` на странице кабинета."""
        from apps.photos.models import UPLOAD_STATE_LABELS

        assert set(UPLOAD_STATE_LABELS) == {
            "deleted",
            "failed",
            "processing",
            "published",
            "hidden",
            "rejected",
            "review",
        }


def _form(user, files=None, **data):
    """Форма с файлом и полями данных; любое поле можно переопределить."""
    if files is None:
        files = {"original": make_upload()}
    return PhotoUploadForm(data=data, files=files, user=user)


def _errors_text(form):
    """Все сообщения формы одной строкой: тесты проверяют текст человеку."""
    return " ".join(
        str(message) for messages in form.errors.values() for message in messages
    )


class TestPhotoUploadForm:
    """Проверка файла и полей: каждый отказ с понятным текстом."""

    @pytest.mark.django_db
    def test_valid_upload_passes(self, user, baseball):
        form = _form(user, title="Финал", year=1994)

        assert form.is_valid(), form.errors

    @pytest.mark.django_db
    def test_file_is_readable_after_validation(self, user, baseball):
        upload = make_upload()
        size = upload.size
        form = _form(user, files={"original": upload})

        assert form.is_valid(), form.errors
        # Без перемотки сохранение получило бы пустой файл.
        assert form.cleaned_data["original"].tell() == 0
        assert len(form.cleaned_data["original"].read()) == size

    @pytest.mark.django_db
    def test_limit_constants_match_plan(self):
        assert MAX_UPLOAD_BYTES == 25 * 1024 * 1024
        assert MIN_SIDE_PX == 300
        assert MAX_SIDE_PX == 10_000

    @pytest.mark.django_db
    def test_too_large_file_is_rejected(self, user, baseball, monkeypatch):
        monkeypatch.setattr("apps.photos.forms.MAX_UPLOAD_BYTES", 1024)
        form = _form(user, files={"original": make_upload()})

        assert not form.is_valid()
        assert "Файл больше" in _errors_text(form)

    @pytest.mark.django_db
    def test_not_an_image_is_rejected(self, user, baseball):
        upload = SimpleUploadedFile(
            "scan.pdf", b"%PDF-1.4 not an image", content_type="application/pdf"
        )
        form = _form(user, files={"original": upload})

        assert not form.is_valid()
        assert "Не удалось прочитать изображение" in _errors_text(form)

    @pytest.mark.django_db
    def test_unsupported_format_is_rejected(self, user, baseball):
        # GIF Pillow открывает, но в архив он не идёт: формат проверяется
        # по содержимому, а не по расширению.
        form = _form(user, files={"original": make_upload(image_format="GIF")})

        assert not form.is_valid()
        assert "Поддерживаются форматы" in _errors_text(form)

    @pytest.mark.django_db
    def test_small_side_is_rejected(self, user, baseball):
        form = _form(user, files={"original": make_upload(size=(200, 200))})

        assert not form.is_valid()
        assert "Слишком маленькое изображение" in _errors_text(form)

    @pytest.mark.django_db
    def test_large_side_is_rejected(self, user, baseball, monkeypatch):
        # Реальные 20000×20000 заняли бы гигабайт памяти, а проверяется
        # логика сравнения, а не число.
        monkeypatch.setattr("apps.photos.forms.MAX_SIDE_PX", 500)
        form = _form(user, files={"original": make_upload(size=(800, 400))})

        assert not form.is_valid()
        assert "Слишком большое изображение" in _errors_text(form)

    @pytest.mark.django_db
    def test_pixel_limit_warning_becomes_error(self, user, baseball, monkeypatch):
        # Pillow бросает исключение лишь при двойном превышении, а 60 Мпикс
        # дают только предупреждение: без его перехвата файл прошёл бы форму.
        monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100_000)
        form = _form(user, files={"original": make_upload(size=(400, 400))})

        assert not form.is_valid()
        assert "Изображение слишком большое" in _errors_text(form)

    @pytest.mark.django_db
    def test_truncated_file_is_rejected(self, user, baseball):
        buffer = io.BytesIO()
        Image.new("RGB", (400, 400), "red").save(buffer, format="JPEG")
        data = buffer.getvalue()
        upload = SimpleUploadedFile("cut.jpg", data[: len(data) // 2])

        form = _form(user, files={"original": upload})

        assert not form.is_valid()
        assert "повреждён" in _errors_text(form)

    @pytest.mark.django_db
    def test_heic_is_accepted(self, user, baseball):
        form = _form(user, files={"original": make_upload(image_format="HEIF")})

        assert form.is_valid(), form.errors

    @pytest.mark.django_db
    def test_daily_limit_blocks_the_next_upload(self, user, baseball):
        _make_photos(user, services.DAILY_UPLOAD_LIMIT)

        form = _form(user)

        assert not form.is_valid()
        assert "не больше" in _errors_text(form)

    @pytest.mark.django_db
    def test_daily_limit_ignores_other_authors(self, user, other_user, baseball):
        _make_photos(other_user, services.DAILY_UPLOAD_LIMIT)

        assert _form(user).is_valid()

    @pytest.mark.django_db
    def test_year_out_of_range_is_rejected(self, user, baseball):
        form = _form(user, year=timezone.localdate().year + 1)

        assert not form.is_valid()
        assert "Год должен быть между" in _errors_text(form)

    @pytest.mark.django_db
    def test_year_must_match_taken_date(self, user, baseball):
        form = _form(user, year=1994, taken_date="1995-05-01")

        assert not form.is_valid()
        assert "не совпадает с годом" in _errors_text(form)

    @pytest.mark.django_db
    def test_own_draft_team_is_found(self, user, baseball):
        team = TeamFactory(name="Соколы", city="Тула", created_by=user)

        form = _form(user, team_name="Соколы (Тула)")

        assert form.is_valid(), form.errors
        assert form.cleaned_data["team"] == team

    @pytest.mark.django_db
    def test_other_author_published_team_is_found(self, user, other_user, baseball):
        team = TeamFactory(
            name="Соколы", city="Москва", created_by=other_user, status=Status.PUBLISHED
        )

        form = _form(user, team_name="Соколы")

        assert form.is_valid(), form.errors
        assert form.cleaned_data["team"] == team

    @pytest.mark.django_db
    def test_foreign_draft_team_is_not_found(self, user, other_user, baseball):
        TeamFactory(name="Соколы", city="Москва", created_by=other_user)

        form = _form(user, team_name="Соколы")

        assert not form.is_valid()
        assert "Не удалось найти" in _errors_text(form)

    @pytest.mark.django_db
    def test_ambiguous_team_name_asks_for_city(self, user, baseball):
        TeamFactory(name="Соколы", city="Москва", created_by=user)
        TeamFactory(name="Соколы", city="Тула", created_by=user)

        form = _form(user, team_name="Соколы")

        assert not form.is_valid()
        assert "Не удалось найти" in _errors_text(form)

    @pytest.mark.django_db
    def test_event_is_found_by_name(self, user, baseball):
        event = Event.objects.create(
            sport=baseball, name="Первенство России", created_by=user
        )

        form = _form(user, event_name="первенство россии")

        assert form.is_valid(), form.errors
        assert form.cleaned_data["event"] == event

    @pytest.mark.django_db
    def test_empty_names_leave_links_empty(self, user, baseball):
        form = _form(user, team_name="   ", event_name="")

        assert form.is_valid(), form.errors
        assert form.cleaned_data["team"] is None
        assert form.cleaned_data["event"] is None


UPLOAD_URL = "/photos/upload/"
MY_PHOTOS_URL = "/photos/mine/"
LOGIN_URL = "/accounts/login/"


class TestPhotoUploadView:
    """Представление загрузки: доступ, сохранение и кабинет автора."""

    def test_anonymous_is_redirected_to_login(self, client):
        response = client.get(UPLOAD_URL)

        assert response.status_code == 302
        assert response["Location"] == f"{LOGIN_URL}?next={UPLOAD_URL}"

    def test_logged_in_user_sees_form(self, client, user, baseball):
        client.force_login(user)

        response = client.get(UPLOAD_URL)

        assert response.status_code == 200
        assert "photo-form" in response.content.decode()

    def test_upload_creates_draft_photo(self, client, user, baseball):
        client.force_login(user)

        response = client.post(
            UPLOAD_URL, {"original": make_upload(), "title": "Финал", "year": 1994}
        )

        assert response.status_code == 302
        assert response["Location"] == MY_PHOTOS_URL
        photo = Photo.objects.get()
        assert photo.status == Status.DRAFT
        assert photo.uploaded_by == user
        assert photo.sport == baseball
        assert photo.original_filename == "photo.jpg"
        # Обработка синхронная: без неё карточка кабинета была бы пустой.
        assert photo.display
        assert photo.processing_status == ProcessingStatus.PROCESSED

    def test_my_photos_shows_new_upload(self, client, user, baseball):
        client.force_login(user)
        client.post(UPLOAD_URL, {"original": make_upload()})

        response = client.get(MY_PHOTOS_URL)

        assert response.status_code == 200
        assert "На проверке" in response.content.decode()

    def test_missing_sport_shows_message(self, client, user, baseball):
        client.force_login(user)
        baseball.is_active = False
        baseball.save(update_fields=["is_active"])

        response = client.get(UPLOAD_URL)

        assert response.status_code == 200
        assert "пока недоступна" in response.content.decode()

    def test_my_photos_requires_login(self, client):
        response = client.get(MY_PHOTOS_URL)

        assert response.status_code == 302
        assert response["Location"] == f"{LOGIN_URL}?next={MY_PHOTOS_URL}"

    def test_team_query_prefills_name(self, client, user, baseball):
        team = TeamFactory(name="Соколы", city="Тула", created_by=user)
        client.force_login(user)

        response = client.get(UPLOAD_URL, {"team": team.pk})

        assert response.status_code == 200
        assert 'value="Соколы (Тула)"' in response.content.decode()

    def test_unknown_team_query_is_ignored(self, client, user, baseball):
        client.force_login(user)

        response = client.get(UPLOAD_URL, {"team": "999"})

        assert response.status_code == 200

    def test_put_is_not_allowed(self, client, user, baseball):
        client.force_login(user)

        assert client.put(UPLOAD_URL).status_code == 405

    def test_delete_is_not_allowed(self, client, user, baseball):
        client.force_login(user)

        assert client.delete(UPLOAD_URL).status_code == 405

    def test_upload_uses_default_sport(self, client, user, baseball):
        client.force_login(user)

        client.post(UPLOAD_URL, {"original": make_upload()})

        assert Photo.objects.get().sport == default_sport()

    def test_processing_fills_derived_fields(self, client, user, baseball):
        """Синхронная обработка должна дать всё, чем живёт карточка."""
        client.force_login(user)

        client.post(UPLOAD_URL, {"original": make_upload(size=(600, 400))})

        photo = Photo.objects.get()
        assert (photo.width, photo.height) == (600, 400)
        assert len(photo.phash) == 16

    def test_original_filename_keeps_only_name(self, client, user, baseball):
        client.force_login(user)

        client.post(UPLOAD_URL, {"original": make_upload(name="album/photo.jpg")})

        assert Photo.objects.get().original_filename == "photo.jpg"

    def test_heic_upload_is_processed(self, client, user, baseball):
        """iPhone снимает в HEIC: форма и пайплайн должны его принять."""
        client.force_login(user)

        client.post(
            UPLOAD_URL, {"original": make_upload("photo.heic", image_format="HEIF")}
        )

        photo = Photo.objects.get()
        assert photo.processing_status == ProcessingStatus.PROCESSED
        assert photo.width == 400

    def test_form_and_cabinet_are_noindex(self, client, user, baseball):
        """Служебные страницы не должны попадать в индекс поисковиков."""
        client.force_login(user)

        for url in (UPLOAD_URL, MY_PHOTOS_URL):
            html = client.get(url).content.decode()
            assert 'content="noindex, nofollow"' in html


class TestUploadedPhotoIsNotPublic:
    """Загруженное фото ждёт модератора: наружу оно не выходит.

    Проверяем все публичные входы сразу — выборку, карту сайта, поиск
    и страницу года: правило отсева одно, но применяется в разных местах,
    и разойтись они могут молча.
    """

    def _upload(self, client, user):
        client.force_login(user)
        client.post(
            UPLOAD_URL,
            {"original": make_upload(), "title": "Финал", "year": 1994},
        )
        return Photo.objects.get()

    def test_not_in_visible_photos(self, client, user, baseball):
        photo = self._upload(client, user)

        assert photo not in visible_photos()

    def test_not_in_sitemap(self, client, user, baseball):
        photo = self._upload(client, user)

        html = client.get("/sitemap-photos.xml").content.decode()

        assert photo.get_absolute_url() not in html

    def test_not_on_year_page(self, client, user, baseball):
        self._upload(client, user)

        # Года без видимых фото в архиве нет — страница отдаёт 404.
        assert client.get("/years/1994/").status_code == 404

    @pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="Поиск работает только на PostgreSQL (tsvector, pg_trgm)",
    )
    def test_not_in_search(self, client, user, baseball):
        self._upload(client, user)

        response = client.get("/search/", {"q": "финал"})

        assert response.context["result"].total == 0

    def test_foreign_direct_link_gives_404(self, client, user, other_user, baseball):
        photo = self._upload(client, user)
        client.force_login(other_user)

        response = client.get(photo.get_absolute_url())

        assert response.status_code == 404

    def test_author_sees_own_photo_with_notice(self, client, user, baseball):
        photo = self._upload(client, user)

        response = client.get(photo.get_absolute_url())

        assert response.status_code == 200
        assert "Не опубликовано" in response.content.decode()

    def test_published_photo_becomes_visible(self, client, user, baseball):
        """Обратная проверка: отсев выше не пуст, публикация открывает."""
        photo = self._upload(client, user)
        photo.publish()

        assert photo in visible_photos()
        assert client.get(photo.get_absolute_url()).status_code == 200


class TestExifYearHint:
    """Год из файла — подсказка в браузере, а не запись в базу."""

    def test_exif_year_is_not_saved_without_confirmation(self, client, user, baseball):
        client.force_login(user)
        upload = make_upload(taken_at="1994:05:01 12:00:00")

        client.post(UPLOAD_URL, {"original": upload})

        photo = Photo.objects.get()
        assert photo.year is None
        # EXIF при этом сохранён: он подсказка модератору.
        assert photo.exif["DateTimeOriginal"] == "1994:05:01 12:00:00"

    def test_confirmed_year_is_saved(self, client, user, baseball):
        client.force_login(user)
        upload = make_upload(taken_at="1994:05:01 12:00:00")

        client.post(UPLOAD_URL, {"original": upload, "year": 1994})

        assert Photo.objects.get().year == 1994


class TestMyPhotosCabinet:
    """Кабинет автора: свои фото в любом состоянии, с причиной решения."""

    def _make(self, user, **kwargs):
        # Обработанное фото — норма кабинета; тесты, которым нужна другая
        # стадия, переопределяют `processing_status` явно.
        kwargs.setdefault("processing_status", ProcessingStatus.PROCESSED)
        return PhotoFactory(
            uploaded_by=user,
            original=make_image_file("photo.jpg"),
            **kwargs,
        )

    def test_shows_only_own_photos(self, client, user, other_user, baseball):
        own = self._make(user, title="Моё фото")
        self._make(other_user, title="Чужое фото")
        client.force_login(user)

        html = client.get(MY_PHOTOS_URL).content.decode()

        assert "Моё фото" in html
        assert "Чужое фото" not in html
        assert own.get_absolute_url() in html

    def test_deleted_photo_stays_in_cabinet(self, client, user, baseball):
        """Молча исчезнувшее фото человек воспринял бы как потерю архива."""
        photo = self._make(user, title="Удалённое")
        photo.soft_delete(reason="Не по теме архива")
        client.force_login(user)

        html = client.get(MY_PHOTOS_URL).content.decode()

        assert "Удалённое" in html
        assert "Удалено" in html

    def test_all_seven_states_are_shown(self, client, user, baseball):
        states = {
            "review": self._make(
                user,
                title="На проверке",
                status=Status.DRAFT,
                processing_status=ProcessingStatus.PROCESSED,
            ),
            "published": self._make(user, title="Опубликовано"),
            "hidden": self._make(user, title="Скрытое", status=Status.HIDDEN),
            "rejected": self._make(user, title="Отклонённое", status=Status.REJECTED),
            "failed": self._make(
                user,
                title="Сбойное",
                status=Status.DRAFT,
                processing_status=ProcessingStatus.FAILED,
            ),
            "processing": self._make(
                user,
                title="В работе",
                status=Status.DRAFT,
                processing_status=ProcessingStatus.UPLOADED,
            ),
            "deleted": self._make(user, title="Удалённое", status=Status.DRAFT),
        }
        states["deleted"].soft_delete()
        client.force_login(user)

        html = client.get(MY_PHOTOS_URL).content.decode()

        for state, photo in states.items():
            assert f"badge-status--{state}" in html
            assert photo.upload_state_label in html

    def test_moderation_reason_is_shown(self, client, user, baseball):
        photo = self._make(user, status=Status.DRAFT)
        photo.reject(reason="Фото не по теме архива")
        client.force_login(user)

        html = client.get(MY_PHOTOS_URL).content.decode()

        assert "Фото не по теме архива" in html

    def test_processing_error_is_shown(self, client, user, baseball):
        photo = self._make(
            user, status=Status.DRAFT, processing_status=ProcessingStatus.FAILED
        )
        photo.processing_error = "Файл повреждён"
        photo.save(update_fields=["processing_error"])
        client.force_login(user)

        assert "Файл повреждён" in client.get(MY_PHOTOS_URL).content.decode()

    def test_has_refresh_link(self, client, user, baseball):
        self._make(user)
        client.force_login(user)

        html = client.get(MY_PHOTOS_URL).content.decode()

        assert "Обновить" in html
        assert f'href="{MY_PHOTOS_URL}"' in html

    def test_photo_without_display_shows_placeholder(self, client, user, baseball):
        """Обработка могла не дойти до `display`: плейсхолдер, не 500."""
        photo = self._make(user, status=Status.DRAFT)
        photo.display = ""
        photo.save(update_fields=["display"])
        client.force_login(user)

        response = client.get(MY_PHOTOS_URL)

        assert response.status_code == 200
        assert "upload-card__placeholder" in response.content.decode()

    def test_pagination_splits_cabinet(self, client, user, baseball):
        for index in range(PHOTO_PAGE_SIZE + 1):
            self._make(user, title=f"Фото {index}")
        client.force_login(user)

        first = client.get(MY_PHOTOS_URL)
        second = client.get(MY_PHOTOS_URL, {"page": 2})

        assert len(first.context["photos"]) == PHOTO_PAGE_SIZE
        assert len(second.context["photos"]) == 1

    def test_empty_cabinet_offers_upload(self, client, user, baseball):
        client.force_login(user)

        html = client.get(MY_PHOTOS_URL).content.decode()

        assert "Вы ещё не загружали фото" in html
        assert UPLOAD_URL in html


class TestRulesPage:
    """Страница правил открыта до регистрации: её читают перед загрузкой."""

    def test_opens_anonymously(self, client):
        response = client.get("/rules/")

        assert response.status_code == 200

    def test_upload_form_links_to_rules(self, client, user, baseball):
        client.force_login(user)

        assert 'href="/rules/"' in client.get(UPLOAD_URL).content.decode()
