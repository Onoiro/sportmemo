"""Представления публичного каталога.

Списки и страницы команд, событий и людей, а также создание и правка команды
зарегистрированным пользователем.
"""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.http import Http404, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_http_methods

from apps.catalog.emails import notify_moderators_new_team
from apps.catalog.filters import event_filters, team_filters
from apps.catalog.forms import TeamForm
from apps.catalog.models import Event, Person, Team
from apps.catalog.roster import memberships_in_year, roster_years
from apps.comments.services import comments_context
from apps.core.models import Status
from apps.core.schema import compact, event_schema, person_schema, team_schema
from apps.core.seo import page_meta
from apps.core.shortcuts import (
    PHOTO_ORDERING,
    PHOTO_PAGE_SIZE,
    default_sport,
    get_visible_or_404,
    paginate,
    photos_count,
    visible_photos,
)

TEAM_PAGE_SIZE = 24
EVENT_PAGE_SIZE = 20
# Сколько человек показываем на странице команды без фильтра по году.
# Выбранный год показывается целиком: иначе фильтр отсекал бы людей,
# ради которых его и применили.
TEAM_ROSTER_PREVIEW = 12

# Подписи известных ключей `attributes`. Ключ, которого здесь нет, выводится
# как есть: набор атрибутов зависит от вида спорта и задаётся в админке.
ATTRIBUTE_LABELS = {
    "stage": _("Этап"),
    "score": _("Счёт"),
    "result": _("Результат"),
    "innings": _("Иннингов"),
    "teams": _("Команд"),
}

# Служебные ключи: в интерфейсе не показываются.
ATTRIBUTE_SERVICE_KEYS = {"demo"}


def attribute_items(attributes):
    """Атрибуты объекта парами «подпись, значение» для шаблона.

    `attributes` — JSONB без фиксированной схемы: служебные ключи прячем,
    известные подписываем, остальные выводим как есть. Списки склеиваем
    через запятую — таблицу произвольных значений в `dl` не уложить.
    """
    items = []
    for key, value in (attributes or {}).items():
        if key in ATTRIBUTE_SERVICE_KEYS:
            continue
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        items.append({"label": ATTRIBUTE_LABELS.get(key, key), "value": value})
    return items


def gallery_filter(**params):
    """Фильтр галереи для страниц, где он не лежит в `request.GET`.

    `QueryDict` собирается из словаря, а не из строки: slug может содержать
    кириллицу, пробелы и `&`, и склейка строки сломала бы ссылку.
    """
    query = QueryDict("", mutable=True)
    query.update(params)
    return query


def team_memberships(team):
    """Состав команды из опубликованных людей.

    Черновик человека в публичную выдачу не попадает, даже если он записан
    в команду. Срез здесь не делается: режимов показа два, и какой применить,
    решает представление.
    """
    return (
        team.memberships.filter(person__in=Person.public.published())
        .select_related("person")
        .order_by(
            F("year_from").desc(nulls_last=True),
            "person__last_name",
            "person__first_name",
        )
    )


def roster_fallback_year(team):
    """Последний год, известный по команде.

    Нужен незакрытым периодам: человек с `year_from` и пустым `year_to`
    числится в команде до этого года. Считается по членствам и событиям
    команды: у молодой команды без событий верхней границей останется
    год начала членства, и это лучше, чем «до сегодняшнего дня».
    """
    years = [
        year
        for year in team.memberships.values_list("year_from", "year_to")
        for year in year
        if year
    ]
    years += [
        year for year in team.event_teams.values_list("event__year", flat=True) if year
    ]
    return max(years) if years else None


def roster_year(request, years):
    """Год фильтра состава, если он есть в списке доступных.

    Неизвестный или нечисловой параметр считается неприменённым фильтром:
    опечатка в ссылке не должна давать пустую страницу.
    """
    value = request.GET.get("year")
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if year in years else None


def safe_next_url(request):
    """Адрес возврата из формы команды.

    Принимаем только относительный путь: открытый редирект на чужой сайт —
    известная уязвимость фишинга. Абсолютный адрес нашего же сайта тоже
    отбрасываем — он не нужен, а проверка хоста зависела бы от прокси.
    """
    value = request.POST.get("next") or request.GET.get("next") or ""
    # Только путь от корня: «photos/upload/» браузер отсчитал бы от текущего
    # адреса и увёл бы человека на несуществующую страницу.
    if not value.startswith("/") or urlsplit(value).netloc:
        return ""
    if not url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return ""
    return value


