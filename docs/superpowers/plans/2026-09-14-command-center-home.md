# Command-Center Home Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `#/`'s silent auto-redirect-into-last-league with a real
landing page: a grid of cards, one per tracked league, each showing your
starting roster, power rank, this week's matchup (Sleeper leagues), and
flags for a suggested lineup swap / waiver add / trade target — so you
can tell which league needs you right now without opening each one.

**Architecture:** One new engine function (a team's live-updating
projected score), one new ingest function (this week's matchup pairing +
live per-player points), one new endpoint composing both plus every
existing per-league computation this initiative already built (power
rank, lineup diff, waiver recommendations, trade targets), and a new
frontend module that fires one request per tracked league in parallel so
cards render independently as their data arrives.

**Tech Stack:** Python (FastAPI backend, pytest), vanilla JS/CSS frontend
(no framework, matches this codebase's existing `board.js`/`season.js`
house style).

**Spec:** [docs/superpowers/specs/2026-09-14-command-center-home-design.md](../specs/2026-09-14-command-center-home-design.md)

## Global Constraints

- The command-center endpoint is Sleeper-only for the matchup/projected-score
  piece (ESPN's matchup pairing isn't ingested anywhere in this codebase —
  confirmed during brainstorming); ESPN leagues still get roster/power-rank/
  flags, just `matchup: null`.
- One endpoint per league (`GET /api/leagues/{league_key}/home-summary`),
  never one combined response for the whole grid — the frontend fires every
  tracked league's request in parallel so a slow or failing league never
  blocks the rest of the grid.
- No win probability — explicitly out of scope for this pass (see spec's
  Deferred section).
- A starter row's per-player `value` is VOR (`ValuedPlayer.vor`), matching
  the existing Lineup tab's own convention exactly — **not** the same
  number as `matchup.your_projected`/`opponent_projected`, which are raw
  fantasy points (see Task 2's docstring for why these are deliberately
  different scales, not an inconsistency).
- Live updates: the frontend re-polls each season-mode card's
  `home-summary` on a 60s interval while the home screen is mounted,
  clearing on unmount.
- Card ordering: alphabetical by league name, stable across polls.
- No JS test framework exists in this repo (confirmed: no `package.json`).
  Frontend verification is live browser testing against the real dev
  server and a real tracked league, per this initiative's established
  practice.

## Interfaces this plan corrects from the spec

Two things the spec described in a way that doesn't match the real code,
found while reading the actual implementations before writing this plan
(not defects in the spec's *decisions* — the direction is right, the
exact mechanism named was wrong):

1. The spec said the matchup/projected-score row would "reuse the exact
   same per-player weekly-value function the Lineup tab already uses."
   That function, `weekly_lineup.weekly_value`, returns **VOR**
   (value-over-replacement), not raw fantasy points — confirmed by
   reading `src/ffdo/api/app.py`'s existing `/lineup` endpoint, which
   sets `"value": round(vp.vor, 1)`. VOR is not a number comparable to
   what a "projected score" should show (a real-feeling point total like
   "118.4"). Task 2 below is a new, small, genuinely different function
   operating on raw points instead.
2. The spec's Card content section implied the season screen's top-line
   "POWER RANK" tile uses `scope="full"` (starting + bench). Reading
   `_your_roster_payload` in `app.py` shows it actually reads
   `power_payload["overall"]["starters"]` — `scope="starters"`. Task 3
   below uses `scope="starters"` to match that tile exactly, not `"full"`.

## Task 1: Current-week matchup pairing + live points

**Files:**
- Create: `src/ffdo/ingest/sleeper/matchups.py`
- Test: `tests/ingest/test_sleeper_matchups.py`

**Interfaces:**
- Consumes: `ffdo.ingest.client.{V1, SleeperClient}` (`SleeperClient.get_json(url) -> Any`, raises `httpx.HTTPStatusError` on a non-2xx response, same as every other ingest module in this codebase).
- Produces: `CurrentWeekMatchups` dataclass (`pairing: dict[int, int]`, `live_points: dict[str, float]`) and `fetch(sleeper, league_id, week) -> CurrentWeekMatchups`, consumed directly by Task 3.

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/test_sleeper_matchups.py`:

```python
from unittest.mock import MagicMock

from ffdo.ingest.sleeper import matchups


def _client(rows):
    client = MagicMock()
    client.get_json.return_value = rows
    return client


def test_fetch_pairs_rosters_sharing_a_matchup_id():
    rows = [
        {"roster_id": 1, "matchup_id": 10, "players_points": {"p1": 12.5, "p2": 8.0}},
        {"roster_id": 2, "matchup_id": 10, "players_points": {"p3": 9.5}},
        {"roster_id": 3, "matchup_id": 11, "players_points": {"p4": 3.0}},
        {"roster_id": 4, "matchup_id": 11, "players_points": {}},
    ]
    result = matchups.fetch(_client(rows), "L1", 3)
    assert result.pairing == {1: 2, 2: 1, 3: 4, 4: 3}
    assert result.live_points == {"p1": 12.5, "p2": 8.0, "p3": 9.5, "p4": 3.0}


def test_fetch_leaves_an_unpaired_roster_out_of_pairing():
    # Odd team count: matchup_id 12 has only one roster in it (a bye).
    rows = [
        {"roster_id": 1, "matchup_id": 10, "players_points": {"p1": 5.0}},
        {"roster_id": 2, "matchup_id": 10, "players_points": {"p2": 6.0}},
        {"roster_id": 5, "matchup_id": 12, "players_points": {"p5": 1.0}},
    ]
    result = matchups.fetch(_client(rows), "L1", 3)
    assert result.pairing == {1: 2, 2: 1}
    assert 5 not in result.pairing
    assert result.live_points == {"p1": 5.0, "p2": 6.0, "p5": 1.0}


