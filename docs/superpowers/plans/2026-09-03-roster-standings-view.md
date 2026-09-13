# Roster & Standings View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the foundation's "Season mode — coming soon" placeholder with a real season screen: a luck-adjusted league power ranking (overall + QB/RB/WR/TE, starters/full toggle), your-roster panel, provider standings, and a Sleeper dynasty draft-capital ranking — all from one new endpoint.

**Architecture:** A self-contained `season` bounded context. New ingest parsers (`nfl_state`, `rosters`, `actuals`, `traded_picks`, plus ESPN variants) feed a new engine seam `ros_value.roster_value(...)` (rest-of-season VOR for redraft, blended-projection × age-curve for dynasty — the function #4 later replaces) and `power_ranking.rank(...)` (reuses the existing `engine/roster.team_lineup`). `GET /api/leagues/{key}/season` assembles the payload; `web/season/season.js` renders the two-panel screen, mounted by `board.js` when the draft is complete. The draft board is untouched.

**Tech Stack:** Python 3.12, FastAPI, `httpx` (`MockTransport` in tests), stdlib only for new logic, pytest. Frontend is dependency-free vanilla JS/CSS served by `StaticFiles`.

**Spec:** `docs/superpowers/specs/2026-09-03-roster-standings-view-design.md`

## Global Constraints

- **Python** `>=3.12`. **No new runtime dependencies.**
- **Stacked PR:** this branch (`claude/roster-standings-view`) is stacked on `claude/multi-league-fantasy-dashboard-e0d264` (PR #24). All the foundation's types and routes exist. Base every task on that branch's HEAD.
- **Layer rule:** nothing above `ffdo/ingest/` sees provider JSON keys in raw form; adapters translate at that boundary. `ffdo/domain/models.py` and `ffdo/engine/*` have no I/O.
- **Frozen dataclasses:** new domain types are `@dataclass(frozen=True, slots=True)`.
- **League key:** `"{provider}:{provider_league_id}:{season}"` — already the URL path segment; `_load_league(key)` resolves it (404 on miss).
- **`TrackedLeague` field is `fmt`; the JSON key / `resolved_format` property is `format`.** `resolved_format` returns `format_override or fmt` ∈ `{"redraft","keeper","dynasty"}`.
- **The `roster_value` signature and `dict[str, ValuedPlayer]` return are frozen** (spec §4.1) — #4's replacement must match. Any change to it after Task 7 is a plan defect, not a refactor.
- **Credentials never in a response body.** New types carry no cookies; ESPN cookies come from `_STORE.get_credential("espn")` via `_require_espn_credential(action)`.
- **Error contract:** 404 unknown league; 400 ESPN connect-first / expired (reuse `_require_espn_credential`); 502 on `(httpx.HTTPError, RuntimeError)` from a provider (the foundation's pattern); a rostered player absent from `profiles`/`valued` is silently omitted (contributes 0), logged at debug.
- **Test isolation:** every `tests/api/` test runs against a `tmp_path` DB via the autouse fixture in `tests/api/conftest.py`; no test hits the network or a real `data/` file.
- **Commit after every task.** Prefix: `feat:` / `refactor:` / `test:` / `chore:`.
- **`uv run pytest` green at the end of every task.** The one acceptable warning is the pre-existing `StarletteDeprecationWarning` about `starlette.testclient`.

---

## File Structure

**Created:**
- `src/ffdo/ingest/nfl_state.py` — `current_week(sleeper) -> NflWeek` (Sleeper `/state/nfl`).
- `src/ffdo/ingest/rosters.py` — `fetch(sleeper, league_id) -> list[RosterEntry]` (Sleeper rosters + users → current player lists, starters, record, points).
- `src/ffdo/ingest/actuals.py` — `points_so_far(sleeper, league_id, through_week) -> dict[str, float]` (Sleeper matchups → banked points per player).
- `src/ffdo/ingest/sleeper/__init__.py` — new subpackage marker.
- `src/ffdo/ingest/sleeper/traded_picks.py` — `capital(...) -> list[DraftPickAsset]` (Sleeper `/traded_picks` → pick ownership + projected slots).
- `src/ffdo/ingest/espn/rosters.py` — `fetch(espn, league_id, season, crosswalk) -> tuple[list[RosterEntry], NflWeek]`.
- `src/ffdo/ingest/espn/actuals.py` — `points_so_far(mroster_raw, crosswalk, through_week) -> dict[str, float]`.
- `src/ffdo/engine/dynasty_curve.py` — `multiplier(position, age, years_exp) -> float` (coarse age/experience curve; #4 replaces).
- `src/ffdo/engine/ros_value.py` — `roster_value(...) -> dict[str, ValuedPlayer]` (**the swappable seam**).
- `src/ffdo/engine/power_ranking.py` — `rank(rosters, valued, league, standings_rank, *, position, scope) -> list[PowerRow]`.
- `src/ffdo/web/season/season.js`, `src/ffdo/web/season/season.css` — the season screen.
- Tests: `tests/ingest/test_nfl_state.py`, `tests/ingest/test_rosters.py`, `tests/ingest/test_actuals.py`, `tests/ingest/sleeper/__init__.py`, `tests/ingest/sleeper/test_traded_picks.py`, `tests/ingest/espn/test_rosters.py`, `tests/ingest/espn/test_actuals.py`, `tests/engine/test_dynasty_curve.py`, `tests/engine/test_ros_value.py`, `tests/engine/test_power_ranking.py`, `tests/api/test_season_endpoint.py`.

**Modified:**
- `src/ffdo/domain/models.py` — add `NflWeek`, `RosterEntry`, `PowerRow`, `DraftPickAsset`.
- `src/ffdo/domain/constants.py` — add `NFL_BYE_WEEKS`.
- `src/ffdo/ingest/teams.py` — extract `_display_names(users) -> dict[str, str]`; `parse` uses it.
- `src/ffdo/api/app.py` — `_nfl_state_cache`, `_season_proj_anchor_for(season)`, `_roster_count_cache`; `GET /api/leagues/{league_key}/season`; `needs_attention` in `list_leagues_endpoint`.
- `src/ffdo/web/board/board.js` — `refresh()` dynamic-imports `season.js` and calls `mountSeason` on `draft_status == "complete"`; delete `renderSeasonMode`.
- `src/ffdo/web/app.css` — remove the `.card.season-mode` / `.stat-grid` / `.chip` placeholder rules (moved to `season.css`, superseded).
- `tests/ingest/test_teams.py` — add one case asserting the extracted helper is used (no behavior change).
- `README.md` — one line noting the season view.

**Deleted:** none.

---

## Task 1: Domain types + bye-week constant

**Files:**
- Modify: `src/ffdo/domain/models.py`
- Modify: `src/ffdo/domain/constants.py`
- Test: `tests/domain/test_models.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `NflWeek(season: int, week: int, season_type: str, complete: bool)` — frozen, slots.
  - `RosterEntry(roster_id: int, team_name: str, player_ids: tuple[str, ...], starter_ids: tuple[str, ...], wins: int, losses: int, ties: int, points_for: float, points_against: float)` — frozen, slots.
  - `PowerRow(roster_id: int, team_name: str, is_you: bool, value: float, bench_value: float, power_rank: int, standings_rank: int)` — frozen, slots. Property `delta -> int` returns `self.standings_rank - self.power_rank`.
  - `DraftPickAsset(season: int, round: int, projected_slot: int | None, current_owner_roster_id: int, original_roster_id: int, via_team_name: str | None)` — frozen, slots. Property `label -> str`: `f"{round}.{projected_slot:02d}"` when `projected_slot is not None`, else `f"R{round}"`.
  - `constants.NFL_BYE_WEEKS: dict[int, dict[str, int]]` — `{season: {team_abbrev: bye_week}}`.

- [ ] **Step 1: Write the failing test**

Add to `tests/domain/test_models.py`:

```python
from ffdo.domain.models import DraftPickAsset, NflWeek, PowerRow, RosterEntry


def test_nfl_week_constructs():
    w = NflWeek(season=2026, week=10, season_type="regular", complete=False)
    assert w.week == 10 and w.complete is False


def test_roster_entry_holds_tuples():
    r = RosterEntry(roster_id=1, team_name="X", player_ids=("a", "b"),
                    starter_ids=("a",), wins=6, losses=3, ties=0,
                    points_for=1284.6, points_against=1244.0)
    assert r.player_ids == ("a", "b") and r.starter_ids == ("a",)


def test_power_row_delta_is_standings_minus_power():
    row = PowerRow(roster_id=1, team_name="X", is_you=True, value=340.0,
                   bench_value=0.0, power_rank=2, standings_rank=4)
    assert row.delta == 2          # roster ranks 2 spots better than record


def test_draft_pick_asset_label():
    with_slot = DraftPickAsset(season=2027, round=1, projected_slot=2,
                               current_owner_roster_id=7, original_roster_id=11,
                               via_team_name="Picks R Us")
    assert with_slot.label == "1.02"
    round_only = DraftPickAsset(season=2028, round=2, projected_slot=None,
                                current_owner_roster_id=7, original_roster_id=7,
                                via_team_name=None)
    assert round_only.label == "R2"


def test_nfl_bye_weeks_has_current_season():
    from ffdo.domain.constants import NFL_BYE_WEEKS
    assert 2026 in NFL_BYE_WEEKS
    assert NFL_BYE_WEEKS[2026]["ATL"] in range(4, 15)   # a real bye week
    assert len(NFL_BYE_WEEKS[2026]) == 32               # all teams
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/domain/test_models.py -q -k "nfl_week or roster_entry or power_row or draft_pick or bye_weeks"`
Expected: FAIL — `ImportError: cannot import name 'NflWeek'`.

- [ ] **Step 3: Write minimal implementation**

In `src/ffdo/domain/models.py`, after `TeamProfile` (keep `from typing import Any` already present from the foundation):

```python
@dataclass(frozen=True, slots=True)
class NflWeek:
    season: int
    week: int              # the upcoming / in-progress week (Sleeper display_week)
    season_type: str       # "pre" | "regular" | "post"
    complete: bool          # regular season over -> the view freezes values


@dataclass(frozen=True, slots=True)
class RosterEntry:
    roster_id: int
    team_name: str
    player_ids: tuple[str, ...]       # Sleeper player ids (ESPN ids crosswalked upstream)
    starter_ids: tuple[str, ...]
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float


@dataclass(frozen=True, slots=True)
class PowerRow:
    roster_id: int
    team_name: str
    is_you: bool
    value: float
    bench_value: float
    power_rank: int
    standings_rank: int

    @property
    def delta(self) -> int:
        return self.standings_rank - self.power_rank


@dataclass(frozen=True, slots=True)
class DraftPickAsset:
    season: int
    round: int
    projected_slot: int | None
    current_owner_roster_id: int
    original_roster_id: int
    via_team_name: str | None

    @property
    def label(self) -> str:
        if self.projected_slot is not None:
            return f"{self.round}.{self.projected_slot:02d}"
        return f"R{self.round}"
```

In `src/ffdo/domain/constants.py`, after `SEASON_LENGTH`:

```python
# NFL bye weeks by season. Hand-maintained -- update each August when the
# schedule is released. Team abbreviations match Sleeper's `team` field on
# PlayerProfile (e.g. "ARI", "BAL", "LAR"). Source: the published NFL
# regular-season schedule.
NFL_BYE_WEEKS: Final[dict[int, dict[str, int]]] = {
    2026: {
        "ARI": 8, "ATL": 5, "BAL": 7, "BUF": 7, "CAR": 14, "CHI": 5, "CIN": 10,
        "CLE": 9, "DAL": 10, "DEN": 12, "DET": 8, "GB": 5, "HOU": 6, "IND": 11,
        "JAX": 8, "KC": 10, "LAC": 12, "LAR": 8, "LV": 8, "MIA": 12, "MIN": 6,
        "NE": 14, "NO": 11, "NYG": 11, "NYJ": 9, "PHI": 9, "PIT": 5, "SEA": 8,
        "SF": 14, "TB": 9, "TEN": 10, "WAS": 12,
    },
}
```

> The 2026 bye weeks above are placeholders drawn from a plausible distribution. If the real 2026 schedule is available, replace them; the test only checks structure (32 teams, weeks 4–14). Flag in the commit message that the values need a real-schedule pass.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/domain/test_models.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/domain/models.py src/ffdo/domain/constants.py tests/domain/test_models.py
git commit -m "feat: season-view domain types (NflWeek, RosterEntry, PowerRow, DraftPickAsset) + bye weeks"
```

---

## Task 2: `nfl_state` ingest

**Files:**
- Create: `src/ffdo/ingest/nfl_state.py`
- Test: `tests/ingest/test_nfl_state.py`

**Interfaces:**
- Consumes: `SleeperClient`, `V1` from `ffdo.ingest.client`; `NflWeek` from Task 1.
- Produces: `current_week(sleeper: SleeperClient) -> NflWeek` — `GET /v1/state/nfl`.

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/test_nfl_state.py`:

```python
import httpx

from ffdo.ingest import nfl_state
from ffdo.ingest.client import SleeperClient


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def test_current_week_from_display_week():
    def handler(request):
        assert request.url.path == "/v1/state/nfl"
        return httpx.Response(200, json={
            "season": "2026", "season_type": "regular",
            "week": 10, "display_week": 10, "leg": 10,
        })

    w = nfl_state.current_week(_client(handler))
    assert w.season == 2026
    assert w.week == 10
    assert w.season_type == "regular"
    assert w.complete is False


def test_current_week_falls_back_to_week_when_no_display_week():
    def handler(request):
        return httpx.Response(200, json={"season": "2026", "season_type": "regular", "week": 3})

    assert nfl_state.current_week(_client(handler)).week == 3


def test_postseason_is_complete():
    def handler(request):
        return httpx.Response(200, json={
            "season": "2026", "season_type": "post", "week": 19, "display_week": 19})

    w = nfl_state.current_week(_client(handler))
    assert w.season_type == "post"
    assert w.complete is True


def test_week_past_18_is_complete_even_if_labeled_regular():
    def handler(request):
        return httpx.Response(200, json={
            "season": "2026", "season_type": "regular", "week": 19, "display_week": 19})

    assert nfl_state.current_week(_client(handler)).complete is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_nfl_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.ingest.nfl_state'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ffdo/ingest/nfl_state.py`:

```python
"""The current NFL week, from Sleeper's /state/nfl. The anchor for
'rest-of-season' math and which matchup week to read. Shared with
sub-project #3 (weekly optimal lineup)."""

from __future__ import annotations

from ffdo.domain.models import NflWeek
from ffdo.ingest.client import V1, SleeperClient


def current_week(sleeper: SleeperClient) -> NflWeek:
    raw = sleeper.get_json(f"{V1}/state/nfl")
    week = int(raw.get("display_week") or raw.get("week") or 0)
    season_type = raw.get("season_type") or "regular"
    return NflWeek(
        season=int(raw["season"]),
        week=week,
        season_type=season_type,
        complete=season_type == "post" or week > 18,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/test_nfl_state.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/nfl_state.py tests/ingest/test_nfl_state.py
git commit -m "feat: nfl_state.current_week — the current NFL week service"
```

---

## Task 3: `teams` refactor + `rosters` ingest (Sleeper)

**Files:**
- Modify: `src/ffdo/ingest/teams.py`
- Create: `src/ffdo/ingest/rosters.py`
- Test: `tests/ingest/test_rosters.py`
- Modify: `tests/ingest/test_teams.py`

**Interfaces:**
- Consumes: `SleeperClient`, `V1`; `RosterEntry` from Task 1.
- Produces:
  - `teams._display_names(users: list[dict]) -> dict[str, str]` — `{user_id: team_name_or_display_name}`, extracted from the current `parse` body.
  - `rosters.fetch(sleeper: SleeperClient, league_id: str) -> list[RosterEntry]` — one `RosterEntry` per roster, ordered by `roster_id`.

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/test_rosters.py`:

```python
import httpx

from ffdo.ingest import rosters
from ffdo.ingest.client import SleeperClient

_ROSTERS_RAW = [
    {"roster_id": 1, "owner_id": "u1",
     "players": ["100", "200", "300"], "starters": ["100", "200", "0"],
     "settings": {"wins": 6, "losses": 3, "ties": 0,
                  "fpts": 1284, "fpts_decimal": 60,
                  "fpts_against": 1244, "fpts_against_decimal": 0}},
    {"roster_id": 2, "owner_id": "u2",
     "players": None, "starters": None,
     "settings": {"wins": 3, "losses": 6, "ties": 0, "fpts": 1100, "fpts_against": 1200}},
]
_USERS_RAW = [
    {"user_id": "u1", "display_name": "user1", "metadata": {"team_name": "The Foobars"}},
    {"user_id": "u2", "display_name": "CoolTeam", "metadata": {}},
]


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def _handler(request):
    url = str(request.url)
    if url.endswith("/league/L1/rosters"):
        return httpx.Response(200, json=_ROSTERS_RAW)
    if url.endswith("/league/L1/users"):
        return httpx.Response(200, json=_USERS_RAW)
    raise AssertionError(url)


def test_fetch_maps_players_starters_and_record():
    out = rosters.fetch(_client(_handler), "L1")
    assert [r.roster_id for r in out] == [1, 2]
    r1 = out[0]
    assert r1.team_name == "The Foobars"
    assert r1.player_ids == ("100", "200", "300")
    assert r1.starter_ids == ("100", "200")          # "0" (empty slot) dropped
    assert r1.wins == 6 and r1.losses == 3
    assert r1.points_for == 1284.60                   # fpts + fpts_decimal/100
    assert r1.points_against == 1244.0


def test_fetch_handles_null_players_and_missing_decimals():
    out = rosters.fetch(_client(_handler), "L1")
    r2 = out[1]
    assert r2.player_ids == ()
    assert r2.starter_ids == ()
    assert r2.team_name == "CoolTeam"                 # falls back to display_name
    assert r2.points_for == 1100.0
```

Add to `tests/ingest/test_teams.py`:

```python
def test_display_names_helper_is_reused_by_parse():
    from ffdo.ingest.teams import _display_names
    users = [{"user_id": "u1", "display_name": "u1", "metadata": {"team_name": "Alpha"}}]
    assert _display_names(users) == {"u1": "Alpha"}
    # parse still works exactly as before
    out = teams.parse([{"roster_id": 1, "owner_id": "u1"}], users)
    assert out[1].display_name == "Alpha"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/test_rosters.py tests/ingest/test_teams.py -q`
Expected: `test_rosters.py` FAIL (`ModuleNotFoundError`), `test_teams.py::test_display_names_helper_is_reused_by_parse` FAIL (`ImportError: cannot import name '_display_names'`).

- [ ] **Step 3: Extract the helper in `teams.py`**

Replace `src/ffdo/ingest/teams.py`'s `parse` with a version that uses an extracted helper:

```python
def _display_names(users: list[dict[str, Any]]) -> dict[str, str]:
    """{user_id: team_name or display_name}. Team name from a user's
    metadata beats the raw display name; a user with neither is omitted."""
    out: dict[str, str] = {}
    for u in users:
        user_id = u.get("user_id")
        if user_id is None:
            continue
        metadata = u.get("metadata") or {}
        name = metadata.get("team_name") or u.get("display_name")
        if name:
            out[str(user_id)] = name
    return out


def parse(
    rosters: list[dict[str, Any]],
    users: list[dict[str, Any]],
) -> dict[int, TeamProfile]:
    names = _display_names(users)
    out: dict[int, TeamProfile] = {}
    for r in rosters:
        raw_roster_id = r.get("roster_id")
        if raw_roster_id is None:
            continue
        roster_id = int(raw_roster_id)
        owner_id = r.get("owner_id")
        name = names.get(str(owner_id)) if owner_id is not None else None
        out[roster_id] = TeamProfile(roster_id=roster_id,
                                     display_name=name or f"Team {roster_id}")
    return out
```

- [ ] **Step 4: Write `rosters.py`**

Create `src/ffdo/ingest/rosters.py`:

```python
"""Current rosters + standings for a Sleeper league, from
/league/<id>/rosters + /users. Distinct from the draft board, which reads
rosters off draft picks -- post-draft, adds/drops/trades have moved
players, so the live roster feed is authoritative."""

from __future__ import annotations

from ffdo.domain.models import RosterEntry
from ffdo.ingest.client import V1, SleeperClient
from ffdo.ingest.teams import _display_names


def _points(settings: dict, whole_key: str, decimal_key: str) -> float:
    whole = float(settings.get(whole_key) or 0)
    return whole + float(settings.get(decimal_key) or 0) / 100.0


def fetch(sleeper: SleeperClient, league_id: str) -> list[RosterEntry]:
    rosters_raw = sleeper.get_json(f"{V1}/league/{league_id}/rosters")
    users_raw = sleeper.get_json(f"{V1}/league/{league_id}/users")
    names = _display_names(users_raw)

    out: list[RosterEntry] = []
    for r in rosters_raw:
        raw_id = r.get("roster_id")
        if raw_id is None:
            continue
        roster_id = int(raw_id)
        settings = r.get("settings") or {}
        owner_id = r.get("owner_id")
        players = tuple(str(p) for p in (r.get("players") or []))
        starters = tuple(str(p) for p in (r.get("starters") or [])
                         if p not in ("0", 0, None))
        out.append(RosterEntry(
            roster_id=roster_id,
            team_name=(names.get(str(owner_id)) if owner_id is not None else None)
                      or f"Team {roster_id}",
            player_ids=players,
            starter_ids=starters,
            wins=int(settings.get("wins") or 0),
            losses=int(settings.get("losses") or 0),
            ties=int(settings.get("ties") or 0),
            points_for=_points(settings, "fpts", "fpts_decimal"),
            points_against=_points(settings, "fpts_against", "fpts_against_decimal"),
        ))
    out.sort(key=lambda e: e.roster_id)
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/test_rosters.py tests/ingest/test_teams.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/ingest/teams.py src/ffdo/ingest/rosters.py tests/ingest/test_rosters.py tests/ingest/test_teams.py
git commit -m "feat: rosters.fetch (Sleeper live rosters + standings); extract teams._display_names"
```

---

## Task 4: `actuals` ingest (Sleeper)

**Files:**
- Create: `src/ffdo/ingest/actuals.py`
- Test: `tests/ingest/test_actuals.py`

**Interfaces:**
- Consumes: `SleeperClient`, `V1`.
- Produces: `points_so_far(sleeper: SleeperClient, league_id: str, through_week: int) -> dict[str, float]` — `{player_id: banked_points}` summed over weeks `1..through_week`; a missing/`[]` week contributes nothing; `through_week <= 0` returns `{}`.

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/test_actuals.py`:

```python
import httpx

from ffdo.ingest import actuals
from ffdo.ingest.client import SleeperClient


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def test_sums_players_points_across_weeks():
    weeks = {
        1: [{"roster_id": 1, "players_points": {"100": 20.5, "200": 8.0}},
            {"roster_id": 2, "players_points": {"300": 15.0}}],
        2: [{"roster_id": 1, "players_points": {"100": 12.0}},
            {"roster_id": 2, "players_points": {"300": 9.5, "400": 4.0}}],
    }

    def handler(request):
        w = int(str(request.url).rsplit("/", 1)[-1])
        return httpx.Response(200, json=weeks.get(w, []))

    out = actuals.points_so_far(_client(handler), "L1", 2)
    assert out["100"] == 32.5      # 20.5 + 12.0
    assert out["200"] == 8.0       # only week 1
    assert out["300"] == 24.5
    assert out["400"] == 4.0


def test_empty_or_missing_week_contributes_nothing():
    def handler(request):
        w = int(str(request.url).rsplit("/", 1)[-1])
        if w == 1:
            return httpx.Response(200, json=[{"roster_id": 1, "players_points": {"100": 10.0}}])
        return httpx.Response(200, json=[])       # week 2 not played yet

    out = actuals.points_so_far(_client(handler), "L1", 2)
    assert out == {"100": 10.0}


def test_through_week_zero_returns_empty_and_makes_no_calls():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=[])

    assert actuals.points_so_far(_client(handler), "L1", 0) == {}
    assert calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_actuals.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ffdo/ingest/actuals.py`:

```python
"""Season-to-date actual fantasy points per player, from a Sleeper
league's weekly matchups. `players_points` is already scored under the
league's own settings, so no re-scoring here. Shared with sub-projects
#3 and #7."""

from __future__ import annotations

from ffdo.ingest.client import V1, SleeperClient


def points_so_far(
    sleeper: SleeperClient, league_id: str, through_week: int,
) -> dict[str, float]:
    banked: dict[str, float] = {}
    for week in range(1, max(0, through_week) + 1):
        rows = sleeper.get_json(f"{V1}/league/{league_id}/matchups/{week}")
        for row in rows or []:
            for pid, pts in (row.get("players_points") or {}).items():
                banked[str(pid)] = banked.get(str(pid), 0.0) + float(pts)
    return banked
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/test_actuals.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/actuals.py tests/ingest/test_actuals.py
git commit -m "feat: actuals.points_so_far — season-to-date banked points per player (Sleeper)"
```

---

## Task 5: `traded_picks` ingest (Sleeper)

**Files:**
- Create: `src/ffdo/ingest/sleeper/__init__.py` (empty)
- Create: `src/ffdo/ingest/sleeper/traded_picks.py`
- Create: `tests/ingest/sleeper/__init__.py` (empty)
- Create: `tests/ingest/sleeper/test_traded_picks.py`

**Interfaces:**
- Consumes: `SleeperClient`, `V1`; `DraftPickAsset` from Task 1.
- Produces:
  - `capital(sleeper, league_id, *, num_teams, rounds, standings_order, draft_years, team_names) -> list[DraftPickAsset]`
    - `standings_order: list[int]` — roster_ids **worst-to-best** (= projected order of the next rookie draft).
    - `draft_years: tuple[int, ...]` — the years to enumerate (e.g. `(2027, 2028)`).
    - `team_names: dict[int, str]` — `{roster_id: team_name}`, for `via_team_name`.
    - Returns every pick for every `(year, round, roster)`, with trades applied. `projected_slot` is set only for `min(draft_years)`.
    - On `httpx.HTTPStatusError` (404), `RuntimeError` (retry exhaustion), or an empty `[]`, returns the **implicit** set (every roster owns its own picks) — never raises. The endpoint decides whether to surface it.

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/sleeper/__init__.py` (empty) and `tests/ingest/sleeper/test_traded_picks.py`:

```python
import httpx

from ffdo.ingest.client import SleeperClient
from ffdo.ingest.sleeper import traded_picks

# 3-team league; standings worst->best = [3, 2, 1] (roster 3 picks first)
STANDINGS = [3, 2, 1]
NAMES = {1: "Alpha", 2: "Bravo", 3: "Charlie"}


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def _capital(traded_raw, years=(2027, 2028), rounds=1):
    def handler(request):
        assert request.url.path.endswith("/traded_picks")
        return httpx.Response(200, json=traded_raw)

    return traded_picks.capital(
        _client(handler), "L1",
        num_teams=3, rounds=rounds, standings_order=STANDINGS,
        draft_years=years, team_names=NAMES)


def test_no_trades_every_roster_owns_its_own_picks():
    out = _capital([])
    # 3 rosters x 2 years x 1 round = 6 assets
    assert len(out) == 6
    a = {(p.season, p.round, p.original_roster_id): p for p in out}
    own = a[(2027, 1, 3)]
    assert own.current_owner_roster_id == 3
    assert own.original_roster_id == 3
    assert own.via_team_name is None
    assert own.projected_slot == 1                 # roster 3 is worst -> pick 1
    assert a[(2027, 1, 1)].projected_slot == 3     # roster 1 is best -> pick 3
    assert a[(2028, 1, 1)].projected_slot is None  # year+1 -> round only


def test_single_trade_moves_ownership_and_sets_via():
    # roster 1's 2027 R1 pick was traded to roster 3
    traded = [{"season": "2027", "round": 1, "roster_id": 1,
               "owner_id": 3, "previous_owner_id": 1}]
    out = _capital(traded)
    pick = next(p for p in out if p.season == 2027 and p.original_roster_id == 1)
    assert pick.current_owner_roster_id == 3
    assert pick.via_team_name == "Charlie"         # current owner's name
    assert pick.projected_slot == 3                # slot follows ORIGINAL roster (1 = best)


def test_chain_trade_lands_on_final_owner():
    # roster 1's pick: 1 -> 2, then 2 -> 3
    traded = [
        {"season": "2027", "round": 1, "roster_id": 1, "owner_id": 2, "previous_owner_id": 1},
        {"season": "2027", "round": 1, "roster_id": 1, "owner_id": 3, "previous_owner_id": 2},
    ]
    out = _capital(traded)
    pick = next(p for p in out if p.season == 2027 and p.original_roster_id == 1)
    assert pick.current_owner_roster_id == 3


def test_404_returns_implicit_ownership_not_an_error():
    def handler(request):
        return httpx.Response(404, json={"error": "not found"})

    out = traded_picks.capital(
        _client(handler), "L1", num_teams=3, rounds=1,
        standings_order=STANDINGS, draft_years=(2027,), team_names=NAMES)
    assert len(out) == 3
    assert all(p.current_owner_roster_id == p.original_roster_id for p in out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/sleeper/test_traded_picks.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.ingest.sleeper'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ffdo/ingest/sleeper/__init__.py` (empty file) and `src/ffdo/ingest/sleeper/traded_picks.py`:

```python
"""Draft-pick ownership for a Sleeper dynasty/keeper league, from
/league/<id>/traded_picks. Untraded picks are implicit -- every roster
owns its own pick in every round of every upcoming draft year unless a
trade entry says otherwise. Projected slot = the ORIGINAL roster's
position in reverse-standings order (worst record drafts first)."""

from __future__ import annotations

import httpx

from ffdo.domain.models import DraftPickAsset
from ffdo.ingest.client import V1, SleeperClient


def _traded_raw(sleeper: SleeperClient, league_id: str) -> list[dict]:
    try:
        return sleeper.get_json(f"{V1}/league/{league_id}/traded_picks") or []
    except (httpx.HTTPError, RuntimeError):
        return []


def capital(
    sleeper: SleeperClient,
    league_id: str,
    *,
    num_teams: int,
    rounds: int,
    standings_order: list[int],
    draft_years: tuple[int, ...],
    team_names: dict[int, str],
) -> list[DraftPickAsset]:
    traded = _traded_raw(sleeper, league_id)

    # (year, round, original_roster) -> final current owner
    owner: dict[tuple[int, int, int], int] = {}
    for entry in traded:
        try:
            key = (int(entry["season"]), int(entry["round"]), int(entry["roster_id"]))
        except (KeyError, TypeError, ValueError):
            continue
        owner[key] = int(entry["owner_id"])

    next_year = min(draft_years) if draft_years else None
    out: list[DraftPickAsset] = []
    for year in draft_years:
        for rnd in range(1, rounds + 1):
            for original in range(1, num_teams + 1):
                current = owner.get((year, rnd, original), original)
                slot = (standings_order.index(original) + 1
                        if year == next_year and original in standings_order
                        else None)
                out.append(DraftPickAsset(
                    season=year,
                    round=rnd,
                    projected_slot=slot,
                    current_owner_roster_id=current,
                    original_roster_id=original,
                    via_team_name=team_names.get(current) if current != original else None,
                ))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/sleeper/test_traded_picks.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/sleeper/ tests/ingest/sleeper/
git commit -m "feat: traded_picks.capital — Sleeper dynasty pick ownership + projected slots"
```

---

## Task 6: `dynasty_curve` engine

**Files:**
- Create: `src/ffdo/engine/dynasty_curve.py`
- Test: `tests/engine/test_dynasty_curve.py`

**Interfaces:**
- Produces: `multiplier(position: str, age: int | None, years_exp: int | None) -> float` — clamped to `[0.55, 1.15]`.

- [ ] **Step 1: Write the failing test**

Create `tests/engine/test_dynasty_curve.py`:

```python
from ffdo.engine.dynasty_curve import multiplier


def test_young_rb_beats_old_rb():
    assert multiplier("RB", 24, 3) > multiplier("RB", 30, 8)


def test_prime_wr_near_one():
    assert 0.95 <= multiplier("WR", 26, 4) <= 1.15


def test_rookie_is_nudged_down():
    assert multiplier("WR", 22, 0) < multiplier("WR", 22, 3)


def test_none_age_is_treated_as_peak_no_penalty():
    assert multiplier("RB", None, 3) >= 1.0


def test_kicker_and_defense_are_age_agnostic():
    assert multiplier("K", 39, 15) == 1.0
    assert multiplier("DEF", None, None) == 1.0


def test_always_clamped_to_band():
    for pos in ("QB", "RB", "WR", "TE"):
        for age in range(19, 45):
            m = multiplier(pos, age, 5)
            assert 0.55 <= m <= 1.15
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_dynasty_curve.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ffdo/engine/dynasty_curve.py`:

```python
"""Coarse dynasty age/experience multiplier -- a PLACEHOLDER.

Sub-project #4 (post-draft valuation model) replaces this with a
data-driven aging model (breakout curves, position-specific decline
rates fit to real data, contract/keeper cost). Until then this is a
hand-drawn per-position curve: enough to stop a dynasty power ranking
from treating a 31-year-old RB and a 24-year-old RB as equal, not
enough to trust to a tenth of a point.

`engine.ros_value.roster_value` is the seam #4 swaps; this module is
called only from its dynasty branch.
"""

from __future__ import annotations

# (peak_lo, peak_hi, decline_per_year_after_peak_hi, floor)
_CURVE: dict[str, tuple[int, int, float, float]] = {
    "RB": (23, 26, 0.09, 0.55),
    "WR": (25, 28, 0.05, 0.60),
    "TE": (25, 29, 0.04, 0.70),
    "QB": (26, 33, 0.03, 0.75),
}
_PEAK = 1.10
_MIN, _MAX = 0.55, 1.15


def multiplier(position: str, age: int | None, years_exp: int | None) -> float:
    curve = _CURVE.get(position)
    if curve is None:            # K, DEF, anything unmapped
        return 1.0

    peak_lo, peak_hi, decline, floor = curve
    if age is None:
        base = _PEAK
    elif age < peak_lo:
        # ramping up to peak: 0.04/yr below peak_lo, so a 21-yo RB ~ 1.02
        base = _PEAK - 0.04 * (peak_lo - age)
    elif age <= peak_hi:
        base = _PEAK
    else:
        base = _PEAK - decline * (age - peak_hi)

    base = max(floor, base)

    if years_exp == 0:
        base *= 0.92
    elif years_exp == 1:
        base *= 0.98

    return max(_MIN, min(_MAX, base))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_dynasty_curve.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/dynasty_curve.py tests/engine/test_dynasty_curve.py
git commit -m "feat: dynasty_curve.multiplier — coarse age/experience curve (placeholder for #4)"
```

---

## Task 7: `ros_value` engine — the swappable seam

**Files:**
- Create: `src/ffdo/engine/ros_value.py`
- Test: `tests/engine/test_ros_value.py`

**Interfaces:**
- Consumes: `score_stats` from `ffdo.engine.scoring`; `vor.compute` from `ffdo.engine.vor`; `dynasty_curve.multiplier` from Task 6; `SeasonProjection`, `PlayerProfile`, `ValuedPlayer` from `ffdo.domain.models`.
- Produces (**frozen contract — #4 replaces this function keeping the signature**):

```python
def roster_value(
    player_ids: Iterable[str],
    league,                                  # duck-typed: .scoring_settings, .roster_positions, .starting_slots, .num_teams
    *,
    resolved_format: str,                    # "redraft" | "keeper" | "dynasty"
    season_proj: Mapping[str, SeasonProjection],
    profiles: Mapping[str, PlayerProfile],
    actuals: Mapping[str, float],
    weeks_played: int,
    season_weeks: int = 18,
) -> dict[str, ValuedPlayer]:
```

- `K = 4` — a module constant, the pace-blend half-life.
- Injured-out set: `_INJURY_OUT = frozenset({"IR", "PUP", "Out", "Sus"})`; a player is zeroed when `not profile.active` or `profile.injury_status in _INJURY_OUT`.

- [ ] **Step 1: Write the failing test**

Create `tests/engine/test_ros_value.py`:

```python
from ffdo.domain.models import LeagueProfile, PlayerProfile, SeasonProjection
from ffdo.engine import ros_value


def _league():
    return LeagueProfile(league_id="x", season=2026, num_teams=12,
                         roster_positions=("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"),
                         scoring_settings={"pass_yd": 0.04, "pass_td": 4.0,
                                           "rush_yd": 0.1, "rush_td": 6.0,
                                           "rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0},
                         budget=200)


def _profile(pid, pos, *, age=26, exp=4, active=True, injury=None):
    return PlayerProfile(player_id=pid, first_name=pos, last_name=pid, position=pos,
                         team="X", age=age, years_exp=exp, injury_status=injury, active=active)


def _proj(pid, stats):
    return SeasonProjection(player_id=pid, season=2026, stats=stats, last_modified=None)


# A full-season RB projection worth ~200 pts under _league()'s scoring:
_RB_STATS = {"rush_yd": 1200.0, "rush_td": 10.0, "rec": 40.0, "rec_yd": 300.0}


def test_early_season_stays_near_preseason():
    profiles = {"rb": _profile("rb", "RB")}
    proj = {"rb": _proj("rb", _RB_STATS)}
    # week 2, banked 24 pts (a ~12/game pace, well under the ~200/18 ≈ 11.1... actually on pace)
    v_wk2 = ros_value.roster_value(
        ["rb"], _league(), resolved_format="redraft", season_proj=proj,
        profiles=profiles, actuals={"rb": 24.0}, weeks_played=2)
    v_pre = ros_value.roster_value(
        ["rb"], _league(), resolved_format="redraft", season_proj=proj,
        profiles=profiles, actuals={}, weeks_played=0)
    # week-2 ROS is close to (preseason - banked); the blend barely moved it
    assert abs(v_wk2["rb"].projected_points - (v_pre["rb"].projected_points)) < 30


def test_underperformer_vor_drops_by_midseason():
    profiles = {"rb": _profile("rb", "RB"), "rb2": _profile("rb2", "RB")}
    proj = {"rb": _proj("rb", _RB_STATS), "rb2": _proj("rb2", _RB_STATS)}
    # rb2 is on pace; rb is way under (5 pts/game through 10 weeks = 50 banked)
    valued = ros_value.roster_value(
        ["rb", "rb2"], _league(), resolved_format="redraft", season_proj=proj,
        profiles=profiles, actuals={"rb": 50.0, "rb2": 110.0}, weeks_played=10)
    assert valued["rb"].vor < valued["rb2"].vor


def test_redraft_subtracts_banked_dynasty_does_not():
    profiles = {"rb": _profile("rb", "RB", age=24)}
    proj = {"rb": _proj("rb", _RB_STATS)}
    kw = dict(season_proj=proj, profiles=profiles, actuals={"rb": 120.0}, weeks_played=10)
    redraft = ros_value.roster_value(["rb"], _league(), resolved_format="redraft", **kw)
    dynasty = ros_value.roster_value(["rb"], _league(), resolved_format="dynasty", **kw)
    # dynasty keeps ~full-season value (x age curve); redraft is what's LEFT
    assert dynasty["rb"].projected_points > redraft["rb"].projected_points


def test_injured_out_player_is_zeroed():
    profiles = {"rb": _profile("rb", "RB", injury="IR")}
    proj = {"rb": _proj("rb", _RB_STATS)}
    valued = ros_value.roster_value(
        ["rb"], _league(), resolved_format="redraft", season_proj=proj,
        profiles=profiles, actuals={}, weeks_played=5)
    assert valued["rb"].projected_points == 0.0


def test_player_without_a_projection_is_omitted():
    profiles = {"rb": _profile("rb", "RB")}
    valued = ros_value.roster_value(
        ["rb", "ghost"], _league(), resolved_format="redraft", season_proj={"rb": _proj("rb", _RB_STATS)},
        profiles=profiles, actuals={}, weeks_played=0)
    assert "ghost" not in valued
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_ros_value.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ffdo/engine/ros_value.py`:

```python
"""Per-player value for the season screen -- THE SWAPPABLE SEAM.

Sub-project #4 (post-draft valuation model) replaces this whole function
with a multi-year model, keeping the exact signature and the
`dict[str, ValuedPlayer]` return so the swap is drop-in. Everything
downstream (`engine.power_ranking`, the /season endpoint) imports
`roster_value` by name and never looks inside.

What it does today:
  current_full = blend(preseason projection, season-to-date pace),
                 weight shifting toward pace as weeks_played grows
  redraft/keeper: value = max(0, current_full - banked)   [rest of season]
  dynasty:        value = current_full * dynasty_curve.multiplier(...)
Then engine.vor.compute puts it on a value-over-replacement scale.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ffdo.domain.models import PlayerProfile, SeasonProjection, ValuedPlayer
from ffdo.engine import dynasty_curve, vor
from ffdo.engine.scoring import score_stats

K = 4  # pace-blend half-life: at weeks_played == K, pace and preseason weigh equally
_INJURY_OUT = frozenset({"IR", "PUP", "Out", "Sus"})


def _blended_full_season(
    preseason: float, banked: float, weeks_played: int, season_weeks: int,
) -> float:
    if weeks_played <= 0:
        return preseason
    pace_full = (banked / weeks_played) * season_weeks
    w = weeks_played / (weeks_played + K)
    return preseason * (1.0 - w) + pace_full * w


def roster_value(
    player_ids: Iterable[str],
    league,
    *,
    resolved_format: str,
    season_proj: Mapping[str, SeasonProjection],
    profiles: Mapping[str, PlayerProfile],
    actuals: Mapping[str, float],
    weeks_played: int,
    season_weeks: int = 18,
) -> dict[str, ValuedPlayer]:
    is_dynasty = resolved_format == "dynasty"
    value_pts: dict[str, float] = {}

    for pid in player_ids:
        proj = season_proj.get(pid)
        profile = profiles.get(pid)
        if proj is None or profile is None:
            continue

        preseason = score_stats(proj.stats, league.scoring_settings)
        banked = float(actuals.get(pid, 0.0))
        current_full = _blended_full_season(preseason, banked, weeks_played, season_weeks)

        if not profile.active or profile.injury_status in _INJURY_OUT:
            value_pts[pid] = 0.0
        elif is_dynasty:
            value_pts[pid] = current_full * dynasty_curve.multiplier(
                profile.position, profile.age, profile.years_exp)
        else:
            value_pts[pid] = max(0.0, current_full - banked)

    return vor.compute(value_pts, profiles, league)
```

> `vor.compute` sets `projected_points` to the raw pre-VOR points it was handed (see `engine/vor.py:40`) — so the tests above read `.projected_points` for "the value that fed VOR" and `.vor` for "value over replacement". Both are meaningful.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_ros_value.py -q`
Expected: PASS. If `test_early_season_stays_near_preseason`'s `< 30` bound is too tight or loose against the real `_league()` scoring, adjust the bound (not the implementation) — the point is "week 2 barely moves it".

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/ros_value.py tests/engine/test_ros_value.py
git commit -m "feat: ros_value.roster_value — format-aware per-player value (the seam #4 replaces)"
```

---

## Task 8: `power_ranking` engine

**Files:**
- Create: `src/ffdo/engine/power_ranking.py`
- Test: `tests/engine/test_power_ranking.py`

**Interfaces:**
- Consumes: `team_lineup` from `ffdo.engine.roster`; `RosterEntry`, `PowerRow`, `ValuedPlayer` from `ffdo.domain.models`.
- Produces:

```python
def rank(
    rosters: list[RosterEntry],
    valued: Mapping[str, ValuedPlayer],
    league,                               # .roster_positions, .starting_slots, .num_teams
    standings_rank: Mapping[int, int],    # roster_id -> provider standings position (1 = best)
    your_roster_id: int | None,
    *,
    position: str,                        # "OVR" | "QB" | "RB" | "WR" | "TE"
    scope: str,                           # "starters" | "full"
) -> list[PowerRow]:
```

- [ ] **Step 1: Write the failing test**

Create `tests/engine/test_power_ranking.py`:

```python
from ffdo.domain.models import LeagueProfile, PlayerProfile, RosterEntry, ValuedPlayer
from ffdo.engine import power_ranking


def _league():
    return LeagueProfile(league_id="x", season=2026, num_teams=3,
                         roster_positions=("QB", "RB", "WR", "FLEX", "BN", "BN"),
                         scoring_settings={}, budget=200)


def _vp(pid, pos, v):
    prof = PlayerProfile(player_id=pid, first_name=pos, last_name=pid, position=pos,
                         team="X", age=26, years_exp=4, injury_status=None, active=True)
    return ValuedPlayer(profile=prof, projected_points=v, adjusted_points=v,
                        vor=v, tier=1, adjustments={})


def _entry(rid, players, starters=()):
    return RosterEntry(roster_id=rid, team_name=f"T{rid}", player_ids=tuple(players),
                       starter_ids=tuple(starters), wins=0, losses=0, ties=0,
                       points_for=0.0, points_against=0.0)


# team 1: strong starters, empty bench.  team 2: weaker starters, deep bench.
VALUED = {
    "q1": _vp("q1", "QB", 30), "r1": _vp("r1", "RB", 40), "w1": _vp("w1", "WR", 35), "f1": _vp("f1", "RB", 20),
    "q2": _vp("q2", "QB", 25), "r2": _vp("r2", "RB", 22), "w2": _vp("w2", "WR", 20), "f2": _vp("f2", "WR", 18),
    "b2a": _vp("b2a", "RB", 15), "b2b": _vp("b2b", "WR", 14),
    "q3": _vp("q3", "QB", 10), "r3": _vp("r3", "RB", 12), "w3": _vp("w3", "WR", 11), "f3": _vp("f3", "TE", 5),
}
ROSTERS = [
    _entry(1, ["q1", "r1", "w1", "f1"]),
    _entry(2, ["q2", "r2", "w2", "f2", "b2a", "b2b"]),
    _entry(3, ["q3", "r3", "w3", "f3"]),
]
STANDINGS = {1: 2, 2: 1, 3: 3}   # team 2 leads the standings


def test_overall_starters_ranks_by_starting_lineup_value():
    rows = power_ranking.rank(ROSTERS, VALUED, _league(), STANDINGS, 1,
                              position="OVR", scope="starters")
    assert [r.roster_id for r in rows] == [1, 2, 3]
    assert rows[0].power_rank == 1 and rows[0].is_you is True
    assert rows[0].delta == STANDINGS[1] - 1        # 2 - 1 = +1


def test_full_scope_can_reorder_vs_starters():
    starters = power_ranking.rank(ROSTERS, VALUED, _league(), STANDINGS, None,
                                  position="OVR", scope="starters")
    full = power_ranking.rank(ROSTERS, VALUED, _league(), STANDINGS, None,
                              position="OVR", scope="full")
    # team 2's deep bench lifts its full value; the gap to team 1 shrinks
    s_gap = starters[0].value - next(r for r in starters if r.roster_id == 2).value
    f_gap = full[0].value - next(r for r in full if r.roster_id == 2).value
    assert f_gap < s_gap
    assert any(r.bench_value > 0 for r in full)
    assert all(r.bench_value == 0 for r in starters)


def test_position_ranking_respects_scope():
    # RB, starters: only the RB actually in a starting slot (dedicated or FLEX) counts
    rb_start = power_ranking.rank(ROSTERS, VALUED, _league(), STANDINGS, None,
                                  position="RB", scope="starters")
    rb_full = power_ranking.rank(ROSTERS, VALUED, _league(), STANDINGS, None,
                                 position="RB", scope="full")
    t2_start = next(r for r in rb_start if r.roster_id == 2).value
    t2_full = next(r for r in rb_full if r.roster_id == 2).value
    assert t2_full > t2_start          # b2a (benched RB) only counts under "full"


def test_delta_sign_positive_when_roster_beats_record():
    rows = power_ranking.rank(ROSTERS, VALUED, _league(), {1: 3, 2: 1, 3: 2}, None,
                              position="OVR", scope="starters")
    you = next(r for r in rows if r.roster_id == 1)
    assert you.power_rank == 1 and you.standings_rank == 3
    assert you.delta == 2             # ranks 2 spots better than the standings
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_power_ranking.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ffdo/engine/power_ranking.py`:

```python
"""League power ranking -- teams ordered by roster value, luck removed.

Overall value uses the existing greedy starting-lineup fill
(`engine.roster.team_lineup`), which handles FLEX / superflex from
`roster_positions` alone. Position rankings sum a team's value at one
position; under scope="starters" only players the lineup fill actually
started count (a WR started in FLEX counts toward WR)."""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.models import PowerRow, RosterEntry, ValuedPlayer
from ffdo.engine.roster import team_lineup

_POSITIONS = ("QB", "RB", "WR", "TE")


def _team_value(
    entry: RosterEntry,
    valued: Mapping[str, ValuedPlayer],
    league,
    *,
    position: str,
    scope: str,
) -> tuple[float, float]:
    """Returns (value, bench_value) for one team at one (position, scope)."""
    team_valued = {pid: valued[pid] for pid in entry.player_ids if pid in valued}
    lineup = team_lineup(team_valued, league)

    if position == "OVR":
        if scope == "full":
            return lineup.starting_vor + lineup.bench_vor, lineup.bench_vor
        return lineup.starting_vor, 0.0

    at_pos = {pid: vp for pid, vp in team_valued.items()
              if vp.profile.position == position}
    started = sum(vp.vor for pid, vp in at_pos.items() if pid in lineup.starters)
    if scope == "starters":
        return started, 0.0
    total = sum(vp.vor for vp in at_pos.values())
    return total, total - started


def rank(
    rosters: list[RosterEntry],
    valued: Mapping[str, ValuedPlayer],
    league,
    standings_rank: Mapping[int, int],
    your_roster_id: int | None,
    *,
    position: str,
    scope: str,
) -> list[PowerRow]:
    scored = []
    for entry in rosters:
        value, bench = _team_value(entry, valued, league, position=position, scope=scope)
        scored.append((entry, value, bench))

    scored.sort(key=lambda t: (-t[1], t[0].roster_id))   # value desc, roster_id for determinism

    return [
        PowerRow(
            roster_id=entry.roster_id,
            team_name=entry.team_name,
            is_you=entry.roster_id == your_roster_id,
            value=round(value, 1),
            bench_value=round(bench, 1),
            power_rank=i + 1,
            standings_rank=standings_rank.get(entry.roster_id, i + 1),
        )
        for i, (entry, value, bench) in enumerate(scored)
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_power_ranking.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/power_ranking.py tests/engine/test_power_ranking.py
git commit -m "feat: power_ranking.rank — overall + per-position, starters/full, delta vs standings"
```

---

## Task 9: ESPN `rosters` + `actuals` ingest

**Files:**
- Create: `src/ffdo/ingest/espn/rosters.py`
- Create: `src/ffdo/ingest/espn/actuals.py`
- Test: `tests/ingest/espn/test_rosters.py`
- Test: `tests/ingest/espn/test_actuals.py`

> **Unverified shape.** ESPN's `mTeam`/`mRoster` JSON is not verified against a live league in this sub-project (spec §1.2). Parse defensively; the test fixtures below are the contract; a follow-up validates and fixes against reality.

**Interfaces:**
- Consumes: `EspnClient`, `BASE` from `ffdo.ingest.espn.client`; a `crosswalk` object with `.to_sleeper(espn_id: str) -> str | None` (see note below); `RosterEntry`, `NflWeek`.
- Produces:
  - `rosters.fetch(espn: EspnClient, league_id: str, season: int, crosswalk) -> tuple[list[RosterEntry], NflWeek, dict]` — the third element is the raw `mRoster` payload, handed to `actuals.points_so_far` so it makes no second HTTP call.
  - `actuals.points_so_far(mroster_raw: dict, crosswalk, through_week: int) -> dict[str, float]`.

> **Crosswalk interface:** the board endpoint builds an `espn_crosswalk_mod.Crosswalk` (see `ffdo/ingest/espn/crosswalk.py`). Check what method maps an ESPN player id → Sleeper id; if it exposes a dict rather than a method, pass that dict and look up with `.get`. Task 10's endpoint wiring must match whatever this task settles on — record the exact call in this task's report.

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/espn/test_rosters.py`:

```python
import httpx

from ffdo.ingest.espn import rosters
from ffdo.ingest.espn.client import EspnClient


class _CW:
    """Minimal crosswalk stand-in: ESPN id -> Sleeper id."""
    def __init__(self, m): self._m = m
    def to_sleeper(self, espn_id): return self._m.get(str(espn_id))


_LEAGUE_RAW = {
    "settings": {
        "status": {"currentMatchupPeriod": 10},
        "scheduleSettings": {"matchupPeriodCount": 14},
    },
    "teams": [
        {"id": 1, "name": "Alpha",
         "record": {"overall": {"wins": 7, "losses": 2, "ties": 0,
                                "pointsFor": 1352.8, "pointsAgainst": 1190.0}},
         "roster": {"entries": [
             {"playerId": 1001, "lineupSlotId": 0},
             {"playerId": 1002, "lineupSlotId": 2},
             {"playerId": 1003, "lineupSlotId": 20},   # BN
             {"playerId": 9999, "lineupSlotId": 21},   # IR, and not in crosswalk
         ]}},
    ],
}
_CROSSWALK = _CW({"1001": "s1", "1002": "s2", "1003": "s3"})


def _client(handler):
    return EspnClient("s2", "{SWID}", base_delay=0, transport=httpx.MockTransport(handler))


def test_fetch_parses_teams_starters_and_week():
    def handler(request):
        return httpx.Response(200, json=_LEAGUE_RAW)

    entries, week, _raw = rosters.fetch(_client(handler), "L1", 2026, _CROSSWALK)
    assert len(entries) == 1
    e = entries[0]
    assert e.team_name == "Alpha"
    assert e.player_ids == ("s1", "s2", "s3")          # 9999 dropped (crosswalk miss)
    assert e.starter_ids == ("s1", "s2")               # slots 20/21 excluded
    assert e.wins == 7 and e.points_for == 1352.8
    assert week.week == 10 and week.complete is False


def test_week_past_matchup_period_count_is_complete():
    raw = {**_LEAGUE_RAW}
    raw["settings"] = {**raw["settings"], "status": {"currentMatchupPeriod": 15}}

    def handler(request):
        return httpx.Response(200, json=raw)

    _e, week, _r = rosters.fetch(_client(handler), "L1", 2026, _CROSSWALK)
    assert week.complete is True
```

Create `tests/ingest/espn/test_actuals.py`:

```python
from ffdo.ingest.espn import actuals


class _CW:
    def __init__(self, m): self._m = m
    def to_sleeper(self, espn_id): return self._m.get(str(espn_id))


_MROSTER = {
    "teams": [
        {"roster": {"entries": [
            {"playerId": 1001, "playerPoolEntry": {"player": {"stats": [
                {"statSourceId": 0, "scoringPeriodId": 1, "appliedTotal": 18.4},
                {"statSourceId": 0, "scoringPeriodId": 2, "appliedTotal": 12.1},
                {"statSourceId": 1, "scoringPeriodId": 3, "appliedTotal": 99.0},  # projection, ignore
                {"statSourceId": 0, "scoringPeriodId": 11, "appliedTotal": 20.0}, # past cutoff
            ]}}},
        ]}},
    ],
}


def test_sums_actual_period_totals_up_to_cutoff():
    out = actuals.points_so_far(_MROSTER, _CW({"1001": "s1"}), through_week=9)
    assert out["s1"] == 30.5     # 18.4 + 12.1; period 11 and the projection excluded


def test_crosswalk_miss_is_skipped():
    out = actuals.points_so_far(_MROSTER, _CW({}), through_week=9)
    assert out == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/espn/test_rosters.py tests/ingest/espn/test_actuals.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementations**

Create `src/ffdo/ingest/espn/rosters.py`:

```python
"""ESPN current rosters + standings + week, from mTeam/mRoster/mSettings.
ESPN player ids are crosswalked to Sleeper ids (the valuation source is
Sleeper's player pool regardless of provider). UNVERIFIED against a live
league -- see the spec. Every field access is defensive."""

from __future__ import annotations

from ffdo.domain.models import NflWeek, RosterEntry
from ffdo.ingest.espn.client import BASE, EspnClient

_BENCH_SLOTS = {20, 21}   # BN, IR


def fetch(espn: EspnClient, league_id: str, season: int, crosswalk):
    raw = espn.get_json(
        f"{BASE}/seasons/{season}/segments/0/leagues/{league_id}"
        "?view=mTeam&view=mRoster&view=mSettings")

    settings = raw.get("settings") or {}
    current_period = int((settings.get("status") or {}).get("currentMatchupPeriod") or 0)
    period_count = int((settings.get("scheduleSettings") or {}).get("matchupPeriodCount") or 18)
    week = NflWeek(
        season=season,
        week=current_period,
        season_type="regular" if current_period <= period_count else "post",
        complete=current_period > period_count,
    )

    entries: list[RosterEntry] = []
    for team in raw.get("teams") or []:
        rec = ((team.get("record") or {}).get("overall") or {})
        roster_entries = ((team.get("roster") or {}).get("entries") or [])
        player_ids: list[str] = []
        starter_ids: list[str] = []
        for re in roster_entries:
            sleeper_id = crosswalk.to_sleeper(re.get("playerId"))
            if sleeper_id is None:
                continue
            player_ids.append(sleeper_id)
            if re.get("lineupSlotId") not in _BENCH_SLOTS:
                starter_ids.append(sleeper_id)
        name = team.get("name") or " ".join(
            p for p in (team.get("location"), team.get("nickname")) if p
        ) or f"Team {team.get('id')}"
        entries.append(RosterEntry(
            roster_id=int(team["id"]),
            team_name=name,
            player_ids=tuple(player_ids),
            starter_ids=tuple(starter_ids),
            wins=int(rec.get("wins") or 0),
            losses=int(rec.get("losses") or 0),
            ties=int(rec.get("ties") or 0),
            points_for=float(rec.get("pointsFor") or 0.0),
            points_against=float(rec.get("pointsAgainst") or 0.0),
        ))
    entries.sort(key=lambda e: e.roster_id)
    return entries, week, raw
```

Create `src/ffdo/ingest/espn/actuals.py`:

```python
"""Season-to-date actual points per player from an already-fetched
mRoster payload -- no extra HTTP call. `statSourceId == 0` is the actual
stat line (1 is a projection); `scoringPeriodId` is the NFL week.
UNVERIFIED against a live league."""

from __future__ import annotations


def points_so_far(mroster_raw: dict, crosswalk, through_week: int) -> dict[str, float]:
    banked: dict[str, float] = {}
    for team in mroster_raw.get("teams") or []:
        for entry in ((team.get("roster") or {}).get("entries") or []):
            sleeper_id = crosswalk.to_sleeper(entry.get("playerId"))
            if sleeper_id is None:
                continue
            stats = (((entry.get("playerPoolEntry") or {}).get("player") or {}).get("stats") or [])
            for s in stats:
                if s.get("statSourceId") != 0:
                    continue
                if int(s.get("scoringPeriodId") or 0) > max(0, through_week):
                    continue
                banked[sleeper_id] = banked.get(sleeper_id, 0.0) + float(s.get("appliedTotal") or 0.0)
    return banked
```

> If the real `crosswalk` object from `ffdo/ingest/espn/crosswalk.py` has no `.to_sleeper` method, add a thin `to_sleeper` wrapper to it (small, in scope) OR adjust these two modules to use whatever it exposes — and record the decision in the task report so Task 10 wires it the same way.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/espn/ -q`
Expected: PASS (new tests + no regression in the existing ESPN ingest tests).

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/espn/rosters.py src/ffdo/ingest/espn/actuals.py tests/ingest/espn/test_rosters.py tests/ingest/espn/test_actuals.py
git commit -m "feat: ESPN rosters + actuals ingest (unverified shape — follow-up validates)"
```

---

## Task 10: `GET /api/leagues/{league_key}/season` endpoint

**Files:**
- Modify: `src/ffdo/api/app.py`
- Test: `tests/api/test_season_endpoint.py`

**Interfaces:**
- Consumes: everything from Tasks 1–9; existing `_load_league`, `_require_espn_credential`, `players_cache`, `_load_players`, `_load_projections`, `_projections_cache_for`, `_espn_crosswalk_cache_for`, `_espn_player_pool_cache_for`, `_TTLCache`.
- Produces: `GET /api/leagues/{league_key}/season` → the payload in spec §5.1.

> Large task. Work the steps in order. The suite stays green throughout (this is additive — no existing route changes).

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_season_endpoint.py`:

```python
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ffdo.api import app as app_mod
from ffdo.api.app import create_app
from ffdo.api.store import LeagueStore
from ffdo.domain.models import TrackedLeague
from ffdo.ingest.client import V1


def _tracked(**over):
    base = dict(
        league_key="sleeper:L1:2026", provider="sleeper", provider_league_id="L1",
        season=2026, name="Test League", user_id="U1", roster_id=1,
        draft_id="D1", draft_type="snake", draft_status="complete",
        num_teams=2, budget=None, rounds=15,
        roster_positions=("QB", "RB", "WR", "FLEX", "BN", "BN"),
        scoring_settings={"pass_yd": 0.04, "pass_td": 4.0, "rush_yd": 0.1,
                          "rush_td": 6.0, "rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0},
        fmt="redraft", format_override=None, raw_settings={"draft_rounds": 4},
        is_mock=False, tracked_at="2026-09-03T00:00:00+00:00",
        last_refreshed_at="2026-09-03T00:00:00+00:00",
    )
    return TrackedLeague(**{**base, **over})


_STATE = {"season": "2026", "season_type": "regular", "week": 10, "display_week": 10}
_PLAYERS = {
    "p_qb": {"first_name": "Q", "last_name": "B", "position": "QB", "team": "AAA",
             "age": 27, "years_exp": 5, "active": True},
    "p_rb": {"first_name": "R", "last_name": "B", "position": "RB", "team": "BBB",
             "age": 24, "years_exp": 3, "active": True},
    "p_wr": {"first_name": "W", "last_name": "R", "position": "WR", "team": "CCC",
             "age": 26, "years_exp": 4, "active": True},
    "p_rb2": {"first_name": "R", "last_name": "B2", "position": "RB", "team": "DDD",
              "age": 29, "years_exp": 7, "active": True},
}
_PROJ = [
    {"player_id": "p_qb", "last_modified": int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000),
     "stats": {"pass_yd": 4200.0, "pass_td": 30.0}},
    {"player_id": "p_rb", "last_modified": int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000),
     "stats": {"rush_yd": 1100.0, "rush_td": 9.0, "rec": 40.0, "rec_yd": 300.0}},
    {"player_id": "p_wr", "last_modified": int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000),
     "stats": {"rec": 90.0, "rec_yd": 1200.0, "rec_td": 8.0}},
    {"player_id": "p_rb2", "last_modified": int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000),
     "stats": {"rush_yd": 900.0, "rush_td": 6.0, "rec": 30.0, "rec_yd": 200.0}},
]
_ROSTERS = [
    {"roster_id": 1, "owner_id": "U1", "players": ["p_qb", "p_rb", "p_wr"],
     "starters": ["p_qb", "p_rb", "p_wr", "0"],
     "settings": {"wins": 6, "losses": 3, "fpts": 1284, "fpts_decimal": 0,
                  "fpts_against": 1244, "fpts_against_decimal": 0}},
    {"roster_id": 2, "owner_id": "U2", "players": ["p_rb2"],
     "starters": ["p_rb2"], "settings": {"wins": 3, "losses": 6, "fpts": 1100, "fpts_against": 1250}},
]
_USERS = [{"user_id": "U1", "display_name": "You"}, {"user_id": "U2", "display_name": "Them"}]
_MATCHUPS = [{"roster_id": 1, "players_points": {"p_rb": 90.0}},
             {"roster_id": 2, "players_points": {"p_rb2": 88.0}}]


def _recording_client(extra=None):
    resp = {
        f"{V1}/state/nfl": _STATE,
        f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS,
        f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS,
        "/projections/": _PROJ,
        "/matchups/": _MATCHUPS,
    }
    resp.update(extra or {})

    class _C:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            for key, val in resp.items():
                if key in url:
                    return val
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass
    return _C


def _seed(monkeypatch, tmp_path, tracked):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(tracked)
    monkeypatch.setattr(app_mod, "_STORE", store)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _recording_client())


def test_season_payload_is_well_formed(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _tracked())
    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season")
    assert res.status_code == 200
    body = res.json()
    assert body["nfl_week"]["week"] == 10
    assert body["your_roster"]["team_name"] == "You"
    assert len(body["your_roster"]["players"]) == 3
    assert all("value" in p and "starter" in p for p in body["your_roster"]["players"])
    pr = body["power_ranking"]
    assert set(pr["overall"]) == {"starters", "full"}
    assert set(pr["by_position"]) == {"QB", "RB", "WR", "TE"}
    assert len(pr["overall"]["starters"]) == 2
    assert body["draft_capital"] is None                      # redraft
    assert [s["roster_id"] for s in body["standings"]]          # provider standings present


def test_dynasty_league_gets_draft_capital(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _tracked(fmt="dynasty"))
    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season")
    body = res.json()
    assert isinstance(body["draft_capital"], list)
    assert body["draft_capital"][0]["picks"]                    # per-year pick chips


def test_404_for_unknown_league(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    monkeypatch.setattr(app_mod, "_STORE", store)
    assert TestClient(create_app()).get("/api/leagues/sleeper:ghost:2026/season").status_code == 404


def test_502_on_provider_outage(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)

    class _Dead:
        def __init__(self, *a, **k): pass
        def get_json(self, *a, **k):
            raise RuntimeError("GET ... failed after 4 attempts")
        def close(self): pass
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _Dead)
    assert TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season").status_code == 502


def test_cross_format_guard_same_roster_different_values(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _tracked(fmt="redraft"))
    redraft = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season").json()

    _seed(monkeypatch, tmp_path, _tracked(fmt="dynasty"))
    dynasty = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season").json()

    r_vals = {p["player_id"]: p["value"] for p in redraft["your_roster"]["players"]}
    d_vals = {p["player_id"]: p["value"] for p in dynasty["your_roster"]["players"]}
    assert r_vals != d_vals    # resolved_format actually threads through roster_value
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_season_endpoint.py -q`
Expected: FAIL — 404 for `/season` (route not defined) → assertions fail.

- [ ] **Step 3: Add the caches and the projection anchor helper**

In `create_app()` in `src/ffdo/api/app.py`, near the other `_TTLCache`s (after `_espn_crosswalk_caches`):

```python
    _nfl_state_cache = _TTLCache(ttl_seconds=3600)
    _season_proj_anchor_caches: dict[int, _TTLCache] = {}
    _roster_count_caches: dict[str, _TTLCache] = {}   # league_key -> count, for needs_attention

    def _season_proj_anchor_for(season: int) -> _TTLCache:
        return _season_proj_anchor_caches.setdefault(season, _TTLCache(ttl_seconds=24 * 3600))

    def _roster_count_cache_for(league_key: str) -> _TTLCache:
        return _roster_count_caches.setdefault(league_key, _TTLCache(ttl_seconds=600))

    def _load_projection_anchor(sleeper, season):
        """The preseason projection anchor for ros_value. Sleeper wipes /
        overwrites projections after kickoff (`_load_projections` raises
        ContaminatedProjectionError then), so fall back to the
        post-kickoff feed with allow_contaminated -- the pace blend in
        ros_value increasingly ignores a stale anchor anyway."""
        try:
            proj, _adp = _load_projections(sleeper, season)
            return proj
        except proj_mod.ContaminatedProjectionError:
            import logging
            logging.getLogger("ffdo.api").warning(
                "season anchor: using post-kickoff projections for %s "
                "(no preseason snapshot)", season)
            raw = sleeper.get_json(
                f"{client_mod.PROJECTIONS}/{season}"
                "?season_type=regular&position[]=QB&position[]=RB"
                "&position[]=WR&position[]=TE&position[]=DEF&position[]=K")
            proj, _adp = proj_mod.parse(raw, season, allow_contaminated=True)
            return proj
```

- [ ] **Step 4: Add the `_standings_rank` helper**

Free function near the top of `app.py` (module level, next to `_league_public_dict`):

```python
def _standings_rank(rosters) -> dict[int, int]:
    """roster_id -> standings position (1 = best), by (wins, points_for) desc
    -- the default tiebreak both Sleeper and ESPN use."""
    order = sorted(rosters, key=lambda r: (r.wins, r.points_for), reverse=True)
    return {r.roster_id: i + 1 for i, r in enumerate(order)}
```

- [ ] **Step 5: Add module imports for the new ingest/engine modules**

In `create_app()`'s import block:

```python
    from ffdo.ingest import actuals as actuals_mod
    from ffdo.ingest import nfl_state as nfl_state_mod
    from ffdo.ingest import rosters as rosters_mod
    from ffdo.ingest.sleeper import traded_picks as traded_picks_mod
    from ffdo.ingest.espn import rosters as espn_rosters_mod
    from ffdo.ingest.espn import actuals as espn_actuals_mod
    from ffdo.engine import power_ranking as power_ranking_mod
    from ffdo.engine import ros_value as ros_value_mod
    from ffdo.domain.constants import NFL_BYE_WEEKS
```

- [ ] **Step 6: Write the endpoint — Sleeper branch**

Add after `get_board` (before the static mounts):

```python
    @app.get("/api/leagues/{league_key}/season")
    def get_season(league_key: str) -> dict:
        lg = _load_league(league_key)
        fmt = lg.resolved_format

        if lg.provider == "espn":
            return _season_espn(lg)

        sleeper = client_mod.SleeperClient()
        try:
            nfl = _nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))
            profiles, _idx = players_cache.get(lambda: _load_players(sleeper))
            proj_anchor = _season_proj_anchor_for(lg.season).get(
                lambda: _load_projection_anchor(sleeper, lg.season))
            rosters = rosters_mod.fetch(sleeper, lg.provider_league_id)
            through_week = max(0, nfl.week - 1) if not nfl.complete else 18
            actuals = actuals_mod.points_so_far(sleeper, lg.provider_league_id, through_week)

            standings_rank = _standings_rank(rosters)
            names = {r.roster_id: r.team_name for r in rosters}

            capital = None
            if fmt in ("dynasty", "keeper"):
                worst_to_best = sorted(rosters, key=lambda r: (r.wins, r.points_for))
                capital = traded_picks_mod.capital(
                    sleeper, lg.provider_league_id,
                    num_teams=lg.num_teams,
                    rounds=int((lg.raw_settings or {}).get("draft_rounds") or 4),
                    standings_order=[r.roster_id for r in worst_to_best],
                    draft_years=(lg.season + 1, lg.season + 2),
                    team_names=names)
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(status_code=502,
                                detail="Couldn't reach Sleeper, try again") from exc
        finally:
            sleeper.close()

        return _assemble_season(lg, nfl, through_week, rosters, profiles, proj_anchor,
                                actuals, standings_rank, capital)
