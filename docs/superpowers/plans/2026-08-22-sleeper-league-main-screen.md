# Sleeper League Main Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real main/connect screen where the user enters a Sleeper league ID and username; the app resolves that against Sleeper's API (league settings including scoring configuration, the league's draft, the user, and their roster), persists the result so it survives a restart, warms the player/projection caches, and lets the user enter the draft room with the format pre-selected from the league's real draft type.

**Architecture:** A new `ffdo.ingest.connect.resolve()` orchestrates the Sleeper lookups and returns a `Session` (a new frozen domain type). A file-backed `SessionStore` (new `ffdo/api/session.py`) persists it. The existing `_league_id()`/`_draft_id()`/`_roster_id()` helpers in `ffdo/api/app.py` check the store first and fall back to today's env vars — so `/api/board`'s entire engine pipeline (scoring → VOR → board) automatically operates on whatever league is currently connected, with zero changes to `ffdo.engine.*`. Three new endpoints (`POST /api/connect`, `GET /api/session`, `GET /api/readiness`) drive a new static main screen (`web/index.html` + `web/main.js`); the existing board moves to `/board`.

**Tech Stack:** Python 3.12, FastAPI, httpx, pytest (existing stack — no new dependencies). Vanilla JS/CSS for the frontend, matching `web/board.js`'s existing style.

**Spec:** `docs/superpowers/specs/2026-08-22-sleeper-league-main-screen-design.md`

## Global Constraints

- No new dependencies — everything is built on `httpx`, `fastapi`, and the standard library already in `pyproject.toml`.
- `ffdo.engine.scoring` and `ffdo.engine.vor` are NOT modified — they already take `scoring_settings`/`league` as parameters; the fix is entirely in how `league_id` gets resolved, not in the engine.
- `LeagueProfile`'s new `name`/`status` fields are defaulted (`= ""`) so none of the 8 existing direct-construction call sites need updating.
- `_league_id()` / `_draft_id()` / `_roster_id()` keep their existing zero-argument signatures — existing tests that import and call them directly must keep passing unmodified.
- `data/session.json` is local runtime state, not committed fixture data — it's gitignored, unlike `data/snapshots/`.
- Every new test that touches `SessionStore` persistence uses `tmp_path`, never the real `data/session.json`.
- Follow existing code conventions exactly: `from __future__ import annotations`, frozen `slots=True` dataclasses for domain types, lazy `from ffdo.X import Y as Y_mod` imports inside `create_app()` for ingest/engine modules (matches every existing import in that function).

---

## Task 1: Regression test — VOR reflects the league's scoring settings

This task adds no production code. It's the direct regression guard for the user's requirement ("VOR is adjusted for scoring configuration") and can run standalone before anything else exists.

**Files:**
- Modify: `tests/engine/test_vor.py`

**Interfaces:**
- Consumes: `ffdo.engine.scoring.score_stats(stats, weights)` (existing), `ffdo.engine.vor.compute(points, profiles, league)` (existing), `ffdo.domain.models.LeagueProfile`/`PlayerProfile` (existing).
- Produces: nothing new — this is a test-only task.

- [ ] **Step 1: Write the failing test**

Append to `tests/engine/test_vor.py` (add `from ffdo.engine import scoring, vor` — note `scoring` needs adding to the existing `from ffdo.engine import vor` import line):

```python
from ffdo.engine import scoring, vor
```

