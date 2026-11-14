"""Правило вывода происхождения снимка: подпись, владелец, уточнение.

Порядок проверок живёт в `apps/photos/source.py`, а не в шаблоне: привязанный
`Source` побеждает (подробный блок печатается отдельно), пустое происхождение
значит «не указано», а имя владельца личного архива берётся из автора фото,
чтобы после анонимизации аккаунта в базе не осталось чужого имени.
"""

import pytest

from apps.photos.models import SourceOrigin
from apps.photos.source import SourceDisplay, source_display
from tests.factories import PhotoFactory, SourceFactory, UserFactory

pytestmark = pytest.mark.django_db


def test_bound_source_wins():
    """Привязан `Source` — упрощённый блок не нужен вовсе."""
    photo = PhotoFactory(
        source=SourceFactory(),
        source_origin=SourceOrigin.PRESS,
        source_note="Советский спорт, 1994",
    )

    assert source_display(photo) is None


def test_empty_origin_gives_nothing():
    """Пустая строка — «человек не заполнял», а не ответ."""
    photo = PhotoFactory(source_origin="")

    assert source_display(photo) is None


def test_personal_without_author_uses_neutral_holder():
    """Анонимизированный аккаунт даёт нейтральную подпись владельца."""
    photo = PhotoFactory(source_origin=SourceOrigin.PERSONAL, uploaded_by=None)

    shown = source_display(photo)

    assert shown == SourceDisplay(
        origin_label="из моего личного архива",
        holder="Из личного архива автора",
        note="",
    )


def test_personal_holder_is_author_name():
    """Имя владельца личного архива выводится из автора фото."""
    author = UserFactory(display_name="Сергей Иванов")
    photo = PhotoFactory(source_origin=SourceOrigin.PERSONAL, uploaded_by=author)

    assert source_display(photo).holder == "Сергей Иванов"


def test_personal_without_display_name_uses_neutral_holder():
    """Пустое имя автора тоже даёт нейтральную подпись."""
    author = UserFactory(display_name="")
    photo = PhotoFactory(source_origin=SourceOrigin.PERSONAL, uploaded_by=author)

    assert source_display(photo).holder == "Из личного архива автора"


@pytest.mark.parametrize(
    ("origin", "label"),
    [
        (SourceOrigin.PERSONAL, "из моего личного архива"),
        (SourceOrigin.OTHER_ARCHIVE, "из архива другого человека"),
        (SourceOrigin.PRESS, "газета, журнал, книга"),
        (SourceOrigin.INTERNET, "из интернета"),
        (SourceOrigin.UNKNOWN, "не знаю"),
    ],
)
def test_every_origin_has_its_own_label(origin, label):
    """Подписи берутся из `SourceOrigin`.

    Второй набор подписей разошёлся бы с админкой и с `SourceKind`.
    """
    photo = PhotoFactory(source_origin=origin, uploaded_by=None)

    assert source_display(photo).origin_label == label


def test_press_with_note_has_no_holder():
    """У печатного источника владельца нет, уточнение идёт как есть."""
    photo = PhotoFactory(
        source_origin=SourceOrigin.PRESS,
        source_note="  Советский спорт, 1994  ",
    )

    shown = source_display(photo)

    assert shown.origin_label == "газета, журнал, книга"
    assert shown.holder == ""
    assert shown.note == "Советский спорт, 1994"
