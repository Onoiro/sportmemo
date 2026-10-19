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
| `apps/comments` | `Comment` (generic FK, replies, statuses), `CommentSettings` (premoderation), target allow-list (`targets.py`), form, add view, visible query (`services.py`) |
| `apps/moderation` | `Report`, `ModerationAction` (moderator decision log), report form and page |

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
Open Graph), 2.11 (sitemap, robots and Schema.org), 2.12 (search: infrastructure),
2.13 (search: interface), 2.14 (comments and reports in the UI) and 2.15 (catalog filters)
are complete. Stage 2 (public archive) is closed.

## Deliberately deferred

Not doing until needed: API (DRF/django-ninja), SPA, k8s, microservices, multi-sport UI, a "sport
constructor". Photo processing is stage 3. Legal pages and SMTP are stage 4.

## Current state

- Version: 0.16.0; development branch `master`; 940 tests passing on Python 3.12 and 3.13
  (`make test`); linter clean (`make lint`).
- Stage 1 (foundation and data model) is complete. Stage 2 (public archive) is complete:
  sub-stages 2.1–2.15 are done.

### Public UI

Components and pages. The rules for visibility, privacy and their reasoning live in
"Code rules".

- Skeleton: `templates/base.html` and the partials (`navbar`, `footer`, `breadcrumbs`,
  `messages`), error pages `templates/404.html` and `templates/500.html`. Theme —
  `static/css/sportmemo.css` and `static/js/theme.js`: warm sepia by default and a dark variant,
  choice kept in `localStorage`; an inline script in `<head>` applies the theme before paint,
  so there is no flash. Frontend libraries are vendored in `static/vendor/` (Bootstrap 5,
  Bootstrap Icons, htmx, Alpine.js). No CDN: versions, licences and checksums are in
  `static/vendor/README.md`.
- Progressive enhancement: `static/js/gallery.js` and `static/js/comments.js` add `aria-busy`
  and live-region messages. Without JavaScript the "show more" button stays a plain link
  `?page=N`, and the comment form is sent as a plain POST with a redirect back.
- The photo grid is one component (`templates/photos/partials/grid.html` + `grid_items.html`),
  shared by the home page, the year page and the htmx fragment `photos:grid`. "Show more"
  appends the next batch without a reload: the fragment returns only the cards and the button
  container, and on the last page an empty container with `hx-swap-oob`, so the button
  disappears.
- Pages: `/teams/` (24 per page) and `/events/` (20) are lists; `/years/` and `/years/<year>/`
  (24 photos) are by year, plus a "Events of the year" block; `/photos/<pk>/` is a photo;
  `/teams/<slug>/` and `/events/<slug>/` are "visiting cards" (facts header, description,
  attributes, gallery, links); `/people/<slug>/` is a person (main photo, public name, life
  years, biography, attributes, "Teams and roles", "Photos", "Also known as"). There is
  deliberately no `/people/` list: you can only reach a person page from a photo or from a team
  roster.
- Attributes (`Team.attributes`, `Event.attributes`) are rendered as "label, value" pairs via
  `attribute_items()` (`apps/catalog/views.py`): the service key `demo` is hidden, known keys
  are translated, unknown keys are shown as is, lists are joined with commas.
- `Event.date_label` is the event date caption built from months and years ("July 1980",
  "June — July 1988", "July 1988 — August 1989", "1980", "date not specified"): the month name
  comes from the active locale via `date_format`, there are no separate month strings.
- `/search/` (`search:index`, the `apps/search` app): infrastructure is sub-stage 2.12,
  the interface is 2.13.
- Public queries: `SoftDeleteQuerySet.published()` (filters by `status=Status.PUBLISHED`),
  `.visible()` (additionally `rights_confirmed=True` — for photos). Shared entry points live in
  `apps/core/shortcuts.py`: `visible_photos()`, `archive_stats()`, `paginate()`,
  `photos_count()` and `get_visible_or_404(model, *, request=None, **lookup)`. The helper takes
  the model as a parameter and does not import app models — otherwise a cycle
  `core` ↔ `catalog`/`photos` is easy to create. `get_visible_or_404`: the object comes from
  `public`; an unpublished object is returned only to the author (`created_by` / `uploaded_by`)
  and a moderator, everyone else gets 404; a deleted or merged object gives 404 to everyone;
  the author and moderator see the "not published" notice
  (`templates/partials/moderation_notice.html`).
- Batch size (24) and card ordering are shared by the year page and the htmx fragment
  (`PHOTO_PAGE_SIZE`, `PHOTO_ORDERING` in `apps/core/shortcuts.py`): if they diverge, "show more"
  will start skipping or repeating photos. Pages where the filter is not in `request.GET`
  (the year page) pass it to the gallery explicitly.