def next_with_team(url, team):
    """Адрес возврата с номером созданной команды.

    Форма фото принимает `?team=<pk>`: без этого человек вернулся бы
    в пустую форму и вводил название команды, которую только что завёл.
    """
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.append(("team", str(team.pk)))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def initial_team_name(request):
    """Название из `?name=` для формы создания команды.

    Ссылка «создать команду с таким названием» стоит рядом с ошибкой
    «не удалось найти»: человек не должен вводить то же название второй раз
    и опечататься в нём.
    """
    value = (request.GET.get("name") or "").strip()
    # Ограничение поля: длинное значение всё равно обрезалось бы браузером,
    # но лучше не отдавать его в разметку целиком.
    return value[:300]


def can_edit_team(user, team) -> bool:
    """Может ли пользователь править команду.

    Правит автор и модератор. Всем остальным — 404, а не 403: не раскрываем
    существование чужого черновика. Та же проверка, что в
    `get_visible_or_404`, — правила не должны расходиться.
    """
    if user is None or not user.is_authenticated:
        return False
    return bool(getattr(user, "is_moderator", False)) or team.created_by_id == user.pk


def get_editable_team_or_404(request, slug):
    """Команда для правки или 404.

    Объект берётся из `public` (без удалённых и слитых), право проверяется
    отдельно: удалённую команду не должен править даже автор.
    """
    try:
        team = Team.public.select_related("sport").get(slug=slug)
    except Team.DoesNotExist as exc:
        raise Http404("Команда не найдена") from exc

    if not can_edit_team(request.user, team):
        raise Http404("Команда не найдена")
    return team


@require_GET
def team_list(request):
    """Список опубликованных команд с фильтрами по городу и десятилетию.

    Фильтры разбирает `apps/catalog/filters.py`: там же живут справочники
    вариантов. Невалидное значение не применяется — опечатка в ссылке
    не должна давать пустую страницу.
    """
    filters = team_filters(request)
    context = {
        "page_obj": paginate(request, filters.queryset, TEAM_PAGE_SIZE),
        "filters": filters.panel,
        "meta": page_meta(
            request,
            title=_("Команды"),
            description=_(
                "Команды архива SportMemo: города, годы основания и фотографии."
            ),
            canonical_query=filters.query,
        ),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"label": _("Команды")},
        ],
    }
    return render(request, "catalog/team_list.html", context)


@require_GET
def event_list(request):
    """Список опубликованных событий с фильтрами по году, типу и городу.

    `nulls_last=True` обязателен: в PostgreSQL при `DESC` события без года
    иначе оказались бы первыми. Порядок задаёт `event_filters()`.
    """
    filters = event_filters(request)
    context = {
        "page_obj": paginate(request, filters.queryset, EVENT_PAGE_SIZE),
        "filters": filters.panel,
        "meta": page_meta(
            request,
            title=_("События"),
            description=_("Турниры, серии и товарищеские встречи в архиве SportMemo."),
            canonical_query=filters.query,
        ),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"label": _("События")},
        ],
    }
    return render(request, "catalog/event_list.html", context)


@require_GET
def team_detail(request, slug):
    """Страница команды: визитка, фотографии, события и состав.

    Состав можно отфильтровать по году (`?year=1994`): архив наполняется
    по чужим альбомам, и состав за конкретный год заведомо неполный, поэтому
    фильтр отвечает на вопрос «кто известен по этому году», а не показывает
    полный состав.

    Черновик, скрытое, отклонённое, удалённое и слитое отсеивает
    `get_visible_or_404`: автор и модератор видят страницу с плашкой,
    остальные получают 404.
    """
    team = get_visible_or_404(Team, request=request, slug=slug)
    # Хелпер отдаёт объект без предвыборки связей — перечитываем один раз,
    # чтобы шаблон не делал отдельный запрос за видом спорта.
    team = Team.public.select_related("sport").get(pk=team.pk)

    photos = visible_photos().filter(team=team).select_related("team", "event")
    events = (
        Event.public.published()
        .filter(event_teams__team=team)
        .select_related("sport")
        .annotate(photos_total=photos_count())
        .order_by(F("year").desc(nulls_last=True), "name")
        .distinct()
    )
    # Состав — только из опубликованных людей: черновик человека в публичную
    # выдачу не попадает, даже если он записан в команду.
    memberships = list(team_memberships(team))
    fallback_year = roster_fallback_year(team)
    years = roster_years(memberships, fallback_year)
    selected_year = roster_year(request, years)
    if selected_year:
        # Выбранный год показываем целиком: срез отсекал бы людей, ради
        # которых фильтр и применили.
        roster = memberships_in_year(memberships, selected_year, fallback_year)
    else:
        roster = memberships[:TEAM_ROSTER_PREVIEW]

    context = {
        "team": team,
        # Фильтр не лежит в `request.GET`: кнопка «показать ещё» получает его
        # из `gallery_query`.
        "page_obj": paginate(
            request, photos.order_by(*PHOTO_ORDERING), PHOTO_PAGE_SIZE
        ),
        "gallery_query": gallery_filter(team=team.slug),
        "events": events,
        "roster": roster,
        "roster_total": len(memberships),
        "roster_years": years,
        "selected_year": selected_year,
        "attribute_items": attribute_items(team.attributes),
        "can_edit": can_edit_team(request.user, team),
        "meta": page_meta(
            request,
            title=team.full_name,
            description=team.description
            or _("Команда «%(name)s» в архиве SportMemo.") % {"name": team.name},
            image=team.logo,
            image_alt=team.full_name,
            # Страницу черновика открывают только автор и модератор:
            # в поиске ей делать нечего.
            noindex=not team.is_published,
        ),
        # Разметка только у опубликованной команды: черновик открыт автору
        # и модератору, но поисковику в нём делать нечего.
        "jsonld_blocks": compact(
            team_schema(request, team) if team.is_published else None
        ),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"url": reverse("catalog:team_list"), "label": _("Команды")},
            {"label": team.name},
        ],
        **comments_context(request, team),
    }
    return render(request, "catalog/team_detail.html", context)


