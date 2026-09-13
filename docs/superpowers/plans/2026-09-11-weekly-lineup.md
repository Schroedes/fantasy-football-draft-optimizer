# Weekly Optimal Lineup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `[Lineup | Power ranking | Draft capital]` third tab to the season screen: this week's optimal starting lineup vs. what you actually have set, lock-aware (a player whose NFL game already started can't be suggested for a swap), with a decision ledger that records the recommendation and later resolves whether you followed it.

**Architecture:** Two new Sleeper-only ingest parsers (`weekly_projections`, `schedule`) feed a new engine module `weekly_lineup` that reuses `engine/vor.compute` and the same greedy dedicated-then-FLEX algorithm `engine/replacement.greedy_fill_slots` already implements, exposed with slot-index attribution so a diff against the provider's real positionally-aligned starters array is possible. `GET /api/leagues/{key}/lineup` assembles the diff and writes/resolves a row in a new SQLite-backed `LineupLedger`. `web/season/season.js` gains a lazily-fetched Lineup tab, shown first.

**Tech Stack:** Python 3.12, FastAPI, `httpx` (`MockTransport` in tests), stdlib `sqlite3` (no ORM), pytest. Frontend is dependency-free vanilla JS/CSS, same as the rest of `web/`.

**Spec:** `docs/superpowers/specs/2026-09-11-weekly-lineup-design.md`

## Global Constraints

