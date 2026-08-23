# Sleeper league main screen — design spec

Date: 2026-08-22

## Problem

The app currently has no entry screen. `/api/board` (and the board UI at
`/`) is driven entirely by `FFDO_LEAGUE_ID` / `FFDO_DRAFT_ID` /
`FFDO_ROSTER_ID` env vars set before the process starts. There is no way
to point the app at a league/user from the browser, and no username →
roster resolution anywhere in the codebase.

`design/Main.dc.html` is an existing Claude Design canvas mock of a main
screen (league card, roster slot chips, "data readiness" list, format
toggle, "Enter draft room" button) but it hardcodes one specific league's
data and has no concept of connecting to a league at all — the card data
is static, and the format toggle/enter button just fake state transitions
with `setTimeout`.

This spec covers building a real main screen: the user types a Sleeper
league ID and username, the app resolves that against Sleeper's API
(league settings — including that league's scoring configuration, the
league's draft, the user, and their roster within that league), stores
the result so later requests (including the existing board) don't need
it re-entered, and lets the user proceed into the draft room with the
format pre-selected from the league's real draft type.

Leagues differ in scoring (PPR vs. standard, TE premium, negative points
for fumbles/INTs, custom bonuses, etc.), and `ffdo.engine.vor` computes
Value Over Replacement from points that are themselves computed by
`ffdo.engine.scoring.score_stats(stats, weights)` — the `weights` are a
league's `scoring_settings`, not a constant. Loading a league on the main
screen must load *that league's* scoring settings so VOR always reflects
the league actually connected, not some other league's rules left over
from a previous session.

## Non-goals

- No historical/cached-auctions feature (the mock's "5 completed auctions
  cached" line) — nothing in the codebase produces that data for an
  arbitrary league; dropped from the real UI.
- No multi-user / multi-session support. This is a single local process
  for one user's draft day, same as today.
- No auth. League ID + username are public Sleeper identifiers already
  used unauthenticated by the existing `/v1/league/<id>` calls.
- No automated frontend tests (none exist in this repo today for
  `board.js` either) — manual in-browser verification only.

## Architecture

```
Browser (main screen)
  → POST /api/connect {league_id, username}
      → ffdo.ingest.connect.resolve(...)   [Sleeper HTTP calls]
      → SessionStore.save(session)          [data/session.json + in-memory]
      → BackgroundTasks: warm players_cache, projections_cache
  → GET /api/session   (rehydrate on load / after refresh)
  → GET /api/readiness (poll while caches are warming)
  → "Enter draft room" → navigate to /board
       → /board's existing /api/board reads league_id/draft_id/roster_id
         from SessionStore first, env vars as fallback (unchanged
         zero-config behavior when no session exists)
```

### Scoring settings and VOR

`get_board()` already re-fetches the full league object (`league.parse()`,
which includes `scoring_settings`) fresh from Sleeper on every request,
keyed off whatever `league_id` `_league_id()` currently resolves to. Once
`_league_id()` is session-aware (see API endpoints below), connecting to
a new league and then loading `/board` automatically scores and computes
VOR against *that* league's live scoring settings — no change to
`ffdo.engine.scoring` or `ffdo.engine.vor` is needed, and nothing caches
scoring settings in a way that could go stale relative to the connected
league. `players_cache`/`projections_cache` stay safe to share across
different connected leagues because they hold raw stats/projections, not
pre-scored points — `score_stats(stats, weights)` is applied fresh per
request using the current league's `weights`.

The one gap this spec closes: `scoring_settings` was fetched during
`resolve()` but not captured anywhere durable — it's added to `Session`
(below) purely so it's visible/available going forward (e.g. the main
screen showing how many scoring keys synced), not because VOR correctness
depended on it being stored.

## Session storage

New `ffdo/api/session.py`:

- `Session` — frozen dataclass: `username`, `user_id`, `league_id`,
  `draft_id`, `roster_id: int | None`, `league_name`, `season`,
  `num_teams`, `budget`, `roster_positions: tuple[str, ...]`,
  `scoring_settings: Mapping[str, float]`, `draft_type`, `draft_status`,
  `connected_at` (ISO timestamp string). `scoring_settings` comes
  straight from the `LeagueProfile` parsed during `resolve()` (see
  Ingest additions below) — captured on the `Session` so it's part of
  the "readily available" data the main screen (and any future feature)
  can read without re-hitting Sleeper, per the same reasoning as
  `league_name`/`roster_positions`.
- `SessionStore(path: Path)` — constructor takes an explicit path (no
  hardcoded default inside the class), mirroring `_TTLCache`'s injectable
  clock so tests never touch the real file:
  - `load() -> Session | None` — reads JSON from `path` if it exists;
    returns `None` (not an exception) if missing or malformed.
  - `save(session) -> None` — writes JSON to `path`, creating parent dirs
    as needed.
  - `get() -> Session | None` — in-memory cached value, populated from
    `load()` on first access.
  - `clear() -> None` — deletes the file and clears the in-memory value
    (not exposed via API in this spec, but useful for tests / a future
    "disconnect" action).
- App wiring: `app.py` constructs one `SessionStore(Path("data") /
  "session.json")` at app-creation time (inside `create_app()`, not a
  module-level singleton, so tests can build their own `create_app()`
  instance with a different store if ever needed — though for this spec,
  tests exercise `SessionStore` directly with a tmp path rather than
  patching the app's instance).
- `data/session.json` is added to `.gitignore` — it's local runtime
  state, not committed fixture data (unlike `data/snapshots/`).

## Ingest additions

`ffdo/domain/models.py`:
- `LeagueProfile` gains `name: str = ""` and `status: str = ""` (defaulted
  so the 8 existing direct-construction call sites in tests don't need
  updating).

`ffdo/ingest/league.py`:
- `parse()` sets `name=raw.get("name", "")` and `status=raw.get("status",
  "")` from the league JSON.
- New `find_roster_id(rosters: list[dict], user_id: str) -> int | None`
  — scans `/v1/league/<id>/rosters` entries for `owner_id == user_id`,
  returns that roster's `roster_id`, or `None` if no match.
- New `most_recent_draft_id(drafts: list[dict]) -> str | None` — Sleeper
  returns a league's drafts newest-first; take `drafts[0]["draft_id"]`,
  or `None` if the list is empty.

`ffdo/ingest/user.py` (new):
- `parse(raw: dict) -> tuple[str, str]` — returns `(user_id,
  display_name)` from `/v1/user/<username>` JSON.

`ffdo/ingest/connect.py` (new):
- `resolve(sleeper: SleeperClient, league_id: str, username: str) ->
  Session` — orchestrates:
  1. `GET /v1/league/{league_id}` → `league.parse()`. 404/permanent HTTP
     error → raise `ConnectError("League not found")`.
  2. `GET /v1/league/{league_id}/drafts` → `league.most_recent_draft_id()`.
     Empty/missing → raise `ConnectError("No draft found for this
     league")`.
  3. `GET /v1/draft/{draft_id}` → `draft.parse(meta, [])` for
     `draft_type`/`status`/`rounds`/`budget` (picks list intentionally
     empty; full pick state isn't needed until the board loads).
  4. `GET /v1/user/{username}` → `user.parse()`. 404 → raise
     `ConnectError("Username not found")`.
  5. `GET /v1/league/{league_id}/rosters` → `league.find_roster_id()`.
     `None` → raise `ConnectError("This user is not a member of that
     league")`.
  6. Assemble and return a `Session` (not yet saved — `resolve()` is pure
     I/O + parsing; the endpoint handler saves it).
- `ConnectError(Exception)` — plain exception carrying a user-facing
  message; `/api/connect` catches it and returns 400 with that message
  as the body. Any other exception (network failure, unexpected shape)
  propagates as a 500 — not masked, since that's a real bug/outage
  signal rather than a bad user input.

## API endpoints (`app.py`)

- `POST /api/connect` — body `{league_id: str, username: str}`.
  - Calls `connect.resolve()`, saves via `SessionStore.save()`.
  - Uses FastAPI `BackgroundTasks` to call `players_cache.get(...)` and
    `projections_cache.get(...)` after the response is sent (same
    loaders already used in `get_board()`), so the caches are warm by
    the time the user reaches `/board`.
  - Returns the `Session` as JSON on success; `ConnectError` → 400 with
    `{"detail": message}`.
- `GET /api/session` — returns the current `Session` as JSON, or `null`
  if none exists. Used on main-screen page load to skip the form and
  show the connected view directly (including after a server restart,
  since the store is file-backed).
- `GET /api/readiness` — returns
  `{"league_draft": "synced", "players": "synced"|"pending",
  "projections": "synced"|"pending"}`. `league_draft` is `"synced"`
  whenever a session exists (it's fetched synchronously in
  `/api/connect`). `players`/`projections` are computed by peeking at
  whether `players_cache`/`projections_cache` already hold a value
  — this peek must not itself trigger a fetch (needs a
  `_TTLCache.has_value()`-style check separate from `.get()`).
- `_league_id()` / `_draft_id()` / `_roster_id()` — each checks
  `session_store.get()` first (`.league_id`, `.draft_id`, `.roster_id`
  respectively); falls back to the existing env-var/default logic
  untouched when no session exists. This is the only change to
  `get_board()`'s behavior — the rest of `get_board()` is unmodified.

## Frontend

Static mounts split:
- `web/` (new `main.html`, `main.js`, styles) mounted at `/` — the main
  screen.
- Existing `index.html`, `board.js`, `board.css` move to `web/board/`,
  mounted at `/board`.

`main.html` / `main.js` (new, plain JS matching `board.js`'s style — no
framework):
- On load: `GET /api/session`. If a session exists, render the connected
  view immediately (pre-filled from that data) — satisfies "data is
  readily available" without re-prompting. If not, render the connect
  form.
- **Form state**: League ID input, Username input, "Connect" button.
  Submits to `POST /api/connect`. On success, switches to connected view
  with the returned session. On `ConnectError` (400), shows the message
  inline under the form; inputs keep their values.
- **Connected state** (adapted from `design/Main.dc.html`, wired to real
  data instead of the mock's hardcoded/faked values):
  - League card: `league_name`, `league_id`, status badge (`draft_status`,
    e.g. `pre_draft`/`drafting`/`complete`), stat grid (teams, budget,
    rounds, format — all from the session), roster slot chips built from
    `roster_positions` (starters highlighted, `BN` muted, same visual
    treatment as the mock). The footer note (which the mock used for the
    fabricated auction-history line) becomes a real, generic line:
    "`{len(scoring_settings)}` scoring keys synced" — confirms the
    league's actual scoring configuration loaded, without claiming
    anything not actually true for an arbitrary league.
  - Data readiness card: rows for League+draft settings (always
    "Synced" once a session exists), Players, Projections — each
    polling `GET /api/readiness` every ~1.5s while any row is
    `"pending"`, stopping once all are `"synced"`. Drops the mock's
    fabricated historical-auctions note.
  - Format toggle: pre-selected to the session's real `draft_type`;
    still togglable (matches the mock's existing behavior/copy pattern,
    with copy genericized — no more "your other leagues" phrasing tied
    to one hardcoded league).
  - "Enter draft room" button: navigates to `/board` (real navigation,
    not the mock's fake `setTimeout` "connecting" animation).

## Testing

- `tests/ingest/test_user.py` (new) — `user.parse()` against sample
  Sleeper user JSON.
- `tests/ingest/test_league.py` — add cases for `find_roster_id()` and
  `most_recent_draft_id()`, plus `parse()` now carrying `name`/`status`.
- `tests/ingest/test_connect.py` (new) — `connect.resolve()` against a
  mocked `SleeperClient` (httpx `MockTransport`, same pattern as
  `test_client.py`), covering the happy path and each `ConnectError`
  case (league not found, username not found, user not in league).
  Happy-path assertion includes that the returned `Session.scoring_settings`
  matches the mocked league's `scoring_settings` verbatim — this is the
  regression guard for "scoring settings actually get captured on
  connect."
- `tests/api/test_app.py` — add a test that two `/api/board` calls against
  sessions for two different leagues (different `scoring_settings`, e.g.
  one with `rec: 1.0` full-PPR and one with `rec: 0.0` standard) produce
  different `adjusted`/`vor` numbers for the same player's stat line —
  end-to-end confirmation that switching the connected league actually
  changes valuations, not just that the raw settings round-trip.
- `tests/api/test_session.py` (new) — `SessionStore` round-trips through
  a tmp-path file; `load()` returns `None` for a missing/malformed file.
- `tests/api/test_app.py` — extend the existing `_league_id`/`_draft_id`/
  `_roster_id` tests with session-present-takes-precedence-over-env-var
  cases; add `TestClient`-based tests for `/api/connect` (success + each
  error case), `/api/session`, `/api/readiness`.
- No new frontend automated tests (consistent with `board.js` today);
  manual verification via `run` in-browser: connect form → error states
  → successful connect → readiness rows resolving → format toggle →
  enter draft room → board loads with the resolved league/roster.