```python
def test_vor_reflects_the_leagues_scoring_settings():
    """Different scoring_settings must produce a different VOR for the same
    raw stat line -- ffdo.engine.scoring.score_stats and ffdo.engine.vor.compute
    take scoring_settings/league as parameters rather than hardcoding one
    league's rules, so switching the connected league (see ffdo.ingest.connect)
    must actually change valuations, not just round-trip the settings."""
    stats = {
        "star": {"rec": 80.0, "rec_yd": 600.0, "rush_yd": 400.0, "rush_td": 4},
        "replacement": {"rec": 20.0, "rec_yd": 150.0, "rush_yd": 300.0, "rush_td": 1},
    }
    profiles = _profiles({"star": "RB", "replacement": "RB"})
    league = LeagueProfile(league_id="x", season=2026, num_teams=1,
                           roster_positions=("RB",), scoring_settings={}, budget=200)

    def star_vor(scoring_settings):
        points = {pid: scoring.score_stats(s, scoring_settings) for pid, s in stats.items()}
        return vor.compute(points, profiles, league)["star"].vor

    full_ppr_vor = star_vor({"rec": 1.0, "rec_yd": 0.1, "rush_yd": 0.1, "rush_td": 6})
    standard_vor = star_vor({"rec": 0.0, "rec_yd": 0.1, "rush_yd": 0.1, "rush_td": 6})

    assert full_ppr_vor != standard_vor
    assert full_ppr_vor == 133.0  # 204 - 71
    assert standard_vor == 73.0   # 124 - 51
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_vor.py::test_vor_reflects_the_leagues_scoring_settings -v`
Expected: FAIL — `NameError: name 'scoring' is not defined` (the import hasn't been added yet).

- [ ] **Step 3: Add the import**

Change the top of `tests/engine/test_vor.py` from:
```python
from ffdo.domain.models import LeagueProfile, PlayerProfile
from ffdo.engine import vor
```
to:
```python
from ffdo.domain.models import LeagueProfile, PlayerProfile
from ffdo.engine import scoring, vor
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_vor.py -v`
Expected: PASS — all tests in the file, including the new one.

- [ ] **Step 5: Commit**

```bash
git add tests/engine/test_vor.py
git commit -m "test: guard that VOR responds to a league's scoring settings"
```

---

## Task 2: Domain types — `LeagueProfile.name`/`status`, new `Session`

**Files:**
- Modify: `src/ffdo/domain/models.py`
- Test: `tests/domain/test_models.py`

**Interfaces:**
- Consumes: nothing new (pure dataclass additions).
- Produces:
  - `LeagueProfile.name: str` (default `""`), `LeagueProfile.status: str` (default `""`).
  - `Session` — new frozen dataclass with fields: `username: str`, `user_id: str`, `league_id: str`, `draft_id: str`, `roster_id: int | None`, `league_name: str`, `season: int`, `num_teams: int`, `budget: int | None`, `roster_positions: tuple[str, ...]`, `scoring_settings: Mapping[str, float]`, `draft_type: str`, `draft_status: str`, `connected_at: str`. Used by Tasks 3, 4, 5, 6, 7.

- [ ] **Step 1: Write the failing tests**

Add to `tests/domain/test_models.py` (extend the existing `from ffdo.domain.models import (...)` block to also import `Session`, and add these tests after `test_league_profile_derives_starting_slots_and_roster_size`):

```python
def test_league_profile_name_and_status_default_to_empty_string():
    lg = LeagueProfile(
        league_id="x", season=2026, num_teams=12,
        roster_positions=("QB", "BN"), scoring_settings={}, budget=200,
    )
    assert lg.name == ""
    assert lg.status == ""


def test_league_profile_accepts_name_and_status():
    lg = LeagueProfile(
        league_id="x", season=2026, num_teams=12,
        roster_positions=("QB", "BN"), scoring_settings={}, budget=200,
        name="P-Vegas Ballers", status="pre_draft",
    )
    assert lg.name == "P-Vegas Ballers"
    assert lg.status == "pre_draft"


def test_session_is_frozen_and_holds_the_connected_leagues_identity():
    session = Session(
        username="tester", user_id="U1", league_id="L1", draft_id="D1",
        roster_id=3, league_name="Test League", season=2026, num_teams=12,
        budget=200,
        roster_positions=("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                          "BN", "BN", "BN", "BN", "BN"),
        scoring_settings={"rec": 0.5}, draft_type="auction",
        draft_status="pre_draft", connected_at="2026-08-22T00:00:00+00:00",
    )
    assert session.roster_id == 3
    assert session.scoring_settings == {"rec": 0.5}
    with pytest.raises(dataclasses.FrozenInstanceError):
        session.roster_id = 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/domain/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'Session'` and/or `TypeError: LeagueProfile.__init__() got an unexpected keyword argument 'name'`.

- [ ] **Step 3: Implement**

In `src/ffdo/domain/models.py`, update the import line at the top of the test file first:
```python
from ffdo.domain.models import (
    DraftPick, DraftState, LeagueProfile, MarketADP,
    PlayerProfile, SeasonProjection, SeasonStatLine, Session,
)
```

Then in `src/ffdo/domain/models.py`, change the `LeagueProfile` dataclass:
```python
@dataclass(frozen=True, slots=True)
class LeagueProfile:
    league_id: str
    season: int
    num_teams: int
    roster_positions: tuple[str, ...]
    scoring_settings: Mapping[str, float]
    budget: int | None
    name: str = ""
    status: str = ""

    @property
    def starting_slots(self) -> tuple[str, ...]:
        return tuple(p for p in self.roster_positions if p != "BN")

    @property
    def roster_size(self) -> int:
        return len(self.roster_positions)
```

And add a new `Session` dataclass at the end of the file, after `ValuedPlayer`:
```python
@dataclass(frozen=True, slots=True)
class Session:
    """A connected Sleeper league/user/draft, as resolved by
    `ffdo.ingest.connect.resolve()` and persisted by `ffdo.api.session.SessionStore`.
    """
    username: str
    user_id: str
    league_id: str
    draft_id: str
    roster_id: int | None
    league_name: str
    season: int
    num_teams: int
    budget: int | None
    roster_positions: tuple[str, ...]
    scoring_settings: Mapping[str, float]
    draft_type: str
    draft_status: str
    connected_at: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/domain/test_models.py -v`
Expected: PASS — all tests, including the three new ones.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest -v`
Expected: PASS — the 8 existing `LeagueProfile(...)` call sites across other test files must still construct successfully since `name`/`status` are defaulted.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/domain/models.py tests/domain/test_models.py
git commit -m "feat: add LeagueProfile.name/status and a Session domain type"
```

---

## Task 3: `ffdo.ingest.league` — name/status wiring, `find_roster_id`, `most_recent_draft_id`

**Files:**
- Modify: `src/ffdo/ingest/league.py`
- Test: `tests/ingest/test_adapters.py`

**Interfaces:**
- Consumes: `LeagueProfile.name`/`status` (Task 2).
- Produces:
  - `league.parse(raw)` now sets `name`/`status` from the raw league JSON.
  - `league.most_recent_draft_id(drafts: list[dict]) -> str | None` — used by Task 5.
  - `league.find_roster_id(rosters: list[dict], user_id: str) -> int | None` — used by Task 5.

- [ ] **Step 1: Write the failing tests**

Add to `tests/ingest/test_adapters.py` (after `test_league_parse_reads_roster_and_scoring`):

```python
def test_league_parse_reads_name_and_status():
    raw = {"league_id": "1", "season": "2026", "settings": {"num_teams": 12},
          "total_rosters": 12, "roster_positions": ["QB", "RB"],
          "scoring_settings": {}, "name": "Test League", "status": "pre_draft"}
    lg = league.parse(raw)
    assert lg.name == "Test League"
    assert lg.status == "pre_draft"


def test_league_parse_defaults_missing_name_and_status_to_empty_string():
    raw = {"league_id": "1", "season": "2026", "settings": {"num_teams": 12},
          "total_rosters": 12, "roster_positions": ["QB"], "scoring_settings": {}}
    lg = league.parse(raw)
    assert lg.name == ""
    assert lg.status == ""


def test_most_recent_draft_id_takes_the_first_entry():
    """Sleeper's /league/<id>/drafts returns a league's drafts newest-first."""
    drafts = [{"draft_id": "newest"}, {"draft_id": "older"}]
    assert league.most_recent_draft_id(drafts) == "newest"


def test_most_recent_draft_id_is_none_for_an_empty_list():
    assert league.most_recent_draft_id([]) is None


def test_find_roster_id_matches_on_owner_id():
    rosters = [{"roster_id": 1, "owner_id": "u1"}, {"roster_id": 2, "owner_id": "u2"}]
    assert league.find_roster_id(rosters, "u2") == 2


def test_find_roster_id_is_none_when_the_user_owns_no_roster():
    rosters = [{"roster_id": 1, "owner_id": "u1"}]
    assert league.find_roster_id(rosters, "stranger") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_adapters.py -v`
Expected: FAIL — `AttributeError: module 'ffdo.ingest.league' has no attribute 'most_recent_draft_id'` (and the name/status assertions fail since `parse()` doesn't set them yet).

- [ ] **Step 3: Implement**

Replace `src/ffdo/ingest/league.py` with:

```python
"""Translates /v1/league/<id> into LeagueProfile."""

from __future__ import annotations

from typing import Any

from ffdo.domain.models import LeagueProfile


def parse(raw: dict[str, Any]) -> LeagueProfile:
    settings = raw.get("settings") or {}
    return LeagueProfile(
        league_id=raw["league_id"],
        season=int(raw["season"]),
        num_teams=int(settings.get("num_teams") or raw.get("total_rosters")),
        roster_positions=tuple(raw["roster_positions"]),
        scoring_settings={k: float(v)
                          for k, v in (raw.get("scoring_settings") or {}).items()},
        budget=settings.get("budget"),
        name=raw.get("name") or "",
        status=raw.get("status") or "",
    )


def most_recent_draft_id(drafts: list[dict[str, Any]]) -> str | None:
    """`drafts` is the payload of `/v1/league/<id>/drafts`, newest first."""
    if not drafts:
        return None
    return drafts[0]["draft_id"]


def find_roster_id(rosters: list[dict[str, Any]], user_id: str) -> int | None:
    """`rosters` is the payload of `/v1/league/<id>/rosters`."""
    for roster in rosters:
        if roster.get("owner_id") == user_id:
            return int(roster["roster_id"])
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/test_adapters.py -v`
Expected: PASS — all tests in the file.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/league.py tests/ingest/test_adapters.py
git commit -m "feat: parse league name/status; add find_roster_id and most_recent_draft_id"
```

---

## Task 4: `ffdo.ingest.user` — parse `/v1/user/<username>`

**Files:**
- Create: `src/ffdo/ingest/user.py`
- Test: `tests/ingest/test_user.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `user.parse(raw: dict) -> tuple[str, str]` — returns `(user_id, display_name)`. Used by Task 5.

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/test_user.py`:

```python
from ffdo.ingest import user


def test_parse_extracts_user_id_and_display_name():
    raw = {"user_id": "437507358097141760", "username": "handle",
          "display_name": "Handle Display Name"}
    user_id, display_name = user.parse(raw)
    assert user_id == "437507358097141760"
    assert display_name == "Handle Display Name"


def test_parse_falls_back_to_username_when_display_name_is_missing():
    raw = {"user_id": "1", "username": "handle", "display_name": None}
    user_id, display_name = user.parse(raw)
    assert display_name == "handle"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_user.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.ingest.user'`.

- [ ] **Step 3: Implement**

Create `src/ffdo/ingest/user.py`:

```python
"""Translates /v1/user/<username> into a (user_id, display_name) pair."""

from __future__ import annotations

from typing import Any


def parse(raw: dict[str, Any]) -> tuple[str, str]:
    user_id = raw["user_id"]
    display_name = raw.get("display_name") or raw.get("username") or ""
    return user_id, display_name
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/test_user.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/user.py tests/ingest/test_user.py
git commit -m "feat: add ffdo.ingest.user to parse /v1/user/<username>"
```

---

## Task 5: `ffdo.ingest.connect` — resolve a league ID + username into a `Session`

**Files:**
- Create: `src/ffdo/ingest/connect.py`
- Test: `tests/ingest/test_connect.py`

**Interfaces:**
- Consumes: `Session` (Task 2), `league.most_recent_draft_id`/`find_roster_id`/`parse` (Task 3), `user.parse` (Task 4), `draft.parse` (existing), `client.V1`/`SleeperClient` (existing).
- Produces: `connect.resolve(sleeper, league_id, username, *, now=None) -> Session`, `connect.ConnectError(Exception)`. Used by Task 7 (`/api/connect`).

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/test_connect.py`:

```python
from datetime import datetime, timezone

import httpx
import pytest

from ffdo.ingest import connect
from ffdo.ingest.client import SleeperClient

LEAGUE_RAW = {
    "league_id": "L1", "season": "2026", "settings": {"num_teams": 12, "budget": 200},
    "total_rosters": 12,
    "roster_positions": ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                        "BN", "BN", "BN", "BN", "BN"],
    "scoring_settings": {"rec": 0.5, "pass_td": 4},
    "name": "Test League", "status": "pre_draft",
}
DRAFTS_RAW = [{"draft_id": "D1", "type": "auction", "status": "pre_draft"}]
DRAFT_META = {"draft_id": "D1", "type": "auction", "status": "pre_draft",
             "settings": {"teams": 12, "rounds": 13, "budget": 200}}
USER_RAW = {"user_id": "U1", "display_name": "tester", "username": "tester"}
ROSTERS_RAW = [{"roster_id": 3, "owner_id": "U1"},
              {"roster_id": 4, "owner_id": "U2"}]


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def _happy_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.endswith("/league/L1/rosters"):
        return httpx.Response(200, json=ROSTERS_RAW)
    if url.endswith("/league/L1/drafts"):
        return httpx.Response(200, json=DRAFTS_RAW)
    if url.endswith("/league/L1"):
        return httpx.Response(200, json=LEAGUE_RAW)
    if url.endswith("/draft/D1"):
        return httpx.Response(200, json=DRAFT_META)
    if url.endswith("/user/tester"):
        return httpx.Response(200, json=USER_RAW)
    raise AssertionError(f"unexpected URL: {url}")


def test_resolve_returns_a_fully_populated_session():
    client = _client(_happy_handler)
    session = connect.resolve(
        client, "L1", "tester",
        now=lambda: datetime(2026, 8, 22, tzinfo=timezone.utc))

    assert session.username == "tester"
    assert session.user_id == "U1"
    assert session.league_id == "L1"
    assert session.draft_id == "D1"
    assert session.roster_id == 3
    assert session.league_name == "Test League"
    assert session.season == 2026
    assert session.num_teams == 12
    assert session.budget == 200
    assert session.roster_positions == (
        "QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
        "BN", "BN", "BN", "BN", "BN")
    assert session.scoring_settings == {"rec": 0.5, "pass_td": 4}
    assert session.draft_type == "auction"
    assert session.draft_status == "pre_draft"
    assert session.connected_at == "2026-08-22T00:00:00+00:00"


def test_resolve_falls_back_to_the_drafts_budget_when_league_settings_omit_it():
    """Mirrors the fallback already used in ffdo.api.app.get_board(): some
    leagues carry the auction budget on the draft object, not the league's
    own settings. LEAGUE_RAW normally carries settings.budget=200 (asserted
    by test_resolve_returns_a_fully_populated_session) -- this test removes
    it to isolate the fallback path specifically."""
    league_no_budget = {**LEAGUE_RAW, "settings": {"num_teams": 12}}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/league/L1/rosters"):
            return httpx.Response(200, json=ROSTERS_RAW)
        if url.endswith("/league/L1/drafts"):
            return httpx.Response(200, json=DRAFTS_RAW)
        if url.endswith("/league/L1"):
            return httpx.Response(200, json=league_no_budget)
        if url.endswith("/draft/D1"):
            return httpx.Response(200, json=DRAFT_META)
        if url.endswith("/user/tester"):
            return httpx.Response(200, json=USER_RAW)
        raise AssertionError(f"unexpected URL: {url}")

    session = connect.resolve(_client(handler), "L1", "tester")
    assert session.budget == 200  # from DRAFT_META.settings.budget


def test_resolve_raises_when_league_is_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(connect.ConnectError, match="League not found"):
        connect.resolve(_client(handler), "bad-league", "tester")


def test_resolve_raises_when_the_league_has_no_draft():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/league/L1/drafts"):
            return httpx.Response(200, json=[])
        if url.endswith("/league/L1"):
            return httpx.Response(200, json=LEAGUE_RAW)
        raise AssertionError(f"unexpected URL: {url}")

    with pytest.raises(connect.ConnectError, match="No draft found"):
        connect.resolve(_client(handler), "L1", "tester")


def test_resolve_raises_when_username_is_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/user/ghost"):
            return httpx.Response(404, json={"error": "not found"})
        if url.endswith("/league/L1/drafts"):
            return httpx.Response(200, json=DRAFTS_RAW)
        if url.endswith("/league/L1"):
            return httpx.Response(200, json=LEAGUE_RAW)
        if url.endswith("/draft/D1"):
            return httpx.Response(200, json=DRAFT_META)
        raise AssertionError(f"unexpected URL: {url}")

    with pytest.raises(connect.ConnectError, match="Username not found"):
        connect.resolve(_client(handler), "L1", "ghost")


def test_resolve_raises_when_the_user_has_no_roster_in_this_league():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/league/L1/rosters"):
            return httpx.Response(200, json=[{"roster_id": 9, "owner_id": "someone-else"}])
        if url.endswith("/league/L1/drafts"):
            return httpx.Response(200, json=DRAFTS_RAW)
        if url.endswith("/league/L1"):
            return httpx.Response(200, json=LEAGUE_RAW)
        if url.endswith("/draft/D1"):
            return httpx.Response(200, json=DRAFT_META)
        if url.endswith("/user/tester"):
            return httpx.Response(200, json=USER_RAW)
        raise AssertionError(f"unexpected URL: {url}")

    with pytest.raises(connect.ConnectError, match="not a member"):
        connect.resolve(_client(handler), "L1", "tester")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_connect.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.ingest.connect'`.

- [ ] **Step 3: Implement**

Create `src/ffdo/ingest/connect.py`:

```python
"""Resolves a Sleeper league ID + username into a connected Session.

Orchestrates the handful of Sleeper calls needed to go from "league ID and
username" to a fully-identified league/draft/roster -- the one-time lookup
that runs when the main screen's connect form is submitted, not on every
board poll.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone

import httpx

from ffdo.domain.models import Session
from ffdo.ingest import draft as draft_mod
from ffdo.ingest import league as league_mod
from ffdo.ingest import user as user_mod
from ffdo.ingest.client import V1, SleeperClient


class ConnectError(Exception):
    """A user-facing reason `resolve()` could not connect a league."""


def resolve(
    sleeper: SleeperClient,
    league_id: str,
    username: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> Session:
    now = now or (lambda: datetime.now(timezone.utc))

    try:
        league_raw = sleeper.get_json(f"{V1}/league/{league_id}")
    except httpx.HTTPStatusError as exc:
        raise ConnectError("League not found") from exc
    league = league_mod.parse(league_raw)

    drafts_raw = sleeper.get_json(f"{V1}/league/{league_id}/drafts")
    draft_id = league_mod.most_recent_draft_id(drafts_raw)
    if draft_id is None:
        raise ConnectError("No draft found for this league")

    draft_meta = sleeper.get_json(f"{V1}/draft/{draft_id}")
    state = draft_mod.parse(draft_meta, [])

    # Some leagues carry the auction budget on the draft object rather than
    # the league's own settings -- same fallback ffdo.api.app.get_board()
    # already applies, kept consistent here so a connected Session's budget
    # is never spuriously None for a league this app already supports.
    if league.budget is None:
        league = replace(league, budget=state.budget)

    try:
        user_raw = sleeper.get_json(f"{V1}/user/{username}")
    except httpx.HTTPStatusError as exc:
        raise ConnectError("Username not found") from exc
    user_id, _display_name = user_mod.parse(user_raw)

    rosters_raw = sleeper.get_json(f"{V1}/league/{league_id}/rosters")
    roster_id = league_mod.find_roster_id(rosters_raw, user_id)
    if roster_id is None:
        raise ConnectError("This user is not a member of that league")

    return Session(
        username=username,
        user_id=user_id,
        league_id=league.league_id,
        draft_id=draft_id,
        roster_id=roster_id,
        league_name=league.name,
        season=league.season,
        num_teams=league.num_teams,
        budget=league.budget,
        roster_positions=league.roster_positions,
        scoring_settings=league.scoring_settings,
        draft_type=state.draft_type,
        draft_status=state.status,
        connected_at=now().isoformat(),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/test_connect.py -v`
Expected: PASS — all 6 tests.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/connect.py tests/ingest/test_connect.py
git commit -m "feat: add ffdo.ingest.connect.resolve to connect a league ID + username"
```

---

## Task 6: `ffdo.api.session.SessionStore` — file-backed persistence

**Files:**
- Create: `src/ffdo/api/session.py`
- Test: `tests/api/test_session.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `Session` (Task 2).
- Produces: `SessionStore(path: Path)` with `.get() -> Session | None`, `.load() -> Session | None`, `.save(session: Session) -> None`, `.clear() -> None`. Used by Task 7.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_session.py`:

```python
from ffdo.api.session import SessionStore
from ffdo.domain.models import Session


def _session(**overrides):
    base = dict(
        username="tester", user_id="U1", league_id="L1", draft_id="D1",
        roster_id=3, league_name="Test League", season=2026, num_teams=12,
        budget=200,
        roster_positions=("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                          "BN", "BN", "BN", "BN", "BN"),
        scoring_settings={"rec": 0.5}, draft_type="auction",
        draft_status="pre_draft", connected_at="2026-08-22T00:00:00+00:00",
    )
    return Session(**{**base, **overrides})


def test_get_returns_none_when_no_file_exists(tmp_path):
    store = SessionStore(tmp_path / "session.json")
    assert store.get() is None


def test_save_then_get_round_trips_the_session(tmp_path):
    store = SessionStore(tmp_path / "session.json")
    session = _session()
    store.save(session)
    assert store.get() == session


def test_a_fresh_store_reads_what_a_prior_store_saved(tmp_path):
    """Simulates a process restart: a new SessionStore pointed at the same
    path must recover the previously-connected session from disk."""
    path = tmp_path / "session.json"
    SessionStore(path).save(_session())

    loaded = SessionStore(path).get()
    assert loaded == _session()


def test_save_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "session.json"
    SessionStore(path).save(_session())
    assert path.exists()


def test_load_returns_none_for_malformed_json(tmp_path):
    path = tmp_path / "session.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert SessionStore(path).get() is None


def test_clear_deletes_the_file_and_resets_the_cache(tmp_path):
    path = tmp_path / "session.json"
    store = SessionStore(path)
    store.save(_session())
    store.clear()
    assert store.get() is None
    assert not path.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.api.session'`.

- [ ] **Step 3: Implement**

Create `src/ffdo/api/session.py`:

```python
"""Persists the connected league/user/draft so it survives a process restart.

A single JSON file plus an in-memory cache -- this app is a single local
process for one user's draft day, so there is no multi-session or
concurrency concern to design for.
"""

from __future__ import annotations

import json
from pathlib import Path

from ffdo.domain.models import Session

_UNSET = object()


class SessionStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._cached: Session | None | object = _UNSET

    def get(self) -> Session | None:
        if self._cached is _UNSET:
            self._cached = self.load()
        return self._cached

    def load(self) -> Session | None:
        if not self._path.exists():
            return None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        try:
            return Session(
                username=raw["username"],
                user_id=raw["user_id"],
                league_id=raw["league_id"],
                draft_id=raw["draft_id"],
                roster_id=raw["roster_id"],
                league_name=raw["league_name"],
                season=raw["season"],
                num_teams=raw["num_teams"],
                budget=raw["budget"],
                roster_positions=tuple(raw["roster_positions"]),
                scoring_settings=raw["scoring_settings"],
                draft_type=raw["draft_type"],
                draft_status=raw["draft_status"],
                connected_at=raw["connected_at"],
            )
        except (KeyError, TypeError):
            return None

    def save(self, session: Session) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "username": session.username,
            "user_id": session.user_id,
            "league_id": session.league_id,
            "draft_id": session.draft_id,
            "roster_id": session.roster_id,
            "league_name": session.league_name,
            "season": session.season,
            "num_teams": session.num_teams,
            "budget": session.budget,
            "roster_positions": list(session.roster_positions),
            "scoring_settings": dict(session.scoring_settings),
            "draft_type": session.draft_type,
            "draft_status": session.draft_status,
            "connected_at": session.connected_at,
        }
        self._path.write_text(json.dumps(payload), encoding="utf-8")
        self._cached = session

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()
        self._cached = None
```

Then add a new line to `.gitignore` (after the existing `*.sqlite` line):
```
data/session.json
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_session.py -v`
Expected: PASS — all 6 tests.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/api/session.py tests/api/test_session.py .gitignore
git commit -m "feat: add SessionStore to persist the connected league across restarts"
```

---

## Task 7: Wire `SessionStore` into `app.py`; add `/api/connect`, `/api/session`, `/api/readiness`

This is the integration task — it's the largest, and depends on Tasks 2, 3, 5, 6 all being merged first.

**Files:**
- Modify: `src/ffdo/api/app.py`
- Test: `tests/api/test_app.py`

**Interfaces:**
- Consumes: `connect.resolve`/`connect.ConnectError` (Task 5), `SessionStore` (Task 6), `Session` (Task 2).
- Produces:
  - `_TTLCache.has_value() -> bool`.
  - `_league_id()`/`_draft_id()`/`_roster_id()` now check `_SESSION_STORE.get()` first.
  - `POST /api/connect`, `GET /api/session`, `GET /api/readiness`.
  - Module-level `_SESSION_STORE: SessionStore` — tests override it via `monkeypatch.setattr(app_mod, "_SESSION_STORE", SessionStore(tmp_path / "session.json"))`.
  - Used by Task 8 (static mount split touches the same file) and Task 9 (frontend calls these three endpoints).

- [ ] **Step 1: Write the failing tests**

At the top of `tests/api/test_app.py`, change the imports from:
```python
from ffdo.api.app import (
    _DEFAULT_DRAFT_ID, _DEFAULT_LEAGUE_ID, _TTLCache, _active_only,
    _draft_id, _league_id, _roster_id,
)
from ffdo.domain.models import PlayerProfile
```
to:
```python
from ffdo.api import app as app_mod
from ffdo.api.app import (
    _DEFAULT_DRAFT_ID, _DEFAULT_LEAGUE_ID, _TTLCache, _active_only,
    _draft_id, _league_id, _roster_id, create_app,
)
from ffdo.api.session import SessionStore
from ffdo.domain.models import PlayerProfile, Session
from fastapi.testclient import TestClient
```

Add these helpers and tests anywhere after the imports (e.g. right before `def test_ttlcache_serves_cached_value_within_ttl_without_refetching`):

```python
def _session(**overrides):
    base = dict(
        username="tester", user_id="U1", league_id="session-league",
        draft_id="session-draft", roster_id=5, league_name="Test League",
        season=2026, num_teams=12, budget=200,
        roster_positions=("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                          "BN", "BN", "BN", "BN", "BN"),
        scoring_settings={"rec": 0.5}, draft_type="auction",
        draft_status="pre_draft", connected_at="2026-08-22T00:00:00+00:00",
    )
    return Session(**{**base, **overrides})


class _FakeSleeperClient:
    """Stands in for ffdo.ingest.client.SleeperClient so tests never make a
    real network call. Returns an empty list for any projections URL (that
    parser requires a list) and an empty dict otherwise."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_json(self, url: str):
        return [] if "/projections/" in url else {}

    def close(self) -> None:
        pass


def test_has_value_is_false_before_the_first_load():
    cache = _TTLCache(ttl_seconds=10)
    assert cache.has_value() is False


def test_has_value_is_true_after_a_load_and_does_not_trigger_a_fetch():
    cache = _TTLCache(ttl_seconds=10)
    calls = []
    cache.get(lambda: calls.append(1) or "value")
    assert cache.has_value() is True
    assert cache.has_value() is True
    assert calls == [1], "has_value() must not call the loader"


def test_league_id_prefers_the_connected_session_over_env_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("FFDO_LEAGUE_ID", "env-league")
    store = SessionStore(tmp_path / "session.json")
    store.save(_session())
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    assert _league_id() == "session-league"


def test_draft_id_prefers_the_connected_session_over_env_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("FFDO_DRAFT_ID", "env-draft")
    store = SessionStore(tmp_path / "session.json")
    store.save(_session())
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    assert _draft_id() == "session-draft"


def test_roster_id_prefers_the_connected_session_over_env_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("FFDO_ROSTER_ID", "999")
    store = SessionStore(tmp_path / "session.json")
    store.save(_session())
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    assert _roster_id() == 5


def test_ids_fall_back_to_env_vars_when_no_session_is_connected(monkeypatch, tmp_path):
    monkeypatch.delenv("FFDO_LEAGUE_ID", raising=False)
    monkeypatch.setattr(app_mod, "_SESSION_STORE", SessionStore(tmp_path / "session.json"))

    assert _league_id() == _DEFAULT_LEAGUE_ID


def test_connect_endpoint_returns_400_for_a_connect_error(monkeypatch, tmp_path):
    from ffdo.ingest import connect as connect_mod

    monkeypatch.setattr(app_mod, "_SESSION_STORE", SessionStore(tmp_path / "session.json"))

    def raise_connect_error(sleeper, league_id, username):
        raise connect_mod.ConnectError("League not found")

    monkeypatch.setattr("ffdo.ingest.connect.resolve", raise_connect_error)

    client = TestClient(create_app())
    res = client.post("/api/connect", json={"league_id": "bad", "username": "tester"})

    assert res.status_code == 400
    assert res.json()["detail"] == "League not found"


def test_connect_endpoint_rejects_a_blank_league_id_or_username(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "_SESSION_STORE", SessionStore(tmp_path / "session.json"))
    client = TestClient(create_app())

    res = client.post("/api/connect", json={"league_id": "  ", "username": "tester"})

    assert res.status_code == 400


def test_connect_endpoint_saves_the_session_and_returns_it(monkeypatch, tmp_path):
    store = SessionStore(tmp_path / "session.json")
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    fake_session = _session(league_id="L9")
    monkeypatch.setattr("ffdo.ingest.connect.resolve",
                        lambda sleeper, league_id, username: fake_session)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/connect", json={"league_id": "L9", "username": "tester"})

    assert res.status_code == 200
    assert res.json()["league_id"] == "L9"
    assert store.get() == fake_session


def test_session_endpoint_returns_null_when_nothing_is_connected(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "_SESSION_STORE", SessionStore(tmp_path / "session.json"))
    client = TestClient(create_app())

    res = client.get("/api/session")
    assert res.json() is None


def test_session_endpoint_returns_the_connected_session(monkeypatch, tmp_path):
    store = SessionStore(tmp_path / "session.json")
    store.save(_session())
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)
    client = TestClient(create_app())

    res = client.get("/api/session")
    assert res.json()["league_id"] == "session-league"


def test_readiness_endpoint_reports_pending_before_anything_is_connected(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "_SESSION_STORE", SessionStore(tmp_path / "session.json"))
    client = TestClient(create_app())

    res = client.get("/api/readiness")
    assert res.json() == {"league_draft": "pending", "players": "pending", "projections": "pending"}


def test_readiness_endpoint_reports_synced_after_connecting(monkeypatch, tmp_path):
    store = SessionStore(tmp_path / "session.json")
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)
    monkeypatch.setattr("ffdo.ingest.connect.resolve",
                        lambda sleeper, league_id, username: _session())
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    client.post("/api/connect", json={"league_id": "L1", "username": "tester"})

    res = client.get("/api/readiness")
    body = res.json()
    assert body == {"league_draft": "synced", "players": "synced", "projections": "synced"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_app.py -v`
Expected: FAIL — `AttributeError: module 'ffdo.api.app' has no attribute '_SESSION_STORE'` and `_TTLCache` has no `has_value`.

- [ ] **Step 3: Implement**

Replace `src/ffdo/api/app.py` in full with:

```python
"""FastAPI app. Serves board state and the static board."""

from __future__ import annotations

import os
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from ffdo.api.session import SessionStore

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Defaults pin this user's real 2026 auction league/draft, so the app works
# out of the box with zero config. `FFDO_LEAGUE_ID` / `FFDO_DRAFT_ID` let it
# point at a different league (e.g. a snake league) without a settings UI --
# read fresh on every request rather than frozen as module constants at
# import time, so an env var set after the process starts (or changed by a
# test via monkeypatch) actually takes effect. A connected `Session` (see
# `_SESSION_STORE` below) takes precedence over both when one exists -- these
# env vars are the zero-config fallback for when the main screen's connect
# flow has never been used.
_DEFAULT_LEAGUE_ID = "1315881559957458944"
_DEFAULT_DRAFT_ID = "1315881559965835264"

# Module-level, not created inside `create_app()`, because `_league_id()` /
# `_draft_id()` / `_roster_id()` are free functions with no app instance in
# hand -- called both from `get_board()` and directly from tests. Tests that
# need an isolated store monkeypatch this attribute rather than constructing
# their own `create_app()` wiring:
#   monkeypatch.setattr(app_mod, "_SESSION_STORE", SessionStore(tmp_path / "session.json"))
_SESSION_STORE = SessionStore(Path("data") / "session.json")


def _league_id() -> str:
    session = _SESSION_STORE.get()
    if session is not None:
        return session.league_id
    return os.environ.get("FFDO_LEAGUE_ID", _DEFAULT_LEAGUE_ID)


def _draft_id() -> str:
    session = _SESSION_STORE.get()
    if session is not None:
        return session.draft_id
    return os.environ.get("FFDO_DRAFT_ID", _DEFAULT_DRAFT_ID)


def _roster_id() -> int | None:
    session = _SESSION_STORE.get()
    if session is not None:
        return session.roster_id
    raw = os.environ.get("FFDO_ROSTER_ID")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _active_only(points: dict[str, float], profiles: dict) -> dict[str, float]:
    """Drop retired/inactive players from the valuation pool.

    `PlayerProfile.active` is parsed but was never used as a filter, so a
    retired player with a stale projection (e.g. Cam Newton) could still
    slip onto the board with a deeply negative VOR instead of not
    appearing at all.
    """
    return {pid: pts for pid, pts in points.items() if profiles[pid].active}


class _TTLCache:
    """Caches the result of `loader` in-process for `ttl_seconds`.

    The board endpoint is polled every 3s by the browser; the players feed
    alone is ~14MB and rarely changes, so re-fetching it on every poll is
    not viable. Projections change rarely during a draft window either.
    Draft state is intentionally NOT cached here -- it must reflect live
    picks on every poll.
    """

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._value: Any = None
        self._fetched_at: float = float("-inf")
        # Injectable so tests can fake elapsed time without real sleeps.
        self._now: Callable[[], float] = time.monotonic

    def get(self, loader: Callable[[], Any]) -> Any:
        now = self._now()
        if self._value is None or (now - self._fetched_at) > self._ttl:
            self._value = loader()
            self._fetched_at = now
        return self._value

    def has_value(self) -> bool:
        """True if a value is already cached -- never triggers a fetch."""
        return self._value is not None


def create_app() -> FastAPI:
    app = FastAPI(title="ffdo")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    from ffdo.api import board as board_mod
    from ffdo.engine import auction, scoring, vor
    from ffdo.ingest import client as client_mod
    from ffdo.ingest import connect as connect_mod
    from ffdo.ingest import draft as draft_mod
    from ffdo.ingest import league as league_mod
    from ffdo.ingest import players as players_mod
    from ffdo.ingest import projections as proj_mod

    players_cache = _TTLCache(ttl_seconds=24 * 3600)
    projections_cache = _TTLCache(ttl_seconds=3600)

    def _load_players(sleeper: client_mod.SleeperClient) -> dict:
        return players_mod.parse(sleeper.get_json(f"{client_mod.V1}/players/nfl"))

    def _load_projections(sleeper: client_mod.SleeperClient, season: int):
        return proj_mod.parse(
            sleeper.get_json(
                f"{client_mod.PROJECTIONS}/{season}"
                "?season_type=regular&position[]=QB&position[]=RB"
                "&position[]=WR&position[]=TE"),
            season)

    def _warm_caches(season: int) -> None:
        """Pre-populates the players/projections TTL caches in the
        background after a successful /api/connect, so the draft room's
        first load doesn't pay for both fetches synchronously."""
        sleeper = client_mod.SleeperClient()
        try:
            players_cache.get(lambda: _load_players(sleeper))
            projections_cache.get(lambda: _load_projections(sleeper, season))
        finally:
            sleeper.close()

    @app.post("/api/connect")
    def connect_league(payload: dict, background_tasks: BackgroundTasks) -> dict:
        league_id = str(payload.get("league_id", "")).strip()
        username = str(payload.get("username", "")).strip()
        if not league_id or not username:
            raise HTTPException(
                status_code=400, detail="League ID and username are required")

        sleeper = client_mod.SleeperClient()
        try:
            session = connect_mod.resolve(sleeper, league_id, username)
        except connect_mod.ConnectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            sleeper.close()

        _SESSION_STORE.save(session)
        background_tasks.add_task(_warm_caches, session.season)
        return asdict(session)

    @app.get("/api/session")
    def get_session() -> dict | None:
        session = _SESSION_STORE.get()
        return asdict(session) if session is not None else None

    @app.get("/api/readiness")
    def get_readiness() -> dict:
        session = _SESSION_STORE.get()
        return {
            "league_draft": "synced" if session is not None else "pending",
            "players": "synced" if players_cache.has_value() else "pending",
            "projections": "synced" if projections_cache.has_value() else "pending",
        }

    @app.get("/api/board")
    def get_board() -> dict:
        league_id = _league_id()
        draft_id = _draft_id()
        sleeper = client_mod.SleeperClient()
        try:
            lg = league_mod.parse(
                sleeper.get_json(f"{client_mod.V1}/league/{league_id}"))
            profiles = players_cache.get(lambda: _load_players(sleeper))
            proj, adp_data = projections_cache.get(
                lambda: _load_projections(sleeper, lg.season))
            state = draft_mod.parse(
                sleeper.get_json(f"{client_mod.V1}/draft/{draft_id}"),
                sleeper.get_json(f"{client_mod.V1}/draft/{draft_id}/picks"))
        finally:
            sleeper.close()

        # Sleeper's /league/<id> settings carry no auction budget field for
        # this league -- the budget lives on the draft object instead (see
        # ffdo.ingest.draft.parse). Fall back to the draft's budget so the
        # engine's league.num_teams * league.budget math never hits a
        # league.budget of None.
        if lg.budget is None:
            lg = replace(lg, budget=state.budget)

        # Sleeper's projections endpoint does not actually honor the
        # position[] query filter server-side (confirmed against the live
        # API) -- it returns every position it has projections for,
        # including FB/CB/K/DEF, none of which this league rosters.
        # `vor.compute` now structurally excludes any position without a
        # replacement level derived from `league.roster_positions` (see
        # ffdo.engine.vor), so no position allowlist is needed here; scoring
        # a few extra positions that get excluded downstream is cheap.
        points = {pid: scoring.score_stats(p.stats, lg.scoring_settings)
                  for pid, p in proj.items() if pid in profiles}
        points = _active_only(points, profiles)
        valued = vor.assign_tiers(vor.compute(points, profiles, lg))

        if state.draft_type == "auction":
            baseline = auction.baseline_prices(valued, lg)
            return board_mod.build_auction_board(
                lg, state, valued, baseline, roster_id=_roster_id())

        from ffdo.engine import market
        available = {pid for pid in valued if pid not in state.drafted_player_ids()}
        adp_means = {pid: a.adp["half_ppr"] for pid, a in adp_data.items()
                     if a.adp.get("half_ppr", 999) < 999}
        picks_until = lg.num_teams  # conservative: one full round
        survival = market.simulate_survival(adp_means, available, picks_until)
        cow = market.cost_of_waiting(valued, survival, available)
        return board_mod.build_snake_board(lg, state, valued, survival, cow)

    # Static mounts MUST be registered last: StaticFiles("/") matches any
    # path under it, so routes declared after this point would be shadowed.
    # `/board` is mounted before `/` so the board's own files aren't
    # shadowed by the root mount matching first.
    board_dir = WEB_DIR / "board"
    if board_dir.exists():
        app.mount("/board", StaticFiles(directory=board_dir, html=True), name="board")
    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_app.py -v`
Expected: PASS — all tests, including the pre-existing ones.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/api/app.py tests/api/test_app.py
git commit -m "feat: wire SessionStore into app.py; add /api/connect, /api/session, /api/readiness"
```

---

## Task 8: Split static mounts — move the board under `web/board/`

**Files:**
- Move: `src/ffdo/web/index.html` → `src/ffdo/web/board/index.html`
- Move: `src/ffdo/web/board.js` → `src/ffdo/web/board/board.js`
- Move: `src/ffdo/web/board.css` → `src/ffdo/web/board/board.css`

No test file — this is a pure file-layout change with no new logic (the `/board` mount registration was already added and tested in Task 7). Verified manually via `run` in Task 9, once the main screen can link to it.

**Interfaces:**
- Consumes: `app.py`'s `/board` mount (Task 7, already in place — this task only makes `board_dir` exist).
- Produces: the board reachable at `/board/`, `/board/board.js`, `/board/board.css`. Consumed by Task 9's "Enter draft room" link.

- [ ] **Step 1: Move the files**

```bash
mkdir -p src/ffdo/web/board
git mv src/ffdo/web/index.html src/ffdo/web/board/index.html
git mv src/ffdo/web/board.js src/ffdo/web/board/board.js
git mv src/ffdo/web/board.css src/ffdo/web/board/board.css
```

- [ ] **Step 2: Fix the moved index.html's asset paths from absolute to relative**

In `src/ffdo/web/board/index.html`, the file is now served under the `/board` mount, so `board.css`/`board.js` live at `/board/board.css` and `/board/board.js`, not `/board.css`/`/board.js`. Change:
```html
<link rel="stylesheet" href="/board.css">
```
to:
```html
<link rel="stylesheet" href="board.css">
```
and:
```html
<script src="/board.js"></script>
```
to:
```html
<script src="board.js"></script>
```
Leave `fetch("/api/board")` in `board.js` unchanged — `/api/board` is a top-level API route, unaffected by the static mount move.

- [ ] **Step 3: Run the full test suite to confirm no regressions**

Run: `uv run pytest -v`
Expected: PASS — `board_dir.exists()` is now true, so the `/board` mount registers; no test currently asserts on the mount's contents directly (Task 7's tests exercise the `/api/*` routes, not the static files).

- [ ] **Step 4: Commit**

```bash
git add src/ffdo/web
git commit -m "refactor: move the draft board under web/board/, served at /board"
```

---

## Task 9: Main screen — `web/index.html`, `web/main.js`, `web/main.css`

**Files:**
- Create: `src/ffdo/web/index.html`
- Create: `src/ffdo/web/main.js`
- Create: `src/ffdo/web/main.css`

No automated test — no frontend test framework exists in this repo (`board.js` has none either). Verified manually in-browser per Step 4.

**Interfaces:**
- Consumes: `GET /api/session`, `POST /api/connect`, `GET /api/readiness` (Task 7); `/board` (Task 8).
- Produces: the main screen at `/`. Terminal — nothing downstream consumes this task.

- [ ] **Step 1: Create `src/ffdo/web/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FFDO — Connect</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="main.css">
</head>
<body>
<div id="shell">
  <header id="brand-row">
    <div class="brand">
      <span class="brand-name">FFDO</span>
      <span class="brand-tag">/ DRAFT ROOM</span>
    </div>
    <p class="tagline">Draft-day decision support &mdash; values players, tracks the room, never names a pick.</p>
  </header>

  <section id="connect-form" class="card">
    <h1>Connect your league</h1>
    <label class="field">
      <span>Sleeper League ID</span>
      <input id="league-id-input" type="text" placeholder="e.g. 1315881559957458944" autocomplete="off">
    </label>
    <label class="field">
      <span>Sleeper Username</span>
      <input id="username-input" type="text" placeholder="e.g. yourusername" autocomplete="off">
    </label>
    <p id="connect-error" class="error-msg" hidden></p>
    <button id="connect-btn">Connect</button>
  </section>

  <section id="connected-view" hidden>
    <div class="cards-row">
      <div class="card league-card">
        <div class="card-head">
          <div class="league-identity">
            <span class="eyebrow">League</span>
            <h2 id="league-name">&mdash;</h2>
            <span class="league-id-tag" id="league-id-tag"></span>
          </div>
          <span id="status-badge" class="status-badge"></span>
        </div>
        <div class="stat-grid">
          <div class="stat"><span class="eyebrow">Teams</span><b id="stat-teams">&mdash;</b></div>
          <div class="stat"><span class="eyebrow">Budget</span><b id="stat-budget">&mdash;</b></div>
          <div class="stat"><span class="eyebrow">Rounds</span><b id="stat-rounds">&mdash;</b></div>
          <div class="stat"><span class="eyebrow">Format</span><b id="stat-format" class="accent">&mdash;</b></div>
        </div>
        <div class="hr"></div>
        <div class="roster-slots">
          <span class="eyebrow">Roster</span>
          <div id="roster-chips" class="chip-row"></div>
        </div>
        <div class="hr"></div>
        <p id="scoring-note" class="note"></p>
      </div>

      <div class="card readiness-card">
        <span class="eyebrow">Data readiness</span>
        <div id="readiness-rows"></div>
      </div>
    </div>

    <div class="bottom-bar card">
      <div class="format-block">
        <div class="format-toggle" id="format-toggle">
          <button data-format="auction">Auction</button>
          <button data-format="snake">Snake</button>
        </div>
        <p id="format-note" class="note"></p>
      </div>
      <button id="enter-btn">Enter draft room &rarr;</button>
    </div>
  </section>
</div>
<script src="main.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `src/ffdo/web/main.css`**

```css
:root {
  color-scheme: dark;
  --bg: oklch(0.15 0.014 258);
  --surface: oklch(0.205 0.014 258);
  --surface-2: oklch(0.24 0.014 258);
  --border: oklch(0.32 0.014 258);
  --border-strong: oklch(0.40 0.016 258);
  --text: oklch(0.94 0.006 258);
  --muted: oklch(0.66 0.014 258);
  --faint: oklch(0.48 0.014 258);
  --accent: #22D3EE;
  --green: #34D399;
  --red: #F87171;
  --amber: #FBBF24;
  --font-sans: 'IBM Plex Sans', -apple-system, 'Segoe UI', sans-serif;
  --font-mono: 'IBM Plex Mono', 'SFMono-Regular', Consolas, monospace;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  padding: 28px;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.4 var(--font-sans);
  min-height: 100vh;
}

#shell { max-width: 960px; margin: 0 auto; display: flex; flex-direction: column; gap: 22px; }

#brand-row { display: flex; flex-direction: column; gap: 6px; }
.brand { display: flex; align-items: baseline; gap: 10px; }
.brand-name { font-family: var(--font-mono); font-weight: 600; font-size: 20px; letter-spacing: 2px; }
.brand-tag { font-family: var(--font-mono); font-size: 12px; color: var(--accent); letter-spacing: 1px; }
.tagline { margin: 0; font-size: 13px; color: var(--muted); }

.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 26px 28px;
}

.eyebrow {
  display: block;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 1.5px;
  color: var(--faint);
  font-weight: 600;
}

/* ---- connect form ---- */
#connect-form { display: flex; flex-direction: column; gap: 16px; max-width: 420px; }
#connect-form h1 { margin: 0; font-size: 20px; }
.field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--muted); }
.field input {
  font: 14px var(--font-mono);
  color: var(--text);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 7px;
  padding: 10px 12px;
}
.field input:focus { outline: none; border-color: var(--accent); }
.error-msg { margin: 0; color: var(--red); font-size: 13px; }

