# SportMemo — Project Rules

> **This file is kept in sync with [`KODA.md`](KODA.md).** Both contain the same set of rules:
> `KODA.md` in Russian (for Koda CLI sessions), `AGENTS.md` in English (for external agents and
> contributors). When changing the rules, update **both** files.

## What this project is

SportMemo is a public archive of sports photographs. It starts with baseball in Russia: youth,
junior and veteran teams, and the personal paper archives of players, coaches and fans. Users
digitize their archives and publish them to a shared archive with attribution: who is in the photo,
team, event, year, source.

- Domain: sportmemo.ru
- Repository: github.com/Onoiro/sportmemo
- Owner: Andrey Bogatyrev (former professional baseball player, personal paper archive)

## Architectural rules (invariants)

Violating any of these is a defect, not a stylistic difference.

1. **Multi-sport schema, single-sport product.** The schema has `Sport`; the UI has no sport
   selector. It will appear when an advocate for another sport shows up.
2. **No baseball specifics in table structure.** Generic entities (Team/Event/Person); sport
   specifics go into `attributes` (JSONB). A pitcher's fielding position is free text in
   `TeamMembership.position`, not a dedicated baseball column.
3. **`Person` ≠ `User`.** A person in a photo is a public entity that needs no account. A user is a
   private account, optionally linked to a `Person` ("this is me").
4. **A photo original is never overwritten.** The processed version is a derivative stored in a
   separate field.
5. **Duplicates are merged, never deleted.** `merged_into` + `merge_into(target)`: move relations,
   mark the source. The operation is idempotent.
6. **Everything significant is logged.** `django-simple-history` on all meaningful models plus a
   moderator decision log (`ModerationAction`).
7. **Slug uniqueness is scoped to the sport** for `Team`/`Event`. For `Person` it is global, with a
   numeric suffix on collision.
8. **Minors' last names are not published.** Controlled by `Person.name_visibility`; public context
   uses `Person.public_name`, never `display_name`.
9. **Soft delete, not physical deletion.** An object with `deleted_at` disappears from public
   queries but keeps its relations and history.
10. **A team is created by a user in `draft` status** and published only by a moderator.

## Stack

Python 3.12, Django 5.2 LTS, Django templates + Bootstrap 5 + htmx + Alpine.js, PostgreSQL 17,
MinIO via `django-storages`, `django-imagekit` (thumbnails), `django-simple-history` (audit),
`django-allauth` (email + password with mandatory email confirmation), Celery + Redis, `uv`
(packages), `ruff` (linter), `pytest` + `pytest-django` + `factory_boy` (tests), Docker Compose +
Nginx (infrastructure).

The public UI is server-rendered (Django templates) for SEO. No SPA and no API until there is more
than one client.

## Structure

Apps live in the `apps/` package (`INSTALLED_APPS` lists `apps.catalog` etc.) so the repository root
stays tidy.

| App | Contents |
|---|---|
| `apps/core` | abstract models (`TimeStampedModel`, `SlugModel`, `StatusModel`, `SoftDeleteModel`, `MergeableModel`), `Sport`, admin mixins (`ModerationActionsMixin`, `MergeActionsMixin`, `BaseHistoryAdmin`), `/healthz/`, the `seed_demo` command, public query helpers (`shortcuts.py`), public views (home page, years, `robots.txt`), the `uslug` URL converter (`converters.py`), the pagination tag, `core/urls.py`, shared form helpers (`forms.py`: `BootstrapFormMixin`), SEO meta tags (`seo.py`), sitemap (`sitemaps.py`), Schema.org markup (`schema.py`) |
| `apps/accounts` | custom `User` (email as `USERNAME_FIELD`), consents, roles, link to `Person` |
| `apps/catalog` | `Team`, `TeamMembership` (with `years_label`), `Person`, `PersonName`, `Event`, `EventTeam`, `Event.date_label`, `roster.py` (roster filter by year), `forms.py` (`TeamForm`), `emails.py` (moderator notification), list views and team, event and person detail pages, team create and edit views |
| `apps/photos` | `Source`, `Photo`, `PhotoPerson`, public views (`photo_detail`, the `grid` fragment), `photos/urls.py` |
| `apps/search` | search: the `pg_trgm` extension, search text and query building (`indexing.py`), results and suggestions (`services.py`), the results page (`views.py`, `urls.py`), signals, the `search_reindex` command; it has no models of its own — the index fields live in `catalog` and `photos` |
| `apps/comments` | `Comment` (generic FK, replies) |
| `apps/moderation` | `Report`, `ModerationAction` |

