"""Карта сайта: индекс и секции.

Правило публичности берётся из `apps/core/shortcuts.py`, а не собирается
заново: список адресов в карте и список на странице обязаны совпадать.
Черновики, скрытое, отклонённое, удалённое и слитое в карту не попадают.

Модели импортируются внутри методов: на уровне модуля это дало бы
зависимость `core` → `catalog`/`photos` (правило `shortcuts.py`).
"""

from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class StaticSitemap(Sitemap):
    """Постоянные страницы архива: главная, команды, события, годы.

    Даты изменения у них нет: содержимое собирается из других объектов,
    и «когда обновилась главная» — не факт, а догадка.
    """

    i18n = False

    def items(self):
        return [
            "core:home",
            "catalog:team_list",
            "catalog:event_list",
            "core:year_list",
        ]

    def location(self, item):
        return reverse(item)


class _ArchiveSitemap(Sitemap):
    """Общее для содержательных секций: только публичное, `lastmod`.

    `i18n = False` обязателен: языковых префиксов в адресах нет, и `i18n`
    продублировал бы каждый адрес по числу языков в `LANGUAGES`.
    """

    i18n = False

    def items(self):
        return self.published()

    def published(self):
        """Публичная выборка секции."""
        raise NotImplementedError

    def lastmod(self, item):
        return item.updated_at


class TeamSitemap(_ArchiveSitemap):
    """Страницы опубликованных команд."""

    def published(self):
        from apps.catalog.models import Team

        return Team.public.published()


class EventSitemap(_ArchiveSitemap):
    """Страницы опубликованных событий."""

    def published(self):
        from apps.catalog.models import Event

        return Event.public.published()


class PersonSitemap(_ArchiveSitemap):
    """Страницы опубликованных людей.

    Человек со скрытым именем в карте есть: страница открыта, и фамилии
    на ней нет — `public_name` показывает нейтральную подпись.
    """

    def published(self):
        from apps.catalog.models import Person

        return Person.public.published()


class PhotoSitemap(_ArchiveSitemap):
    """Страницы фото с подтверждёнными правами."""

    def published(self):
        from apps.core.shortcuts import visible_photos

        return visible_photos()
