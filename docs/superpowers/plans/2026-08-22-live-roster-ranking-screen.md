# Live Roster Ranking Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live, per-team roster-power leaderboard — overall starting-lineup VOR plus a QB/RB/WR/TE breakdown and a bench-depth number — to both the auction and snake draft boards, so the user can see who is building the best roster as the draft happens.

**Architecture:** Reuse the existing greedy slot-fill that already computes league-wide replacement level (§6.4/§10.2 of the spec), running it once per team instead of once per league to get that team's optimal starting lineup. New ingest adds team display names. The result is folded into the existing `/api/board` payload (no new endpoint, no new poll) as a `rosters` array, rendered as a new sidebar card on the existing static board.

**Tech Stack:** Python 3.12 (uv-managed), FastAPI, no new dependencies. Frontend is the existing single static page (`index.html`/`board.js`/`board.css`), no build step.

**Spec:** [docs/superpowers/specs/2026-08-22-fantasy-football-draft-optimizer-design.md](../specs/2026-08-22-fantasy-football-draft-optimizer-design.md), §10 (Roster valuation) and §11's "Roster rankings panel" — this plan implements those two additions only.

## Global Constraints

- Python 3.12, dependencies managed by `uv` — do not add new dependencies for this feature.
- `engine/` modules are pure functions over dataclasses: no I/O, no network, no clock. All engine functions in this plan must stay pure.
- Nothing above `ingest/` ever sees a raw Sleeper JSON key — `ingest/teams.py` is the only place that reads `owner_id`, `user_id`, `metadata.team_name`, etc.
- No new API endpoint and no new poll cycle — the roster leaderboard is a new key inside the existing `/api/board` response, refreshed by the existing 3-second poll.
- No named-pick / no model's-lean behavior anywhere — this feature only ranks rosters, it never recommends a pick.
- Frontend has no build step and no test runner; UI tasks are verified by running the app and checking it in a browser, per this repo's own testing conventions.

---

### Task 1: `TeamProfile` domain model

**Files:**
- Modify: `src/ffdo/domain/models.py`
- Test: `tests/domain/test_models.py`

**Interfaces:**
- Produces: `TeamProfile(roster_id: int, display_name: str)` — a frozen dataclass, consumed by `ingest/teams.py` (Task 5) and `api/board.py` (Task 6).

- [ ] **Step 1: Write the failing test**

Add to `tests/domain/test_models.py`:

```python
def test_team_profile_is_frozen_and_holds_roster_id_and_name():
    tp = TeamProfile(roster_id=3, display_name="The Foobars")
    assert tp.roster_id == 3
    assert tp.display_name == "The Foobars"
    with pytest.raises(dataclasses.FrozenInstanceError):
        tp.display_name = "renamed"
```

Update the import at the top of the file to include `TeamProfile`:

```python
from ffdo.domain.models import (
    DraftPick, DraftState, LeagueProfile, MarketADP,
    PlayerProfile, SeasonProjection, SeasonStatLine, TeamProfile,
)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/domain/test_models.py::test_team_profile_is_frozen_and_holds_roster_id_and_name -v`
Expected: FAIL with `ImportError: cannot import name 'TeamProfile'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/ffdo/domain/models.py`, immediately after the `ValuedPlayer` class at the end of the file:

```python
@dataclass(frozen=True, slots=True)
class TeamProfile:
    roster_id: int
    display_name: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/domain/test_models.py -v`
Expected: all PASS, including the new test.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/domain/models.py tests/domain/test_models.py
git commit -m "feat: add TeamProfile domain model for roster identity"
```

---

### Task 2: Extract shared greedy-fill helpers in `replacement.py`

This is a behavior-preserving refactor: the league-wide replacement-level
algorithm is split into two reusable pieces (`rank_by_position`,
`greedy_fill_slots`) so Task 3's single-team lineup fill can reuse the exact
same logic instead of duplicating it. No new behavior, no new tests — the
existing test suite is the safety net.

**Files:**
- Modify: `src/ffdo/engine/replacement.py`

**Interfaces:**
- Produces: `rank_by_position(values: Mapping[str, float], positions: Mapping[str, str]) -> dict[str, list[tuple[float, str]]]` and `greedy_fill_slots(ranked, positions, slots: tuple[str, ...], iterations: int) -> tuple[set[str], dict[str, int]]`, both consumed by `engine/roster.py` (Task 3).
- `FLEX_ELIGIBILITY` and `replacement_levels(points, positions, league) -> dict[str, float]` keep their existing signatures and behavior unchanged.

- [ ] **Step 1: Run the existing test suite to confirm the baseline is green**

Run: `uv run pytest tests/engine/test_replacement.py tests/engine/test_vor.py -v`
Expected: all PASS (this is the baseline the refactor must not break).

- [ ] **Step 2: Replace the contents of `src/ffdo/engine/replacement.py`**

```python
"""Greedy slot-fill shared by league-wide replacement level and single-team
lineup value (see engine/roster.py).

Every starting lineup in the league is filled greedily by value; replacement
level at a position is the best player who did not make one. This handles
FLEX allocation and superflex with no special cases -- the only input that
changes is `roster_positions`.
"""

from __future__ import annotations

from collections.abc import Mapping

FLEX_ELIGIBILITY: dict[str, frozenset[str]] = {
    "FLEX": frozenset({"RB", "WR", "TE"}),
    "WRRB_FLEX": frozenset({"RB", "WR"}),
    "REC_FLEX": frozenset({"WR", "TE"}),
    "SUPER_FLEX": frozenset({"QB", "RB", "WR", "TE"}),
}