def test_fetch_returns_empty_on_a_404_not_yet_generated_week():
    import httpx
    client = MagicMock()
    resp = httpx.Response(404, request=httpx.Request("GET", "http://x"))
    client.get_json.side_effect = httpx.HTTPStatusError("404", request=resp.request, response=resp)
    result = matchups.fetch(client, "L1", 1)
    assert result.pairing == {}
    assert result.live_points == {}


def test_fetch_handles_a_missing_or_empty_rows_list():
    result = matchups.fetch(_client(None), "L1", 1)
    assert result.pairing == {}
    assert result.live_points == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/test_sleeper_matchups.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.ingest.sleeper.matchups'`.

- [ ] **Step 3: Write the implementation**

Create `src/ffdo/ingest/sleeper/matchups.py`:

```python
"""This week's matchup pairing (who's playing whom) and each player's
LIVE, currently-accrued fantasy points for the CURRENT week only --
distinct from `ffdo.ingest.actuals.points_so_far`, which banks only
FINAL weeks (`nfl.week - 1` and earlier) for season-to-date totals and
discards everything except `players_points`. Sleeper's own
`matchups/{week}` row already carries both `matchup_id` (pairing) and
`players_points` (live, updating through game day) -- one HTTP call
covers both, so `current_matchup` and `live_points` are not two separate
fetches."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from ffdo.ingest.client import V1, SleeperClient


@dataclass(frozen=True, slots=True)
class CurrentWeekMatchups:
    pairing: dict[int, int] = field(default_factory=dict)
    live_points: dict[str, float] = field(default_factory=dict)


def fetch(sleeper: SleeperClient, league_id: str, week: int) -> CurrentWeekMatchups:
    try:
        rows = sleeper.get_json(f"{V1}/league/{league_id}/matchups/{week}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return CurrentWeekMatchups()
        raise

    by_matchup: dict[int, list[int]] = {}
    live_points: dict[str, float] = {}
    for row in rows or []:
        roster_id = row.get("roster_id")
        matchup_id = row.get("matchup_id")
        if roster_id is not None and matchup_id is not None:
            by_matchup.setdefault(matchup_id, []).append(roster_id)
        for pid, pts in (row.get("players_points") or {}).items():
            live_points[str(pid)] = float(pts)

    pairing: dict[int, int] = {}
    for roster_ids in by_matchup.values():
        if len(roster_ids) == 2:
            a, b = roster_ids
            pairing[a] = b
            pairing[b] = a
        # len == 1 is a bye (odd team count); anything else is malformed
        # data -- either way, no pairing entry, which the caller reads as
        # "no matchup this week" rather than an error.

    return CurrentWeekMatchups(pairing=pairing, live_points=live_points)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/test_sleeper_matchups.py -v`