## Development conventions

- **Packages are managed with `uv` only.** `uv sync`, `uv run`, `uv lock` after dependency changes.
  Do not add poetry/pip requirements.
- **The project version lives in `pyproject.toml`** (`[project] version`). After changing it, run
  `uv lock` (the project version is recorded in `uv.lock`).
- **Semver.** Before committing, apply the `semver-checker` skill
  (`.agents/skills/semver-checker/SKILL.md`): determine the bump type from `git diff`, update the
  version, add a `Bump version: X → Y (type)` line to the commit message.
  Rules: minor — new features and schema migrations; patch — fixes, UX, translations;
  none — docs, tests, CI.
- **Linter is `ruff`** (`make lint`). Configuration lives in `pyproject.toml`; migrations are
  excluded. `make lint` runs both the linter and the format check (`ruff format --check`).
- **Line length:** 88 characters for code, 79 for comments and docstrings. This follows Django's
  style (88, same as black) while keeping PEP 8 limits for documentation. Enforced by the `E501`
  and `W505` rules; do not bypass them. Run `make format` to reformat.
- **Run `make lint` and `make test` before every commit.** This is mandatory for any change,
  including Makefile and config files.
- **Tests use `pytest`** (`make test`), live in `tests/`, factories in `tests/factories.py`.
- **Migrations:** `make migrations` / `make migrate`. Tests run against PostgreSQL (CI) or SQLite,
  with local file storage instead of MinIO.
- **Settings come from the environment only** (`django-environ`); defaults must work for dev.
  Never commit secrets.
- **Comments** are rare and explain "why", not "what". Code and identifiers are in English; UI
  strings go through i18n.
- **Localization:** UI strings use `gettext`, catalogs live in `locale/` (ru, en).
  `make i18n` extracts and compiles them.

## Language

The project owner is a Russian speaker. Documentation, discussion and agent replies are **in
Russian**. Code identifiers, file names and technical terms follow common English usage.

**Commit messages are written in simple English.** Rule: write so that a person who is not a native
English speaker can understand it.

- Short sentences. One idea per sentence.
- Simple, common words. No idioms, no slang, no word play.
- The subject line is at most 72 characters and uses the imperative: `add`, `fix`, `remove`.
- The commit body explains **why**, not **what**: the reason matters more than the list of changes.
- The version line follows the semver-checker skill: `Bump version: 0.1.0 → 0.2.0 (minor)`.

Good subject: `feat: add photo model and admin page`.
Bad subject: `feat: flesh out the photo bits` — it is not clear what was done.

## Workflow

1. A task comes from `.koda/backlog.md` → branch `feature/<short-name>` → commits →
   `make lint && make test` → squash-merge into `master` → delete the branch.
   Pull requests and GitHub issues are not used: one person runs the project, and the backlog
   and the plans live in the repository, versioned together with the code. Issues make sense
   once a second contributor appears.
2. Stage plans live in `.koda/plans/`. Plan steps are marked `[ ]` → `[x]` as they are completed.
3. Significant work starts with a plan approved by the owner.

Small changes — documentation, texts, comments, config files — may be committed straight
to `master`, without a branch. A branch is needed when code or the data schema changes.

After a squash-merge, `git branch -d` will refuse to delete the branch: git does not treat
such a branch as merged. Check `git diff --stat <branch> master` first (empty means the trees
match), then delete with `git branch -D`.

## Roadmap

| Stage | Content | Status |
|---|---|---|
| 1 | Foundation: data schema, admin, tests, CI | complete |
| 2 | Public archive: layout, pages, galleries, search, comments, sign-up UI | in progress |
| 3 | Photo processing: frame correction (OpenCV), Celery pipeline, perceptual hash, duplicate merge | planned |
| 4 | Mobile PWA, legal layer, deployment, backups, monitoring | planned |

Details in `.koda/plans/foundation-and-data-model.md` (stage 1),
`.koda/plans/public-archive.md` (stage 2 master plan) and `.koda/backlog.md`.