@require_GET
def event_detail(request, slug):
    """Страница события: визитка, команды-участники и фотографии."""
    event = get_visible_or_404(Event, request=request, slug=slug)
    event = Event.public.select_related("sport").get(pk=event.pk)

    photos = visible_photos().filter(event=event).select_related("team", "event")
    # Команда-черновик остаётся в списке: её название выводится текстом,
    # ссылки на неопубликованную команду не будет.
    participants = event.event_teams.select_related("team")

    context = {
        "event": event,
        "page_obj": paginate(
            request, photos.order_by(*PHOTO_ORDERING), PHOTO_PAGE_SIZE
        ),
        "gallery_query": gallery_filter(event=event.slug),
        "participants": participants,
        "attribute_items": attribute_items(event.attributes),
        "meta": page_meta(
            request,
            title=f"{event.name} ({event.year})" if event.year else event.name,
            description=event.description
            or _("Событие «%(name)s» в архиве SportMemo.") % {"name": event.name},
            noindex=not event.is_published,
        ),
        "jsonld_blocks": compact(
            event_schema(request, event) if event.is_published else None
        ),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"url": reverse("catalog:event_list"), "label": _("События")},
            {"label": event.name},
        ],
        **comments_context(request, event),
    }
    return render(request, "catalog/event_detail.html", context)