Expected: PASS (all 4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/sleeper/matchups.py tests/ingest/test_sleeper_matchups.py
git commit -m "feat: ingest current-week matchup pairing and live per-player points

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

## Task 2: Team projected score

**Files:**
- Create: `src/ffdo/engine/matchup_score.py`
- Test: `tests/engine/test_matchup_score.py`

**Interfaces:**
- Consumes: `ffdo.domain.models.{PlayerProfile, WeeklyProjection}`; `ffdo.engine.scoring.score_stats(stats, weights) -> float` (already used by `weekly_lineup.py`, same import).
- Produces: `team_projected_score(starter_ids, *, weekly_points, live_points, profiles, locked_teams, scoring_settings) -> float`, consumed by Task 3.

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/test_matchup_score.py`:

```python
import pytest

from ffdo.domain.models import PlayerProfile, WeeklyProjection
from ffdo.engine import matchup_score


def _profile(pid, team):
    return PlayerProfile(player_id=pid, first_name="F", last_name=pid, position="RB",
                         team=team, age=25, years_exp=3, injury_status=None, active=True)


def _proj(pid, stats):
    return WeeklyProjection(player_id=pid, season=2026, week=1, stats=stats)


_SCORING = {"rush_yd": 0.1, "rush_td": 6.0, "rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0}


def test_uses_live_points_for_a_starter_whose_game_has_locked():
    profiles = {"p1": _profile("p1", "AAA")}
    weekly_points = {"p1": _proj("p1", {"rush_yd": 999.0})}  # would score huge if used -- must be ignored
    live_points = {"p1": 14.3}
    total = matchup_score.team_projected_score(
        ["p1"], weekly_points=weekly_points, live_points=live_points,
        profiles=profiles, locked_teams=frozenset({"AAA"}), scoring_settings=_SCORING)
    assert total == pytest.approx(14.3)


def test_uses_pregame_projection_for_a_starter_whose_game_hasnt_locked():
    profiles = {"p1": _profile("p1", "AAA")}
    weekly_points = {"p1": _proj("p1", {"rush_yd": 100.0, "rush_td": 1.0})}  # 10.0 + 6.0 = 16.0
    live_points = {"p1": 999.0}  # would score huge if used -- must be ignored
    total = matchup_score.team_projected_score(
        ["p1"], weekly_points=weekly_points, live_points=live_points,
        profiles=profiles, locked_teams=frozenset(), scoring_settings=_SCORING)
    assert total == pytest.approx(16.0)


def test_sums_multiple_starters_mixing_locked_and_not_locked():
    profiles = {"p1": _profile("p1", "AAA"), "p2": _profile("p2", "BBB")}
    weekly_points = {"p2": _proj("p2", {"rec": 5.0, "rec_yd": 40.0})}  # 5.0 + 4.0 = 9.0
    live_points = {"p1": 11.0}
    total = matchup_score.team_projected_score(
        ["p1", "p2"], weekly_points=weekly_points, live_points=live_points,
        profiles=profiles, locked_teams=frozenset({"AAA"}), scoring_settings=_SCORING)
    assert total == pytest.approx(20.0)


def test_missing_projection_and_missing_live_points_contribute_zero():
    profiles = {"p1": _profile("p1", "AAA")}
    total = matchup_score.team_projected_score(
        ["p1"], weekly_points={}, live_points={},
        profiles=profiles, locked_teams=frozenset({"AAA"}), scoring_settings=_SCORING)
    assert total == pytest.approx(0.0)


def test_unknown_player_id_contributes_zero_without_erroring():
    total = matchup_score.team_projected_score(
        ["ghost"], weekly_points={}, live_points={},
        profiles={}, locked_teams=frozenset(), scoring_settings=_SCORING)
    assert total == pytest.approx(0.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_matchup_score.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

Create `src/ffdo/engine/matchup_score.py`:

```python
"""A team's best-current-estimate fantasy score for the live/current
week: a starter's LIVE points once their team's game has locked (already
accruing as the game plays, climbing to the final total once it ends),
else their pre-game weekly projection.

Deliberately NOT `engine.weekly_lineup`'s VOR-scaled per-player values --
those compare players against each other for lineup-optimization
purposes and are not a number a user would recognize as "my team's
score" (a real Sleeper-sized total like 118.4). This sums RAW fantasy
points instead, via the same `score_stats` weekly_lineup.py itself
already uses for the projection half.

Powers the command-center home's per-league matchup row -- see
docs/superpowers/specs/2026-09-14-command-center-home-design.md."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ffdo.domain.models import PlayerProfile, WeeklyProjection
from ffdo.engine.scoring import score_stats


def team_projected_score(
    starter_ids: Iterable[str],
    *,
    weekly_points: Mapping[str, WeeklyProjection],
    live_points: Mapping[str, float],
    profiles: Mapping[str, PlayerProfile],
    locked_teams: frozenset[str],
    scoring_settings: Mapping[str, float],
) -> float:
    total = 0.0
    for pid in starter_ids:
        if pid is None:
            continue
        profile = profiles.get(pid)
        if profile is not None and profile.team in locked_teams:
            total += live_points.get(pid, 0.0)
            continue
        proj = weekly_points.get(pid)
        if proj is not None:
            total += score_stats(proj.stats, scoring_settings)
    return total
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_matchup_score.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/matchup_score.py tests/engine/test_matchup_score.py
git commit -m "feat: add engine.matchup_score -- a team's live-updating raw-points projected score

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

## Task 3: `GET /api/leagues/{league_key}/home-summary`

**Files:**
- Modify: `src/ffdo/api/app.py` (module imports near line 211; new endpoint inserted immediately after `_load_trade_targets_context`/`_suggestion_json` and before `@app.post("/api/leagues/{league_key}/trade/suggestions")` at line 1791 -- i.e. between `_suggestion_json`'s closing `}` return (ends around line 1780) and the `trade/suggestions` route decorator)
- Test: `tests/api/test_home_summary_endpoint.py`

**Interfaces:**
- Consumes: Task 1's `matchups_mod.fetch(sleeper, league_id, week) -> CurrentWeekMatchups`; Task 2's `matchup_score_mod.team_projected_score(...)`; the already-merged `_load_trade_targets_context(lg) -> tuple[rosters, valued, profiles, free_agent_ids, your_picks] | None` (`src/ffdo/api/app.py:1703-1748`); `_standings_rank(rosters) -> dict[int, int]` (`src/ffdo/api/app.py:91`, module-level); `weekly_lineup_mod.{weekly_value, optimal_slots, diff}` (same signatures the existing `/lineup` endpoint at `app.py:1417-1520` already calls); `power_ranking_mod.rank(rosters, valued, league, standings_rank, your_roster_id, *, position, scope) -> list[PowerRow]` (`PowerRow` has `.roster_id`, `.power_rank`); `waiver_value_mod.recommend_adds(free_agent_ids, your_roster_ids, valued, profiles, league, faab_curve, remaining_budget, *, top_n=10, min_vor_gain=5.0) -> list[WaiverRecommendation]`; `waivers_mod.{fetch_waivers(sleeper, league_id, *, season, through_week), remaining_budget(claims, *, waiver_budget)}`; `trade_targets_mod.suggest_for_team(your_roster, their_roster, all_rosters, valued, league, free_agent_ids, your_picks, *, pick_curve, current_season, round_size) -> list[TradeSuggestion]`; `FAAB_BID_CURVE`/`PICK_VALUE_CURVE` (already imported constants); `client_mod.V1`.
- Produces: `GET /api/leagues/{league_key}/home-summary` returning the JSON shape below, consumed by Task 4's frontend.

- [ ] **Step 1: Add the `matchups` and `matchup_score` imports**

In `src/ffdo/api/app.py`, immediately after the existing line
`from ffdo.engine import trade_targets as trade_targets_mod` (added by
the prior Trade Targets sub-project, near line 211), add:

```python
    from ffdo.engine import matchup_score as matchup_score_mod
    from ffdo.ingest.sleeper import matchups as matchups_mod
```

- [ ] **Step 2: Write the failing tests**

Create `tests/api/test_home_summary_endpoint.py`:

```python
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ffdo.api import app as app_mod
from ffdo.api.app import create_app
from ffdo.api.store import LeagueStore
from ffdo.ingest.client import V1

from tests.api.test_season_endpoint import (
    _MATCHUPS, _PLAYERS, _PROJ, _ROSTERS, _STATE, _USERS, _tracked)


def _base_resp(rosters=None, matchups_by_week=None):
    resp = {
        f"{V1}/state/nfl": _STATE,
        f"{V1}/league/L1/rosters": rosters if rosters is not None else _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
    }
    for week, rows in (matchups_by_week or {}).items():
        resp[f"{V1}/league/L1/matchups/{week}"] = rows
    return resp


class _FakeClient:
    def __init__(self, resp, *a, **k):
        self._resp = resp
    def get_json(self, url, *a, **k):
        for key, val in self._resp.items():
            if key in url:
                return val
        if "/transactions/" in url:
            return []
        return [] if "/matchups/" in url or "/projections/" in url else {}
    def close(self): pass


def test_home_summary_is_sleeper_only():
    app_mod._STORE.upsert(_tracked(
        league_key="espn:E1:2026", provider="espn", provider_league_id="E1"))
    res = TestClient(create_app()).get("/api/leagues/espn:E1:2026/home-summary")
    assert res.status_code == 400


def test_home_summary_shape_and_empty_flags_with_minimal_shared_fixture(monkeypatch, tmp_path):
    # The shared _ROSTERS fixture rosters exactly 1 player per position per
    # team (see test_season_endpoint.py) -- no surplus anywhere, so waiver
    # and trade-target flags are deterministically absent, and there's no
    # week-10 matchups row in the fixture, so `matchup` is None. This
    # confirms the response shape end-to-end without needing to hand-derive
    # any real pipeline numbers.
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)
    resp = _base_resp()
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", lambda *a, **k: _FakeClient(resp))

    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/home-summary")
    assert res.status_code == 200
    data = res.json()
    assert data["league_key"] == "sleeper:L1:2026"
    assert data["provider"] == "sleeper"
    assert data["record"] == {"wins": 6, "losses": 3, "ties": 0}
    assert data["power_rank"]["value"] in (1, 2)
    assert data["power_rank"]["of"] == 2
    assert data["matchup"] is None
    assert isinstance(data["starters"], list)
    assert data["flags"] == {}


def test_home_summary_returns_degraded_response_when_you_have_no_roster(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft", roster_id=None))
    monkeypatch.setattr(app_mod, "_STORE", store)
    resp = _base_resp()
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", lambda *a, **k: _FakeClient(resp))

    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/home-summary")
    assert res.status_code == 200
    data = res.json()
    assert data["record"] is None
    assert data["power_rank"] is None
    assert data["matchup"] is None
    assert data["starters"] == []
    assert data["flags"] == {}


def test_home_summary_includes_a_real_matchup_with_opponent_and_scores(monkeypatch, tmp_path):
    # A local, hand-built fixture (following this initiative's established
    # pattern for a genuine, non-vacuous integration case) with a real
    # week-10 matchups row pairing roster 1 and roster 2 by matchup_id.
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)
    week10_matchups = [
        {"roster_id": 1, "matchup_id": 1, "players_points": {}},
        {"roster_id": 2, "matchup_id": 1, "players_points": {}},
    ]
    resp = _base_resp(matchups_by_week={10: week10_matchups})
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", lambda *a, **k: _FakeClient(resp))

    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/home-summary")
    assert res.status_code == 200
    data = res.json()
    assert data["matchup"] is not None
    assert data["matchup"]["opponent_name"] == "Them"
    assert isinstance(data["matchup"]["your_projected"], float)
    assert isinstance(data["matchup"]["opponent_projected"], float)


def test_home_summary_matchup_is_none_on_a_bye_week(monkeypatch, tmp_path):
    # Odd team count: a 3rd roster with its own unpaired matchup_id.
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)
    week10_matchups = [
        {"roster_id": 1, "matchup_id": 5, "players_points": {}},
    ]
    resp = _base_resp(matchups_by_week={10: week10_matchups})
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", lambda *a, **k: _FakeClient(resp))

    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/home-summary")
    assert res.status_code == 200
    assert res.json()["matchup"] is None