**Stage 2 is split into 16 sub-stages**, one session each (sub-stage 2.8 is split in two parts:
2.8a — sign-up, login and password; 2.8b — user profile and menu). A sub-stage plan is created
as a separate file when work on it starts (`plans/public-archive-2.N-<name>.md`). The order and
the list of sub-stages are in the master plan. Sub-stages 2.1 (demo data), 2.2 (public UI
skeleton), 2.3 (public queries and lists), 2.4 (gallery component), 2.5 (team and event pages),
2.6 (person and photo pages), 2.7 (team roster filter by year), 2.8a (sign-up, login and
password), 2.8b (user profile and menu), 2.9 (user-created team), 2.10 (meta tags and
Open Graph), 2.11 (sitemap, robots and Schema.org), 2.12 (search: infrastructure) and
2.13 (search: interface) are complete; the next one is 2.14 (comments and reports in the UI).

## Deliberately deferred

Not doing until needed: API (DRF/django-ninja), SPA, k8s, microservices, multi-sport UI, a "sport
constructor". Photo processing is stage 3. Legal pages and SMTP are stage 4.

## Current state

- Version: 0.14.0
- Development branch: `master`
- Stage: 1 (repository foundation and data model) — **complete**;
  stage 2 (public archive) — **in progress**, sub-stages 2.1 (demo data), 2.2
  (public UI skeleton), 2.3 (public queries and lists), 2.4 (gallery component), 2.5
  (team and event pages), 2.6 (person and photo pages), 2.7 (team roster filter by year),
  2.8a (sign-up, login and password), 2.8b (user profile and menu), 2.9 (user-created team),
  2.10 (meta tags and Open Graph), 2.11 (sitemap, robots and Schema.org), 2.12
  (search: infrastructure) and 2.13 (search: interface) complete
- Tests: 799, passing on Python 3.12 and 3.13 (`make test`); linter clean (`make lint`)

### What exists now

| App | Contents |
|---|---|
| `apps/core` | abstract models (`TimeStampedModel`, `SlugModel`, `StatusModel`, `SoftDeleteModel`, `MergeableModel`), `Sport`, admin mixins (`ModerationActionsMixin`, `MergeActionsMixin`, `BaseHistoryAdmin`), `/healthz/`, the `seed_demo` command, public queries (`shortcuts.py`), public views (home page, years, `robots.txt`), `core/urls.py`, shared form helpers (`forms.py`: `BootstrapFormMixin`), SEO meta tags (`seo.py`), sitemap (`sitemaps.py`), Schema.org markup (`schema.py`) |
| `apps/accounts` | email-based `User`, `UserManager`, consents (terms/privacy/PD law + version), roles, `User.person` link, allauth forms with Bootstrap classes, allauth adapter |
| `apps/catalog` | `Team`, `TeamMembership` (with `years_label`), `Person`, `PersonName`, `Event`, `EventTeam`, `Event.date_label`, `roster.py` (roster filter by year), `forms.py` (`TeamForm`), `emails.py` (moderator notification), list views and team, event and person detail pages, team create and edit views |
| `apps/photos` | `Source`, `Photo` (rights, `processing_status`, `phash`, `duplicate_of`), `PhotoPerson`, thumbnails via `ImageSpecField`, the photo page and the gallery fragment |
| `apps/search` | `pg_trgm` extension, `search_vector` fields and GIN indexes, search text and query building (`indexing.py`), results and suggestions (`services.py`), the results page (`views.py`), signals, the `search_reindex` command |
| `apps/comments` | `Comment` (generic FK, replies, statuses) |
| `apps/moderation` | `Report`, `ModerationAction` (moderator decision log) |

Infrastructure: `Dockerfile` + `docker-compose.yml` (db, redis, minio, web, worker), CI
(`.github/workflows/ci.yml`: ruff, migration check, pytest on 3.12 and 3.13 against PostgreSQL 17),
and the `.agents/skills/semver-checker/` skill.

### Public UI

- `templates/base.html` plus `templates/partials/` (navbar, footer, breadcrumbs, messages,
  pagination) and the gallery partials in `templates/photos/partials/`; error pages in
  `templates/404.html` and `templates/500.html`.
- Theme: `static/css/sportmemo.css` and `static/js/theme.js` — warm sepia by default, dark
  variant, choice kept in `localStorage`. An inline script in `<head>` applies the theme before
  paint, so there is no flash.
- Gallery: `static/js/gallery.js` adds `aria-busy` and live-region messages to the htmx
  "show more" button. It loads after htmx; without JavaScript the button stays a plain link.