@require_GET
def person_detail(request, slug):
    """Страница человека: главное фото, биография, команды и фотографии.

    Фамилия несовершеннолетних не публикуется: заголовок, крошки и варианты
    имён строятся только из `public_name` и `is_full_name_public`.
    """
    person = get_visible_or_404(Person, request=request, slug=slug)
    # Хелпер отдаёт объект без предвыборки — перечитываем один раз, иначе
    # шаблон сделает отдельный запрос за главным фото.
    person = Person.public.select_related("avatar").get(pk=person.pk)

    photos = visible_photos().filter(photo_persons__person=person)
    # Главное фото: аватар, если он видим; иначе — самое свежее видимое фото
    # с этим человеком. Невидимый аватар игнорируется: правило прав и статуса
    # живёт в `Photo.is_public`.
    hero_photo = None
    if person.avatar_id and person.avatar.is_public:
        hero_photo = person.avatar
    else:
        hero_photo = photos.order_by(*PHOTO_ORDERING).first()

    # Команда-черновик остаётся в списке: её название выводится текстом.
    memberships = person.memberships.select_related("team").order_by(
        F("year_from").desc(nulls_last=True), "team__name"
    )

    context = {
        "person": person,
        "hero_photo": hero_photo,
        "memberships": memberships,
        # Варианты имён скрываются здесь, а не в шаблоне: прозвище раскрыло бы
        # скрытую фамилию, и правило не должно зависеть от разметки.
        "name_variants": person.names.all() if person.is_full_name_public else [],
        "page_obj": paginate(
            request,
            photos.select_related("team", "event").order_by(*PHOTO_ORDERING),
            PHOTO_PAGE_SIZE,
        ),
        "gallery_query": gallery_filter(person=person.slug),
        "attribute_items": attribute_items(person.attributes),
        # Заголовок и описание строятся только из `public_name`:
        # `display_name` содержит полную фамилию и в метатеги не попадает.
        # При скрытом имени именная фраза читалась бы как ошибка
        # («Человек Имя не указано…»), поэтому берём общее описание сайта.
        "meta": page_meta(
            request,
            title=person.public_name,
            description=(
                _("Человек %(name)s в архиве SportMemo.") % {"name": person.public_name}
                if person.is_full_name_public
                else ""
            ),
            image=hero_photo.preview if hero_photo else None,
            image_alt=person.public_name,
            noindex=not person.is_published,
        ),
        # `person_schema` сам возвращает `None` для скрытого имени:
        # правило живёт в сборке разметки, а не в представлении.
        "jsonld_blocks": compact(
            person_schema(request, person) if person.is_published else None
        ),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"label": person.public_name},
        ],
    }
    return render(request, "catalog/person_detail.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def team_create(request):
    """Создание команды: сохраняется черновиком и ждёт модератора.

    Вид спорта берётся из `default_sport()`: выбора в интерфейсе нет, схема
    мультиспортная (инвариант № 1). Активных видов спорта нет — форма
    недоступна: показать сообщение честнее, чем падать на пустом `sport`.
    """
    sport = default_sport()
    # Адрес возврата нужен форме фото: человек заводит команду прямо
    # из формы загрузки и должен вернуться туда же, а не на визитку.
    next_url = safe_next_url(request)

    if request.method == "POST" and sport is not None:
        form = TeamForm(request.POST, request.FILES, sport=sport)
        if form.is_valid():
            team = form.save(commit=False)
            team.sport = sport
            team.status = Status.DRAFT
            team.created_by = request.user
            team.save()
            notify_moderators_new_team(team)
            messages.success(
                request,
                _("Команда создана и отправлена на проверку модератору."),
            )
            if next_url:
                return redirect(next_with_team(next_url, team))
            return redirect(team.get_absolute_url())
    elif sport is not None:
        form = TeamForm(sport=sport, initial={"name": initial_team_name(request)})
    else:
        form = None

    context = {
        "form": form,
        "team": None,
        "next_url": next_url,
        "form_action": reverse("catalog:team_create"),
        "form_title": _("Новая команда"),
        "submit_label": _("Создать"),
        "sport": sport,
        "meta": page_meta(
            request,
            title=_("Новая команда"),
            noindex=True,
            nofollow=True,
        ),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"url": reverse("catalog:team_list"), "label": _("Команды")},
            {"label": _("Новая команда")},
        ],
    }
    return render(request, "catalog/team_form.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def team_update(request, slug):
    """Правка своей команды.

    Опубликованная команда остаётся опубликованной: правки автора не должны
    ждать модератора, а изменение всё равно попадает в историю. Вернуться
    к повторной модерации можно, если появится вандализм.
    """
    team = get_editable_team_or_404(request, slug)

    if request.method == "POST":
        form = TeamForm(request.POST, request.FILES, instance=team, sport=team.sport)
        if form.is_valid():
            form.save()
            messages.success(request, _("Изменения сохранены."))
            return redirect(team.get_absolute_url())
    else:
        form = TeamForm(instance=team, sport=team.sport)

    context = {
        "form": form,
        "team": team,
        "form_action": reverse("catalog:team_update", args=[team.slug]),
        "form_title": _("Редактирование команды"),
        "submit_label": _("Сохранить"),
        "sport": team.sport,
        "meta": page_meta(
            request,
            title=_("Редактирование команды"),
            noindex=True,
            nofollow=True,
        ),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"url": reverse("catalog:team_list"), "label": _("Команды")},
            {"url": team.get_absolute_url(), "label": team.name},
            {"label": _("Редактирование")},
        ],
    }
    return render(request, "catalog/team_form.html", context)


@login_required
@require_GET
def my_teams(request):
    """Свои команды: любые статусы, включая мягко удалённые.

    Менеджер по умолчанию, а не `public`: автор должен видеть и то, что
    модератор убрал, — иначе команда исчезнет без объяснения.
    """
    teams = (
        Team.objects.filter(created_by=request.user)
        .select_related("sport")
        .order_by("-created_at")
    )
    context = {
        "teams": teams,
        "meta": page_meta(
            request,
            title=_("Мои команды"),
            noindex=True,
            nofollow=True,
        ),
        "breadcrumbs": [
            {"url": reverse("core:home"), "label": _("Главная")},
            {"label": _("Мои команды")},
        ],
    }
    return render(request, "catalog/my_teams.html", context)
