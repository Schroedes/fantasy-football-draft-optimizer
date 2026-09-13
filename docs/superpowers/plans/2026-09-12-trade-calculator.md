# Trade Calculator + Decision Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user evaluate a hypothetical trade (players + draft picks, valued on the same scale #4 already established) and see a permanent, continuously-updating decision ledger of every real trade in a tracked league.

**Architecture:** A new `engine/pick_value.py` gives a draft pick a VOR-equivalent value, fit from the user's own dynasty leagues' real rookie-draft history (`scripts/fit_pick_value.py` → `domain/constants.py::PICK_VALUE_CURVE`, mirroring #4's age-curve pattern exactly). A new `engine/trade_value.py` sums player VOR (reused unchanged from #4) plus pick value for both sides of a trade. A new `ingest/sleeper/transactions.py` detects real completed trades; a new `api/trade_ledger.py` (SQLite, mirrors `lineup_ledger.py`) records them permanently and recomputes their current value on every read. Two new endpoints in `api/app.py`; one new "Trades" tab in `season.js`.

**Scope note:** the `POST /trade/evaluate` endpoint (Task 8) is fully built and tested — the hypothetical evaluator's entire backend ships in this plan. Its interactive frontend (picking players/picks from your roster and a partner's, live value as you build) is real, multi-piece UI work on its own; Task 9 ships the ledger VIEW only for this plan's cut, and the builder UI is called out explicitly as the next fast-follow rather than silently dropped (spec §10 candidate, though not originally listed there — added here since it only became clear during plan-writing how much UI work the builder alone is).

**Tech Stack:** Python 3.12, FastAPI, pytest, SQLite (stdlib), vanilla JS (no framework) — unchanged from the rest of this codebase.

**Spec:** `docs/superpowers/specs/2026-09-12-trade-calculator-design.md`

## Global Constraints

- Phase 1 only: hypothetical trade evaluator + real-trade decision ledger. No trade-target suggestions, no roster-context-aware fairness, no qualitative "fair/lopsided" verdict (spec §2).
- `MIN_SAMPLE = 3` observations required before a pick-value tier (exact slot, then tertile, then round average) is trusted — a controller judgment call, not backtested (spec §4.2).
- `PICK_UNCERTAINTY_DISCOUNT_RATE = 0.20` per year, a separate constant from #4's `dynasty_value.DISCOUNT_RATE` (0.15) — different source of uncertainty, not backtested (spec §4.3).
- The trade evaluator's `side_a` is always the tracked user's own roster — never opened to arbitrary two-team hypotheticals (spec §7, decided during brainstorming).
- The ledger shows every trade in the league, chronological (not a ranked leaderboard) — decided during brainstorming, spec §6.4.
- "Current value" is continuously recomputed on every read, never a one-time "resolved" event: players via banked-points-since-trade (a raw point differential, NOT re-derived VOR — a stated scope simplification, see Task 7); picks via re-running `pick_value.slot_value` with today's inputs (spec §6.3).
- Every task's tests must include at least one check of real magnitude against real fitted data, not only direction/bounds against a synthetic fixture — #4's post-mortem: two real formula bugs shipped because no per-task test checked magnitude against real curve values, only direction.
- `roster_value`, `ValuedPlayer`, `DraftPickAsset`, `traded_picks.capital()` are reused unchanged — this plan only ADDS to the valuation surface, never modifies #4's frozen interfaces.

---

### Task 1: `ingest/sleeper/rookie_drafts.py` — real rookie-draft history

**Files:**
- Create: `src/ffdo/ingest/sleeper/rookie_drafts.py`
- Test: `tests/ingest/sleeper/test_rookie_drafts.py`

**Interfaces:**
- Consumes: `ffdo.ingest.client.{V1, SleeperClient}` (existing), `ffdo.ingest.draft.parse` (existing, unmodified — `draft.parse(meta, picks) -> DraftState`).
- Produces: `RookiePick(season: int, round: int, pick_in_round: int, player_id: str)` (new frozen dataclass), `league_seasons(sleeper, league_id, *, max_seasons=8) -> list[str]`, `rookie_picks(sleeper, league_id) -> list[RookiePick]` — both consumed by Task 2's fitting script only, never called from the live app path.

- [ ] **Step 1: Write the failing tests**

```python
# tests/ingest/sleeper/test_rookie_drafts.py
from ffdo.ingest.sleeper import rookie_drafts


class _FakeClient:
    def __init__(self, responses):
        self._responses = responses

    def get_json(self, url):
        for key, val in self._responses.items():
            if key in url:
                return val
        raise AssertionError(f"unexpected URL: {url}")


def test_league_seasons_walks_the_previous_league_id_chain():
    client = _FakeClient({
        "/league/L3": {"previous_league_id": "L2"},
        "/league/L2": {"previous_league_id": "L1"},
        "/league/L1": {"previous_league_id": None},
    })
    assert rookie_drafts.league_seasons(client, "L3") == ["L3", "L2", "L1"]


def test_league_seasons_stops_at_max_seasons():
    client = _FakeClient({
        "/league/L3": {"previous_league_id": "L2"},
        "/league/L2": {"previous_league_id": "L1"},
        "/league/L1": {"previous_league_id": "L0"},
    })
    assert rookie_drafts.league_seasons(client, "L3", max_seasons=2) == ["L3", "L2"]


def test_rookie_picks_only_reads_completed_linear_drafts():
    client = _FakeClient({
        "/league/L1/drafts": [
            {"draft_id": "d1", "type": "linear", "status": "complete",
             "season": "2025", "settings": {"rounds": 3}, "metadata": {}},
            {"draft_id": "d2", "type": "snake", "status": "complete",
             "season": "2024", "settings": {"rounds": 15}, "metadata": {}},
            {"draft_id": "d3", "type": "linear", "status": "in_progress",
             "season": "2026", "settings": {"rounds": 3}, "metadata": {}},
        ],
        "/draft/d1/picks": [
            {"pick_no": 1, "round": 1, "draft_slot": 1, "roster_id": 5,
             "picked_by": "u1", "player_id": "p1", "metadata": {}},
            {"pick_no": 2, "round": 1, "draft_slot": 2, "roster_id": 7,
             "picked_by": "u2", "player_id": "p2", "metadata": {}},
        ],
    })
    picks = rookie_drafts.rookie_picks(client, "L1")
    assert picks == [
        rookie_drafts.RookiePick(season=2025, round=1, pick_in_round=1, player_id="p1"),
        rookie_drafts.RookiePick(season=2025, round=1, pick_in_round=2, player_id="p2"),
    ]


def test_rookie_picks_returns_empty_for_a_league_with_no_rookie_drafts_yet():
    client = _FakeClient({
        "/league/L1/drafts": [
            {"draft_id": "d1", "type": "auction", "status": "complete",
             "season": "2024", "settings": {"rounds": 20}, "metadata": {}},
        ],
    })
    assert rookie_drafts.rookie_picks(client, "L1") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/sleeper/test_rookie_drafts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.ingest.sleeper.rookie_drafts'`

- [ ] **Step 3: Write the implementation**

```python
# src/ffdo/ingest/sleeper/rookie_drafts.py
"""Real fantasy rookie-draft history for a Sleeper dynasty/keeper league,
walked back through `previous_league_id`. Used ONLY by
scripts/fit_pick_value.py to fit PICK_VALUE_CURVE from real slot-to-
player-to-production data -- never called from the live app path (the
live app only ever reads the already-fitted, checked-in constant).
"""

from __future__ import annotations

from dataclasses import dataclass

from ffdo.ingest import draft as draft_mod
from ffdo.ingest.client import V1, SleeperClient

# A rookie draft is `linear` (not `snake`, not `auction`) -- this
# distinguishes an annual rookie-only draft from the one-time startup
# draft. Confirmed live against real leagues during brainstorming: both of
# the user's tracked dynasty leagues (GDK, Room Temp IQ) run a one-time
# snake/auction startup draft followed by annual linear rookie drafts.
_ROOKIE_DRAFT_TYPE = "linear"


@dataclass(frozen=True, slots=True)
class RookiePick:
    season: int
    round: int
    # Sleeper's `draft_slot`. For a LINEAR draft this equals the pick's
    # position within its round directly -- unlike a snake draft, a linear
    # draft never reverses direction between rounds, so no snake-order
    # math is needed to turn draft_slot into "pick within round."
    pick_in_round: int
    player_id: str


def league_seasons(sleeper: SleeperClient, league_id: str, *, max_seasons: int = 8) -> list[str]:
    """Walks `previous_league_id` back from `league_id`. Returns every
    league_id in the chain, this season first, oldest last. Stops at
    `max_seasons` links or the first missing/absent `previous_league_id`,
    whichever comes first -- bounds the walk for a very long-running
    league."""
    chain = [league_id]
    current = league_id
    for _ in range(max_seasons - 1):
        raw = sleeper.get_json(f"{V1}/league/{current}")
        previous = raw.get("previous_league_id")
        if not previous:
            break
        chain.append(previous)
        current = previous
    return chain


def rookie_picks(sleeper: SleeperClient, league_id: str) -> list[RookiePick]:
    """Every real pick from every completed rookie (linear-type) draft in
    THIS ONE league (not its previous-season chain -- callers walk the
    chain themselves via `league_seasons` and call this once per link)."""
    drafts_raw = sleeper.get_json(f"{V1}/league/{league_id}/drafts")
    out: list[RookiePick] = []
    for entry in drafts_raw:
        if entry.get("type") != _ROOKIE_DRAFT_TYPE or entry.get("status") != "complete":
            continue
        draft_id = entry["draft_id"]
        season = int(entry["season"])
        picks_raw = sleeper.get_json(f"{V1}/draft/{draft_id}/picks")
        state = draft_mod.parse(entry, picks_raw)
        for pick in state.picks:
            out.append(RookiePick(
                season=season, round=pick.round,
                pick_in_round=pick.draft_slot, player_id=pick.player_id))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/sleeper/test_rookie_drafts.py -v`
