# Multi-League Foundation — Design

**Date:** 2026-09-02
**Status:** Approved, pending implementation
**Prior art:**
- `docs/superpowers/specs/2026-08-22-sleeper-league-main-screen-design.md` (the connect/session flow this replaces)
- `docs/superpowers/specs/2026-08-23-espn-league-support-design.md` (the second provider, whose `resolve()` this renames to `track()`)

## 0. Context: the larger initiative

This is sub-project #1 of seven that turn FFDO from a single-league,
draft-day tool into a multi-league, season-long companion. The full
decomposition (each piece gets its own spec → plan → build):

1. **Multi-league foundation** — *this spec*. Connect & persist N leagues, switch between them, per-league config.
2. Roster & standings view — your roster, league standings, power ranking vs leaguemates.
3. Weekly optimal lineup — new weekly-projections ingest + a per-league lineup solver. (+decision ledger)
4. Post-draft player valuation model — redraft vs dynasty values; shared dependency for #5 and #6.
5. Trade calculator. (+decision ledger)
6. Waiver-wire recommender — add/drop + FAAB $ bid given remaining budget. (+decision ledger)
7. Outcome tracking & calibration — a scorecard of past-recommendation quality **plus** auto-calibration
   that nudges tunable parameters based on measured bias, logging every adjustment so it is visible and revertible.

A **decision ledger** (structured record of every recommendation, its inputs, the alternative it beat, and
whether the user followed it) is threaded through #3/#5/#6 from their first commit. #7 grades those records
and feeds calibration back into the engine.

This foundation spec deliberately builds **none** of the season-mode
data model (snapshots, "current NFL week" service, standings math,
ledger tables). Those land in the sub-projects whose requirements
actually define them. What this spec does is make every later piece
*possible*: persistent multi-league state on a database, league-scoped
API routing, and a frontend shell with a clean seam where season mode
plugs in.

## 1. Purpose

Let one user track several fantasy leagues at once — across Sleeper and
ESPN, across redraft and dynasty formats and every scoring variant —
and move between them in the UI. Replace the single-league `Session` /
`session.json` model with a database-backed store of N tracked
leagues.

### 1.1 Goals

- **Discover-then-pick onboarding.** Enter Sleeper username once / paste ESPN cookies once; the app lists
  every league that credential belongs to for a season; the user ticks which to track.
- **Persist N leagues** in one portable SQLite file that survives a process restart and moves to a host
  cleanly (see §1.3 — local-first, deployable-later).
- **League switcher + one adaptive detail screen.** A persistent switcher; selecting a league shows the
  right view for its live state — the existing draft board pre-draft/mid-draft, a season-mode placeholder
  once the draft is complete.
- **League-scoped API routing.** The league key lives in the URL (`/api/leagues/{league_key}/...`), not in
  server-side mutable state.
- **Per-league config captured at track time:** scoring settings, roster positions, detected format
  (redraft/keeper/dynasty) with a manual override, and the untouched provider settings blob for later
  sub-projects to parse.
- **One-shot migration** of an existing `data/session.json` into the new store.

### 1.2 Non-goals

- **Season-mode features.** Roster view, standings, lineup optimizer, trade calculator, waiver recommender,
  the decision ledger, and calibration are all later sub-projects. This spec ships a *placeholder* season
  screen only.
