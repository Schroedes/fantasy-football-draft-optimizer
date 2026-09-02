# Multi-League Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace FFDO's single connected-league model with a SQLite-backed store of N tracked leagues, discovered per provider, reachable through league-scoped API routes and a switcher-based frontend shell.

**Architecture:** A new `LeagueStore` (stdlib `sqlite3`, one file `data/ffdo.db`) holds tracked leagues and per-provider credentials, replacing `SessionStore`/`session.json` (migrated once on first open). The existing one-time resolution functions (`connect.resolve`, `espn.connect.resolve`) are renamed `track()` and return a `TrackedLeague`. `app.py` gains league-management endpoints and moves the three board endpoints under `/api/leagues/{league_key}/...`; the league key is a synthetic string `"{provider}:{provider_league_id}:{season}"` carried in the URL, so there is no server-side "active league" state. The frontend becomes a hash-routed shell (`#/connect`, `#/`, `#/league/{key}`) with a persistent league switcher; the draft board is unchanged behind league-scoped fetch URLs, and a post-draft league shows a season-mode placeholder.

**Tech Stack:** Python 3.12, FastAPI, `httpx` (with `MockTransport` in tests), stdlib `sqlite3`, pytest. Frontend is dependency-free vanilla JS/CSS served by `StaticFiles`.

**Spec:** `docs/superpowers/specs/2026-09-02-multi-league-foundation-design.md`

## Global Constraints

- **Python** `>=3.12` (`pyproject.toml`). No new runtime dependencies — `sqlite3` is stdlib.
- **Layer rule:** nothing above `ffdo/ingest/` sees provider JSON keys or credentials in raw form; adapters translate at that boundary. `ffdo/domain/models.py` has no I/O.
- **Frozen dataclasses:** domain types are `@dataclass(frozen=True, slots=True)`.
- **League key format:** `"{provider}:{provider_league_id}:{season}"` — provider is one of `sleeper`, `espn`, `sleeper-mock`; a mock uses its `draft_id` as the middle segment.
- **Credentials never echoed over HTTP:** `espn_s2` / `swid` are stripped from every API response (existing `_session_public_dict` rule).
- **Test isolation:** every `tests/api/` test runs against a `tmp_path` DB via the autouse fixture in `tests/api/conftest.py`; no test touches a real `data/` file or the network.
- **Commit after every task** with a `feat:` / `refactor:` / `test:` prefixed message.
- **Full suite green:** `uv run pytest` must pass at the end of every task.

---

## File Structure

**Created:**
- `src/ffdo/api/store.py` — `LeagueStore`: SQLite persistence for tracked leagues + provider credentials, plus the one-shot `session.json` migration.
- `src/ffdo/ingest/discover.py` — Sleeper league discovery (`list_leagues`).
- `src/ffdo/ingest/espn/discover.py` — ESPN league discovery (`list_leagues`) via the fan API.
- `src/ffdo/web/index.html`, `src/ffdo/web/app.js`, `src/ffdo/web/app.css` — the app shell (router + switcher + connect/discovery screen).
- `scripts/seed_dev_league.py` — tracks the old pinned 2026 auction league into a local `data/ffdo.db` for zero-config dev.
- `tests/api/test_store.py`, `tests/ingest/test_discover.py`, `tests/ingest/espn/test_discover.py` — new test modules.

**Modified:**
- `src/ffdo/domain/models.py` — remove `Session`; add `TrackedLeague`, `ProviderCredential`, `DiscoveredLeague`.
- `src/ffdo/ingest/league.py` — add `detect_format`.
- `src/ffdo/ingest/espn/league.py` — add `detect_format`.
- `src/ffdo/ingest/connect.py` — `resolve` → `track`, `resolve_mock` → `track_mock`; return `TrackedLeague`.
- `src/ffdo/ingest/espn/connect.py` — `resolve` → `track`; return `TrackedLeague`; caller stores credentials separately.
- `src/ffdo/api/app.py` — `_STORE`/`_load_league` replace `_SESSION_STORE`/`_league_id`/`_draft_id`/`_roster_id`; new league-management endpoints; board endpoints become league-scoped; env-var defaults removed.
- `src/ffdo/web/board/board.js` — fetch URLs become league-scoped; board mounts into the shell.
- `tests/api/conftest.py` — autouse fixture points `_STORE` at a `tmp_path` DB.
- `tests/api/test_app.py` — rewritten for the new endpoints and `_load_league`.
- `tests/ingest/test_connect.py`, `tests/ingest/espn/test_connect.py` — updated for `track()` returning `TrackedLeague`.
- `tests/ingest/test_league.py`, `tests/ingest/espn/test_league.py` — add `detect_format` cases.
- `.gitignore` — add `data/ffdo.db`.
- `README.md` — "connect a provider" replaces the env-var paragraph.

**Deleted:**
- `src/ffdo/api/session.py`, `tests/api/test_session.py`.
- `src/ffdo/web/main.js`, `src/ffdo/web/main.css`.

---

## Task 1: Domain types — `TrackedLeague`, `ProviderCredential`, `DiscoveredLeague`

**Files:**
- Modify: `src/ffdo/domain/models.py`
- Test: `tests/domain/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `TrackedLeague(league_key: str, provider: str, provider_league_id: str, season: int, name: str, user_id: str, roster_id: int | None, draft_id: str, draft_type: str, draft_status: str, num_teams: int, budget: int | None, rounds: int, roster_positions: tuple[str, ...], scoring_settings: Mapping[str, float], fmt: str, format_override: str | None, raw_settings: Mapping[str, Any], is_mock: bool, tracked_at: str, last_refreshed_at: str)`, frozen, slots. Property `resolved_format -> str` returns `self.format_override or self.fmt`. Property `starting_slots -> tuple[str, ...]` returns `tuple(p for p in self.roster_positions if p != "BN")`. Property `roster_size -> int` returns `len(self.roster_positions)`.
  - `ProviderCredential(provider: str, user_identifier: str, espn_s2: str | None, swid: str | None, updated_at: str)`, frozen, slots.
  - `DiscoveredLeague(provider: str, provider_league_id: str, season: int, name: str, num_teams: int, draft_type: str, fmt: str, draft_status: str, already_tracked: bool)`, frozen, slots.
  - Module helper `make_league_key(provider: str, provider_league_id: str, season: int) -> str` returning `f"{provider}:{provider_league_id}:{season}"`.

> Field name note: the dataclass field is `fmt` (not `format`) because `format` shadows a builtin and reads badly in f-strings; the SQLite column and JSON key are `format`. The mapping happens in `store.py` (Task 2) and the endpoint serializers (Task 10).

- [ ] **Step 1: Write the failing test**

Add to `tests/domain/test_models.py`:

```python
from ffdo.domain.models import (
    DiscoveredLeague, ProviderCredential, TrackedLeague, make_league_key,
)


def _tracked(**overrides):
    base = dict(
        league_key="sleeper:L1:2026", provider="sleeper", provider_league_id="L1",
        season=2026, name="Test League", user_id="U1", roster_id=3,
        draft_id="D1", draft_type="auction", draft_status="pre_draft",
        num_teams=12, budget=200, rounds=13,
        roster_positions=("QB", "RB", "RB", "WR", "BN"),
        scoring_settings={"rec": 0.5}, fmt="redraft", format_override=None,
        raw_settings={"type": 0}, is_mock=False,
        tracked_at="2026-09-02T00:00:00+00:00",
        last_refreshed_at="2026-09-02T00:00:00+00:00",
    )
    return TrackedLeague(**{**base, **overrides})


def test_make_league_key_joins_provider_id_and_season():
    assert make_league_key("espn", "1882997948", 2026) == "espn:1882997948:2026"


def test_resolved_format_prefers_the_override():
    assert _tracked(fmt="redraft", format_override="dynasty").resolved_format == "dynasty"
    assert _tracked(fmt="keeper", format_override=None).resolved_format == "keeper"


def test_tracked_league_slot_helpers():
    lg = _tracked(roster_positions=("QB", "RB", "FLEX", "BN", "BN"))
    assert lg.starting_slots == ("QB", "RB", "FLEX")
    assert lg.roster_size == 5


def test_provider_credential_and_discovered_league_construct():
    cred = ProviderCredential(provider="espn", user_identifier="{SWID}",
                              espn_s2="s2", swid="{SWID}", updated_at="t")
    assert cred.espn_s2 == "s2"
    disc = DiscoveredLeague(provider="sleeper", provider_league_id="L1", season=2026,
                            name="X", num_teams=12, draft_type="snake",
                            fmt="dynasty", draft_status="complete", already_tracked=True)
    assert disc.already_tracked is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/domain/test_models.py -q`
Expected: FAIL with `ImportError: cannot import name 'TrackedLeague'`.

- [ ] **Step 3: Write minimal implementation**

In `src/ffdo/domain/models.py`, add near the top after existing imports:

```python
from typing import Any
```

Add these types (place them after `LeagueProfile`, before `DraftPick`), and **delete the `Session` class entirely**:

```python
def make_league_key(provider: str, provider_league_id: str, season: int) -> str:
    return f"{provider}:{provider_league_id}:{season}"


@dataclass(frozen=True, slots=True)
class TrackedLeague:
    league_key: str
    provider: str
    provider_league_id: str
    season: int
    name: str
    user_id: str
    roster_id: int | None
    draft_id: str
    draft_type: str
    draft_status: str
    num_teams: int
    budget: int | None
    rounds: int
    roster_positions: tuple[str, ...]
    scoring_settings: Mapping[str, float]
    fmt: str
    format_override: str | None
    raw_settings: Mapping[str, Any]
    is_mock: bool
    tracked_at: str
    last_refreshed_at: str

    @property
    def resolved_format(self) -> str:
        return self.format_override or self.fmt

    @property
    def starting_slots(self) -> tuple[str, ...]:
        return tuple(p for p in self.roster_positions if p != "BN")

    @property
    def roster_size(self) -> int:
        return len(self.roster_positions)


@dataclass(frozen=True, slots=True)
class ProviderCredential:
    provider: str
    user_identifier: str
    espn_s2: str | None
    swid: str | None
    updated_at: str


@dataclass(frozen=True, slots=True)
class DiscoveredLeague:
    provider: str
    provider_league_id: str
    season: int
    name: str
    num_teams: int
    draft_type: str
    fmt: str
    draft_status: str
    already_tracked: bool
```

Leave `Session` deleted — later tasks remove its remaining importers. Expect `tests/api/test_session.py`, `tests/api/test_app.py`, `tests/ingest/test_connect.py`, `tests/ingest/espn/test_connect.py`, and `src/ffdo/api/session.py` / `connect.py` / `espn/connect.py` / `app.py` to fail to import until their tasks land. That is expected; this task's gate is only `tests/domain/test_models.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/domain/test_models.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/domain/models.py tests/domain/test_models.py
git commit -m "refactor: replace Session with TrackedLeague/ProviderCredential/DiscoveredLeague"
```

---

## Task 2: `LeagueStore` — SQLite CRUD for tracked leagues + credentials

**Files:**
- Create: `src/ffdo/api/store.py`
- Create: `tests/api/test_store.py`

**Interfaces:**
- Consumes: `TrackedLeague`, `ProviderCredential`, `make_league_key` from Task 1.
- Produces:
  - `LeagueStore(path: pathlib.Path)` — creates the DB file and schema on first use.
  - `.list() -> list[TrackedLeague]` — ordered by `tracked_at`.
  - `.get(league_key: str) -> TrackedLeague | None`.
  - `.upsert(tracked: TrackedLeague) -> None` — `INSERT OR REPLACE` on `league_key`; if a row already exists, its stored `format_override` and `tracked_at` are preserved (the incoming values for those two fields are ignored).
  - `.delete(league_key: str) -> None`.
  - `.set_format_override(league_key: str, value: str | None) -> None`.
  - `.touch_status(league_key: str, draft_status: str) -> None` — updates `draft_status` and `last_refreshed_at`; no-op if the key is absent.
  - `.get_credential(provider: str) -> ProviderCredential | None`.
  - `.put_credential(cred: ProviderCredential) -> None` — `INSERT OR REPLACE` on `provider`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_store.py`:

```python
from ffdo.api.store import LeagueStore
from ffdo.domain.models import ProviderCredential, TrackedLeague


def _tracked(**overrides):
    base = dict(
        league_key="sleeper:L1:2026", provider="sleeper", provider_league_id="L1",
        season=2026, name="Test League", user_id="U1", roster_id=3,
        draft_id="D1", draft_type="auction", draft_status="pre_draft",
        num_teams=12, budget=200, rounds=13,
        roster_positions=("QB", "RB", "BN"), scoring_settings={"rec": 0.5},
        fmt="redraft", format_override=None, raw_settings={"type": 0}, is_mock=False,
        tracked_at="2026-09-02T00:00:00+00:00",
        last_refreshed_at="2026-09-02T00:00:00+00:00",
    )
    return TrackedLeague(**{**base, **overrides})


def test_upsert_then_get_round_trips_every_field(tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    lg = _tracked()
    store.upsert(lg)
    got = store.get("sleeper:L1:2026")
    assert got == lg


def test_get_returns_none_for_an_unknown_key(tmp_path):
    assert LeagueStore(tmp_path / "ffdo.db").get("nope:x:2026") is None


def test_list_is_ordered_by_tracked_at(tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(league_key="sleeper:B:2026", provider_league_id="B",
                          tracked_at="2026-09-02T02:00:00+00:00"))
    store.upsert(_tracked(league_key="sleeper:A:2026", provider_league_id="A",
                          tracked_at="2026-09-02T01:00:00+00:00"))
    assert [l.provider_league_id for l in store.list()] == ["A", "B"]


def test_upsert_preserves_an_existing_format_override_and_tracked_at(tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(tracked_at="2026-09-02T01:00:00+00:00"))
    store.set_format_override("sleeper:L1:2026", "dynasty")
    # A later refresh re-upserts with fresh provider data but no override:
    store.upsert(_tracked(name="Renamed", tracked_at="2026-09-09T09:00:00+00:00"))
    got = store.get("sleeper:L1:2026")
    assert got.name == "Renamed"
    assert got.format_override == "dynasty"
    assert got.tracked_at == "2026-09-02T01:00:00+00:00"


def test_delete_removes_the_row(tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())
    store.delete("sleeper:L1:2026")
    assert store.get("sleeper:L1:2026") is None


def test_touch_status_updates_status_and_refreshed_at(tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(draft_status="drafting"))
    store.touch_status("sleeper:L1:2026", "complete")
    got = store.get("sleeper:L1:2026")
    assert got.draft_status == "complete"
    assert got.last_refreshed_at != "2026-09-02T00:00:00+00:00"


def test_touch_status_is_a_noop_for_an_unknown_key(tmp_path):
    LeagueStore(tmp_path / "ffdo.db").touch_status("nope:x:2026", "complete")  # no raise


def test_credentials_round_trip_and_replace_on_provider(tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.put_credential(ProviderCredential("espn", "{SWID}", "s2a", "{SWID}", "t1"))
    store.put_credential(ProviderCredential("espn", "{SWID}", "s2b", "{SWID}", "t2"))
    got = store.get_credential("espn")
    assert got.espn_s2 == "s2b"
    assert store.get_credential("sleeper") is None


def test_a_corrupt_db_file_reads_as_empty_not_an_exception(tmp_path):
    p = tmp_path / "ffdo.db"
    p.write_text("this is not sqlite", encoding="utf-8")
    store = LeagueStore(p)
    assert store.list() == []
    assert store.get("sleeper:L1:2026") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_store.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.api.store'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ffdo/api/store.py`:

```python
"""SQLite-backed store of tracked leagues and per-provider credentials.

Replaces the single-league `session.json` / `SessionStore`. One file
(`data/ffdo.db`), stdlib `sqlite3`, no ORM. This app is still a single
local process for one user, so there is no concurrency model beyond
"open a connection per call."
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ffdo.domain.models import ProviderCredential, TrackedLeague

_LEAGUE_COLUMNS = (
    "league_key", "provider", "provider_league_id", "season", "name", "user_id",
    "roster_id", "draft_id", "draft_type", "draft_status", "num_teams", "budget",
    "rounds", "roster_positions", "scoring_settings", "format", "format_override",
    "raw_settings", "is_mock", "tracked_at", "last_refreshed_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LeagueStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._ready = False

    # -- schema ------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        if not self._ready:
            self._init_schema(conn)
            self._ready = True
        return conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tracked_league (
                    league_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_league_id TEXT NOT NULL,
                    season INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    roster_id INTEGER,
                    draft_id TEXT NOT NULL,
                    draft_type TEXT NOT NULL,
                    draft_status TEXT NOT NULL,
                    num_teams INTEGER NOT NULL,
                    budget INTEGER,
                    rounds INTEGER NOT NULL,
                    roster_positions TEXT NOT NULL,
                    scoring_settings TEXT NOT NULL,
                    format TEXT NOT NULL,
                    format_override TEXT,
                    raw_settings TEXT NOT NULL,
                    is_mock INTEGER NOT NULL,
                    tracked_at TEXT NOT NULL,
                    last_refreshed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_credential (
                    provider TEXT PRIMARY KEY,
                    user_identifier TEXT NOT NULL,
                    espn_s2 TEXT,
                    swid TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.commit()
        except sqlite3.DatabaseError:
            # A corrupt/foreign file at this path: treat the store as empty
            # rather than crashing the app on startup.
            pass

    # -- tracked leagues -------------------------------------------------

    def list(self) -> list[TrackedLeague]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM tracked_league ORDER BY tracked_at"
                ).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [self._row_to_league(r) for r in rows]

    def get(self, league_key: str) -> TrackedLeague | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM tracked_league WHERE league_key = ?", (league_key,)
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        return self._row_to_league(row) if row is not None else None

    def upsert(self, tracked: TrackedLeague) -> None:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT format_override, tracked_at FROM tracked_league WHERE league_key = ?",
                (tracked.league_key,),
            ).fetchone()
            format_override = existing["format_override"] if existing else tracked.format_override
            tracked_at = existing["tracked_at"] if existing else tracked.tracked_at
            values = (
                tracked.league_key, tracked.provider, tracked.provider_league_id,
                tracked.season, tracked.name, tracked.user_id, tracked.roster_id,
                tracked.draft_id, tracked.draft_type, tracked.draft_status,
                tracked.num_teams, tracked.budget, tracked.rounds,
                json.dumps(list(tracked.roster_positions)),
                json.dumps(dict(tracked.scoring_settings)),
                tracked.fmt, format_override,
                json.dumps(dict(tracked.raw_settings)),
                int(tracked.is_mock), tracked_at, tracked.last_refreshed_at,
            )
            placeholders = ", ".join("?" for _ in _LEAGUE_COLUMNS)
            conn.execute(
                f"INSERT OR REPLACE INTO tracked_league "
                f"({', '.join(_LEAGUE_COLUMNS)}) VALUES ({placeholders})",
                values,
            )
            conn.commit()

    def delete(self, league_key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM tracked_league WHERE league_key = ?", (league_key,))
            conn.commit()

    def set_format_override(self, league_key: str, value: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tracked_league SET format_override = ? WHERE league_key = ?",
                (value, league_key),
            )
            conn.commit()

    def touch_status(self, league_key: str, draft_status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tracked_league SET draft_status = ?, last_refreshed_at = ? "
                "WHERE league_key = ?",
                (draft_status, _now(), league_key),
            )
            conn.commit()

    # -- credentials ----------------------------------------------------

    def get_credential(self, provider: str) -> ProviderCredential | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM provider_credential WHERE provider = ?", (provider,)
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        if row is None:
            return None
        return ProviderCredential(
            provider=row["provider"], user_identifier=row["user_identifier"],
            espn_s2=row["espn_s2"], swid=row["swid"], updated_at=row["updated_at"],
        )

    def put_credential(self, cred: ProviderCredential) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO provider_credential "
                "(provider, user_identifier, espn_s2, swid, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (cred.provider, cred.user_identifier, cred.espn_s2, cred.swid,
                 cred.updated_at),
            )
            conn.commit()

    # -- row mapping ---------------------------------------------------

    @staticmethod
    def _row_to_league(row: sqlite3.Row) -> TrackedLeague:
        return TrackedLeague(
            league_key=row["league_key"], provider=row["provider"],
            provider_league_id=row["provider_league_id"], season=row["season"],
            name=row["name"], user_id=row["user_id"], roster_id=row["roster_id"],
            draft_id=row["draft_id"], draft_type=row["draft_type"],
            draft_status=row["draft_status"], num_teams=row["num_teams"],
            budget=row["budget"], rounds=row["rounds"],
            roster_positions=tuple(json.loads(row["roster_positions"])),
            scoring_settings={k: float(v) for k, v
                              in json.loads(row["scoring_settings"]).items()},
            fmt=row["format"], format_override=row["format_override"],
            raw_settings=json.loads(row["raw_settings"]),
            is_mock=bool(row["is_mock"]),
            tracked_at=row["tracked_at"], last_refreshed_at=row["last_refreshed_at"],
        )
```

> The `_row_to_league` `scoring_settings` conversion to `float` mirrors `ingest/league.py`'s parse so equality with a freshly-built `TrackedLeague` holds. If `test_upsert_then_get_round_trips_every_field` fails on `scoring_settings` or `raw_settings` equality, make the test's `_tracked()` use `{"rec": 0.5}` (float) and `{"type": 0}` — JSON round-trips `0` as `int` `0`, which matches.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_store.py -q`
Expected: PASS (all 9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/api/store.py tests/api/test_store.py
git commit -m "feat: add LeagueStore SQLite persistence for tracked leagues"
```

---

## Task 3: One-shot `session.json` → SQLite migration

**Files:**
- Modify: `src/ffdo/api/store.py`
- Modify: `tests/api/test_store.py`

**Interfaces:**
- Consumes: `LeagueStore` from Task 2.
- Produces: `LeagueStore.__init__` gains a `legacy_session_path: Path | None = None` parameter. On first `_connect()`, if that path is given and exists and `tracked_league` is empty, the legacy JSON is read, written as one `tracked_league` row (+ a `provider_credential` row when it carried `espn_s2`/`swid` or a `username`), and the file is renamed to `<name>.migrated`. Idempotent.

- [ ] **Step 1: Write the failing test**

Add to `tests/api/test_store.py`:

```python
import json as _json


_LEGACY_SESSION = {
    "username": "noahdschroeder", "user_id": "U1", "league_id": "1315881559957458944",
    "draft_id": "1315881559965835264", "roster_id": 7, "league_name": "P-Vegas Ballers",
    "season": 2026, "num_teams": 12, "budget": 200,
    "roster_positions": ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                         "BN", "BN", "BN", "BN", "BN"],
    "scoring_settings": {"rec": 0.5}, "draft_type": "auction",
    "draft_status": "pre_draft", "rounds": 13,
    "connected_at": "2026-08-24T00:00:00+00:00", "is_mock": False,
    "provider": "sleeper", "espn_s2": None, "swid": None,
}


def test_migration_imports_a_legacy_session_once(tmp_path):
    legacy = tmp_path / "session.json"
    legacy.write_text(_json.dumps(_LEGACY_SESSION), encoding="utf-8")

    store = LeagueStore(tmp_path / "ffdo.db", legacy_session_path=legacy)
    leagues = store.list()

    assert len(leagues) == 1
    lg = leagues[0]
    assert lg.league_key == "sleeper:1315881559957458944:2026"
    assert lg.name == "P-Vegas Ballers"
    assert lg.roster_id == 7
    assert lg.draft_type == "auction"
    assert lg.fmt == "redraft"
    assert not legacy.exists()
    assert (tmp_path / "session.json.migrated").exists()


def test_migration_is_idempotent_and_skipped_when_leagues_exist(tmp_path):
    legacy = tmp_path / "session.json"
    legacy.write_text(_json.dumps(_LEGACY_SESSION), encoding="utf-8")
    db = tmp_path / "ffdo.db"

    LeagueStore(db, legacy_session_path=legacy).list()
    # Second construction: legacy file is gone, nothing re-imported, no crash.
    store2 = LeagueStore(db, legacy_session_path=legacy)
    assert len(store2.list()) == 1


def test_migration_carries_espn_credentials(tmp_path):
    legacy = tmp_path / "session.json"
    espn_session = {**_LEGACY_SESSION, "provider": "espn",
                    "league_id": "1882997948", "draft_id": "1882997948",
                    "draft_type": "snake", "espn_s2": "s2val", "swid": "{SWID}",
                    "username": ""}
    legacy.write_text(_json.dumps(espn_session), encoding="utf-8")

    store = LeagueStore(tmp_path / "ffdo.db", legacy_session_path=legacy)
    cred = store.get_credential("espn")
    assert cred is not None and cred.espn_s2 == "s2val" and cred.swid == "{SWID}"
    assert store.list()[0].league_key == "espn:1882997948:2026"


def test_no_migration_when_the_legacy_file_is_absent(tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db", legacy_session_path=tmp_path / "nope.json")
    assert store.list() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_store.py -q -k migration`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'legacy_session_path'`.

- [ ] **Step 3: Write minimal implementation**

In `src/ffdo/api/store.py`, update `__init__` and `_connect`, and add `_migrate_legacy_session`:

```python
    def __init__(self, path: Path, legacy_session_path: Path | None = None) -> None:
        self._path = path
        self._legacy_session_path = legacy_session_path
        self._ready = False