Expected: PASS, all 4 tests

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/ingest/sleeper/rookie_drafts.py tests/ingest/sleeper/test_rookie_drafts.py
git commit -m "feat: ingest.sleeper.rookie_drafts -- real fantasy rookie-draft history"
```

---

### Task 2: `scripts/fit_pick_value.py` — fit and run for real

**Files:**
- Create: `scripts/fit_pick_value.py`

**Interfaces:**
- Consumes: `rookie_drafts.{league_seasons, rookie_picks, RookiePick}` (Task 1), `ffdo.ingest.{snapshot, players, stats}` (existing, unmodified), `ffdo.engine.scoring.score_stats` (existing), `ffdo.engine.vor.compute` (existing), `ffdo.domain.constants.STANDARD_HALF_PPR` (existing), `ffdo.domain.models.LeagueProfile` (existing).
- Produces: a printed Python literal for `PICK_VALUE_CURVE`, structured `{round: {"exact": {pick_in_round: value}, "early"/"mid"/"late": value, "round_avg": value}}` -- reviewed by a human, never auto-written (same posture as `fit_age_curve.py`).

This task has no unit tests of its own -- like `fit_age_curve.py`, it is a
one-off script whose correctness is judged by running it for real and
sanity-checking the printed output, not by a pytest file.

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python
"""Fits PICK_VALUE_CURVE from the user's own tracked dynasty leagues' real
rookie-draft history -- prints the result as a Python literal for a human
to review and paste into domain/constants.py by hand. Deliberately does
NOT write the file itself, same reasoning as fit_age_curve.py.

Usage: uv run python scripts/fit_pick_value.py <snapshot-dir> <league-id> [<league-id> ...]

<league-id> is a Sleeper league_id for the CURRENT season of a tracked
dynasty/keeper league (e.g. GDK's or Room Temp IQ's current league_id) --
this script walks each one's own previous_league_id chain internally, so
pass only the current-season id per league, not every historical id.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ffdo.domain.constants import STANDARD_HALF_PPR
from ffdo.domain.models import LeagueProfile
from ffdo.engine import vor
from ffdo.engine.scoring import score_stats
from ffdo.ingest import players as players_mod
from ffdo.ingest import snapshot
from ffdo.ingest import stats as stats_mod
from ffdo.ingest.client import SleeperClient
from ffdo.ingest.sleeper import rookie_drafts

MIN_SAMPLE = 3  # controller judgment call -- see the plan's Global Constraints

_REFERENCE_ROSTER = (
    "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX",
    "BN", "BN", "BN", "BN", "BN", "BN", "BN",
)


def _tertile(pick_in_round: int, round_size: int) -> str:
    third = max(1, round(round_size / 3))
    if pick_in_round <= third:
        return "early"
    if pick_in_round <= 2 * third:
        return "mid"
    return "late"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fit PICK_VALUE_CURVE from real rookie-draft history")
    parser.add_argument("snapshot_dir", type=Path)
    parser.add_argument("league_ids", nargs="+")
    args = parser.parse_args()

    all_profiles = players_mod.parse(snapshot.load("players_nfl", args.snapshot_dir))

    sleeper = SleeperClient()
    try:
        all_picks: list[rookie_drafts.RookiePick] = []
        round_sizes: dict[int, int] = {}   # draft season -> num teams, from Task 1's raw fetch
        for league_id in args.league_ids:
            chain = rookie_drafts.league_seasons(sleeper, league_id)
            for link in chain:
                picks = rookie_drafts.rookie_picks(sleeper, link)
                all_picks.extend(picks)
                for p in picks:
                    round_sizes[p.season] = max(round_sizes.get(p.season, 0), p.pick_in_round)
    finally:
        sleeper.close()

    if not all_picks:
        print("No completed rookie drafts found for the given leagues.", file=sys.stderr)
        sys.exit(1)

    seasons_needed = sorted({p.season for p in all_picks})
    points_by_season: dict[int, dict[str, float]] = {}
    for season in seasons_needed:
        stat_lines = stats_mod.parse(snapshot.load(f"stats_{season}", args.snapshot_dir), season)
        points_by_season[season] = {
            pid: score_stats(line.stats, STANDARD_HALF_PPR)
            for pid, line in stat_lines.items()
        }

    # (round, pick_in_round) -> list of realized VOR values
    exact: dict[tuple[int, int], list[float]] = {}
    for pick in all_picks:
        season_points = points_by_season.get(pick.season, {})
        if pick.player_id not in season_points:
            continue  # no production data for this player that season -- skip, not zero
        reference = LeagueProfile(
            league_id="reference", season=pick.season, num_teams=12,
            roster_positions=_REFERENCE_ROSTER, scoring_settings=STANDARD_HALF_PPR,
            budget=None)
        valued = vor.compute(season_points, all_profiles, reference)
        vp = valued.get(pick.player_id)
        if vp is None:
            continue  # e.g. a position this reference league doesn't start
        exact.setdefault((pick.round, pick.pick_in_round), []).append(vp.vor)

    curve: dict[int, dict] = {}
    for rnd in sorted({r for r, _ in exact}):
        round_size = max(round_sizes.values()) if round_sizes else 12
        round_entries = {pn: vals for (r, pn), vals in exact.items() if r == rnd}
        round_all = [v for vals in round_entries.values() for v in vals]
        tiers: dict = {"exact": {}}
        for pn, vals in round_entries.items():
            if len(vals) >= MIN_SAMPLE:
                tiers["exact"][pn] = round(sum(vals) / len(vals), 2)
        for label in ("early", "mid", "late"):
            tertile_vals = [
                v for (r, pn), vals in exact.items() if r == rnd
                and _tertile(pn, round_size) == label for v in vals
            ]
            if len(tertile_vals) >= MIN_SAMPLE:
                tiers[label] = round(sum(tertile_vals) / len(tertile_vals), 2)
        if round_all:
            tiers["round_avg"] = round(sum(round_all) / len(round_all), 2)
        curve[rnd] = tiers

    print(f"# Fit from {args.snapshot_dir.name}, leagues {args.league_ids}, "
          f"seasons {seasons_needed}, MIN_SAMPLE={MIN_SAMPLE}")
    print("PICK_VALUE_CURVE: Final[dict[int, dict]] = {")
    for rnd in sorted(curve):
        print(f"    {rnd}: {curve[rnd]!r},")
    print("}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it for real**

Run against the user's real tracked dynasty leagues (GDK: `1312210128811872256`,
Room Temp IQ: `1312145369592766464` -- the current-season league_ids, confirmed
live during brainstorming to each have real completed linear rookie drafts
reachable via their `previous_league_id` chains):

```bash
uv run python scripts/fit_pick_value.py data/snapshots/2026-09-12 1312210128811872256 1312145369592766464
```

- [ ] **Step 3: Sanity-check the output**

Confirm: every printed value is a plausible VOR magnitude (roughly in the
same range as `ValuedPlayer.vor` values already visible elsewhere in the
app, not wildly larger/smaller); round 1 values are higher than round 2+
values for the same tier; no `NaN`/`inf`. If a round's `early`/`mid`/`late`
tiers ended up populated but produced a NON-monotonic ordering (e.g. `mid`
higher than `early`) with only `MIN_SAMPLE`-sized samples, that is a real
and expected consequence of this sample's small size -- do not hand-adjust
the printed numbers; the whole point of the exact→tertile→round-average
fallback is that a small sample gets averaged into a coarser, more stable
bucket, not silently corrected.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions (this task adds no tests of its own)

- [ ] **Step 5: Commit**

```bash
git add scripts/fit_pick_value.py
git commit -m "feat: scripts/fit_pick_value.py -- fit and print the pick-value curve for review"
```

---

### Task 3: `domain/constants.py::PICK_VALUE_CURVE`

**Files:**
- Modify: `src/ffdo/domain/constants.py`
- Test: `tests/domain/test_constants.py` (append to the existing file)

**Interfaces:**
- Consumes: Task 2's real printed output (pasted in verbatim).
- Produces: `PICK_VALUE_CURVE: Final[dict[int, dict]]`, consumed by Task 4's `pick_value.slot_value`.

- [ ] **Step 1: Write the failing tests**

```python
# appended to tests/domain/test_constants.py
from ffdo.domain.constants import PICK_VALUE_CURVE