- **The (b) multi-league "command center" home** (a grid of all leagues with per-card status). Agreed as a
  fast-follow *after* the feature set (#2–#7) lands, because its cards need those features to have anything
  to show. The switcher + adaptive screen ("model a") is what this spec builds.
- **Auth / multi-user.** Still one user. Local-first; see §1.3.
- **A provider-plugin architecture.** Two providers is still the case where that abstraction does not earn
  its keep; `api/app.py` keeps branching on `provider` directly.
- **Any provider beyond Sleeper and ESPN.** Yahoo etc. are out of scope.
- **New valuation behavior.** The engine (`scoring`, `vor`, `auction`, `market`, `snake_plan`) is untouched.
  Per-league scoring already flows through `score_stats(stats, weights)`.
- **Automated frontend tests.** None exist in the repo today; manual in-browser verification only.

### 1.3 Deployment stance: local-first, deployable-later

The app stays single-user with no auth and runs locally
(`uv run uvicorn ...`) exactly as today. But nothing is hard-coded to
"localhost + one laptop": all state lives in one SQLite file that can
be copied to a VPS, and no feature assumes the process and the browser
are on the same machine. Real hosting/auth is a future exercise, not
part of this spec.

## 2. Architecture

```
Browser (app shell, hash-routed: #/connect, #/, #/league/{key})
  → POST /api/providers/connect {provider, credentials, season}
      → store provider_credential row
      → ffdo.ingest.discover.list_leagues(...) | ffdo.ingest.espn.discover.list_leagues(...)
      → returns [DiscoveredLeague...]  (name, teams, detected format, draft status)
  → POST /api/leagues/track {provider, provider_league_id, season}   (one or many)
      → ffdo.ingest.connect.track(...) | ffdo.ingest.espn.connect.track(...)   [renamed resolve()]
      → LeagueStore.upsert(tracked_league)
  → GET /api/leagues                          (switcher + badges)
  → GET /api/leagues/{key}/board  /board/live  /readiness   [league-scoped; formerly /api/board...]
      → LeagueStore.get(key) → TrackedLeague; ESPN creds from provider_credential table
      → unchanged scoring / VOR / auction / snake_plan / board build
```

### 2.1 The league key

The app's internal league identifier becomes a surfaced synthetic
string: **`"{provider}:{provider_league_id}:{season}"`** (e.g.
`"sleeper:1315881559957458944:2026"`, `"espn:1882997948:2026"`). It is
the primary key of `tracked_league`, the path segment in every
league-scoped route, and the value the switcher passes around. Two
providers therefore cannot collide, and the same league in two seasons
is two rows.

Mock drafts (Sleeper `is_mock`, no `league_id`) use
`"sleeper-mock:{draft_id}:{season}"`.

## 3. Data model & storage

### 3.1 `ffdo/api/store.py` (new) — replaces `ffdo/api/session.py`

`LeagueStore(path: Path)` — SQLite (`sqlite3`, stdlib), one file at
`data/ffdo.db`. Constructor takes an explicit path (mirrors
`SessionStore` / `_TTLCache` injectable-dependency style) so tests use
a tmp path. Creates tables on first open if absent (plain
`CREATE TABLE IF NOT EXISTS`; no migration framework).

**Table `tracked_league`:**

| column | type | notes |
|---|---|---|
| `league_key` | TEXT PK | `{provider}:{provider_league_id}:{season}` |
| `provider` | TEXT | `sleeper` \| `espn` \| `sleeper-mock` |
| `provider_league_id` | TEXT | bare provider id (or draft id for a mock) |
| `season` | INTEGER | |
| `name` | TEXT | |
| `user_id` | TEXT | Sleeper user id, or ESPN SWID |
| `roster_id` | INTEGER NULL | this user's roster/team id in the league |
| `draft_id` | TEXT | |
| `draft_type` | TEXT | `auction` \| `snake` |
| `draft_status` | TEXT | `pre_draft` \| `drafting` \| `complete` — badge only; §6 checks live |
| `num_teams` | INTEGER | |
| `budget` | INTEGER NULL | auction budget |
| `rounds` | INTEGER | |
| `roster_positions` | TEXT (JSON array) | |
| `scoring_settings` | TEXT (JSON object) | |
| `format` | TEXT | detected: `redraft` \| `keeper` \| `dynasty` |
| `format_override` | TEXT NULL | user's manual pick; wins when set |
| `raw_settings` | TEXT (JSON) | untouched provider settings blob, for #2–#7 |
| `is_mock` | INTEGER (0/1) | |
| `tracked_at` | TEXT (ISO) | |
| `last_refreshed_at` | TEXT (ISO) | bumped by `/refresh` and opportunistic board-poll updates |

**Table `provider_credential`:**

| column | type | notes |
|---|---|---|
| `provider` | TEXT PK | `sleeper` \| `espn` |
| `user_identifier` | TEXT | Sleeper username / ESPN SWID |
| `espn_s2` | TEXT NULL | |
| `swid` | TEXT NULL | |
| `updated_at` | TEXT (ISO) | |

Credentials live in their own table so ESPN cookies are stored once,
not copied onto every ESPN league row, and re-auth updates one place.

**`LeagueStore` methods:**
- `list() -> list[TrackedLeague]`
- `get(league_key) -> TrackedLeague | None`
- `upsert(tracked: TrackedLeague) -> None` — INSERT OR REPLACE on `league_key`, preserving an existing
  `format_override` and `tracked_at` when re-tracking/refreshing.
- `delete(league_key) -> None`
- `set_format_override(league_key, value: str | None) -> None`
- `touch_status(league_key, draft_status: str) -> None` — opportunistic badge update from a board poll.
- `get_credential(provider) -> ProviderCredential | None`
- `put_credential(cred: ProviderCredential) -> None`

Malformed/locked/missing DB on read → return empty/`None`, never raise
(matches `SessionStore.load()` returning `None` for a bad file).

### 3.2 Domain types (`ffdo/domain/models.py`)

- **Remove** `Session`.
- **Add** `TrackedLeague` (frozen, slots) — every `tracked_league` column *except credentials*, with
  `roster_positions: tuple[str, ...]` and `scoring_settings: Mapping[str, float]` typed. Property
  `resolved_format -> str` returns `format_override or format`. Property `starting_slots` /
  `roster_size` carry over from where they lived (they are on `LeagueProfile`, unchanged).
- **Add** `ProviderCredential` (frozen) — `provider`, `user_identifier`, `espn_s2: str | None`,
  `swid: str | None`, `updated_at`.
- **Add** `DiscoveredLeague` (frozen) — `provider`, `provider_league_id`, `season`, `name`,
  `num_teams`, `draft_type`, `format`, `draft_status`, `already_tracked: bool`.
- `LeagueProfile` — unchanged.

### 3.3 Migration

On `LeagueStore` first open: if `data/session.json` exists and
`tracked_league` is empty, read the old session JSON, construct the
equivalent `TrackedLeague` (+ `ProviderCredential` from its
`espn_s2`/`swid`/`username`), `upsert` both, then rename
`session.json` → `session.json.migrated`. Idempotent (guarded by the
empty-table check and the rename). One helper function,
`_migrate_legacy_session(conn, path)`, called from `__init__`.

## 4. Ingest additions

### 4.1 Discovery

**`ffdo/ingest/discover.py` (new) — Sleeper:**
```
list_leagues(sleeper: SleeperClient, user_id: str, season: int) -> list[DiscoveredLeague]
```
`GET /v1/user/{user_id}/leagues/nfl/{season}`. Maps each entry to a
`DiscoveredLeague` — `name`, `num_teams` (`settings.num_teams` or
`total_rosters`), `draft_type` (`settings` / a follow-up per-league
draft lookup is **not** done here — kept lightweight; `draft_type` from
the league settings' `type`-independent hints, else left `""` and
filled at track time), `format` via `league.detect_format(raw)`,
`draft_status` from `status`. Username → `user_id` reuses
`user.parse()` on `GET /v1/user/{username}`.

**`ffdo/ingest/espn/discover.py` (new) — ESPN:**
```
list_leagues(espn_s2: str, swid: str, season: int, *, transport=None) -> list[DiscoveredLeague]
```
Calls ESPN's fan API —
`https://fan.api.espn.com/apis/v2/fans/{swid}?configuration=SITE_DEFAULT&displayEvents=true&displayNow=true&displayRecs=true&recExperiment=us-fantasy-sec_1&featureFlags=fanApiIntegrationWebview&source=ESPN.com+-+FAM&lang=en&section=espn`
(exact query params verified during implementation) — filters
`preferences` entries to `ffl` + the requested `season`, maps to
`DiscoveredLeague`. 401/403 → `ConnectError` with the existing
expired-cookies message. If the fan endpoint proves unreliable in
testing, the manual "add by league ID" path (§6) is the documented
fallback and this function may return `[]` with a logged warning
rather than raising.

Both discovery functions set `already_tracked` by checking the caller-supplied
set of existing `league_key`s.

### 4.2 Format detection — `ffdo/ingest/league.py`

Add `detect_format(raw: dict) -> str`:
- **Sleeper:** `settings.type == 2` → `"dynasty"`; else if `settings.type == 1`
  or `settings.max_keepers` (present and > 0) or `previous_league_id` set → `"keeper"`;
  else `"redraft"`.
- **ESPN** (`ffdo/ingest/espn/league.py` gets its own `detect_format(raw)`):
  `settings.draftSettings.keeperCount > 0` → `"keeper"`; else `"redraft"`.
  ESPN exposes no true dynasty flag — `"dynasty"` there is override-only.

### 4.3 `resolve()` → `track()`

`ffdo/ingest/connect.py`: `resolve()` → `track()`, `resolve_mock()` →
`track_mock()`; return `TrackedLeague` instead of `Session`. The
Sleeper-call orchestration (league → drafts → draft meta → user →
rosters → roster_id) is **unchanged**; only the returned type and the
added `format` / `raw_settings` fields differ.

`ffdo/ingest/espn/connect.py`: `resolve()` → `track()`, same treatment.
It keeps building its own `EspnClient` and still returns the
`espn_s2`/`swid` — but now the caller writes those to
`provider_credential`, not onto the league row.

`ConnectError` stays as-is in both modules.

## 5. API endpoints (`ffdo/api/app.py`)

### 5.1 League management (new)

| method + path | body | returns |
|---|---|---|
| `POST /api/providers/connect` | `{provider:"sleeper", username, season}` or `{provider:"espn", season, espn_s2, swid}` | `{leagues: [DiscoveredLeague...]}` — also upserts the `provider_credential` row |
| `GET /api/leagues/discovered?provider=&season=` | — | `{leagues:[...]}` — re-run discovery against stored credentials (add-more flow, no re-entry) |
| `GET /api/leagues` | — | `[{league_key, name, provider, season, format, resolved_format, draft_status, is_mock, needs_attention:false}]` |
| `GET /api/leagues/{league_key}` | — | one `TrackedLeague`, credential-stripped (name/format/roster chips for the detail screen); 404 if absent |
| `POST /api/leagues/track` | `{provider, provider_league_id, season}` or `{leagues:[...]}` | the `TrackedLeague`(s), credential-stripped |
| `DELETE /api/leagues/{league_key}` | — | `204`; row deleted, any future ledger rows kept |
| `PATCH /api/leagues/{league_key}` | `{format_override: "dynasty" \| null}` | updated `TrackedLeague` |
| `POST /api/leagues/{league_key}/refresh` | — | re-fetch settings/draft/roster from provider, `upsert`, bump `last_refreshed_at` |

`needs_attention` is a hard-coded `false` placeholder — #2+ computes it
(lineup not set, waivers pending, etc.).

### 5.2 Board endpoints become league-scoped

- `GET /api/board`      → `GET /api/leagues/{league_key}/board`
- `GET /api/board/live` → `GET /api/leagues/{league_key}/board/live`
- `GET /api/readiness`  → `GET /api/leagues/{league_key}/readiness`

Internals: `_league_id()` / `_draft_id()` / `_roster_id()` are deleted.
One helper replaces them:
```
_load_league(league_key: str) -> TrackedLeague   # 404 if absent
```
`get_board()` / `get_board_live()` read `provider`, `league_id`,
`draft_id`, `roster_id`, `season`, `scoring_settings`, etc. off that
object. The ESPN branch reads `espn_s2` / `swid` from
`_STORE.get_credential("espn")` and 400s with the existing re-auth
message when they are missing/expired. The per-season/per-league TTL
caches (`players_cache`, `projections_caches`, `teams_caches`,
`espn_player_pool_caches`, `espn_crosswalk_caches`) are **unchanged** —
still keyed by season / `league_id`.

Every successful board poll calls
`_STORE.touch_status(league_key, state.status)` so the switcher badge
tracks reality without a manual refresh.

### 5.3 Removed

- `POST /api/connect`, `GET /api/session`.
- `_DEFAULT_LEAGUE_ID` / `_DEFAULT_DRAFT_ID` and the
  `FFDO_LEAGUE_ID` / `FFDO_DRAFT_ID` / `FFDO_ROSTER_ID` env-var
  fallbacks. The app now always starts from "connect a provider"; the
  old pinned league moves to `scripts/seed_dev_league.py` for local
  dev convenience.
- The module-level `_SESSION_STORE` becomes `_STORE = LeagueStore(Path("data") / "ffdo.db")`,
  monkeypatched by tests the same way.

### 5.4 Error handling

| situation | response |
|---|---|
| discovery, expired ESPN cookies | 400 `{"detail":"Your ESPN cookies look expired — grab fresh espn_s2/SWID values"}` |
| `track` / board / `refresh` on an unknown `league_key` | 404 `{"detail":"League not tracked"}` |
| provider HTTP failure during discovery/track | 502 `{"detail":"Couldn't reach {provider}, try again"}` — not masked as user error |
| ESPN credentials expire while a league is tracked | board endpoint 400 with the re-auth message; detail screen shows a "Reconnect ESPN" prompt |
| `PATCH` with a `format_override` not in `{redraft,keeper,dynasty,null}` | 422 |

## 6. Frontend (`ffdo/web/`)

Plain JS, no build step, matching `board.js` style. An app shell with
**hash routing** — `#/connect`, `#/` (redirects to last-viewed league
or `#/connect` if none), `#/league/{league_key}`.

**File layout:**
- `web/index.html` — the shell (brand row, `<div id="switcher">`, `<main id="view">`), loads `app.js`.
- `web/app.js` — router + switcher + connect/discovery screen.
- `web/app.css` — shell + connect + switcher styles (absorbs today's `main.css`).
- `web/board/` — the existing board files, **logic unchanged**, except the three `fetch("/api/board...")`
  calls in `board.js` take a `leagueKey` (read from the hash) and hit `/api/leagues/{key}/board...`.
  The board mounts into `<main id="view">` for a drafting league.

### 6.1 Connect / discovery screen (`#/connect`)

Reworked from today's `index.html` connect form:
- Provider toggle (Sleeper / ESPN) + credential fields — **unchanged** from today, plus a season field.
- Submit → `POST /api/providers/connect` → render the returned list as a **checklist**: each row shows
  name, teams, detected-format badge, draft-status badge; rows already tracked are shown checked + disabled.
- Tick rows → "Track selected" → `POST /api/leagues/track` → navigate to the first newly-tracked league.
- A "＋ add by league ID / draft link" disclosure keeps the current single-add path (discovery misses, mocks).

### 6.2 League switcher (persistent header)

Present on every screen except `#/connect`. A `<select>` (or simple
dropdown) of tracked leagues — `name` + format badge — plus a
"＋ Add leagues" item routing to `#/connect`. Selecting an option sets
`location.hash = "#/league/" + key`. Last-viewed `league_key` persisted
to `localStorage`; `#/` reads it.

### 6.3 League detail screen (`#/league/{key}`) — adaptive

On entry: `GET /api/leagues/{key}` for name/format/roster chips, then
start polling `/api/leagues/{key}/board`. **Board-vs-season-mode is
decided from the live board response's draft status, not the stored
`draft_status`:**

- status `pre_draft` or `drafting` → render the **existing draft board**, unchanged behavior, keep polling.
- first poll that returns `complete` → swap to **season mode**.
- season mode in this spec = a **placeholder card**: league stat grid (teams, format, scoring-key count,
  budget) + roster-slot chips (the components already rendered on today's connected view), plus copy:
  "Season mode — roster, standings, lineups and waivers arrive in upcoming releases." This card is the
  seam #2/#3/#6 build into.

A `PATCH`-backed format dropdown (redraft/keeper/dynasty) sits on this
screen's header so the override is set where the league is viewed.

Empty state (no tracked leagues) → `#/connect`.

### 6.4 Removed frontend

`web/main.js`, `web/main.css`, the standalone `web/index.html` connect
page (its markup is absorbed into the shell's `#/connect` view).

## 7. Testing

`uv run pytest` green before this sub-project is done. New / changed:

- **`tests/api/test_store.py` (new)** — `LeagueStore` round-trips `tracked_league` + `provider_credential`
  through a tmp-path DB; `list`/`get`/`upsert`/`delete`/`set_format_override`/`touch_status`;
  `upsert` preserves an existing `format_override` and `tracked_at`; malformed/missing DB → empty/`None`,
  no raise; the `session.json` → SQLite migration runs once, is idempotent, and renames the legacy file.
- **`tests/ingest/test_discover.py` (new)** — Sleeper `list_leagues` against a mocked `SleeperClient`
  (httpx `MockTransport`, existing pattern): happy path (multiple leagues), empty list, `already_tracked`
  flagging.
- **`tests/ingest/espn/test_discover.py` (new)** — ESPN `list_leagues` against a mocked fan-API payload:
  happy path, non-`ffl` / wrong-season filtering, 401 → `ConnectError`.
- **`tests/ingest/test_league.py`** — add `detect_format` cases: Sleeper redraft (`type 0`),
  keeper (`type 1` / `max_keepers` / `previous_league_id`), dynasty (`type 2`); ESPN keeper vs redraft.
- **`tests/api/test_connect.py` → renamed `tests/api/test_track.py`** — existing `resolve()` cases pass
  under `track()`, now asserting a `TrackedLeague` with populated `format` and `raw_settings`.
- **`tests/api/test_app.py`** — replace `_league_id`/`_draft_id`/`_roster_id`/env-var tests with
  `_load_league(key)` lookups (unknown key → 404); **keep the cross-league regression guard** —
  `/api/leagues/{key}/board` for two tracked leagues whose `scoring_settings` differ (`rec:1.0` vs `rec:0.0`)
  produces different `adjusted`/`vor` for the same stat line, re-pointed at the new route; `TestClient`
  coverage for `/api/providers/connect`, `/api/leagues`, `/api/leagues/track` (single + batch),
  `DELETE`, `PATCH` (valid + 422), `/refresh`.
- **No new frontend automated tests.** Manual in-browser verification via `run`: connect (Sleeper + ESPN) →
  discovery checklist → track several → switcher moves between them → a `pre_draft` league shows today's
  board → a `complete` league shows the season placeholder → format override persists → migration of an
  existing `session.json` on first launch.

## 8. Config / repo changes

- `data/ffdo.db` added to `.gitignore`. `data/session.json` no longer read after migration.
- `FFDO_LEAGUE_ID` / `FFDO_DRAFT_ID` / `FFDO_ROSTER_ID` removed from `app.py` and README.
- `scripts/seed_dev_league.py` (new) — tracks the old pinned 2026 auction league into a local
  `data/ffdo.db` for zero-config dev, replacing the removed env-var defaults.
- README "Running it" section rewritten: start server → open app → connect a provider → pick leagues.
  Drop the env-var paragraph.

## 9. Open questions / deferred

- **ESPN fan-API stability** — the exact endpoint + query params are verified during implementation;
  manual add-by-ID is the contractual fallback if it is flaky.
- **Credential encryption at rest** — `provider_credential` stores ESPN cookies in plaintext in the SQLite
  file, same exposure as today's `session.json`. Acceptable while local-first; revisit when §1.3's hosting
  step happens.
- **Season rollover** — how a 2026 tracked league relates to its 2027 successor (new row? carry format
  override forward?) is a question for whenever a second season matters; not now.
- **`needs_attention`** computation — deferred to #2.