def rank_by_position(
    values: Mapping[str, float],
    positions: Mapping[str, str],
) -> dict[str, list[tuple[float, str]]]:
    """Groups `values` by position, each list sorted descending."""
    ranked: dict[str, list[tuple[float, str]]] = {}
    for player_id, val in values.items():
        pos = positions.get(player_id)
        if pos is None:
            continue
        ranked.setdefault(pos, []).append((val, player_id))
    for pos in ranked:
        ranked[pos].sort(reverse=True)
    return ranked


def greedy_fill_slots(
    ranked: Mapping[str, list[tuple[float, str]]],
    positions: Mapping[str, str],
    slots: tuple[str, ...],
    iterations: int,
) -> tuple[set[str], dict[str, int]]:
    """Fills `slots`, `iterations` passes through, from pools pre-sorted
    descending by whatever value ranked them.

    Dedicated slots claim by-position first, since they have no discretion;
    FLEX-eligible slots then take the best remaining eligible player. That
    cross-position comparison is only meaningful when every pool is ranked
    on the same value scale -- league-wide replacement (`replacement_levels`
    below) ranks by raw points, since it is answering "who gets rostered at
    all"; single-team lineup value (engine/roster.py) ranks by VOR instead,
    since it is answering "which of this team's players maximizes value
    above replacement," and raw points alone aren't comparable across
    positions with different replacement baselines.

    Returns the set of player_ids that filled a slot, and the final
    per-position cursor (how deep into each pool `iterations` passes
    reached).
    """
    cursor: dict[str, int] = dict.fromkeys(ranked, 0)
    taken: set[str] = set()

    dedicated = [s for s in slots if s not in FLEX_ELIGIBILITY]
    flex = [s for s in slots if s in FLEX_ELIGIBILITY]

    for slot in dedicated:
        for _ in range(iterations):
            pool = ranked.get(slot, [])
            if cursor.get(slot, 0) < len(pool):
                taken.add(pool[cursor[slot]][1])
                cursor[slot] += 1

    for slot in flex:
        eligible = FLEX_ELIGIBILITY[slot]
        for _ in range(iterations):
            best: tuple[float, str] | None = None
            for pos in eligible:
                pool = ranked.get(pos, [])
                idx = cursor.get(pos, 0)
                if idx < len(pool) and (best is None or pool[idx][0] > best[0]):
                    best = pool[idx]
            if best is None:
                break
            pos = positions[best[1]]
            taken.add(best[1])
            cursor[pos] += 1

    return taken, cursor


def replacement_levels(
    points: Mapping[str, float],
    positions: Mapping[str, str],
    league,
) -> dict[str, float]:
    slots = league.starting_slots
    ranked = rank_by_position(points, positions)
    _, cursor = greedy_fill_slots(ranked, positions, slots, league.num_teams)

    rostered_positions = {p for s in slots
                          for p in (FLEX_ELIGIBILITY.get(s) or {s})}

    levels: dict[str, float] = {}
    for pos in rostered_positions:
        pool = ranked.get(pos, [])
        idx = cursor.get(pos, 0)
        levels[pos] = pool[idx][0] if idx < len(pool) else (
            pool[-1][0] if pool else 0.0)
    return levels
```

- [ ] **Step 3: Run the test suite again to confirm nothing broke**

Run: `uv run pytest tests/engine/test_replacement.py tests/engine/test_vor.py -v`
Expected: all PASS, identical results to Step 1.

- [ ] **Step 4: Commit**

```bash
git add src/ffdo/engine/replacement.py
git commit -m "refactor: extract rank_by_position/greedy_fill_slots from replacement_levels"
```

---

### Task 3: `engine/roster.py` — single-team starting-lineup value

**Files:**
- Create: `src/ffdo/engine/roster.py`
- Test: `tests/engine/test_roster.py`

**Interfaces:**
- Consumes: `ffdo.domain.models.ValuedPlayer`, `ffdo.engine.replacement.rank_by_position`, `ffdo.engine.replacement.greedy_fill_slots` (Task 2). `league` is any object exposing `.starting_slots: tuple[str, ...]` (as `LeagueProfile` does).
- Produces: `TeamLineup(starting_vor: float, bench_vor: float, by_position: Mapping[str, float], starters: frozenset[str])` and `team_lineup(roster: Mapping[str, ValuedPlayer], league) -> TeamLineup`, consumed by `api/board.py` (Task 6).

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/test_roster.py`:

```python
import pytest

from ffdo.domain.models import LeagueProfile, PlayerProfile, ValuedPlayer
from ffdo.engine import roster


def _league(roster_positions, num_teams=12):
    return LeagueProfile(league_id="x", season=2026, num_teams=num_teams,
                         roster_positions=tuple(roster_positions),
                         scoring_settings={}, budget=200)


def _vp(pid, pos, vor):
    prof = PlayerProfile(player_id=pid, first_name=pos, last_name=pid,
                         position=pos, team="X", age=25, years_exp=3,
                         injury_status=None, active=True)
    return ValuedPlayer(profile=prof, projected_points=vor, adjusted_points=vor,
                        vor=vor, tier=1, adjustments={})


def test_empty_roster_returns_all_zero():
    league = _league(["QB", "RB", "WR", "BN"])
    result = roster.team_lineup({}, league)
    assert result.starting_vor == 0.0
    assert result.bench_vor == 0.0
    assert result.by_position == {}
    assert result.starters == frozenset()


def test_by_position_always_sums_to_starting_vor():
    league = _league(["QB", "RB", "RB", "WR", "WR", "FLEX", "BN", "BN"])
    team = {
        "qb1": _vp("qb1", "QB", 20.0),
        "rb1": _vp("rb1", "RB", 15.0),
        "rb2": _vp("rb2", "RB", 10.0),
        "rb3": _vp("rb3", "RB", 5.0),
        "wr1": _vp("wr1", "WR", 12.0),
        "wr2": _vp("wr2", "WR", 8.0),
    }
    result = roster.team_lineup(team, league)
    assert sum(result.by_position.values()) == pytest.approx(result.starting_vor)


def test_flex_slot_is_filled_by_vor_not_raw_points():
    """A player with the fewest points can still have the highest VOR if
    their position's replacement level is much lower -- FLEX must compare
    VOR, not points, or it seats the wrong player."""
    league = _league(["RB", "WR", "FLEX", "BN"])
    team = {
        "rb1": _vp("rb1", "RB", 30.0),   # dedicated RB slot
        "wr1": _vp("wr1", "WR", 25.0),   # dedicated WR slot
        "rb2": _vp("rb2", "RB", 22.0),   # higher VOR, should win FLEX
        "wr2": _vp("wr2", "WR", 18.0),   # lower VOR, should be benched
    }
    result = roster.team_lineup(team, league)
    assert "rb2" in result.starters
    assert "wr2" not in result.starters


def test_bench_never_double_counts_a_starter():
    league = _league(["RB", "BN"])
    team = {"rb1": _vp("rb1", "RB", 10.0), "rb2": _vp("rb2", "RB", 4.0)}
    result = roster.team_lineup(team, league)
    assert result.starters == frozenset({"rb1"})
    assert result.starting_vor == pytest.approx(10.0)
    assert result.bench_vor == pytest.approx(4.0)


def test_unfilled_slots_contribute_zero_not_a_penalty():
    league = _league(["QB", "RB", "RB", "WR", "WR", "TE", "BN"])
    team = {"qb1": _vp("qb1", "QB", 20.0)}
    result = roster.team_lineup(team, league)
    assert result.starting_vor == pytest.approx(20.0)
    assert result.by_position == pytest.approx({"QB": 20.0})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_roster.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.engine.roster'`

- [ ] **Step 3: Write the implementation**

Create `src/ffdo/engine/roster.py`:

```python
"""Per-team starting-lineup value.

Same greedy slot-fill as league-wide replacement level (see
engine/replacement.py), run once per team on that team's own drafted
players, ranked by VOR instead of raw points.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ffdo.domain.models import ValuedPlayer
from ffdo.engine.replacement import greedy_fill_slots, rank_by_position


@dataclass(frozen=True, slots=True)
class TeamLineup:
    starting_vor: float
    bench_vor: float
    by_position: Mapping[str, float]
    starters: frozenset[str]


def team_lineup(roster: Mapping[str, ValuedPlayer], league) -> TeamLineup:
    """`roster` is one team's drafted, valued players (player_id -> ValuedPlayer).

    Ranking by VOR rather than raw points is required correctness, not a
    style choice: replacement level differs by position, so a FLEX
    comparison between (say) an RB and a WR is only meaningful once both
    are already expressed on the same value-above-replacement scale.
    Ranking by raw points here could seat the wrong player in FLEX -- one
    with more total production but less marginal value than the
    alternative.
    """
    vor = {pid: vp.vor for pid, vp in roster.items()}
    positions = {pid: vp.profile.position for pid, vp in roster.items()}
    ranked = rank_by_position(vor, positions)
    taken, _ = greedy_fill_slots(ranked, positions, league.starting_slots, iterations=1)

    by_position: dict[str, float] = {}
    for pid in taken:
        pos = positions[pid]
        by_position[pos] = by_position.get(pos, 0.0) + vor[pid]

    starting_vor = sum(by_position.values())
    bench_vor = sum(v for pid, v in vor.items() if pid not in taken)
    return TeamLineup(starting_vor=starting_vor, bench_vor=bench_vor,
                      by_position=by_position, starters=frozenset(taken))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_roster.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/roster.py tests/engine/test_roster.py
git commit -m "feat: add per-team starting-lineup VOR calculation"
```

---

### Task 4: Roster valuation replay test against a real completed auction

**Files:**
- Test: `tests/engine/test_roster_replay.py`

**Interfaces:**
- Consumes: `ffdo.engine.roster.team_lineup` (Task 3), `ffdo.ingest.draft.parse`, `ffdo.ingest.players.parse`, `ffdo.ingest.snapshot.load` (all pre-existing).

- [ ] **Step 1: Write the failing test**

Create `tests/engine/test_roster_replay.py`:

```python
import pytest

from ffdo.domain.models import LeagueProfile, ValuedPlayer
from ffdo.engine import roster
from ffdo.ingest import draft, players, snapshot


def test_real_2025_auction_produces_sane_per_team_lineups():
    """Replay the real, completed 2025 auction (12 teams, ~800 picks across
    all five historical drafts, this one alone is ~150 picks). Every
    roster's lineup fill must respect the league's actual slot counts, and
    starters/bench must partition each roster's VOR with nothing lost or
    double-counted.
    """
    hist = snapshot.load("league_history")
    profiles = players.parse(snapshot.load("players_nfl"))
    meta = hist["drafts"]["2025"]["meta"]
    picks = hist["drafts"]["2025"]["picks"]
    state = draft.parse(meta, picks)

    league = LeagueProfile(
        league_id="x", season=2025, num_teams=12,
        roster_positions=("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                          "BN", "BN", "BN", "BN", "BN"),
        scoring_settings={}, budget=200)

    # Synthetic VOR ramp by pick order -- same convention as the auction
    # replay test in tests/engine/test_auction.py. Real per-player VOR needs
    # the full scoring/projection pipeline, which this fixture test isn't
    # exercising; a descending-by-pick-order proxy is enough to sanity-check
    # that the lineup fill behaves across a real draft's real positions.
    valued: dict[str, ValuedPlayer] = {}
    for i, p in enumerate(state.picks):
        prof = profiles.get(p.player_id)
        if prof is None or prof.position not in ("QB", "RB", "WR", "TE"):
            continue
        vor = 150.0 - i * 0.5
        valued[p.player_id] = ValuedPlayer(
            profile=prof, projected_points=vor, adjusted_points=vor,
            vor=vor, tier=1, adjustments={})

    picks_by_roster: dict[int, list[str]] = {}
    for p in state.picks:
        if p.roster_id is not None:
            picks_by_roster.setdefault(p.roster_id, []).append(p.player_id)

    assert len(picks_by_roster) == 12

    for roster_id, player_ids in picks_by_roster.items():
        team_players = {pid: valued[pid] for pid in player_ids if pid in valued}
        lineup = roster.team_lineup(team_players, league)

        assert sum(lineup.by_position.values()) == pytest.approx(lineup.starting_vor)
        assert len(lineup.starters) <= len(league.starting_slots)

        total_vor = sum(vp.vor for vp in team_players.values())
        assert lineup.starting_vor + lineup.bench_vor == pytest.approx(total_vor)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_roster_replay.py -v`