def test_pick_value_curve_every_round_has_a_round_avg_fallback():
    for rnd, tiers in PICK_VALUE_CURVE.items():
        assert "round_avg" in tiers, f"round {rnd} has no round_avg fallback"


def test_pick_value_curve_values_are_plausible_vor_magnitudes():
    for tiers in PICK_VALUE_CURVE.values():
        for key, val in tiers.items():
            if key == "exact":
                for v in val.values():
                    assert -50.0 < v < 400.0
            else:
                assert -50.0 < val < 400.0


def test_pick_value_curve_rounds_are_positive_integers():
    for rnd in PICK_VALUE_CURVE:
        assert isinstance(rnd, int) and rnd >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/domain/test_constants.py -v -k pick_value_curve`
Expected: FAIL with `ImportError: cannot import name 'PICK_VALUE_CURVE'`

- [ ] **Step 3: Paste in the real constant**

In `src/ffdo/domain/constants.py`, near `DYNASTY_AGE_CURVE` (same file,
adjacent section -- both are fitted-from-real-data valuation curves), add:

```python
# Fit from Task 2's real run -- paste the EXACT printed output here,
# including its leading comment line recording which leagues/seasons/
# MIN_SAMPLE it was fit from. Do not hand-edit the pasted values.
PICK_VALUE_CURVE: Final[dict[int, dict]] = {
    # <-- paste Task 2's real printed dict body here -->
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/domain/test_constants.py -v -k pick_value_curve`
Expected: PASS, all 3 tests

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/domain/constants.py tests/domain/test_constants.py
git commit -m "feat: PICK_VALUE_CURVE -- real, data-fit draft pick valuation"
```

---

### Task 4: `engine/pick_value.py`

**Files:**
- Create: `src/ffdo/engine/pick_value.py`
- Test: `tests/engine/test_pick_value.py`

**Interfaces:**
- Consumes: `ffdo.domain.models.DraftPickAsset` (existing, unmodified), `PICK_VALUE_CURVE`'s shape (Task 3, but tests use synthetic curves, never the real constant, except one test per the Global Constraints' real-magnitude requirement).
- Produces: `PICK_UNCERTAINTY_DISCOUNT_RATE: Final[float]`, `slot_value(pick: DraftPickAsset, curve: Mapping, *, current_season: int, round_size: int, discount_rate: float = PICK_UNCERTAINTY_DISCOUNT_RATE) -> float` -- consumed by Task 5's `trade_value.evaluate_trade`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_pick_value.py
import pytest

from ffdo.domain.constants import PICK_VALUE_CURVE
from ffdo.domain.models import DraftPickAsset
from ffdo.engine import pick_value


def _pick(season=2026, round=1, projected_slot=None):
    return DraftPickAsset(
        season=season, round=round, projected_slot=projected_slot,
        current_owner_roster_id=1, original_roster_id=1, via_team_name=None)


def test_exact_slot_used_when_present():
    curve = {1: {"exact": {3: 100.0}, "round_avg": 40.0}}
    pick = _pick(projected_slot=3)
    result = pick_value.slot_value(pick, curve, current_season=2026, round_size=12,
                                    discount_rate=0.0)
    assert result == pytest.approx(100.0)


def test_falls_back_to_tertile_when_exact_slot_missing():
    curve = {1: {"exact": {}, "early": 90.0, "round_avg": 40.0}}
    pick = _pick(projected_slot=2)   # early tertile of a 12-pick round
    result = pick_value.slot_value(pick, curve, current_season=2026, round_size=12,
                                    discount_rate=0.0)
    assert result == pytest.approx(90.0)


def test_falls_back_to_round_avg_when_tertile_missing():
    curve = {1: {"exact": {}, "round_avg": 40.0}}
    pick = _pick(projected_slot=2)
    result = pick_value.slot_value(pick, curve, current_season=2026, round_size=12,
                                    discount_rate=0.0)
    assert result == pytest.approx(40.0)


def test_no_projected_slot_falls_straight_to_round_avg():
    curve = {1: {"exact": {2: 999.0}, "round_avg": 40.0}}
    pick = _pick(projected_slot=None)
    result = pick_value.slot_value(pick, curve, current_season=2026, round_size=12,
                                    discount_rate=0.0)
    assert result == pytest.approx(40.0)


def test_missing_round_returns_zero():
    curve = {1: {"round_avg": 40.0}}
    pick = _pick(round=5, projected_slot=1)
    result = pick_value.slot_value(pick, curve, current_season=2026, round_size=12,
                                    discount_rate=0.0)
    assert result == 0.0


def test_years_out_zero_applies_no_discount():
    curve = {1: {"exact": {1: 100.0}}}
    pick = _pick(season=2026, projected_slot=1)
    result = pick_value.slot_value(pick, curve, current_season=2026, round_size=12,
                                    discount_rate=0.20)
    assert result == pytest.approx(100.0)


def test_years_out_discounts_correctly():
    curve = {1: {"exact": {1: 100.0}}}
    pick = _pick(season=2028, projected_slot=1)   # 2 years out from current_season=2026
    result = pick_value.slot_value(pick, curve, current_season=2026, round_size=12,
                                    discount_rate=0.20)
    assert result == pytest.approx(100.0 / (1.2 ** 2))


def test_tertile_boundaries_scale_to_round_size():
    # A 9-pick round splits into thirds of 3: early=1-3, mid=4-6, late=7-9.
    curve = {1: {"exact": {}, "mid": 55.0, "round_avg": 20.0}}
    pick = _pick(projected_slot=5)
    result = pick_value.slot_value(pick, curve, current_season=2026, round_size=9,
                                    discount_rate=0.0)
    assert result == pytest.approx(55.0)


def test_real_curve_round_one_exceeds_round_three():
    """Real-magnitude check against the actual fitted curve (Global
    Constraints: at least one test per module must check real magnitude,
    not just direction against a synthetic fixture -- the exact regression
    class that let two bugs into #4's shipped formula)."""
    if 1 not in PICK_VALUE_CURVE or 3 not in PICK_VALUE_CURVE:
        pytest.skip("real curve does not have both round 1 and round 3 data yet")
    round_1_avg = PICK_VALUE_CURVE[1]["round_avg"]
    round_3_avg = PICK_VALUE_CURVE[3]["round_avg"]
    assert round_1_avg > round_3_avg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_pick_value.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.engine.pick_value'`

- [ ] **Step 3: Write the implementation**

```python
# src/ffdo/engine/pick_value.py
"""Gives a draft pick a VOR-equivalent value, fit from real rookie-draft
history (see scripts/fit_pick_value.py, domain/constants.py::PICK_VALUE_CURVE).
The swappable seam for sub-project #5, the way engine/dynasty_value.py was
for #4."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from ffdo.domain.models import DraftPickAsset

PICK_UNCERTAINTY_DISCOUNT_RATE: Final[float] = 0.20  # per year; a separate,
    # larger discount than dynasty_value.DISCOUNT_RATE (0.15) -- that
    # discounts a KNOWN player's future production, this discounts not
    # knowing who the pick even becomes yet. Not backtested (no ground
    # truth exists for "value assigned at trade time" vs. "value once
    # realized"), same honest posture as DISCOUNT_RATE.


def _tertile(pick_in_round: int, round_size: int) -> str:
    third = max(1, round(round_size / 3))
    if pick_in_round <= third:
        return "early"
    if pick_in_round <= 2 * third:
        return "mid"
    return "late"


def slot_value(
    pick: DraftPickAsset,
    curve: Mapping[int, Mapping],
    *,
    current_season: int,
    round_size: int,
    discount_rate: float = PICK_UNCERTAINTY_DISCOUNT_RATE,
) -> float:
    round_data = curve.get(pick.round, {})
    base = None
    if pick.projected_slot is not None:
        base = round_data.get("exact", {}).get(pick.projected_slot)
        if base is None:
            tertile = _tertile(pick.projected_slot, round_size)
            base = round_data.get(tertile)
    if base is None:
        base = round_data.get("round_avg", 0.0)
    years_out = int(pick.season) - current_season
    return base / (1.0 + discount_rate) ** max(0, years_out)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_pick_value.py -v`