- Frontend libraries are vendored in `static/vendor/` (Bootstrap 5, Bootstrap Icons, htmx,
  Alpine.js). No CDN: versions and checksums are in `static/vendor/README.md`.
- Public queries: `SoftDeleteQuerySet.published()` filters by `status=Status.PUBLISHED`;
  `.visible()` additionally requires `rights_confirmed=True` (used for photos). Public pages
  chain `Model.public.published().visible()`.
- Shared entry points for public queries live in `apps/core/shortcuts.py`: `visible_photos()`,
  `archive_stats()`, `paginate(request, queryset, per_page)` and
  `get_visible_or_404(model, *, request=None, **lookup)`. The helper takes the model as an
  argument and imports no app models — otherwise `core` ↔ `catalog`/`photos` would cycle.
- `get_visible_or_404`: the object comes from `public`; an unpublished object is returned only
  to its author (`created_by` / `uploaded_by`) and to a moderator, everyone else gets 404;
  deleted and merged objects are 404 for everyone. Author and moderator see the "not published"
  badge (`templates/partials/moderation_notice.html`).
- Lists `/teams/` (24 per page), `/events/` (20), `/years/` and `/years/<year>/` (24 photos)
  return visible objects only. The year list is built from visible photos — the same source
  as the "years in the archive" counter on the home page.
- The photo grid is one component (`templates/photos/partials/grid.html` plus `grid_items.html`)
  used by the home page, the year page and the htmx fragment. "Show more" appends the next page
  to the grid without a reload: the `photos:grid` fragment returns only the cards and the button
  container, and on the last page an empty container with `hx-swap-oob` makes the button
  disappear. Without JavaScript the same button is a plain `?page=N` link.
- The page size (24) and the card order are shared between the year page and the fragment
  (`PHOTO_PAGE_SIZE`, `PHOTO_ORDERING` in `apps/core/shortcuts.py`): if they drift apart, the
  "show more" step starts skipping or repeating photos. Pages where the filter is not in
  `request.GET` (the year page) pass it to the gallery explicitly — the hook for catalog filters
  in 2.15.
- `/photos/<pk>/` opens anonymously for a published photo with confirmed rights. A draft or a
  photo without confirmed rights is shown only to its author (`uploaded_by`) and to a moderator;
  `get_visible_or_404` does the filtering. Links to the team and the event appear only when they
  are published. In the "People on the photo" block a person's name links to their page when the
  person is published; a draft person's name stays plain text. The view counter grows on every
  open through an atomic `update(views_count=F("views_count") + 1)` instead of `save()`, so no
  `django-simple-history` record is created.
- `/teams/<slug>/` and `/events/<slug>/` are the "visiting card" pages: a header with facts,
  a description, attributes, a gallery and relations. A team shows its events and a roster with
  a filter by year (`?year=1994`); an event shows its participating teams with results.
- The team roster is filtered by year instead of living on a separate page: the archive is
  filled from other people's albums, so a "roster of the year" would read as a complete list.
  The wording promises no completeness — "People in the team in 1994" plus the note "The archive
  does not list everyone who played". A separate page with year grouping is deferred (idea I-8).
- The "all years" mode shows up to 12 people (`TEAM_ROSTER_PREVIEW`) and the total number of
  known people; a selected year is shown in full, otherwise the filter would be pointless.
  The year list holds only years with at least one known person.
- An open period (`year_from` set, `year_to` empty) ends with the last year known for the team
  (the maximum over its memberships and events); without that bound a person would smear across
  every year up to today. A lone `year_to` gives a single year.
- Period logic lives in `apps/catalog/roster.py` and issues no database queries: unit tests cover
  it. The years label is the `TeamMembership.years_label` property, shared with the person page.
- `/people/<slug>/` is the person page: a main photo, the public name, life years, a biography,
  attributes, "Teams and roles", "Photos" and "Also known as". There is deliberately no
  `/people/` list: the page is reachable from a photo or from a team roster only.
- The heading, `<title>`, description and breadcrumbs of the person page are built from
  `Person.public_name` only: `display_name` holds the full last name and never reaches the
  markup. With `hidden` visibility the page shows the neutral label "Name not stated" and still
  opens — otherwise photos and teams would lose the link to the person.
- Name variants (`PersonName`) are shown only when `name_visibility = full` (the
  `Person.is_full_name_public` property) and are hidden in the view, not in the template: a
  nickname or a birth name would reveal the hidden last name. When hidden,
  `context["name_variants"]` is an empty list.
