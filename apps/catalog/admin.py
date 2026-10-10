"""Админка каталога: команды, люди, события."""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import (
    Event,
    EventTeam,
    Person,
    PersonName,
    Team,
    TeamMembership,
)
from apps.core.admin_mixins import (
    MERGE_ACTIONS,
    MODERATION_ACTIONS,
    BaseHistoryAdmin,
    MergeActionsMixin,
    ModerationActionsMixin,
)


class TeamMembershipInline(admin.TabularInline):
    model = TeamMembership
    extra = 0
    autocomplete_fields = ("person",)
    fields = ("person", "role", "year_from", "year_to", "number", "position")


class EventTeamInline(admin.TabularInline):
    model = EventTeam
    extra = 0
    autocomplete_fields = ("team",)
    fields = ("team", "result_note")


class PersonNameInline(admin.TabularInline):
    model = PersonName
    extra = 0
    fields = ("value", "kind", "note")


@admin.register(Team)
class TeamAdmin(MergeActionsMixin, ModerationActionsMixin, BaseHistoryAdmin):
    list_display = (
        "name",
        "city",
        "sport",
        "founded_year",
        "status",
        "deleted_at",
        "created_by",
    )
    list_filter = ("sport", "status", "deleted_at", "city")
    search_fields = ("name", "city", "slug", "description")
    autocomplete_fields = ("created_by", "merged_into")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = (
        "moderated_by",
        "moderated_at",
        "created_at",
        "updated_at",
        "deleted_at",
    )
    inlines = (TeamMembershipInline, EventTeamInline)
    actions = (*MERGE_ACTIONS, *MODERATION_ACTIONS)
    fieldsets = (
        (None, {"fields": ("sport", "name", "slug", "city", "founded_year")}),
        (_("Содержание"), {"fields": ("description", "logo", "attributes")}),
        (
            _("Публикация"),
            {"fields": ("status", "moderated_by", "moderated_at", "moderation_reason")},
        ),
        (
            _("Служебное"),
            {
                "fields": (
                    "created_by",
                    "merged_into",
                    "deleted_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )


@admin.register(Person)
class PersonAdmin(MergeActionsMixin, ModerationActionsMixin, BaseHistoryAdmin):
    list_display = (
        "public_name",
        "first_name",
        "last_name",
        "birth_year",
        "name_visibility",
        "status",
        "deleted_at",
    )
    list_filter = ("status", "name_visibility", "deleted_at", "is_claimed")
    search_fields = ("first_name", "last_name", "middle_name", "display_name", "slug")
    autocomplete_fields = ("created_by", "merged_into", "avatar")
    readonly_fields = (
        "moderated_by",
        "moderated_at",
        "created_at",
        "updated_at",
        "deleted_at",
    )
    inlines = (PersonNameInline,)
    actions = (*MERGE_ACTIONS, *MODERATION_ACTIONS)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "first_name",
                    "last_name",
                    "middle_name",
                    "display_name",
                    "slug",
                )
            },
        ),
        (_("Приватность"), {"fields": ("name_visibility", "is_claimed")}),
        (
            _("Биография"),
            {"fields": ("birth_year", "death_year", "bio", "avatar", "attributes")},
        ),
        (
            _("Публикация"),
            {"fields": ("status", "moderated_by", "moderated_at", "moderation_reason")},
        ),
        (
            _("Служебное"),
            {
                "fields": (
                    "created_by",
                    "merged_into",
                    "deleted_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )


@admin.register(Event)
class EventAdmin(MergeActionsMixin, ModerationActionsMixin, BaseHistoryAdmin):
    list_display = (
        "name",
        "year",
        "event_type",
        "sport",
        "city",
        "status",
        "deleted_at",
    )
    list_filter = ("sport", "event_type", "status", "year", "deleted_at")
    search_fields = ("name", "slug", "city", "description")
    autocomplete_fields = ("created_by", "merged_into")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = (
        "moderated_by",
        "moderated_at",
        "created_at",
        "updated_at",
        "deleted_at",
    )
    inlines = (EventTeamInline,)
    actions = (*MERGE_ACTIONS, *MODERATION_ACTIONS)
    fieldsets = (
        (None, {"fields": ("sport", "name", "slug", "event_type")}),
        (
            _("Даты и место"),
            {"fields": ("date_from", "date_to", "year", "city", "country")},
        ),
        (_("Содержание"), {"fields": ("description", "attributes")}),
        (
            _("Публикация"),
            {"fields": ("status", "moderated_by", "moderated_at", "moderation_reason")},
        ),
        (
            _("Служебное"),
            {
                "fields": (
                    "created_by",
                    "merged_into",
                    "deleted_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )


@admin.register(TeamMembership)
class TeamMembershipAdmin(BaseHistoryAdmin):
    list_display = (
        "person",
        "team",
        "role",
        "year_from",
        "year_to",
        "number",
        "position",
    )
    list_filter = ("role", "year_from", "team__sport")
    search_fields = (
        "person__last_name",
        "person__first_name",
        "team__name",
        "position",
        "number",
    )
    autocomplete_fields = ("team", "person", "created_by")


@admin.register(PersonName)
class PersonNameAdmin(BaseHistoryAdmin):
    list_display = ("value", "person", "kind", "note")
    list_filter = ("kind",)
    search_fields = ("value", "person__last_name", "person__first_name")
    autocomplete_fields = ("person",)


@admin.register(EventTeam)
class EventTeamAdmin(BaseHistoryAdmin):
    list_display = ("event", "team", "result_note")
    list_filter = ("event__sport", "event__year")
    search_fields = ("event__name", "team__name", "result_note")
    autocomplete_fields = ("event", "team", "created_by")
