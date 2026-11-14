"""Правило вывода происхождения снимка для страницы фото.

Автор отвечает на один простой вопрос — откуда у него файл, — и страница
печатает готовую подпись. Шаблон не сравнивает поля: порядок проверок
(привязанный `Source` побеждает, пустая строка значит «не указано»)
должен жить в одном месте, иначе блок на странице и блок в админке
разойдутся.

Отдельный модуль, а не свойство модели: подпись владельца личного архива
берётся из `uploaded_by`, и в модели это дало бы скрытый запрос на каждое
обращение к свойству — в том числе в списках, где автор не нужен.
Представление ставит `select_related` само.
"""

from dataclasses import dataclass

from django.utils.translation import gettext as _

from apps.photos.models import SourceOrigin


@dataclass(frozen=True)
class SourceDisplay:
    """Готовые строки блока «Источник»: подпись, владелец, уточнение."""

    origin_label: str
    holder: str
    note: str


def source_display(photo) -> SourceDisplay | None:
    """Что показать в блоке «Источник». `None` — показывать нечего.

    Привязанный `Source` даёт `None`: шаблон выводит подробный блок сам,
    и второй набор подписей рядом с первым разошёлся бы с ним. Пустое
    происхождение тоже `None` — это «человек ещё не заполнял», а не ответ.
    """
    if photo.source_id or not photo.source_origin:
        return None

    holder = ""
    if photo.source_origin == SourceOrigin.PERSONAL:
        # Имя владельца личного архива не хранится текстом: после
        # анонимизации аккаунта оно осталось бы в базе навсегда.
        author = photo.uploaded_by
        holder = (author.display_name if author else "") or str(
            _("Из личного архива автора")
        )

    return SourceDisplay(
        origin_label=str(photo.get_source_origin_display()),
        holder=holder,
        note=(photo.source_note or "").strip(),
    )