- The main photo of the person page is `Person.avatar` when it is visible (`Photo.is_public`);
  otherwise the newest visible photo of that person (`PHOTO_ORDERING`). When there is nothing,
  a placeholder icon is shown. A draft avatar or an avatar without confirmed rights is ignored.
- The biography is rendered as is: the author and the moderator are responsible for last names
  in the text. The text is not checked — a known limitation of the sub-stage.
- A link to a team or an event is created only when the object is published (`is_published`);
  otherwise the name is plain text. The rule applies to lists, the photo page, participating
  teams and the "Events of the year" block.
- Attributes (`Team.attributes`, `Event.attributes`) are rendered as "label, value" pairs by
  `attribute_items()` (`apps/catalog/views.py`): the service key `demo` is hidden, known keys are
  translated, unknown keys are shown as is, lists are joined with commas.
- `Event.date_label` renders event dates from months and years only: "July 1980",
  "June — July 1988", "July 1988 — August 1989", "1980" or "date not stated". The month name
  comes from the active locale through `date_format`, so no separate month strings are needed.
- Slugs are stored with `allow_unicode=True`, so routes use a custom `<uslug:slug>` converter
  (`apps/core/converters.py`, registered in `CoreConfig.ready()`): the stock `<slug:...>` accepts
  ASCII only and would break `reverse()` on Cyrillic slugs.
- The "Events of the year" block on `/years/<year>/` shows published events of that year without
  pagination; the photo counter in the card uses the shared `photos_count()` annotation from
  `apps/core/shortcuts.py`.
- `/search/` is opened by the results page (`search:index`, the `apps/search` app): the search
  infrastructure (the `pg_trgm` extension, `search_vector` fields, GIN indexes, signals, the
  `search_reindex` command) was built in 2.12, the interface in 2.13.
- `apps/core/migrations/0003_site_domain.py` sets the `django.contrib.sites` domain to
  `sportmemo.ru` — absolute links in emails and sitemaps must not point at `example.com`.

### Account pages

- Sign-up, login, email confirmation and password flows use `django-allauth` and live inside
  the site shell: navbar, footer, theme. Stock package templates are **not** copied — the
  layout (`templates/allauth/layouts/base.html`) and the elements
  (`templates/allauth/elements/*`) are overridden, so both our templates and the pages we do
  not override (email address list, re-authentication) get the same styling.
- The wrapper for account pages is the `main_inner` block in `base.html`, not `content`:
  allauth child templates override `content` and would wipe the wrapper entirely.
- Bootstrap classes on fields are set by the `BootstrapFormMixin` mixin
  (`apps/accounts/forms.py`): allauth templates render widgets as they are, and a template
  cannot add a widget class. Forms are wired through `ACCOUNT_FORMS`; the mixin calls
  `super().__init__()` first because allauth adds and removes fields in its own `__init__`.
- `non_field_errors` are printed as the first row of the form in
  `templates/allauth/elements/fields.html`: the "wrong email or password" error arrives that
  way, not as a field error.
- Email texts live in `templates/account/email/` (wrapper, email confirmation, password
  reset); in dev the console email backend prints them. The subject gets the `[SportMemo]`
  prefix from the site name automatically.
- msgid strings in our templates are **Russian**: our catalogs and the package catalogs share
  no strings, and English msgid from allauth templates must not shadow package translations.
- Links in emails are built from the `django.contrib.sites` domain; email confirmation is
  mandatory (`ACCOUNT_EMAIL_VERIFICATION = "mandatory"`) — without it login sends the user to
  "check your email", and after confirmation the user is logged in at once
  (`ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION`).
- The sign-up form has three mandatory consents with dates and a version — this is legal
  protection for the project, so the checkboxes are required and have no default values.
- The "this is me" claim (`User.person` through moderation) is deferred: without moderation
  anyone could claim someone else's page. The profile (2.8b) only links an already
  connected person.

### User profile

- `/accounts/profile/` is our own page (`apps/accounts/views.py`, `accounts:profile`), not an
  allauth template: it extends `base.html` and gets breadcrumbs. It must not extend the
  allauth layout — that gives a narrow `.auth-shell` column meant for a single form.
- The route is wired **before** `allauth.urls`: the paths do not overlap, but the order makes
  the priority of our pages explicit.
- Only `display_name` can be edited in the profile. Email and password are changed on their
  own allauth pages, and the profile links to them: two places for one value would drift
  apart.
