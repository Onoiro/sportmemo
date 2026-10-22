"""Общие фикстуры тестов SportMemo."""

import pytest

from apps.accounts.models import User, UserRole
from apps.catalog.models import Event, Person, Team
from apps.core.models import Sport
from tests.utils import make_image_file  # noqa: F401 — переиспользуется тестами


@pytest.fixture(autouse=True)
def media_root(tmp_path, settings):
    """Файлы тестов пишутся во временный каталог, а не в media/ проекта."""
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture(autouse=True)
def staticfiles_without_manifest(settings):
    """В тестах не нужен хешированный манифест статики.

    Боевой `CompressedManifestStaticFilesStorage` требует собранный
    `collectstatic`, иначе админка падает на
    «Missing staticfiles manifest entry».
    """
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }


@pytest.fixture(autouse=True)
def cleared_cache():
    """Очистить кэш между тестами.

    django-imagekit кэширует факт «превью существует», а MEDIA_ROOT
    в каждом тесте свой: без очистки тест увидел бы чужой файл
    из предыдущего теста и упал на чтении.
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def celery_eager(settings):
    """Задачи выполняются в том же процессе, брокер не нужен.

    Настройка ставится явно, а не через `DEBUG`: тесты не должны зависеть
    от окружения разработчика и от запущенного Redis.
    """
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.fixture
def baseball(db):
    """Вид спорта «Бейсбол» — создаётся data-миграцией,
    но в тестах нужен явно.
    """
    return Sport.objects.get(slug="baseball")


@pytest.fixture
def user(db):
    """Обычный зарегистрированный пользователь:
    согласия заполнены при регистрации.
    """
    from django.utils import timezone

    now = timezone.now()
    return User.objects.create_user(
        email="user@example.com",
        password="test-pass-12345",
        terms_accepted_at=now,
        privacy_accepted_at=now,
        consent_pd_at=now,
        consent_pd_version="2026-10-01",
    )


@pytest.fixture
def other_user(db):
    return User.objects.create_user(
        email="other@example.com", password="test-pass-12345"
    )


@pytest.fixture
def moderator(db):
    return User.objects.create_user(
        email="moderator@example.com",
        password="test-pass-12345",
        role=UserRole.MODERATOR,
    )


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(
        email="admin@example.com", password="test-pass-12345"
    )


@pytest.fixture
def team(baseball, user):
    return Team.objects.create(
        sport=baseball,
        name="Динамо",
        city="Москва",
        created_by=user,
    )


@pytest.fixture
def person(user):
    return Person.objects.create(
        first_name="Иван", last_name="Смирнов", created_by=user
    )


@pytest.fixture
def event(baseball, user):
    return Event.objects.create(
        sport=baseball,
        name="Первенство России",
        date_from="1994-05-01",
        created_by=user,
    )


@pytest.fixture
def photo(baseball, user):
    """Опубликованное фото с версией для показа — для тестов публикации.

    `display` заполняется сразу: превью строятся от него, и без файла
    карточка фото в тестах была бы пустой.
    """
    from django.utils import timezone

    from apps.core.models import Status
    from apps.photos.models import Photo

    return Photo.objects.create(
        sport=baseball,
        title="Финал 1994",
        original=make_image_file(),
        display=make_image_file("display.jpg"),
        uploaded_by=user,
        rights_confirmed=True,
        rights_confirmed_at=timezone.now(),
        status=Status.PUBLISHED,
    )