```

Note: `_STATE`'s `week` is `10` (from `test_season_endpoint.py`'s shared
fixture) -- that's why the matchup test fixtures above key their rows to
week 10.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_home_summary_endpoint.py -v`
Expected: FAIL (404s -- the endpoint doesn't exist yet).

- [ ] **Step 4: Write the implementation**

In `src/ffdo/api/app.py`, insert the new endpoint immediately after
`_suggestion_json`'s definition (the helper function ending with its
`return {...}` for the suggestion JSON dict, right before
`@app.post("/api/leagues/{league_key}/trade/suggestions")`):

```python
    @app.get("/api/leagues/{league_key}/home-summary")
    def get_home_summary(league_key: str) -> dict:
        """One league's command-center card: power rank, starting-lineup
        diff, this week's matchup (Sleeper only), and attention-flag
        counts (lineup swap / waiver add / trade target). Composes every
        per-league computation this initiative already built -- no new
        scoring logic beyond Task 2's team_projected_score, which exists
        specifically because weekly_lineup's own per-player values are
        VOR, not the raw points a "projected score" needs to show."""
        lg = _load_league(league_key)
        if lg.provider != "sleeper":
            raise HTTPException(status_code=400, detail="Home summary is Sleeper-only for now")

        ctx = _load_trade_targets_context(lg)
        if ctx is None:
            raise HTTPException(status_code=502, detail="Couldn't reach Sleeper, try again")
        rosters, valued, profiles, free_agent_ids, your_picks = ctx

        you = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        if you is None:
            # Observer-only tracking, same degrade /lineup and /waivers
            # already use -- nothing personal to show, not an error.
            return {
                "league_key": lg.league_key, "name": lg.name, "provider": lg.provider,
                "resolved_format": lg.resolved_format, "record": None,
                "power_rank": None, "matchup": None, "starters": [], "flags": {},
            }

        sleeper = client_mod.SleeperClient()
        try:
            nfl = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))
            current_starters = rosters_mod.raw_starters(
                sleeper, lg.provider_league_id, lg.roster_id)
            weekly_proj = _weekly_proj_cache_for(nfl.season, nfl.week).get(
                lambda: weekly_projections_mod.fetch(sleeper, nfl.season, nfl.week))
            try:
                games = _schedule_cache_for(nfl.season, nfl.week).get(
                    lambda: schedule_mod.week_games(sleeper, nfl.season, nfl.week))
            except (httpx.HTTPError, RuntimeError) as exc:
                logging.getLogger("ffdo.api").warning(
                    "home-summary: schedule fetch failed for %s week %s (%s) -- "
                    "treating nothing as locked", lg.league_key, nfl.week, exc)
                games = []
            current_week = matchups_mod.fetch(sleeper, lg.provider_league_id, nfl.week)

            waiver_recs_count = 0
            # `raw_settings` (captured at track/refresh time, already used
            # this same way for draft_rounds elsewhere in this file)
            # avoids a live settings re-fetch just to decide whether this
            # league is FAAB -- acceptable staleness for a flag COUNT,
            # unlike /waivers' own live re-fetch which needs an exact
            # current budget.
            if (lg.raw_settings or {}).get("waiver_type") == 2:
                try:
                    league_raw = sleeper.get_json(f"{client_mod.V1}/league/{lg.provider_league_id}")
                    waiver_budget = float((league_raw.get("settings") or {}).get("waiver_budget") or 0)
                    claims = waivers_mod.fetch_waivers(
                        sleeper, lg.provider_league_id, season=lg.season, through_week=nfl.week)
                    budgets = waivers_mod.remaining_budget(claims, waiver_budget=waiver_budget)
                    your_remaining = budgets.get(lg.roster_id, waiver_budget)
                    waiver_recs = waiver_value_mod.recommend_adds(
                        free_agent_ids, you.player_ids, valued, profiles, lg,
                        FAAB_BID_CURVE, your_remaining)
                    waiver_recs_count = len(waiver_recs)
                except (httpx.HTTPError, RuntimeError):
                    logging.getLogger("ffdo.api").warning(
                        "home-summary: waiver fetch failed for %s, waiver flag omitted",
                        lg.league_key)
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail="Couldn't reach Sleeper, try again") from exc
        finally:
            sleeper.close()

        all_teams = frozenset(p.team for p in profiles.values() if p.team)
        bye_teams = schedule_mod.bye_teams(games, all_teams) if games else frozenset()
        locked_teams = schedule_mod.locked_teams(games)

        all_pids = {pid for r in rosters for pid in r.player_ids}
        weekly_valued = weekly_lineup_mod.weekly_value(
            all_pids, lg, weekly_points=weekly_proj, profiles=profiles, bye_teams=bye_teams)
        your_weekly_valued = {pid: weekly_valued[pid] for pid in you.player_ids if pid in weekly_valued}
        optimal = weekly_lineup_mod.optimal_slots(your_weekly_valued, lg)
        diff_rows = weekly_lineup_mod.diff(
            current_starters, optimal, locked_teams, weekly_valued, profiles, lg)

        def _starter_row(d) -> dict:
            prof = profiles.get(d.current_player_id) if d.current_player_id else None
            vp = weekly_valued.get(d.current_player_id) if d.current_player_id else None
            swap_to = None
            if d.status == "suggested_swap" and d.optimal_player_id:
                swap_prof = profiles.get(d.optimal_player_id)
                swap_to = swap_prof.full_name if swap_prof else d.optimal_player_id
            return {
                "slot_label": d.slot_label,
                "name": prof.full_name if prof else None,
                "value": round(vp.vor, 1) if vp else 0.0,
                "status": d.status,
                "swap_to": swap_to,
            }

        standings_rank = _standings_rank(rosters)
        power_rows = power_ranking_mod.rank(
            rosters, valued, lg, standings_rank, lg.roster_id, position="OVR", scope="starters")
        your_power_row = next((r for r in power_rows if r.roster_id == lg.roster_id), None)
        power_rank = None
        if your_power_row is not None:
            your_standings_rank = standings_rank.get(lg.roster_id)
            power_rank = {
                "value": your_power_row.power_rank,
                "of": len(rosters),
                "delta_vs_standings": (
                    your_standings_rank - your_power_row.power_rank
                    if your_standings_rank is not None else None),
            }

        matchup = None
        opponent_roster_id = current_week.pairing.get(lg.roster_id)
        if opponent_roster_id is not None:
            opponent = next((r for r in rosters if r.roster_id == opponent_roster_id), None)
            if opponent is not None:
                your_score = matchup_score_mod.team_projected_score(
                    you.starter_ids, weekly_points=weekly_proj, live_points=current_week.live_points,
                    profiles=profiles, locked_teams=locked_teams, scoring_settings=lg.scoring_settings)
                opp_score = matchup_score_mod.team_projected_score(
                    opponent.starter_ids, weekly_points=weekly_proj, live_points=current_week.live_points,
                    profiles=profiles, locked_teams=locked_teams, scoring_settings=lg.scoring_settings)
                matchup = {
                    "opponent_name": opponent.team_name,
                    "your_projected": round(your_score, 1),
                    "opponent_projected": round(opp_score, 1),
                }

        trade_suggestion_count = 0
        for partner_roster in rosters:
            if partner_roster.roster_id == you.roster_id:
                continue
            trade_suggestion_count += len(trade_targets_mod.suggest_for_team(
                you, partner_roster, rosters, valued, lg, free_agent_ids, your_picks,
                pick_curve=PICK_VALUE_CURVE, current_season=lg.season, round_size=lg.num_teams))

        flags = {}
        swaps = sum(1 for d in diff_rows if d.status == "suggested_swap")
        if swaps:
            flags["lineup_swaps"] = swaps
        if waiver_recs_count:
            flags["waiver_adds"] = waiver_recs_count
        if trade_suggestion_count:
            flags["trade_targets"] = trade_suggestion_count

        return {
            "league_key": lg.league_key,
            "name": you.team_name,
            "provider": lg.provider,
            "resolved_format": lg.resolved_format,
            "record": {"wins": you.wins, "losses": you.losses, "ties": you.ties},
            "power_rank": power_rank,
            "matchup": matchup,
            "starters": [_starter_row(d) for d in diff_rows],
            "flags": flags,
        }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_home_summary_endpoint.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `uv run pytest`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/api/app.py tests/api/test_home_summary_endpoint.py
git commit -m "feat: add GET /api/leagues/{league_key}/home-summary

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

## Task 4: Command-center home screen (frontend)

**Files:**
- Create: `src/ffdo/web/home/home.js`
- Create: `src/ffdo/web/home/home.css`
- Modify: `src/ffdo/web/app.js`
- Modify: `src/ffdo/web/index.html`
- Modify: `src/ffdo/web/season/season.js`

**Interfaces:**
- Consumes: `GET /api/leagues` (existing, `app.js`'s `loadLeagues()` shape: `{league_key, name, provider, season, format, resolved_format, draft_status, is_mock, needs_attention}` per league); Task 3's `GET /api/leagues/{league_key}/home-summary`; `season.js`'s existing tab-name vocabulary (`data-panel-tab` values: `lineup`, `power`, `capital`, `trades`, `waivers`, `scorecard`, `targets`).
- Produces: `mount(container)` exported from `home.js`, called by `app.js`'s router for the `#/` route. No other task depends on this one.