- Saving is POST-redirect-GET with `messages.success`. Without the redirect, reloading the
  page would repeat the form submission.
- The role row is shown to moderators and administrators only (`user.is_moderator`): for a
  regular user the word "user" says nothing.
- The "My archive profile" block shows a link only when `User.person` is already connected.
  There is deliberately no "This is me" button: the claim flow is not implemented, and we
  must not promise what does not exist.
- The user menu in the navbar is built from existing routes only — otherwise
  `NoReverseMatch` would break every page. The name in the button is truncated with CSS
  (`.account-menu__name`), not in the view: a long value would stretch the header.
- The `account_email` and `account_reauthenticate` pages are **not** overridden: the 2.8a
  shell styles them, and the allauth scripts arrive through the `extra_body` → `extra_js`
  block. Check them after a package update: without those scripts the address management
  buttons do not work.

### Team create and edit

- A team is created by a user as a **draft** (`status=draft`) with `created_by`; a moderator
  publishes it. Until then only the author and moderators see it, everyone else gets 404 —
  the `get_visible_or_404` rule already covers this.
- A team is edited by its author and by moderators; everyone else gets **404, not 403**: we
  do not reveal that someone else's draft exists. The check lives in `can_edit_team()` and
  applies to both GET and POST; even the author cannot edit a deleted team (the object comes
  from `Team.public`).
- Editing a **published** team does **not** reset its status: author changes must not wait for
  a moderator, but everything is written to history (`django-simple-history`). We will return
  to re-moderation if vandalism appears.
- **The sport is not shown in the form** — the product is single-sport (invariant 1). The value
  comes from `default_sport()` (`apps/core/shortcuts.py`): the first active sport by sort order.
  No active sports — the form is unavailable with a clear message, not a 500 error.
- **Duplicate names are not blocked.** "Соколы" exist in different cities, and the decision
  "duplicate or namesake team" is made by a human. The form
  (`TeamForm.find_duplicates()`) searches for teams with the same name (`iexact`) in the same
  sport, **including other users' drafts** — otherwise three identical teams would appear
  while moderation is pending. If found, the "I checked: this is a different team" checkbox is
  required. Someone else's draft is shown by name and status only, without a link; a published
  team gets a link. Real duplicates are merged by a moderator.
- **The team slug is unique within a sport**, so it is generated through `_unique_slug()` with
  a numeric suffix (`динамо`, `динамо-2`). The lookup scope is `Team.objects`, not `public`:
  the `UniqueConstraint` knows nothing about `deleted_at`, and a slug that looks free to
  `public` would still fail at the database level. Renaming does not change the existing slug —
  URLs must not break.
- **A club with several teams** is created as separate entries: "Русстар", "Русстар-2",
  "Русстар (юноши 1992 г. р.)". A hint about this is the help text of the name field. A
  separate "club" entity with a `parent` field is deferred (idea I-9 in the backlog):
  navigation in the archive goes by years and people, and "all photos of a club" will be
  collected by search.
- The moderator notification (`apps/catalog/emails.py`) goes to recipients from the database
  (role moderator or admin, active), not from a setting: the moderator list changes. An SMTP
  error is logged and **does not cancel** team creation; with no moderators the email is not
  sent and a warning is logged.
- The `teams/new/` and `teams/mine/` routes are declared **before** `teams/<uslug:slug>/`:
  the `uslug` converter accepts any letters and dashes and would otherwise capture both.
- `BootstrapFormMixin` lives in `apps/core/forms.py`, not in `accounts`: team forms need
  styled fields too, and importing from `accounts` into `catalog` would be a needless link
  between apps.

### Admin rules

- Moderation (publish / hide / reject / soft-delete / restore) runs as list actions; every decision
  is recorded in `ModerationAction`.
- Duplicate merge is the "Merge duplicates into one object" action: it opens a separate target
  selection page (`admin/core/merge_form.html`) because the target must not be one of the sources.
- `ModerationAction` is read-only in admin: the log is written by actions, not by hand.

### Code rules

- Any model with `deleted_at` must expose `objects` (everything, including deleted) and `public`
  (deleted excluded). Public queries go through `public` only.
- Fields with `auto_now_add` cannot be populated from JSON fixtures — seed data belongs in a data
  migration.
- New FK fields pointing at `User` in `catalog`/`photos` create no cycle: `accounts` migrates
  first, and reverse links into `catalog` go into a separate migration (`accounts/0002_...`).