button {
  font-family: var(--font-sans);
  cursor: pointer;
}
#connect-btn {
  align-self: flex-start;
  font-size: 14px;
  font-weight: 600;
  color: #0a0f14;
  background: var(--accent);
  border: none;
  border-radius: 8px;
  padding: 12px 22px;
}
#connect-btn:disabled { opacity: 0.6; cursor: default; }

/* ---- connected view ---- */
.cards-row { display: flex; gap: 20px; }
.league-card { flex: 1.4; display: flex; flex-direction: column; gap: 18px; }
.readiness-card { flex: 1; display: flex; flex-direction: column; gap: 14px; }

.card-head { display: flex; align-items: flex-start; justify-content: space-between; }
.league-identity { display: flex; flex-direction: column; gap: 4px; }
.league-identity h2 { margin: 0; font-size: 24px; }
.league-id-tag { font-family: var(--font-mono); font-size: 12px; color: var(--faint); }

.status-badge {
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 1px;
  color: var(--amber);
  background: color-mix(in oklch, var(--amber) 14%, transparent);
  border: 1px solid color-mix(in oklch, var(--amber) 40%, transparent);
  padding: 6px 10px;
  border-radius: 6px;
  white-space: nowrap;
}

.stat-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
.stat { display: flex; flex-direction: column; gap: 4px; }
.stat b { font-family: var(--font-mono); font-size: 18px; font-weight: 600; }
.accent { color: var(--accent); }