```

- [ ] **Step 7: Write `_season_espn` and `_assemble_season`**

Both as closures inside `create_app()` (they need the module aliases). `_season_espn`:

```python
    def _season_espn(lg):
        cred = _require_espn_credential("the season view")
        sleeper = client_mod.SleeperClient()
        try:
            profiles, espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            proj_anchor = _season_proj_anchor_for(lg.season).get(
                lambda: _load_projection_anchor(sleeper, lg.season))
        finally:
            sleeper.close()

        espn = espn_client_mod.EspnClient(cred.espn_s2, cred.swid)
        try:
            player_pool_raw = _espn_player_pool_cache_for(lg.season).get(
                lambda: espn.get_json(
                    f"{espn_client_mod.BASE}/seasons/{lg.season}/players?view=kona_player_info",
                    extra_headers=espn_client_mod.PLAYER_POOL_FILTER_HEADER))
            cw = _espn_crosswalk_cache_for(lg.season).get(
                lambda: espn_crosswalk_mod.build(
                    espn_id_index, profiles,
                    espn_crosswalk_mod.parse_player_pool(player_pool_raw)))
            rosters, nfl, mroster_raw = espn_rosters_mod.fetch(
                espn, lg.provider_league_id, lg.season, cw)
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(status_code=502,
                                detail="Couldn't reach ESPN, try again") from exc
        finally:
            espn.close()

        through_week = max(0, nfl.week - 1) if not nfl.complete else 18
        actuals = espn_actuals_mod.points_so_far(mroster_raw, cw, through_week)
        standings_rank = _standings_rank(rosters)
        return _assemble_season(lg, nfl, through_week, rosters, profiles, proj_anchor,
                                actuals, standings_rank, None)   # no draft capital for ESPN