- Catalog filters live in `apps/catalog/filters.py`: option lookups, parameter parsing and the
  `canonical` address are built there, and the template gets a ready panel
  (`FilterPanel` → `SelectField`) and never compares anything with `request.GET`. Option
  lookups use the whole public queryset, not the filtered one: otherwise a selected city
  would disappear from the list after a decade is chosen. An invalid parameter value counts
  as "filter not applied" — a typo in a link must not look like "nothing found".
  `canonical` is built from normalized parameters (`page_meta(..., canonical_query=)`):
  junk and the page number never get into it.
- The year list on `/years/` is computed from visible photos — the same source as the
  "years in the archive" counter on the home page.
- Infrastructure: `Dockerfile` + `docker-compose.yml` (db, redis, minio, web, worker), CI
  (`.github/workflows/ci.yml`: ruff, migration check, pytest on 3.12 and 3.13 against
  PostgreSQL 17), and the `.agents/skills/semver-checker/` skill.
- The `django.contrib.sites` domain is set by the data migration
  `apps/core/migrations/0003_site_domain.py` (`sportmemo.ru`): otherwise absolute links in
  emails and the sitemap would point to `example.com`.

### Accounts, teams, comments

**Accounts (2.8a).** Login, sign-up, email confirmation and password work go through
`django-allauth` inside the shared site skeleton. The stock templates of the package are
**not copied**: the layout (`templates/allauth/layouts/base.html`) and the elements
(`templates/allauth/elements/*`) are overridden, so both our templates and the package pages
we do not override get the styling. The wrapper for account pages is the `main_inner` block in
`base.html`, not `content`: allauth child templates override `content` and would wipe the
wrapper. Bootstrap classes are added to fields by `BootstrapFormMixin`
(`apps/accounts/forms.py`); the forms are wired through `ACCOUNT_FORMS`, and the mixin calls
`super().__init__()` first because allauth adds and removes fields in its own `__init__`.
`non_field_errors` are rendered as the first line of the form (that is how "Wrong email or
password" arrives). Email texts live in `templates/account/email/` (skeleton, email
confirmation, password reset); in dev emails are printed by the console backend, and the
subject gets the `[SportMemo]` prefix from the site name. The msgid of our templates are
**Russian**: our catalogs and the package catalogs share no strings, and English msgid from
allauth templates must not overwrite the package translations. Email confirmation is mandatory
(`ACCOUNT_EMAIL_VERIFICATION = "mandatory"`), and after confirming the user is logged in right
away (`ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION`). The sign-up form has three mandatory consents
with dates and a version — that is the legal protection of the project, so the checkboxes are
mandatory and have no defaults. The "this is me" claim (`User.person` through moderation) is
deferred: without moderation anyone could "claim" someone else's page.

**Profile (2.8b).** `/accounts/profile/` is our page (`apps/accounts/views.py`,
`accounts:profile`); it extends `base.html` and gets breadcrumbs. Extending the allauth layout
is not an option — it gives the narrow `.auth-shell` column meant for a single form. The route
is wired **before** `allauth.urls` (the addresses do not overlap, but the order makes the
priority of our pages explicit). Only `display_name` ("what to call you") can be changed; email
and password are edited on their allauth pages, with links next to them — two places for one
value would diverge. Saving is POST-redirect-GET with `messages.success`: without the redirect,
refreshing the page would repeat the submission. The role is shown only to a moderator and an
administrator (`user.is_moderator`). The "My profile in the archive" block shows a link only
when the `User.person` link exists; there is deliberately no "This is me" button — the claim is
not implemented. The user menu in the navbar is built only from existing routes (otherwise
`NoReverseMatch` would break every page), and the name is truncated by CSS
(`.account-menu__name`). The `account_email` and `account_reauthenticate` pages are **not
overridden**: the 2.8a skeleton styles them, and the scripts arrive via `extra_body` →
`extra_js`; re-check them after a package upgrade — without those scripts the address
management buttons do not work.