```

In `_connect`, after `self._ready = True` is set inside the `if not self._ready:` block, call the migration **before** returning — restructure to:

```python
        first_open = not self._ready
        if first_open:
            self._init_schema(conn)
            self._ready = True
        if first_open:
            self._migrate_legacy_session(conn)
        return conn
```

Add the method:

```python
    def _migrate_legacy_session(self, conn: sqlite3.Connection) -> None:
        path = self._legacy_session_path
        if path is None or not path.exists():
            return
        try:
            already = conn.execute("SELECT COUNT(*) FROM tracked_league").fetchone()[0]
        except sqlite3.DatabaseError:
            return
        if already:
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        provider = raw.get("provider", "sleeper")
        is_mock = bool(raw.get("is_mock"))
        provider_key = "sleeper-mock" if is_mock else provider
        league_id = raw.get("league_id") or raw.get("draft_id", "")
        provider_league_id = raw["draft_id"] if is_mock else league_id
        season = int(raw["season"])
        from ffdo.domain.models import make_league_key
        league_key = make_league_key(provider_key, provider_league_id, season)
        now = _now()
        tracked = TrackedLeague(
            league_key=league_key, provider=provider_key,
            provider_league_id=provider_league_id, season=season,
            name=raw.get("league_name", ""), user_id=raw.get("user_id", ""),
            roster_id=raw.get("roster_id"), draft_id=raw.get("draft_id", ""),
            draft_type=raw.get("draft_type", "snake"),
            draft_status=raw.get("draft_status", ""),
            num_teams=int(raw.get("num_teams", 0)), budget=raw.get("budget"),
            rounds=int(raw.get("rounds", 0)),
            roster_positions=tuple(raw.get("roster_positions", ())),
            scoring_settings={k: float(v) for k, v
                              in (raw.get("scoring_settings") or {}).items()},
            fmt="redraft", format_override=None, raw_settings={}, is_mock=is_mock,
            tracked_at=now, last_refreshed_at=now,
        )
        self._write_league_row(conn, tracked, tracked.format_override, tracked.tracked_at)

        espn_s2, swid = raw.get("espn_s2"), raw.get("swid")
        if espn_s2 or swid:
            conn.execute(
                "INSERT OR REPLACE INTO provider_credential "
                "(provider, user_identifier, espn_s2, swid, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("espn", swid or "", espn_s2, swid, now),
            )
        elif provider == "sleeper" and raw.get("username"):
            conn.execute(
                "INSERT OR REPLACE INTO provider_credential "
                "(provider, user_identifier, espn_s2, swid, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("sleeper", raw["username"], None, None, now),
            )
        conn.commit()
        path.rename(path.with_name(path.name + ".migrated"))