```

> The `cw` object must expose whatever Task 9 settled on (`.to_sleeper` or a dict). If Task 9's report says the real `Crosswalk` lacks it, add the shim there — this task consumes it as-is.

`_assemble_season` (the shared payload builder):

```python
    def _assemble_season(lg, nfl, through_week, rosters, profiles, proj_anchor,
                         actuals, standings_rank, capital):
        all_pids = {pid for r in rosters for pid in r.player_ids}
        valued = ros_value_mod.roster_value(
            all_pids, lg, resolved_format=lg.resolved_format,
            season_proj=proj_anchor, profiles=profiles, actuals=actuals,
            weeks_played=through_week,
            season_weeks=constants_season_weeks(lg.season))

        def _rank(position, scope):
            rows = power_ranking_mod.rank(rosters, valued, lg, standings_rank,
                                          lg.roster_id, position=position, scope=scope)
            return [{
                "roster_id": row.roster_id, "team_name": row.team_name,
                "is_you": row.is_you, "value": row.value,
                "bench_value": row.bench_value, "power_rank": row.power_rank,
                "standings_rank": row.standings_rank, "delta": row.delta,
            } for row in rows]

        power_ranking_payload = {
            "overall": {"starters": _rank("OVR", "starters"), "full": _rank("OVR", "full")},
            "by_position": {
                p: {"starters": _rank(p, "starters"), "full": _rank(p, "full")}
                for p in ("QB", "RB", "WR", "TE")
            },
        }

        you = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        your_roster = _your_roster_payload(lg, nfl, you, valued, profiles,
                                           power_ranking_payload, standings_rank) if you else None

        return {
            "nfl_week": {"season": nfl.season, "week": nfl.week,
                         "season_type": nfl.season_type, "complete": nfl.complete,
                         "values_through_week": through_week},
            "your_roster": your_roster,
            "power_ranking": power_ranking_payload,
            "standings": [
                {"roster_id": r.roster_id, "team_name": r.team_name,
                 "wins": r.wins, "losses": r.losses, "ties": r.ties,
                 "points_for": round(r.points_for, 1),
                 "points_against": round(r.points_against, 1)}
                for r in sorted(rosters, key=lambda r: standings_rank[r.roster_id])
            ],
            "draft_capital": _draft_capital_payload(capital, rosters, lg.roster_id),
        }