- Demo data is created by the `seed_demo` command (`make seed-demo`) and marked with
  `attributes={"demo": true}`; `make seed-demo-flush` removes it. This is a development tool:
  demo data is never moved to production, and the deploy checklist includes "do not move demo
  data".
- Public pages return published, non-deleted objects only: `Model.public.published()` (plus
  `.visible()` for photos). Drafts never appear on any public page.
- A new public page takes its queryset from `apps/core/shortcuts.py` instead of building the
  `public.published()` chain by hand: the filtering rule must live in one place.
- A link to an unpublished object is never created: check `is_published` before the link,
  otherwise render the name as plain text. A draft must not leak through a direct link or
  reach a search engine index.
- People in public context are rendered through `Person.public_name`, and a team roster is
  filtered by `Person.public.published()`: a draft person never appears publicly, even when
  listed in a team. The publicity filter runs before the year filter and before the preview slice.
- The name privacy rule lives in the view, not in the template: name variants (`PersonName`)
  go into the context only when `Person.is_full_name_public` is true. A template change must
  not be able to reveal a hidden last name.
- The roster filter lives in the URL (`?year=1994`) and works as a plain GET form without
  JavaScript. An unknown or non-numeric year is treated as "no filter": a typo in a link must
  not produce an empty page.
- `display_name` never reaches public markup — `public_name` does: `Person.save()` fills
  `display_name` with the full name, last name included. A page that needs the full name on
  purpose must ask for it explicitly.
- The main photo of the person page follows one rule: the avatar when it is visible, otherwise
  the first visible photo. Visibility is checked through `Photo.is_public`, not by comparing
  statuses inside a template.
- The photo counter in team and event cards comes from the shared `photos_count()` annotation
  (`apps/core/shortcuts.py`): a local copy would drift from the counter on another page.
- Slugs are Cyrillic, so routes built on `SlugModel` use the `uslug` converter
  (`apps/core/converters.py`) instead of the stock `<slug:...>`. The `QueryDict` for gallery
  links is built from a dict, not from a string: Cyrillic and `&` inside a slug would break
  the URL.
- The view counter of a photo grows with `QuerySet.update()` on purpose: `save()` would write
  a history record on every page open and flood the audit log.
- SEO meta tags (`title`, `description`, `canonical`, `og:*`) are built in views through
  `page_meta()` (`apps/core/seo.py`), not in templates: the card image depends on page data,
  and the name privacy rule must live in one place. The partial
  `templates/partials/meta.html` only renders the ready `meta` dict. `noindex` is the default
  of the `seo_meta` context processor for pages that set no `meta` of their own: a new page
  stays out of search engines until its author describes it. The view context overrides the
  processor, so the `meta` dict from `page_meta()` must be complete, never partial.
- Schema.org markup is built in `apps/core/schema.py`, not in templates, for the same reason
  as meta tags: the name privacy rule and the image choice must live in one place. The
  `{% jsonld %}` tag (`apps/core/templatetags/jsonld.py`) serialises the dict and escapes `<`,
  `>`, `&`, U+2028 and U+2029: without that a name like `</script>` would break the page.
  The partial `templates/partials/jsonld.html` renders the blocks from the `jsonld_blocks`
  context variable; a page without markup never sets it, so the partial stays empty. A `Person`
  is marked up for `full` and `first_name_only`, and has no block for `hidden`: the last name
  must never reach machine-readable data in any variant.
- The sitemap (`apps/core/sitemaps.py`) lists public objects only and takes its queries from
  `apps/core/shortcuts.py` instead of rebuilding `public.published()`: the address list in the
  sitemap and the list on the page must match. The domain for the sitemap and `robots.txt` comes
  from `django.contrib.sites`, not from `request.get_host()`: nginx sits in front of the site,
  so the address would depend on proxy settings. Sitemap route names stay as Django defines
  them — `views.index` builds section links from those names, and renaming them would produce
  broken links. `i18n = False` is required: URLs have no language prefix, and `i18n = True`
  would duplicate every address once per language in `LANGUAGES`.
- Django serves `/robots.txt` and `/sitemap.xml`, not nginx: the sitemap address and the list
  of closed sections must live next to the code and go into the repository. `robots.txt` is
  a polite request, not protection; drafts are protected by `get_visible_or_404`, which returns
  404 to outsiders.