- [ ] **Step 1: Write `home.css`**

Create `src/ffdo/web/home/home.css`:

```css
/* Command-center home -- reuses app.css's existing tokens (--bg,
   --surface, --surface-2, --border, --border-strong, --text, --muted,
   --faint, --accent, --green, --red, --amber, --font-sans, --font-mono)
   exclusively, matching season.css's own theme discipline. No new colors. */

.home-continue {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 20px;
  margin-bottom: 20px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
}
.home-continue a { font-weight: 600; }

.home-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
  padding: 4px 20px 28px;
}

.home-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 18px 20px;
  cursor: pointer;
  transition: border-color 0.15s ease;
}
.home-card:hover { border-color: var(--border-strong); }
.home-card.attn { border-color: color-mix(in oklch, var(--amber) 45%, var(--border)); }

.home-card-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px; }
.home-card-name { font-weight: 700; font-size: 14.5px; }
.home-card-sub { font-size: 11px; color: var(--faint); margin-bottom: 12px; }

.home-card-skeleton { color: var(--faint); font-size: 12.5px; padding: 20px 0; }
.home-card-error { color: var(--red); font-size: 12.5px; padding: 10px 0; }
.home-card-retry {
  font: 600 11.5px var(--font-sans);
  color: var(--text);
  background: var(--surface-2);
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  padding: 5px 11px;
  cursor: pointer;
  margin-top: 6px;
}

/* pre-draft minimal card */
.home-card.predraft { display: flex; flex-direction: column; justify-content: center; min-height: 120px; }
.home-predraft-status {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--accent);
  background: color-mix(in oklch, var(--accent) 14%, transparent);
  border-radius: 6px;
  padding: 4px 9px;
  margin-top: 10px;
  align-self: flex-start;
}

.home-rank-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.home-rank-big { font-family: var(--font-mono); font-size: 19px; font-weight: 700; color: var(--accent); }
.home-rank-label { font-size: 10.5px; color: var(--faint); }
.home-rank-delta { font-size: 11px; color: var(--green); font-family: var(--font-mono); }
.home-rank-delta.neg { color: var(--red); }

.home-matchup { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 12px; font-size: 12.5px; }
.home-matchup-score { font-family: var(--font-mono); font-weight: 700; font-size: 15px; }
.home-matchup-vs { color: var(--faint); font-size: 10.5px; text-transform: uppercase; letter-spacing: 1px; }
.home-matchup-opp { color: var(--muted); text-align: right; }

.home-roster-list { display: flex; flex-direction: column; gap: 1px; margin-bottom: 12px; }
.home-roster-row { display: flex; align-items: center; gap: 8px; padding: 4px 0; border-bottom: 1px solid var(--border); font-size: 11.5px; }
.home-roster-row:last-child { border-bottom: none; }
.home-roster-row .pos {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 600;
  color: var(--muted);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 5px;
  width: 26px;
  text-align: center;
  flex-shrink: 0;
}
.home-roster-row .nm { flex: 1; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.home-roster-row.swap-out { color: var(--amber); }
.home-roster-row.swap-out .nm { text-decoration: line-through; text-decoration-color: var(--amber); opacity: 0.65; }
.home-roster-row .val { font-family: var(--font-mono); color: var(--muted); font-size: 11px; }

.home-flags { display: flex; flex-direction: column; gap: 6px; }
.home-flag {
  display: block;
  font-size: 11.5px;
  padding: 5px 9px;
  border-radius: 6px;
}
.home-flag.swap { background: color-mix(in oklch, var(--amber) 12%, transparent); color: var(--amber); }
.home-flag.waiver { background: color-mix(in oklch, var(--green) 12%, transparent); color: var(--green); }
.home-flag.target { background: color-mix(in oklch, var(--accent) 12%, transparent); color: var(--accent); }
```