```

Add the two remaining helpers as closures:

```python
    def constants_season_weeks(season: int) -> int:
        from ffdo.domain.constants import SEASON_LENGTH
        return SEASON_LENGTH.get(season, 18)

    def _your_roster_payload(lg, nfl, you, valued, profiles, power_payload, standings_rank):
        byes = NFL_BYE_WEEKS.get(lg.season, {})
        players = []
        # slot assignment: use the provider's starter_ids; everyone else is BN
        starters = set(you.starter_ids)
        for pid in you.player_ids:
            vp = valued.get(pid)
            prof = profiles.get(pid)
            if prof is None:
                continue
            players.append({
                "player_id": pid, "name": prof.full_name, "position": prof.position,
                "team": prof.team, "slot": prof.position if pid in starters else "BN",
                "starter": pid in starters,
                "value": round(vp.vor, 1) if vp else 0.0,
                "age": prof.age, "bye_week": byes.get(prof.team or ""),
                "injury_status": prof.injury_status,
            })
        players.sort(key=lambda p: (not p["starter"], -p["value"]))

        overall_you = next((row for row in power_payload["overall"]["starters"]
                            if row["roster_id"] == lg.roster_id), None)
        pos_rank = {}
        for p in ("QB", "RB", "WR", "TE"):
            rows = power_payload["by_position"][p]["starters"]
            hit = next((r for r in rows if r["roster_id"] == lg.roster_id), None)
            pos_rank[p] = hit["power_rank"] if hit else None

        bench_full = next((row for row in power_payload["overall"]["full"]
                           if row["roster_id"] == lg.roster_id), None)

        return {
            "roster_id": you.roster_id, "team_name": you.team_name,
            "wins": you.wins, "losses": you.losses, "ties": you.ties,
            "points_for": round(you.points_for, 1),
            "points_against": round(you.points_against, 1),
            "power_rank": overall_you["power_rank"] if overall_you else None,
            "standings_rank": standings_rank.get(you.roster_id),
            "positional_rank": pos_rank,
            "bench_value": bench_full["bench_value"] if bench_full else 0.0,
            "callout": _roster_callout(lg, you, valued, profiles),
            "players": players,
        }

    def _roster_callout(lg, you, valued, profiles):
        starting_positions = [s for s in lg.roster_positions if s != "BN"]
        # count rostered players by position
        by_pos: dict[str, int] = {}
        startable: dict[str, int] = {}
        for pid in you.player_ids:
            prof = profiles.get(pid)
            if prof is None:
                continue
            by_pos[prof.position] = by_pos.get(prof.position, 0) + 1
            vp = valued.get(pid)
            if vp and vp.vor > 0:
                startable[prof.position] = startable.get(prof.position, 0) + 1
        short = lg.roster_size - len(you.player_ids)
        if short > 0:
            return f"{short} empty roster spot{'s' if short != 1 else ''}"
        for pos in ("QB", "RB", "WR", "TE"):
            if pos in starting_positions and startable.get(pos, 0) == 1:
                return f"thin at {pos}"
        return None

    def _draft_capital_payload(capital, rosters, your_roster_id):
        if capital is None:
            return None
        names = {r.roster_id: r.team_name for r in rosters}
        by_owner: dict[int, list] = {}
        for asset in capital:
            by_owner.setdefault(asset.current_owner_roster_id, []).append(asset)
        ranked = sorted(
            by_owner.items(),
            key=lambda kv: (-len(kv[1]),
                            min((a.projected_slot or 99) for a in kv[1])))
        out = []
        for i, (owner_id, assets) in enumerate(ranked):
            picks: dict[str, list] = {}
            for a in sorted(assets, key=lambda a: (a.season, a.round, a.projected_slot or 99)):
                picks.setdefault(str(a.season), []).append({
                    "label": a.label, "round": a.round,
                    "projected_slot": a.projected_slot,
                    "via_team_name": a.via_team_name,
                })
            out.append({
                "power_rank": i + 1, "roster_id": owner_id,
                "team_name": names.get(owner_id, f"Team {owner_id}"),
                "is_you": owner_id == your_roster_id, "picks": picks,
            })
        return out