Expected: PASS, all 9 tests (or 8 + 1 skip if Task 3's real curve doesn't
yet have both round 1 and round 3 populated -- acceptable, not a failure)

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/engine/pick_value.py tests/engine/test_pick_value.py
git commit -m "feat: engine.pick_value.slot_value -- VOR-equivalent draft pick valuation"
```

---

### Task 5: `engine/trade_value.py`

**Files:**
- Create: `src/ffdo/engine/trade_value.py`
- Modify: `src/ffdo/domain/models.py` (add `TradeEvaluation`)
- Test: `tests/engine/test_trade_value.py`

**Interfaces:**
- Consumes: `ValuedPlayer.vor` (existing, unmodified), `pick_value.slot_value` (Task 4), `DraftPickAsset` (existing).
- Produces: `domain.models.TradeEvaluation` (new), `trade_value.evaluate_trade(side_a, side_b, *, valued_players, pick_curve, current_season, round_size) -> TradeEvaluation` -- consumed by Task 8's API wiring.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_trade_value.py
import pytest

from ffdo.domain.models import DraftPickAsset, PlayerProfile, ValuedPlayer
from ffdo.engine import trade_value


def _profile(pid, pos="RB"):
    return PlayerProfile(player_id=pid, first_name="A", last_name="B",
                         position=pos, team="X", age=25, years_exp=3,
                         injury_status=None, active=True)


def _valued(pid, vor):
    return ValuedPlayer(profile=_profile(pid), projected_points=vor + 50.0,
                        adjusted_points=vor + 50.0, vor=vor, tier=1, adjustments={})


def _pick(season=2026, round=1, slot=1):
    return DraftPickAsset(season=season, round=round, projected_slot=slot,
                          current_owner_roster_id=1, original_roster_id=1,
                          via_team_name=None)


def test_side_values_sum_players_and_picks():
    valued = {"p1": _valued("p1", 40.0), "p2": _valued("p2", 10.0)}
    curve = {1: {"exact": {1: 60.0}}}
    side_a = {"player_ids": ["p1"], "picks": []}
    side_b = {"player_ids": ["p2"], "picks": [_pick()]}
    result = trade_value.evaluate_trade(
        side_a, side_b, valued_players=valued, pick_curve=curve,
        current_season=2026, round_size=12)
    assert result.side_a_value == pytest.approx(40.0)
    assert result.side_b_value == pytest.approx(70.0)   # 10.0 + 60.0
    assert result.differential == pytest.approx(-30.0)


def test_differential_pct_is_none_when_a_side_is_zero():
    valued = {"p1": _valued("p1", 0.0), "p2": _valued("p2", 10.0)}
    side_a = {"player_ids": ["p1"], "picks": []}
    side_b = {"player_ids": ["p2"], "picks": []}
    result = trade_value.evaluate_trade(
        side_a, side_b, valued_players=valued, pick_curve={},
        current_season=2026, round_size=12)
    assert result.differential_pct is None


def test_differential_pct_computed_against_the_smaller_side():
    valued = {"p1": _valued("p1", 100.0), "p2": _valued("p2", 50.0)}
    side_a = {"player_ids": ["p1"], "picks": []}
    side_b = {"player_ids": ["p2"], "picks": []}
    result = trade_value.evaluate_trade(
        side_a, side_b, valued_players=valued, pick_curve={},
        current_season=2026, round_size=12)
    assert result.differential_pct == pytest.approx(1.0)   # 50 / 50


def test_unknown_player_id_is_silently_skipped():
    valued = {"p1": _valued("p1", 40.0)}
    side_a = {"player_ids": ["p1", "ghost"], "picks": []}
    side_b = {"player_ids": [], "picks": []}
    result = trade_value.evaluate_trade(
        side_a, side_b, valued_players=valued, pick_curve={},
        current_season=2026, round_size=12)
    assert result.side_a_value == pytest.approx(40.0)


def test_redraft_trade_with_no_picks_works():
    valued = {"p1": _valued("p1", 20.0), "p2": _valued("p2", 20.0)}
    side_a = {"player_ids": ["p1"], "picks": []}
    side_b = {"player_ids": ["p2"], "picks": []}
    result = trade_value.evaluate_trade(
        side_a, side_b, valued_players=valued, pick_curve={},
        current_season=2026, round_size=12)
    assert result.differential == pytest.approx(0.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_trade_value.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.engine.trade_value'`

- [ ] **Step 3: Add `TradeEvaluation` to `domain/models.py`**

Append to `src/ffdo/domain/models.py`:

```python
@dataclass(frozen=True, slots=True)
class TradeEvaluation:
    side_a_value: float
    side_b_value: float
    differential: float          # side_a_value - side_b_value
    differential_pct: float | None   # None when either side's value is 0.0
```

- [ ] **Step 4: Write the implementation**

```python
# src/ffdo/engine/trade_value.py
"""Sums player VOR (unchanged from #4) plus draft-pick value (new, see
engine/pick_value.py) for both sides of a hypothetical trade. Deliberately
produces raw numbers only -- no fairness verdict, no roster-context
awareness (spec §2, §5): those need the roster-needs modeling a future
sub-project will build for trade-target suggestions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ffdo.domain.models import DraftPickAsset, TradeEvaluation, ValuedPlayer
from ffdo.engine import pick_value


def _side_value(
    side: Mapping[str, Sequence],
    valued_players: Mapping[str, ValuedPlayer],
    pick_curve: Mapping[int, Mapping],
    *,
    current_season: int,
    round_size: int,
) -> float:
    player_total = sum(
        valued_players[pid].vor for pid in side.get("player_ids", ())
        if pid in valued_players)
    pick_total = sum(
        pick_value.slot_value(p, pick_curve, current_season=current_season,
                              round_size=round_size)
        for p in side.get("picks", ()))
    return player_total + pick_total


def evaluate_trade(
    side_a: Mapping[str, Sequence],
    side_b: Mapping[str, Sequence],
    *,
    valued_players: Mapping[str, ValuedPlayer],
    pick_curve: Mapping[int, Mapping],
    current_season: int,
    round_size: int,
) -> TradeEvaluation:
    a_value = _side_value(side_a, valued_players, pick_curve,
                          current_season=current_season, round_size=round_size)
    b_value = _side_value(side_b, valued_players, pick_curve,
                          current_season=current_season, round_size=round_size)
    differential = a_value - b_value
    smaller = min(a_value, b_value)
    differential_pct = (differential / smaller) if smaller != 0.0 else None
    return TradeEvaluation(
        side_a_value=a_value, side_b_value=b_value,
        differential=differential, differential_pct=differential_pct)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_trade_value.py -v`
Expected: PASS, all 5 tests

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/engine/trade_value.py src/ffdo/domain/models.py tests/engine/test_trade_value.py
git commit -m "feat: engine.trade_value.evaluate_trade -- hypothetical trade value comparison"
```

---

### Task 6: `ingest/sleeper/transactions.py` — real trade detection

**Files:**
- Create: `src/ffdo/ingest/sleeper/transactions.py`
- Modify: `src/ffdo/domain/models.py` (add `TradeTransaction`)
- Test: `tests/ingest/sleeper/test_transactions.py`

**Interfaces:**
- Consumes: `ffdo.ingest.client.{V1, SleeperClient}` (existing).
- Produces: `domain.models.TradeTransaction` (new), `fetch_trades(sleeper, league_id, *, season, through_week) -> list[TradeTransaction]` -- consumed by Task 7's ledger.

- [ ] **Step 1: Write the failing tests**

```python
# tests/ingest/sleeper/test_transactions.py
from ffdo.domain.models import DraftPickAsset
from ffdo.ingest.sleeper import transactions


class _FakeClient:
    def __init__(self, by_week):
        self._by_week = by_week

    def get_json(self, url):
        for week, payload in self._by_week.items():
            if f"/transactions/{week}" in url:
                return payload
        raise AssertionError(f"unexpected URL: {url}")


def test_fetch_trades_filters_to_completed_trades_only():
    client = _FakeClient({
        1: [
            {"type": "trade", "status": "complete", "transaction_id": "t1",
             "roster_ids": [2, 3], "adds": {"p1": 2}, "drops": {"p1": 3},
             "draft_picks": [], "created": 1000},
            {"type": "waiver", "status": "complete", "transaction_id": "w1",
             "roster_ids": [2], "adds": {"p9": 2}, "drops": {}, "created": 900},
            {"type": "trade", "status": "pending", "transaction_id": "t2",
             "roster_ids": [4, 5], "adds": {}, "drops": {}, "created": 1100},
        ],
    })
    result = transactions.fetch_trades(client, "L1", season=2026, through_week=1)
    assert len(result) == 1
    assert result[0].transaction_id == "t1"


def test_fetch_trades_splits_adds_by_roster_into_both_directions():
    client = _FakeClient({
        1: [{"type": "trade", "status": "complete", "transaction_id": "t1",
             "roster_ids": [2, 3],
             "adds": {"p1": 2, "p2": 3}, "drops": {"p1": 3, "p2": 2},
             "draft_picks": [], "created": 1000}],
    })
    result = transactions.fetch_trades(client, "L1", season=2026, through_week=1)
    trade = result[0]
    assert trade.roster_a_id == 2
    assert trade.roster_b_id == 3
    assert trade.roster_a_gets == ["p1"]
    assert trade.roster_b_gets == ["p2"]