- [ ] **Step 2: Write `home.js`**

Create `src/ffdo/web/home/home.js`:

```js
// The command-center home screen: a grid of cards, one per tracked
// league, matching board.js/season.js's house style -- plain ES module
// exporting mount(), module-level singleton state (only one home screen
// is ever mounted at a time), direct DOM string-building, no framework.
const LAST_LEAGUE_KEY = "ffdo:lastLeagueKey";
const POLL_INTERVAL_MS = 60000;

let _c, _leagues, _summaries, _pollTimer;

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export async function mount(container) {
  _c = container;
  _summaries = new Map();  // league_key -> {status: "loading"|"ok"|"error", data|error}
  clearInterval(_pollTimer);

  const res = await fetch("/api/leagues");
  _leagues = res.ok ? await res.json() : [];

  if (_leagues.length === 0) {
    location.hash = "#/connect";
    return;
  }

  render();
  loadAllSummaries();
  _pollTimer = setInterval(loadAllSummaries, POLL_INTERVAL_MS);
}

export function unmount() {
  clearInterval(_pollTimer);
  _c = null;
}

function loadAllSummaries() {
  for (const lg of _leagues) {
    if (lg.draft_status !== "complete") continue;
    loadOneSummary(lg.league_key);
  }
}

async function loadOneSummary(leagueKey) {
  const prior = _summaries.get(leagueKey);
  _summaries.set(leagueKey, { status: "loading", data: prior && prior.data });
  if (!prior) render();
  try {
    const res = await fetch(`/api/leagues/${encodeURIComponent(leagueKey)}/home-summary`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      _summaries.set(leagueKey, { status: "error", error: body.detail || "Couldn't load this league" });
    } else {
      _summaries.set(leagueKey, { status: "ok", data: await res.json() });
    }
  } catch (e) {
    _summaries.set(leagueKey, { status: "error", error: "Couldn't load this league" });
  }
  render();
}

function render() {
  if (!_c) return;
  const sorted = [..._leagues].sort((a, b) => a.name.localeCompare(b.name));

  let continueHtml = "";
  let lastKey = null;
  try { lastKey = localStorage.getItem(LAST_LEAGUE_KEY); } catch {}
  const lastLeague = lastKey ? _leagues.find(l => l.league_key === lastKey) : null;
  if (lastLeague) {
    continueHtml = `<div class="home-continue">
      <span>Pick up where you left off</span>
      <a href="#/league/${encodeURIComponent(lastLeague.league_key)}">Continue: ${escapeHtml(lastLeague.name)} &rarr;</a>
    </div>`;
  }

  const cards = sorted.map(lg => cardHtml(lg)).join("");
  _c.innerHTML = `${continueHtml}<div class="home-grid">${cards}</div>`;

  _c.querySelectorAll("[data-home-card]").forEach(el => {
    el.addEventListener("click", (e) => {
      if (e.target.closest("[data-home-retry]")) return;
      const tab = e.target.closest("[data-home-flag]");
      const key = el.dataset.homeCard;
      if (tab) { location.hash = `#/league/${encodeURIComponent(key)}?tab=${tab.dataset.homeFlag}`; return; }
      location.hash = `#/league/${encodeURIComponent(key)}`;
    });
  });
  _c.querySelectorAll("[data-home-retry]").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      loadOneSummary(btn.dataset.homeRetry);
    });
  });
}