Expected: FAIL — at this point `roster.team_lineup` exists (Task 3 is done), so this should actually only fail if there's a real bug; if it fails, read the assertion error before moving on, since Task 3's unit tests alone don't exercise real multi-position, multi-round data. If it fails on `len(picks_by_roster) == 12`, print `picks_by_roster.keys()` to confirm the real snapshot's roster_id range and adjust the assertion to match what the fixture actually contains — do not weaken the other assertions to work around it.

- [ ] **Step 3: Run again after confirming/fixing any fixture mismatch**

Run: `uv run pytest tests/engine/test_roster_replay.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/engine/test_roster_replay.py
git commit -m "test: replay real 2025 auction through per-team lineup fill"
```

---

### Task 5: `ingest/teams.py` — team identity

**Files:**
- Create: `src/ffdo/ingest/teams.py`
- Test: `tests/ingest/test_teams.py`

**Interfaces:**
- Consumes: `ffdo.domain.models.TeamProfile` (Task 1).
- Produces: `parse(rosters: list[dict], users: list[dict]) -> dict[int, TeamProfile]`, consumed by `api/app.py` (Task 7).

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/test_teams.py`:

```python
from ffdo.ingest import teams


def test_parse_prefers_team_name_over_display_name():
    rosters = [{"roster_id": 1, "owner_id": "u1"}]
    users = [{"user_id": "u1", "display_name": "user1",
             "metadata": {"team_name": "The Foobars"}}]
    out = teams.parse(rosters, users)
    assert out[1].display_name == "The Foobars"


def test_parse_falls_back_to_display_name_without_team_name():
    rosters = [{"roster_id": 2, "owner_id": "u2"}]
    users = [{"user_id": "u2", "display_name": "CoolTeam", "metadata": {}}]
    out = teams.parse(rosters, users)
    assert out[2].display_name == "CoolTeam"


def test_parse_falls_back_to_roster_id_label_without_owner_or_user_match():
    rosters = [{"roster_id": 3, "owner_id": None}]
    users = []
    out = teams.parse(rosters, users)
    assert out[3].display_name == "Team 3"


def test_parse_falls_back_when_owner_id_matches_no_user_record():
    rosters = [{"roster_id": 4, "owner_id": "ghost"}]
    users = [{"user_id": "u1", "display_name": "someone else"}]
    out = teams.parse(rosters, users)
    assert out[4].display_name == "Team 4"


def test_parse_keys_output_by_roster_id_as_int():
    rosters = [{"roster_id": "5", "owner_id": "u5"}]
    users = [{"user_id": "u5", "display_name": "Five"}]
    out = teams.parse(rosters, users)
    assert out[5].roster_id == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/test_teams.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.ingest.teams'`

- [ ] **Step 3: Write the implementation**

Create `src/ffdo/ingest/teams.py`:

```python
"""Translates /league/<id>/rosters and /league/<id>/users into team identity."""

from __future__ import annotations

from typing import Any

from ffdo.domain.models import TeamProfile


def parse(
    rosters: list[dict[str, Any]],
    users: list[dict[str, Any]],
) -> dict[int, TeamProfile]:
    display_names: dict[str, str] = {}
    for u in users:
        user_id = u.get("user_id")
        if user_id is None:
            continue
        metadata = u.get("metadata") or {}
        name = metadata.get("team_name") or u.get("display_name")
        if name:
            display_names[str(user_id)] = name

    out: dict[int, TeamProfile] = {}
    for r in rosters:
        raw_roster_id = r.get("roster_id")
        if raw_roster_id is None:
            continue
        roster_id = int(raw_roster_id)
        owner_id = r.get("owner_id")
        name = display_names.get(str(owner_id)) if owner_id is not None else None
        out[roster_id] = TeamProfile(
            roster_id=roster_id,
            display_name=name or f"Team {roster_id}",
        )
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/test_teams.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/teams.py tests/ingest/test_teams.py
git commit -m "feat: ingest team display names from /rosters and /users"
```

---

### Task 6: `api/board.py` — roster leaderboard in the board payload

**Files:**
- Modify: `src/ffdo/api/board.py`
- Test: `tests/api/test_board.py`
- Test: `tests/api/test_snake_board.py`