def test_fetch_trades_parses_draft_picks_by_new_owner():
    client = _FakeClient({
        1: [{"type": "trade", "status": "complete", "transaction_id": "t1",
             "roster_ids": [2, 3], "adds": {}, "drops": {},
             "draft_picks": [{"round": 1, "season": "2027", "roster_id": 2,
                             "owner_id": 2, "previous_owner_id": 3}],
             "created": 1000}],
    })
    result = transactions.fetch_trades(client, "L1", season=2026, through_week=1)
    trade = result[0]
    assert trade.picks_to_a == []
    assert len(trade.picks_to_b) == 0
    # owner_id 2 == roster_a_id (first of sorted roster_ids) -- picks_to_a
    assert trade.picks_to_a == [
        DraftPickAsset(season=2027, round=1, projected_slot=None,
                       current_owner_roster_id=2, original_roster_id=3,
                       via_team_name=None)]


def test_fetch_trades_scans_every_week_through_the_given_week():
    client = _FakeClient({
        1: [{"type": "trade", "status": "complete", "transaction_id": "t1",
             "roster_ids": [2, 3], "adds": {}, "drops": {}, "draft_picks": [],
             "created": 1000}],
        2: [{"type": "trade", "status": "complete", "transaction_id": "t2",
             "roster_ids": [2, 3], "adds": {}, "drops": {}, "draft_picks": [],
             "created": 2000}],
    })
    result = transactions.fetch_trades(client, "L1", season=2026, through_week=2)
    assert {t.transaction_id for t in result} == {"t1", "t2"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/sleeper/test_transactions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.ingest.sleeper.transactions'`

- [ ] **Step 3: Add `TradeTransaction` to `domain/models.py`**

Append to `src/ffdo/domain/models.py`:

```python
@dataclass(frozen=True, slots=True)
class TradeTransaction:
    transaction_id: str
    season: int
    week: int
    roster_a_id: int
    roster_b_id: int
    roster_a_gets: list[str]
    roster_b_gets: list[str]
    picks_to_a: list[DraftPickAsset]
    picks_to_b: list[DraftPickAsset]
    traded_at_ms: int   # Sleeper's raw `created` epoch-millis timestamp
```

- [ ] **Step 4: Write the implementation**

```python
# src/ffdo/ingest/sleeper/transactions.py
"""Real completed trades, from /league/<id>/transactions/<week>. Verified
live during brainstorming: this endpoint returns every transaction type
(trade, waiver, free_agent), with trade records carrying roster_ids (both
parties), adds/drops (player_id -> roster_id, both directions), draft_picks
(round/season/owner_id/previous_owner_id), a stable transaction_id, and a
created timestamp -- everything api/trade_ledger.py needs, no gaps."""

from __future__ import annotations

from ffdo.domain.models import DraftPickAsset, TradeTransaction
from ffdo.ingest.client import V1, SleeperClient


def _parse_trade(raw: dict, season: int, week: int) -> TradeTransaction:
    roster_ids = sorted(int(r) for r in raw["roster_ids"])
    a_id, b_id = roster_ids[0], roster_ids[1]

    adds = raw.get("adds") or {}
    a_gets = [pid for pid, dest in adds.items() if int(dest) == a_id]
    b_gets = [pid for pid, dest in adds.items() if int(dest) == b_id]

    picks_to_a: list[DraftPickAsset] = []
    picks_to_b: list[DraftPickAsset] = []
    for p in raw.get("draft_picks") or []:
        owner = int(p["owner_id"])
        asset = DraftPickAsset(
            season=int(p["season"]), round=int(p["round"]), projected_slot=None,
            current_owner_roster_id=owner, original_roster_id=int(p["previous_owner_id"]),
            via_team_name=None)
        (picks_to_a if owner == a_id else picks_to_b).append(asset)

    return TradeTransaction(
        transaction_id=raw["transaction_id"], season=season, week=week,
        roster_a_id=a_id, roster_b_id=b_id,
        roster_a_gets=a_gets, roster_b_gets=b_gets,
        picks_to_a=picks_to_a, picks_to_b=picks_to_b,
        traded_at_ms=int(raw["created"]))


def fetch_trades(
    sleeper: SleeperClient, league_id: str, *, season: int, through_week: int,
) -> list[TradeTransaction]:
    out: list[TradeTransaction] = []
    for week in range(1, through_week + 1):
        raw_list = sleeper.get_json(f"{V1}/league/{league_id}/transactions/{week}")
        for raw in raw_list:
            if raw.get("type") == "trade" and raw.get("status") == "complete":
                out.append(_parse_trade(raw, season, week))
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/sleeper/test_transactions.py -v`
Expected: PASS, all 4 tests

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/ingest/sleeper/transactions.py src/ffdo/domain/models.py tests/ingest/sleeper/test_transactions.py
git commit -m "feat: ingest.sleeper.transactions.fetch_trades -- real completed trade detection"
```

---

### Task 7: `api/trade_ledger.py`

**Files:**
- Create: `src/ffdo/api/trade_ledger.py`
- Test: `tests/api/test_trade_ledger.py`

**Interfaces:**
- Consumes: `TradeTransaction` (Task 6).
- Produces: `TradeLedgerEntry` (frozen dataclass), `TradeLedger` class with
  `record_if_absent(league_key, trade, *, side_a_value, side_b_value, banked_a, banked_b) -> TradeLedgerEntry`,
  `list_for_league(league_key) -> list[TradeLedgerEntry]` -- consumed by Task 8's API wiring.

**Design note on "current value" (Global Constraints):** this ledger
stores each side's banked-points-at-trade-time snapshot, but does NOT
itself recompute "current value" -- that requires fresh valuation/pick
data the ledger has no access to (it only persists what it's given). Task
8's API handler recomputes current value at read time and merges it onto
what `list_for_league` returns before responding. This mirrors
`lineup_ledger.py`'s own separation: the ledger persists, the API layer
computes.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_trade_ledger.py
from dataclasses import replace

from ffdo.api.trade_ledger import TradeLedger
from ffdo.domain.models import TradeTransaction


def _trade(tid="t1", roster_a=2, roster_b=3):
    return TradeTransaction(
        transaction_id=tid, season=2026, week=3,
        roster_a_id=roster_a, roster_b_id=roster_b,
        roster_a_gets=["p1"], roster_b_gets=["p2"],
        picks_to_a=[], picks_to_b=[], traded_at_ms=1000)


def test_record_if_absent_writes_once(tmp_path):
    ledger = TradeLedger(tmp_path / "ffdo.db")
    first = ledger.record_if_absent(
        "sleeper:L1:2026", _trade(), side_a_value=40.0, side_b_value=30.0,
        banked_a={"p1": 12.0}, banked_b={"p2": 8.0})
    again = ledger.record_if_absent(
        "sleeper:L1:2026", _trade(), side_a_value=999.0, side_b_value=999.0,
        banked_a={}, banked_b={})
    assert again.side_a_value_at_trade == first.side_a_value_at_trade == 40.0


def test_list_for_league_returns_only_that_leagues_trades(tmp_path):
    ledger = TradeLedger(tmp_path / "ffdo.db")
    ledger.record_if_absent("sleeper:L1:2026", _trade("t1"), side_a_value=1.0,
                            side_b_value=1.0, banked_a={}, banked_b={})
    ledger.record_if_absent("sleeper:L2:2026", _trade("t2"), side_a_value=2.0,
                            side_b_value=2.0, banked_a={}, banked_b={})
    result = ledger.list_for_league("sleeper:L1:2026")
    assert [r.transaction_id for r in result] == ["t1"]


def test_list_for_league_is_chronological(tmp_path):
    ledger = TradeLedger(tmp_path / "ffdo.db")
    # TradeTransaction is frozen, so dataclasses.replace makes a new
    # instance rather than mutating -- recorded out of order to confirm
    # list_for_league sorts by traded_at_ms, not by insertion order.
    later = replace(_trade("t-later", roster_a=2, roster_b=3), traded_at_ms=2000)
    earlier = replace(_trade("t-earlier", roster_a=4, roster_b=5), traded_at_ms=1000)
    ledger.record_if_absent("sleeper:L1:2026", later, side_a_value=1.0,
                            side_b_value=1.0, banked_a={}, banked_b={})
    ledger.record_if_absent("sleeper:L1:2026", earlier, side_a_value=1.0,
                            side_b_value=1.0, banked_a={}, banked_b={})
    result = ledger.list_for_league("sleeper:L1:2026")
    assert [r.transaction_id for r in result] == ["t-earlier", "t-later"]


def test_corrupt_db_treated_as_empty(tmp_path):
    path = tmp_path / "ffdo.db"
    path.write_bytes(b"not a real sqlite file")
    ledger = TradeLedger(path)
    assert ledger.list_for_league("sleeper:L1:2026") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_trade_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.api.trade_ledger'`

- [ ] **Step 3: Write the implementation**

```python
# src/ffdo/api/trade_ledger.py
"""Decision ledger for real, completed trades -- every trade in the
league, not only ones the tracked user is party to (spec §6.1).

Records a trade's value snapshot once, on first detection
(`record_if_absent`) -- never overwritten, the same write-once posture as
`lineup_ledger.py`. Does NOT compute "current value" itself: that needs
fresh valuation/pick data this module has no access to. The API layer
(api/app.py) recomputes it at read time and merges it onto what
`list_for_league` returns (spec §6.3) -- this module only ever persists
and returns the frozen trade-time snapshot plus enough raw data
(banked-points-at-trade) for that recomputation to happen elsewhere.

Same connection pattern as lineup_ledger.py -- one file, stdlib sqlite3,
no ORM, corrupt-DB tolerance (treat as empty)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ffdo.domain.models import TradeTransaction


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class TradeLedgerEntry:
    league_key: str
    transaction_id: str
    season: int
    week: int
    roster_a_id: int
    roster_b_id: int
    roster_a_gets: list[str]
    roster_b_gets: list[str]
    picks_to_a: list[dict]   # DraftPickAsset fields, serialized -- see _row_to_entry
    picks_to_b: list[dict]
    traded_at_ms: int
    side_a_value_at_trade: float
    side_b_value_at_trade: float
    banked_a_at_trade: dict[str, float]
    banked_b_at_trade: dict[str, float]
    recorded_at: str


class TradeLedger:
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
                CREATE TABLE IF NOT EXISTS trade_record (
                    league_key TEXT NOT NULL,
                    transaction_id TEXT NOT NULL,
                    season INTEGER NOT NULL,
                    week INTEGER NOT NULL,
                    roster_a_id INTEGER NOT NULL,
                    roster_b_id INTEGER NOT NULL,
                    roster_a_gets_json TEXT NOT NULL,
                    roster_b_gets_json TEXT NOT NULL,
                    picks_to_a_json TEXT NOT NULL,
                    picks_to_b_json TEXT NOT NULL,
                    traded_at_ms INTEGER NOT NULL,
                    side_a_value_at_trade REAL NOT NULL,
                    side_b_value_at_trade REAL NOT NULL,
                    banked_a_json TEXT NOT NULL,
                    banked_b_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (league_key, transaction_id)
                );
                """
            )
            conn.commit()
        except sqlite3.DatabaseError:
            pass

    def get(self, league_key: str, transaction_id: str) -> TradeLedgerEntry | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM trade_record WHERE league_key = ? AND transaction_id = ?",
                    (league_key, transaction_id),
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        return self._row_to_entry(row) if row is not None else None

    def record_if_absent(
        self,
        league_key: str,
        trade: TradeTransaction,
        *,
        side_a_value: float,
        side_b_value: float,
        banked_a: dict[str, float],
        banked_b: dict[str, float],
    ) -> TradeLedgerEntry:
        existing = self.get(league_key, trade.transaction_id)
        if existing is not None:
            return existing
        recorded_at = _now()
        picks_to_a_json = json.dumps([
            {"season": p.season, "round": p.round, "projected_slot": p.projected_slot,
             "current_owner_roster_id": p.current_owner_roster_id,
             "original_roster_id": p.original_roster_id} for p in trade.picks_to_a])
        picks_to_b_json = json.dumps([
            {"season": p.season, "round": p.round, "projected_slot": p.projected_slot,
             "current_owner_roster_id": p.current_owner_roster_id,
             "original_roster_id": p.original_roster_id} for p in trade.picks_to_b])
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO trade_record "
                    "(league_key, transaction_id, season, week, roster_a_id, roster_b_id, "
                    " roster_a_gets_json, roster_b_gets_json, picks_to_a_json, picks_to_b_json, "
                    " traded_at_ms, side_a_value_at_trade, side_b_value_at_trade, "
                    " banked_a_json, banked_b_json, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (league_key, trade.transaction_id, trade.season, trade.week,
                     trade.roster_a_id, trade.roster_b_id,
                     json.dumps(trade.roster_a_gets), json.dumps(trade.roster_b_gets),
                     picks_to_a_json, picks_to_b_json, trade.traded_at_ms,
                     side_a_value, side_b_value,
                     json.dumps(banked_a), json.dumps(banked_b), recorded_at),
                )
                conn.commit()
        except sqlite3.DatabaseError:
            pass
        result = self.get(league_key, trade.transaction_id)
        return result if result is not None else TradeLedgerEntry(
            league_key=league_key, transaction_id=trade.transaction_id,
            season=trade.season, week=trade.week,
            roster_a_id=trade.roster_a_id, roster_b_id=trade.roster_b_id,
            roster_a_gets=trade.roster_a_gets, roster_b_gets=trade.roster_b_gets,
            picks_to_a=[], picks_to_b=[], traded_at_ms=trade.traded_at_ms,
            side_a_value_at_trade=side_a_value, side_b_value_at_trade=side_b_value,
            banked_a_at_trade=banked_a, banked_b_at_trade=banked_b,
            recorded_at=recorded_at)

    def list_for_league(self, league_key: str) -> list[TradeLedgerEntry]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM trade_record WHERE league_key = ? ORDER BY traded_at_ms ASC",
                    (league_key,),
                ).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [self._row_to_entry(row) for row in rows]

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> TradeLedgerEntry:
        return TradeLedgerEntry(
            league_key=row["league_key"], transaction_id=row["transaction_id"],
            season=row["season"], week=row["week"],
            roster_a_id=row["roster_a_id"], roster_b_id=row["roster_b_id"],
            roster_a_gets=json.loads(row["roster_a_gets_json"]),
            roster_b_gets=json.loads(row["roster_b_gets_json"]),
            picks_to_a=json.loads(row["picks_to_a_json"]),
            picks_to_b=json.loads(row["picks_to_b_json"]),
            traded_at_ms=row["traded_at_ms"],
            side_a_value_at_trade=row["side_a_value_at_trade"],
            side_b_value_at_trade=row["side_b_value_at_trade"],
            banked_a_at_trade=json.loads(row["banked_a_json"]),
            banked_b_at_trade=json.loads(row["banked_b_json"]),
            recorded_at=row["recorded_at"],
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_trade_ledger.py -v`
Expected: PASS, all 4 tests

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/api/trade_ledger.py tests/api/test_trade_ledger.py
git commit -m "feat: api.trade_ledger.TradeLedger -- persists every real trade in a league"
```

---

### Task 8: `api/app.py` wiring — two new endpoints

**Files:**
- Modify: `src/ffdo/api/app.py`
- Test: `tests/api/test_trade_endpoints.py`

**Interfaces:**
- Consumes: `trade_value.evaluate_trade` (Task 5), `transactions.fetch_trades` (Task 6), `TradeLedger` (Task 7), `PICK_VALUE_CURVE` (Task 3), plus existing `ros_value_mod.roster_value`, `rosters_mod.fetch`, `traded_picks_mod.capital`, `_load_league`, `_load_players`, `players_cache`, `client_mod.SleeperClient`, `_through_week`, `nfl_state_cache`, `_season_proj_anchor_for`, `actuals_mod.points_so_far` (all existing, read `get_season`/`get_lineup` for exact usage before wiring).

**Design note on "current value" for players (Global Constraints):**
current player value is `today's banked points - banked points snapshotted
at trade time`, a RAW POINT DIFFERENTIAL -- deliberately NOT re-derived
VOR (that would need a full league-wide replacement-level recomputation
for an arbitrary sub-window of the season, real added complexity for
Phase 1). This is a different SCALE than the trade-time value (which IS
VOR-scaled) -- the API response keeps them as clearly separate,
distinctly-labeled fields (`current_player_points_delta`, not folded into
a single "current_value" number next to the VOR-scaled trade-time value)
so nothing implies they are directly comparable.

**Design note on pick "current value" for real ledger trades:** a real
trade's `draft_picks` payload from Sleeper's transactions feed (Task 6)
carries no `projected_slot` -- that field only exists on `DraftPickAsset`
objects produced by `traded_picks.capital()`'s own standings-order
computation, which this endpoint does not re-run per ledger entry (real
added complexity: matching a stored pick back to its current standings-
implied slot needs a fresh `capital()` call plus a `(season, round,
original_roster_id)` lookup). So every real ledger entry's pick value
always falls through to `round_avg` (never exact-slot or tertile) --
`pick_value.slot_value` already degrades to this safely when
`projected_slot is None`, so this is a documented simplification, not a
bug. Its `years_out` discount still meaningfully updates as time passes.
The hypothetical evaluator (`POST /trade/evaluate`) is unaffected --
picks THERE come from the payload with whatever `projected_slot` the
caller supplies, which can be the real one from `traded_picks.capital()`'s
output.

- [ ] **Step 1: Read the current file structure first**

Read `_load_league`, `get_season`, `get_lineup`, and `_LINEUP_LEDGER`'s
module-level instantiation in `src/ffdo/api/app.py` before editing -- this
file has grown across four prior sub-projects, so confirm helper names and
exact call shapes against the real current file rather than assuming they
have not shifted.

- [ ] **Step 2: Write the failing tests**

```python
# tests/api/test_trade_endpoints.py
from fastapi.testclient import TestClient

from ffdo.api.app import V1, app_mod, create_app
from ffdo.api.store import LeagueStore

# Reuses the same _tracked/_ROSTERS/_USERS/_PLAYERS/_STATE/_PROJ/_MATCHUPS
# fixtures already defined in tests/api/test_season_endpoint.py -- read
# that file first to confirm they are still present under these exact
# names before importing them here.
from tests.api.test_season_endpoint import (
    _MATCHUPS, _PLAYERS, _PROJ, _ROSTERS, _STATE, _USERS, _tracked)


def test_trade_evaluate_returns_both_sides_value(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
    }

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            for key, val in resp.items():
                if key in url:
                    return val
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeClient)
    client = TestClient(create_app())
    body = {"partner_roster_id": 2,
            "side_a": {"player_ids": ["p_rb"], "picks": []},
            "side_b": {"player_ids": [], "picks": []}}
    res = client.post("/api/leagues/sleeper:L1:2026/trade/evaluate", json=body)
    assert res.status_code == 200
    data = res.json()
    assert "side_a_value" in data and "side_b_value" in data and "differential" in data