- **Python** `>=3.12`. **No new runtime dependencies.**
- **Stacked PR:** this branch (`claude/weekly-lineup`) is stacked on `claude/roster-standings-view` (PR #25). All of #2's types, ingest, and the `/season` endpoint exist. Base every task on that branch's HEAD.
- **Layer rule:** nothing above `ffdo/ingest/` sees provider JSON keys in raw form; adapters translate at that boundary. `ffdo/domain/models.py` and `ffdo/engine/*` have no I/O.
- **Frozen dataclasses:** new domain types are `@dataclass(frozen=True, slots=True)`.
- **Slot indexing is `league.starting_slots`, never `league.roster_positions`.** `roster_positions` includes `"BN"` entries; a starting-lineup solve/diff only ever concerns the non-`"BN"` slots. `starting_slots` is an existing `TrackedLeague` property (`tuple(p for p in roster_positions if p != "BN")`) — do not reimplement it.
- **`engine.roster.team_lineup` and `engine.replacement.greedy_fill_slots` are not modified.** The new slot-aware solver in `engine/weekly_lineup.py` is additive code that reproduces their algorithm with index attribution, not a change to code #2 and draft day already depend on.
- **`RosterEntry.starter_ids` (from #2) is NOT positionally aligned** — it's a compacted set with Sleeper's `"0"` empty-slot placeholders already filtered out. A slot-by-slot diff needs the raw, positionally-aligned array back; that's what the new `ingest/rosters.py::raw_starters` is for. Do not try to reuse `starter_ids` for the diff.
- **Sleeper-only.** `weekly_projections` and `schedule` are Sleeper-specific, undocumented-but-confirmed-live endpoints. `GET /lineup` returns 400 for any `provider != "sleeper"` tracked league — this is a plan-level ruling, not a spec ambiguity: the spec's ingest section only ever defined Sleeper paths, so an ESPN league silently attempting this with no ESPN data source would be worse than an honest 400.
- **A schedule-fetch failure degrades gracefully; a weekly-projections failure does not.** `ingest/sleeper/schedule.py` is unofficial and undocumented — if it errors, `GET /lineup` logs a warning and treats nothing as locked (never raises past the endpoint). `ingest/sleeper/weekly_projections.py` failing is treated like any other provider outage: `except (httpx.HTTPError, RuntimeError)` → 502, same as `/season`.
- **"Locked" and "week_locked" both mean "every game's status is no longer `pre_game`"** — not "every game is `complete`". A provider locks a team's actual starters at that team's own kickoff, not at the game's final whistle, so grading the ledger (§ Task 8) or reporting `week_locked` at "started" rather than "finished" is the correct point at which no further lineup action is possible. This refines the spec's §5 prose ("checks whether every game... is now `complete`") to the precise, correct criterion; the intent (grade once nothing more can change) is unchanged.
- **Error contract:** 404 unknown league (`_load_league`); 400 for a non-Sleeper provider (see above); 502 on `(httpx.HTTPError, RuntimeError)` from a provider call that isn't the schedule fetch.
- **Test isolation:** every `tests/api/` test runs against a `tmp_path` DB; no test hits the network or a real `data/` file.
- **Commit after every task.** Prefix: `feat:` / `refactor:` / `test:` / `chore:`.
- **`uv run pytest` green at the end of every task.** The one acceptable warning is the pre-existing `StarletteDeprecationWarning` about `starlette.testclient`.

---

## File Structure

**Created:**
- `src/ffdo/ingest/sleeper/weekly_projections.py` — `fetch(sleeper, season, week) -> dict[str, WeeklyProjection]`.
- `src/ffdo/ingest/sleeper/schedule.py` — `week_games(sleeper, season, week) -> list[dict]`, `locked_teams(games) -> frozenset[str]`, `bye_teams(games, all_teams) -> frozenset[str]`, `week_locked(games) -> bool`.
- `src/ffdo/engine/weekly_lineup.py` — `weekly_value(...) -> dict[str, ValuedPlayer]`, `optimal_slots(valued, league) -> dict[int, str | None]`, `diff(...) -> list[SlotDiff]`.
- `src/ffdo/api/lineup_ledger.py` — `LineupLedger` (SQLite-backed decision ledger), `LineupRecord`.
- Tests: `tests/ingest/sleeper/test_weekly_projections.py`, `tests/ingest/sleeper/test_schedule.py`, `tests/engine/test_weekly_lineup.py`, `tests/api/test_lineup_ledger.py`, `tests/api/test_lineup_endpoint.py`.

**Modified:**
- `src/ffdo/domain/models.py` — add `WeeklyProjection`, `SlotDiff`.
- `src/ffdo/domain/constants.py` — add `INJURY_OUT_STATUSES` (promoted from `engine/ros_value.py`'s private `_INJURY_OUT`).
- `src/ffdo/engine/ros_value.py` — import and use `INJURY_OUT_STATUSES`; drop the private `_INJURY_OUT` constant. Behavior unchanged.
- `src/ffdo/ingest/client.py` — add `SCHEDULE = "https://api.sleeper.app/schedule/nfl/regular"` alongside the existing `V1`/`PROJECTIONS`.
- `src/ffdo/ingest/rosters.py` — add `raw_starters(sleeper, league_id, roster_id) -> tuple[str | None, ...]`.
- `src/ffdo/api/app.py` — new caches `_weekly_proj_cache_for(season, week)`, `_schedule_cache_for(season, week)`; module-level `_LINEUP_LEDGER`; `GET /api/leagues/{league_key}/lineup`.
- `src/ffdo/web/season/season.js` — third tab `Lineup`, shown first, lazily fetched.
- `src/ffdo/web/season/season.css` — lineup row/diff styles.
- `README.md` — one line noting the Lineup tab.

**Deleted:** none.

---

## Task 1: Domain types + `INJURY_OUT_STATUSES` promotion

**Files:**
- Modify: `src/ffdo/domain/models.py`
- Modify: `src/ffdo/domain/constants.py`
- Modify: `src/ffdo/engine/ros_value.py`
- Test: `tests/domain/test_models.py`, `tests/domain/test_constants.py` (create if it doesn't exist), `tests/engine/test_ros_value.py` (no new test needed — just confirm it still passes)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `WeeklyProjection(player_id: str, season: int, week: int, stats: Mapping[str, float])` — frozen, slots.
  - `SlotDiff(slot_index: int, slot_label: str, status: str, current_player_id: str | None, optimal_player_id: str | None, delta: float)` — frozen, slots.
  - `constants.INJURY_OUT_STATUSES: frozenset[str]` = `{"IR", "PUP", "Out", "Sus"}`.

- [ ] **Step 1: Write the failing test**

Add to `tests/domain/test_models.py`:

```python
from ffdo.domain.models import SlotDiff, WeeklyProjection


def test_weekly_projection_constructs():
    p = WeeklyProjection(player_id="p1", season=2026, week=10,
                         stats={"pass_yd": 260.0})
    assert p.week == 10 and p.stats["pass_yd"] == 260.0


def test_slot_diff_constructs():
    d = SlotDiff(slot_index=2, slot_label="FLEX", status="suggested_swap",
                current_player_id="p1", optimal_player_id="p2", delta=5.2)
    assert d.status == "suggested_swap" and d.delta == 5.2
```

Create `tests/domain/test_constants.py` (or add to it if a file with a different name already covers `domain/constants.py` — check `tests/domain/` first; if none exists, create this one):

```python
from ffdo.domain.constants import INJURY_OUT_STATUSES


def test_injury_out_statuses_covers_the_known_hard_outs():
    assert INJURY_OUT_STATUSES == {"IR", "PUP", "Out", "Sus"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/domain/test_models.py tests/domain/test_constants.py -q -k "weekly_projection or slot_diff or injury_out"`
Expected: FAIL — `ImportError: cannot import name 'WeeklyProjection'` (and similarly for `SlotDiff`, `INJURY_OUT_STATUSES`).

- [ ] **Step 3: Write minimal implementation**

In `src/ffdo/domain/models.py`, add after the existing `DraftPickAsset` class (or at the end of the file if that class isn't the last one — check the current end of the file and append there):

```python
@dataclass(frozen=True, slots=True)
class WeeklyProjection:
    player_id: str
    season: int
    week: int
    stats: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class SlotDiff:
    slot_index: int                   # index into league.starting_slots (NOT roster_positions)
    slot_label: str                   # "RB", "FLEX", etc. -- starting_slots[slot_index]
    status: str                       # "match" | "suggested_swap" | "missed"
    current_player_id: str | None
    optimal_player_id: str | None
    delta: float                      # optimal value - current value; 0.0 when status == "match"
```

In `src/ffdo/domain/constants.py`, add (near the top, alongside `SEASON_LENGTH`):

```python
# Injury statuses that mean a player cannot take the field at all, shared
# by engine/ros_value.py (zeroes a rest-of-season value for these) and
# engine/weekly_lineup.py (excludes these entirely from a weekly solve --
# see that module's docstring for why exclusion, not zeroing, is correct
# there).
INJURY_OUT_STATUSES: Final[frozenset[str]] = frozenset({"IR", "PUP", "Out", "Sus"})
```

(Check the top of `constants.py` for the existing `from typing import Final` import — it's already there for `SEASON_LENGTH`/`NFL_BYE_WEEKS`; reuse it, don't add a duplicate import.)

In `src/ffdo/engine/ros_value.py`:
- Remove the line `_INJURY_OUT = frozenset({"IR", "PUP", "Out", "Sus"})`.
- Add `INJURY_OUT_STATUSES` to the existing `from ffdo.domain.models import ...` import line's sibling import, or add a new import line: `from ffdo.domain.constants import INJURY_OUT_STATUSES`.
- Change `if not profile.active or profile.injury_status in _INJURY_OUT:` to `if not profile.active or profile.injury_status in INJURY_OUT_STATUSES:`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/domain/test_models.py tests/domain/test_constants.py tests/engine/test_ros_value.py -q`
Expected: PASS — all of Task 1's new tests, and every existing `test_ros_value.py` test (the injury-exclusion behavior is unchanged, only where the constant lives moved).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, same count as the branch point plus the new tests from this task.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/domain/models.py src/ffdo/domain/constants.py src/ffdo/engine/ros_value.py tests/domain/test_models.py tests/domain/test_constants.py
git commit -m "feat: WeeklyProjection/SlotDiff domain types; promote INJURY_OUT_STATUSES"
```

---

## Task 2: `ingest/sleeper/weekly_projections.py`

**Files:**
- Create: `src/ffdo/ingest/sleeper/weekly_projections.py`
- Test: `tests/ingest/sleeper/test_weekly_projections.py`

**Interfaces:**
- Consumes: `ffdo.domain.models.WeeklyProjection` (Task 1), `ffdo.ingest.client.PROJECTIONS`, `ffdo.ingest.client.SleeperClient`.
- Produces: `fetch(sleeper: SleeperClient, season: int, week: int) -> dict[str, WeeklyProjection]`.

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/sleeper/test_weekly_projections.py`:

```python
import httpx

from ffdo.ingest.client import PROJECTIONS, SleeperClient
from ffdo.ingest.sleeper import weekly_projections


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def test_fetch_hits_the_week_scoped_url_with_position_filters():
    seen_urls = []

    def handler(request):
        seen_urls.append(str(request.url))
        return httpx.Response(200, json=[])

    weekly_projections.fetch(_client(handler), 2026, 10)
    assert len(seen_urls) == 1
    url = seen_urls[0]
    assert url.startswith(f"{PROJECTIONS}/2026/10?")
    for pos in ("QB", "RB", "WR", "TE", "DEF", "K"):
        assert f"position%5B%5D={pos}" in url or f"position[]={pos}" in url


def test_fetch_parses_stats_into_weekly_projection():
    def handler(request):
        return httpx.Response(200, json=[
            {"player_id": "p1", "stats": {"pass_yd": 260.0, "pass_td": 2.0}},
        ])

    out = weekly_projections.fetch(_client(handler), 2026, 10)
    assert out["p1"].player_id == "p1"
    assert out["p1"].season == 2026
    assert out["p1"].week == 10
    assert out["p1"].stats == {"pass_yd": 260.0, "pass_td": 2.0}


def test_fetch_drops_a_row_with_no_player_id():
    def handler(request):
        return httpx.Response(200, json=[{"stats": {"pass_yd": 260.0}}])

    assert weekly_projections.fetch(_client(handler), 2026, 10) == {}


def test_fetch_drops_a_row_with_only_gp_and_no_real_projection():
    def handler(request):
        return httpx.Response(200, json=[
            {"player_id": "p1", "stats": {"gp": 1.0}},
        ])

    assert weekly_projections.fetch(_client(handler), 2026, 10) == {}


def test_fetch_ignores_boolean_stat_values():
    def handler(request):
        return httpx.Response(200, json=[
            {"player_id": "p1", "stats": {"pass_yd": 260.0, "some_flag": True}},
        ])

    out = weekly_projections.fetch(_client(handler), 2026, 10)
    assert "some_flag" not in out["p1"].stats
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/sleeper/test_weekly_projections.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.ingest.sleeper.weekly_projections'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ffdo/ingest/sleeper/weekly_projections.py`:

```python
"""This week's projected points per player, from Sleeper's weekly
projections feed. Unlike `ingest/projections.py`'s season-total feed, a
single week's projection is meant to be refined right up to kickoff -- a
Tuesday projection being less accurate than Sunday morning's is the
projection doing its job, not corruption -- so there is no
ContaminatedProjectionError-style guard here."""

from __future__ import annotations

from typing import Any

from ffdo.domain.models import WeeklyProjection
from ffdo.ingest.client import PROJECTIONS, SleeperClient


def fetch(sleeper: SleeperClient, season: int, week: int) -> dict[str, WeeklyProjection]:
    raw: list[dict[str, Any]] = sleeper.get_json(
        f"{PROJECTIONS}/{season}/{week}"
        "?season_type=regular&position[]=QB&position[]=RB"
        "&position[]=WR&position[]=TE&position[]=DEF"
        "&position[]=K")

    out: dict[str, WeeklyProjection] = {}
    for row in raw or []:
        player_id = row.get("player_id")
        stats = row.get("stats") or {}
        if not player_id or not stats:
            continue
        # bool is a subclass of int; excluded so a JSON boolean stat value
        # is dropped rather than silently coerced to 1.0/0.0 (mirrors
        # ffdo.ingest.projections.parse).
        numeric = {k: float(v) for k, v in stats.items()
                  if isinstance(v, (int, float)) and not isinstance(v, bool)}
        # A row with nothing but `gp` carries no scoring signal -- same
        # "not actually projected" filter ingest/projections.py applies.
        if not (numeric.keys() - {"gp"}):
            continue
        out[str(player_id)] = WeeklyProjection(
            player_id=str(player_id), season=season, week=week, stats=numeric)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/sleeper/test_weekly_projections.py -v`
Expected: PASS, all 5 tests.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/ingest/sleeper/weekly_projections.py tests/ingest/sleeper/test_weekly_projections.py
git commit -m "feat: ingest.sleeper.weekly_projections.fetch -- this week's projected points"
```

---

## Task 3: `ingest/sleeper/schedule.py`

**Files:**
- Modify: `src/ffdo/ingest/client.py`
- Create: `src/ffdo/ingest/sleeper/schedule.py`
- Test: `tests/ingest/sleeper/test_schedule.py`

**Interfaces:**
- Consumes: `ffdo.ingest.client.SleeperClient`, the new `ffdo.ingest.client.SCHEDULE` constant.
- Produces:
  - `week_games(sleeper: SleeperClient, season: int, week: int) -> list[dict]`
  - `locked_teams(games: list[dict]) -> frozenset[str]`
  - `bye_teams(games: list[dict], all_teams: frozenset[str]) -> frozenset[str]`
  - `week_locked(games: list[dict]) -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/sleeper/test_schedule.py`:

```python
import httpx

from ffdo.ingest.client import SleeperClient
from ffdo.ingest.sleeper import schedule

_RAW = [
    {"status": "complete", "date": "2026-11-12", "home": "AAA", "away": "BBB",
     "week": 10, "game_id": "1"},
    {"status": "pre_game", "date": "2026-11-15", "home": "CCC", "away": "DDD",
     "week": 10, "game_id": "2"},
    {"status": "pre_game", "date": "2026-11-08", "home": "EEE", "away": "FFF",
     "week": 9, "game_id": "3"},
]


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def test_week_games_filters_to_the_requested_week():
    def handler(request):
        return httpx.Response(200, json=_RAW)

    games = schedule.week_games(_client(handler), 2026, 10)
    assert len(games) == 2
    assert all(g["week"] == 10 for g in games)


def test_locked_teams_includes_home_and_away_of_a_non_pre_game_game():
    games = [g for g in _RAW if g["week"] == 10]
    locked = schedule.locked_teams(games)
    assert locked == frozenset({"AAA", "BBB"})


def test_locked_teams_excludes_a_pre_game_games_teams():
    games = [g for g in _RAW if g["week"] == 10]
    locked = schedule.locked_teams(games)
    assert "CCC" not in locked and "DDD" not in locked


def test_bye_teams_is_every_team_not_playing_that_week():
    games = [g for g in _RAW if g["week"] == 10]
    all_teams = frozenset({"AAA", "BBB", "CCC", "DDD", "GGG"})
    assert schedule.bye_teams(games, all_teams) == frozenset({"GGG"})


def test_week_locked_false_while_any_game_is_pre_game():
    games = [g for g in _RAW if g["week"] == 10]
    assert schedule.week_locked(games) is False


def test_week_locked_true_once_every_game_has_started():
    games = [{**g, "status": "complete"} for g in _RAW if g["week"] == 10]
    assert schedule.week_locked(games) is True


def test_week_locked_false_for_an_empty_games_list():
    assert schedule.week_locked([]) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/sleeper/test_schedule.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.ingest.sleeper.schedule'`.

- [ ] **Step 3: Write minimal implementation**

In `src/ffdo/ingest/client.py`, add alongside the existing `V1`/`PROJECTIONS` constants:

```python
SCHEDULE = "https://api.sleeper.app/schedule/nfl/regular"
```

Create `src/ffdo/ingest/sleeper/schedule.py`:

```python
"""This week's NFL game schedule, from Sleeper's unofficial (undocumented
in Sleeper's public API docs) /schedule/nfl/regular/{season} endpoint.
Confirmed live and working 2026-09-11.

`status` is the live lock signal this project trusts: it starts as
"pre_game" and transitions once a game starts (through at least
"complete") -- this answers "can this player still be swapped" without
needing exact kickoff timestamps, which this endpoint doesn't even expose
(`date` is calendar-day only, no time-of-day).

If this endpoint ever disappears or changes shape, callers must degrade
gracefully rather than fail the whole request -- see
`ffdo.api.app.get_lineup`'s handling of a failed `week_games` call."""

from __future__ import annotations

from typing import Any

from ffdo.ingest.client import SCHEDULE, SleeperClient


def week_games(sleeper: SleeperClient, season: int, week: int) -> list[dict[str, Any]]:
    raw = sleeper.get_json(f"{SCHEDULE}/{season}")
    return [g for g in (raw or []) if g.get("week") == week]


def locked_teams(games: list[dict[str, Any]]) -> frozenset[str]:
    locked: set[str] = set()
    for g in games:
        if g.get("status") == "pre_game":
            continue
        if g.get("home"):
            locked.add(g["home"])
        if g.get("away"):
            locked.add(g["away"])
    return frozenset(locked)


def bye_teams(games: list[dict[str, Any]], all_teams: frozenset[str]) -> frozenset[str]:
    playing = {t for g in games for t in (g.get("home"), g.get("away")) if t}
    return all_teams - playing


def week_locked(games: list[dict[str, Any]]) -> bool:
    """True once every game that week has at least started -- a provider
    locks a team's actual starters at that team's own kickoff, not at the
    game's final whistle, so "started" (not "finished") is the point past
    which no further lineup action is possible. An empty list (no schedule
    data at all -- see the graceful-degradation note above) is never
    considered locked."""
    return bool(games) and all(g.get("status") != "pre_game" for g in games)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/sleeper/test_schedule.py -v`
Expected: PASS, all 7 tests.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/ingest/client.py src/ffdo/ingest/sleeper/schedule.py tests/ingest/sleeper/test_schedule.py
git commit -m "feat: ingest.sleeper.schedule -- live per-game lock/bye status"
```

---

## Task 4: `ingest/rosters.py::raw_starters`

**Files:**
- Modify: `src/ffdo/ingest/rosters.py`
- Test: `tests/ingest/test_rosters.py`

**Interfaces:**
- Consumes: `ffdo.ingest.client.V1`, `ffdo.ingest.client.SleeperClient` (both already imported in this file).
- Produces: `raw_starters(sleeper: SleeperClient, league_id: str, roster_id: int) -> tuple[str | None, ...]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/ingest/test_rosters.py` (check the existing file first for its `_client`/handler helper style and reuse it rather than redefining one — if it already has a helper named `_client`, use it; if not, add one matching this shape):

```python
def test_raw_starters_preserves_positional_alignment_including_empty_slots():
    rosters_raw = [
        {"roster_id": 1, "owner_id": "U1", "players": ["a", "b", "c"],
         "starters": ["a", "0", "c", "b"],
         "settings": {"wins": 0, "losses": 0}},
        {"roster_id": 2, "owner_id": "U2", "players": ["d"],
         "starters": ["d"], "settings": {"wins": 0, "losses": 0}},
    ]

    def handler(request):
        if request.url.path.endswith("/rosters"):
            return httpx.Response(200, json=rosters_raw)
        return httpx.Response(200, json=[])

    out = rosters.raw_starters(_client(handler), "L1", roster_id=1)
    assert out == ("a", None, "c", "b")


def test_raw_starters_returns_empty_tuple_for_an_unknown_roster_id():
    def handler(request):
        return httpx.Response(200, json=[
            {"roster_id": 1, "owner_id": "U1", "players": [], "starters": [],
             "settings": {}},
        ])

    assert rosters.raw_starters(_client(handler), "L1", roster_id=99) == ()
```

(If `httpx` isn't already imported at the top of this test file, add `import httpx`. If the module is imported as `from ffdo.ingest import rosters` already, reuse that; if it's imported some other way, match the existing style.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_rosters.py -v -k raw_starters`
Expected: FAIL — `AttributeError: module 'ffdo.ingest.rosters' has no attribute 'raw_starters'`.

- [ ] **Step 3: Write minimal implementation**

In `src/ffdo/ingest/rosters.py`, add after the existing `fetch` function:

```python
def raw_starters(
    sleeper: SleeperClient, league_id: str, roster_id: int,
) -> tuple[str | None, ...]:
    """The tracked user's OWN starters array, exactly as Sleeper returns
    it -- positionally aligned to the league's starting slots (in order,
    length always equal to the starting-slot count), with `"0"` (Sleeper's
    empty-slot placeholder) mapped to `None`.

    Deliberately NOT `RosterEntry.starter_ids` (see `fetch` above): that
    field is a compacted SET with alignment already discarded, correct for
    #2's "is this player starting at all" question but wrong for a
    slot-by-slot diff, which needs the alignment back. This fetches the
    same endpoint `fetch` does and is safe to call alongside it -- no
    caching here, same as `fetch`, since a roster's starters can change at
    any moment right up to kickoff."""
    rosters_raw = sleeper.get_json(f"{V1}/league/{league_id}/rosters")
    for r in rosters_raw:
        if r.get("roster_id") == roster_id:
            return tuple(
                None if p in ("0", 0, None) else str(p)
                for p in (r.get("starters") or [])
            )
    return ()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/test_rosters.py -v`
Expected: PASS, including the two new tests and every pre-existing test in this file.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/ingest/rosters.py tests/ingest/test_rosters.py
git commit -m "feat: rosters.raw_starters -- positionally-aligned starters for the lineup diff"
```

---

## Task 5: `engine/weekly_lineup.py` — `weekly_value` + `optimal_slots`

**Files:**
- Create: `src/ffdo/engine/weekly_lineup.py`
- Test: `tests/engine/test_weekly_lineup.py`

**Interfaces:**
- Consumes: `ffdo.domain.constants.INJURY_OUT_STATUSES` (Task 1), `ffdo.domain.models.{PlayerProfile, ValuedPlayer, WeeklyProjection}`, `ffdo.engine.vor.compute`, `ffdo.engine.replacement.{FLEX_ELIGIBILITY, rank_by_position}`, `ffdo.engine.scoring.score_stats`.
- Produces:
  - `weekly_value(player_ids: Iterable[str], league, *, weekly_points: Mapping[str, WeeklyProjection], profiles: Mapping[str, PlayerProfile], bye_teams: frozenset[str]) -> dict[str, ValuedPlayer]`
  - `optimal_slots(valued: Mapping[str, ValuedPlayer], league) -> dict[int, str | None]`

A minimal duck-typed `league` for these tests needs `.scoring_settings` and `.starting_slots` (and `.roster_positions`/`.num_teams` are NOT needed by either function in this task). Build one with a tiny local dataclass or `types.SimpleNamespace` — check how `tests/engine/test_ros_value.py` builds its fixture league and match that style exactly (do not invent a second convention).

- [ ] **Step 1: Write the failing test**

First, read `tests/engine/test_ros_value.py`'s league-fixture helper (near its top) to copy its exact shape. Then create `tests/engine/test_weekly_lineup.py`:

```python
from ffdo.domain.models import PlayerProfile, WeeklyProjection
from ffdo.engine import weekly_lineup

# Match the League fixture shape used in tests/engine/test_ros_value.py --
# a minimal object with `.scoring_settings` and `.starting_slots`.
# (Copy that exact helper/class here rather than importing across test
# files, matching this codebase's existing tests/engine/ convention of
# each test file owning its own fixtures.)


def _profile(pid, position, team="AAA", injury_status=None, active=True):
    return PlayerProfile(
        player_id=pid, first_name=pid, last_name="X", position=position,
        team=team, age=25, years_exp=3, injury_status=injury_status,
        active=active,
    )


def _proj(pid, week, **stats):
    return WeeklyProjection(player_id=pid, season=2026, week=week, stats=stats)


SCORING = {"pass_yd": 0.04, "pass_td": 4.0, "rush_yd": 0.1, "rush_td": 6.0,
          "rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0}


class _League:
    def __init__(self, starting_slots, scoring_settings=SCORING, num_teams=2):
        self.starting_slots = starting_slots
        self.scoring_settings = scoring_settings
        self.num_teams = num_teams


def test_weekly_value_excludes_a_bye_team_player_entirely():
    profiles = {"p1": _profile("p1", "RB", team="BYE")}
    weekly_points = {"p1": _proj("p1", 10, rush_yd=100.0, rush_td=1.0)}
    league = _League(starting_slots=("RB",))

    valued = weekly_lineup.weekly_value(
        ["p1"], league, weekly_points=weekly_points, profiles=profiles,
        bye_teams=frozenset({"BYE"}))
    assert "p1" not in valued


def test_weekly_value_excludes_a_hard_out_player_entirely():
    profiles = {"p1": _profile("p1", "RB", injury_status="Out")}
    weekly_points = {"p1": _proj("p1", 10, rush_yd=100.0, rush_td=1.0)}
    league = _League(starting_slots=("RB",))

    valued = weekly_lineup.weekly_value(
        ["p1"], league, weekly_points=weekly_points, profiles=profiles,
        bye_teams=frozenset())
    assert "p1" not in valued


def test_weekly_value_includes_a_merely_questionable_player():
    profiles = {"p1": _profile("p1", "RB", injury_status="Questionable")}
    weekly_points = {"p1": _proj("p1", 10, rush_yd=100.0, rush_td=1.0)}
    league = _League(starting_slots=("RB",))

    valued = weekly_lineup.weekly_value(
        ["p1"], league, weekly_points=weekly_points, profiles=profiles,
        bye_teams=frozenset())
    assert "p1" in valued


def test_optimal_slots_assigns_dedicated_slots_by_rank():
    profiles = {"p_hi": _profile("p_hi", "RB"), "p_lo": _profile("p_lo", "RB")}
    weekly_points = {"p_hi": _proj("p_hi", 10, rush_yd=150.0),
                     "p_lo": _proj("p_lo", 10, rush_yd=20.0)}
    league = _League(starting_slots=("RB",))

    valued = weekly_lineup.weekly_value(
        ["p_hi", "p_lo"], league, weekly_points=weekly_points,
        profiles=profiles, bye_teams=frozenset())
    slots = weekly_lineup.optimal_slots(valued, league)
    assert slots == {0: "p_hi"}


def test_optimal_slots_flex_takes_the_best_remaining_eligible_player():
    profiles = {"p_rb1": _profile("p_rb1", "RB"), "p_rb2": _profile("p_rb2", "RB"),
               "p_wr1": _profile("p_wr1", "WR")}
    weekly_points = {
        "p_rb1": _proj("p_rb1", 10, rush_yd=150.0),   # best RB -> dedicated RB slot
        "p_rb2": _proj("p_rb2", 10, rush_yd=60.0),    # 2nd RB -> should win FLEX over p_wr1
        "p_wr1": _proj("p_wr1", 10, rec=3.0, rec_yd=20.0),   # weaker than p_rb2 on VOR
    }
    league = _League(starting_slots=("RB", "FLEX"))

    valued = weekly_lineup.weekly_value(
        ["p_rb1", "p_rb2", "p_wr1"], league, weekly_points=weekly_points,
        profiles=profiles, bye_teams=frozenset())
    slots = weekly_lineup.optimal_slots(valued, league)
    assert slots[0] == "p_rb1"
    assert slots[1] == "p_rb2"


def test_optimal_slots_processes_dedicated_slots_before_flex_regardless_of_roster_order():
    """The critical ordering case: FLEX appears BEFORE the dedicated RB slot
    in `starting_slots`. `engine.replacement.greedy_fill_slots` always fills
    ALL dedicated slots first, then FLEX -- `optimal_slots` must reproduce
    that two-phase order, not a naive left-to-right walk of
    `starting_slots`, or FLEX could steal the best RB before the dedicated
    RB slot gets a turn."""
    profiles = {"p_rb1": _profile("p_rb1", "RB"), "p_rb2": _profile("p_rb2", "RB")}
    weekly_points = {"p_rb1": _proj("p_rb1", 10, rush_yd=150.0),
                     "p_rb2": _proj("p_rb2", 10, rush_yd=60.0)}
    league = _League(starting_slots=("FLEX", "RB"))   # FLEX listed FIRST

    valued = weekly_lineup.weekly_value(
        ["p_rb1", "p_rb2"], league, weekly_points=weekly_points,
        profiles=profiles, bye_teams=frozenset())
    slots = weekly_lineup.optimal_slots(valued, league)
    # Dedicated RB slot (index 1) must get the BEST RB (p_rb1), even though
    # FLEX (index 0) is processed... wait, is listed first in the tuple.
    # A naive left-to-right walk would let FLEX (index 0) grab p_rb1 first.
    # The correct two-phase algorithm fills the dedicated RB slot (index 1)
    # with p_rb1 and leaves FLEX (index 0) with the remaining p_rb2.
    assert slots[1] == "p_rb1"
    assert slots[0] == "p_rb2"


def test_optimal_slots_none_when_fewer_eligible_players_than_slots():
    profiles = {"p1": _profile("p1", "RB")}
    weekly_points = {"p1": _proj("p1", 10, rush_yd=100.0)}
    league = _League(starting_slots=("RB", "RB"))

    valued = weekly_lineup.weekly_value(
        ["p1"], league, weekly_points=weekly_points, profiles=profiles,
        bye_teams=frozenset())
    slots = weekly_lineup.optimal_slots(valued, league)
    assert slots[0] == "p1"
    assert slots[1] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_weekly_lineup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.engine.weekly_lineup'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ffdo/engine/weekly_lineup.py`:

```python
"""This week's lineup value and optimal slot assignment.

`weekly_value` scores a player's WEEKLY projection (not the rest-of-season
blend `engine.ros_value` computes) and feeds it into the same
`engine.vor.compute` #2 already reuses unchanged, after EXCLUDING players
who cannot play at all this week (on bye, or a hard-out injury status) --
exclusion, not a zero score. #2's "value 0 now, might recover later"
reasoning for a season-long asset doesn't apply here: a player who cannot
play this Sunday has no scenario in which starting them helps this week,
so they must not be eligible to fill a slot at all, not merely score low
enough to lose the comparison (a thin position with every remaining option
also scoring low could otherwise still "win" a slot by default).

`optimal_slots` is a slot-labeled sibling of `engine.roster.team_lineup`:
the IDENTICAL two-phase (dedicated-slots-first, then FLEX) greedy walk
`engine.replacement.greedy_fill_slots` already performs, reimplemented
here only to additionally record which specific `league.starting_slots`
index each pick fills -- `greedy_fill_slots` itself returns just the
aggregate starter set, which is enough for #2's power ranking but not for
a slot-by-slot diff against the provider's own positionally-aligned
starters array. Neither `greedy_fill_slots` nor `team_lineup` is modified;
for an identical roster and league this produces the identical starter
SET team_lineup would.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ffdo.domain.constants import INJURY_OUT_STATUSES
from ffdo.domain.models import PlayerProfile, ValuedPlayer, WeeklyProjection
from ffdo.engine import vor
from ffdo.engine.replacement import FLEX_ELIGIBILITY, rank_by_position
from ffdo.engine.scoring import score_stats


def weekly_value(
    player_ids: Iterable[str],
    league,
    *,
    weekly_points: Mapping[str, WeeklyProjection],
    profiles: Mapping[str, PlayerProfile],
    bye_teams: frozenset[str],
) -> dict[str, ValuedPlayer]:
    value_pts: dict[str, float] = {}
    for pid in player_ids:
        proj = weekly_points.get(pid)
        profile = profiles.get(pid)
        if proj is None or profile is None:
            continue
        if profile.team in bye_teams or profile.injury_status in INJURY_OUT_STATUSES:
            continue
        value_pts[pid] = score_stats(proj.stats, league.scoring_settings)
    return vor.compute(value_pts, profiles, league)


def optimal_slots(
    valued: Mapping[str, ValuedPlayer],
    league,
) -> dict[int, str | None]:
    vor_by_pid = {pid: vp.vor for pid, vp in valued.items()}
    positions = {pid: vp.profile.position for pid, vp in valued.items()}
    ranked = rank_by_position(vor_by_pid, positions)
    cursor: dict[str, int] = dict.fromkeys(ranked, 0)

    slots = league.starting_slots
    dedicated = [(i, s) for i, s in enumerate(slots) if s not in FLEX_ELIGIBILITY]
    flex = [(i, s) for i, s in enumerate(slots) if s in FLEX_ELIGIBILITY]

    result: dict[int, str | None] = {}
    for i, slot in dedicated:
        pool = ranked.get(slot, [])
        idx = cursor.get(slot, 0)
        if idx < len(pool):
            result[i] = pool[idx][1]
            cursor[slot] = idx + 1
        else:
            result[i] = None

    for i, slot in flex:
        eligible = FLEX_ELIGIBILITY[slot]
        best: tuple[float, str] | None = None
        for pos in eligible:
            pool = ranked.get(pos, [])
            idx = cursor.get(pos, 0)
            if idx < len(pool) and (best is None or pool[idx][0] > best[0]):
                best = pool[idx]
        if best is not None:
            pos = positions[best[1]]
            result[i] = best[1]
            cursor[pos] = cursor.get(pos, 0) + 1
        else:
            result[i] = None

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_weekly_lineup.py -v`
Expected: PASS, all 7 tests -- pay particular attention to
`test_optimal_slots_processes_dedicated_slots_before_flex_regardless_of_roster_order`,
which is the test that would catch a naive (incorrect) left-to-right
implementation.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/engine/weekly_lineup.py tests/engine/test_weekly_lineup.py
git commit -m "feat: weekly_lineup.weekly_value + optimal_slots -- this week's VOR and slot-aware solve"
```

---

## Task 6: `engine/weekly_lineup.py` — `diff`

**Files:**
- Modify: `src/ffdo/engine/weekly_lineup.py`
- Modify: `tests/engine/test_weekly_lineup.py`

**Interfaces:**
- Consumes: `ffdo.domain.models.SlotDiff` (Task 1), everything `weekly_value`/`optimal_slots` (Task 5) produce.
- Produces: `diff(current_starters: tuple[str | None, ...], optimal: Mapping[int, str | None], locked_teams: frozenset[str], valued: Mapping[str, ValuedPlayer], profiles: Mapping[str, PlayerProfile], league) -> list[SlotDiff]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/engine/test_weekly_lineup.py`:

```python
from ffdo.domain.models import SlotDiff


def _valued_from(profiles, weekly_points, league, bye_teams=frozenset()):
    return weekly_lineup.weekly_value(
        list(profiles), league, weekly_points=weekly_points, profiles=profiles,
        bye_teams=bye_teams)


def test_diff_match_when_current_equals_optimal():
    profiles = {"p1": _profile("p1", "RB")}
    weekly_points = {"p1": _proj("p1", 10, rush_yd=100.0)}
    league = _League(starting_slots=("RB",))
    valued = _valued_from(profiles, weekly_points, league)
    optimal = weekly_lineup.optimal_slots(valued, league)

    rows = weekly_lineup.diff(("p1",), optimal, frozenset(), valued, profiles, league)
    assert rows == [SlotDiff(slot_index=0, slot_label="RB", status="match",
                             current_player_id="p1", optimal_player_id=None, delta=0.0)]


def test_diff_suggested_swap_when_better_option_is_unlocked():
    profiles = {"p_bench": _profile("p_bench", "RB", team="BENCH_TEAM"),
               "p_started": _profile("p_started", "RB", team="STARTED_TEAM")}
    weekly_points = {"p_bench": _proj("p_bench", 10, rush_yd=150.0),
                     "p_started": _proj("p_started", 10, rush_yd=20.0)}
    league = _League(starting_slots=("RB",))
    valued = _valued_from(profiles, weekly_points, league)
    optimal = weekly_lineup.optimal_slots(valued, league)
    assert optimal[0] == "p_bench"

    rows = weekly_lineup.diff(("p_started",), optimal, frozenset(),
                              valued, profiles, league)
    row = rows[0]
    assert row.status == "suggested_swap"
    assert row.current_player_id == "p_started"
    assert row.optimal_player_id == "p_bench"
    assert row.delta > 0


def test_diff_missed_when_current_starters_team_already_locked():
    profiles = {"p_bench": _profile("p_bench", "RB", team="BENCH_TEAM"),
               "p_started": _profile("p_started", "RB", team="STARTED_TEAM")}
    weekly_points = {"p_bench": _proj("p_bench", 10, rush_yd=150.0),
                     "p_started": _proj("p_started", 10, rush_yd=20.0)}
    league = _League(starting_slots=("RB",))
    valued = _valued_from(profiles, weekly_points, league)
    optimal = weekly_lineup.optimal_slots(valued, league)

    rows = weekly_lineup.diff(("p_started",), optimal, frozenset({"STARTED_TEAM"}),
                              valued, profiles, league)
    assert rows[0].status == "missed"


def test_diff_missed_for_an_empty_slot_whose_only_fix_already_locked():
    profiles = {"p_bench": _profile("p_bench", "RB", team="BENCH_TEAM")}
    weekly_points = {"p_bench": _proj("p_bench", 10, rush_yd=150.0)}
    league = _League(starting_slots=("RB",))
    valued = _valued_from(profiles, weekly_points, league)
    optimal = weekly_lineup.optimal_slots(valued, league)
    assert optimal[0] == "p_bench"

    rows = weekly_lineup.diff((None,), optimal, frozenset({"BENCH_TEAM"}),
                              valued, profiles, league)
    assert rows[0].status == "missed"
    assert rows[0].current_player_id is None
    assert rows[0].optimal_player_id == "p_bench"


def test_diff_suggested_swap_for_an_empty_slot_whose_fix_is_still_unlocked():
    profiles = {"p_bench": _profile("p_bench", "RB", team="BENCH_TEAM")}
    weekly_points = {"p_bench": _proj("p_bench", 10, rush_yd=150.0)}
    league = _League(starting_slots=("RB",))
    valued = _valued_from(profiles, weekly_points, league)
    optimal = weekly_lineup.optimal_slots(valued, league)

    rows = weekly_lineup.diff((None,), optimal, frozenset(),
                              valued, profiles, league)
    assert rows[0].status == "suggested_swap"


def test_diff_match_when_both_current_and_optimal_are_empty():
    league = _League(starting_slots=("RB",))
    rows = weekly_lineup.diff((None,), {0: None}, frozenset(), {}, {}, league)
    assert rows[0].status == "match"
    assert rows[0].current_player_id is None
    assert rows[0].optimal_player_id is None


def test_diff_current_starter_excluded_from_valued_still_shows_a_zero_value():
    """A current starter who was excluded from `valued` (bye/hard-out) must
    not crash the diff, and reads as 0.0 rather than raising KeyError."""
    profiles = {"p_bye": _profile("p_bye", "RB", team="BYE_TEAM"),
               "p_bench": _profile("p_bench", "RB", team="BENCH_TEAM")}
    weekly_points = {"p_bye": _proj("p_bye", 10, rush_yd=150.0),
                     "p_bench": _proj("p_bench", 10, rush_yd=50.0)}
    league = _League(starting_slots=("RB",))
    valued = _valued_from(profiles, weekly_points, league, bye_teams=frozenset({"BYE_TEAM"}))
    assert "p_bye" not in valued
    optimal = weekly_lineup.optimal_slots(valued, league)

    rows = weekly_lineup.diff(("p_bye",), optimal, frozenset(), valued, profiles, league)
    assert rows[0].current_player_id == "p_bye"
    assert rows[0].status == "suggested_swap"
    assert rows[0].delta == round(valued["p_bench"].vor - 0.0, 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_weekly_lineup.py -v -k test_diff`
Expected: FAIL — `AttributeError: module 'ffdo.engine.weekly_lineup' has no attribute 'diff'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/ffdo/engine/weekly_lineup.py` (append after `optimal_slots`; also add `SlotDiff` to the existing `from ffdo.domain.models import ...` line):

```python
def diff(
    current_starters: tuple[str | None, ...],
    optimal: Mapping[int, str | None],
    locked_teams: frozenset[str],
    valued: Mapping[str, ValuedPlayer],
    profiles: Mapping[str, PlayerProfile],
    league,
) -> list[SlotDiff]:
    def value_of(pid: str | None) -> float:
        if pid is None:
            return 0.0
        vp = valued.get(pid)
        return vp.vor if vp is not None else 0.0

    rows: list[SlotDiff] = []
    for i, slot_label in enumerate(league.starting_slots):
        current = current_starters[i] if i < len(current_starters) else None
        best = optimal.get(i)

        if current == best:
            rows.append(SlotDiff(
                slot_index=i, slot_label=slot_label, status="match",
                current_player_id=current, optimal_player_id=None, delta=0.0))
            continue

        current_profile = profiles.get(current) if current else None
        best_profile = profiles.get(best) if best else None
        current_locked = current_profile is not None and current_profile.team in locked_teams
        empty_and_best_locked = (
            current is None and best_profile is not None
            and best_profile.team in locked_teams)
        status = "missed" if (current_locked or empty_and_best_locked) else "suggested_swap"

        rows.append(SlotDiff(
            slot_index=i, slot_label=slot_label, status=status,
            current_player_id=current, optimal_player_id=best,
            delta=round(value_of(best) - value_of(current), 1)))
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_weekly_lineup.py -v`
Expected: PASS, all tests in the file (Task 5's and Task 6's).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/engine/weekly_lineup.py tests/engine/test_weekly_lineup.py
git commit -m "feat: weekly_lineup.diff -- lock-aware slot-by-slot comparison"
```

---

## Task 7: `api/lineup_ledger.py`

**Files:**
- Create: `src/ffdo/api/lineup_ledger.py`
- Test: `tests/api/test_lineup_ledger.py`

**Interfaces:**
- Consumes: stdlib `sqlite3`, `json`, `datetime`.
- Produces:
  - `LineupRecord(league_key: str, season: int, week: int, recommended: dict[int, str | None], actual: tuple[str | None, ...], recorded_at: str, followed: str | None, resolved_at: str | None)` — frozen, slots.
  - `LineupLedger(path: Path)` with `.record_if_absent(league_key, season, week, recommended, actual) -> LineupRecord`, `.get(league_key, season, week) -> LineupRecord | None`, `.resolve(league_key, season, week, final_actual) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_lineup_ledger.py`:

```python
from ffdo.api.lineup_ledger import LineupLedger


def test_record_if_absent_creates_a_row(tmp_path):
    ledger = LineupLedger(tmp_path / "ffdo.db")
    record = ledger.record_if_absent(
        "sleeper:L1:2026", 2026, 10,
        recommended={0: "p1", 1: "p2"}, actual=("p3", "p2"))

    assert record.recommended == {0: "p1", 1: "p2"}
    assert record.actual == ("p3", "p2")
    assert record.followed is None
    assert record.resolved_at is None

    fetched = ledger.get("sleeper:L1:2026", 2026, 10)
    assert fetched == record


def test_record_if_absent_is_a_no_op_on_a_second_call_same_week(tmp_path):
    ledger = LineupLedger(tmp_path / "ffdo.db")
    first = ledger.record_if_absent(
        "sleeper:L1:2026", 2026, 10,
        recommended={0: "p1"}, actual=("p_old",))
    second = ledger.record_if_absent(
        "sleeper:L1:2026", 2026, 10,
        recommended={0: "DIFFERENT"}, actual=("p_new",))

    assert second.recommended == {0: "p1"}   # unchanged from the first call
    assert second.actual == ("p_old",)
    assert first.recorded_at == second.recorded_at


def test_get_returns_none_for_a_week_never_recorded(tmp_path):
    ledger = LineupLedger(tmp_path / "ffdo.db")
    assert ledger.get("sleeper:L1:2026", 2026, 10) is None


def test_resolve_full_when_final_matches_recommendation_everywhere(tmp_path):
    ledger = LineupLedger(tmp_path / "ffdo.db")
    ledger.record_if_absent("sleeper:L1:2026", 2026, 10,
                            recommended={0: "p1", 1: "p2"}, actual=("p_old", "p2"))

    followed = ledger.resolve("sleeper:L1:2026", 2026, 10, final_actual=("p1", "p2"))
    assert followed == "full"
    assert ledger.get("sleeper:L1:2026", 2026, 10).followed == "full"
    assert ledger.get("sleeper:L1:2026", 2026, 10).resolved_at is not None


def test_resolve_partial_when_final_matches_some_but_not_all(tmp_path):
    ledger = LineupLedger(tmp_path / "ffdo.db")
    ledger.record_if_absent("sleeper:L1:2026", 2026, 10,
                            recommended={0: "p1", 1: "p2"}, actual=("p_old", "p_old2"))

    followed = ledger.resolve("sleeper:L1:2026", 2026, 10, final_actual=("p1", "p_old2"))
    assert followed == "partial"


def test_resolve_none_when_final_matches_nothing_beyond_the_original(tmp_path):
    ledger = LineupLedger(tmp_path / "ffdo.db")
    ledger.record_if_absent("sleeper:L1:2026", 2026, 10,
                            recommended={0: "p1", 1: "p2"}, actual=("p_old", "p_old2"))

    followed = ledger.resolve("sleeper:L1:2026", 2026, 10,
                              final_actual=("p_old", "p_old2"))
    assert followed == "none"


def test_two_different_weeks_are_independent_rows(tmp_path):
    ledger = LineupLedger(tmp_path / "ffdo.db")
    ledger.record_if_absent("sleeper:L1:2026", 2026, 9, recommended={0: "wk9"}, actual=("a",))
    ledger.record_if_absent("sleeper:L1:2026", 2026, 10, recommended={0: "wk10"}, actual=("b",))

    assert ledger.get("sleeper:L1:2026", 2026, 9).recommended == {0: "wk9"}
    assert ledger.get("sleeper:L1:2026", 2026, 10).recommended == {0: "wk10"}
```

(Delete the `test_record_if_absent_does_not_overwrite_an_existing_row` placeholder stub above before running -- it's a leftover name collision with the real test below it; the actual behavior it names is already covered by `test_record_if_absent_is_a_no_op_on_a_second_call_same_week`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_lineup_ledger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.api.lineup_ledger'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ffdo/api/lineup_ledger.py`:

```python
"""Decision ledger for the weekly lineup recommendation.

One row per (league_key, season, week). Written once, on the first
`/lineup` view of that week (`record_if_absent`) -- never overwritten by a
later view the same week, even as projections refine, because the
ledger's job is "what did we tell you, and what did you have set, the
FIRST time you looked." Resolved once every game that week has started
(`resolve`, called from `ffdo.api.app.get_lineup` once
`ingest.sleeper.schedule.week_locked` is true): compares the final actual
starters against the stored recommendation, slot by slot.

Same connection pattern as `ffdo.api.store.LeagueStore` -- one file,
stdlib `sqlite3`, no ORM. This app is still a single local process for one
user, so there is no concurrency model beyond "open a connection per
call."
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class LineupRecord:
    league_key: str
    season: int
    week: int
    recommended: dict[int, str | None]
    actual: tuple[str | None, ...]
    recorded_at: str
    followed: str | None
    resolved_at: str | None


class LineupLedger:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._ready = False

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
                CREATE TABLE IF NOT EXISTS lineup_recommendation (
                    league_key TEXT NOT NULL,
                    season INTEGER NOT NULL,
                    week INTEGER NOT NULL,
                    recommended_json TEXT NOT NULL,
                    actual_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    followed TEXT,
                    resolved_at TEXT,
                    PRIMARY KEY (league_key, season, week)
                );
                """
            )
            conn.commit()
        except sqlite3.DatabaseError:
            # A corrupt/foreign file at this path: treat the store as empty
            # rather than crashing the app on startup (mirrors LeagueStore).
            pass

    def get(self, league_key: str, season: int, week: int) -> LineupRecord | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM lineup_recommendation "
                    "WHERE league_key = ? AND season = ? AND week = ?",
                    (league_key, season, week),
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        return self._row_to_record(row) if row is not None else None

    def record_if_absent(
        self,
        league_key: str,
        season: int,
        week: int,
        recommended: dict[int, str | None],
        actual: tuple[str | None, ...],
    ) -> LineupRecord:
        existing = self.get(league_key, season, week)
        if existing is not None:
            return existing
        recorded_at = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO lineup_recommendation "
                "(league_key, season, week, recommended_json, actual_json, "
                " recorded_at, followed, resolved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (league_key, season, week,
                 json.dumps({str(k): v for k, v in recommended.items()}),
                 json.dumps(list(actual)),
                 recorded_at, None, None),
            )
            conn.commit()
        # Re-read rather than trust the just-written values: a concurrent
        # writer (impossible in this single-process app today, but cheap
        # to get right) could have won the INSERT OR IGNORE race.
        return self.get(league_key, season, week)  # type: ignore[return-value]

    def resolve(
        self,
        league_key: str,
        season: int,
        week: int,
        final_actual: tuple[str | None, ...],
    ) -> str:
        record = self.get(league_key, season, week)
        if record is None:
            raise ValueError(
                f"no lineup_recommendation row for {league_key} season {season} week {week}")
        total = len(record.recommended)
        matches = sum(
            1 for i, want in record.recommended.items()
            if i < len(final_actual) and final_actual[i] == want
        )
        followed = "full" if matches == total else ("partial" if matches > 0 else "none")
        with self._connect() as conn:
            conn.execute(
                "UPDATE lineup_recommendation SET followed = ?, resolved_at = ? "
                "WHERE league_key = ? AND season = ? AND week = ?",
                (followed, _now(), league_key, season, week),
            )
            conn.commit()
        return followed

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> LineupRecord:
        recommended_raw = json.loads(row["recommended_json"])
        return LineupRecord(
            league_key=row["league_key"], season=row["season"], week=row["week"],
            recommended={int(k): v for k, v in recommended_raw.items()},
            actual=tuple(json.loads(row["actual_json"])),
            recorded_at=row["recorded_at"], followed=row["followed"],
            resolved_at=row["resolved_at"],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_lineup_ledger.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/api/lineup_ledger.py tests/api/test_lineup_ledger.py
git commit -m "feat: LineupLedger -- decision ledger for the weekly lineup recommendation"
```

---

## Task 8: `GET /api/leagues/{league_key}/lineup`

**Files:**
- Modify: `src/ffdo/api/app.py`
- Test: `tests/api/test_lineup_endpoint.py`

**Interfaces:**
- Consumes: everything from Tasks 1-7 -- `ffdo.ingest.sleeper.weekly_projections.fetch`, `ffdo.ingest.sleeper.schedule.{week_games, locked_teams, bye_teams, week_locked}`, `ffdo.ingest.rosters.raw_starters`, `ffdo.engine.weekly_lineup.{weekly_value, optimal_slots, diff}`, `ffdo.api.lineup_ledger.LineupLedger`. Also reuses existing `app.py` machinery: `_load_league`, `players_cache`/`_load_players`, `_nfl_state_cache`/`nfl_state.current_week`, `ffdo.ingest.rosters.fetch` (the existing full-roster-pool fetch from #2), `_TTLCache`.
- Produces: the `GET /api/leagues/{league_key}/lineup` route.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_lineup_endpoint.py`:

```python
"""`GET /api/leagues/{league_key}/lineup` -- this week's optimal-lineup diff."""

from fastapi.testclient import TestClient

from ffdo.api import app as app_mod
from ffdo.api.app import create_app
from ffdo.api.lineup_ledger import LineupLedger
from ffdo.api.store import LeagueStore
from ffdo.ingest.client import PROJECTIONS, V1
from tests.api.test_season_endpoint import _PLAYERS, _ROSTERS, _STATE, _USERS, _tracked

SCHEDULE = "https://api.sleeper.app/schedule/nfl/regular"

_WEEKLY_PROJ = [
    {"player_id": "p_qb", "stats": {"pass_yd": 260.0, "pass_td": 2.0}},
    {"player_id": "p_rb", "stats": {"rush_yd": 90.0, "rush_td": 1.0, "rec": 3.0, "rec_yd": 20.0}},
    {"player_id": "p_wr", "stats": {"rec": 7.0, "rec_yd": 95.0, "rec_td": 1.0}},
    {"player_id": "p_rb2", "stats": {"rush_yd": 60.0, "rush_td": 0.0, "rec": 2.0, "rec_yd": 15.0}},
]
# p_qb -> AAA (unlocked), p_rb -> BBB (locked), p_wr -> CCC (unlocked),
# p_rb2 -> DDD (locked) -- see _PLAYERS in test_season_endpoint for team assignment.
_SCHEDULE_2026 = [
    {"status": "pre_game", "date": "2026-11-15", "home": "AAA", "away": "CCC",
     "week": 10, "game_id": "1"},
    {"status": "complete", "date": "2026-11-12", "home": "BBB", "away": "DDD",
     "week": 10, "game_id": "2"},
]


def _recording_client(extra=None):
    resp = {
        f"{V1}/state/nfl": _STATE,
        f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS,
        f"{V1}/players/nfl": _PLAYERS,
        f"{PROJECTIONS}/2026/10": _WEEKLY_PROJ,
        f"{SCHEDULE}/2026": _SCHEDULE_2026,
    }
    resp.update(extra or {})

    class _C:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            for key, val in resp.items():
                if key in url:
                    return val
            return []
        def close(self): pass
    return _C


def _seed(monkeypatch, tmp_path, tracked, extra=None):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(tracked)
    monkeypatch.setattr(app_mod, "_STORE", store)
    monkeypatch.setattr(app_mod, "_LINEUP_LEDGER", LineupLedger(tmp_path / "ffdo.db"))
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _recording_client(extra))


def test_lineup_payload_is_well_formed(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _tracked())
    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/lineup")
    assert res.status_code == 200
    body = res.json()

    assert body["nfl_week"] == {"season": 2026, "week": 10}
    # starting_slots for _tracked()'s default roster_positions ("QB","RB","WR","FLEX","BN","BN")
    # is ("QB","RB","WR","FLEX") -- 4 rows.
    assert len(body["diff"]) == 4
    assert body["week_locked"] is False   # AAA/CCC are still pre_game
    assert body["ledger"]["followed"] is None
    assert "recorded_at" in body["ledger"]


def test_lineup_flags_a_swap_when_a_better_unlocked_bench_option_exists(monkeypatch, tmp_path):
    # The base fixture's roster 1 has no bench depth (all 3 rostered
    # players are already starting, FLEX sits empty with nothing to fill
    # it). This test adds a 4th player, p_wr2 (team AAA, still pre_game --
    # unlocked), on the bench, projected well enough to win the empty FLEX
    # slot outright.
    extended_players = {**_PLAYERS, "p_wr2": {
        "first_name": "W", "last_name": "R2", "position": "WR", "team": "AAA",
        "age": 23, "years_exp": 1, "active": True,
    }}
    custom_rosters = [
        {"roster_id": 1, "owner_id": "U1",
         "players": ["p_qb", "p_rb", "p_wr", "p_wr2"],
         "starters": ["p_qb", "p_rb", "p_wr", "0"],
         "settings": {"wins": 6, "losses": 3, "fpts": 1284, "fpts_decimal": 0,
                      "fpts_against": 1244, "fpts_against_decimal": 0}},
    ]
    extended_proj = _WEEKLY_PROJ + [
        {"player_id": "p_wr2", "stats": {"rec": 9.0, "rec_yd": 140.0, "rec_td": 2.0}},
    ]
    _seed(monkeypatch, tmp_path, _tracked(), extra={
        f"{V1}/league/L1/rosters": custom_rosters,
        f"{V1}/players/nfl": extended_players,
        f"{PROJECTIONS}/2026/10": extended_proj,
    })
    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/lineup")
    body = res.json()
    flex_row = next(d for d in body["diff"] if d["slot_label"] == "FLEX")
    assert flex_row["status"] == "suggested_swap"
    assert flex_row["optimal"]["player_id"] == "p_wr2"
    assert flex_row["delta"] > 0


def test_lineup_missed_row_when_current_starters_team_already_locked(monkeypatch, tmp_path):
    # p_rb (roster 1's current RB starter) plays for BBB, which is
    # "complete" in _SCHEDULE_2026 -- locked. Add a 4th player, p_rb3
    # (team AAA, still pre_game), projected much better than p_rb, so
    # `optimal_slots` picks p_rb3 for the dedicated RB slot. Since the
    # CURRENT starter's (p_rb's) team already locked, this must read as
    # "missed", not "suggested_swap" -- the user can no longer bench p_rb.
    extended_players = {**_PLAYERS, "p_rb3": {
        "first_name": "R", "last_name": "B3", "position": "RB", "team": "AAA",
        "age": 22, "years_exp": 0, "active": True,
    }}
    custom_rosters = [
        {"roster_id": 1, "owner_id": "U1",
         "players": ["p_qb", "p_rb", "p_wr", "p_rb3"],
         "starters": ["p_qb", "p_rb", "p_wr", "0"],
         "settings": {"wins": 6, "losses": 3, "fpts": 1284, "fpts_decimal": 0,
                      "fpts_against": 1244, "fpts_against_decimal": 0}},
    ]
    extended_proj = _WEEKLY_PROJ + [
        {"player_id": "p_rb3", "stats": {"rush_yd": 200.0, "rush_td": 2.0}},
    ]
    _seed(monkeypatch, tmp_path, _tracked(), extra={
        f"{V1}/league/L1/rosters": custom_rosters,
        f"{V1}/players/nfl": extended_players,
        f"{PROJECTIONS}/2026/10": extended_proj,
    })
    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/lineup")
    body = res.json()
    rb_row = next(d for d in body["diff"] if d["slot_label"] == "RB")
    assert rb_row["status"] == "missed"
    assert rb_row["current"]["player_id"] == "p_rb"
    assert rb_row["optimal"]["player_id"] == "p_rb3"


def test_lineup_week_locked_true_once_every_game_has_started(monkeypatch, tmp_path):
    all_locked = [{**g, "status": "complete"} for g in _SCHEDULE_2026]
    _seed(monkeypatch, tmp_path, _tracked(), extra={f"{SCHEDULE}/2026": all_locked})
    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/lineup")
    assert res.json()["week_locked"] is True


def test_lineup_returns_400_for_a_non_sleeper_provider(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(provider="espn", league_key="espn:E1:2026"))
    monkeypatch.setattr(app_mod, "_STORE", store)
    res = TestClient(create_app()).get("/api/leagues/espn:E1:2026/lineup")
    assert res.status_code == 400


def test_lineup_returns_404_for_an_unknown_league(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    monkeypatch.setattr(app_mod, "_STORE", store)
    res = TestClient(create_app()).get("/api/leagues/sleeper:UNKNOWN:2026/lineup")
    assert res.status_code == 404


def test_lineup_degrades_gracefully_when_schedule_fetch_fails(monkeypatch, tmp_path):
    """The schedule endpoint is unofficial -- a failure must not 502 the
    whole response, per the plan's Global Constraints. Everything just
    reads as unlocked."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)
    monkeypatch.setattr(app_mod, "_LINEUP_LEDGER", LineupLedger(tmp_path / "ffdo.db"))

    base_resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/players/nfl": _PLAYERS,
        f"{PROJECTIONS}/2026/10": _WEEKLY_PROJ,
    }

    class _FlakySchedule:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            if "/schedule/" in url:
                raise RuntimeError("GET ... failed after 4 attempts")
            for key, val in base_resp.items():
                if key in url:
                    return val
            return []
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FlakySchedule)
    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/lineup")
    assert res.status_code == 200
    assert res.json()["week_locked"] is False


def test_lineup_ledger_is_written_on_first_view_and_not_overwritten_on_second(
        monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _tracked())
    client = TestClient(create_app())
    first = client.get("/api/leagues/sleeper:L1:2026/lineup").json()
    second = client.get("/api/leagues/sleeper:L1:2026/lineup").json()
    assert first["ledger"]["recorded_at"] == second["ledger"]["recorded_at"]


def test_lineup_ledger_resolves_once_the_week_is_fully_locked(monkeypatch, tmp_path):
    all_locked = [{**g, "status": "complete"} for g in _SCHEDULE_2026]
    _seed(monkeypatch, tmp_path, _tracked())
    client = TestClient(create_app())
    client.get("/api/leagues/sleeper:L1:2026/lineup")   # first view -- records the row

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient",
                        _recording_client({f"{SCHEDULE}/2026": all_locked}))
    resolved = client.get("/api/leagues/sleeper:L1:2026/lineup").json()
    assert resolved["ledger"]["followed"] in ("full", "partial", "none")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_lineup_endpoint.py -v`
Expected: FAIL — `404` or a connection/route-not-found error on every test (the route doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

In `src/ffdo/api/app.py`:

1. Add new imports near the top of the file, alongside the existing `from ffdo.api.store import LeagueStore` line:

```python
from ffdo.api.lineup_ledger import LineupLedger
```

2. Add a new module-level instance near the existing `_STORE = LeagueStore(...)` (same `data/ffdo.db` file, a different table):

```python
_LINEUP_LEDGER = LineupLedger(Path("data") / "ffdo.db")
```

3. Inside `create_app()`, alongside the existing local imports (`from ffdo.ingest.sleeper import traded_picks as traded_picks_mod` and its neighbors), add:

```python
    from ffdo.ingest.sleeper import weekly_projections as weekly_projections_mod
    from ffdo.ingest.sleeper import schedule as schedule_mod
    from ffdo.engine import weekly_lineup as weekly_lineup_mod
```

4. Inside `create_app()`, alongside the existing cache-dict declarations (`projections_caches`, `season_proj_anchor_caches`, `roster_count_caches`), add:

```python
    weekly_proj_caches: dict[tuple[int, int], _TTLCache] = {}
    schedule_caches: dict[tuple[int, int], _TTLCache] = {}
```

5. Alongside the existing `_projections_cache_for`/`_season_proj_anchor_for`/`_roster_count_cache_for` helper functions, add:

```python
    def _weekly_proj_cache_for(season: int, week: int) -> _TTLCache:
        return weekly_proj_caches.setdefault((season, week), _TTLCache(ttl_seconds=900))

    def _schedule_cache_for(season: int, week: int) -> _TTLCache:
        return schedule_caches.setdefault((season, week), _TTLCache(ttl_seconds=900))
```

6. Add the route itself. Place it right after the existing `get_season` function (before the "Static mounts MUST be registered last" comment block):

```python
    @app.get("/api/leagues/{league_key}/lineup")
    def get_lineup(league_key: str) -> dict:
        """This week's optimal-lineup recommendation vs. your actual
        current starters, lock-aware. Sleeper-only: the weekly-projections
        and schedule/lock feeds this endpoint depends on have no ESPN
        equivalent in this codebase (see the plan's Global Constraints) --
        an ESPN league gets an honest 400 rather than a recommendation
        silently built on data that was never fetched for it.
        """
        lg = _load_league(league_key)
        if lg.provider != "sleeper":
            raise HTTPException(
                status_code=400, detail="Weekly lineup is Sleeper-only for now")

        sleeper = client_mod.SleeperClient()
        try:
            nfl = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))

            if lg.roster_id is None:
                # A league the user only observes -- nothing personal to
                # recommend, same posture as #2's `your_roster: null`.
                return {
                    "nfl_week": {"season": nfl.season, "week": nfl.week},
                    "week_locked": False, "swaps_suggested": 0, "diff": [],
                    "ledger": None,
                }

            profiles, _espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            rosters = rosters_mod.fetch(sleeper, lg.provider_league_id)
            current_starters = rosters_mod.raw_starters(
                sleeper, lg.provider_league_id, lg.roster_id)
            weekly_proj = _weekly_proj_cache_for(nfl.season, nfl.week).get(
                lambda: weekly_projections_mod.fetch(sleeper, nfl.season, nfl.week))

            try:
                games = _schedule_cache_for(nfl.season, nfl.week).get(
                    lambda: schedule_mod.week_games(sleeper, nfl.season, nfl.week))
            except (httpx.HTTPError, RuntimeError) as exc:
                # Unofficial, undocumented endpoint -- degrade to "nothing
                # is locked" rather than fail the whole request.
                logging.getLogger("ffdo.api").warning(
                    "lineup: schedule fetch failed for %s week %s (%s) -- "
                    "treating nothing as locked", lg.league_key, nfl.week, exc)
                games = []
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(
                status_code=502, detail="Couldn't reach Sleeper, try again") from exc
        finally:
            sleeper.close()

        all_teams = frozenset(p.team for p in profiles.values() if p.team)
        bye_teams = schedule_mod.bye_teams(games, all_teams)
        locked_teams = schedule_mod.locked_teams(games)
        locked_now = schedule_mod.week_locked(games)

        all_pids = {pid for r in rosters for pid in r.player_ids}
        valued = weekly_lineup_mod.weekly_value(
            all_pids, lg, weekly_points=weekly_proj, profiles=profiles,
            bye_teams=bye_teams)
        optimal = weekly_lineup_mod.optimal_slots(valued, lg)
        diff_rows = weekly_lineup_mod.diff(
            current_starters, optimal, locked_teams, valued, profiles, lg)

        record = _LINEUP_LEDGER.record_if_absent(
            lg.league_key, nfl.season, nfl.week, optimal, current_starters)
        if record.followed is None and locked_now:
            _LINEUP_LEDGER.resolve(lg.league_key, nfl.season, nfl.week, current_starters)
            record = _LINEUP_LEDGER.get(lg.league_key, nfl.season, nfl.week)

        def _player_json(pid: str | None) -> dict | None:
            if pid is None:
                return None
            prof = profiles.get(pid)
            vp = valued.get(pid)
            return {
                "player_id": pid,
                "name": prof.full_name if prof else pid,
                "team": prof.team if prof else None,
                "value": round(vp.vor, 1) if vp is not None else 0.0,
            }

        return {
            "nfl_week": {"season": nfl.season, "week": nfl.week},
            "week_locked": locked_now,
            "swaps_suggested": sum(1 for d in diff_rows if d.status == "suggested_swap"),
            "diff": [
                {"slot_index": d.slot_index, "slot_label": d.slot_label,
                 "status": d.status,
                 "current": _player_json(d.current_player_id),
                 "optimal": _player_json(d.optimal_player_id),
                 "delta": d.delta}
                for d in diff_rows
            ],
            "ledger": {"recorded_at": record.recorded_at, "followed": record.followed},
        }
```

(`rosters_mod`, `client_mod`, `nfl_state_mod`, `players_cache`, `nfl_state_cache`, `_load_players`, `HTTPException`, `httpx`, `logging` are all already imported/defined earlier in this file from #2's work -- do not re-import them.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_lineup_endpoint.py -v`
Expected: PASS, all 9 tests.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/api/app.py tests/api/test_lineup_endpoint.py
git commit -m "feat: GET /api/leagues/{key}/lineup -- weekly lineup diff + ledger"
```

---

## Task 9: Frontend — the Lineup tab

**Files:**
- Modify: `src/ffdo/web/season/season.js`
- Modify: `src/ffdo/web/season/season.css`

> No automated frontend tests (repo convention, same as #2). Manual verification in Step 5.

- [ ] **Step 1: Read the current file**

Read `src/ffdo/web/season/season.js` in full before editing (it changed shape across #2's Task 12 and its final-review fix wave — do not assume the exact line numbers from the design spec's earlier draft; find the real current structure: module state near the top, `renderRightPanel()` building the tab bar, `renderPower()`/`renderCapital()` as the two existing tab bodies, `mountSeason`/`load`/`render`).

- [ ] **Step 2: Add module state and the lazy fetch**

Near the existing `let _panel = "power", _pos = "OVR", _scope = "starters";` module state line, add:

```javascript
let _lineupData = null;   // null until the Lineup tab has been opened at least once
```

Change the default `_panel` value from `"power"` to `"lineup"` so the Lineup tab is what the screen opens on:

```javascript
let _panel = "lineup", _pos = "OVR", _scope = "starters";
```

Find where `_panel` gets reset inside `mountSeason` (search for `_panel = "power";` near the module's reset/mount logic) and change that reset to `"lineup"` too. **Also reset `_lineupData = null;` in that same block** — without it, switching from one tracked league to another would leave the PREVIOUS league's lineup data in `_lineupData`, and since `renderRightPanel()`'s lazy-load check is `if (_lineupData === null) loadLineup();`, a stale non-null value would skip fetching the new league's data entirely and silently render the wrong league's lineup.

- [ ] **Step 3: Add the tab bar entry and lazy-load trigger**

Find `renderRightPanel()`'s tab-bar template (the `Array.isArray(_data.draft_capital) ? ...` block from #2's final fix wave). Change it so the tab bar ALWAYS includes Lineup, regardless of `draft_capital`:

```javascript
function renderRightPanel() {
  const showCapital = Array.isArray(_data.draft_capital);
  const tabBar = `
    <div class="panel-tabs">
      <button data-panel-tab="lineup" class="${_panel === "lineup" ? "on" : ""}">Lineup</button>
      <button data-panel-tab="power" class="${_panel === "power" ? "on" : ""}">Power ranking</button>
      ${showCapital ? `<button data-panel-tab="capital" class="${_panel === "capital" ? "on" : ""}">Draft capital</button>` : ""}
    </div>`;
  if (_panel === "lineup") {
    if (_lineupData === null) {
      loadLineup();
      return tabBar + `<div class="lineup-loading">Loading this week's lineup…</div>`;
    }
    return tabBar + renderLineup();
  }
  return tabBar + (_panel === "capital" ? renderCapital() : renderPower());
}
```

(Match this against whatever the real current function signature/body actually is per Step 1 -- the tab-bar button markup and class-toggling convention should mirror the existing `power`/`capital` buttons exactly; only the structure shown above is new.)

- [ ] **Step 4: Add `loadLineup` and `renderLineup`**

Add these two new functions near `renderCapital`/`renderPower` (same file):

```javascript
async function loadLineup() {
  try {
    const res = await fetch(`/api/leagues/${encodeURIComponent(_key)}/lineup`);
    if (!res.ok) {
      _lineupData = { error: (await res.json().catch(() => ({}))).detail || "Couldn't load the lineup" };
    } else {
      _lineupData = await res.json();
    }
  } catch (e) {
    _lineupData = { error: "Couldn't load the lineup" };
  }
  render();
}

function renderLineup() {
  if (_lineupData.error) {
    return `<div class="lineup-error">${escapeHtml(_lineupData.error)}</div>`;
  }
  const d = _lineupData;
  if (d.diff.length === 0) {
    return `<div class="lineup-empty">Roster not available</div>`;
  }
  const header = d.week_locked
    ? `Week ${d.nfl_week.week} — locked, review below`
    : `Week ${d.nfl_week.week} lineup — ${d.swaps_suggested} swap${d.swaps_suggested === 1 ? "" : "s"} suggested`;

  const rows = d.diff.map(row => {
    const slot = `<span class="slot-chip">${escapeHtml(row.slot_label)}</span>`;
    if (row.status === "match") {
      const cur = row.current
        ? `${escapeHtml(row.current.name)} <span class="lineup-team">${escapeHtml(row.current.team || "")}</span> · ${row.current.value}`
        : `<span class="lineup-team">empty</span>`;
      return `<div class="lineup-row lineup-match">${slot}<span class="lineup-current">${cur}</span></div>`;
    }
    const curName = row.current ? escapeHtml(row.current.name) : "empty";
    const optName = row.optimal ? escapeHtml(row.optimal.name) : "";
    const sign = row.delta > 0 ? "+" : "";
    if (row.status === "suggested_swap") {
      return `<div class="lineup-row lineup-swap">
        ${slot}
        <span class="lineup-current lineup-bench-out">${curName}</span>
        <span class="lineup-arrow">→</span>
        <span class="lineup-optimal">${optName}</span>
        <span class="lineup-delta">${sign}${row.delta} pts</span>
      </div>`;
    }
    return `<div class="lineup-row lineup-missed">
      ${slot}
      <span class="lineup-current lineup-bench-out">${curName}</span>
      <span class="lineup-missed-label">missed — ${optName} already locked out</span>
    </div>`;
  }).join("");

  return `<div class="lineup-header">${escapeHtml(header)}</div><div class="lineup-list">${rows}</div>`;
}
```

- [ ] **Step 5: Add CSS**

Read `src/ffdo/web/season/season.css` first to match its existing selector/token conventions (`.pr-row`, `.slot-chip`, `.you-badge`, etc. from #2), then append:

```css
.lineup-header { font-size: 13px; color: var(--muted); margin-bottom: 10px; }
.lineup-list { display: flex; flex-direction: column; gap: 6px; }
.lineup-row {
  display: grid;
  grid-template-columns: 64px 1fr auto auto;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.lineup-match { opacity: 0.85; }
.lineup-swap {
  border-color: color-mix(in oklch, var(--accent) 40%, transparent);
  background: color-mix(in oklch, var(--accent) 8%, transparent);
}
.lineup-missed { opacity: 0.6; }
.lineup-bench-out { text-decoration: line-through; color: var(--muted); }
.lineup-arrow { color: var(--accent); }
.lineup-optimal { color: var(--text); font-weight: 600; }
.lineup-delta { color: var(--accent); font-variant-numeric: tabular-nums; }
.lineup-missed-label { color: var(--muted); font-size: 12px; }
.lineup-team { color: var(--muted); font-size: 12px; }
.lineup-loading, .lineup-empty, .lineup-error { color: var(--muted); padding: 20px 0; }
```

- [ ] **Step 6: Wire the tab-click handler**

Find the existing click handler that sets `_panel = panelBtn.dataset.panelTab; render();` (from #2's `mountSeason` wiring) and confirm it already handles a `data-panel-tab="lineup"` click generically (it should, since it just reads `dataset.panelTab` and re-renders) — no change needed there, but verify by reading it. If the existing handler special-cases `"power"`/`"capital"` by name instead of being generic, generalize it to accept any tab value from `dataset.panelTab`.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (no Python touched in this task).

- [ ] **Step 8: Node syntax check**

Run: `node --check src/ffdo/web/season/season.js`
Expected: no output (valid syntax).

- [ ] **Step 9: Commit**

```bash
git add src/ffdo/web/season/season.js src/ffdo/web/season/season.css
git commit -m "feat: season screen Lineup tab -- weekly diff, lazy-loaded, shown first"
```

---

## Task 10: README + browser smoke test

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update `README.md`**

Add one line under the existing season-view description added in #2 (find that paragraph and add this sentence to the end of it, or as a new short paragraph immediately after):

```markdown
The season screen's **Lineup** tab shows this week's optimal starting
lineup against what you actually have set, lock-aware (a player whose
game has already started is never suggested as a swap).
```

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, full count from all 9 prior tasks.

- [ ] **Step 3: Browser smoke (manual — the executor or controller)**

Start the server against the pinned dev league (`uv run python scripts/seed_dev_league.py Schroedes`, then `uv run uvicorn ffdo.api.app:app --port 8150`), open a tracked, drafted league, and in the browser:

- The season screen opens on the **Lineup** tab (not Power ranking).
- The tab bar reads `[Lineup | Power ranking]` for a redraft/keeper league, `[Lineup | Power ranking | Draft capital]` for a dynasty league with tradeable picks.
- A `match` row renders plainly; if any bench player currently outscores a starter and their game hasn't started, a `suggested_swap` row renders highlighted with a `+N.N pts` delta.
- Switching to Power ranking and back to Lineup does not re-fetch `/lineup` a second time (check devtools network tab) — confirms the `_lineupData` cache-until-remount behavior.
- No console errors.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "chore: document the Lineup tab in the README"
```

---

## Self-Review Notes (for the plan author, not a task)

- **Spec coverage:** §1.1 goals -- solve (Task 5), diff (Task 6), lock-awareness (Task 3+6), ledger (Task 7), third tab shown first (Task 9) -- all covered. §1.2 non-goals -- no write-back, no multi-week, no ESPN weekly ingest -- respected (Task 8's 400 for non-Sleeper). §3-§6 ingest/engine/ledger/API sections map 1:1 to Tasks 2-3-5-6-7-8. §7 testing plan's specific cases (bye exclusion, hard-out exclusion, FLEX-before-dedicated ordering, lock states, ledger overwrite behavior, error contract) are each a named test above, not a generic "add tests" placeholder. §9's `INJURY_OUT_STATUSES` promotion is Task 1.
- **Refinements made during planning, flagged in Global Constraints rather than silently applied:** slot-indexing uses `starting_slots` not `roster_positions` (the spec's own §2.1 already said this correctly after its self-review, carried through consistently here); `raw_starters` as new ingest (spec already called this out explicitly); the dedicated-then-FLEX two-phase ordering requirement for `optimal_slots` (an implementation-correctness detail the spec's prose didn't spell out at the algorithm level -- Task 5 has a dedicated test for it); `week_locked`/ledger-resolution using "started" not "finished" as the lock criterion (a precision refinement of the spec's §5 prose, same intent).
- **Type consistency check:** `WeeklyProjection`/`SlotDiff` (Task 1) match their use in Tasks 2/5/6/8 exactly. `weekly_value`/`optimal_slots`/`diff` signatures are identical between their Task 5/6 definitions and Task 8's call sites. `LineupLedger`/`LineupRecord`'s method names and return shapes (Task 7) match Task 8's usage (`record_if_absent`, `.get`, `.resolve`, `.recommended`, `.followed`, `.recorded_at`) exactly.