```

Refactor `upsert`'s row-writing into a shared `_write_league_row(conn, tracked, format_override, tracked_at)` static/helper method so the migration reuses it (extract the `values = (...)` + `INSERT OR REPLACE` block verbatim, parameterised on the two preserved fields).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_store.py -q`
Expected: PASS (all 13 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/api/store.py tests/api/test_store.py
git commit -m "feat: migrate a legacy session.json into LeagueStore on first open"
```

---

## Task 4: Sleeper `detect_format`

**Files:**
- Modify: `src/ffdo/ingest/league.py`
- Modify: `tests/ingest/test_league.py`

**Interfaces:**
- Produces: `ffdo.ingest.league.detect_format(raw: dict) -> str` returning `"dynasty"` | `"keeper"` | `"redraft"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/ingest/test_league.py`:

```python
from ffdo.ingest.league import detect_format


def test_detect_format_dynasty_from_settings_type_2():
    assert detect_format({"settings": {"type": 2}}) == "dynasty"


def test_detect_format_keeper_from_settings_type_1():
    assert detect_format({"settings": {"type": 1}}) == "keeper"


def test_detect_format_keeper_from_max_keepers_or_previous_league():
    assert detect_format({"settings": {"type": 0, "max_keepers": 2}}) == "keeper"
    assert detect_format({"settings": {"type": 0}, "previous_league_id": "L0"}) == "keeper"


def test_detect_format_redraft_by_default():
    assert detect_format({"settings": {"type": 0}}) == "redraft"
    assert detect_format({}) == "redraft"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_league.py -q -k detect_format`
Expected: FAIL with `ImportError: cannot import name 'detect_format'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/ffdo/ingest/league.py`:

```python
def detect_format(raw: dict[str, Any]) -> str:
    """Best-effort redraft / keeper / dynasty classification from a Sleeper
    league object. Not authoritative — the user can override it — but right
    for most real leagues."""
    settings = raw.get("settings") or {}
    if settings.get("type") == 2:
        return "dynasty"
    if (settings.get("type") == 1
            or (settings.get("max_keepers") or 0) > 0
            or raw.get("previous_league_id")):
        return "keeper"
    return "redraft"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/test_league.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/league.py tests/ingest/test_league.py
git commit -m "feat: detect redraft/keeper/dynasty from a Sleeper league object"
```

---

## Task 5: ESPN `detect_format`

**Files:**
- Modify: `src/ffdo/ingest/espn/league.py`
- Modify: `tests/ingest/espn/test_league.py`

**Interfaces:**
- Produces: `ffdo.ingest.espn.league.detect_format(raw: dict) -> str` returning `"keeper"` | `"redraft"` (never `"dynasty"` — ESPN has no such flag; dynasty is override-only there).

- [ ] **Step 1: Write the failing test**

Add to `tests/ingest/espn/test_league.py`:

```python
from ffdo.ingest.espn.league import detect_format


def test_detect_format_keeper_when_keeper_count_positive():
    raw = {"settings": {"draftSettings": {"keeperCount": 3}}}
    assert detect_format(raw) == "keeper"


def test_detect_format_redraft_when_no_keepers():
    assert detect_format({"settings": {"draftSettings": {"keeperCount": 0}}}) == "redraft"
    assert detect_format({"settings": {}}) == "redraft"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/espn/test_league.py -q -k detect_format`
Expected: FAIL with `ImportError: cannot import name 'detect_format'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/ffdo/ingest/espn/league.py`:

```python
def detect_format(raw: dict[str, Any]) -> str:
    """ESPN exposes keeper settings but no dynasty flag — `"dynasty"` is
    override-only for ESPN leagues."""
    draft_settings = (raw.get("settings") or {}).get("draftSettings") or {}
    return "keeper" if (draft_settings.get("keeperCount") or 0) > 0 else "redraft"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/espn/test_league.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/espn/league.py tests/ingest/espn/test_league.py
git commit -m "feat: detect keeper vs redraft from an ESPN league object"
```

---

## Task 6: Sleeper league discovery — `ingest/discover.py`

**Files:**
- Create: `src/ffdo/ingest/discover.py`
- Create: `tests/ingest/test_discover.py`

**Interfaces:**
- Consumes: `SleeperClient` from `ffdo.ingest.client`; `user.parse`; `league.detect_format`; `DiscoveredLeague` from Task 1.
- Produces:
  - `resolve_user_id(sleeper: SleeperClient, username: str) -> str` — `GET /v1/user/{username}` → `user.parse()[0]`; raises `ffdo.ingest.connect.ConnectError("Username not found")` on `httpx.HTTPStatusError`.
  - `list_leagues(sleeper: SleeperClient, user_id: str, season: int, *, tracked_keys: frozenset[str] = frozenset()) -> list[DiscoveredLeague]` — `GET /v1/user/{user_id}/leagues/nfl/{season}`; maps each entry; `already_tracked` is `make_league_key("sleeper", <id>, season) in tracked_keys`.

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/test_discover.py`:

```python
import httpx
import pytest

from ffdo.ingest import discover
from ffdo.ingest.client import SleeperClient
from ffdo.ingest.connect import ConnectError

_LEAGUES_RAW = [
    {"league_id": "L1", "name": "Redraft Home", "season": "2026",
     "total_rosters": 12, "status": "in_season", "settings": {"type": 0, "num_teams": 12}},
    {"league_id": "L2", "name": "The Dynasty", "season": "2026",
     "total_rosters": 10, "status": "pre_draft",
     "settings": {"type": 2, "num_teams": 10}},
]


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def test_list_leagues_maps_each_entry():
    def handler(request):
        assert request.url.path == "/v1/user/U1/leagues/nfl/2026"
        return httpx.Response(200, json=_LEAGUES_RAW)

    out = discover.list_leagues(_client(handler), "U1", 2026)

    assert [d.provider_league_id for d in out] == ["L1", "L2"]
    assert out[0].fmt == "redraft" and out[1].fmt == "dynasty"
    assert out[0].num_teams == 12
    assert out[0].draft_status == "in_season"
    assert all(d.provider == "sleeper" and d.season == 2026 for d in out)
    assert all(d.already_tracked is False for d in out)


def test_list_leagues_flags_already_tracked():
    def handler(request):
        return httpx.Response(200, json=_LEAGUES_RAW)

    out = discover.list_leagues(_client(handler), "U1", 2026,
                                tracked_keys=frozenset({"sleeper:L2:2026"}))
    by_id = {d.provider_league_id: d for d in out}
    assert by_id["L2"].already_tracked is True
    assert by_id["L1"].already_tracked is False


def test_list_leagues_returns_empty_for_no_leagues():
    out = discover.list_leagues(_client(lambda r: httpx.Response(200, json=[])), "U1", 2026)
    assert out == []


def test_resolve_user_id_raises_connect_error_on_404():
    def handler(request):
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(ConnectError, match="Username not found"):
        discover.resolve_user_id(_client(handler), "ghost")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_discover.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.ingest.discover'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ffdo/ingest/discover.py`:

```python
"""Lists every Sleeper league a user is in for a season — the input to the
discover-then-pick onboarding flow. Distinct from `ingest.connect`, which
does the heavier per-league league/draft/roster resolution once the user
picks which leagues to track."""

from __future__ import annotations

import httpx

from ffdo.domain.models import DiscoveredLeague, make_league_key
from ffdo.ingest import league as league_mod
from ffdo.ingest import user as user_mod
from ffdo.ingest.client import V1, SleeperClient
from ffdo.ingest.connect import ConnectError


def resolve_user_id(sleeper: SleeperClient, username: str) -> str:
    try:
        raw = sleeper.get_json(f"{V1}/user/{username}")
    except httpx.HTTPStatusError as exc:
        raise ConnectError("Username not found") from exc
    return user_mod.parse(raw)[0]


def list_leagues(
    sleeper: SleeperClient,
    user_id: str,
    season: int,
    *,
    tracked_keys: frozenset[str] = frozenset(),
) -> list[DiscoveredLeague]:
    raw = sleeper.get_json(f"{V1}/user/{user_id}/leagues/nfl/{season}")
    out: list[DiscoveredLeague] = []
    for lg in raw or []:
        league_id = str(lg["league_id"])
        settings = lg.get("settings") or {}
        out.append(DiscoveredLeague(
            provider="sleeper",
            provider_league_id=league_id,
            season=season,
            name=lg.get("name") or "",
            num_teams=int(settings.get("num_teams") or lg.get("total_rosters") or 0),
            draft_type="",  # resolved at track time from the draft object
            fmt=league_mod.detect_format(lg),
            draft_status=lg.get("status") or "",
            already_tracked=make_league_key("sleeper", league_id, season) in tracked_keys,
        ))
    return out
```

> Circular-import check: `ingest/connect.py` does not import `ingest/discover.py`, so `from ffdo.ingest.connect import ConnectError` here is safe. If Task 8's rename introduces a cycle, move `ConnectError` to a tiny `ffdo/ingest/errors.py` and re-export it from `connect.py` — but only if an `ImportError` actually appears.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/test_discover.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/discover.py tests/ingest/test_discover.py
git commit -m "feat: list a Sleeper user's leagues for discover-then-pick onboarding"
```

---

## Task 7: ESPN league discovery — `ingest/espn/discover.py`

**Files:**
- Create: `src/ffdo/ingest/espn/discover.py`
- Create: `tests/ingest/espn/test_discover.py`

**Interfaces:**
- Consumes: `EspnClient`; `espn.connect.normalize_swid`; `espn.league.detect_format`; `DiscoveredLeague`.
- Produces: `list_leagues(espn_s2: str, swid: str, season: int, *, tracked_keys: frozenset[str] = frozenset(), transport: httpx.BaseTransport | None = None) -> list[DiscoveredLeague]` — calls the fan API `https://fan.api.espn.com/apis/v2/fans/{swid}?displayEvents=true&displayNow=true&displayRecs=false&featureFlags=fanApiIntegrationWebview&source=ESPN.com&lang=en&section=espn`, filters `preferences` to `fantasy` entries whose `abbrev`/`gameId` marks football and whose season matches, maps to `DiscoveredLeague`. `ConnectError` on 401/403. On any other error, returns `[]` (manual add-by-ID is the documented fallback — spec §4.1).

> The fan-API response shape is not verified in this repo. Parse defensively: an entry's league id, name, size, and draft status live under `preferences[].metaData.entry.groups[0]` in community writeups, but treat every access as `.get(...)` with a fallback and skip an entry that lacks a league id. The test below pins the shape the parser expects; if a real call in dev returns something different, adjust the parser and the fixture together and note it in the commit.

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/espn/test_discover.py`:

```python
import httpx
import pytest

from ffdo.ingest.espn import discover
from ffdo.ingest.espn.connect import ConnectError

_FAN_RAW = {
    "preferences": [
        {"type": {"code": "fantasy"}, "metaData": {"entry": {
            "entryId": 7, "gameId": 1, "seasonId": 2026,
            "name": "Team Schroeder",
            "groups": [{"groupId": 1882997948, "groupName": "Dynasty Warehouse",
                        "groupSize": 12, "draftComplete": True}]}}},
        {"type": {"code": "fantasy"}, "metaData": {"entry": {
            "entryId": 3, "gameId": 1, "seasonId": 2025,
            "groups": [{"groupId": 999, "groupName": "Last Year", "groupSize": 10}]}}},
        {"type": {"code": "fantasy"}, "metaData": {"entry": {
            "entryId": 9, "gameId": 40, "seasonId": 2026,   # gameId 40 = basketball
            "groups": [{"groupId": 555, "groupName": "Hoops"}]}}},
    ]
}


def _transport(handler):
    return httpx.MockTransport(handler)


def test_list_leagues_filters_to_football_and_season_and_maps():
    def handler(request):
        assert "fan.api.espn.com" in str(request.url)
        return httpx.Response(200, json=_FAN_RAW)

    out = discover.list_leagues("s2", "{SWID}", 2026, transport=_transport(handler))

    assert len(out) == 1
    d = out[0]
    assert d.provider == "espn"
    assert d.provider_league_id == "1882997948"
    assert d.name == "Dynasty Warehouse"
    assert d.num_teams == 12
    assert d.draft_status == "complete"
    assert d.season == 2026


def test_list_leagues_flags_already_tracked():
    def handler(request):
        return httpx.Response(200, json=_FAN_RAW)

    out = discover.list_leagues("s2", "{SWID}", 2026,
                                tracked_keys=frozenset({"espn:1882997948:2026"}),
                                transport=_transport(handler))
    assert out[0].already_tracked is True


def test_list_leagues_raises_on_expired_cookies():
    def handler(request):
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(ConnectError, match="expired"):
        discover.list_leagues("s2", "{SWID}", 2026, transport=_transport(handler))


def test_list_leagues_returns_empty_on_an_unexpected_error():
    def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    assert discover.list_leagues("s2", "{SWID}", 2026,
                                 transport=_transport(handler)) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/espn/test_discover.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ffdo/ingest/espn/discover.py`:

```python
"""Lists every ESPN fantasy-football league a SWID belongs to for a season,
via ESPN's unofficial "fan" API. The espn_s2/SWID cookie pair the user
already pastes for one league covers all of them.

The fan-API response shape is not contract-stable; every field access is
defensive and an unparseable entry is skipped. On any non-auth error this
returns `[]` — the manual add-by-league-ID path is the fallback (spec
§4.1)."""

from __future__ import annotations

import logging

import httpx

from ffdo.domain.models import DiscoveredLeague, make_league_key
from ffdo.ingest.espn import league as league_mod
from ffdo.ingest.espn.client import _USER_AGENT
from ffdo.ingest.espn.connect import ConnectError, normalize_swid

log = logging.getLogger(__name__)

_FAN_BASE = "https://fan.api.espn.com/apis/v2/fans"
_FAN_QUERY = (
    "?displayEvents=true&displayNow=true&displayRecs=false"
    "&featureFlags=fanApiIntegrationWebview&source=ESPN.com&lang=en&section=espn"
)
_FOOTBALL_GAME_IDS = {1, "1", "ffl"}


def list_leagues(
    espn_s2: str,
    swid: str,
    season: int,
    *,
    tracked_keys: frozenset[str] = frozenset(),
    transport: httpx.BaseTransport | None = None,
) -> list[DiscoveredLeague]:
    swid = normalize_swid(swid)
    headers = {
        "Cookie": f"espn_s2={espn_s2}; SWID={swid}",
        "User-Agent": _USER_AGENT,
    }
    url = f"{_FAN_BASE}/{swid}{_FAN_QUERY}"
    try:
        with httpx.Client(timeout=30.0, transport=transport) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            raw = resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            raise ConnectError(
                "Your ESPN cookies look expired -- grab fresh espn_s2/SWID values"
            ) from exc
        log.warning("ESPN fan API returned %s; falling back to manual add",
                    exc.response.status_code)
        return []
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("ESPN fan API call failed (%s); falling back to manual add", exc)
        return []

    out: list[DiscoveredLeague] = []
    for pref in raw.get("preferences") or []:
        if ((pref.get("type") or {}).get("code")) != "fantasy":
            continue
        entry = (pref.get("metaData") or {}).get("entry") or {}
        if entry.get("gameId") not in _FOOTBALL_GAME_IDS:
            continue
        if int(entry.get("seasonId") or 0) != season:
            continue
        groups = entry.get("groups") or []
        if not groups:
            continue
        group = groups[0]
        league_id = group.get("groupId")
        if league_id is None:
            continue
        league_id = str(league_id)
        draft_complete = bool(group.get("draftComplete"))
        out.append(DiscoveredLeague(
            provider="espn",
            provider_league_id=league_id,
            season=season,
            name=group.get("groupName") or "",
            num_teams=int(group.get("groupSize") or 0),
            draft_type="",  # resolved at track time
            fmt="redraft",  # detect_format needs mSettings, not in the fan payload
            draft_status="complete" if draft_complete else "pre_draft",
            already_tracked=make_league_key("espn", league_id, season) in tracked_keys,
        ))
    return out
```

> `league_mod` is imported for symmetry with the Sleeper module and future use; if a linter flags it as unused, drop the import. The `fmt="redraft"` default is deliberate — the fan payload carries no scoring/keeper detail, and the real format is filled in at track time (Task 9) from `mSettings`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/espn/test_discover.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/espn/discover.py tests/ingest/espn/test_discover.py
git commit -m "feat: list a SWID's ESPN football leagues via the fan API"
```

---

## Task 8: Rename Sleeper `resolve` → `track`, return `TrackedLeague`

**Files:**
- Modify: `src/ffdo/ingest/connect.py`
- Modify: `tests/ingest/test_connect.py`

**Interfaces:**
- Consumes: `TrackedLeague`, `make_league_key`, `league.detect_format`.
- Produces:
  - `track(sleeper: SleeperClient, league_id: str, username: str, *, now=None) -> TrackedLeague`
  - `track_mock(sleeper: SleeperClient, draft_id: str, username: str, *, now=None) -> TrackedLeague`
  - `ConnectError` unchanged.
  - The old names `resolve` / `resolve_mock` are **removed** (no alias).

- [ ] **Step 1: Write the failing test**

Rewrite the assertions in `tests/ingest/test_connect.py` — replace `connect.resolve(` → `connect.track(` and `connect.resolve_mock(` → `connect.track_mock(` throughout, then update the return-shape assertions. For `test_resolve_returns_a_fully_populated_session` (rename to `test_track_returns_a_fully_populated_tracked_league`):

```python
def test_track_returns_a_fully_populated_tracked_league():
    client = _client(_happy_handler)
    lg = connect.track(
        client, "L1", "tester",
        now=lambda: datetime(2026, 8, 22, tzinfo=timezone.utc))

    assert lg.league_key == "sleeper:L1:2026"
    assert lg.provider == "sleeper"
    assert lg.provider_league_id == "L1"
    assert lg.user_id == "U1"
    assert lg.roster_id == 3
    assert lg.draft_id == "D1"
    assert lg.name == "Test League"
    assert lg.season == 2026
    assert lg.num_teams == 12
    assert lg.budget == 200
    assert lg.roster_positions == (
        "QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
        "BN", "BN", "BN", "BN", "BN")
    assert lg.scoring_settings == {"rec": 0.5, "pass_td": 4.0}
    assert lg.draft_type == "auction"
    assert lg.draft_status == "pre_draft"
    assert lg.rounds == 13
    assert lg.fmt == "redraft"
    assert lg.is_mock is False
    assert lg.tracked_at == "2026-08-22T00:00:00+00:00"
    assert lg.last_refreshed_at == "2026-08-22T00:00:00+00:00"
    assert lg.raw_settings == LEAGUE_RAW["settings"]
```

For `test_resolve_mock_returns_a_fully_populated_session` (rename to `test_track_mock_...`):

```python
    lg = connect.track_mock(
        _mock_client(handler), "1397145756879605760", "schroedes",
        now=lambda: datetime(2026, 8, 22, tzinfo=timezone.utc))

    assert lg.is_mock is True
    assert lg.provider == "sleeper-mock"
    assert lg.league_key == "sleeper-mock:1397145756879605760:2026"
    assert lg.provider_league_id == "1397145756879605760"
    assert lg.draft_id == "1397145756879605760"
    assert lg.roster_id == 1
    assert lg.budget is None
    assert lg.scoring_settings["rec"] == 0.5
    assert lg.draft_type == "snake"
    assert lg.fmt == "redraft"
```

Keep every `ConnectError` test as-is except for the `resolve` → `track` name change. Update `test_resolve_falls_back_to_the_drafts_budget...` and `test_resolve_reads_rounds...` the same way (`.budget` / `.rounds` still exist on `TrackedLeague`).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_connect.py -q`
Expected: FAIL with `AttributeError: module 'ffdo.ingest.connect' has no attribute 'track'`.

- [ ] **Step 3: Write minimal implementation**

Rewrite `src/ffdo/ingest/connect.py`. Replace the `Session` import with `TrackedLeague`, `make_league_key`; add `league.detect_format`. Rename functions and build a `TrackedLeague`:

```python
from ffdo.domain.models import TrackedLeague, make_league_key


def track(
    sleeper: SleeperClient,
    league_id: str,
    username: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> TrackedLeague:
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

    budget = league.budget if league.budget is not None else state.budget

    try:
        user_raw = sleeper.get_json(f"{V1}/user/{username}")
    except httpx.HTTPStatusError as exc:
        raise ConnectError("Username not found") from exc
    user_id, _display_name = user_mod.parse(user_raw)

    rosters_raw = sleeper.get_json(f"{V1}/league/{league_id}/rosters")
    roster_id = league_mod.find_roster_id(rosters_raw, user_id)
    if roster_id is None:
        raise ConnectError("This user is not a member of that league")

    stamp = now().isoformat()
    return TrackedLeague(
        league_key=make_league_key("sleeper", league.league_id, league.season),
        provider="sleeper",
        provider_league_id=league.league_id,
        season=league.season,
        name=league.name,
        user_id=user_id,
        roster_id=roster_id,
        draft_id=draft_id,
        draft_type=state.draft_type,
        draft_status=state.status,
        num_teams=league.num_teams,
        budget=budget,
        rounds=state.rounds,
        roster_positions=league.roster_positions,
        scoring_settings=league.scoring_settings,
        fmt=league_mod.detect_format(league_raw),
        format_override=None,
        raw_settings=league_raw.get("settings") or {},
        is_mock=False,
        tracked_at=stamp,
        last_refreshed_at=stamp,
    )
```

For `track_mock`, mirror `resolve_mock`'s existing body but return a `TrackedLeague` with `provider="sleeper-mock"`, `provider_league_id=draft_id`, `league_key=make_league_key("sleeper-mock", draft_id, lg.season)`, `fmt="redraft"`, `raw_settings=draft_raw.get("settings") or {}`, `format_override=None`, `tracked_at`/`last_refreshed_at` = `stamp`. Delete `resolve` and `resolve_mock`.

Update the module docstring's "connected Session" wording to "tracked league".

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/test_connect.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/connect.py tests/ingest/test_connect.py
git commit -m "refactor: connect.resolve -> track, returning a TrackedLeague"
```

---

## Task 9: Rename ESPN `resolve` → `track`, return `TrackedLeague`

**Files:**
- Modify: `src/ffdo/ingest/espn/connect.py`
- Modify: `tests/ingest/espn/test_connect.py`

**Interfaces:**
- Produces: `track(league_id: str, season: int, espn_s2: str, swid: str, profiles: dict[str, PlayerProfile], espn_id_index: dict[str, str], *, now=None, transport=None) -> TrackedLeague` — same signature as the old `resolve`, new return type. `provider="espn"`, `provider_league_id=league.league_id`, `draft_id=league.league_id`, `league_key=make_league_key("espn", league.league_id, league.season)`, `fmt=espn_league_mod.detect_format(raw)`, `raw_settings=raw.get("settings") or {}`. `espn_s2` / `swid` are **not** on `TrackedLeague` — the endpoint (Task 10) writes them to `provider_credential`. `normalize_swid`, `ConnectError` unchanged.
- The old `resolve` name is removed.

- [ ] **Step 1: Write the failing test**

In `tests/ingest/espn/test_connect.py`, replace `connect.resolve(` → `connect.track(` and update the return assertions from `session.*` / `Session` fields to `TrackedLeague` fields. The happy-path test asserts:

```python
    lg = connect.track("1882997948", 2026, "s2val", "SWID-VALUE",
                       profiles, espn_id_index,
                       now=lambda: datetime(2026, 8, 23, tzinfo=timezone.utc),
                       transport=httpx.MockTransport(handler))

    assert lg.provider == "espn"
    assert lg.league_key == "espn:1882997948:2026"
    assert lg.provider_league_id == "1882997948"
    assert lg.draft_id == "1882997948"
    assert lg.roster_id == 7
    assert lg.draft_type == "snake"
    assert lg.fmt in ("redraft", "keeper")
    assert lg.tracked_at == "2026-08-23T00:00:00+00:00"
    assert not hasattr(lg, "espn_s2")
```

Keep the ESPN `ConnectError` cases (expired cookies, non-snake, not-a-member, player-pool failure) with only the `resolve` → `track` rename.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/espn/test_connect.py -q`
Expected: FAIL with `AttributeError: ... has no attribute 'track'`.

- [ ] **Step 3: Write minimal implementation**

In `src/ffdo/ingest/espn/connect.py`: swap the `Session` import for `TrackedLeague`, `make_league_key`; rename `resolve` → `track`; replace the returned `Session(...)` with:

```python
    stamp = now().isoformat()
    return TrackedLeague(
        league_key=make_league_key("espn", league.league_id, league.season),
        provider="espn",
        provider_league_id=league.league_id,
        season=league.season,
        name=league.name,
        user_id=swid,
        roster_id=roster_id,
        draft_id=league.league_id,
        draft_type=state.draft_type,
        draft_status=state.status,
        num_teams=league.num_teams,
        budget=league.budget,
        rounds=state.rounds,
        roster_positions=league.roster_positions,
        scoring_settings=league.scoring_settings,
        fmt=league_mod.detect_format(raw),
        format_override=None,
        raw_settings=raw.get("settings") or {},
        is_mock=False,
        tracked_at=stamp,
        last_refreshed_at=stamp,
    )
```

(`league_mod` is `ffdo.ingest.espn.league`, already imported.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/espn/test_connect.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/espn/connect.py tests/ingest/espn/test_connect.py
git commit -m "refactor: espn.connect.resolve -> track, returning a TrackedLeague"
```

---

## Task 10: Rewire `app.py` — league-scoped routing and league-management endpoints

**Files:**
- Modify: `src/ffdo/api/app.py`
- Delete: `src/ffdo/api/session.py`, `tests/api/test_session.py`
- Modify: `tests/api/conftest.py`
- Modify: `tests/api/test_app.py` (full rewrite of the endpoint/`_load_league` tests)

**Interfaces:**
- Consumes: `LeagueStore`, `TrackedLeague`, `ProviderCredential`, `make_league_key`; `discover.list_leagues` / `resolve_user_id`; `espn.discover.list_leagues`; `connect.track` / `track_mock`; `espn.connect.track`.
- Produces (module level, monkeypatchable):
  - `_STORE = LeagueStore(Path("data") / "ffdo.db", legacy_session_path=Path("data") / "session.json")`
  - `_load_league(league_key: str) -> TrackedLeague` — `_STORE.get(...)` or `raise HTTPException(404, "League not tracked")`.
  - `_league_public_dict(lg: TrackedLeague) -> dict` — `dataclasses.asdict`, then rename `fmt` → `format`, add `resolved_format`, drop nothing else (no credentials on `TrackedLeague`).
- Produces (HTTP):
  - `POST /api/providers/connect`
  - `GET /api/leagues`
  - `GET /api/leagues/{league_key}`
  - `POST /api/leagues/track`
  - `DELETE /api/leagues/{league_key}` → 204
  - `PATCH /api/leagues/{league_key}` (body `{"format_override": str | null}`)
  - `POST /api/leagues/{league_key}/refresh`
  - `GET /api/leagues/{league_key}/board`, `/board/live`, `/readiness`
- Removed: `POST /api/connect`, `GET /api/session`, `_league_id`, `_draft_id`, `_roster_id`, `_DEFAULT_LEAGUE_ID`, `_DEFAULT_DRAFT_ID`, env-var reads.

> This is the largest task. Do the board-endpoint move and the management endpoints together — they share the `_STORE` / `_load_league` swap and can't be half-done with a green suite. Work through the steps in order; the suite goes red mid-task and comes back green at Step 12.

- [ ] **Step 1: Delete the obsolete session module and its test**

```bash
git rm src/ffdo/api/session.py tests/api/test_session.py
```

- [ ] **Step 2: Update the test isolation fixture**

Replace `tests/api/conftest.py` body:

```python
from __future__ import annotations

import pytest

from ffdo.api import app as app_mod
from ffdo.api.store import LeagueStore


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "_STORE", LeagueStore(tmp_path / "ffdo.db"))
```

- [ ] **Step 3: Write the failing tests for the management endpoints**

Replace the whole of `tests/api/test_app.py` with a new module. Key fixtures and tests (this is the full replacement — no `_session()` helper, no `Session` import):

```python
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ffdo.api import app as app_mod
from ffdo.api.app import _TTLCache, _active_only, _load_league, _uncached, create_app
from ffdo.api.store import LeagueStore
from ffdo.domain.models import DiscoveredLeague, PlayerProfile, TrackedLeague
from ffdo.ingest.client import PROJECTIONS, V1


def _tracked(**overrides):
    base = dict(
        league_key="sleeper:L123:2025", provider="sleeper", provider_league_id="L123",
        season=2025, name="Test League", user_id="U1", roster_id=5,
        draft_id="D123", draft_type="auction", draft_status="drafting",
        num_teams=2, budget=200, rounds=3,
        roster_positions=("QB", "RB", "BN"), scoring_settings={"rec": 0.5},
        fmt="redraft", format_override=None, raw_settings={}, is_mock=False,
        tracked_at="2026-09-02T00:00:00+00:00",
        last_refreshed_at="2026-09-02T00:00:00+00:00",
    )
    return TrackedLeague(**{**base, **overrides})


class _FakeSleeperClient:
    def __init__(self, *a, **k): pass
    def get_json(self, url): return [] if "/projections/" in url else {}
    def close(self): pass


def _recording_client(responses):
    calls = []
    class _RecordingClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url):
            calls.append(url)
            for key, value in responses.items():
                if key in url:
                    return value
            return [] if "/projections/" in url else {}
        def close(self): pass
    return _RecordingClient, calls


def _recording_espn_client(responses):
    calls = []
    class _RecordingEspnClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url, extra_headers=None, max_attempts=4):
            calls.append(url)
            for key, value in responses.items():
                if key in url:
                    return value
            return {}
        def close(self): pass
    return _RecordingEspnClient, calls


# --- _load_league ----------------------------------------------------------

def test_load_league_returns_the_tracked_league(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)
    assert _load_league("sleeper:L123:2025").draft_id == "D123"


def test_load_league_404s_for_an_unknown_key(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "_STORE", LeagueStore(tmp_path / "ffdo.db"))
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _load_league("sleeper:nope:2025")
    assert exc.value.status_code == 404


# --- POST /api/providers/connect + discovery ------------------------------

def test_providers_connect_sleeper_stores_credential_and_returns_discovered(monkeypatch):
    discovered = [DiscoveredLeague("sleeper", "L1", 2026, "Home", 12, "", "redraft",
                                   "in_season", False)]
    monkeypatch.setattr("ffdo.ingest.discover.resolve_user_id",
                        lambda sleeper, username: "U1")
    monkeypatch.setattr("ffdo.ingest.discover.list_leagues",
                        lambda sleeper, user_id, season, tracked_keys=frozenset(): discovered)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/providers/connect",
                      json={"provider": "sleeper", "username": "noah", "season": 2026})

    assert res.status_code == 200
    assert [d["provider_league_id"] for d in res.json()["leagues"]] == ["L1"]
    assert app_mod._STORE.get_credential("sleeper").user_identifier == "noah"


def test_providers_connect_espn_strips_cookies_from_the_response(monkeypatch):
    monkeypatch.setattr("ffdo.ingest.espn.discover.list_leagues",
                        lambda espn_s2, swid, season, tracked_keys=frozenset(), transport=None: [])
    client = TestClient(create_app())
    res = client.post("/api/providers/connect", json={
        "provider": "espn", "season": 2026, "espn_s2": "s2", "swid": "{SWID}"})
    assert res.status_code == 200
    assert "s2" not in res.text and "SWID" not in res.json()


# --- POST /api/leagues/track ---------------------------------------------

def test_track_endpoint_persists_a_sleeper_league(monkeypatch):
    monkeypatch.setattr("ffdo.ingest.connect.track",
                        lambda sleeper, league_id, username: _tracked(
                            league_key="sleeper:L9:2026", provider_league_id="L9", season=2026))
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)
    monkeypatch.setattr(
        app_mod, "_STORE",
        __import__("ffdo.api.store", fromlist=["LeagueStore"]).LeagueStore(":memory:")) \
        if False else None  # keep autouse fixture's store

    client = TestClient(create_app())
    # username comes from the stored sleeper credential:
    app_mod._STORE.put_credential(
        __import__("ffdo.domain.models", fromlist=["ProviderCredential"])
        .ProviderCredential("sleeper", "noah", None, None, "t"))
    res = client.post("/api/leagues/track", json={
        "provider": "sleeper", "provider_league_id": "L9", "season": 2026})

    assert res.status_code == 200
    assert app_mod._STORE.get("sleeper:L9:2026") is not None


def test_get_leagues_lists_tracked_with_resolved_format(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="keeper", format_override="dynasty"))
    monkeypatch.setattr(app_mod, "_STORE", store)
    client = TestClient(create_app())
    row = client.get("/api/leagues").json()[0]
    assert row["format"] == "keeper"
    assert row["resolved_format"] == "dynasty"
    assert row["needs_attention"] is False


def test_patch_sets_a_format_override(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)
    client = TestClient(create_app())
    res = client.patch("/api/leagues/sleeper:L123:2025",
                       json={"format_override": "dynasty"})
    assert res.status_code == 200
    assert store.get("sleeper:L123:2025").format_override == "dynasty"


def test_patch_rejects_a_bogus_format(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)
    client = TestClient(create_app())
    res = client.patch("/api/leagues/sleeper:L123:2025",
                       json={"format_override": "banana"})
    assert res.status_code == 422


def test_delete_untracks(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)
    client = TestClient(create_app())
    assert client.delete("/api/leagues/sleeper:L123:2025").status_code == 204
    assert store.get("sleeper:L123:2025") is None
```

> The `test_track_endpoint_persists_a_sleeper_league` sketch above is deliberately ugly to show the credential dependency — clean it up when implementing: build the store via the autouse fixture (`app_mod._STORE`), `put_credential` a sleeper cred, monkeypatch `connect.track`, POST, assert. Keep the `_uncached` / `_TTLCache` / `_active_only` tests from the old file verbatim (they don't touch the store). **Drop** every old test named `test_connect_endpoint_*`, `test_session_endpoint_*`, `test_league_id_*`, `test_draft_id_*`, `test_roster_id_*`, `test_ids_fall_back_*`, `test_league_and_draft_ids_*` — the endpoints and functions they cover are gone.

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_app.py -q`
Expected: FAIL — `ImportError: cannot import name '_load_league'` (and `create_app` still exposes the old routes).

- [ ] **Step 5: Swap the store wiring in `app.py`**

Replace the `SessionStore` import and `_SESSION_STORE` line:

```python
from ffdo.api.store import LeagueStore

_STORE = LeagueStore(Path("data") / "ffdo.db",
                     legacy_session_path=Path("data") / "session.json")
```

Delete `_DEFAULT_LEAGUE_ID`, `_DEFAULT_DRAFT_ID`, `_league_id()`, `_draft_id()`, `_roster_id()`, `_session_public_dict()`, and the `import os` / `re` uses that become dead (keep `re` — `_TRAILING_DRAFT_ID_RE` still used by `_extract_draft_id`; drop `import os`).

Add:

```python
from dataclasses import asdict
from fastapi import HTTPException

from ffdo.domain.models import TrackedLeague


def _load_league(league_key: str) -> TrackedLeague:
    lg = _STORE.get(league_key)
    if lg is None:
        raise HTTPException(status_code=404, detail="League not tracked")
    return lg


def _league_public_dict(lg: TrackedLeague) -> dict:
    data = asdict(lg)
    data["format"] = data.pop("fmt")
    data["resolved_format"] = lg.resolved_format
    return data
```

- [ ] **Step 6: Add the provider-connect + discovery endpoint**

Inside `create_app()`, after the cache setup:

```python
    @app.post("/api/providers/connect")
    def providers_connect(payload: dict) -> dict:
        provider = str(payload.get("provider") or "").strip().lower()
        try:
            season = int(payload["season"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Season must be a year")
        tracked_keys = frozenset(l.league_key for l in _STORE.list())

        if provider == "sleeper":
            username = str(payload.get("username", "")).strip()
            if not username:
                raise HTTPException(status_code=400, detail="Username is required")
            sleeper = client_mod.SleeperClient()
            try:
                user_id = discover_mod.resolve_user_id(sleeper, username)
                leagues = discover_mod.list_leagues(
                    sleeper, user_id, season, tracked_keys=tracked_keys)
            except connect_mod.ConnectError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                sleeper.close()
            _STORE.put_credential(models_mod.ProviderCredential(
                provider="sleeper", user_identifier=username,
                espn_s2=None, swid=None, updated_at=_now_iso()))
        elif provider == "espn":
            espn_s2 = str(payload.get("espn_s2", "")).strip()
            swid = str(payload.get("swid", "")).strip()
            if not espn_s2 or not swid:
                raise HTTPException(status_code=400, detail="espn_s2 and SWID are required")
            try:
                leagues = espn_discover_mod.list_leagues(
                    espn_s2, swid, season, tracked_keys=tracked_keys)
            except espn_connect_mod.ConnectError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            _STORE.put_credential(models_mod.ProviderCredential(
                provider="espn", user_identifier=swid,
                espn_s2=espn_s2, swid=swid, updated_at=_now_iso()))
        else:
            raise HTTPException(status_code=400, detail="Unknown provider")

        return {"leagues": [asdict(d) | {"format": asdict(d).pop("fmt")}
                            for d in leagues]}
```

Add module imports: `from ffdo.ingest import discover as discover_mod`, `from ffdo.ingest.espn import discover as espn_discover_mod`, `from ffdo.domain import models as models_mod`, and a `_now_iso()` helper (`datetime.now(timezone.utc).isoformat()`). Fix the `asdict(d) | {...}` line into a clean helper `_discovered_public(d)` that returns the dict with `fmt`→`format`.

- [ ] **Step 7: Add `GET /api/leagues/discovered`**

```python
    @app.get("/api/leagues/discovered")
    def leagues_discovered(provider: str, season: int) -> dict:
        cred = _STORE.get_credential(provider)
        if cred is None:
            raise HTTPException(status_code=400,
                                detail=f"No stored {provider} credentials -- connect first")
        tracked_keys = frozenset(l.league_key for l in _STORE.list())
        if provider == "sleeper":
            sleeper = client_mod.SleeperClient()
            try:
                leagues = discover_mod.list_leagues(
                    sleeper, discover_mod.resolve_user_id(sleeper, cred.user_identifier),
                    season, tracked_keys=tracked_keys)
            finally:
                sleeper.close()
        elif provider == "espn":
            leagues = espn_discover_mod.list_leagues(
                cred.espn_s2, cred.swid, season, tracked_keys=tracked_keys)
        else:
            raise HTTPException(status_code=400, detail="Unknown provider")
        return {"leagues": [_discovered_public(d) for d in leagues]}
```

- [ ] **Step 8: Add `GET /api/leagues` and `GET /api/leagues/{league_key}`**

```python
    @app.get("/api/leagues")
    def list_leagues_endpoint() -> list[dict]:
        return [
            {
                "league_key": l.league_key, "name": l.name, "provider": l.provider,
                "season": l.season, "format": l.fmt,
                "resolved_format": l.resolved_format,
                "draft_status": l.draft_status, "is_mock": l.is_mock,
                "needs_attention": False,
            }
            for l in _STORE.list()
        ]

    @app.get("/api/leagues/{league_key}")
    def get_league(league_key: str) -> dict:
        return _league_public_dict(_load_league(league_key))
```

> **Route order:** FastAPI matches in declaration order. `GET /api/leagues/discovered` (Step 7) must be declared **before** `GET /api/leagues/{league_key}`, or `discovered` is captured as a `league_key`. Put the literal-path routes first.

- [ ] **Step 9: Add `POST /api/leagues/track`**

```python
    @app.post("/api/leagues/track")
    def track_leagues(payload: dict) -> dict:
        items = payload.get("leagues") or [payload]
        results = []
        for item in items:
            provider = str(item.get("provider") or "").strip().lower()
            pid = str(item.get("provider_league_id", "")).strip()
            try:
                season = int(item["season"])
            except (KeyError, TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Season must be a year")

            if provider in ("sleeper", "sleeper-mock"):
                cred = _STORE.get_credential("sleeper")
                if cred is None:
                    raise HTTPException(status_code=400,
                                        detail="Connect Sleeper before tracking a league")
                sleeper = client_mod.SleeperClient()
                try:
                    if provider == "sleeper-mock":
                        lg = connect_mod.track_mock(sleeper, pid, cred.user_identifier)
                    else:
                        lg = connect_mod.track(sleeper, pid, cred.user_identifier)
                except connect_mod.ConnectError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                finally:
                    sleeper.close()
            elif provider == "espn":
                cred = _STORE.get_credential("espn")
                if cred is None or cred.espn_s2 is None:
                    raise HTTPException(status_code=400,
                                        detail="Connect ESPN before tracking a league")
                sleeper = client_mod.SleeperClient()
                try:
                    profiles, espn_id_index = players_cache.get(
                        lambda: _load_players(sleeper))
                finally:
                    sleeper.close()
                try:
                    lg = espn_connect_mod.track(
                        pid, season, cred.espn_s2, cred.swid, profiles, espn_id_index)
                except espn_connect_mod.ConnectError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
            else:
                raise HTTPException(status_code=400, detail="Unknown provider")

            _STORE.upsert(lg)
            results.append(_league_public_dict(lg))
        return {"leagues": results}
```

- [ ] **Step 10: Add `DELETE`, `PATCH`, `POST /refresh`**

```python
    @app.delete("/api/leagues/{league_key}", status_code=204)
    def untrack_league(league_key: str) -> None:
        _load_league(league_key)
        _STORE.delete(league_key)

    @app.patch("/api/leagues/{league_key}")
    def patch_league(league_key: str, payload: dict) -> dict:
        _load_league(league_key)
        value = payload.get("format_override")
        if value not in (None, "redraft", "keeper", "dynasty"):
            raise HTTPException(status_code=422, detail="Invalid format_override")
        _STORE.set_format_override(league_key, value)
        return _league_public_dict(_load_league(league_key))

    @app.post("/api/leagues/{league_key}/refresh")
    def refresh_league(league_key: str) -> dict:
        lg = _load_league(league_key)
        if lg.provider in ("sleeper", "sleeper-mock"):
            cred = _STORE.get_credential("sleeper")
            sleeper = client_mod.SleeperClient()
            try:
                if lg.provider == "sleeper-mock":
                    fresh = connect_mod.track_mock(sleeper, lg.provider_league_id,
                                                   cred.user_identifier)
                else:
                    fresh = connect_mod.track(sleeper, lg.provider_league_id,
                                              cred.user_identifier)
            except connect_mod.ConnectError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                sleeper.close()
        else:
            cred = _STORE.get_credential("espn")
            if cred is None or cred.espn_s2 is None:
                raise HTTPException(status_code=400,
                                    detail="Your ESPN cookies look expired -- reconnect ESPN")
            sleeper = client_mod.SleeperClient()
            try:
                profiles, espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            finally:
                sleeper.close()
            try:
                fresh = espn_connect_mod.track(lg.provider_league_id, lg.season,
                                               cred.espn_s2, cred.swid,
                                               profiles, espn_id_index)
            except espn_connect_mod.ConnectError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        _STORE.upsert(fresh)
        return _league_public_dict(_load_league(league_key))
```

- [ ] **Step 11: Move the three board endpoints under `/api/leagues/{league_key}`**

Change the decorators and signatures:

```python
    @app.get("/api/leagues/{league_key}/readiness")
    def get_readiness(league_key: str) -> dict:
        lg = _load_league(league_key)
        projections_synced = _projections_cache_for(lg.season).has_value()
        return {
            "league_draft": "synced",
            "players": "synced" if players_cache.has_value() else "pending",
            "projections": "synced" if projections_synced else "pending",
        }

    @app.get("/api/leagues/{league_key}/board/live")
    def get_board_live(league_key: str) -> dict:
        session = _load_league(league_key)
        ...  # body unchanged except: `session` is now a TrackedLeague from
             # _load_league (not _SESSION_STORE.get()), `provider = session.provider`,
             # and the ESPN branch reads espn_s2/swid from
             # `_STORE.get_credential("espn")` instead of `session.espn_s2`.

    @app.get("/api/leagues/{league_key}/board")
    def get_board(league_key: str) -> dict:
        session = _load_league(league_key)
        ...
```

Concrete edits to the two board bodies:
- Replace `session = _SESSION_STORE.get()` → `session = _load_league(league_key)` (top of each).
- Replace `provider = session.provider if session is not None else "sleeper"` → `provider = session.provider`.
- Delete the `if session is None or session.espn_s2 is None ...` guards; instead, in the ESPN branch: `cred = _STORE.get_credential("espn")` and `if cred is None or cred.espn_s2 is None: raise HTTPException(400, "Your ESPN cookies look expired -- reconnect ESPN")`. Use `cred.espn_s2` / `cred.swid` where the code currently uses `session.espn_s2` / `session.swid`.
- Replace `session.league_id` → `session.provider_league_id` in the ESPN URL builds; `session.season` unchanged.
- Sleeper branch: replace `league_id = _league_id()` / `draft_id = _draft_id()` with `is_mock = session.is_mock`, `draft_id = session.draft_id`, and for the non-mock path use `league_id = session.provider_league_id`. For the mock path (`is_mock`), keep the existing `mock_draft_mod` logic but source `draft_id` from `session.draft_id`.
- Replace `roster_id = _roster_id()` → `roster_id = session.roster_id` (non-mock). The mock branch's live `mock_draft_mod.resolve_roster_id(draft_meta, session.user_id)` stays — `session` is now the `TrackedLeague`, which still has `.user_id`.
- Delete the `session = _SESSION_STORE.get()` re-fetch inside the mock branch (line ~515) — `session` is already the loaded league.
- After building `state` in **both** board endpoints, add: `_STORE.touch_status(league_key, state.status)`.
- The mock `scoring_type` drift → 400 handling (`mock_draft_mod.MockDraftError`) is unchanged.
- `get_board`'s final `board["is_mock"] = is_mock` unchanged.
- Delete `_warm_caches`'s call sites? No — `_warm_caches` was invoked from `/api/connect` (now removed). Call it from `POST /api/leagues/track` via `BackgroundTasks` after `_STORE.upsert(lg)` for the first tracked league, passing `lg.season, lg.provider_league_id, lg.provider, cred.espn_s2, cred.swid`. Keep `_warm_caches` otherwise as-is.

- [ ] **Step 12: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS. Fix any `test_board.py` / `test_snake_board.py` breakage — those files unit-test `board.build_*` directly and should be unaffected; only `test_board.py::test_healthz_returns_ok` touches `create_app` and must still pass. If `tests/api/test_app.py` board-endpoint tests are still referenced from the old file, port them now (see Task 11).

- [ ] **Step 13: Commit**

```bash
git add -A
git commit -m "refactor: league-scoped API routing and league-management endpoints"
```

---

## Task 11: Port the board-endpoint integration tests to league-scoped routes

**Files:**
- Modify: `tests/api/test_app.py`

**Interfaces:**
- Consumes: the endpoints from Task 10.

> The old `test_app.py` had ~10 `GET /api/board` / `/api/board/live` integration tests (real-league, mock, ESPN, DEF/K, snake, scoring drift). Port the ones that still describe real behavior; each now needs a tracked league in `_STORE` and hits `/api/leagues/{key}/board`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_app.py` (using the `_tracked`, `_recording_client`, `_recording_espn_client` helpers already in the file):

```python
_BOARD_REAL_LEAGUE_RAW = {
    "league_id": "L123", "season": "2025",
    "settings": {"num_teams": 2, "budget": 200},
    "roster_positions": ["QB", "RB", "BN"],
    "scoring_settings": {"rush_yd": 0.1, "rush_td": 6.0},
    "name": "Board Test League", "status": "drafting",
}
_BOARD_DRAFT_RAW = {"draft_id": "D123", "type": "auction", "status": "drafting",
                    "settings": {"teams": 2, "rounds": 3, "budget": 200}}
_BOARD_PLAYERS_RAW = {"P1": {"first_name": "Test", "last_name": "Runner", "position": "RB",
                             "team": "AAA", "age": 25, "years_exp": 3, "active": True}}
_BOARD_PROJECTIONS_RAW = [{"player_id": "P1",
    "last_modified": int(datetime(2025, 8, 1, tzinfo=timezone.utc).timestamp() * 1000),
    "stats": {"rush_yd": 1000.0, "rush_td": 10.0}}]


def test_board_endpoint_scores_a_player_for_a_tracked_sleeper_league(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())  # sleeper:L123:2025
    monkeypatch.setattr(app_mod, "_STORE", store)

    FakeClient, calls = _recording_client({
        f"{V1}/draft/D123/picks": [],
        f"{V1}/draft/D123": _BOARD_DRAFT_RAW,
        f"{V1}/league/L123/rosters": [],
        f"{V1}/league/L123/users": [],
        f"{V1}/league/L123": _BOARD_REAL_LEAGUE_RAW,
        f"{V1}/players/nfl": _BOARD_PLAYERS_RAW,
        f"{PROJECTIONS}/2025": _BOARD_PROJECTIONS_RAW,
    })
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L123:2025/board")

    assert res.status_code == 200
    body = res.json()
    assert body["is_mock"] is False
    assert "P1" in {p["player_id"] for p in body["players"]}


def test_board_endpoint_404s_for_an_untracked_league(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "_STORE", LeagueStore(tmp_path / "ffdo.db"))
    client = TestClient(create_app())
    assert client.get("/api/leagues/sleeper:ghost:2025/board").status_code == 404


def test_board_poll_updates_the_stored_draft_status(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(draft_status="drafting"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    complete_draft = {**_BOARD_DRAFT_RAW, "status": "complete"}
    FakeClient, _ = _recording_client({
        f"{V1}/draft/D123/picks": [],
        f"{V1}/draft/D123": complete_draft,
        f"{V1}/league/L123/rosters": [],
        f"{V1}/league/L123/users": [],
        f"{V1}/league/L123": {**_BOARD_REAL_LEAGUE_RAW, "status": "complete"},
        f"{V1}/players/nfl": _BOARD_PLAYERS_RAW,
        f"{PROJECTIONS}/2025": _BOARD_PROJECTIONS_RAW,
    })
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    client = TestClient(create_app())
    client.get("/api/leagues/sleeper:L123:2025/board")

    assert store.get("sleeper:L123:2025").draft_status == "complete"


def test_board_live_espn_reads_credentials_from_the_store(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(
        league_key="espn:1882997948:2026", provider="espn",
        provider_league_id="1882997948", season=2026, draft_id="1882997948",
        draft_type="snake", roster_positions=("QB", "RB", "BN")))
    store.put_credential(__import__("ffdo.domain.models", fromlist=["ProviderCredential"])
                         .ProviderCredential("espn", "{SWID}", "s2value", "{SWID}", "t"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    espn_raw = {"id": 1882997948,
                "settings": {"size": 2, "draftSettings": {"type": "SNAKE"}},
                "draftDetail": {"drafted": False, "inProgress": True, "picks": []}}
    FakeEspnClient, espn_calls = _recording_espn_client({
        "players?view=kona_player_info": [],
        "leagues/1882997948": espn_raw,
    })
    monkeypatch.setattr("ffdo.ingest.espn.client.EspnClient", FakeEspnClient)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.get("/api/leagues/espn:1882997948:2026/board/live")

    assert res.status_code == 200
    assert res.json() == {"live_nomination": None, "picks_made": 0}
    assert any("leagues/1882997948" in c for c in espn_calls)
```

Port `test_get_board_mock_mode_*` (backfill + live roster-id, single-fetch, snake smoke, scoring drift → 400) the same way: `store.upsert(_tracked(league_key="sleeper-mock:D999:2026", provider="sleeper-mock", provider_league_id="D999", is_mock=True, ...))` and GET `/api/leagues/sleeper-mock:D999:2026/board`. Keep the cross-league scoring regression guard: two tracked leagues with different `scoring_settings` produce different `vor` for the same stat line.

- [ ] **Step 2: Run tests to verify they fail (then pass)**

Run: `uv run pytest tests/api/test_app.py -q`
Expected: initially FAIL where a ported test exposes a wiring gap in Task 10; fix in `app.py`; then PASS.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/api/test_app.py src/ffdo/api/app.py
git commit -m "test: port board-endpoint integration tests to league-scoped routes"
```

---

## Task 12: Frontend shell — router, switcher, connect/discovery screen

**Files:**
- Create: `src/ffdo/web/index.html`, `src/ffdo/web/app.js`, `src/ffdo/web/app.css`
- Delete: `src/ffdo/web/main.js`, `src/ffdo/web/main.css`
- Keep: the existing `src/ffdo/web/index.html` content is replaced (it currently is the connect page).

> No automated frontend tests exist in this repo; this task is verified manually via the `run` skill. The `StaticFiles` mounts in `app.py` are unchanged: `web/` at `/`, `web/board/` at `/board`.

- [ ] **Step 1: Replace `web/index.html` with the shell**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FFDO</title>
<link rel="stylesheet" href="app.css">
</head>
<body>
<div id="shell">
  <header id="switcher-bar" hidden>
    <span class="brand">FFDO</span>
    <select id="league-switcher"></select>
    <a href="#/connect" class="add-leagues">+ Add leagues</a>
  </header>
  <main id="view"></main>
</div>
<script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write `web/app.js`**

A hash router with three views. Full file (no framework):

```javascript
const view = document.getElementById("view");
const bar = document.getElementById("switcher-bar");
const switcher = document.getElementById("league-switcher");

const LAST_LEAGUE_KEY = "ffdo:lastLeagueKey";

async function getJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

async function loadLeagues() {
  try { return await getJSON("/api/leagues"); }
  catch { return []; }
}

function renderSwitcher(leagues, activeKey) {
  if (!leagues.length) { bar.hidden = true; return; }
  bar.hidden = false;
  switcher.innerHTML = leagues.map(l =>
    `<option value="${l.league_key}"${l.league_key === activeKey ? " selected" : ""}>` +
    `${l.name} — ${l.resolved_format}</option>`).join("");
  switcher.onchange = () => { location.hash = `#/league/${switcher.value}`; };
}

async function route() {
  const hash = location.hash || "#/";
  const leagues = await loadLeagues();

  if (hash === "#/connect") { renderSwitcher(leagues, null); return renderConnect(leagues); }

  const m = hash.match(/^#\/league\/(.+)$/);
  if (m) {
    const key = decodeURIComponent(m[1]);
    try { localStorage.setItem(LAST_LEAGUE_KEY, key); } catch {}
    renderSwitcher(leagues, key);
    return renderLeague(key);
  }

  // "#/" — go to last-viewed or first league, else connect
  if (!leagues.length) { location.hash = "#/connect"; return; }
  let last = null;
  try { last = localStorage.getItem(LAST_LEAGUE_KEY); } catch {}
  const target = leagues.find(l => l.league_key === last) || leagues[0];
  location.hash = `#/league/${target.league_key}`;
}

// --- connect / discovery -------------------------------------------------

function renderConnect(tracked) {
  view.innerHTML = `
    <section class="card">
      <h1>Connect a provider</h1>
      <div class="provider-toggle">
        <button data-provider="sleeper" class="on">Sleeper</button>
        <button data-provider="espn">ESPN</button>
      </div>
      <form id="connect-form">
        <div data-fields="sleeper">
          <label>Sleeper username <input name="username" autocomplete="off"></label>
        </div>
        <div data-fields="espn" hidden>
          <label>espn_s2 <input name="espn_s2" type="password" autocomplete="off"></label>
          <label>SWID <input name="swid" type="password" autocomplete="off"></label>
        </div>
        <label>Season <input name="season" value="${new Date().getFullYear()}"></label>
        <p class="error" id="connect-error" hidden></p>
        <button type="submit">Find my leagues</button>
      </form>
      <div id="discovered"></div>
    </section>`;

  let provider = "sleeper";
  view.querySelectorAll(".provider-toggle button").forEach(b => b.onclick = () => {
    provider = b.dataset.provider;
    view.querySelectorAll(".provider-toggle button").forEach(x => x.classList.toggle("on", x === b));
    view.querySelector('[data-fields="sleeper"]').hidden = provider !== "sleeper";
    view.querySelector('[data-fields="espn"]').hidden = provider !== "espn";
  });

  view.querySelector("#connect-form").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const payload = { provider, season: Number(fd.get("season")) };
    if (provider === "sleeper") payload.username = fd.get("username");
    else { payload.espn_s2 = fd.get("espn_s2"); payload.swid = fd.get("swid"); }
    const err = view.querySelector("#connect-error");
    err.hidden = true;
    try {
      const { leagues } = await getJSON("/api/providers/connect",
        { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify(payload) });
      renderDiscovered(leagues, payload.season);
    } catch (ex) { err.textContent = ex.message; err.hidden = false; }
  };
}

function renderDiscovered(leagues, season) {
  const box = view.querySelector("#discovered");
  if (!leagues.length) { box.innerHTML = "<p>No leagues found for that season.</p>"; return; }
  box.innerHTML = `
    <h2>Leagues found</h2>
    <ul class="discovered-list">
      ${leagues.map((l, i) => `
        <li>
          <label>
            <input type="checkbox" data-i="${i}" ${l.already_tracked ? "checked disabled" : "checked"}>
            <span class="lg-name">${l.name}</span>
            <span class="badge">${l.format}</span>
            <span class="badge muted">${l.draft_status || "—"}</span>
          </label>
        </li>`).join("")}
    </ul>
    <button id="track-selected">Track selected leagues</button>`;
  box.querySelector("#track-selected").onclick = async () => {
    const picks = [...box.querySelectorAll("input[type=checkbox]:checked:not(:disabled)")]
      .map(cb => leagues[Number(cb.dataset.i)])
      .map(l => ({ provider: l.provider, provider_league_id: l.provider_league_id,
                   season: l.season }));
    if (!picks.length) return;
    const { leagues: tracked } = await getJSON("/api/leagues/track",
      { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ leagues: picks }) });
    location.hash = `#/league/${tracked[0].league_key}`;
  };
}

// --- league detail ------------------------------------------------------

async function renderLeague(key) {
  let meta;
  try { meta = await getJSON(`/api/leagues/${encodeURIComponent(key)}`); }
  catch (ex) { view.innerHTML = `<p class="error">${ex.message}</p>`; return; }

  view.innerHTML = `<div id="league-body">Loading ${meta.name}…</div>`;
  // board.js (loaded by the board view) decides board-vs-season-mode from
  // the live board poll; see Task 13.
  window.__ffdoLeagueKey = key;
  window.__ffdoLeagueMeta = meta;
  const mod = await import("./board/board.js");
  if (mod.mount) mod.mount(document.getElementById("league-body"), key, meta);
}

window.addEventListener("hashchange", route);
route();
```

- [ ] **Step 3: Write `web/app.css`**

Port the palette/typography from the current `web/main.css` (dark oklch theme, IBM Plex fonts) into `app.css`, plus styles for `#switcher-bar`, `.provider-toggle`, `.discovered-list`, `.badge`, `.card`, `.error`. Keep it minimal — it only needs to be legible; the design canvas is the visual reference, not a pixel target for this task.

- [ ] **Step 4: Delete the old connect page assets**

```bash
git rm src/ffdo/web/main.js src/ffdo/web/main.css
```

- [ ] **Step 5: Manual verification**

Use the `run` skill: start the server, seed a dev league (`uv run python scripts/seed_dev_league.py` — from Task 13; if running this task first, `POST /api/providers/connect` + `/api/leagues/track` by hand via the UI). Verify: empty state redirects to `#/connect`; Sleeper connect lists leagues; ticking + "Track selected" navigates to `#/league/...`; the switcher appears and moves between leagues.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: hash-routed app shell with league switcher and discovery screen"
```

---

## Task 13: Frontend — league-scoped board fetches and the adaptive league screen

**Files:**
- Modify: `src/ffdo/web/board/board.js`
- Modify: `src/ffdo/web/board/index.html` (only if it must change to be embeddable; otherwise leave)

**Interfaces:**
- Consumes: `window.__ffdoLeagueKey`, `window.__ffdoLeagueMeta` set by `app.js` (Task 12).
- Produces: `board.js` exports `mount(container, leagueKey, meta)`.

- [ ] **Step 1: Make `board.js` league-scoped and embeddable**

- Wrap the current top-level bootstrap (the `setInterval` calls, initial `refresh()`) in an exported `mount(container, leagueKey, meta)` function. Keep the module-level `state` object.
- Add `const LEAGUE_KEY = leagueKey;` inside `mount` (or a module-level `let leagueKey`), and change the three fetches:
  - `fetch("/api/board")` → `fetch(\`/api/leagues/${encodeURIComponent(leagueKey)}/board\`)`
  - `fetch("/api/board/live")` → `fetch(\`/api/leagues/${encodeURIComponent(leagueKey)}/board/live\`)`
  - any `fetch("/api/readiness")` → the league-scoped path.
- In `refresh()`, after `state.data = await res.json();`, branch on draft status: the board response carries `format` (`auction`/`snake`) and `picks_made`; add a check on the live draft status. Simplest signal available: the board response does **not** currently include the raw draft status. **Add** `"draft_status": state.status` to the return dict of both `build_auction_board` and `build_snake_board` in `src/ffdo/api/board.py` (one line each), plus to `get_board_live`'s response in `app.py` (`"draft_status": state.status`). Then in `board.js`:

```javascript
if (state.data.draft_status === "complete") {
  renderSeasonMode(container, window.__ffdoLeagueMeta);
  clearInterval(state.pollId); clearInterval(state.livePollId);
  return;
}
```

- [ ] **Step 2: Add `renderSeasonMode`**

```javascript
function renderSeasonMode(container, meta) {
  container.innerHTML = `
    <section class="card season-mode">
      <span class="badge">DRAFT COMPLETE</span>
      <h1>Season mode</h1>
      <p>${meta.name} has drafted. Roster analysis, standings, weekly lineups
         and waivers arrive in upcoming releases — each reads this league's
         own scoring and format.</p>
      <div class="format-override">
        <label>Format
          <select id="fmt-override">
            ${["redraft", "keeper", "dynasty"].map(f =>
              `<option value="${f}"${meta.resolved_format === f ? " selected" : ""}>${f}</option>`).join("")}
          </select>
        </label>
      </div>
    </section>`;
  container.querySelector("#fmt-override").onchange = (e) => {
    fetch(`/api/leagues/${encodeURIComponent(meta.league_key)}`, {
      method: "PATCH", headers: { "content-type": "application/json" },
      body: JSON.stringify({ format_override: e.target.value }),
    });
  };
}
```

- [ ] **Step 3: Add the failing test for the board.py change**

In `tests/api/test_board.py`:

```python
def test_auction_board_includes_the_live_draft_status():
    league = _league()
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "complete",
                         "settings": {"teams": 12, "rounds": 14, "budget": 200}}, [])
    out = board.build_auction_board(league, state, {}, {})
    assert out["draft_status"] == "complete"


def test_snake_board_includes_the_live_draft_status():
    league = _league()
    state = draft.parse({"draft_id": "d", "type": "snake", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 14}}, [])
    out = board.build_snake_board(league, state, {}, {}, {})
    assert out["draft_status"] == "drafting"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/api/test_board.py -q -k draft_status`
Expected: FAIL with `KeyError: 'draft_status'`.

- [ ] **Step 5: Implement the board.py change**

Add `"draft_status": state.status,` to the returned dict in `build_auction_board` and `build_snake_board` (`src/ffdo/api/board.py`), and `"draft_status": state.status` to `get_board_live`'s return in `app.py`.

- [ ] **Step 6: Run tests**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Manual verification via the `run` skill**

Track a pre-draft league → `#/league/{key}` shows the existing board. Track (or `POST /refresh`) a completed league → the same route shows "Season mode" with a working format dropdown. Confirm the switcher stays visible on both.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: adaptive league screen — draft board pre-draft, season-mode placeholder after"
```

---

## Task 14: Dev seed script, gitignore, README

**Files:**
- Create: `scripts/seed_dev_league.py`
- Modify: `.gitignore`, `README.md`

**Interfaces:**
- Consumes: `LeagueStore`, `connect.track`, `ProviderCredential`, `SleeperClient`.

- [ ] **Step 1: Write `scripts/seed_dev_league.py`**

```python
"""Tracks the pinned 2026 auction league into a local data/ffdo.db, so a
fresh clone has something on the board without going through the connect
UI. Replaces the removed FFDO_LEAGUE_ID / FFDO_DRAFT_ID env-var defaults.

Usage: uv run python scripts/seed_dev_league.py [SLEEPER_USERNAME]
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from ffdo.api.store import LeagueStore
from ffdo.domain.models import ProviderCredential
from ffdo.ingest import connect
from ffdo.ingest.client import SleeperClient

PINNED_LEAGUE_ID = "1315881559957458944"


def main() -> None:
    username = sys.argv[1] if len(sys.argv) > 1 else "noahdschroeder"
    store = LeagueStore(Path("data") / "ffdo.db")
    sleeper = SleeperClient()
    try:
        lg = connect.track(sleeper, PINNED_LEAGUE_ID, username)
    finally:
        sleeper.close()
    store.put_credential(ProviderCredential(
        provider="sleeper", user_identifier=username, espn_s2=None, swid=None,
        updated_at=datetime.now(timezone.utc).isoformat()))
    store.upsert(lg)
    print(f"tracked {lg.name} ({lg.league_key})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Update `.gitignore`**

Add under the existing `data/session.json` line:

```
data/ffdo.db
data/session.json.migrated
```

- [ ] **Step 3: Update `README.md`**

Replace the env-var paragraph (lines ~25-28) with:

```markdown
Then open `http://localhost:8000` in a browser and connect a provider
(Sleeper username, or ESPN `espn_s2` / `SWID` cookies), then pick which
leagues to track.

For a zero-config dev league, run `uv run python scripts/seed_dev_league.py`
to track the pinned 2026 auction league into `data/ffdo.db`.
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/seed_dev_league.py .gitignore README.md
git commit -m "chore: dev seed script, gitignore ffdo.db, README connect flow"
```

---

## Self-Review

**1. Spec coverage:**

| Spec section | Task(s) |
|---|---|
| §3.1 `LeagueStore` tables + methods | 2 |
| §3.2 domain types (`TrackedLeague`/`ProviderCredential`/`DiscoveredLeague`, `resolved_format`) | 1 |
| §3.3 `session.json` migration | 3 |
| §4.1 Sleeper `list_leagues` + `resolve_user_id` | 6 |
| §4.1 ESPN `list_leagues` (fan API, `[]` fallback) | 7 |
| §4.2 `detect_format` (Sleeper + ESPN) | 4, 5 |
| §4.3 `resolve` → `track` returning `TrackedLeague` | 8, 9 |
| §5.1 `POST /api/providers/connect`, `GET /api/leagues/discovered`, `GET /api/leagues`, `GET /api/leagues/{key}`, `POST /api/leagues/track`, `DELETE`, `PATCH`, `POST /refresh` | 10 |
| §5.2 board endpoints league-scoped + `touch_status` on poll | 10, 11, 13 |
| §5.3 removals (`/api/connect`, `/api/session`, env vars, `_SESSION_STORE`→`_STORE`) | 10 |
| §5.4 error handling (404 unknown key, 400 expired cookies, 422 bad override, 502 provider down) | 7, 10 |
| §6.1 connect/discovery screen | 12 |
| §6.2 persistent switcher + localStorage last-viewed | 12 |
| §6.3 adaptive league screen (live status decides board vs season mode) + format override | 13 |
| §6.4 delete `main.js`/`main.css` | 12 |
| §7 tests (`test_store`, `test_discover` ×2, `detect_format`, `test_track`, ported board tests) | 2–11 |
| §8 gitignore, seed script, README | 14 |

> Gap accepted: §5.4's "provider HTTP failure during discovery → 502" is only partially covered — Task 7's ESPN discovery swallows non-auth errors to `[]` per spec §4.1, and Task 6's Sleeper discovery lets `httpx` errors propagate as 500 (FastAPI default) rather than an explicit 502. If an explicit 502 is wanted for the Sleeper path, wrap the `discover.list_leagues` call in `providers_connect` with `except httpx.HTTPError → HTTPException(502, ...)`. Left as a one-line note rather than a task step because the spec's own §4.1 makes the fallback behavior the priority.

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". The ESPN fan-API parser (Task 7) is explicitly flagged as shape-unverified with a defensive parser and a pinned test fixture — that is a documented risk, not a placeholder. Task 10 Step 3's `test_track_endpoint_persists_a_sleeper_league` sketch is called out as "clean up when implementing" with the exact shape to produce.

**3. Type consistency:**
- `TrackedLeague` field is `fmt`; SQLite column and JSON key are `format`. Mapping points: `store.py` `_row_to_league`/`upsert` (Task 2), `_league_public_dict` and `list_leagues_endpoint` (Task 10), `_discovered_public` (Task 10). `DiscoveredLeague.fmt` → JSON `format` in `renderDiscovered` consumes `l.format` (Task 12). Consistent.
- `make_league_key(provider, provider_league_id, season)` — same arg order in Tasks 1, 6, 7, 8, 9.
- `LeagueStore.upsert` preserves `format_override`/`tracked_at` — asserted in Task 2, relied on by Task 10's `/refresh` and `PATCH`.
- `connect.track(sleeper, league_id, username)` / `connect.track_mock(sleeper, draft_id, username)` / `espn.connect.track(league_id, season, espn_s2, swid, profiles, espn_id_index)` — signatures fixed in Tasks 8/9, called with those exact args in Task 10 Steps 9–11.
- `board.js` `mount(container, leagueKey, meta)` — defined Task 13, called Task 12 Step 2 `renderLeague`.
- `_load_league` raises `HTTPException(404)` — Task 10; relied on by every board test's 404 case in Task 11.

**4. Additional spec requirement with no task:** none found. `needs_attention: false` placeholder is in Task 10 Step 8. `_warm_caches` re-wiring (orphaned when `/api/connect` was removed) is handled in Task 10 Step 11.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-02-multi-league-foundation.md`. Two execution options:

**1. Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