def test_trades_endpoint_returns_empty_list_with_no_real_trades(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
    }

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            if "/transactions/" in url:
                return []
            for key, val in resp.items():
                if key in url:
                    return val
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeClient)
    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/trades")
    assert res.status_code == 200
    assert res.json()["trades"] == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_trade_endpoints.py -v`
Expected: FAIL (404s -- the routes don't exist yet)

- [ ] **Step 4: Wire it in**

In `src/ffdo/api/app.py`:

1. Add `PICK_VALUE_CURVE` to the existing constants import line:
   ```python
   from ffdo.domain.constants import DYNASTY_AGE_CURVE, NFL_BYE_WEEKS, PICK_VALUE_CURVE, SEASON_LENGTH
   ```

2. Alongside the existing `from ffdo.ingest.sleeper import traded_picks as traded_picks_mod` line, add:
   ```python
   from ffdo.ingest.sleeper import transactions as transactions_mod
   from ffdo.engine import trade_value as trade_value_mod
   from ffdo.api.trade_ledger import TradeLedger
   ```

3. Alongside the existing `_LINEUP_LEDGER = LineupLedger(Path("data") / "ffdo.db")` module-level line, add:
   ```python
   _TRADE_LEDGER = TradeLedger(Path("data") / "ffdo.db")
   ```

4. Add both new endpoints, following `get_lineup`'s pattern of a fully
   self-contained fetch (own `SleeperClient`, own `profiles`/`rosters`,
   own `roster_value` call) rather than reaching into `_assemble_season`:

   ```python
   @app.post("/api/leagues/{league_key}/trade/evaluate")
   def evaluate_trade_endpoint(league_key: str, payload: dict) -> dict:
       """Hypothetical trade evaluator. side_a is always the tracked
       user's own roster (spec §7) -- partner_roster_id names the other
       team side_b's players/picks are assumed to belong to."""
       lg = _load_league(league_key)
       if lg.provider != "sleeper":
           raise HTTPException(status_code=400, detail="Trade evaluator is Sleeper-only for now")

       sleeper = client_mod.SleeperClient()
       try:
           nfl = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))
           profiles, _espn_id_index = players_cache.get(lambda: _load_players(sleeper))
           proj_anchor = _season_proj_anchor_for(lg.season).get(
               lambda: _load_projection_anchor(sleeper, lg.season))
           rosters = rosters_mod.fetch(sleeper, lg.provider_league_id)
           through_week = _through_week(nfl)
           actuals = actuals_mod.points_so_far(sleeper, lg.provider_league_id, through_week)
       except (httpx.HTTPError, RuntimeError) as exc:
           raise HTTPException(status_code=502, detail="Couldn't reach Sleeper, try again") from exc
       finally:
           sleeper.close()

       all_pids = {pid for r in rosters for pid in r.player_ids}
       valued = ros_value_mod.roster_value(
           all_pids, lg, resolved_format=lg.resolved_format, season_proj=proj_anchor,
           profiles=profiles, actuals=actuals, weeks_played=through_week,
           season_weeks=_season_weeks(lg.season))

       side_a = payload.get("side_a") or {}
       side_b = payload.get("side_b") or {}

       def _picks_from_payload(raw_picks: list) -> list:
           return [DraftPickAsset(
               season=int(p["season"]), round=int(p["round"]),
               projected_slot=p.get("projected_slot"),
               current_owner_roster_id=int(p.get("current_owner_roster_id", 0)),
               original_roster_id=int(p.get("original_roster_id", 0)),
               via_team_name=None) for p in raw_picks]

       result = trade_value_mod.evaluate_trade(
           {"player_ids": side_a.get("player_ids", []),
            "picks": _picks_from_payload(side_a.get("picks", []))},
           {"player_ids": side_b.get("player_ids", []),
            "picks": _picks_from_payload(side_b.get("picks", []))},
           valued_players=valued, pick_curve=PICK_VALUE_CURVE,
           current_season=lg.season, round_size=lg.num_teams)
       return {
           "side_a_value": round(result.side_a_value, 1),
           "side_b_value": round(result.side_b_value, 1),
           "differential": round(result.differential, 1),
           "differential_pct": (round(result.differential_pct, 3)
                                if result.differential_pct is not None else None),
       }

   @app.get("/api/leagues/{league_key}/trades")
   def get_trades(league_key: str) -> dict:
       """The real-trade decision ledger. Detects any new completed trade
       (every trade in the league, not only the tracked user's -- spec
       §6.1) as a side effect, then returns every recorded trade with a
       freshly recomputed current value (spec §6.3 -- never a one-time
       resolution)."""
       lg = _load_league(league_key)
       if lg.provider != "sleeper":
           raise HTTPException(status_code=400, detail="Trade ledger is Sleeper-only for now")

       sleeper = client_mod.SleeperClient()
       try:
           nfl = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))
           profiles, _espn_id_index = players_cache.get(lambda: _load_players(sleeper))
           proj_anchor = _season_proj_anchor_for(lg.season).get(
               lambda: _load_projection_anchor(sleeper, lg.season))
           rosters = rosters_mod.fetch(sleeper, lg.provider_league_id)
           through_week = _through_week(nfl)
           actuals = actuals_mod.points_so_far(sleeper, lg.provider_league_id, through_week)
           real_trades = transactions_mod.fetch_trades(
               sleeper, lg.provider_league_id, season=lg.season, through_week=through_week)
       except (httpx.HTTPError, RuntimeError) as exc:
           raise HTTPException(status_code=502, detail="Couldn't reach Sleeper, try again") from exc
       finally:
           sleeper.close()

       all_pids = {pid for r in rosters for pid in r.player_ids}
       valued = ros_value_mod.roster_value(
           all_pids, lg, resolved_format=lg.resolved_format, season_proj=proj_anchor,
           profiles=profiles, actuals=actuals, weeks_played=through_week,
           season_weeks=_season_weeks(lg.season))

       for trade in real_trades:
           side_a = {"player_ids": trade.roster_a_gets, "picks": trade.picks_to_a}
           side_b = {"player_ids": trade.roster_b_gets, "picks": trade.picks_to_b}
           evaluation = trade_value_mod.evaluate_trade(
               side_a, side_b, valued_players=valued, pick_curve=PICK_VALUE_CURVE,
               current_season=lg.season, round_size=lg.num_teams)
           banked_a = {pid: actuals.get(pid, 0.0) for pid in trade.roster_a_gets}
           banked_b = {pid: actuals.get(pid, 0.0) for pid in trade.roster_b_gets}
           _TRADE_LEDGER.record_if_absent(
               lg.league_key, trade, side_a_value=evaluation.side_a_value,
               side_b_value=evaluation.side_b_value, banked_a=banked_a, banked_b=banked_b)

       entries = _TRADE_LEDGER.list_for_league(lg.league_key)
       out = []
       for entry in entries:
           current_a_delta = sum(
               actuals.get(pid, 0.0) - entry.banked_a_at_trade.get(pid, 0.0)
               for pid in entry.roster_a_gets)
           current_b_delta = sum(
               actuals.get(pid, 0.0) - entry.banked_b_at_trade.get(pid, 0.0)
               for pid in entry.roster_b_gets)
           current_picks_a = sum(
               pick_value_mod.slot_value(
                   DraftPickAsset(season=p["season"], round=p["round"],
                                  projected_slot=p["projected_slot"],
                                  current_owner_roster_id=p["current_owner_roster_id"],
                                  original_roster_id=p["original_roster_id"],
                                  via_team_name=None),
                   PICK_VALUE_CURVE, current_season=lg.season, round_size=lg.num_teams)
               for p in entry.picks_to_a)
           current_picks_b = sum(
               pick_value_mod.slot_value(
                   DraftPickAsset(season=p["season"], round=p["round"],
                                  projected_slot=p["projected_slot"],
                                  current_owner_roster_id=p["current_owner_roster_id"],
                                  original_roster_id=p["original_roster_id"],
                                  via_team_name=None),
                   PICK_VALUE_CURVE, current_season=lg.season, round_size=lg.num_teams)
               for p in entry.picks_to_b)
           out.append({
               "transaction_id": entry.transaction_id, "week": entry.week,
               "roster_a_id": entry.roster_a_id, "roster_b_id": entry.roster_b_id,
               "roster_a_gets": entry.roster_a_gets, "roster_b_gets": entry.roster_b_gets,
               "side_a_value_at_trade": round(entry.side_a_value_at_trade, 1),
               "side_b_value_at_trade": round(entry.side_b_value_at_trade, 1),
               "current_player_points_delta_a": round(current_a_delta, 1),
               "current_player_points_delta_b": round(current_b_delta, 1),
               "current_pick_value_a": round(current_picks_a, 1),
               "current_pick_value_b": round(current_picks_b, 1),
           })
       return {"trades": out}
   ```

   Add `from ffdo.engine import pick_value as pick_value_mod` and
   `from ffdo.domain.models import DraftPickAsset` alongside the other
   function-local imports inside `create_app()` if not already present at
   that scope (check first -- `DraftPickAsset` may already be imported for
   `_draft_capital_payload`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_trade_endpoints.py -v`