function cardHtml(lg) {
  if (lg.draft_status !== "complete") {
    return `<div class="home-card predraft" data-home-card="${escapeHtml(lg.league_key)}">
      <div class="home-card-name">${escapeHtml(lg.name)}</div>
      <div class="home-card-sub">${escapeHtml(lg.provider)} &middot; ${escapeHtml(lg.resolved_format)}</div>
      <span class="home-predraft-status">${escapeHtml(lg.draft_status || "Pre-draft")}</span>
    </div>`;
  }

  const state = _summaries.get(lg.league_key);
  if (!state || state.status === "loading" && !state.data) {
    return `<div class="home-card" data-home-card="${escapeHtml(lg.league_key)}">
      <div class="home-card-head"><div class="home-card-name">${escapeHtml(lg.name)}</div></div>
      <div class="home-card-sub">${escapeHtml(lg.provider)} &middot; ${escapeHtml(lg.resolved_format)}</div>
      <div class="home-card-skeleton">Loading&hellip;</div>
    </div>`;
  }
  if (state.status === "error") {
    return `<div class="home-card" data-home-card="${escapeHtml(lg.league_key)}">
      <div class="home-card-head"><div class="home-card-name">${escapeHtml(lg.name)}</div></div>
      <div class="home-card-sub">${escapeHtml(lg.provider)} &middot; ${escapeHtml(lg.resolved_format)}</div>
      <div class="home-card-error">${escapeHtml(state.error)}</div>
      <button class="home-card-retry" type="button" data-home-retry="${escapeHtml(lg.league_key)}">Retry</button>
    </div>`;
  }

  const d = state.data;
  const hasFlags = d.flags && Object.keys(d.flags).length > 0;
  const record = d.record ? `${d.record.wins}-${d.record.losses}${d.record.ties ? `-${d.record.ties}` : ""}` : "";

  let rankHtml = "";
  if (d.power_rank) {
    const delta = d.power_rank.delta_vs_standings;
    const deltaText = delta == null ? "" : (delta > 0 ? `+${delta}` : `${delta}`);
    const deltaClass = delta != null && delta < 0 ? "neg" : "";
    rankHtml = `<div class="home-rank-row">
      <span class="home-rank-big">${ordinal(d.power_rank.value)}</span>
      <span class="home-rank-label">of ${d.power_rank.of} &middot; power rank</span>
      ${deltaText ? `<span class="home-rank-delta ${deltaClass}">${deltaText}</span>` : ""}
    </div>`;
  }

  let matchupHtml = "";
  if (d.matchup) {
    matchupHtml = `<div class="home-matchup">
      <span class="home-matchup-score">${d.matchup.your_projected.toFixed(1)}</span>
      <span class="home-matchup-vs">vs</span>
      <span class="home-matchup-opp">${d.matchup.opponent_projected.toFixed(1)}<br>${escapeHtml(d.matchup.opponent_name)}</span>
    </div>`;
  }

  const rosterRows = (d.starters || []).map(s => {
    const isSwap = s.status === "suggested_swap";
    const nameHtml = isSwap
      ? `${escapeHtml(s.name || "empty")} &rarr; ${escapeHtml(s.swap_to || "")}`
      : escapeHtml(s.name || "empty");
    return `<div class="home-roster-row${isSwap ? " swap-out" : ""}">
      <span class="pos">${escapeHtml(s.slot_label)}</span>
      <span class="nm">${nameHtml}</span>
      <span class="val">${s.status === "match" || isSwap ? s.value.toFixed(1) : ""}</span>
    </div>`;
  }).join("");

  const flagRows = [];
  if (d.flags.lineup_swaps) {
    flagRows.push(`<span class="home-flag swap" data-home-flag="lineup">${d.flags.lineup_swaps} lineup swap${d.flags.lineup_swaps === 1 ? "" : "s"} suggested</span>`);
  }
  if (d.flags.waiver_adds) {
    flagRows.push(`<span class="home-flag waiver" data-home-flag="waivers">${d.flags.waiver_adds} waiver add${d.flags.waiver_adds === 1 ? "" : "s"} recommended</span>`);
  }
  if (d.flags.trade_targets) {
    flagRows.push(`<span class="home-flag target" data-home-flag="targets">${d.flags.trade_targets} trade target${d.flags.trade_targets === 1 ? "" : "s"} found</span>`);
  }

  return `<div class="home-card${hasFlags ? " attn" : ""}" data-home-card="${escapeHtml(lg.league_key)}">
    <div class="home-card-head"><div class="home-card-name">${escapeHtml(d.name)}</div></div>
    <div class="home-card-sub">${escapeHtml(lg.provider)} &middot; ${escapeHtml(lg.resolved_format)}${record ? ` &middot; ${record}` : ""}</div>
    ${rankHtml}
    ${matchupHtml}
    <div class="home-roster-list">${rosterRows}</div>
    ${hasFlags ? `<div class="home-flags">${flagRows.join("")}</div>` : ""}
  </div>`;
}

