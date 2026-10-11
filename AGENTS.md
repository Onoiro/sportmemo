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
| `apps/core` | abstract models (`TimeStampedModel`, `SlugModel`, `StatusModel`, `SoftDeleteModel`, `MergeableModel`), `Sport`, admin mixins (`ModerationActionsMixin`, `MergeActionsMixin`, `BaseHistoryAdmin`), `/healthz/`, the `seed_demo` command, public query helpers (`shortcuts.py`), public views (home page, years, search), the pagination tag, `core/urls.py` |
| `apps/accounts` | custom `User` (email as `USERNAME_FIELD`), consents, roles, link to `Person` |
| `apps/catalog` | `Team`, `TeamMembership`, `Person`, `PersonName`, `Event`, `EventTeam`, `catalog/urls.py` with team and event lists |
| `apps/photos` | `Source`, `Photo`, `PhotoPerson` |
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

**Stage 2 is split into 15 sub-stages**, one session each. A sub-stage plan is created as a
separate file when work on it starts (`plans/public-archive-2.N-<name>.md`). The order and the
list of sub-stages are in the master plan. Sub-stages 2.1 (demo data), 2.2 (public UI skeleton) and 2.3 (public queries and lists) are
complete; the next one is 2.4 (gallery component), plan approved:
`.koda/plans/public-archive-2.4-photo-gallery.md`.

## Deliberately deferred

Not doing until needed: API (DRF/django-ninja), SPA, k8s, microservices, multi-sport UI, a "sport
constructor". Photo processing is stage 3. Legal pages and SMTP are stage 4.

## Current state

- Version: 0.3.0
- Development branch: `master`
- Stage: 1 (repository foundation and data model) — **complete**;
  stage 2 (public archive) — **in progress**, sub-stages 2.1 (demo data), 2.2
  (public UI skeleton) and 2.3 (public queries and lists) complete
- Tests: 230, passing on Python 3.12 and 3.13 (`make test`); linter clean (`make lint`)

### What exists now

| App | Contents |
|---|---|
| `apps/core` | abstract models (`TimeStampedModel`, `SlugModel`, `StatusModel`, `SoftDeleteModel`, `MergeableModel`), `Sport`, admin mixins (`ModerationActionsMixin`, `MergeActionsMixin`, `BaseHistoryAdmin`), `/healthz/`, the `seed_demo` command, public views (home page, placeholders), `core/urls.py` |
| `apps/accounts` | email-based `User`, `UserManager`, consents (terms/privacy/PD law + version), roles, `User.person` link, signup form with consents, allauth adapter |
| `apps/catalog` | `Team`, `TeamMembership`, `Person`, `PersonName`, `Event`, `EventTeam`, `catalog/urls.py` with placeholder lists |
| `apps/photos` | `Source`, `Photo` (rights, `processing_status`, `phash`, `duplicate_of`), `PhotoPerson`, thumbnails via `ImageSpecField` |
| `apps/comments` | `Comment` (generic FK, replies, statuses) |
| `apps/moderation` | `Report`, `ModerationAction` (moderator decision log) |

Infrastructure: `Dockerfile` + `docker-compose.yml` (db, redis, minio, web, worker), CI
(`.github/workflows/ci.yml`: ruff, migration check, pytest on 3.12 and 3.13 against PostgreSQL 17),
and the `.agents/skills/semver-checker/` skill.

### Public UI

- `templates/base.html` plus `templates/partials/` (navbar, footer, breadcrumbs, messages,
  pagination) and `templates/photos/partials/card.html`; error pages in `templates/404.html` and
  `templates/500.html`.
- Theme: `static/css/sportmemo.css` and `static/js/theme.js` — warm sepia by default, dark
  variant, choice kept in `localStorage`. An inline script in `<head>` applies the theme before
  paint, so there is no flash.
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
  deleted and merged objects are 404 for everyone. The "not published" badge in the UI
  arrives in sub-stage 2.5.
- Lists `/teams/` (24 per page), `/events/` (20), `/years/` and `/years/<year>/` (24 photos)
  return visible objects only. The year list is built from visible photos — the same source
  as the "years in the archive" counter on the home page.
- Sections that do not exist yet (`/search/`) render a shared placeholder template; the real
  search arrives in sub-stage 2.13.
- `apps/core/migrations/0003_site_domain.py` sets the `django.contrib.sites` domain to
  `sportmemo.ru` — absolute links in emails and sitemaps must not point at `example.com`.

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
- UI strings go through `gettext` only. Run `make i18n` after adding strings. `makemessages`
  marks new strings as `fuzzy` when it finds a similar old one, and `msgfmt` skips fuzzy
  entries — check the fuzzy blocks after extraction and clear the flags.
- `django-imagekit` caches the fact that a thumbnail exists in the Django cache. In tests each
  test has its own `MEDIA_ROOT`, so `tests/conftest.py` has a `cleared_cache` fixture; without
  it thumbnail tests fail while reading a file from another test.