.hr { height: 1px; background: var(--border); }

.roster-slots { display: flex; flex-direction: column; gap: 8px; }
.chip-row { display: flex; flex-wrap: wrap; gap: 8px; }
.chip {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.5px;
  padding: 6px 10px;
  border-radius: 6px;
}
.chip-starter {
  background: color-mix(in oklch, var(--accent) 16%, transparent);
  color: var(--accent);
  border: 1px solid color-mix(in oklch, var(--accent) 40%, transparent);
}
.chip-bench { background: var(--surface-2); color: var(--faint); border: 1px solid var(--border); }

.note { margin: 0; font-size: 12.5px; color: var(--muted); line-height: 1.5; }

.readiness-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 2px;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.readiness-row:last-child { border-bottom: none; }
.readiness-label { flex: 1; }
.readiness-status { font-family: var(--font-mono); font-size: 12px; color: var(--muted); }
.dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.dot.synced { background: var(--green); box-shadow: 0 0 0 3px color-mix(in oklch, var(--green) 20%, transparent); }
.dot.pending { background: var(--amber); box-shadow: 0 0 0 3px color-mix(in oklch, var(--amber) 20%, transparent); }

.bottom-bar { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.format-block { display: flex; align-items: center; gap: 20px; }
.format-toggle {
  display: flex;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 3px;
  gap: 2px;
}
.format-toggle button {
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: 0.5px;
  padding: 8px 16px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
}
.format-toggle button.on { background: var(--accent); color: #0a0f14; }

#enter-btn {
  font-size: 14.5px;
  font-weight: 600;
  color: #0a0f14;
  background: var(--accent);
  border: none;
  border-radius: 8px;
  padding: 13px 22px;
}
```

- [ ] **Step 3: Create `src/ffdo/web/main.js`**

```js
let state = {
  session: null,
  format: null,
  connecting: false,
  readinessTimer: null,
};

async function fetchSession() {
  const res = await fetch("/api/session");
  const session = await res.json();
  if (session) {
    state.session = session;
    state.format = session.draft_type;
    showConnected();
    pollReadiness();
  }
}

function showConnected() {
  document.getElementById("connect-form").hidden = true;
  document.getElementById("connected-view").hidden = false;
  renderConnected();
}

function renderConnected() {
  const s = state.session;
  if (!s) return;

  document.getElementById("league-name").textContent = s.league_name || "(unnamed league)";
  document.getElementById("league-id-tag").textContent = `league_id ${s.league_id}`;
  document.getElementById("status-badge").textContent = (s.draft_status || "").toUpperCase();
  document.getElementById("stat-teams").textContent = s.num_teams;
  document.getElementById("stat-budget").textContent = s.budget !== null ? `$${s.budget}` : "—";
  document.getElementById("stat-rounds").textContent = s.roster_positions ? s.roster_positions.length : "—";
  document.getElementById("stat-format").textContent = s.draft_type === "auction" ? "Auction" : "Snake";

  const positions = s.roster_positions || [];
  const starters = positions.filter(p => p !== "BN");
  const bench = positions.filter(p => p === "BN");
  document.getElementById("roster-chips").innerHTML =
    starters.map(p => `<span class="chip chip-starter">${p}</span>`).join("") +
    bench.map(p => `<span class="chip chip-bench">${p}</span>`).join("");

  const scoringCount = s.scoring_settings ? Object.keys(s.scoring_settings).length : 0;
  document.getElementById("scoring-note").textContent = `${scoringCount} scoring keys synced`;

  renderFormatToggle();
}

function renderFormatToggle() {
  document.querySelectorAll("#format-toggle button").forEach(btn => {
    btn.classList.toggle("on", btn.dataset.format === state.format);
  });
  const notes = {
    auction: "This league's draft is scored as an auction — the board leads with budget, live inflation, and your max bid.",
    snake: "This league's draft is a snake draft — the board leads with Cost of Waiting instead of dollars.",
  };
  document.getElementById("format-note").textContent = notes[state.format] || "";
}

document.querySelectorAll("#format-toggle button").forEach(btn =>
  btn.addEventListener("click", () => {
    state.format = btn.dataset.format;
    renderFormatToggle();
  }));

document.getElementById("enter-btn").addEventListener("click", () => {
  window.location.href = "/board";
});

async function pollReadiness() {
  const res = await fetch("/api/readiness");
  const r = await res.json();
  renderReadiness(r);

  if (state.readinessTimer) clearTimeout(state.readinessTimer);
  const allSynced = r.league_draft === "synced" && r.players === "synced" && r.projections === "synced";
  if (!allSynced) {
    state.readinessTimer = setTimeout(pollReadiness, 1500);
  }
}

function renderReadiness(r) {
  const rows = [
    { label: "League + draft settings", status: r.league_draft },
    { label: "Players", status: r.players },
    { label: "Projections & ADP", status: r.projections },
  ];
  document.getElementById("readiness-rows").innerHTML = rows.map(row => `
    <div class="readiness-row">
      <span class="dot ${row.status}"></span>
      <span class="readiness-label">${row.label}</span>
      <span class="readiness-status">${row.status === "synced" ? "Synced" : "Syncing…"}</span>
    </div>`).join("");
}

async function connect() {
  const leagueId = document.getElementById("league-id-input").value.trim();
  const username = document.getElementById("username-input").value.trim();
  const errorEl = document.getElementById("connect-error");
  errorEl.hidden = true;

  if (!leagueId || !username) {
    errorEl.textContent = "League ID and username are both required.";
    errorEl.hidden = false;
    return;
  }
  if (state.connecting) return;

  state.connecting = true;
  const btn = document.getElementById("connect-btn");
  btn.disabled = true;
  btn.textContent = "Connecting…";

  try {
    const res = await fetch("/api/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ league_id: leagueId, username }),
    });
    const body = await res.json();
    if (!res.ok) {
      errorEl.textContent = body.detail || "Could not connect to that league.";
      errorEl.hidden = false;
      return;
    }
    state.session = body;
    state.format = body.draft_type;
    showConnected();
    pollReadiness();
  } catch (err) {
    errorEl.textContent = "Network error — check your connection and try again.";
    errorEl.hidden = false;
    console.error("connect failed", err);
  } finally {
    state.connecting = false;
    btn.disabled = false;
    btn.textContent = "Connect";
  }
}

document.getElementById("connect-btn").addEventListener("click", connect);

fetchSession();
```

- [ ] **Step 4: Manual verification in-browser**

Run: `uv run uvicorn ffdo.api.app:app --port 8000`

Then, using the `run` skill (or a browser directly):
1. Open `http://localhost:8000/` — confirm the connect form renders (not the board).
2. Submit an obviously-bad league ID/username — confirm the inline error message appears and the form stays filled in.
3. Submit a real Sleeper league ID and username (or a known-good test league) — confirm the connected view renders: league name/status/stats/roster chips, data readiness rows transitioning from "Syncing…" to "Synced".
4. Click the format toggle — confirm it switches the highlighted button and the note text.
5. Click "Enter draft room" — confirm it navigates to `/board` and the existing board loads and renders players (using `board.css`/`board.js` correctly at their new relative paths).
6. Reload `/` — confirm it goes straight to the connected view (via `GET /api/session`), not back to the form.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/web/index.html src/ffdo/web/main.js src/ffdo/web/main.css
git commit -m "feat: add the main/connect screen at /"
```
