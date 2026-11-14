"""Кандидаты и резолв людей на фото.

Правило видимости одно на подсказку и на резолв: подсказка не должна
предлагать человека, которого резолв потом не найдёт. Приватность имени
проверяется отдельно — скрытая фамилия не участвует в поиске, а скрытый
человек не показывается вовсе.
"""

import pytest

from apps.catalog.models import NameKind, NameVisibility, Person, PersonName
from apps.core.models import Status
from apps.photos.forms import PersonCreateForm, PhotoPersonForm
from apps.photos.models import PhotoPerson, PhotoPersonRole
from apps.photos.people import (
    MAX_PHOTO_PEOPLE,
    PersonOption,
    add_person,
    create_person_for_photo,
    people_limit_reached,
    person_options,
    remove_person,
    resolve_person,
)
from tests.factories import (
    ModeratorFactory,
    PersonFactory,
    PersonNameFactory,
    PhotoFactory,
    PhotoPersonFactory,
    TeamFactory,
    TeamMembershipFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


def test_hidden_person_is_not_found():
    """Скрытое имя не показывается и не находится."""
    PersonFactory(
        first_name="Скрытый",
        last_name="Человек",
        name_visibility=NameVisibility.HIDDEN,
        status=Status.PUBLISHED,
    )
    photo = PhotoFactory()

    assert person_options(photo, None, "Скрытый") == []
    assert resolve_person(photo, None, "Скрытый Человек") == []


def test_first_name_only_found_by_first_name_not_by_last_name():
    """Фамилия при `first_name_only` в поиске не участвует."""
    PersonFactory(
        first_name="Алексей",
        last_name="Петров",
        name_visibility=NameVisibility.FIRST_NAME_ONLY,
        status=Status.PUBLISHED,
    )
    photo = PhotoFactory()

    assert [option.person.first_name for option in person_options(photo, None, "Алекс")]
    assert person_options(photo, None, "Петров") == []
    assert resolve_person(photo, None, "Петров Алексей") == []


def test_foreign_draft_is_hidden_own_draft_is_visible():
    """Чужой черновик в кандидатах не появляется, свой — да."""
    author = UserFactory()
    PersonFactory(
        first_name="Чужой",
        last_name="Черновик",
        created_by=UserFactory(),
        status=Status.DRAFT,
    )
    PersonFactory(
        first_name="Свой",
        last_name="Черновик",
        created_by=author,
        status=Status.DRAFT,
    )
    photo = PhotoFactory(uploaded_by=author)

    labels = [option.label for option in person_options(photo, author, "Черновик")]

    assert any("Свой" in label for label in labels)
    assert not any("Чужой" in label for label in labels)


def test_team_roster_goes_first():
    """Состав команды фото идёт раньше остальных людей."""
    team = TeamFactory()
    photo = PhotoFactory(team=team, year=1994)
    other = PersonFactory(
        first_name="Борис", last_name="Абрамов", status=Status.PUBLISHED
    )
    member = PersonFactory(first_name="Яков", last_name="Яшин", status=Status.PUBLISHED)
    TeamMembershipFactory(team=team, person=member, year_from=1992, year_to=1996)

    options = person_options(photo, None)

    assert [option.person for option in options] == [member, other]
    assert options[0].is_team_member is True
    assert options[1].is_team_member is False


def test_label_has_year_and_team():
    """Подпись несёт год и команду: без них имя не узнать."""
    team = TeamFactory(name="Русстар")
    photo = PhotoFactory(team=team)
    person = PersonFactory(
        first_name="Сергей",
        last_name="Иванов",
        birth_year=1971,
        status=Status.PUBLISHED,
    )
    TeamMembershipFactory(team=team, person=person, year_from=1990, year_to=1995)

    options = person_options(photo, None)

    assert options[0].label == "Сергей Иванов, 1971, Русстар"


def test_merged_person_is_not_a_candidate():
    """Слитый человек уходит из кандидатов и не находится по имени."""
    target = PersonFactory(
        first_name="Канон", last_name="Иванов", status=Status.PUBLISHED
    )
    duplicate = PersonFactory(
        first_name="Дубль", last_name="Иванов", status=Status.PUBLISHED
    )
    duplicate.merge_into(target)
    photo = PhotoFactory()

    labels = [option.label for option in person_options(photo, None, "Иванов")]

    assert not any("Дубль" in label for label in labels)
    assert resolve_person(photo, None, "Иванов Дубль") == []


def test_reverse_order_finds_person():
    """«Фамилия имя» и «имя фамилия» находят одного и того же человека."""
    person = PersonFactory(
        first_name="Сергей", last_name="Иванов", status=Status.PUBLISHED
    )
    photo = PhotoFactory()

    straight = resolve_person(photo, None, "Сергей Иванов")
    reverse = resolve_person(photo, None, "Иванов Сергей")

    assert [option.person for option in straight] == [person]
    assert [option.person for option in reverse] == [person]


def test_name_variant_finds_person():
    """Вариант имени находит человека, если имя полное."""
    person = PersonFactory(
        first_name="Алексей", last_name="Петров", status=Status.PUBLISHED
    )
    PersonNameFactory(person=person, value="Алеша Петров")
    photo = PhotoFactory()

    found = resolve_person(photo, None, "Алеша Петров")

    assert [option.person for option in found] == [person]


def test_suggestion_candidate_resolves():
    """Кандидат из подсказки находится и резолвом."""
    PersonFactory(first_name="Сергей", last_name="Иванов", status=Status.PUBLISHED)
    photo = PhotoFactory()

    option = person_options(photo, None, "Иванов")[0]

    assert resolve_person(photo, None, option.person.full_name) == [option]


def test_options_are_person_option():
    """Кандидат несёт готовую подпись, а не модель."""
    PersonFactory(first_name="Сергей", last_name="Иванов", status=Status.PUBLISHED)
    photo = PhotoFactory()

    assert isinstance(person_options(photo, None)[0], PersonOption)


def test_limit_counts_links():
    """Предел считается по отметкам фото."""
    photo = PhotoFactory()
    for _ in range(MAX_PHOTO_PEOPLE):
        PhotoPersonFactory(photo=photo)

    assert people_limit_reached(photo) is True


def test_limit_not_reached_for_few_people():
    """Несколько отметок предела не дают."""
    photo = PhotoFactory()
    PhotoPersonFactory(photo=photo)

    assert people_limit_reached(photo) is False


def test_add_person_creates_link_with_author_and_role():
    """Отметка помнит, кто её поставил, и несёт роль."""
    author = UserFactory()
    photo = PhotoFactory(uploaded_by=author)
    person = PersonFactory(status=Status.PUBLISHED)

    link = add_person(
        photo,
        person,
        user=author,
        role=PhotoPersonRole.COACH,
        position="слева второй",
    )

    assert link is not None
    assert link.added_by == author
    assert link.role == PhotoPersonRole.COACH
    assert link.position == "слева второй"


def test_add_person_twice_returns_none():
    """Двойной клик по «Добавить» не падает и не даёт вторую связь."""
    author = UserFactory()
    photo = PhotoFactory(uploaded_by=author)
    person = PersonFactory(status=Status.PUBLISHED)

    assert add_person(photo, person, user=author, role=PhotoPersonRole.PLAYER)
    assert add_person(photo, person, user=author, role=PhotoPersonRole.PLAYER) is None
    assert PhotoPerson.objects.filter(photo=photo, person=person).count() == 1


def test_add_person_refuses_at_limit():
    """Предел людей на фото отказывает в новой отметке."""
    author = UserFactory()
    photo = PhotoFactory(uploaded_by=author)
    for _ in range(MAX_PHOTO_PEOPLE):
        PhotoPersonFactory(photo=photo)
    person = PersonFactory(status=Status.PUBLISHED)

    assert add_person(photo, person, user=author, role=PhotoPersonRole.PLAYER) is None


def test_remove_person_deletes_link():
    """Снятие отметки убирает связь."""
    photo = PhotoFactory()
    link = PhotoPersonFactory(photo=photo)

    assert remove_person(photo, link) is True
    assert not PhotoPerson.objects.filter(pk=link.pk).exists()


def test_remove_person_of_another_photo_does_nothing():
    """Связь чужого фото по угаданному номеру не снимается."""
    photo = PhotoFactory()
    other_link = PhotoPersonFactory()

    assert remove_person(photo, other_link) is False
    assert PhotoPerson.objects.filter(pk=other_link.pk).exists()


def test_remove_missing_link_returns_false():
    """Повторное снятие отвечает «отметки уже нет»."""
    photo = PhotoFactory()
    link = PhotoPersonFactory(photo=photo)
    remove_person(photo, link)

    assert remove_person(photo, link) is False


def test_create_person_is_draft_for_regular_author():
    """Обычный автор заводит черновик: публикует модератор."""
    author = UserFactory()
    photo = PhotoFactory(uploaded_by=author)
    person = Person(first_name="Сергей", last_name="Иванов")

    created = create_person_for_photo(
        photo, person, user=author, role=PhotoPersonRole.PLAYER
    )

    assert created.pk is not None
    assert created.status == Status.DRAFT
    assert created.created_by == author
    assert PhotoPerson.objects.filter(photo=photo, person=created).exists()


def test_create_person_is_published_for_trusted_author():
    """Доверенный автор публикует человека сразу."""
    author = UserFactory(is_trusted=True)
    photo = PhotoFactory(uploaded_by=author)
    person = Person(first_name="Сергей", last_name="Иванов")

    created = create_person_for_photo(
        photo, person, user=author, role=PhotoPersonRole.PLAYER
    )

    assert created.status == Status.PUBLISHED
    assert created.moderated_by is None


def test_create_person_writes_alias_as_name_variant():
    """Введённое в альбоме написание иначе не найдётся поиском."""
    author = UserFactory()
    photo = PhotoFactory(uploaded_by=author)
    person = Person(first_name="Алексей", last_name="Петров")

    created = create_person_for_photo(
        photo,
        person,
        user=author,
        role=PhotoPersonRole.PLAYER,
        alias="Алеша Петров",
    )

    variant = PersonName.objects.get(person=created, value="Алеша Петров")
    assert variant.kind == NameKind.ALT_SPELLING


def test_create_person_skips_alias_equal_to_full_name():
    """Вариант, совпадающий с полным именем, — мусор для слияния."""
    author = UserFactory()
    photo = PhotoFactory(uploaded_by=author)
    person = Person(first_name="Сергей", last_name="Иванов")

    created = create_person_for_photo(
        photo,
        person,
        user=author,
        role=PhotoPersonRole.PLAYER,
        alias="Сергей Иванов",
    )

    assert not PersonName.objects.filter(person=created).exists()


def test_create_person_rolls_back_when_link_fails(monkeypatch):
    """Человек без отметки при ошибке не остаётся сиротой в архиве."""
    author = UserFactory()
    photo = PhotoFactory(uploaded_by=author)
    person = Person(first_name="Сергей", last_name="Иванов")

    def broken(*args, **kwargs):
        raise RuntimeError("storage down")

    monkeypatch.setattr("apps.photos.people.add_person", broken)

    with pytest.raises(RuntimeError):
        create_person_for_photo(photo, person, user=author, role=PhotoPersonRole.PLAYER)

    assert not Person.objects.filter(first_name="Сергей", last_name="Иванов").exists()


def test_create_person_refuses_at_limit_without_saving_person():
    """При пределе человек не заводится: сирота в архиве хуже отказа."""
    author = UserFactory()
    photo = PhotoFactory(uploaded_by=author)
    for _ in range(MAX_PHOTO_PEOPLE):
        PhotoPersonFactory(photo=photo)
    person = Person(first_name="Сергей", last_name="Иванов")

    with pytest.raises(ValueError):
        create_person_for_photo(photo, person, user=author, role=PhotoPersonRole.PLAYER)

    assert not Person.objects.filter(first_name="Сергей", last_name="Иванов").exists()


def _form(photo, user, data):
    return PhotoPersonForm(data=data, photo=photo, user=user)


def test_form_resolves_single_person():
    """Одно совпадение попадает в `cleaned_data` готовым объектом."""
    person = PersonFactory(
        first_name="Сергей", last_name="Иванов", status=Status.PUBLISHED
    )
    photo = PhotoFactory()

    form = _form(photo, None, {"person_name": "Иванов Сергей", "role": "player"})

    assert form.is_valid()
    assert form.cleaned_data["person"] == person


def test_form_reports_unknown_name():
    """Ненайденное имя даёт ошибку и текст для ссылки «Завести нового»."""
    photo = PhotoFactory()

    form = _form(photo, None, {"person_name": "Неизвестный Никто", "role": "player"})

    assert not form.is_valid()
    assert form.unresolved == "Неизвестный Никто"
    assert "person_name" in form.errors


def test_form_reports_two_namesakes():
    """Два тёзки не угадываются: форма просит уточнить."""
    PersonFactory(first_name="Сергей", last_name="Иванов", status=Status.PUBLISHED)
    PersonFactory(first_name="Сергей", last_name="Иванов", status=Status.PUBLISHED)
    photo = PhotoFactory()

    form = _form(photo, None, {"person_name": "Иванов Сергей", "role": "player"})

    assert not form.is_valid()
    assert len(form.ambiguous) == 2
    assert form.unresolved == ""


def test_form_suggestions_are_candidates():
    """Подсказки берутся из той же выборки, что и резолв."""
    PersonFactory(first_name="Сергей", last_name="Иванов", status=Status.PUBLISHED)
    photo = PhotoFactory()

    form = _form(photo, None, {})

    assert [option.person.last_name for option in form.suggestions] == ["Иванов"]


def test_create_form_requires_confirm_for_namesake():
    """Однофамильца без подтверждения не заводим."""
    PersonFactory(first_name="Сергей", last_name="Иванов", status=Status.PUBLISHED)
    photo = PhotoFactory()

    form = _create_form(
        photo,
        None,
        {
            "first_name": "Сергей",
            "last_name": "Иванов",
            "name_visibility": NameVisibility.FULL,
            "role": "player",
        },
    )

    assert not form.is_valid()
    assert form.duplicates
    assert "first_name" in form.errors


def test_create_form_accepts_confirmed_namesake():
    """С подтверждением форма валидна и отдаёт несохранённого человека."""
    PersonFactory(first_name="Сергей", last_name="Иванов", status=Status.PUBLISHED)
    photo = PhotoFactory()

    form = _create_form(
        photo,
        None,
        {
            "first_name": "Сергей",
            "last_name": "Иванов",
            "name_visibility": NameVisibility.FIRST_NAME_ONLY,
            "role": "player",
            "confirm_duplicate": "on",
        },
    )

    assert form.is_valid()
    person = form.save(commit=False)
    assert person.pk is None
    assert person.name_visibility == NameVisibility.FIRST_NAME_ONLY


def _create_form(photo, user, data):
    return PersonCreateForm(data=data, photo=photo, user=user)


def test_confirmed_form_saves_person_with_link():
    """Подтверждённая форма сохраняется вместе с отметкой через сервис."""
    author = UserFactory()
    photo = PhotoFactory(uploaded_by=author)
    PersonFactory(first_name="Сергей", last_name="Иванов", status=Status.PUBLISHED)

    form = _create_form(
        photo,
        author,
        {
            "first_name": "Сергей",
            "last_name": "Иванов",
            "name_visibility": NameVisibility.FIRST_NAME_ONLY,
            "role": "coach",
            "alias": "Сережа Иванов",
            "confirm_duplicate": "on",
        },
    )
    assert form.is_valid()

    created = create_person_for_photo(
        photo,
        form.save(commit=False),
        user=author,
        role=form.cleaned_data["role"],
        position=form.cleaned_data["position"],
        alias=form.cleaned_data["alias"],
    )

    assert created.pk is not None
    assert PersonName.objects.filter(person=created, value="Сережа Иванов").exists()
    link = PhotoPerson.objects.get(photo=photo, person=created)
    assert link.role == PhotoPersonRole.COACH
    assert link.added_by == author


EDIT_URL = "/photos/{pk}/edit/"
ADD_URL = "/photos/{pk}/people/add/"
NEW_URL = "/photos/{pk}/people/new/"
REMOVE_URL = "/photos/{pk}/people/{link}/remove/"
LOGIN_URL = "/accounts/login/"


def _url(template, photo, link=None):
    return template.format(pk=photo.pk, link=getattr(link, "pk", None))


def _published_person(**kwargs):
    return PersonFactory(status=Status.PUBLISHED, **kwargs)


class TestPhotoPersonAdd:
    """Добавление отметки с правки фото: доступ и ответы формы."""

    def test_anonymous_post_goes_to_login_with_edit_next(self, client, baseball):
        photo = PhotoFactory(sport=baseball)
        edit_url = _url(EDIT_URL, photo)

        response = client.post(
            _url(ADD_URL, photo),
            {"person_name": "Иванов Сергей", "role": "player", "next": edit_url},
        )

        assert response.status_code == 302
        assert response["Location"] == f"{LOGIN_URL}?next={edit_url}"

    def test_get_is_not_allowed(self, client, user, baseball):
        """Отметка — действие, а не страница: GET отвечает 405."""
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        client.force_login(user)

        assert client.get(_url(ADD_URL, photo)).status_code == 405

    def test_author_adds_person(self, client, user, baseball):
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        person = _published_person(first_name="Сергей", last_name="Иванов")
        client.force_login(user)

        response = client.post(
            _url(ADD_URL, photo),
            {
                "person_name": "Иванов Сергей",
                "role": "coach",
                "position": "слева второй",
                "next": _url(EDIT_URL, photo),
            },
        )

        assert response.status_code == 302
        assert response["Location"] == _url(EDIT_URL, photo)
        link = PhotoPerson.objects.get(photo=photo, person=person)
        assert link.role == PhotoPersonRole.COACH
        assert link.added_by == user

    def test_author_sees_success_message(self, client, user, baseball):
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        _published_person(first_name="Сергей", last_name="Иванов")
        client.force_login(user)

        response = client.post(
            _url(ADD_URL, photo),
            {"person_name": "Иванов Сергей", "role": "player"},
            follow=True,
        )

        assert "Человек отмечен на фото" in response.content.decode()

    def test_repeated_add_says_already_marked(self, client, user, baseball):
        """Двойной клик не даёт вторую связь и не молчит."""
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        person = _published_person(first_name="Сергей", last_name="Иванов")
        PhotoPersonFactory(photo=photo, person=person)
        client.force_login(user)

        response = client.post(
            _url(ADD_URL, photo),
            {"person_name": "Иванов Сергей", "role": "player"},
            follow=True,
        )

        assert "уже отмечен" in response.content.decode()
        assert PhotoPerson.objects.filter(photo=photo, person=person).count() == 1

    def test_unknown_name_returns_page_with_field_error(self, client, user, baseball):
        """Невалидный POST отвечает 400: человек видит, что не так с именем."""
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        client.force_login(user)

        response = client.post(
            _url(ADD_URL, photo),
            {"person_name": "Неизвестный Никто", "role": "player"},
        )

        assert response.status_code == 400
        assert "person_name" in response.context["people_form"].errors
        assert PhotoPerson.objects.filter(photo=photo).count() == 0

    def test_foreign_photo_gives_404(self, client, user, other_user, baseball):
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        _published_person(first_name="Сергей", last_name="Иванов")
        client.force_login(other_user)

        response = client.post(
            _url(ADD_URL, photo), {"person_name": "Иванов Сергей", "role": "player"}
        )

        assert response.status_code == 404
        assert not PhotoPerson.objects.filter(photo=photo).exists()

    def test_moderator_of_another_sport_gives_404(self, client, baseball, softball):
        """Область модератора проверяет `can_edit_photo`."""
        photo = PhotoFactory(sport=softball)
        _published_person(first_name="Сергей", last_name="Иванов")
        client.force_login(ModeratorFactory(moderated_sports=[baseball]))

        response = client.post(
            _url(ADD_URL, photo), {"person_name": "Иванов Сергей", "role": "player"}
        )

        assert response.status_code == 404


class TestPhotoPersonRemove:
    """Снятие отметки: только POST и только своя связь."""

    def test_author_removes_link(self, client, user, baseball):
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        link = PhotoPersonFactory(photo=photo)
        client.force_login(user)

        response = client.post(
            _url(REMOVE_URL, photo, link), {"next": _url(EDIT_URL, photo)}
        )

        assert response.status_code == 302
        assert not PhotoPerson.objects.filter(pk=link.pk).exists()

    def test_remove_message_is_shown(self, client, user, baseball):
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        link = PhotoPersonFactory(photo=photo)
        client.force_login(user)

        response = client.post(_url(REMOVE_URL, photo, link), follow=True)

        assert "Отметка снята" in response.content.decode()

    def test_link_of_another_photo_is_not_removed(self, client, user, baseball):
        """Номер связи угадывается по порядку: чужое фото не трогаем."""
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        foreign_link = PhotoPersonFactory()
        client.force_login(user)

        response = client.post(
            _url(REMOVE_URL, photo, foreign_link),
            {"next": _url(EDIT_URL, photo)},
        )

        assert response.status_code == 302
        assert PhotoPerson.objects.filter(pk=foreign_link.pk).exists()

    def test_get_is_not_allowed(self, client, user, baseball):
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        link = PhotoPersonFactory(photo=photo)
        client.force_login(user)

        assert client.get(_url(REMOVE_URL, photo, link)).status_code == 405

    def test_foreign_photo_gives_404(self, client, user, other_user, baseball):
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        link = PhotoPersonFactory(photo=photo)
        client.force_login(other_user)

        assert client.post(_url(REMOVE_URL, photo, link)).status_code == 404


class TestPhotoPersonCreate:
    """Создание человека из правки: доступ и возврат на правку."""

    def test_anonymous_get_goes_to_login(self, client, baseball):
        photo = PhotoFactory(sport=baseball)
        url = _url(NEW_URL, photo)

        response = client.get(url)

        assert response.status_code == 302
        assert response["Location"] == f"{LOGIN_URL}?next={url}"

    def test_foreign_photo_gives_404(self, client, user, other_user, baseball):
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        client.force_login(other_user)

        assert client.get(_url(NEW_URL, photo)).status_code == 404
        assert (
            client.post(_url(NEW_URL, photo), {"first_name": "Сергей"}).status_code
            == 404
        )

    def test_moderator_of_another_sport_gives_404(self, client, baseball, softball):
        photo = PhotoFactory(sport=softball)
        client.force_login(ModeratorFactory(moderated_sports=[baseball]))

        assert client.get(_url(NEW_URL, photo)).status_code == 404

    def test_post_creates_person_and_returns_to_edit(self, client, user, baseball):
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        client.force_login(user)

        response = client.post(
            _url(NEW_URL, photo),
            {
                "first_name": "Сергей",
                "last_name": "Иванов",
                "name_visibility": NameVisibility.FULL,
                "role": "player",
                "alias": "Сережа Иванов",
            },
        )

        assert response.status_code == 302
        assert response["Location"] == _url(EDIT_URL, photo)
        person = Person.objects.get(first_name="Сергей", last_name="Иванов")
        assert person.created_by == user
        assert person.status == Status.DRAFT
        assert PersonName.objects.filter(person=person, value="Сережа Иванов").exists()
        assert PhotoPerson.objects.filter(photo=photo, person=person).exists()


class TestPhotoPeoplePage:
    """Блок «Люди на фото» на странице правки и страница создания."""

    def test_edit_page_lists_marked_people(self, client, user, baseball):
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        person = _published_person(first_name="Сергей", last_name="Иванов")
        PhotoPersonFactory(
            photo=photo, person=person, role=PhotoPersonRole.COACH, position="слева"
        )
        client.force_login(user)

        html = client.get(_url(EDIT_URL, photo)).content.decode()

        assert "Люди на фото" in html
        assert person.public_name in html
        assert "тренер" in html
        assert "слева" in html

    def test_draft_person_has_no_link_and_is_marked(self, client, user, baseball):
        """Черновик выводится подписью: ссылки на неопубликованное нет."""
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        person = PersonFactory(
            first_name="Сергей",
            last_name="Иванов",
            created_by=user,
            status=Status.DRAFT,
        )
        PhotoPersonFactory(photo=photo, person=person)
        client.force_login(user)

        html = client.get(_url(EDIT_URL, photo)).content.decode()

        assert "не опубликован" in html
        assert person.get_absolute_url() not in html

    def test_add_form_is_on_edit_page(self, client, user, baseball):
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        client.force_login(user)

        html = client.get(_url(EDIT_URL, photo)).content.decode()

        assert 'id="person-options"' in html
        assert 'name="person_name"' in html
        assert "Завести нового человека" in html

    def test_people_block_goes_after_photo_form(self, client, user, baseball):
        """Блок людей не вкладывается в форму данных о снимке."""
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        client.force_login(user)

        html = client.get(_url(EDIT_URL, photo)).content.decode()
        form_start = html.index('class="photo-details__form"')
        form_end = html.index("</form>", form_start)

        assert html.index('class="people-block"') > form_end
        assert html.index('class="people-form"') > form_end

    def test_limit_replaces_form_with_note(self, client, user, baseball):
        """При пределе форма не показывается: сервис отказал бы."""
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        for _ in range(MAX_PHOTO_PEOPLE):
            PhotoPersonFactory(photo=photo)
        client.force_login(user)

        html = client.get(_url(EDIT_URL, photo)).content.decode()

        assert 'class="people-form"' not in html
        assert f"не больше {MAX_PHOTO_PEOPLE} человек" in html

    def test_create_page_opens_for_author(self, client, user, baseball):
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        client.force_login(user)

        response = client.get(_url(NEW_URL, photo), {"name": "Сергей Иванов"})

        assert response.status_code == 200
        html = response.content.decode()
        assert "Новый человек" in html
        assert 'value="Сергей Иванов"' in html


class TestPhotoPeopleFlow:
    """Сквозной сценарий подэтапа: завести, отметить, снять."""

    def test_author_creates_marks_and_removes_person(self, client, user, baseball):
        """Весь путь автора за один проход: завести, снять, отметить снова."""
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        client.force_login(user)

        created = client.post(
            _url(NEW_URL, photo),
            {
                "first_name": "Сергей",
                "last_name": "Иванов",
                "name_visibility": NameVisibility.FULL,
                "role": "player",
                "alias": "Сережа Иванов",
            },
        )
        assert created.status_code == 302
        person = Person.objects.get(first_name="Сергей", last_name="Иванов")
        assert person.public_name in client.get(_url(EDIT_URL, photo)).content.decode()

        # Отметка создаётся вместе с человеком, поэтому повторный POST
        # по тому же имени отвечает «уже отмечен», а не меняет роль.
        link = PhotoPerson.objects.get(photo=photo, person=person)
        repeated = client.post(
            _url(ADD_URL, photo),
            {
                "person_name": "Сережа Иванов",
                "role": "coach",
                "next": _url(EDIT_URL, photo),
            },
            follow=True,
        )
        assert "уже отмечен" in repeated.content.decode()
        assert PhotoPerson.objects.filter(photo=photo, person=person).count() == 1

        removed = client.post(_url(REMOVE_URL, photo, link), follow=True)
        assert "Отметка снята" in removed.content.decode()

        added = client.post(
            _url(ADD_URL, photo),
            {
                "person_name": "Сережа Иванов",
                "role": "coach",
                "next": _url(EDIT_URL, photo),
            },
        )
        assert added.status_code == 302
        link = PhotoPerson.objects.get(photo=photo, person=person)
        assert link.role == PhotoPersonRole.COACH
        assert link.added_by == user

    def test_trusted_author_gets_published_person_with_link(self, client, baseball):
        """Доверенный автор публикует человека сразу, ссылка работает."""
        author = UserFactory(is_trusted=True)
        photo = PhotoFactory(uploaded_by=author, sport=baseball)
        client.force_login(author)

        client.post(
            _url(NEW_URL, photo),
            {
                "first_name": "Сергей",
                "last_name": "Иванов",
                "name_visibility": NameVisibility.FULL,
                "role": "player",
            },
        )

        person = Person.objects.get(first_name="Сергей", last_name="Иванов")
        assert person.status == Status.PUBLISHED
        html = client.get(_url(EDIT_URL, photo)).content.decode()
        assert person.get_absolute_url() in html

    def test_moderator_of_same_sport_marks_person(self, client, baseball):
        """Право на отметку — то же `can_edit_photo`, а не авторство."""
        photo = PhotoFactory(sport=baseball)
        person = _published_person(first_name="Сергей", last_name="Иванов")
        client.force_login(ModeratorFactory(moderated_sports=[baseball]))

        response = client.post(
            _url(ADD_URL, photo), {"person_name": "Иванов Сергей", "role": "player"}
        )

        assert response.status_code == 302
        assert PhotoPerson.objects.filter(photo=photo, person=person).exists()

    def test_hidden_person_is_not_in_suggestions(self, client, user, baseball):
        """Скрытого человека нет ни в подсказке, ни в резолве."""
        photo = PhotoFactory(uploaded_by=user, sport=baseball)
        PersonFactory(
            first_name="Скрытый",
            last_name="Человек",
            name_visibility=NameVisibility.HIDDEN,
            status=Status.PUBLISHED,
        )
        client.force_login(user)

        html = client.get(_url(EDIT_URL, photo)).content.decode()

        assert "Скрытый" not in html

    def test_hidden_person_marked_on_photo_is_not_offered_again(self):
        """Отмеченный скрытый человек не возвращается в кандидаты.

        Исключение `hidden` сильнее правила «уже отмеченные видны»: у такого
        человека нет публичной подписи, и предлагать его имя повторно нельзя.
        """
        author = UserFactory()
        photo = PhotoFactory(uploaded_by=author)
        hidden = PersonFactory(
            first_name="Скрытый",
            last_name="Человек",
            name_visibility=NameVisibility.HIDDEN,
            status=Status.PUBLISHED,
        )
        PhotoPersonFactory(photo=photo, person=hidden)

        offered = [option.person for option in person_options(photo, author)]

        assert hidden not in offered

    def test_hidden_person_variant_is_not_found(self):
        """Вариант имени скрытого человека тоже не ищется."""
        person = PersonFactory(
            first_name="Скрытый",
            last_name="Человек",
            name_visibility=NameVisibility.HIDDEN,
            status=Status.PUBLISHED,
        )
        PersonNameFactory(person=person, value="Скрытый Человек")
        photo = PhotoFactory()

        assert resolve_person(photo, None, "Скрытый Человек") == []


class TestPersonMergeFromForm:
    """Человек, заведённый из формы, сливается как любой другой."""

    def test_marked_person_merge_moves_link_to_target(self, baseball):
        """Отметка и вариант имени переезжают к канону при слиянии людей."""
        author = UserFactory()
        photo = PhotoFactory(uploaded_by=author, sport=baseball)
        created = create_person_for_photo(
            photo,
            Person(first_name="Алексей", last_name="Петров"),
            user=author,
            role=PhotoPersonRole.PLAYER,
            alias="Алеша Петров",
        )
        target = PersonFactory(
            first_name="Алексей", last_name="Петров", status=Status.PUBLISHED
        )

        created.merge_into(target)

        link = PhotoPerson.objects.get(photo=photo)
        assert link.person == target
        assert PersonName.objects.filter(person=target, value="Алеша Петров").exists()

    def test_merged_person_is_no_longer_offered(self, baseball):
        """После слияния человек из формы уходит из кандидатов."""
        author = UserFactory()
        photo = PhotoFactory(uploaded_by=author, sport=baseball)
        created = create_person_for_photo(
            photo,
            Person(first_name="Алексей", last_name="Петров"),
            user=author,
            role=PhotoPersonRole.PLAYER,
        )
        target = PersonFactory(
            first_name="Алексей", last_name="Петров", status=Status.PUBLISHED
        )

        created.merge_into(target)

        offered = [option.person for option in person_options(photo, author, "Петров")]

        assert target in offered
        assert created not in offered