```

- [ ] **Step 8: Run the season tests**

Run: `uv run pytest tests/api/test_season_endpoint.py -q`
Expected: PASS. If `test_cross_format_guard` fails because redraft and dynasty happen to produce equal values for this fixture, adjust the fixture (e.g. make `p_rb` age 24 and `p_rb2` age 31 so the dynasty curve clearly separates them) — not the implementation.

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (additive change; no existing route touched).

- [ ] **Step 10: Commit**

```bash
git add src/ffdo/api/app.py tests/api/test_season_endpoint.py
git commit -m "feat: GET /api/leagues/{key}/season — power ranking, roster panel, standings, draft capital"
```

---

## Task 11: `needs_attention` in `GET /api/leagues`

**Files:**
- Modify: `src/ffdo/api/app.py` (`list_leagues_endpoint`, and the season endpoint warms the cache)
- Test: `tests/api/test_app.py` (add cases)

**Interfaces:**
- Consumes: `_roster_count_cache_for` from Task 10; `RosterEntry`.
- Produces: `list_leagues_endpoint` returns `needs_attention: bool` — `true` when a warmed roster count is below `roster_size` OR a starting slot is unfilled; `false` when cold or fine.

- [ ] **Step 1: Write the failing test**

Add to `tests/api/test_app.py`:

```python
def test_needs_attention_true_for_a_short_roster_once_warmed(monkeypatch, tmp_path):
    from tests.api.test_season_endpoint import _tracked, _recording_client, _ROSTERS
    store = LeagueStore(tmp_path / "ffdo.db")
    # roster 1 has 3 players but roster_size is 6 (QB,RB,WR,FLEX,BN,BN)
    store.upsert(_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _recording_client())

    client = TestClient(create_app())
    # cold: needs_attention defaults false
    assert client.get("/api/leagues").json()[0]["needs_attention"] is False
    # warm it via the season endpoint
    client.get("/api/leagues/sleeper:L1:2026/season")
    assert client.get("/api/leagues").json()[0]["needs_attention"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_app.py -q -k needs_attention`
Expected: FAIL — second assertion is `False` (nothing warms the cache / endpoint doesn't read it).

- [ ] **Step 3: Warm the cache in the season endpoint**

In `_assemble_season` (Task 10), after `valued` is computed, add:

```python
        you_entry = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        if you_entry is not None:
            unfilled = sum(1 for s in lg.roster_positions if s != "BN") > len(you_entry.starter_ids)
            short = len(you_entry.player_ids) < lg.roster_size
            _roster_count_cache_for(lg.league_key).get(lambda: {"attn": bool(unfilled or short)})
```

> `_TTLCache.get(loader)` caches the loader's return; storing a dict lets `has_value()` + a follow-up read work. If `_TTLCache` has no "peek the value" accessor beyond `has_value()`, add a `value` property to `_TTLCache` returning `self._value` (tiny, in scope) — or store the cache as a plain `dict[str, bool]` with a timestamp instead. Pick the smaller change and note it.

- [ ] **Step 4: Read it in `list_leagues_endpoint`**

```python
    @app.get("/api/leagues")
    def list_leagues_endpoint() -> list[dict]:
        rows = []
        for lg in _STORE.list():
            cache = _roster_count_caches.get(lg.league_key)
            attn = bool(cache.value["attn"]) if (cache and cache.has_value()) else False
            rows.append({
                "league_key": lg.league_key, "name": lg.name,
                "provider": lg.provider, "season": lg.season,
                "format": lg.fmt, "resolved_format": lg.resolved_format,
                "draft_status": lg.draft_status, "is_mock": lg.is_mock,
                "needs_attention": attn,
            })
        return rows
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/api/test_app.py tests/api/test_season_endpoint.py -q` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/api/app.py tests/api/test_app.py
git commit -m "feat: compute needs_attention (short roster / unfilled slot) for the switcher"
```

---

## Task 12: Frontend — the season screen

**Files:**
- Create: `src/ffdo/web/season/season.js`, `src/ffdo/web/season/season.css`
- Modify: `src/ffdo/web/board/board.js`
- Modify: `src/ffdo/web/app.css`

> No automated frontend tests (repo convention). Manual verification via `run` + a controller browser smoke.

- [ ] **Step 1: Write `season.js`**

Create `src/ffdo/web/season/season.js`. Structure (full file — no framework, matches `board.js` house style):

- `escapeHtml(s)` — local copy from `board.js:515-519`.
- Module state: `let _c, _key, _meta, _data; let _panel = "power", _pos = "OVR", _scope = "starters";`
- `export async function mountSeason(container, leagueKey, meta)`:
  - `_c = container; _key = leagueKey; _meta = meta;`
  - `container.innerHTML = SHELL;` (sub-strip + two-panel skeleton + refresh button).
  - `await load();`
  - wire the refresh button → `load()`.
- `async function load()`:
  - `const res = await fetch(\`/api/leagues/${encodeURIComponent(_key)}/season\`);`
  - on `!res.ok` → `_c.querySelector("#season-body").textContent = (await res.json().catch(()=>({}))).detail || "Couldn't load the season view";` return.
  - `_data = await res.json(); render();`
- `render()`:
  - left panel: `renderYourTeam(_data.your_roster, _data.nfl_week)`.
  - right panel: if `_meta.resolved_format === "dynasty"` render the `[Power ranking | Draft capital]` tab bar; else no tab bar. Then `_panel === "capital" ? renderCapital() : renderPower()`.
  - all provider strings through `escapeHtml`.
- `renderPower()`:
  - position sub-tabs (`Overall/QB/RB/WR/TE`) + scope toggle (`Starters only / Full roster`), each button `onclick` sets `_pos`/`_scope` and calls `render()` (no refetch).
  - table from `_pos === "OVR" ? _data.power_ranking.overall[_scope] : _data.power_ranking.by_position[_pos][_scope]`.
  - columns `# · Team · {value} · Rec · PF · Δ`; Δ shown only when `_pos === "OVR"` (else `·`); your row tinted (`row.is_you`), `YOU` badge.
  - `Rec`/`PF` come from the matching `_data.standings` row (join on `roster_id`).
- `renderCapital()`:
  - `_data.draft_capital` — table `# · Team · {year1} picks · {year2} picks` where the years are `Object.keys(row.picks).sort()`.
  - pick chips: `via_team_name` → amber + `label + " via " + via`, else accent-tint + `label`.
  - a `blended power + capital score — coming` ghost chip; a legend line.
  - empty `_data.draft_capital` → "No tradeable picks in this league".
- `renderYourTeam(you, week)`:
  - if `you == null` → "Roster not available".
  - header: team name, `record · Nth of {n} · PF`, `power rank {n} / {delta:+} vs standings` (delta = `standings_rank - power_rank`).
  - four positional-strength bars from `you.positional_rank` (`{pos}` · bar width `(num_teams - rank + 1) / num_teams * 100`% · `{rank}th /{n}`; colors: rank ≤ 3 green, rank ≥ n-2 red).
  - roster: `you.players` grouped starters-then-bench (already sorted by the endpoint); each row `slot chip · name · team · bye · value`, bench muted, negative value red.
  - `bench value {you.bench_value}`; `callout` if present.
- `SHELL` / small helpers as needed.

Match `season.css` selectors (Step 2). Every `${x}` that is a provider string → `${escapeHtml(x)}`.

- [ ] **Step 2: Write `season.css`**

Create `src/ffdo/web/season/season.css` — port the palette tokens from `app.css`, add: `.season-strip`, `.season-two-panel`, `.season-left`, `.season-right`, `.pos-tabs`, `.scope-toggle`, `.panel-tabs`, `.pr-table` + `.pr-row` (grid), `.pr-row.you`, `.you-badge`, `.pos-bar` + `.pos-bar-fill`, `.roster-row`, `.slot-chip`, `.slot-chip.bn`, `.pick-chip`, `.pick-chip.traded`, `.ghost-chip`, `.callout`. Keep it minimal and legible; the design canvas (`design/roster-standings/`) is the visual reference, not a pixel target.

- [ ] **Step 3: Swap `renderSeasonMode` for `mountSeason` in `board.js`**

In `src/ffdo/web/board/board.js`, in `refresh()` where it currently does:

```javascript
if (state.data.draft_status === "complete") {
  clearInterval(state.pollId); clearInterval(state.livePollId);
  renderSeasonMode(_container, _meta);
  return;
}
```

replace with:

```javascript
if (state.data.draft_status === "complete") {
  clearInterval(state.pollId); clearInterval(state.livePollId);
  try {
    const m = await import("../season/season.js");
    await m.mountSeason(_container, _leagueKey, _meta);
  } catch (e) {
    _container.textContent = "Couldn't load the season view — check the console.";
    console.error("season module failed to load", e);
  }
  return;
}
```

Delete the `renderSeasonMode` function from `board.js` entirely.

- [ ] **Step 4: Remove the superseded placeholder CSS from `app.css`**

In `src/ffdo/web/app.css`, delete the `.card.season-mode`, `.season-mode .badge`, `.format-override`, `.stat-grid`, `.stat`, `.chip-row`, `.chip`, `.chip.bn` rules that Task 13 of the foundation added for the placeholder. (They are replaced by `season.css`.)

- [ ] **Step 5: Verify the suite + parse checks**

Run: `uv run pytest -q` (expect 449 + Task 1–11's new tests, still green — no Python touched here).
Run: `node --input-type=module --check < src/ffdo/web/season/season.js` (or copy to a temp `.mjs` and `node --check`).
Start `uv run uvicorn ffdo.api.app:app --port 8150`, then:
- `curl -s http://localhost:8150/season/season.js | grep -c "export async function mountSeason"` → 1
- `curl -s http://localhost:8150/season/season.css | head -1`
Stop the server.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/web/season/ src/ffdo/web/board/board.js src/ffdo/web/app.css
git commit -m "feat: season screen — two-panel power ranking + roster + standings + draft capital"
```

---

## Task 13: README + a real-league browser smoke

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update `README.md`**

Under the app description / "Running it", add one line:

```markdown
Once a league's draft is complete, its page becomes the **season view** —
a luck-adjusted power ranking (overall and by position, starters or full
roster), your roster with rest-of-season / dynasty values, the league
standings, and — for Sleeper dynasty/keeper leagues — a draft-capital
ranking.
```

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 3: Browser smoke (manual — the executor or controller)**

Seed the pinned dev league (`uv run python scripts/seed_dev_league.py Schroedes`), start the server, and in a browser:
- Navigate to a completed-draft league → the season screen renders (not the old placeholder).
- Redraft league → right panel has no top tab bar; position sub-tabs + starters/full toggle re-rank the table with no network call (check devtools).
- If a dynasty Sleeper league is tracked → `[Power ranking | Draft capital]` tabs; the capital tab shows pick chips.
- Refresh button re-hits `/season` (one request in devtools).
- No console errors.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "chore: document the season view in the README"
```

---

## Self-Review

**1. Spec coverage:**

| Spec section | Task(s) |
|---|---|
| §2.1 domain types (`NflWeek`, `RosterEntry`, `PowerRow`+delta, `DraftPickAsset`+label) | 1 |
| §3.1 `nfl_state.current_week` | 2 |
| §3.2 Sleeper `rosters.fetch` + `teams._display_names` refactor | 3 |
| §3.4 Sleeper `actuals.points_so_far` | 4 |
| §3.6 Sleeper `traded_picks.capital` (implicit ownership, chain trades, projected slot, 404→implicit) | 5 |
| §4.2 `dynasty_curve.multiplier` | 6 |
| §4.1 `ros_value.roster_value` — blend, format branch, injury zero, frozen seam | 7 |
| §4.3 projection anchor (`_load_projection_anchor`, `allow_contaminated` fallback) | 10 (Step 3) |
| §4.4 `power_ranking.rank` — OVR + position, both scopes, delta | 8 |
| §4.5 `_standings_rank` (wins, points_for) | 10 (Step 4) |
| §3.3 / §3.5 ESPN `rosters` + `actuals` | 9 |
| §5.1 `/season` payload (all 10 arrays, your_roster, standings, draft_capital shape) | 10 |
| §5.2 caching (`_nfl_state_cache`, `_season_proj_anchor_for`, no cache for rosters/actuals) | 10 |
| §5.3 errors (404, 400 ESPN, 502, draft_status≠complete still 200, absent player omitted) | 10 |
| §5.4 `needs_attention` | 11 |
| §6 frontend (`season.js`/`season.css`, `board.js` swap, placeholder removal) | 12 |
| §8 `NFL_BYE_WEEKS`, `ingest/sleeper/` subpackage, README | 1, 5, 13 |
| §7 tests (nfl_state, rosters, actuals, traded_picks, dynasty_curve, ros_value, power_ranking, ESPN, season endpoint, cross-format guard) | 2–11 |

> Gap accepted: the spec's "`positional_rank` uses the starters scope" and the "callout" logic are implemented in Task 10's `_your_roster_payload` / `_roster_callout` closures but have no dedicated unit test — they are exercised by `test_season_payload_is_well_formed`'s structural assertions. If a reviewer wants them pinned, add a focused assertion to that test (roster with a 1-deep TE → `callout == "thin at TE"`).

**2. Placeholder scan:** No "TBD"/"handle edge cases". Task 1's bye-week values are flagged as needing a real-schedule pass (structural test only — deliberate, not a placeholder in the plan sense). Task 9's crosswalk-interface uncertainty is called out explicitly with a "record the decision in the report" instruction and a fallback (`.to_sleeper` shim). Task 11's `_TTLCache.value` accessor is flagged as "add if absent, note which".

**3. Type consistency:**
- `roster_value(player_ids, league, *, resolved_format, season_proj, profiles, actuals, weeks_played, season_weeks=18)` — defined Task 7, called Task 10 `_assemble_season` with exactly those kwargs. `K` is a module constant, not a param (spec §4.1). Consistent.
- `power_ranking.rank(rosters, valued, league, standings_rank, your_roster_id, *, position, scope)` — defined Task 8, called Task 10 `_rank` closure with those args. Consistent. (Task 8's interface block lists `your_roster_id` after `standings_rank` — matches the impl.)
- `traded_picks.capital(sleeper, league_id, *, num_teams, rounds, standings_order, draft_years, team_names)` — defined Task 5, called Task 10 with those kwargs. Consistent.
- `RosterEntry` fields — `player_ids`/`starter_ids` are `tuple[str,...]` (Task 1), produced as tuples by Tasks 3 & 9, consumed as iterables by Tasks 8 & 10.
- `NflWeek.complete` drives `through_week` in Task 10 (`nfl.week - 1` normally, `18` when complete) and is passed as `weeks_played` to `roster_value` and `through_week` to `actuals`. One value, three call sites, consistent.
- `PowerRow.delta` (property, Task 1) surfaced as `"delta"` in the endpoint JSON (Task 10) and consumed by `season.js` (Task 12) — but `season.js` also recomputes `standings_rank - power_rank` for the your-team header; both are the same number. Note for the implementer: prefer the JSON `delta` field.
- `DraftPickAsset.label` (property, Task 1) used in Task 10's `_draft_capital_payload` and rendered by Task 12. Consistent.
- `ros_value` reads `vp.projected_points` and `vp.vor`; Task 7's note pins that `vor.compute` sets `projected_points` to the pre-VOR points — the Task 7 tests rely on this, and `engine/vor.py:40` confirms it.

**4. Additional spec requirement with no task:** none found. The "blended power + capital score" and the "preseason projection snapshot" are explicit §1.2 / §9 non-goals. `needs_attention`'s cold-cache-is-false behavior is in Task 11 Step 4.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-03-roster-standings-view.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — tasks in this session with checkpoints.

Which approach?