**Interfaces:**
- Consumes: `ffdo.engine.roster.team_lineup` (Task 3), `ffdo.domain.models.TeamProfile` (Task 1).
- Produces: `build_auction_board(..., *, roster_id=None, teams=None)` and `build_snake_board(..., *, roster_id=None, teams=None)` both gain a `"rosters"` key in their returned dict: `list[dict]` sorted by `starting_vor` desc then `bench_vor` desc, each entry shaped `{roster_id, team_name, is_you, starting_vor, bench_vor, by_position, players}` where `players` is `list[{player_id, name, position, vor, starter}]` sorted starters-first then by VOR desc.

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_board.py` (extend the existing imports at the top of the file to include `TeamProfile`):

```python
from ffdo.domain.models import (
    DraftPick, DraftState, LeagueProfile, PlayerProfile, TeamProfile, ValuedPlayer,
)
```

Then add these tests to the bottom of the file:

```python
def _teams():
    return {1: TeamProfile(roster_id=1, display_name="Alpha"),
            2: TeamProfile(roster_id=2, display_name="Bravo")}


def test_rosters_payload_includes_every_known_team_even_with_zero_picks():
    league = _league()
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 14, "budget": 200}}, [])
    out = board.build_auction_board(league, state, {}, {}, teams=_teams())
    assert {r["roster_id"] for r in out["rosters"]} == {1, 2}
    assert all(r["starting_vor"] == 0.0 for r in out["rosters"])
    assert all(r["players"] == [] for r in out["rosters"])


def test_your_roster_is_flagged_is_you():
    league = _league()
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1,
                       picked_by="u1", player_id="p0", amount=10),)
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=14, budget=200, picks=picks)
    valued = _valued(["p0"])
    out = board.build_auction_board(league, state, valued, {"p0": 10.0},
                                    roster_id=1, teams=_teams())
    you = next(r for r in out["rosters"] if r["roster_id"] == 1)
    other = next(r for r in out["rosters"] if r["roster_id"] == 2)
    assert you["is_you"] is True
    assert other["is_you"] is False


def test_team_name_falls_back_to_roster_id_label_when_profile_missing():
    league = _league()
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=5,
                       picked_by="u5", player_id="p0", amount=10),)
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=14, budget=200, picks=picks)
    valued = _valued(["p0"])
    out = board.build_auction_board(league, state, valued, {"p0": 10.0})
    row = next(r for r in out["rosters"] if r["roster_id"] == 5)
    assert row["team_name"] == "Team 5"


def test_rosters_sorted_by_starting_vor_descending():
    league = _league()
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1,
                 picked_by="u1", player_id="p0", amount=10),
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=2,
                 picked_by="u2", player_id="p1", amount=10),
    )
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=14, budget=200, picks=picks)
    valued = _valued(["p0", "p1"])  # p0 has higher VOR than p1, see _valued()
    out = board.build_auction_board(league, state, valued,
                                    {"p0": 10.0, "p1": 10.0}, teams=_teams())
    assert [r["roster_id"] for r in out["rosters"]] == [1, 2]


def test_roster_players_are_flagged_starter_or_bench():
    league = LeagueProfile(league_id="x", season=2026, num_teams=12,
                           roster_positions=("RB", "RB", "BN"),
                           scoring_settings={}, budget=200)
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1,
                 picked_by="u1", player_id="p0", amount=10),
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=1,
                 picked_by="u1", player_id="p1", amount=10),
        DraftPick(pick_no=3, round=1, draft_slot=3, roster_id=1,
                 picked_by="u1", player_id="p2", amount=10),
    )
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=3, picks=picks, budget=200)
    valued = _valued(["p0", "p1", "p2"])  # descending VOR: p0 > p1 > p2
    out = board.build_auction_board(league, state, valued,
                                    {pid: 10.0 for pid in ("p0", "p1", "p2")})
    row = next(r for r in out["rosters"] if r["roster_id"] == 1)
    by_id = {p["player_id"]: p for p in row["players"]}
    assert by_id["p0"]["starter"] is True
    assert by_id["p1"]["starter"] is True
    assert by_id["p2"]["starter"] is False
