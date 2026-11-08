"""Вкладки кабинета автора: отбор, счётчики, право и действия.

Правило отбора живёт здесь, а не в представлении и не в шаблоне: один и тот
же `Q` идёт и в выборку, и в `Count(filter=...)`, поэтому вкладки и счётчики
не могут разойтись. Шаблон получает готовую панель и не сравнивает статусы.

Раскладка состояний согласована с `Photo.upload_state`: вкладка «Не в архиве»
собирает `rejected`, `hidden`, `deleted` и `failed` — ровно те фото, которых
нет и не будет в публичном архиве и которые не ждут проверки. Иначе скрытое
модератором фото не попало бы ни в одну вкладку, кроме «Все», и сумма
вкладок не сошлась бы с общим числом.

Убранное автором фото живёт только во вкладке «Убранные»: иначе оно
попадало бы и в «Все», и в свою вкладку, а «Все» снова стало бы свалкой.
"""

from dataclasses import dataclass

from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import Status
from apps.photos.models import Photo, ProcessingStatus

TAB_ALL = "all"
TAB_ARCHIVE = "archive"
TAB_REVIEW = "review"
TAB_FAILED = "failed"  # подпись «Не в архиве»
TAB_REMOVED = "removed"

# Порядок вкладок в панели: от широкого к узкому, уборка — последней.
TAB_ORDER = (TAB_ALL, TAB_ARCHIVE, TAB_REVIEW, TAB_FAILED, TAB_REMOVED)

TAB_LABELS = {
    TAB_ALL: _("Все"),
    TAB_ARCHIVE: _("В архиве"),
    TAB_REVIEW: _("На проверке"),
    TAB_FAILED: _("Не в архиве"),
    TAB_REMOVED: _("Убранные"),
}

# Подпись под панелью: на «Убранных» человек должен понимать, что уборка
# из списка — не удаление из архива.
TAB_NOTES = {
    TAB_REMOVED: _("Эти фото остаются в архиве и открываются по ссылке"),
}


@dataclass(frozen=True)
class CabinetTab:
    """Вкладка панели: ключ, подпись, счётчик и отметка выбранной."""

    key: str
    label: str
    count: int
    selected: bool = False


def _archive_q():
    """Фото, которые есть в публичном архиве."""
    return Q(
        status=Status.PUBLISHED,
        deleted_at__isnull=True,
        processing_status=ProcessingStatus.PROCESSED,
    )


def _review_q():
    """Фото, которые ещё ждут решения модератора."""
    return (
        Q(deleted_at__isnull=True)
        & ~Q(processing_status=ProcessingStatus.FAILED)
        & (
            Q(
                processing_status__in=(
                    ProcessingStatus.UPLOADED,
                    ProcessingStatus.PROCESSING,
                )
            )
            | Q(status=Status.DRAFT)
        )
    )


def selected_tab(request) -> str:
    """Значение `?tab=`, невалидное считается «Все».

    Опечатка в ссылке не должна давать пустую страницу — то же правило,
    что у фильтров каталога.
    """
    value = (request.GET.get("tab") or "").strip()
    return value if value in TAB_ORDER else TAB_ALL


def tab_filter(tab):
    """Условие отбора для вкладки.

    «Не в архиве» считается дополнением к двум другим вкладкам, а не
    перечислением статусов: порядок проверок в `upload_state` сильнее
    статуса (фото в обработке читается как «Обрабатывается», даже если
    его уже скрыли), и перечисление дало бы фото в двух вкладках сразу.
    Так сумма трёх вкладок равна «Все» по построению.
    """
    visible = Q(cabinet_removed_at__isnull=True)
    if tab == TAB_ARCHIVE:
        return visible & _archive_q()
    if tab == TAB_REVIEW:
        return visible & _review_q()
    if tab == TAB_FAILED:
        return visible & ~(_archive_q() | _review_q())
    if tab == TAB_REMOVED:
        return Q(cabinet_removed_at__isnull=False)
    return visible


def cabinet_photos(user, tab):
    """Фото автора для вкладки.

    Менеджер по умолчанию, а не `public`: кабинет показывает и то, что
    убрал модератор, — как раз оно чаще всего и мешает в списке.
    `merged_into` предзагружается для `is_page_available`: карточка решает,
    ставить ли ссылку.
    """
    return (
        Photo.objects.filter(uploaded_by=user)
        .filter(tab_filter(tab))
        .select_related("team", "event", "merged_into")
        .order_by("-created_at")
    )


def cabinet_counts(user) -> dict:
    """Счётчики вкладок одним запросом.

    Пять вкладок не должны стоить пять запросов, поэтому один `aggregate`
    с пятью `Count(filter=...)`.
    """
    return Photo.objects.filter(uploaded_by=user).aggregate(
        **{tab: Count("pk", filter=tab_filter(tab)) for tab in TAB_ORDER}
    )


def cabinet_tabs(user, selected: str) -> tuple:
    """Готовая панель вкладок для шаблона."""
    counts = cabinet_counts(user)
    return tuple(
        CabinetTab(
            key=tab,
            label=str(TAB_LABELS[tab]),
            count=counts.get(tab, 0),
            selected=tab == selected,
        )
        for tab in TAB_ORDER
    )


def tab_note(tab: str) -> str:
    """Подпись под панелью для вкладки; пусто — подписи нет."""
    return str(TAB_NOTES.get(tab, ""))


def can_manage_cabinet(user, photo) -> bool:
    """Может ли пользователь убирать и возвращать это фото.

    Только автор: чужой кабинет не дело модератора, и уборка не является
    решением модерации.
    """
    if user is None or not user.is_authenticated:
        return False
    return photo.uploaded_by_id == user.pk


def remove_from_cabinet(photo) -> bool:
    """Убрать фото из списка кабинета.

    `False` — фото уже убрано: это гонка двух вкладок, а не ошибка
    (то же правило, что у сервиса решений 4.3).
    """
    return photo.remove_from_cabinet()


def restore_to_cabinet(photo) -> bool:
    """Вернуть фото в список кабинета; `False` — оно и так в списке."""
    return photo.restore_to_cabinet()