- UI strings go through `gettext` only. Run `make i18n` after adding strings. `makemessages`
  marks new strings as `fuzzy` when it finds a similar old one, and `msgfmt` skips fuzzy
  entries — check the fuzzy blocks after extraction and clear the flags.
- Template comments use `{% comment %}...{% endcomment %}` only. Django's `{# ... #}` works
  within a **single line**: a closing `#}` on the next line is not recognised, and the comment
  text leaks into the HTML.
- `django-imagekit` caches the fact that a thumbnail exists in the Django cache. In tests each
  test has its own `MEDIA_ROOT`, so `tests/conftest.py` has a `cleared_cache` fixture; without
  it thumbnail tests fail while reading a file from another test.
- The search index is a derived field, and code keeps it up to date: signals
  (`apps/search/signals.py`) on object save, and the `make search-reindex` command for a full
  pass. Write through `queryset.update()`, not `save()`: `save()` would add a row to
  `django-simple-history` and move `updated_at` (which feeds `lastmod` in the sitemap)
  on every reindex.
- One function, `build_search_text()` (`apps/search/indexing.py`), builds the indexed text:
  the name privacy rule must live in one place. With `first_name_only` the last name and name
  variants stay out of the text; with `hidden` the text is empty; `display_name` is never
  indexed. The trigram index keeps the raw fields, so hiding there is done by the query
  filter: public search results must go through `public.published()` and respect
  `name_visibility`.
- Drafts and photos without confirmed rights are indexed as usual: publication is filtered by
  the queryset, not by the index. Reindexing on every status change would mean extra writes.
- User input is turned into a query by `build_search_query()`: words are cleaned with a
  regular expression (`&`, `|`, `!`, `:` are part of the `tsquery` language), and each word
  gets a `:*` prefix. Without the prefix a partial word does not match: the Russian
  dictionary reduces "Громовцы" to "громовц" while the query "гром" becomes "гром".
- Index fields live in the `catalog` and `photos` models, not in `apps/search`: `makemigrations`
  writes `AddField` only into the app that owns the model. The `search` app owns the `pg_trgm`
  extension, text building, signals and the reindex command.
- Search works on PostgreSQL only (`tsvector`, `pg_trgm`); search tests are marked with
  a vendor `skipif`. `CREATE EXTENSION` needs superuser rights — on the production server it is
  a separate line in the deploy checklist.
- The search page lives in `apps/search` (`search:index`), not in `core`: `core` is the bottom
  layer in this project, and a link from it to catalog models would create a reverse dependency.
  The result set is built by `apps/search/services.py` (`search_page()`, `suggestions()`), and
  the view only parses the input: `SearchGroup` knows the card template name, so the group
  partial does not branch by object type, and `Suggestion` carries a ready label, URL and kind —
  the template never learns which model a suggestion came from.
- Search is a public result set: a draft is shown to nobody, not even to its author or
  a moderator. `get_visible_or_404` is not used here: the author would see a draft on a random
  query and would not understand why others cannot.
- Suggestions ("did you mean") compare safe fields only: name and city for a team and an event;
  for a person `first_name` always, while `last_name` and name variants (`PersonName.value`)
  are compared only when `name_visibility=full` (a `Case/When` condition, not a filter:
  a `first_name_only` person must stay in the suggestions by first name), and people with
  a hidden name are excluded at all. The trigram index keeps the raw last name, so the rule
  lives in `services.py` and is covered by tests.
- Suggestions are computed only when the whole result set is empty: trigram queries cost more
  than full-text ones and are useless once something is found. Non-empty input that leaves no
  words (`!!!`) gives "nothing found" without the suggestion block — there is nothing to compare
  by trigrams. Input is cut to `MAX_QUERY_LENGTH` (100) before the `tsquery` is built.
- The results page is closed to indexing (`noindex, follow`): addresses with an arbitrary query
  must not appear in search engines, while the found cards still lead to real archive pages.
  Search cards are reused as they are; for people a new
  `templates/catalog/partials/person_card.html` uses the shared `.entity-card` style and never
  renders `display_name` — only `public_name`.
- A result group shows up to `GROUP_LIMIT` (20) cards plus the total counter; when the group is
  cut, the note "Showing the first N of M" appears under the grid. There is no pagination on the
  results page: it is an overview, not a list.
- Account pages are styled through the layout and elements, not by copying templates: every
  extra copied file is one more place to fix when the package updates. Write an own template
  only where the text must be ours.

