"""Тесты management-команды `seed_demo`."""

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from PIL import Image

from apps.accounts.models import User, UserRole
from apps.catalog.models import Event, Person, Team
from apps.core.management.commands.seed_demo import (
    DEMO_EMAIL,
    DEMO_MODERATOR_EMAIL,
    PHOTO_SIZES,
    _make_demo_image,
)
from apps.core.models import Status
from apps.photos.models import Photo, Source
from tests.utils import make_image_file

pytestmark = pytest.mark.django_db


@pytest.fixture
def seed(settings):
    """Запуск команды в режиме разработки, независимо от DEBUG в тестах."""

    def run(**kwargs):
        call_command("seed_demo", **kwargs)

    settings.DEBUG = True
    return run


class TestDemoSet:
    def test_creates_full_demo_set(self, seed):
        seed()

        assert Team.objects.count() == 5
        assert Person.objects.count() == 30
        assert Event.objects.count() == 8
        assert Source.objects.count() == 6
        assert Photo.objects.count() == 40

    def test_creates_two_demo_users(self, seed):
        seed()

        author = User.objects.get(email=DEMO_EMAIL)
        moderator = User.objects.get(email=DEMO_MODERATOR_EMAIL)

        assert author.role == UserRole.USER
        assert moderator.role == UserRole.MODERATOR
        assert author.has_consents

    def test_publishes_most_of_the_archive(self, seed):
        seed()

        assert Team.objects.filter(status=Status.PUBLISHED).count() == 4
        assert Team.objects.filter(status=Status.DRAFT).count() == 1
        assert Event.objects.filter(status=Status.DRAFT).count() == 2
        assert Person.objects.filter(status=Status.DRAFT).count() == 2
        assert Photo.objects.filter(status=Status.DRAFT).count() == 3

    def test_marks_demo_objects(self, seed):
        seed()

        assert Team.objects.filter(attributes__demo=True).count() == 5
        assert Person.objects.filter(attributes__demo=True).count() == 30
        assert Event.objects.filter(attributes__demo=True).count() == 8

    def test_photos_limit_changes_photo_count(self, seed):
        seed(photos=3)

        assert Photo.objects.count() == 3

    def test_second_run_does_not_duplicate(self, seed):
        seed()
        counts = (Team.objects.count(), Person.objects.count(), Photo.objects.count())

        seed()

        assert (
            Team.objects.count(),
            Person.objects.count(),
            Photo.objects.count(),
        ) == counts
        assert User.objects.count() == 2


class TestPhotos:
    def test_photos_have_original_files(self, seed):
        seed(photos=5)

        for photo in Photo.objects.all():
            assert photo.original
            assert photo.original.storage.exists(photo.original.name)

    def test_photos_use_different_proportions(self, seed):
        seed()

        sizes = {(photo.width, photo.height) for photo in Photo.objects.all()}

        assert sizes == set(PHOTO_SIZES)

    def test_photo_without_rights_is_not_public(self, seed):
        seed()

        without_rights = Photo.objects.filter(rights_confirmed=False)

        assert without_rights.count() == 3
        assert all(not photo.is_public for photo in without_rights)

    def test_photo_without_team_and_event_exists(self, seed):
        seed()

        assert Photo.objects.filter(team__isnull=True, event__isnull=True).exists()

    def test_thumbnail_is_generated(self, seed):
        seed(photos=1)

        photo = Photo.objects.get()

        assert photo.thumbnail
        assert photo.thumbnail.storage.exists(photo.thumbnail.name)


class TestPrivacy:
    def test_minor_shows_first_name_only(self, seed):
        seed()

        person = Person.objects.get(slug="kirill-prohorov")

        assert person.public_name == "Кирилл"
        assert "Прохоров" not in person.public_name

    def test_hidden_name_has_neutral_label(self, seed):
        seed()

        person = Person.objects.get(slug="yudin-stanislav")

        assert person.public_name == "Имя не указано"
        assert "Юдин" not in person.public_name


class TestFlush:
    def test_flush_removes_demo_and_keeps_real_data(self, seed, user, baseball):
        team = Team.objects.create(sport=baseball, name="Настоящая", created_by=user)
        photo = Photo.objects.create(
            sport=baseball,
            title="Настоящее фото",
            original=make_image_file(),
            uploaded_by=user,
            rights_confirmed=True,
            status=Status.PUBLISHED,
        )
        seed()

        seed(flush=True)

        assert not Team.objects.filter(attributes__demo=True).exists()
        assert not Person.objects.filter(attributes__demo=True).exists()
        assert not Event.objects.filter(attributes__demo=True).exists()
        assert not Photo.objects.filter(uploaded_by__email=DEMO_EMAIL).exists()
        assert not User.objects.filter(
            email__in=[DEMO_EMAIL, DEMO_MODERATOR_EMAIL]
        ).exists()

        assert Team.objects.filter(pk=team.pk).exists()
        assert Photo.objects.filter(pk=photo.pk).exists()
        assert photo.original.storage.exists(photo.original.name)

    def test_flush_deletes_original_files(self, seed):
        seed(photos=2)
        photo = Photo.objects.first()
        storage = photo.original.storage
        name = photo.original.name

        seed(flush=True)

        assert not storage.exists(name)


class TestGuards:
    def test_refuses_without_debug(self, settings):
        settings.DEBUG = False

        with pytest.raises(CommandError, match="DEBUG"):
            call_command("seed_demo")

    def test_force_allows_run_without_debug(self, settings):
        settings.DEBUG = False

        call_command("seed_demo", force=True, photos=1)

        assert Photo.objects.count() == 1

    def test_flush_also_requires_debug(self, settings):
        settings.DEBUG = False

        with pytest.raises(CommandError, match="DEBUG"):
            call_command("seed_demo", flush=True)


class TestImageGeneration:
    def test_same_seed_gives_same_bytes(self):
        first = _make_demo_image("Соколы на сборах", 1985, (1200, 800), 7)
        second = _make_demo_image("Соколы на сборах", 1985, (1200, 800), 7)

        assert first == second

    def test_image_has_requested_size(self):
        data = _make_demo_image(
            "Показательный матч на открытии стадиона", 2007, (800, 1200), 3
        )

        image = Image.open(io.BytesIO(data))

        assert image.size == (800, 1200)
        assert image.format == "JPEG"