Expected: PASS, both tests

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/api/app.py tests/api/test_trade_endpoints.py
git commit -m "feat: wire trade evaluator + real-trade ledger into the API"
```

---

### Task 9: Frontend — Trades tab

**Files:**
- Modify: `src/ffdo/web/season/season.js`
- Modify: `src/ffdo/web/season/season.css` (only if new classes need rules beyond what already exists for `.lineup-*`/`.capital-*` -- check first, reuse existing panel/row/badge classes wherever the shape matches)

**Interfaces:**
- Consumes: `POST /api/leagues/{key}/trade/evaluate`, `GET /api/leagues/{key}/trades` (Task 8).

No automated test for this task (this codebase has no JS test runner --
matches sub-project #3's Lineup tab, verified manually). Verification is
the manual browser smoke test in Task 10.

- [ ] **Step 1: Read the current file**

Read `src/ffdo/web/season/season.js` in full -- confirm `_panel`,
`renderRightPanel()`'s tab-bar construction, the `loadLineup`/`renderLineup`
lazy-load pattern, and the `data-panel-tab`/`data-pos-tab`/`data-scope-tab`
delegated-click convention are still exactly as described here before
editing, since this file has grown across three prior sub-projects.

- [ ] **Step 2: Add the tab and lazy-load state**

Add `_tradesData = null;` alongside the existing `_lineupData = null;`
module-level declaration, and reset it in `mountSeason()` alongside
`_lineupData = null;`.

Add a `data-panel-tab="trades"` button to `renderRightPanel()`'s tab bar,
always shown (unlike the conditional `showCapital` button -- trades apply
to every format, redraft included, just without picks):

```javascript
<button data-panel-tab="trades" class="${_panel === "trades" ? "on" : ""}">Trades</button>
```

Add a branch in `renderRightPanel()`'s panel dispatch, following the exact
lazy-load-then-render shape `_panel === "lineup"` already uses:

```javascript
if (_panel === "trades") {
  if (_tradesData === null) {
    loadTrades();
    return tabBar + `<div class="lineup-loading">Loading trades&hellip;</div>`;
  }
  return tabBar + renderTrades();
}
```

- [ ] **Step 3: Write `loadTrades()`**

Mirrors `loadLineup()`'s stale-response guard exactly (capture `_key`
before the first `await`, discard the response if the user has since
switched leagues):

```javascript
async function loadTrades() {
  const myKey = _key;
  let result;
  try {
    const res = await fetch(`/api/leagues/${encodeURIComponent(myKey)}/trades`);
    if (_key !== myKey) return;
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      if (_key !== myKey) return;
      result = { error: body.detail || "Couldn't load trades" };
    } else {
      const data = await res.json();
      if (_key !== myKey) return;
      result = data;
    }
  } catch (e) {
    if (_key !== myKey) return;
    result = { error: "Couldn't load trades" };
  }
  _tradesData = result;
  render();
}
```

- [ ] **Step 4: Write `renderTrades()`**

```javascript
function renderTrades() {
  if (_tradesData.error) {
    return `<div class="lineup-error">${escapeHtml(_tradesData.error)}</div>`;
  }
  if (_tradesData.trades.length === 0) {
    return `<div class="lineup-empty">No trades in this league yet</div>`;
  }
  const rows = _tradesData.trades.map(t => {
    const gotA = t.roster_a_gets.join(", ") || "(nothing)";
    const gotB = t.roster_b_gets.join(", ") || "(nothing)";
    return `<div class="lineup-row">
      <div>Week ${t.week}: Roster ${t.roster_a_id} got ${escapeHtml(gotA)}
        (value at trade ${t.side_a_value_at_trade}, since then
        ${t.current_player_points_delta_a > 0 ? "+" : ""}${t.current_player_points_delta_a} pts
        ${t.current_pick_value_a ? ` + ${t.current_pick_value_a} pick value now` : ""})</div>
      <div>Roster ${t.roster_b_id} got ${escapeHtml(gotB)}
        (value at trade ${t.side_b_value_at_trade}, since then
        ${t.current_player_points_delta_b > 0 ? "+" : ""}${t.current_player_points_delta_b} pts
        ${t.current_pick_value_b ? ` + ${t.current_pick_value_b} pick value now` : ""})</div>
    </div>`;
  }).join("");
  return `<div class="lineup-list">${rows}</div>`;
}
```

This deliberately reuses the existing `.lineup-row`/`.lineup-list`/`.lineup-error`/`.lineup-empty` CSS classes rather than inventing new ones -- the visual shape (a bordered row list in the right panel) is identical to the Lineup tab's, so no new CSS should be needed. Confirm this by checking `season.css` for those class definitions before adding anything new.

A full trade-builder UI (picking players/picks from your roster and a
chosen partner roster, calling `POST /trade/evaluate` live as you build)
is real, substantial frontend work with several interactive pieces
(roster/partner selectors, multi-select checkboxes, a live-updating value
readout). Given this plan's already-large scope (9 backend-heavy tasks
before this one), this task ships the ledger view only for the initial
cut; the interactive builder is the very next follow-up, tracked in the
spec's future-improvements section as a fast-follow rather than silently
dropped. State this explicitly in the task's commit message.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/web/season/season.js
git commit -m "feat: season screen Trades tab -- real-trade ledger view (builder UI is a fast-follow)"
```