**Team create and edit (2.9).** A team is created as a **draft** (`status=draft`) with
`created_by`, and a moderator publishes it; before publication only the author and a moderator
see the team, everyone else gets 404. The author and a moderator can edit it, everyone else gets
**404, not 403**: we do not reveal that someone else's draft exists; the check lives in
`can_edit_team()` and is applied the same way to GET and POST, and the object is taken from
`Team.public` (not even the author can edit a deleted team). Editing a published team does
**not reset the status** — the author's changes should not wait for a moderator, but everything
is written to history. **The sport field is not shown in the form** — the product is
single-sport (invariant #1), and the value comes from `default_sport()`; when there are no
active sports the form is unavailable with a message, not a 500 error. **Duplicate names are
not blocked** ("Sokoly" exist in different cities, and a human decides): `TeamForm.find_duplicates()`
looks for teams with the same name (`iexact`) in the same sport **including other people's
drafts** — otherwise three identical teams would appear while moderation is pending; when a
match is found a "I checked, this is a different team" checkbox is required, and someone else's
draft is shown by name and status only, without a link. **The team slug is unique within a
sport** (`_unique_slug()` with a numeric suffix), and the lookup area is `Team.objects`, not
`public`: `UniqueConstraint` knows nothing about `deleted_at`. Editing the name does not change
the existing slug — URLs must not break. **A club with several teams** is created as separate
records ("Russtar", "Russtar-2", "Russtar (junior team, born 1992)"), and the hint about it is
the help text of the name field; a separate "club" entity with a `parent` field is deferred
(idea I-9). The moderator email (`apps/catalog/emails.py`) goes to recipients from the database
(role moderator or admin, active), not from a setting; an SMTP error is logged and does **not**
cancel the team creation, and when there are no moderators no email is sent. The routes
`teams/new/` and `teams/mine/` are declared **before** `teams/<uslug:slug>/`: the `uslug`
converter accepts any letters and hyphens and would otherwise capture both addresses.
`BootstrapFormMixin` lives in `apps/core/forms.py`, not in `accounts`: team forms need styled
fields too, and importing from `accounts` into `catalog` would be an extra link between apps.

**Comments and reports (2.14).** A comment is **published immediately**: premoderation is off
by default — the archive grows from other people's albums, and a correction like "this is not
Ivanov but Petrov" must appear at once, not after a moderator. It is enabled by a switch, not by
a code change: `CommentSettings` is a singleton setting in the admin (one row, created by a data
migration; adding and deleting rows is forbidden, and the changelist leads to the edit form). It
is read through the cache and resets it on save — the switch takes effect at once. **Comment
visibility lives in `apps/comments/services.py`** (`comments_context()`, `visible_comments_q()`),
not in templates: the three pages live in different apps, and a separate query in each would
give different rules. Who sees what: anonymous and logged-in users see published comments; the
author additionally sees their own comment in `draft` with a "pending review" badge; hidden,
rejected and deleted comments are seen by nobody, including the author (that is a moderator's
decision, not a draft), and a reply to a hidden root comment is not shown. **Replies are flat**:
a reply to a reply is raised by the form to the root comment — a tree needs recursion in the
template and reads worse in markup. **The target allow-list is `apps/comments/targets.py`**
(photo, team, event): a generic FK does not restrict the target itself, and without the list the
form could create a comment on a user or on a report; the object is fetched with
`get_visible_or_404`. **Duplicate protection** — the same author, the same target and the same
text within 10 minutes; IP throttling is deferred (P2-17), and so are editing and deleting your
own comment (P2-16). The form works without JavaScript; with htmx the block is re-rendered
entirely (`hx-swap="outerHTML"`), and `next` is checked against an open redirect. **A report is
a separate page** `/reports/add/`, reachable anonymously: a modal window needs JavaScript, and a
report link should be easy to forward. **There are no moderation buttons on public pages**: the
admin is the single place for decisions, and each one is written to `ModerationAction` and to
history. **Moderator emails are sent about reports only** (new comments would be a flood of
emails with premoderation off); the shared part lives in `apps/core/emails.py`
(`moderator_emails()`, `notify_moderators()`), and the wrappers with the text are in `catalog`
and `moderation`; `notify_moderators()` never raises. **Report form protection** — a `website`
honeypot field: when filled we show thanks and record nothing (answering a bot with a refusal
would tell it that it was caught). A repeated report by the same logged-in user on the same
object with the same reason within a day is not created; anonymous users are not checked.
`/reports/add/` is closed to indexing (`noindex`) and added to `robots.txt`.

**Admin.** Moderation (publish / hide / reject / soft-delete / restore) is a list action; every
decision is written to `ModerationAction`. Merging duplicates is the "Merge duplicates into one
object" action, which opens a separate target selection page (`admin/core/merge_form.html`)
because the target must not be one of the sources. `ModerationAction` is read-only: the log is
filled by actions, not by hand.

### Code rules

- Models with `deleted_at` must have both `objects` (everything, including deleted rows) and `public`
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
- List filters live in `apps/catalog/filters.py`, not in templates and not in views: option
  lookups, parameter parsing and the `canonical` address live in one place, and the template
  gets a ready panel. An invalid parameter value counts as "filter not applied".
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

