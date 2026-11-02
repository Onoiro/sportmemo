"""Жалобы: правило «открыта», отбор по области и карточки списка.

Правило «жалоба открыта» живёт в `open_reports` и `is_open` рядом: список
и действие обязаны отвечать одинаково. Разойдись они — жалоба была бы видна
в списке, но не решалась бы, а это худший вид дефекта: модератор нажимает
кнопку и не понимает, почему ничего не происходит.

Свою жалобу модератор не разбирает: разбирать собственное заявление —
то же, что одобрять свой черновик. Второй человек не нужен, а решение
выглядит как проверка. Правило стоит и в выборке, и в действии: номер
жалобы угадывается по порядку, и POST обошёл бы скрытую строку списка.

Модели жалоб импортируются напрямую: это своё приложение, цикла нет.
Чужие модели (`catalog`, `photos`) тянутся через `content_type`, иначе
модуль потянул бы за собой всё приложение ради одного заголовка.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.shortcuts import paginate
from apps.moderation.models import Report, ReportStatus

# Порция списка. Карточка жалобы легче карточки фото (превью цели
# не грузим), но 20 — уже предел для телефона.
REPORT_PAGE_SIZE = 20

# Жалоба, которую ещё можно разобрать. Решённые и отклонённые живут
# в админке: интерфейс про работу, а не про отчёт.
OPEN_STATUSES = (ReportStatus.NEW, ReportStatus.IN_REVIEW)


def open_reports(user):
    """Открытые жалобы области модератора, свежие сверху.

    Жалоба без вида спорта видна всем: `can_moderate` на объекте без вида
    спорта отвечает «можно», и фильтр списка обязан совпадать с ним.
    Иначе такая жалоба открывалась бы по прямой ссылке, но терялась
    в списке — а жалоба не должна потеряться.

    Свои жалобы исключаются через `Q(reporter__isnull=True) | ~Q(...)`:
    `exclude(reporter=user)` выбросил бы и анонимные жалобы, потому что
    сравнение с `NULL` не бывает истинным.
    """
    qs = Report.objects.filter(status__in=OPEN_STATUSES).select_related(
        "content_type", "reporter", "sport"
    )

    sport_ids = user.moderated_sport_ids()
    if sport_ids is not None:
        qs = qs.filter(Q(sport__isnull=True) | Q(sport_id__in=sport_ids))

    user_id = getattr(user, "pk", None)
    if user_id is not None:
        qs = qs.filter(Q(reporter__isnull=True) | ~Q(reporter_id=user_id))

    # Свежие сверху: свежая жалоба о нарушении прав не должна ждать неделю,
    # в отличие от очереди черновиков, где старое важнее.
    return qs.order_by("-created_at", "-pk")


def is_open(report) -> bool:
    """Можно ли ещё разобрать жалобу — то же правило для одного объекта."""
    return report.status in OPEN_STATUSES


def is_own_report(report, user) -> bool:
    """Подал ли жалобу этот пользователь. Свою разбирает другой человек.

    Анонимная жалоба «своей» не бывает: у `reporter` пусто, и без проверки
    на пустой `pk` модератор с заглушкой вместо пользователя не смог бы
    её открыть.
    """
    user_id = getattr(user, "pk", None)
    if user_id is None:
        return False
    return report.reporter_id == user_id


def _targets(reports) -> dict[tuple[int, int], object]:
    """Цели жалоб пачкой: `(content_type_id, object_id) → объект`.

    Generic-FK даёт запрос на карточку, поэтому цели грузятся одним
    запросом на модель: число запросов не должно расти вместе с порцией.

    Берём `objects`, а не `public`: удалённая цель нужна, чтобы карточка
    сказала «объект удалён», а не промолчала.
    """
    by_type = defaultdict(list)
    for report in reports:
        by_type[report.content_type_id].append(report.object_id)

    loaded = {}
    for ct_id, ids in by_type.items():
        # Кэш `ContentType`: запроса за строкой не будет.
        model = ContentType.objects.get_for_id(ct_id).model_class()
        if model is None:
            continue
        for obj in model.objects.filter(pk__in=ids):
            loaded[(ct_id, obj.pk)] = obj
    return loaded


def _title(obj) -> str:
    """Заголовок цели.

    У фото `str()` отдаёт «Фото #12», а модератору нужно название снимка.
    У команды и события заголовок уже собран в `str()` вместе с городом
    и годом.
    """
    title = getattr(obj, "title", "")
    if title:
        return title
    return str(obj)


def _target_fields(target):
    """Заголовок, адрес и пометка цели.

    Ссылка строится только у живого объекта: удалённая и слитая цель
    отдала бы 404, а ссылка в карточке жалобы должна вести туда, куда
    обещает. Модель вне белого списка может не иметь адреса вовсе.
    """
    if target is None:
        return str(_("объект недоступен")), None, ""
    if getattr(target, "is_deleted", False):
        return _title(target), None, str(_("объект удалён"))
    if getattr(target, "merged_into_id", None):
        return _title(target), None, str(_("объект удалён"))

    get_url = getattr(target, "get_absolute_url", None)
    if get_url is None:
        return _title(target), None, ""
    try:
        url = get_url()
    except Exception:
        # Чужой метод адреса может упасть на неполных данных. Карточка
        # без ссылки лучше, чем страница с ошибкой.
        return _title(target), None, ""
    return _title(target), url, ""


def _reporter(report) -> str:
    """Подпись заявителя. Анонимная жалоба так и называется."""
    reporter = report.reporter
    if reporter is None:
        return str(_("анонимно"))
    return reporter.display_name or reporter.email


@dataclass(frozen=True)
class ReportCard:
    """Карточка жалобы с готовыми полями.

    Шаблон печатает значения и ничего не сравнивает — то же правило,
    что у `Photo.upload_state`, `Event.date_label` и `QueueCard`.
    """

    report_id: int
    reason_label: str
    comment: str
    status_label: str
    is_new: bool
    target_title: str
    target_url: str | None
    target_note: str
    reporter: str
    created_at: datetime


def build_card(report, target) -> ReportCard:
    """Карточка жалобы. `target=None` — цель не нашлась."""
    title, url, note = _target_fields(target)
    return ReportCard(
        report_id=report.pk,
        reason_label=str(report.get_reason_display()),
        comment=report.comment,
        status_label=str(report.get_status_display()),
        is_new=report.status == ReportStatus.NEW,
        target_title=title,
        target_url=url,
        target_note=note,
        reporter=_reporter(report),
        created_at=report.created_at,
    )


def reports_page(user, request):
    """Страница списка: `(page_obj, cards)` со свежих жалоб."""
    page_obj = paginate(request, open_reports(user), REPORT_PAGE_SIZE)
    targets = _targets(page_obj.object_list)
    cards = [
        build_card(report, targets.get((report.content_type_id, report.object_id)))
        for report in page_obj.object_list
    ]
    return page_obj, cards
