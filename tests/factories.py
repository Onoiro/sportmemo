"""Фабрики тестовых объектов (factory_boy).

Фабрики описывают «типичный» объект, чтобы тесты занимались проверяемым
поведением, а не заполнением обязательных полей.
"""

import factory
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from apps.accounts.models import User, UserRole
from apps.catalog.models import Event, Person, PersonName, Team, TeamMembership
from apps.comments.models import Comment
from apps.core.models import Sport, Status
from apps.moderation.models import ModerationAction, ModerationActionType, Report
from apps.photos.models import Photo, PhotoPerson, Source
from tests.utils import make_image_file


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ("email",)
        # Пароль сохраняет хук ниже. Автосохранение после post_generation
        # здесь лишнее — без этого флага factory_boy предупреждает
        # о грядущем изменении поведения.
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    display_name = factory.Faker("first_name", locale="ru_RU")
    role = UserRole.USER
    terms_accepted_at = factory.LazyFunction(timezone.now)
    privacy_accepted_at = factory.LazyFunction(timezone.now)
    consent_pd_at = factory.LazyFunction(timezone.now)
    consent_pd_version = "2026-10-01"

    @factory.post_generation
    def password(obj, create, extracted, **kwargs):
        if not create:
            return
        obj.set_password(extracted or "test-pass-12345")
        obj.save(update_fields=["password"])


class ModeratorFactory(UserFactory):
    role = UserRole.MODERATOR


class SportFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Sport
        django_get_or_create = ("slug",)

    name = "Бейсбол"
    slug = "baseball"


class TeamFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Team

    sport = factory.SubFactory(SportFactory)
    name = factory.Sequence(lambda n: f"Команда {n}")
    city = "Москва"
    created_by = factory.SubFactory(UserFactory)


class PersonFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Person

    first_name = factory.Sequence(lambda n: f"Имя{n}")
    last_name = factory.Sequence(lambda n: f"Фамилия{n}")
    created_by = factory.SubFactory(UserFactory)


class PersonNameFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PersonName

    person = factory.SubFactory(PersonFactory)
    value = factory.Sequence(lambda n: f"Прозвище{n}")


class TeamMembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TeamMembership

    team = factory.SubFactory(TeamFactory)
    person = factory.SubFactory(PersonFactory)
    year_from = 1994


class EventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Event

    sport = factory.SubFactory(SportFactory)
    name = factory.Sequence(lambda n: f"Турнир {n}")
    date_from = factory.Faker("date_object")
    created_by = factory.SubFactory(UserFactory)


class SourceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Source

    kind = "paper_photo"
    title = factory.Sequence(lambda n: f"Источник {n}")
    holder = "Семейный архив"


class PhotoFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Photo

    sport = factory.SubFactory(SportFactory)
    title = factory.Sequence(lambda n: f"Фото {n}")
    original = factory.LazyFunction(make_image_file)
    uploaded_by = factory.SubFactory(UserFactory)
    rights_confirmed = True
    rights_confirmed_at = factory.LazyFunction(timezone.now)
    status = Status.PUBLISHED


class PhotoPersonFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PhotoPerson

    photo = factory.SubFactory(PhotoFactory)
    person = factory.SubFactory(PersonFactory)
    role = "player"


class CommentFactory(factory.django.DjangoModelFactory):
    """object_id и content_type тесты задают явно:
    цель комментария — часть сценария.
    """

    class Meta:
        model = Comment

    author = factory.SubFactory(UserFactory)
    text = factory.Faker("sentence", locale="ru_RU")
    content_type = factory.LazyFunction(
        lambda: ContentType.objects.get_for_model(Photo)
    )


class ReportFactory(factory.django.DjangoModelFactory):
    """object_id тесты задают явно."""

    class Meta:
        model = Report

    reporter = factory.SubFactory(UserFactory)
    reason = "wrong_data"
    content_type = factory.LazyFunction(
        lambda: ContentType.objects.get_for_model(Photo)
    )


class ModerationActionFactory(factory.django.DjangoModelFactory):
    """object_id тесты задают явно."""

    class Meta:
        model = ModerationAction

    moderator = factory.SubFactory(ModeratorFactory)
    action = ModerationActionType.HIDE
    content_type = factory.LazyFunction(lambda: ContentType.objects.get_for_model(Team))