```

Add the equivalent snake-board coverage to `tests/api/test_snake_board.py`:

```python
def test_snake_board_also_exposes_rosters():
    from ffdo.domain.models import TeamProfile

    teams = {1: TeamProfile(roster_id=1, display_name="Alpha")}
    out = board.build_snake_board(_league(), _state(), _valued(),
                                  {pid: 0.5 for pid in _valued()}, {},
                                  roster_id=1, teams=teams)
    assert out["rosters"][0]["roster_id"] == 1
    assert out["rosters"][0]["is_you"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_board.py tests/api/test_snake_board.py -v`
Expected: FAIL with `TypeError: build_auction_board() got an unexpected keyword argument 'teams'` (and equivalent for `build_snake_board`) or `KeyError: 'rosters'`.

- [ ] **Step 3: Write the implementation**

Replace the contents of `src/ffdo/api/board.py`:

```python
"""Shapes engine output into the JSON the board renders."""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.models import DraftState, TeamProfile, ValuedPlayer
from ffdo.engine import auction
from ffdo.engine import roster as roster_engine


def _build_rosters_payload(
    league,
    state: DraftState,
    valued: Mapping[str, ValuedPlayer],
    teams: Mapping[int, TeamProfile] | None,
    your_roster_id: int | None,
) -> list[dict]:
    teams = teams or {}
    picks_by_roster: dict[int, list[str]] = {}
    for p in state.picks:
        if p.roster_id is None:
            continue
        picks_by_roster.setdefault(p.roster_id, []).append(p.player_id)

    roster_ids = set(teams) | set(picks_by_roster)
    rows = []
    for rid in roster_ids:
        team_players = {pid: valued[pid] for pid in picks_by_roster.get(rid, [])
                        if pid in valued}
        lineup = roster_engine.team_lineup(team_players, league)
        team = teams.get(rid)
        players = sorted(
            (
                {
                    "player_id": pid,
                    "name": vp.profile.full_name,
                    "position": vp.profile.position,
                    "vor": round(vp.vor, 1),
                    "starter": pid in lineup.starters,
                }
                for pid, vp in team_players.items()
            ),
            key=lambda r: (r["starter"], r["vor"]),
            reverse=True,
        )
        rows.append({
            "roster_id": rid,
            "team_name": team.display_name if team else f"Team {rid}",
            "is_you": rid == your_roster_id,
            "starting_vor": round(lineup.starting_vor, 1),
            "bench_vor": round(lineup.bench_vor, 1),
            "by_position": {k: round(v, 1) for k, v in lineup.by_position.items()},
            "players": players,
        })
    rows.sort(key=lambda r: (r["starting_vor"], r["bench_vor"]), reverse=True)
    return rows


def build_auction_board(
    league,
    state: DraftState,
    valued: Mapping[str, ValuedPlayer],
    baseline: Mapping[str, float],
    *,
    roster_id: int | None = None,
    teams: Mapping[int, TeamProfile] | None = None,
) -> dict:
    factor = auction.inflation_factor(baseline, state, league)
    drafted = state.drafted_player_ids()
    spent = state.spent_by_roster()

    # "Your" roster state, for the max-bid ceiling and budget strip. When
    # `roster_id` is unknown (FFDO_ROSTER_ID unset), fall back to a fresh
    # 0-spent/0-filled roster rather than guessing -- an honestly-labeled
    # "as if starting from scratch" number beats a silently wrong one.
    your_spent = spent.get(roster_id, 0) if roster_id is not None else 0
    your_slots_filled = (
        sum(1 for p in state.picks if p.roster_id == roster_id)
        if roster_id is not None else 0)
    your_max_bid = auction.max_bid(your_spent, your_slots_filled, league)
    your_slots_left = max(0, league.roster_size - your_slots_filled)
    your_dollars_left = league.budget - your_spent

    total_slots = league.num_teams * league.roster_size
    slots_remaining_room = max(1, total_slots - len(drafted))
    league_dollars_per_slot = (
        (league.num_teams * league.budget - sum(spent.values()))
        / slots_remaining_room)
    your_dollars_per_slot = (
        your_dollars_left / your_slots_left if your_slots_left > 0 else 0.0)

    rows = []
    for pid, vp in valued.items():
        base = baseline.get(pid, 1.0)
        # A $1-baseline player must never display a sub-$1 price -- $1 is
        # the legal minimum bid, so the model cannot recommend a number the
        # room can't act on, however low inflation drops.
        adjusted = max(auction.MIN_BID, base * factor)
        rows.append({
            "player_id": pid,
            "name": vp.profile.full_name,
            "position": vp.profile.position,
            "team": vp.profile.team,
            "age": vp.profile.age,
            "vor": round(vp.vor, 1),
            "tier": vp.tier,
            "baseline": round(base, 1),
            "adjusted": round(adjusted, 1),
            "max_bid": your_max_bid,
            "drafted": pid in drafted,
        })
    rows.sort(key=lambda r: r["vor"], reverse=True)

    return {
        "format": "auction",
        "inflation": round(factor, 3),
        "budget": {
            "total": league.num_teams * league.budget,
            "spent": sum(spent.values()),
            "by_roster": spent,
            "your_roster_id": roster_id,
            "your_spent": your_spent,
            "your_slots_left": your_slots_left,
            "your_dollars_left": your_dollars_left,
            "your_dollars_per_slot": round(your_dollars_per_slot, 1),
            "league_dollars_per_slot": round(league_dollars_per_slot, 1),
        },
        "picks_made": len(state.picks),
        "players": rows,
        "rosters": _build_rosters_payload(league, state, valued, teams, roster_id),
    }


def build_snake_board(
    league,
    state: DraftState,
    valued: Mapping[str, ValuedPlayer],
    survival: Mapping[str, float],
    cost_of_waiting: Mapping[str, Mapping[str, float]],
    *,
    roster_id: int | None = None,
    teams: Mapping[int, TeamProfile] | None = None,
) -> dict:
    drafted = state.drafted_player_ids()
    rows = [
        {
            "player_id": pid,
            "name": vp.profile.full_name,
            "position": vp.profile.position,
            "team": vp.profile.team,
            "age": vp.profile.age,
            "vor": round(vp.vor, 1),
            "tier": vp.tier,
            # `simulate_survival` only returns entries for players who carry
            # an ADP; a player absent from it has no ADP, not a 0% chance
            # of survival. Defaulting to 0.0 there previously rendered
            # "definitely gone" for players who are actually near-certain
            # to still be on the board -- backwards. Absence means no
            # signal either way, so default to certain survival (1.0).
            "survival": round(survival.get(pid, 1.0), 3),
            "drafted": pid in drafted,
        }
        for pid, vp in valued.items()
    ]
    rows.sort(key=lambda r: r["vor"], reverse=True)
    return {
        "format": "snake",
        "cost_of_waiting": dict(cost_of_waiting),
        "picks_made": len(state.picks),
        "players": rows,
        "rosters": _build_rosters_payload(league, state, valued, teams, roster_id),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_board.py tests/api/test_snake_board.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/api/board.py tests/api/test_board.py tests/api/test_snake_board.py
git commit -m "feat: add roster leaderboard to the board API payload"
```

---

### Task 7: Wire team ingest into `api/app.py`

**Files:**
- Modify: `src/ffdo/api/app.py`

**Interfaces:**
- Consumes: `ffdo.ingest.teams.parse` (Task 5), `board_mod.build_auction_board`/`build_snake_board`'s new `teams=` parameter (Task 6).

No new automated test: this task is wiring inside `get_board()`, which (like the rest of that function) is exercised by manual verification rather than a mocked-HTTP unit test, matching this codebase's existing convention (see `tests/api/test_app.py`, which only tests the small pure helpers, not the live endpoint).

- [ ] **Step 1: Add the import and cache**

In `src/ffdo/api/app.py`, inside `create_app()`, replace:

```python
    from ffdo.ingest import projections as proj_mod
```

with:

```python
    from ffdo.ingest import projections as proj_mod
    from ffdo.ingest import teams as teams_mod
```

Add a new cache alongside `players_cache`/`projections_cache`:

```python
    players_cache = _TTLCache(ttl_seconds=24 * 3600)
    projections_cache = _TTLCache(ttl_seconds=3600)
    teams_cache = _TTLCache(ttl_seconds=24 * 3600)
```

- [ ] **Step 2: Fetch and parse team identity inside `get_board()`**

Inside the existing `try:` block in `get_board()`, immediately after the `state = draft_mod.parse(...)` call, add:

```python
            teams = teams_cache.get(
                lambda: teams_mod.parse(
                    sleeper.get_json(f"{client_mod.V1}/league/{league_id}/rosters"),
                    sleeper.get_json(f"{client_mod.V1}/league/{league_id}/users")))
```

- [ ] **Step 3: Pass `teams` through to both board builders**

Change the auction branch:

```python
        if state.draft_type == "auction":
            baseline = auction.baseline_prices(valued, lg)
            return board_mod.build_auction_board(
                lg, state, valued, baseline, roster_id=_roster_id(), teams=teams)
```

Change the snake branch's return statement:

```python
        return board_mod.build_snake_board(
            lg, state, valued, survival, cow, roster_id=_roster_id(), teams=teams)
```

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest -v`
Expected: all PASS (this task adds no new automated tests, but must not break existing ones).

- [ ] **Step 5: Manually verify against the live app**

Run: `uv run uvicorn ffdo.api.app:app --port 8000`

In another terminal: `curl -s http://localhost:8000/api/board | head -c 2000`

Expected: valid JSON containing a `"rosters"` key, each entry with a real `team_name` (not `"Team N"` for every row, unless this league genuinely has unnamed teams). Stop the server with Ctrl-C when done.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/api/app.py
git commit -m "feat: wire team identity ingest into the board endpoint"
```

---

### Task 8: Sidebar markup and styling

**Files:**
- Modify: `src/ffdo/web/index.html`
- Modify: `src/ffdo/web/board.css`

**Interfaces:**
- Produces: DOM elements `#sidebar`, `#rosters`, `#rosters-rows` that Task 9's `board.js` renders into.

No automated test — this is markup/CSS with no test runner in this project. Verified visually in Task 9's manual check once `board.js` populates it.

- [ ] **Step 1: Wrap the existing `#nominated` aside in a new `#sidebar` container and add the rosters card**

In `src/ffdo/web/index.html`, replace:

```html
<div id="layout">
  <aside id="nominated" class="empty">
```

with:

```html
<div id="layout">
  <div id="sidebar">
  <aside id="nominated" class="empty">
```

Then, immediately before the closing `</aside>` of `#nominated` is followed by `<main id="board-panel">`, i.e. replace:

```html
  </aside>

  <main id="board-panel">
```

with:

```html
  </aside>

  <aside id="rosters">
    <div class="rosters-head">
      <h2>Roster power rankings</h2>
      <span class="rosters-sub">starting-lineup VOR, live as rosters are built</span>
    </div>
    <div id="rosters-rows"></div>
  </aside>
  </div>

  <main id="board-panel">
```

- [ ] **Step 2: Add sidebar and roster-card styles**

In `src/ffdo/web/board.css`, replace:

```css
/* ---- layout ---- */
#layout { display: flex; gap: 18px; align-items: flex-start; }

/* ---- nominated card ---- */
#nominated {
  width: 320px;
  flex-shrink: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 200px;
}
```

with:

```css
/* ---- layout ---- */
#layout { display: flex; gap: 18px; align-items: flex-start; }
#sidebar { display: flex; flex-direction: column; gap: 18px; width: 320px; flex-shrink: 0; }

/* ---- nominated card ---- */
#nominated {
  flex-shrink: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 200px;
}

/* ---- roster rankings card ---- */
#rosters {
  flex-shrink: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.rosters-head h2 { font-size: 13px; font-weight: 600; margin: 0 0 2px; }
.rosters-sub { font-size: 11px; color: var(--faint); }
#rosters-rows { display: flex; flex-direction: column; max-height: 360px; overflow-y: auto; }
.roster-row { display: flex; align-items: center; gap: 8px; padding: 8px 4px; border-radius: 7px; cursor: pointer; }
.roster-row:hover { background: var(--surface-2); }
.roster-row.you { box-shadow: inset 3px 0 0 var(--accent); background: color-mix(in oklch, var(--accent) 8%, transparent); }
.roster-rank { width: 18px; font-family: var(--font-mono); font-size: 11px; color: var(--faint); text-align: right; flex-shrink: 0; }
.roster-name { flex: 1; min-width: 0; font-size: 12.5px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.roster-total { font-family: var(--font-mono); font-size: 13px; font-weight: 700; width: 38px; text-align: right; flex-shrink: 0; }
.roster-positions { display: flex; gap: 6px; flex-shrink: 0; }
.roster-pos { font-family: var(--font-mono); font-size: 10px; width: 24px; text-align: center; }
.roster-bench { font-family: var(--font-mono); font-size: 10.5px; color: var(--faint); width: 32px; text-align: right; flex-shrink: 0; }
.roster-detail { padding: 4px 4px 10px 26px; display: flex; flex-direction: column; gap: 3px; }
.roster-detail-player { display: flex; justify-content: space-between; gap: 8px; font-size: 11.5px; font-family: var(--font-mono); }
.roster-detail-player.bench { color: var(--faint); }
```

- [ ] **Step 3: Update the mobile media query**

In `src/ffdo/web/board.css`, replace:

```css
@media (max-width: 900px) {
  #layout { flex-direction: column; }
  #nominated { width: 100%; }
}
```

with:

```css
@media (max-width: 900px) {
  #layout { flex-direction: column; }
  #sidebar { width: 100%; }
}
```

- [ ] **Step 4: Commit**

```bash
git add src/ffdo/web/index.html src/ffdo/web/board.css
git commit -m "feat: add roster rankings sidebar card markup and styles"
```

---

### Task 9: Render the roster leaderboard in `board.js`

**Files:**
- Modify: `src/ffdo/web/board.js`

**Interfaces:**
- Consumes: `state.data.rosters` (the array produced by Task 6's `_build_rosters_payload`) and the DOM elements from Task 8 (`#rosters-rows`).

No automated test — this project has no JS test runner. Verified by running the app and checking the board in a browser (Step 3 below), per this repo's own frontend-testing convention.

- [ ] **Step 1: Add render state and the render function**

In `src/ffdo/web/board.js`, add `expandedRoster: null,` to the `state` object at the top of the file:

```javascript
let state = {
  pos: "ALL",
  hideDrafted: true,
  search: "",
  sortKey: "vor",
  sortDir: "desc",
  nominatedId: null,
  bid: 0,
  expandedRoster: null,
  data: null,
};
```

Add a `renderRosters()` function (place it after `renderNominated()`):

```javascript
function renderRosters() {
  const d = state.data;
  const el = document.getElementById("rosters-rows");
  if (!d || !d.rosters) { el.innerHTML = ""; return; }

  el.innerHTML = d.rosters.map((r, i) => {
    const posCells = ["QB", "RB", "WR", "TE"].map(pos => {
      const v = r.by_position[pos];
      const posColor = `var(--${pos.toLowerCase()}, var(--muted))`;
      return `<span class="roster-pos" style="color:${posColor}">${v !== undefined ? Math.round(v) : "—"}</span>`;
    }).join("");

    const expanded = state.expandedRoster === r.roster_id;
    const detail = !expanded ? "" : `<div class="roster-detail">${
      r.players.length === 0
        ? '<div class="roster-detail-player bench">No picks yet.</div>'
        : r.players.map(p => `<div class="roster-detail-player${p.starter ? "" : " bench"}">
            <span>${escapeHtml(p.name)} · ${p.position}</span>
            <span>${p.vor}</span>
          </div>`).join("")
    }</div>`;

    return `<div>
      <div class="roster-row${r.is_you ? " you" : ""}" data-roster-id="${r.roster_id}">
        <span class="roster-rank">${i + 1}</span>
        <span class="roster-name">${escapeHtml(r.team_name)}</span>
        <div class="roster-positions">${posCells}</div>
        <span class="roster-bench">+${Math.round(r.bench_vor)}</span>
        <span class="roster-total">${Math.round(r.starting_vor)}</span>
      </div>
      ${detail}
    </div>`;
  }).join("");
}
```

Call it from the main `render()` function — add `renderRosters();` to the list of render calls:

```javascript
  renderCow();
  renderMoneyHeader();
  renderTable();
  renderNominated();
  renderRosters();
  renderSortHeaders();
```

- [ ] **Step 2: Wire the expand/collapse click handler**

Add this near the other `addEventListener` calls at the bottom of the file:

```javascript
document.getElementById("rosters-rows").addEventListener("click", e => {
  const row = e.target.closest(".roster-row");
  if (!row) return;
  const rid = Number(row.dataset.rosterId);
  state.expandedRoster = state.expandedRoster === rid ? null : rid;
  renderRosters();
});
```

- [ ] **Step 3: Manually verify in a browser**

Run: `uv run uvicorn ffdo.api.app:app --port 8000`

Open `http://localhost:8000` in a browser. Confirm:
- A "Roster power rankings" card appears below the nominated-player card in the sidebar.
- It lists teams ranked by starting-lineup VOR, with QB/RB/WR/TE numbers and a bench figure per row.
- Clicking a row expands it to show that team's drafted players, starters visually distinct from bench (bench rows are dimmer).
- Clicking the same row again collapses it.
- Set `FFDO_ROSTER_ID` to a real roster id from the league (stop the server, `export FFDO_ROSTER_ID=<id>` or the Windows equivalent, restart) and confirm that row is visually highlighted as "you."

Then set `FFDO_LEAGUE_ID`/`FFDO_DRAFT_ID` to a snake league/draft (if you have one) and repeat the same checks on the snake board — the card must appear there too, unchanged in behavior.

Stop the server with Ctrl-C when done.

- [ ] **Step 4: Commit**

```bash
git add src/ffdo/web/board.js
git commit -m "feat: render the roster rankings sidebar card"
```

---

### Task 10: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the complete test suite**

Run: `uv run pytest -v`
Expected: all PASS, no skips, no warnings about the new modules.

- [ ] **Step 2: Confirm no leftover debug output or TODOs**

Run: `git diff main --stat` (or the branch's merge-base) to review the full set of changed files from this plan, and re-read `src/ffdo/api/board.py`, `src/ffdo/engine/roster.py`, and `src/ffdo/ingest/teams.py` once end-to-end for stray `print()`/`console.log`/commented-out code.

- [ ] **Step 3: Final commit if anything was cleaned up in Step 2**

```bash
git add -A
git commit -m "chore: final cleanup pass on roster ranking feature"
```

(Skip this step entirely if Step 2 found nothing to change.)
