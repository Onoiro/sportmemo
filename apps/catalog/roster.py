"""Фильтр состава команды по годам.

Архив наполняется по чужим альбомам и вырезкам, поэтому состав за конкретный
год заведомо неполный. Модуль отвечает на узкий вопрос — «кто известен по этому
году» — и не обещает полноты.

Логика периодов живёт здесь, а не в представлении: её нужно проверять
unit-тестами без обращений к базе, а формат подписи годов должен быть один
на всю страницу.
"""

from apps.catalog.models import Role

# Порядок ролей в списке: сначала тренерский штаб, затем игроки. Держим рядом
# с логикой, а не в шаблоне: та же сортировка нужна тестам.
ROLE_ORDER = {
    Role.COACH: 0,
    Role.MANAGER: 1,
    Role.STAFF: 2,
    Role.PLAYER: 3,
}


def membership_bounds(membership, fallback_year=None):
    """Границы периода человека в команде: `(с какого года, по какой)`.

    Незакрытый период (`year_from` есть, `year_to` пуст) заканчивается
    последним годом, известным по команде: иначе человек либо «размазался»
    бы на все годы до сегодняшнего, либо не попал бы никуда, кроме года начала.
    Один `year_to` без `year_from` даёт односторонний период: утверждать,
    что человек играл раньше, нельзя.

    Возвращает `(None, None)`, если годов нет вовсе.
    """
    if membership.year_from:
        return membership.year_from, (
            membership.year_to or fallback_year or membership.year_from
        )
    if membership.year_to:
        return None, membership.year_to
    return None, None


def membership_years(membership, fallback_year=None):
    """Годы, в которые человек числится в команде.

    Период с известным началом раскрывается в диапазон: «1992–1998» — это
    семь лет, и человек должен находиться в каждом из них. Период без начала
    даёт один год — последний известный.
    """
    start, end = membership_bounds(membership, fallback_year)
    if start is None:
        return [end] if end else []
    return list(range(start, end + 1))


def roster_years(memberships, fallback_year=None):
    """Годы состава, от новых к старым.

    В список попадают только годы, в которые известен хотя бы один человек:
    пустой год в фильтре выглядел бы как обещание данных, которых нет.
    """
    years = set()
    for membership in memberships:
        years.update(membership_years(membership, fallback_year))
    return sorted(years, reverse=True)


def sort_key(membership):
    """Порядок людей внутри года: роль, затем фамилия и имя.

    Сортировка модели (`-year_from`) при фильтре по году смысла не имеет:
    все люди относятся к одному году.
    """
    person = membership.person
    return (
        ROLE_ORDER.get(membership.role, len(ROLE_ORDER)),
        person.last_name,
        person.first_name,
        membership.year_from or 0,
    )


def memberships_in_year(memberships, year, fallback_year=None):
    """Люди, известные по этому году, в порядке `sort_key`.

    Два членства одного человека в одном году (игрок и тренер) дают две
    строки: они различаются ролью, годами и амплуа, и склеивать их нельзя
    без потери сведений.
    """
    matched = [
        membership
        for membership in memberships
        if year in membership_years(membership, fallback_year)
    ]
    return sorted(matched, key=sort_key)