function ordinal(n) {
  const rem100 = n % 100;
  if (rem100 >= 11 && rem100 <= 13) return `${n}th`;
  switch (n % 10) {
    case 1: return `${n}st`;
    case 2: return `${n}nd`;
    case 3: return `${n}rd`;
    default: return `${n}th`;
  }
}
```

- [ ] **Step 3: Wire the new route into `app.js`**

In `src/ffdo/web/app.js`, replace the `route()` function:

```js
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
```

with:

```js
let _homeModule = null;

async function route() {
  const hash = location.hash || "#/";
  const leagues = await loadLeagues();

  if (hash === "#/connect") { renderSwitcher(leagues, null); return renderConnect(leagues); }

  const m = hash.match(/^#\/league\/(.+)$/);
  if (m) {
    const key = decodeURIComponent(m[1]).split("?")[0];
    try { localStorage.setItem(LAST_LEAGUE_KEY, key); } catch {}
    renderSwitcher(leagues, key);
    if (_homeModule) { _homeModule.unmount(); _homeModule = null; }
    return renderLeague(key);
  }

  // "#/" — the command-center home grid.
  renderSwitcher(leagues, null);
  if (!leagues.length) { location.hash = "#/connect"; return; }
  view.innerHTML = `<div id="home-root"></div>`;
  try {
    _homeModule = await import("./home/home.js");
    if (!document.querySelector('link[href$="home/home.css"]')) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = "/home/home.css";
      document.head.appendChild(link);
    }
    _homeModule.mount(document.getElementById("home-root"));
  } catch (e) {
    document.getElementById("home-root").textContent =
      "Couldn't load the home screen — check the console.";
    console.error("home module failed to load", e);
  }
}
```

The `?tab=` suffix `home.js`'s flag click-through appends stays in
`location.hash` itself (only the `key` variable used for the API
call/`localStorage` strips it via `.split("?")[0]` above) — Step 4 below
makes `season.js` read that same `location.hash` directly at mount time,
so a flag click actually lands on the right tab, per the spec's Flag
click-through section.

Also note: `renderSwitcher(leagues, null)` above already matches
`renderSwitcher`'s existing signature (`renderSwitcher(leagues, activeKey)`), so no change needed there.

- [ ] **Step 4: Make `season.js` land on the tab a flag click asked for**

In `src/ffdo/web/season/season.js`, replace the three lines in
`mountSeason()` that reset tab state (currently):

```js
  _panel = "lineup";
  _pos = "OVR";
  _scope = "starters";
```

with:

```js
  // A command-center flag click (home.js) navigates to
  // "#/league/<key>?tab=<name>" -- read that suffix directly off the
  // current hash rather than threading a parameter through app.js's
  // renderLeague()/board.js's mount(), since both already exist and
  // neither currently passes anything beyond the league key.
  const tabParam = (location.hash.split("?tab=")[1] || "").split("&")[0];
  const validTabs = ["lineup", "power", "capital", "trades", "waivers", "scorecard", "targets"];
  _panel = validTabs.includes(tabParam) ? tabParam : "lineup";
  _pos = "OVR";
  _scope = "starters";
```

- [ ] **Step 5: Make the brand a "Home" link**

In `src/ffdo/web/index.html`, replace:

```html
    <span class="brand">FFDO</span>
```

with:

```html
    <a href="#/" class="brand">FFDO</a>
```

- [ ] **Step 6: Style the brand link to not look like a body-text link**

In `src/ffdo/web/app.css`, immediately after the existing
`#switcher-bar .brand { ... }` rule, add:

```css
#switcher-bar .brand:hover { color: var(--accent); }
```

- [ ] **Step 7: Manually verify against a real league**

Start the dev server (`ffdo-api`, port 8150, via this worktree's
`.claude/launch.json`) against a real tracked league database. Confirm:
- Navigating to `http://localhost:8150/#/` (or just the bare root) shows
  the grid, not the old auto-redirect.
- Each season-mode card fills in from its loading skeleton as its
  `home-summary` response arrives; a pre-draft league (if one is
  tracked) shows the minimal card immediately.
- Cards are sorted alphabetically.
- Clicking a card body opens that league at its normal default (Lineup)
  tab; clicking a flag opens the league landed directly on the matching
  tab (a waiver-adds flag → Waivers tab, a trade-target flag → Targets
  tab, a lineup-swap flag → Lineup tab).
- Clicking the "FFDO" brand text from inside a league returns to the
  grid.
- With no tracked leagues (or by simulating it), `#/` still goes to
  `#/connect`.
- Open the browser console and confirm no errors; wait roughly 60s (or
  temporarily lower `POLL_INTERVAL_MS` for the check, then restore it)
  and confirm a summary re-fetch fires via the Network tab.
- Simulate a per-league failure (a scoped `window.fetch` override
  returning a 502 for one league's `home-summary`, same technique used
  to verify Trade Targets' click-through in an earlier sub-project) and
  confirm only that one card shows the error + Retry state, and clicking
  Retry re-fetches just that card.

- [ ] **Step 8: Run the full test suite once more**

Run: `uv run pytest`
Expected: PASS, no regressions (this task is frontend/docs-only from the
backend's perspective).

- [ ] **Step 9: Commit**

```bash
git add src/ffdo/web/home/home.js src/ffdo/web/home/home.css src/ffdo/web/app.js src/ffdo/web/index.html src/ffdo/web/app.css src/ffdo/web/season/season.js
git commit -m "feat: add command-center home grid, replace #/'s auto-redirect

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