---

### Task 10: README + browser smoke test

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update `README.md`**

Find the existing paragraph (added in sub-project #2, extended in #4)
describing the season screen's dynasty values, and add:

```markdown
The **Trades** tab shows every real trade in the league (not just yours),
each with its value at the moment it happened and how that trade has
played out since -- draft picks included, valued from real historical
rookie-draft outcomes rather than a guessed chart.
```

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, full count from all 9 prior tasks (this task's own commit
won't change the count)

- [ ] **Step 3: Browser smoke (manual — the executor or controller)**

Start the server (`uv run uvicorn ffdo.api.app:app --port 8150`) against a
league with real trade history -- `GDK` (`sleeper:1312210128811872256:2026`,
already tracked from #4's smoke test) has real trades confirmed live during
brainstorming.

- Open the season screen, switch to the Trades tab. Confirm real trades
  render (no 500, no empty state if GDK genuinely has trade history).
- Confirm a redraft/keeper league's Trades tab also loads without error
  (no picks expected there, but the tab itself must not break).
- Check devtools network tab: `/trades` succeeds; note whether reload
  speed changes now that trades are recorded in the ledger vs. the first
  (detection) load.
- No console errors on the Trades tab specifically (a pre-existing,
  unrelated `board.js` live-refresh error was already noted during #4's
  smoke test -- not this task's concern).
- Manually exercise `POST /trade/evaluate` via curl or the browser devtools
  console against a real league, confirming a sane `side_a_value`/`side_b_value`
  for a real player-for-player hypothetical.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "chore: document the Trades tab in the README"
```
