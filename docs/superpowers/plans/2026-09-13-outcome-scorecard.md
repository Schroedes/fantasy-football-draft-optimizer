# Outcome Scorecard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Every subagent dispatched for this plan (implementer, task reviewer, final reviewer) MUST be pinned to the Sonnet model explicitly — never let one fall through to a more expensive default.**

**Goal:** Build an outcome scorecard that shows how good FFDO's lineup, trade, waiver, and draft-day recommendations have actually been, backed by two new decision ledgers (waivers, draft picks) and a new positional-scarcity VOR knob validated the same way `DURABILITY_WEIGHT` was.

**Architecture:** Two new write-once SQLite ledgers (`waiver_ledger.py`, `draft_pick_ledger.py`) mirror the existing `trade_ledger.py`/`lineup_ledger.py` pattern exactly. A new pure-engine module (`engine/scorecard.py`) computes four independent metrics from lightweight outcome dataclasses the API layer builds from ledger rows — `engine/` stays free of any `api/` import, matching every other engine module's dependency shape. A new `engine/scarcity.py` module adds a real but initially-inert (`SCARCITY_STRENGTH = 0.0`) VOR adjustment, promoted only by a one-off backtest script, never auto-applied.

**Tech Stack:** Python 3.12, FastAPI, stdlib `sqlite3` (no ORM), pytest, vanilla JS/CSS (no framework) for the season screen.

**Spec:** `docs/superpowers/specs/2026-09-13-outcome-scorecard-design.md`

## Global Constraints

- Sleeper-only for now, same posture as every prior season-screen endpoint (`/lineup`, `/trades`, `/waivers`): a non-Sleeper league gets a 400, not a silent partial result.
- Scan any transaction/pick history through `nfl.week` (the LIVE current week), never a stats-final `through_week` variable — this is the "live vs. final data" bug class that bit sub-project #5's `GET /trades` and must not recur here.
- Every new SQLite table follows the existing ledgers' exact posture: one file (`data/ffdo.db`), stdlib `sqlite3`, no ORM, corrupt-DB tolerance (`except sqlite3.DatabaseError: pass`/`return None`/`return []`), write-once via `record_if_absent`, never overwritten.
- `engine/` modules depend only on `domain/` and other `engine/` modules — never `api/`. `engine/scorecard.py` takes lightweight dataclasses it defines itself, not the ledgers' own storage-shaped dataclasses; `api/app.py` (the composition root) converts between them, the same way it already shapes every other endpoint's JSON response.
- `SCARCITY_STRENGTH` and every other promoted-constant pattern in this codebase (`DURABILITY_WEIGHT`, `AGE_WEIGHT`) default to a value that changes nothing (`0.0`) until a real backtest justifies moving it — this plan does not commit to promoting `SCARCITY_STRENGTH` above `0.0`.
- All subagents dispatched to execute this plan (implementer, task reviewer, final reviewer, any fix-round dispatch) use the Sonnet model explicitly.

---

### Task 1: `engine/scarcity.py` — positional cliff and scarcity multiplier

**Files:**
- Create: `src/ffdo/engine/scarcity.py`
- Test: `tests/engine/test_scarcity.py`

**Interfaces:**
- Consumes: nothing beyond stdlib — pure functions over already-ranked pools.
- Produces: `positional_cliff(ranked, levels) -> dict[str, float]`, `scarcity_multiplier(cliff, strength) -> dict[str, float]`, `CLIFF_DEPTH: Final[int] = 3`. Task 2 imports both functions.

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_scarcity.py
from ffdo.engine import scarcity


def test_positional_cliff_measures_gap_below_replacement():
    ranked = {"RB": [(30.0, "a"), (20.0, "b"), (15.0, "c"),
                     (10.0, "d"), (5.0, "e"), (2.0, "f")]}
    levels = {"RB": 15.0}
    cliff = scarcity.positional_cliff(ranked, levels)
    # replacement is "c" (15.0) at idx 2; next 3 below are 10.0/5.0/2.0, mean 5.6667
    assert cliff["RB"] == 15.0 - (10.0 + 5.0 + 2.0) / 3


def test_positional_cliff_thin_pool_uses_fewer_than_depth():
    ranked = {"TE": [(10.0, "a"), (5.0, "b")]}
    levels = {"TE": 5.0}
    cliff = scarcity.positional_cliff(ranked, levels)
    assert cliff["TE"] == 5.0 - 5.0  # only "b" itself is at/below level, nothing below it


def test_positional_cliff_nothing_below_replacement_is_zero():
    ranked = {"K": [(10.0, "a")]}
    levels = {"K": 10.0}
    cliff = scarcity.positional_cliff(ranked, levels)
    assert cliff["K"] == 0.0


def test_scarcity_multiplier_zero_strength_is_a_no_op():
    cliff = {"RB": 20.0, "WR": 4.0}
    result = scarcity.scarcity_multiplier(cliff, 0.0)
    assert result == {"RB": 1.0, "WR": 1.0}


def test_scarcity_multiplier_scales_by_relative_cliff():
    cliff = {"RB": 20.0, "WR": 4.0}
    result = scarcity.scarcity_multiplier(cliff, 1.0)
    assert result["RB"] == 1.0 + 1.0 * (20.0 / 20.0)
    assert result["WR"] == 1.0 + 1.0 * (4.0 / 20.0)


def test_scarcity_multiplier_all_zero_cliffs_returns_ones():
    cliff = {"RB": 0.0, "WR": 0.0}
    result = scarcity.scarcity_multiplier(cliff, 2.0)
    assert result == {"RB": 1.0, "WR": 1.0}


def test_scarcity_multiplier_empty_cliff_returns_empty():
    assert scarcity.scarcity_multiplier({}, 1.0) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/engine/test_scarcity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.engine.scarcity'`

- [ ] **Step 3: Write the implementation**

```python
# src/ffdo/engine/scarcity.py
"""Positional scarcity: how much a position's VOR should scale up when its
replacement-level cliff (the points gap immediately below replacement) is
steep -- there's no comparable fallback once that player is gone, unlike a
position with plenty of similarly-valued depth just below the cutoff.

Both functions are pure math over an already-ranked player pool -- no I/O,
no league-object coupling beyond what the caller (engine.vor.compute)
already has in hand from engine.replacement.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

CLIFF_DEPTH: Final[int] = 3


def positional_cliff(
    ranked: Mapping[str, list[tuple[float, str]]],
    levels: Mapping[str, float],
) -> dict[str, float]:
    """Points gap between each position's replacement level and the mean of
    the CLIFF_DEPTH players ranked just below it. Always >= 0: `ranked[pos]`
    is sorted descending and the players counted are ranked below the
    replacement-level entry, so they can only be worth the same or less. A
    position with fewer than CLIFF_DEPTH players left below replacement (a
    thin pool) averages over however many remain; a position with none left
    below replacement gets 0.0 -- there is nothing to fall off a cliff into.
    """
    cliffs: dict[str, float] = {}
    for pos, level in levels.items():
        pool = ranked.get(pos, [])
        idx = next((i for i, (v, _) in enumerate(pool) if v <= level), len(pool))
        below = [v for v, _ in pool[idx + 1: idx + 1 + CLIFF_DEPTH]]
        cliffs[pos] = (level - sum(below) / len(below)) if below else 0.0
    return cliffs


def scarcity_multiplier(cliff: Mapping[str, float], strength: float) -> dict[str, float]:
    """1.0 + strength * (this position's cliff / the steepest cliff of any
    position). strength=0.0 always returns every multiplier as 1.0 -- a
    pure no-op, matching SCARCITY_STRENGTH's default in engine.vor until a
    real backtest (scripts/backtest_scarcity.py) promotes it.
    """
    if not cliff:
        return {}
    max_cliff = max(cliff.values())
    if max_cliff <= 0.0:
        return dict.fromkeys(cliff, 1.0)
    return {pos: 1.0 + strength * (c / max_cliff) for pos, c in cliff.items()}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/test_scarcity.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/scarcity.py tests/engine/test_scarcity.py
git commit -m "feat: engine.scarcity -- positional cliff + scarcity multiplier"
```

---

### Task 2: Wire scarcity into `engine/vor.py`

**Files:**
- Modify: `src/ffdo/engine/vor.py`
- Test: `tests/engine/test_vor.py`

**Interfaces:**
- Consumes: `scarcity.positional_cliff`, `scarcity.scarcity_multiplier` (Task 1); `replacement.rank_by_position` (existing).
- Produces: `vor.SCARCITY_STRENGTH: Final[float] = 0.0`; `vor.compute(..., scarcity_strength: float = SCARCITY_STRENGTH)`. Task 3's backtest script calls `vor.compute` directly with an explicit `scarcity_strength`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/engine/test_vor.py` (the file already defines `_profiles` and `_league` helpers used below — do not redefine them):

```python
def test_scarcity_strength_zero_leaves_vor_unchanged():
    points = {f"rb{i}": 200.0 - 10 * i for i in range(6)}
    profiles = _profiles({f"rb{i}": "RB" for i in range(6)})
    points.update({f"wr{i}": 150.0 - 10 * i for i in range(6)})
    profiles.update(_profiles({f"wr{i}": "WR" for i in range(6)}))
    valued = vor.compute(points, profiles, _league(), scarcity_strength=0.0)
    assert valued["rb0"].vor == 200.0 - 180.0


def test_scarcity_strength_scales_vor_by_relative_cliff():
    # league() has 2 teams x 1 RB slot x 1 WR slot -> replacement is the
    # 3rd-best player at each position (idx 2).
    # RB: 200,190,180,170,160,150 -> replacement 180.0, next 3 below
    #     (170,160,150) mean 160.0 -> cliff 20.0
    # WR: 150,148,146,144,142,140 -> replacement 146.0, next 3 below
    #     (144,142,140) mean 142.0 -> cliff 4.0 (RB's cliff is the max)
    points = {f"rb{i}": 200.0 - 10 * i for i in range(6)}
    points.update({f"wr{i}": 150.0 - 2 * i for i in range(6)})
    profiles = _profiles({f"rb{i}": "RB" for i in range(6)})
    profiles.update(_profiles({f"wr{i}": "WR" for i in range(6)}))

    valued = vor.compute(points, profiles, _league(), scarcity_strength=1.0)
    raw_rb_vor = 200.0 - 180.0
    raw_wr_vor = 150.0 - 146.0
    # RB carries the steepest cliff (20.0, the max), so its multiplier is
    # 1.0 + 1.0 * (20/20) = 2.0
    assert valued["rb0"].vor == raw_rb_vor * 2.0
    # WR's multiplier is 1.0 + 1.0 * (4/20) = 1.2
    assert valued["wr0"].vor == raw_wr_vor * 1.2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/engine/test_vor.py -v`
Expected: FAIL with `TypeError: compute() got an unexpected keyword argument 'scarcity_strength'`

- [ ] **Step 3: Write the implementation**

Replace the full contents of `src/ffdo/engine/vor.py`:

```python
"""Value over replacement, plus tier detection by gap clustering."""

from __future__ import annotations

from collections.abc import Mapping
from statistics import median
from typing import Final

from ffdo.domain.models import PlayerProfile, ValuedPlayer
from ffdo.engine import scarcity
from ffdo.engine.replacement import rank_by_position, replacement_levels

# Promoted above zero only on real out-of-sample backtest improvement -- see
# scripts/backtest_scarcity.py. Same posture as adjustments.AGE_WEIGHT: a
# real capability that ships inert until the data says otherwise.
SCARCITY_STRENGTH: Final[float] = 0.0


def compute(
    points: Mapping[str, float],
    profiles: Mapping[str, PlayerProfile],
    league,
    *,
    adjustments: Mapping[str, Mapping[str, float]] | None = None,
    scarcity_strength: float = SCARCITY_STRENGTH,
) -> dict[str, ValuedPlayer]:
    adjustments = adjustments or {}
    adjusted = {
        pid: pts + sum(adjustments.get(pid, {}).values())
        for pid, pts in points.items()
        if pid in profiles
    }
    positions = {pid: profiles[pid].position for pid in adjusted}
    levels = replacement_levels(adjusted, positions, league)

    # Recomputed here rather than threaded out of replacement_levels: that
    # function's signature is also depended on by engine.roster's
    # single-team lineup solve, and ranking is cheap relative to everything
    # else this function already does.
    ranked = rank_by_position(adjusted, positions)
    cliff = scarcity.positional_cliff(ranked, levels)
    multiplier = scarcity.scarcity_multiplier(cliff, scarcity_strength)

    out: dict[str, ValuedPlayer] = {}
    for pid, adj_pts in adjusted.items():
        pos = positions[pid]
        if pos not in levels:
            # No roster slot (dedicated or FLEX-reachable) exists for this
            # position in `league.roster_positions`, so there is no
            # replacement level to measure against. Silently defaulting to
            # 0.0 here would turn a player's raw point total into his VOR --
            # exactly backwards, since it makes an unrostered position look
            # like an extreme bargain. Exclude instead: a player at a
            # position the league doesn't start has no meaningful VOR.
            continue
        out[pid] = ValuedPlayer(
            profile=profiles[pid],
            projected_points=points[pid],
            adjusted_points=adj_pts,
            vor=(adj_pts - levels[pos]) * multiplier.get(pos, 1.0),
            tier=0,
            adjustments=dict(adjustments.get(pid, {})),
        )
    return out
```

(`assign_tiers` and any other existing function in this file below `compute` are unchanged — only `compute` and its imports/module-level constant change.)

- [ ] **Step 4: Run the full vor test file to verify everything passes**

Run: `uv run pytest tests/engine/test_vor.py -v`
Expected: PASS (all existing tests plus the 2 new ones — existing tests use `vor.compute`'s default `scarcity_strength=0.0`, which leaves every VOR value byte-identical to before this change)

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/vor.py tests/engine/test_vor.py
git commit -m "feat: wire positional scarcity into vor.compute, inert by default"
```

---

### Task 3: `scripts/backtest_scarcity.py` — validation script

**Files:**
- Create: `scripts/backtest_scarcity.py`

**Interfaces:**
- Consumes: `vor.compute(..., scarcity_strength=...)` (Task 2); `backtest.harness.spearman` (existing); the `LeagueProfile`-shaped reference-pool pattern already used by `scripts/fit_faab_curve.py`/`scripts/fit_pick_value.py`.
- Produces: nothing consumed by later tasks — a standalone, one-off script, same category as `fit_faab_curve.py`. Prints results for human review; never auto-writes `SCARCITY_STRENGTH`.

- [ ] **Step 1: Write the script**

```python
# scripts/backtest_scarcity.py
"""One-off backtest: does positional-scarcity-weighted VOR rank players
closer to their real final-season outcome than plain VOR does? Prints
results for human review -- SCARCITY_STRENGTH in engine/vor.py is only
ever promoted by hand, the same posture DURABILITY_WEIGHT was promoted
under in sub-project #4 (see backtest/harness.py, whose real-data-loading
this script's evaluate_season mirrors).

Baseline here is plain VOR (scarcity_strength=0.0), not ADP -- VOR already
beats ADP (see harness.py's own baseline_rho against ADP); the question
this script answers is narrower: does scarcity improve on top of VOR that
already exists.

Run: uv run python scripts/backtest_scarcity.py [season ...]
     (defaults to 2023 2024 2025 if no seasons given)
"""

from __future__ import annotations

import sys

import numpy as np

from ffdo.backtest.harness import spearman
from ffdo.domain.constants import STANDARD_HALF_PPR
from ffdo.domain.models import LeagueProfile
from ffdo.engine import vor as vor_mod
from ffdo.ingest import players as players_mod
from ffdo.ingest import projections as proj_mod
from ffdo.ingest import snapshot
from ffdo.ingest import stats as stats_mod

OFFENSE = {"QB", "RB", "WR", "TE"}
_ADP_KEY = "half_ppr"
_REFERENCE_ROSTER = (
    "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX",
    "BN", "BN", "BN", "BN", "BN", "BN", "BN",
)


def evaluate_season(season: int, scarcity_strength: float) -> dict:
    profiles = players_mod.parse(snapshot.load("players_nfl"))
    actual = stats_mod.parse(snapshot.load(f"stats_{season}"), season)
    _proj, adp = proj_mod.parse(
        snapshot.load(f"projections_{season}_CONTAMINATED"),
        season, allow_contaminated=True)

    ids, adp_values, truth = [], [], []
    for pid, market in adp.items():
        value = market.adp.get(_ADP_KEY, 999.0)
        prof = profiles.get(pid)
        if value >= 999 or prof is None or prof.position not in OFFENSE:
            continue
        line = actual.get(pid)
        if line is None:
            continue
        ids.append(pid)
        adp_values.append(value)
        truth.append(line.stats.get("pts_half_ppr", 0.0))

    # Same ADP-rank-to-pseudo-points mapping harness.py's evaluate_season
    # uses, so plain VOR here is computed on the same scale a real
    # draft-day valuation would see.
    order = np.argsort(adp_values)
    pseudo = np.empty(len(ids), dtype=float)
    pseudo[order] = np.linspace(300.0, 20.0, len(ids))
    points = dict(zip(ids, pseudo, strict=True))

    subset = {pid: profiles[pid] for pid in ids}
    reference = LeagueProfile(
        league_id="reference", season=season, num_teams=12,
        roster_positions=_REFERENCE_ROSTER, scoring_settings=STANDARD_HALF_PPR,
        budget=None)

    baseline_valued = vor_mod.compute(points, subset, reference, scarcity_strength=0.0)
    model_valued = vor_mod.compute(points, subset, reference, scarcity_strength=scarcity_strength)

    baseline = [baseline_valued[pid].vor if pid in baseline_valued else 0.0 for pid in ids]
    model = [model_valued[pid].vor if pid in model_valued else 0.0 for pid in ids]

    baseline_rho = spearman(baseline, truth)
    model_rho = spearman(model, truth)
    return {
        "season": season, "scarcity_strength": scarcity_strength, "n": len(ids),
        "baseline_rho": round(baseline_rho, 4), "model_rho": round(model_rho, 4),
        "improvement": round(model_rho - baseline_rho, 4),
    }


def sweep(season: int, strengths: list[float]) -> list[dict]:
    return [evaluate_season(season, s) for s in strengths]


if __name__ == "__main__":
    seasons = [int(s) for s in sys.argv[1:]] or [2023, 2024, 2025]
    strengths = [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0]
    for season in seasons:
        print(f"=== season {season} ===")
        for row in sweep(season, strengths):
            print(row)
```

- [ ] **Step 2: Run it and confirm it prints without crashing**

Run: `uv run python scripts/backtest_scarcity.py 2024`
Expected: prints one line per `scarcity_strength` candidate for season 2024, each with `n`, `baseline_rho`, `model_rho`, `improvement`. The `scarcity_strength=0.0` row's `model_rho` must exactly equal its `baseline_rho` (both are plain VOR) — if they differ, something in Task 2's wiring is wrong and must be fixed before proceeding.

This step is validation, not promotion: reviewing whether any candidate shows real improvement and deciding whether to raise `SCARCITY_STRENGTH` above `0.0` in `engine/vor.py` is a human judgment call, out of scope for this task.

- [ ] **Step 3: Commit**

```bash
git add scripts/backtest_scarcity.py
git commit -m "feat: scripts/backtest_scarcity.py -- validate scarcity strength against real outcomes"
```

---

### Task 4: `api/waiver_ledger.py` — new decision ledger

**Files:**
- Create: `src/ffdo/api/waiver_ledger.py`
- Test: `tests/api/test_waiver_ledger.py`

**Interfaces:**
- Consumes: stdlib only.
- Produces: `WaiverLedgerEntry` (frozen dataclass: `league_key, transaction_id, season, week, roster_id, add_player_id, drop_player_id, recommended_bid, actual_bid, predicted_vor_gain, won, recorded_at`), `WaiverLedger(path).record_if_absent(league_key, *, transaction_id, season, week, roster_id, add_player_id, drop_player_id, recommended_bid, actual_bid, predicted_vor_gain, won) -> WaiverLedgerEntry`, `.get(league_key, transaction_id) -> WaiverLedgerEntry | None`, `.list_for_league(league_key) -> list[WaiverLedgerEntry]`. Task 6 wires this into `app.py`; Task 11 consumes `list_for_league`'s output (via a lightweight `WaiverOutcome` built in `app.py`, not this dataclass directly).

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_waiver_ledger.py
from ffdo.api.waiver_ledger import WaiverLedger


def _kwargs(**overrides):
    base = dict(
        transaction_id="w1", season=2026, week=3, roster_id=5,
        add_player_id="p1", drop_player_id="p9", recommended_bid=15.0,
        actual_bid=15, predicted_vor_gain=8.0, won=True,
    )
    base.update(overrides)
    return base


def test_record_if_absent_persists_and_returns_the_entry(tmp_path):
    ledger = WaiverLedger(tmp_path / "test.db")
    entry = ledger.record_if_absent("league1", **_kwargs())
    assert entry.transaction_id == "w1"
    assert entry.roster_id == 5
    assert entry.add_player_id == "p1"
    assert entry.drop_player_id == "p9"
    assert entry.recommended_bid == 15.0
    assert entry.actual_bid == 15
    assert entry.predicted_vor_gain == 8.0
    assert entry.won is True
    assert entry.recorded_at


def test_record_if_absent_is_idempotent(tmp_path):
    ledger = WaiverLedger(tmp_path / "test.db")
    first = ledger.record_if_absent("league1", **_kwargs())
    second = ledger.record_if_absent("league1", **_kwargs(actual_bid=999, won=False))
    assert second.recorded_at == first.recorded_at
    assert second.actual_bid == 15  # the SECOND call's values are ignored -- write-once
    assert second.won is True


def test_get_returns_none_when_absent(tmp_path):
    ledger = WaiverLedger(tmp_path / "test.db")
    assert ledger.get("league1", "nonexistent") is None


def test_get_returns_the_recorded_entry(tmp_path):
    ledger = WaiverLedger(tmp_path / "test.db")
    ledger.record_if_absent("league1", **_kwargs())
    entry = ledger.get("league1", "w1")
    assert entry is not None
    assert entry.transaction_id == "w1"


def test_list_for_league_scopes_to_the_given_league(tmp_path):
    ledger = WaiverLedger(tmp_path / "test.db")
    ledger.record_if_absent("league1", **_kwargs(transaction_id="w1"))
    ledger.record_if_absent("league2", **_kwargs(transaction_id="w2"))
    result = ledger.list_for_league("league1")
    assert [e.transaction_id for e in result] == ["w1"]


def test_list_for_league_supports_a_lost_claim_with_null_recommendation_fields(tmp_path):
    ledger = WaiverLedger(tmp_path / "test.db")
    ledger.record_if_absent(
        "league1", transaction_id="w1", season=2026, week=1, roster_id=5,
        add_player_id="p2", drop_player_id=None, recommended_bid=None,
        actual_bid=3, predicted_vor_gain=None, won=False)
    entry = ledger.get("league1", "w1")
    assert entry.recommended_bid is None
    assert entry.drop_player_id is None
    assert entry.won is False


def test_corrupt_db_is_tolerated_as_empty(tmp_path):
    db_path = tmp_path / "test.db"
    db_path.write_bytes(b"not a real sqlite file")
    ledger = WaiverLedger(db_path)
    assert ledger.get("league1", "w1") is None
    assert ledger.list_for_league("league1") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_waiver_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.api.waiver_ledger'`

- [ ] **Step 3: Write the implementation**

```python
# src/ffdo/api/waiver_ledger.py
"""Decision ledger for FAAB waiver claims -- deferred from sub-project #6,
built here alongside the outcome scorecard that reads it (spec
docs/superpowers/specs/2026-09-13-outcome-scorecard-design.md).

Unlike lineup_ledger.py (needs week-lock to resolve) and trade_ledger.py
(never resolves, continuously revalued), a waiver claim's outcome is known
synchronously -- Sleeper's transaction feed reports a final status (won or
lost) as soon as that week's waivers process -- so record_if_absent
captures the whole outcome in one write, no separate resolve() step.

Same connection pattern as the other ledgers: one file, stdlib sqlite3, no
ORM, corrupt-DB tolerance (treat as empty)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class WaiverLedgerEntry:
    league_key: str
    transaction_id: str
    season: int
    week: int
    roster_id: int
    add_player_id: str
    drop_player_id: str | None
    recommended_bid: float | None
    actual_bid: int
    predicted_vor_gain: float | None
    won: bool
    recorded_at: str


class WaiverLedger:
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
                CREATE TABLE IF NOT EXISTS waiver_record (
                    league_key TEXT NOT NULL,
                    transaction_id TEXT NOT NULL,
                    season INTEGER NOT NULL,
                    week INTEGER NOT NULL,
                    roster_id INTEGER NOT NULL,
                    add_player_id TEXT NOT NULL,
                    drop_player_id TEXT,
                    recommended_bid REAL,
                    actual_bid INTEGER NOT NULL,
                    predicted_vor_gain REAL,
                    won INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (league_key, transaction_id)
                );
                """
            )
            conn.commit()
        except sqlite3.DatabaseError:
            pass

    def get(self, league_key: str, transaction_id: str) -> WaiverLedgerEntry | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM waiver_record WHERE league_key = ? AND transaction_id = ?",
                    (league_key, transaction_id),
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        return self._row_to_entry(row) if row is not None else None

    def record_if_absent(
        self,
        league_key: str,
        *,
        transaction_id: str,
        season: int,
        week: int,
        roster_id: int,
        add_player_id: str,
        drop_player_id: str | None,
        recommended_bid: float | None,
        actual_bid: int,
        predicted_vor_gain: float | None,
        won: bool,
    ) -> WaiverLedgerEntry:
        existing = self.get(league_key, transaction_id)
        if existing is not None:
            return existing
        recorded_at = _now()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO waiver_record "
                    "(league_key, transaction_id, season, week, roster_id, add_player_id, "
                    " drop_player_id, recommended_bid, actual_bid, predicted_vor_gain, won, "
                    " recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (league_key, transaction_id, season, week, roster_id, add_player_id,
                     drop_player_id, recommended_bid, actual_bid, predicted_vor_gain,
                     int(won), recorded_at),
                )
                conn.commit()
        except sqlite3.DatabaseError:
            pass
        result = self.get(league_key, transaction_id)
        return result if result is not None else WaiverLedgerEntry(
            league_key=league_key, transaction_id=transaction_id, season=season, week=week,
            roster_id=roster_id, add_player_id=add_player_id, drop_player_id=drop_player_id,
            recommended_bid=recommended_bid, actual_bid=actual_bid,
            predicted_vor_gain=predicted_vor_gain, won=won, recorded_at=recorded_at)

    def list_for_league(self, league_key: str) -> list[WaiverLedgerEntry]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM waiver_record WHERE league_key = ? ORDER BY recorded_at ASC",
                    (league_key,),
                ).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [self._row_to_entry(row) for row in rows]

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> WaiverLedgerEntry:
        return WaiverLedgerEntry(
            league_key=row["league_key"], transaction_id=row["transaction_id"],
            season=row["season"], week=row["week"], roster_id=row["roster_id"],
            add_player_id=row["add_player_id"], drop_player_id=row["drop_player_id"],
            recommended_bid=row["recommended_bid"], actual_bid=row["actual_bid"],
            predicted_vor_gain=row["predicted_vor_gain"], won=bool(row["won"]),
            recorded_at=row["recorded_at"],
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_waiver_ledger.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/api/waiver_ledger.py tests/api/test_waiver_ledger.py
git commit -m "feat: api.waiver_ledger -- FAAB claim decision ledger (deferred from #6)"
```

---

### Task 5: Extend `ingest/sleeper/waivers.py` with win/loss capture

**Files:**
- Modify: `src/ffdo/domain/models.py` (`WaiverClaim`)
- Modify: `src/ffdo/ingest/sleeper/waivers.py`
- Test: `tests/ingest/sleeper/test_waivers.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `WaiverClaim.won: bool = True` (new trailing field, default preserves every existing construction site); `fetch_all_claims(sleeper, league_id, *, season, through_week) -> list[WaiverClaim]`. Task 6 calls `fetch_all_claims`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ingest/sleeper/test_waivers.py` (the file already defines `_FakeClient` — reuse it, do not redefine):

```python
def test_fetch_all_claims_includes_both_wins_and_losses():
    client = _FakeClient({
        1: [
            {"type": "waiver", "status": "complete", "transaction_id": "w1",
             "roster_ids": [3], "adds": {"p1": 3},
             "settings": {"waiver_bid": 12}, "created": 1000},
            {"type": "waiver", "status": "failed", "transaction_id": "w2",
             "roster_ids": [4], "adds": {"p2": 4},
             "settings": {"waiver_bid": 5}, "created": 1100},
            {"type": "trade", "status": "complete", "transaction_id": "t1",
             "roster_ids": [2, 3], "adds": {"p9": 2}, "created": 900},
        ],
    })
    result = waivers.fetch_all_claims(client, "L1", season=2026, through_week=1)
    assert {c.transaction_id for c in result} == {"w1", "w2"}
    won_by_id = {c.transaction_id: c.won for c in result}
    assert won_by_id["w1"] is True
    assert won_by_id["w2"] is False


def test_fetch_all_claims_scans_every_week_through_the_given_week():
    client = _FakeClient({
        1: [{"type": "waiver", "status": "complete", "transaction_id": "w1",
             "roster_ids": [3], "adds": {"p1": 3},
             "settings": {"waiver_bid": 1}, "created": 1000}],
        2: [{"type": "waiver", "status": "failed", "transaction_id": "w2",
             "roster_ids": [3], "adds": {"p2": 3},
             "settings": {"waiver_bid": 2}, "created": 2000}],
    })
    result = waivers.fetch_all_claims(client, "L1", season=2026, through_week=2)
    assert {c.transaction_id for c in result} == {"w1", "w2"}


def test_fetch_waivers_claims_default_won_to_true():
    client = _FakeClient({
        1: [{"type": "waiver", "status": "complete", "transaction_id": "w1",
             "roster_ids": [3], "adds": {"p1": 3},
             "settings": {"waiver_bid": 12}, "created": 1000}],
    })
    result = waivers.fetch_waivers(client, "L1", season=2026, through_week=1)
    assert result[0].won is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/ingest/sleeper/test_waivers.py -v`
Expected: FAIL with `AttributeError: module 'ffdo.ingest.sleeper.waivers' has no attribute 'fetch_all_claims'`

- [ ] **Step 3: Write the implementation**

In `src/ffdo/domain/models.py`, find the `WaiverClaim` dataclass:

```python
class WaiverClaim:
    transaction_id: str
    season: int
    week: int
    roster_id: int
    player_id: str
    bid_amount: float
    created_ms: int
```

Add a trailing field with a default (safe for every existing construction site, which will simply get `won=True`):

```python
class WaiverClaim:
    transaction_id: str
    season: int
    week: int
    roster_id: int
    player_id: str
    bid_amount: float
    created_ms: int
    won: bool = True
```

In `src/ffdo/ingest/sleeper/waivers.py`, update the module docstring and `fetch_waivers`'s `WaiverClaim(...)` construction to pass `won=True` explicitly (its behavior is unchanged -- it already only returns `status == "complete"` claims -- this just makes the now-meaningful field visible at the one call site that only ever produces wins), then add `fetch_all_claims`:

```python
"""Real completed FAAB waiver claims, from /league/<id>/transactions/<week>
-- the sibling of ingest.sleeper.transactions.fetch_trades, filtered to
type == "waiver" instead of "trade". Also provides remaining_budget, which
computes each roster's current remaining FAAB budget from a list of claims
via a simple order-independent sum (waiver_budget minus total bid amount
spent) -- the order the claims are summed in doesn't matter here.

fetch_waivers only returns WON claims (status == "complete"): a lost claim
never spends FAAB budget, so remaining_budget's only caller has no use for
them. api.waiver_ledger needs BOTH outcomes to compute a win rate -- that's
what fetch_all_claims is for.

A separate, genuinely different need -- the offline scripts/fit_faab_curve.py
fitting script -- must walk a season's claims in chronological order to
recover each claim's intermediate remaining-before state, which neither
function here provides."""

from __future__ import annotations

from collections.abc import Sequence

from ffdo.domain.models import WaiverClaim
from ffdo.ingest.client import V1, SleeperClient


def fetch_waivers(
    sleeper: SleeperClient, league_id: str, *, season: int, through_week: int,
) -> list[WaiverClaim]:
    out: list[WaiverClaim] = []
    for week in range(1, through_week + 1):
        raw_list = sleeper.get_json(f"{V1}/league/{league_id}/transactions/{week}")
        for raw in raw_list:
            if raw.get("type") != "waiver" or raw.get("status") != "complete":
                continue
            adds = raw.get("adds") or {}
            if not adds:
                continue
            roster_id = int(raw["roster_ids"][0])
            player_id = next(iter(adds))
            bid = ((raw.get("settings") or {}).get("waiver_bid")) or 0.0
            out.append(WaiverClaim(
                transaction_id=raw["transaction_id"], season=season, week=week,
                roster_id=roster_id, player_id=player_id,
                bid_amount=float(bid), created_ms=int(raw["created"]), won=True))
    return out


def fetch_all_claims(
    sleeper: SleeperClient, league_id: str, *, season: int, through_week: int,
) -> list[WaiverClaim]:
    """Every FAAB waiver-type transaction, win or loss -- for
    api.waiver_ledger, which needs to know about losses too. Scan only
    through nfl.week (never a stats-final through_week -- the #5 lesson):
    any non-"complete" status seen for an already-processed week is a real,
    final loss, not a still-pending claim."""
    out: list[WaiverClaim] = []
    for week in range(1, through_week + 1):
        raw_list = sleeper.get_json(f"{V1}/league/{league_id}/transactions/{week}")
        for raw in raw_list:
            if raw.get("type") != "waiver":
                continue
            adds = raw.get("adds") or {}
            if not adds:
                continue
            roster_id = int(raw["roster_ids"][0])
            player_id = next(iter(adds))
            bid = ((raw.get("settings") or {}).get("waiver_bid")) or 0.0
            out.append(WaiverClaim(
                transaction_id=raw["transaction_id"], season=season, week=week,
                roster_id=roster_id, player_id=player_id,
                bid_amount=float(bid), created_ms=int(raw["created"]),
                won=raw.get("status") == "complete"))
    return out


def remaining_budget(
    claims: Sequence[WaiverClaim], *, waiver_budget: float,
) -> dict[int, float]:
    """Sum each roster's bid amounts across all claims and subtract from waiver_budget."""
    spent: dict[int, float] = {}
    for claim in claims:
        spent[claim.roster_id] = spent.get(claim.roster_id, 0.0) + claim.bid_amount
    return {roster_id: waiver_budget - total for roster_id, total in spent.items()}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/ingest/sleeper/test_waivers.py -v`
Expected: PASS (all existing tests plus the 3 new ones)

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/domain/models.py src/ffdo/ingest/sleeper/waivers.py tests/ingest/sleeper/test_waivers.py
git commit -m "feat: fetch_all_claims -- capture lost waiver claims too, for the ledger"
```

---

### Task 6: Wire `waiver_ledger` into `GET /api/leagues/{league_key}/waivers`

**Files:**
- Modify: `src/ffdo/api/app.py`
- Test: `tests/api/test_app.py` (or the existing waiver-endpoint test file if separate -- find it with `grep -rl "def test.*waiver" tests/api/` before writing new tests, and add to that file)

**Interfaces:**
- Consumes: `WaiverLedger` (Task 4), `waivers_mod.fetch_all_claims` (Task 5), the existing `recommendations` list already computed in `get_waivers`.
- Produces: nothing new consumed by later tasks in this plan (Task 11 reads the ledger directly via `_WAIVER_LEDGER.list_for_league`, called from Task 13's new endpoint).

- [ ] **Step 1: Find the exact existing test file for this endpoint**

Run: `grep -rl "waivers" tests/api/*.py`

Add the new test below to whichever file already covers `GET /api/leagues/{league_key}/waivers` (do not create a new test file if one already exists for this endpoint).

- [ ] **Step 2: Write the failing test**

This test drives the app through its public HTTP interface (the same style the existing waiver-endpoint tests already use -- match their exact fixture/client setup, since this plan cannot see that file's fixtures without you reading it first). The behavior under test: a second call to `GET /waivers` for the same league does not re-record an already-ledgered claim, and a claim matching this week's recommendations gets `recommended_bid`/`predicted_vor_gain` populated.

```python
def test_get_waivers_records_your_own_claims_into_the_ledger(client_with_league):
    # Uses whatever fixture this test file's existing waiver tests use to
    # get a TestClient wired to a league with a real completed FAAB claim
    # for the tracked roster in the mocked Sleeper responses -- extend that
    # existing fixture's mocked transactions to include one, rather than
    # building a new one, so this test exercises the exact same endpoint
    # path the other waiver tests already cover.
    resp = client_with_league.get("/api/leagues/league1:L1:2026/waivers")
    assert resp.status_code == 200
    # A second call must not fail or duplicate -- record_if_absent is
    # idempotent, and this endpoint calls it on every request.
    resp2 = client_with_league.get("/api/leagues/league1:L1:2026/waivers")
    assert resp2.status_code == 200
```

(This step's exact fixture wiring depends on the existing test file's conventions -- read `tests/api/test_app.py`'s waiver-endpoint tests first, then adapt the mocked transaction payload to include a `type: "waiver", status: "complete"` entry for the tracked roster, matching this task's Step 3 code below.)

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_app.py -k waivers -v`
Expected: passes trivially before the change (the endpoint doesn't yet touch the ledger) -- this task is additive, so there is no natural "red" state for this specific test beyond confirming the fixture itself loads. Proceed to the implementation and re-run after.

- [ ] **Step 4: Write the implementation**

In `src/ffdo/api/app.py`, add the import and module-level singleton near the existing ledgers:

```python
from ffdo.api.waiver_ledger import WaiverLedger
```

```python
_WAIVER_LEDGER = WaiverLedger(Path("data") / "ffdo.db")
```

In `get_waivers`, the existing fetch block is:

```python
            claims = waivers_mod.fetch_waivers(
                sleeper, lg.provider_league_id, season=lg.season, through_week=nfl.week)
            budgets = waivers_mod.remaining_budget(claims, waiver_budget=waiver_budget)

            all_player_ids = set(profiles)
```

Add a second fetch right after it, still inside the same `try` block (before `sleeper.close()` in `finally`):

```python
            claims = waivers_mod.fetch_waivers(
                sleeper, lg.provider_league_id, season=lg.season, through_week=nfl.week)
            budgets = waivers_mod.remaining_budget(claims, waiver_budget=waiver_budget)

            # For the ledger (spec: outcome scorecard) -- every claim for
            # the tracked roster, win or loss, not just the winning ones
            # `claims` above is scoped to.
            your_claims = (
                [c for c in waivers_mod.fetch_all_claims(
                    sleeper, lg.provider_league_id, season=lg.season, through_week=nfl.week)
                 if c.roster_id == lg.roster_id]
                if lg.roster_id is not None else [])

            all_player_ids = set(profiles)
```

After `recommendations = waiver_value_mod.recommend_adds(...)` and before the `return` statement, add the recording step. The existing tail is:

```python
        recommendations = waiver_value_mod.recommend_adds(
            free_agent_ids, you.player_ids, valued, profiles, lg,
            FAAB_BID_CURVE, your_remaining)

        return {
            "remaining_budget": round(your_remaining, 1),
            "recommendations": [
                {"free_agent_id": r.free_agent_id, "drop_player_id": r.drop_player_id,
                 "vor_gain": round(r.vor_gain, 1), "suggested_bid": r.suggested_bid}
                for r in recommendations
            ],
        }
```

Replace it with:

```python
        recommendations = waiver_value_mod.recommend_adds(
            free_agent_ids, you.player_ids, valued, profiles, lg,
            FAAB_BID_CURVE, your_remaining)

        # A claim only matches a recommendation if it happened THIS week
        # (recommend_adds only ever returns the current week's top-N) --
        # older claims are recorded with recommended_bid/predicted_vor_gain
        # as None, an accepted, disclosed limitation: past weeks'
        # recommendations were never persisted before this ledger existed,
        # so they cannot be reconstructed now.
        recommended_by_player = {r.free_agent_id: r for r in recommendations}
        for claim in your_claims:
            rec = recommended_by_player.get(claim.player_id)
            _WAIVER_LEDGER.record_if_absent(
                lg.league_key, transaction_id=claim.transaction_id, season=claim.season,
                week=claim.week, roster_id=claim.roster_id, add_player_id=claim.player_id,
                drop_player_id=rec.drop_player_id if rec else None,
                recommended_bid=rec.suggested_bid if rec else None,
                actual_bid=int(claim.bid_amount),
                predicted_vor_gain=rec.vor_gain if rec else None,
                won=claim.won)

        return {
            "remaining_budget": round(your_remaining, 1),
            "recommendations": [
                {"free_agent_id": r.free_agent_id, "drop_player_id": r.drop_player_id,
                 "vor_gain": round(r.vor_gain, 1), "suggested_bid": r.suggested_bid}
                for r in recommendations
            ],
        }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_app.py -k waivers -v`
Expected: PASS, including the new idempotency test

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/api/app.py tests/api/test_app.py
git commit -m "feat: record your own waiver claims into the decision ledger"
```

---

### Task 7: `api/draft_pick_ledger.py` — new decision ledger

**Files:**
- Create: `src/ffdo/api/draft_pick_ledger.py`
- Test: `tests/api/test_draft_pick_ledger.py`

**Interfaces:**
- Consumes: stdlib only.
- Produces: `DraftPickLedgerEntry` (frozen dataclass: `league_key, draft_id, pick_no, round, roster_id, player_id, position, amount, grade, vor_at_pick, predicted_survival, recorded_at`), `DraftPickLedger(path).record_if_absent(league_key, *, draft_id, pick_no, round, roster_id, player_id, position, amount, grade, vor_at_pick, predicted_survival) -> DraftPickLedgerEntry`, `.get(league_key, draft_id, pick_no) -> DraftPickLedgerEntry | None`, `.list_for_league(league_key) -> list[DraftPickLedgerEntry]`. Task 8 wires this into `app.py`'s live draft-board endpoint; Task 12 consumes `list_for_league`'s output.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_draft_pick_ledger.py
from ffdo.api.draft_pick_ledger import DraftPickLedger


def _kwargs(**overrides):
    base = dict(
        draft_id="d1", pick_no=5, round=1, roster_id=3, player_id="p1",
        position="RB", amount=None, grade="GOOD", vor_at_pick=42.5,
        predicted_survival=0.35,
    )
    base.update(overrides)
    return base


def test_record_if_absent_persists_and_returns_the_entry(tmp_path):
    ledger = DraftPickLedger(tmp_path / "test.db")
    entry = ledger.record_if_absent("league1", **_kwargs())
    assert entry.pick_no == 5
    assert entry.round == 1
    assert entry.roster_id == 3
    assert entry.player_id == "p1"
    assert entry.position == "RB"
    assert entry.grade == "GOOD"
    assert entry.vor_at_pick == 42.5
    assert entry.predicted_survival == 0.35
    assert entry.recorded_at


def test_record_if_absent_is_idempotent(tmp_path):
    ledger = DraftPickLedger(tmp_path / "test.db")
    first = ledger.record_if_absent("league1", **_kwargs())
    second = ledger.record_if_absent("league1", **_kwargs(grade="POOR"))
    assert second.recorded_at == first.recorded_at
    assert second.grade == "GOOD"  # second call's values ignored -- write-once


def test_different_picks_in_the_same_draft_are_both_recorded(tmp_path):
    ledger = DraftPickLedger(tmp_path / "test.db")
    ledger.record_if_absent("league1", **_kwargs(pick_no=1))
    ledger.record_if_absent("league1", **_kwargs(pick_no=2))
    result = ledger.list_for_league("league1")
    assert [e.pick_no for e in result] == [1, 2]


def test_get_returns_none_when_absent(tmp_path):
    ledger = DraftPickLedger(tmp_path / "test.db")
    assert ledger.get("league1", "d1", 1) is None


def test_list_for_league_scopes_to_the_given_league(tmp_path):
    ledger = DraftPickLedger(tmp_path / "test.db")
    ledger.record_if_absent("league1", **_kwargs())
    ledger.record_if_absent("league2", **_kwargs())
    result = ledger.list_for_league("league1")
    assert len(result) == 1
    assert result[0].league_key == "league1"


def test_ungraded_pick_supports_null_fields(tmp_path):
    """A pick recorded via the early-return (draft already complete on
    first poll) path has no grade/vor/survival available yet."""
    ledger = DraftPickLedger(tmp_path / "test.db")
    entry = ledger.record_if_absent(
        "league1", draft_id="d1", pick_no=1, round=1, roster_id=None,
        player_id="p1", position=None, amount=None, grade=None,
        vor_at_pick=None, predicted_survival=None)
    assert entry.grade is None
    assert entry.vor_at_pick is None
    assert entry.predicted_survival is None
    assert entry.roster_id is None


def test_corrupt_db_is_tolerated_as_empty(tmp_path):
    db_path = tmp_path / "test.db"
    db_path.write_bytes(b"not a real sqlite file")
    ledger = DraftPickLedger(db_path)
    assert ledger.get("league1", "d1", 1) is None
    assert ledger.list_for_league("league1") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_draft_pick_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.api.draft_pick_ledger'`

- [ ] **Step 3: Write the implementation**

```python
# src/ffdo/api/draft_pick_ledger.py
"""Decision ledger for real draft picks -- new in sub-project #7. Nothing
before this persisted a draft's picks once the draft completed: DraftState
is fetched live from Sleeper and discarded, and engine.grading's grades
were recomputed fresh on every board poll, never stored. Without this
ledger the outcome scorecard would have nothing to show for the Draft
metric after a draft ends.

Recorded incrementally as the live draft board is polled (see api/app.py's
get_board), one row per pick, the moment that pick is first seen --
record_if_absent means re-polling mid-draft never duplicates a row. A pick
seen for the first time via the "draft already complete" early-return path
(app.py) is recorded with grade/vor_at_pick/predicted_survival all None --
better than permanently losing that pick, even though it can never be
graded after the fact (grading needs the live valuation context that path
deliberately skips).

Same connection pattern as the other ledgers: one file, stdlib sqlite3, no
ORM, corrupt-DB tolerance (treat as empty)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class DraftPickLedgerEntry:
    league_key: str
    draft_id: str
    pick_no: int
    round: int
    roster_id: int | None
    player_id: str
    position: str | None
    amount: int | None
    grade: str | None
    vor_at_pick: float | None
    predicted_survival: float | None
    recorded_at: str


class DraftPickLedger:
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
                CREATE TABLE IF NOT EXISTS draft_pick_record (
                    league_key TEXT NOT NULL,
                    draft_id TEXT NOT NULL,
                    pick_no INTEGER NOT NULL,
                    round INTEGER NOT NULL,
                    roster_id INTEGER,
                    player_id TEXT NOT NULL,
                    position TEXT,
                    amount INTEGER,
                    grade TEXT,
                    vor_at_pick REAL,
                    predicted_survival REAL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (league_key, draft_id, pick_no)
                );
                """
            )
            conn.commit()
        except sqlite3.DatabaseError:
            pass

    def get(self, league_key: str, draft_id: str, pick_no: int) -> DraftPickLedgerEntry | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM draft_pick_record "
                    "WHERE league_key = ? AND draft_id = ? AND pick_no = ?",
                    (league_key, draft_id, pick_no),
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        return self._row_to_entry(row) if row is not None else None

    def record_if_absent(
        self,
        league_key: str,
        *,
        draft_id: str,
        pick_no: int,
        round: int,
        roster_id: int | None,
        player_id: str,
        position: str | None,
        amount: int | None,
        grade: str | None,
        vor_at_pick: float | None,
        predicted_survival: float | None,
    ) -> DraftPickLedgerEntry:
        existing = self.get(league_key, draft_id, pick_no)
        if existing is not None:
            return existing
        recorded_at = _now()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO draft_pick_record "
                    "(league_key, draft_id, pick_no, round, roster_id, player_id, position, "
                    " amount, grade, vor_at_pick, predicted_survival, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (league_key, draft_id, pick_no, round, roster_id, player_id, position,
                     amount, grade, vor_at_pick, predicted_survival, recorded_at),
                )
                conn.commit()
        except sqlite3.DatabaseError:
            pass
        result = self.get(league_key, draft_id, pick_no)
        return result if result is not None else DraftPickLedgerEntry(
            league_key=league_key, draft_id=draft_id, pick_no=pick_no, round=round,
            roster_id=roster_id, player_id=player_id, position=position, amount=amount,
            grade=grade, vor_at_pick=vor_at_pick, predicted_survival=predicted_survival,
            recorded_at=recorded_at)

    def list_for_league(self, league_key: str) -> list[DraftPickLedgerEntry]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM draft_pick_record WHERE league_key = ? "
                    "ORDER BY draft_id ASC, pick_no ASC",
                    (league_key,),
                ).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [self._row_to_entry(row) for row in rows]

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> DraftPickLedgerEntry:
        return DraftPickLedgerEntry(
            league_key=row["league_key"], draft_id=row["draft_id"], pick_no=row["pick_no"],
            round=row["round"], roster_id=row["roster_id"], player_id=row["player_id"],
            position=row["position"], amount=row["amount"], grade=row["grade"],
            vor_at_pick=row["vor_at_pick"], predicted_survival=row["predicted_survival"],
            recorded_at=row["recorded_at"],
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_draft_pick_ledger.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/api/draft_pick_ledger.py tests/api/test_draft_pick_ledger.py
git commit -m "feat: api.draft_pick_ledger -- persists real draft picks, nothing did before"
```

---

### Task 8: Wire `draft_pick_ledger` into the live draft board endpoint

**Files:**
- Modify: `src/ffdo/api/app.py`
- Test: `tests/api/test_app.py` (find the existing `get_board`/`/board` test coverage with `grep -rl "def test.*board" tests/api/` and add to that file)

**Interfaces:**
- Consumes: `DraftPickLedger` (Task 7); `grading.grade_auction_pick`/`grading.grade_snake_pick` (existing, `engine/grading.py`).
- Produces: nothing consumed by later tasks in this plan (Task 12 reads the ledger directly).

This task has a real edge case worth understanding before writing code: `get_board` early-returns (before computing valuations/grades) the moment `state.status == "complete"`, to avoid loading Sleeper's post-kickoff-contaminated projections feed for no reason. If the very last pick of a draft and the status flip to `"complete"` both show up in the SAME poll, that poll takes the early-return path and would otherwise never see that pick at all. The fix: record every not-yet-seen pick in BOTH branches -- fully graded in the normal (in-progress) path, and with `grade`/`vor_at_pick`/`predicted_survival` all `None` in the early-return path (better than silently losing the pick). **Because this endpoint is polled live during an active draft, the controller running this plan should manually smoke-test an actual live or mock draft through to completion** and confirm every real pick shows up in `draft_pick_record` afterward -- per this project's established pattern (sub-projects #3 and #5's most serious bugs were both found this way, not by automated review).

- [ ] **Step 1: Find the exact existing test coverage for `/board`**

Run: `grep -rl "def test.*board\|/board\"" tests/api/*.py`

- [ ] **Step 2: Write the failing tests**

Add to whichever file already covers `GET /api/leagues/{league_key}/board` (match its existing fixture/mocking conventions for Sleeper draft responses -- this plan cannot see that file's exact fixtures without you reading it first):

```python
def test_get_board_records_picks_into_the_draft_pick_ledger(client_with_live_draft):
    # Uses this file's existing fixture for a live (in-progress) snake or
    # auction draft with at least one real pick already made.
    resp = client_with_live_draft.get("/api/leagues/league1:L1:2026/board")
    assert resp.status_code == 200
    # A second poll must not fail or duplicate a row -- record_if_absent
    # is idempotent and this endpoint calls it on every poll.
    resp2 = client_with_live_draft.get("/api/leagues/league1:L1:2026/board")
    assert resp2.status_code == 200


def test_get_board_records_ungraded_picks_when_draft_is_already_complete(client_with_complete_draft):
    # Uses this file's existing fixture for a draft whose status is already
    # "complete" on first poll -- the early-return path.
    resp = client_with_complete_draft.get("/api/leagues/league1:L1:2026/board")
    assert resp.status_code == 200
    assert resp.json()["draft_status"] == "complete"
```

- [ ] **Step 3: Run the tests to confirm the fixtures load (they will pass trivially before the ledger wiring -- this task is additive)**

Run: `uv run pytest tests/api/test_app.py -k "board and ledger" -v`
Expected: PASS on the bare HTTP behavior (200 status); proceed to Step 4, then verify the ledger side-effect directly in Step 6.

- [ ] **Step 4: Write the implementation**

In `src/ffdo/api/app.py`, add the import and module-level singleton:

```python
from ffdo.api.draft_pick_ledger import DraftPickLedger
```

```python
_DRAFT_PICK_LEDGER = DraftPickLedger(Path("data") / "ffdo.db")
# In-memory only, per draft_id -- the last known live simulate_survival()
# estimate for each player while they were still available. A player who
# gets drafted between two polls drops out of the NEXT poll's `survival`
# dict, so this must be updated with dict.update (merge), never replaced
# wholesale, or the very last known value for a just-drafted player would
# be lost the moment he's no longer "available". Process-lifetime only,
# same as every other in-memory cache in this module (e.g. nfl_state_cache)
# -- resets on restart, which is fine: it only ever backfills a value that
# would otherwise be None.
_DRAFT_SURVIVAL_CACHE: dict[str, dict[str, float]] = {}
```

In `get_board`, the early-return branch currently reads:

```python
                state = draft_mod.parse(draft_meta, picks_raw)
                if state.status == "complete":
                    # The season screen (src/ffdo/web/season/season.js) takes over
                    # once draft_status flips to "complete" -- board.js never
                    # renders this board data in that case, and Sleeper's
                    # projections feed is reliably contaminated post-kickoff
                    # (`_load_projections` raises on it), so loading it here just
                    # to throw the result away would 500 every real league whose
                    # draft happened before the app was reopened. See get_season
                    # for the actual season-view data.
                    _STORE.touch_status(league_key, state.status)
                    return {"draft_status": state.status, "is_mock": is_mock}
```

Replace it with (adding the ungraded-fallback recording, guarded to real drafts only):

```python
                state = draft_mod.parse(draft_meta, picks_raw)
                if state.status == "complete":
                    # The season screen (src/ffdo/web/season/season.js) takes over
                    # once draft_status flips to "complete" -- board.js never
                    # renders this board data in that case, and Sleeper's
                    # projections feed is reliably contaminated post-kickoff
                    # (`_load_projections` raises on it), so loading it here just
                    # to throw the result away would 500 every real league whose
                    # draft happened before the app was reopened. See get_season
                    # for the actual season-view data.
                    _STORE.touch_status(league_key, state.status)
                    if not is_mock:
                        # This path never loads valuations, so any pick(s)
                        # first seen here (the final pick and a status flip
                        # to "complete" landing in the same poll) get
                        # recorded ungraded rather than lost entirely.
                        for pick in state.picks:
                            _DRAFT_PICK_LEDGER.record_if_absent(
                                league_key, draft_id=state.draft_id, pick_no=pick.pick_no,
                                round=pick.round, roster_id=pick.roster_id,
                                player_id=pick.player_id, position=None, amount=pick.amount,
                                grade=None, vor_at_pick=None, predicted_survival=None)
                    return {"draft_status": state.status, "is_mock": is_mock}
```

Near the end of `get_board`, the snake branch currently reads:

```python
            picks_until = lg.num_teams  # conservative: one full round
            survival = market.simulate_survival(adp_means, available, picks_until)
            cow = market.cost_of_waiting(valued, survival, available)
```

Add a cache update right after `survival` is computed:

```python
            picks_until = lg.num_teams  # conservative: one full round
            survival = market.simulate_survival(adp_means, available, picks_until)
            _DRAFT_SURVIVAL_CACHE.setdefault(state.draft_id, {}).update(survival)
            cow = market.cost_of_waiting(valued, survival, available)
```

Finally, right before `board["is_mock"] = is_mock; return board` (the very end of the function), add the fully-graded recording step for both auction and snake:

```python
        board["is_mock"] = is_mock
        return board
```

becomes:

```python
        if not is_mock:
            from ffdo.engine import grading as grading_mod
            for pick in state.picks:
                if _DRAFT_PICK_LEDGER.get(lg.league_key, state.draft_id, pick.pick_no) is not None:
                    continue
                vp = valued.get(pick.player_id)
                vor_at_pick = round(vp.vor, 2) if vp is not None else None
                position = profiles[pick.player_id].position if pick.player_id in profiles else None
                if state.draft_type == "auction":
                    grade = None
                    if pick.amount is not None and vp is not None:
                        base = baseline.get(pick.player_id, 1.0)
                        grade = grading_mod.grade_auction_pick(base, pick.amount)
                    predicted_survival = None
                else:
                    grade = None
                    if vp is not None:
                        drafted_before = {
                            p.player_id for p in state.picks if p.pick_no < pick.pick_no}
                        alternatives = [
                            other.vor for pid, other in valued.items()
                            if other.vor > 0 and pid != pick.player_id and pid not in drafted_before
                        ]
                        grade = grading_mod.grade_snake_pick(vp.vor, alternatives)
                    predicted_survival = _DRAFT_SURVIVAL_CACHE.get(state.draft_id, {}).get(
                        pick.player_id)
                _DRAFT_PICK_LEDGER.record_if_absent(
                    lg.league_key, draft_id=state.draft_id, pick_no=pick.pick_no,
                    round=pick.round, roster_id=pick.roster_id, player_id=pick.player_id,
                    position=position, amount=pick.amount, grade=grade,
                    vor_at_pick=vor_at_pick, predicted_survival=predicted_survival)

        board["is_mock"] = is_mock
        return board
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_app.py -k board -v`
Expected: PASS (all existing board tests plus the 2 new ones)

- [ ] **Step 6: Manually verify against a real or mock draft**

This is the load-bearing check this task's design note calls for. Start the app, open a real (or Sleeper mock) draft through to completion while polling `/board`, then query the SQLite file directly:

```bash
sqlite3 data/ffdo.db "SELECT pick_no, round, player_id, grade, predicted_survival FROM draft_pick_record ORDER BY pick_no;"
```

Confirm every real pick made appears exactly once, with no gaps in `pick_no`, and that snake-draft picks show a non-null `grade` (auction-draft picks with no `amount` recorded are expected to show `grade IS NULL`, per `grading.grade_auction_pick`'s existing "no price, no grade" behavior — unchanged from `board.py`).

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/api/app.py tests/api/test_app.py
git commit -m "feat: persist real draft picks + grades into the ledger as the board is polled"
```

---

### Task 9: `engine/scorecard.py` — lineup metric

**Files:**
- Modify: `src/ffdo/api/lineup_ledger.py` (add `list_for_league`)
- Create: `src/ffdo/engine/scorecard.py`
- Test: `tests/api/test_lineup_ledger.py` (add to the existing file), `tests/engine/test_scorecard.py`

**Interfaces:**
- Consumes: `historical_weekly_stats.fetch`'s return shape (`dict[str, dict[str, float]]`, existing), `scoring.score_stats` (existing).
- Produces: `LineupLedger.list_for_league(league_key) -> list[LineupRecord]`; `scorecard.LineupOutcome` (frozen dataclass: `season, week, recommended, actual, followed`); `scorecard.lineup_metric(outcomes, weekly_points, scoring_settings) -> dict`. Task 13 builds `LineupOutcome`s from `LineupRecord`s and calls this.

- [ ] **Step 1: Write the failing test for `list_for_league`**

Add to `tests/api/test_lineup_ledger.py` (read it first to match its existing fixture style -- likely `tmp_path`-based, matching every other ledger test in this codebase):

```python
def test_list_for_league_scopes_to_the_given_league(tmp_path):
    ledger = LineupLedger(tmp_path / "test.db")
    ledger.record_if_absent("league1", 2026, 1, {0: "p1"}, ("p1",))
    ledger.record_if_absent("league2", 2026, 1, {0: "p9"}, ("p9",))
    result = ledger.list_for_league("league1")
    assert len(result) == 1
    assert result[0].league_key == "league1"


def test_list_for_league_orders_by_season_then_week(tmp_path):
    ledger = LineupLedger(tmp_path / "test.db")
    ledger.record_if_absent("league1", 2026, 3, {0: "p1"}, ("p1",))
    ledger.record_if_absent("league1", 2026, 1, {0: "p2"}, ("p2",))
    result = ledger.list_for_league("league1")
    assert [r.week for r in result] == [1, 3]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_lineup_ledger.py -k list_for_league -v`
Expected: FAIL with `AttributeError: 'LineupLedger' object has no attribute 'list_for_league'`

- [ ] **Step 3: Add `list_for_league` to `LineupLedger`**

In `src/ffdo/api/lineup_ledger.py`, add this method to the `LineupLedger` class (matching the `list_for_league` already present in `TradeLedger`, `WaiverLedger`, and `DraftPickLedger`):

```python
    def list_for_league(self, league_key: str) -> list[LineupRecord]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM lineup_recommendation WHERE league_key = ? "
                    "ORDER BY season ASC, week ASC",
                    (league_key,),
                ).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [self._row_to_record(row) for row in rows]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/api/test_lineup_ledger.py -v`
Expected: PASS (all existing tests plus the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/api/lineup_ledger.py tests/api/test_lineup_ledger.py
git commit -m "feat: LineupLedger.list_for_league -- needed by the outcome scorecard"
```

- [ ] **Step 6: Write the failing tests for `scorecard.lineup_metric`**

```python
# tests/engine/test_scorecard.py
from ffdo.engine import scorecard


def test_lineup_metric_counts_followed_status():
    outcomes = [
        scorecard.LineupOutcome(season=2026, week=1, recommended={}, actual=(), followed="full"),
        scorecard.LineupOutcome(season=2026, week=2, recommended={}, actual=(), followed="partial"),
        scorecard.LineupOutcome(season=2026, week=3, recommended={}, actual=(), followed="none"),
    ]
    result = scorecard.lineup_metric(outcomes, {}, {})
    assert result["weeks_resolved"] == 3
    assert result["weeks_full"] == 1
    assert result["weeks_partial"] == 1
    assert result["weeks_none"] == 1


def test_lineup_metric_sums_points_left_on_the_bench_when_not_followed():
    outcomes = [
        scorecard.LineupOutcome(
            season=2026, week=1, recommended={0: "recommended_p"},
            actual=("actual_p",), followed="none"),
    ]
    weekly_points = {
        (2026, 1): {
            "recommended_p": {"rec_yd": 100.0},  # 100 yards -> 10.0 pts at 0.1/yd
            "actual_p": {"rec_yd": 20.0},         # 20 yards -> 2.0 pts
        },
    }
    scoring_settings = {"rec_yd": 0.1}
    result = scorecard.lineup_metric(outcomes, weekly_points, scoring_settings)
    assert result["points_left_on_bench"] == 8.0


def test_lineup_metric_fully_followed_weeks_contribute_no_points_left():
    outcomes = [
        scorecard.LineupOutcome(
            season=2026, week=1, recommended={0: "p1"}, actual=("p1",), followed="full"),
    ]
    result = scorecard.lineup_metric(outcomes, {}, {})
    assert result["points_left_on_bench"] == 0.0


def test_lineup_metric_missing_weekly_stats_defaults_to_zero_points():
    outcomes = [
        scorecard.LineupOutcome(
            season=2026, week=1, recommended={0: "p1"}, actual=("p2",), followed="none"),
    ]
    result = scorecard.lineup_metric(outcomes, {}, {})  # no weekly_points entry for (2026, 1)
    assert result["points_left_on_bench"] == 0.0


def test_lineup_metric_empty_outcomes():
    result = scorecard.lineup_metric([], {}, {})
    assert result == {
        "weeks_resolved": 0, "weeks_full": 0, "weeks_partial": 0,
        "weeks_none": 0, "points_left_on_bench": 0.0,
    }
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `uv run pytest tests/engine/test_scorecard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.engine.scorecard'`

- [ ] **Step 8: Write the implementation**

```python
# src/ffdo/engine/scorecard.py
"""Outcome scorecard: how good FFDO's recommendations have actually been,
per recommendation type. Pure functions over lightweight outcome
dataclasses this module defines itself -- NOT the ledgers' own
storage-shaped dataclasses (api.lineup_ledger.LineupRecord etc.), since
engine/ modules depend only on domain/ and other engine/ modules, never
api/. api.app.py (the composition root) converts ledger rows into these
outcome objects before calling in here, the same way it already shapes
every other endpoint's JSON response.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ffdo.engine.scoring import score_stats


@dataclass(frozen=True, slots=True)
class LineupOutcome:
    season: int
    week: int
    recommended: Mapping[int, str | None]
    actual: tuple[str | None, ...]
    followed: str  # "full" | "partial" | "none" -- only resolved rows are passed in


def lineup_metric(
    outcomes: Sequence[LineupOutcome],
    weekly_points: Mapping[tuple[int, int], Mapping[str, dict[str, float]]],
    scoring_settings: Mapping[str, float],
) -> dict:
    """weekly_points is keyed (season, week) -> {player_id: raw stat dict},
    the exact shape ingest.sleeper.historical_weekly_stats.fetch returns --
    the caller fetches only the (season, week) pairs these outcomes
    actually need, not every week that ever existed."""
    full = sum(1 for o in outcomes if o.followed == "full")
    partial = sum(1 for o in outcomes if o.followed == "partial")
    none_ = sum(1 for o in outcomes if o.followed == "none")

    points_left = 0.0
    for o in outcomes:
        if o.followed == "full":
            continue
        stats = weekly_points.get((o.season, o.week), {})
        for i, want in o.recommended.items():
            got = o.actual[i] if i < len(o.actual) else None
            if got == want or want is None:
                continue
            want_pts = score_stats(stats[want], scoring_settings) if want in stats else 0.0
            got_pts = (score_stats(stats[got], scoring_settings)
                      if got is not None and got in stats else 0.0)
            points_left += max(0.0, want_pts - got_pts)

    return {
        "weeks_resolved": len(outcomes),
        "weeks_full": full, "weeks_partial": partial, "weeks_none": none_,
        "points_left_on_bench": round(points_left, 1),
    }
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/test_scorecard.py -v`
Expected: PASS (5 tests)

- [ ] **Step 10: Commit**

```bash
git add src/ffdo/engine/scorecard.py tests/engine/test_scorecard.py
git commit -m "feat: engine.scorecard.lineup_metric -- weeks followed + points left on the bench"
```

---

### Task 10: `engine/scorecard.py` — trade metric

**Files:**
- Modify: `src/ffdo/engine/scorecard.py`
- Test: `tests/engine/test_scorecard.py`

**Interfaces:**
- Consumes: nothing new (pure dataclass math, same as Task 9).
- Produces: `scorecard.TradeOutcome` (frozen dataclass: `transaction_id, roster_a_id, roster_b_id, side_a_value_at_trade, side_b_value_at_trade, side_a_current_value, side_b_current_value`), `scorecard.trade_metric(outcomes, your_roster_id) -> dict`. Task 13 builds `TradeOutcome`s from `TradeLedgerEntry`s (mirroring the same current-value recompute `GET /trades` already does) and calls this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/engine/test_scorecard.py`:

```python
def test_trade_metric_scopes_to_your_own_trades():
    outcomes = [
        scorecard.TradeOutcome(
            transaction_id="t1", roster_a_id=1, roster_b_id=2,
            side_a_value_at_trade=10.0, side_b_value_at_trade=10.0,
            side_a_current_value=15.0, side_b_current_value=10.0),
        scorecard.TradeOutcome(
            transaction_id="t2", roster_a_id=3, roster_b_id=4,  # not your trade
            side_a_value_at_trade=10.0, side_b_value_at_trade=10.0,
            side_a_current_value=5.0, side_b_current_value=10.0),
    ]
    result = scorecard.trade_metric(outcomes, your_roster_id=1)
    assert result["total_trades_in_league"] == 2
    assert result["your_trades"] == 1


def test_trade_metric_classifies_gained_lost_unchanged():
    outcomes = [
        scorecard.TradeOutcome(
            transaction_id="gained", roster_a_id=1, roster_b_id=2,
            side_a_value_at_trade=10.0, side_b_value_at_trade=10.0,
            side_a_current_value=20.0, side_b_current_value=10.0),
        scorecard.TradeOutcome(
            transaction_id="lost", roster_a_id=1, roster_b_id=2,
            side_a_value_at_trade=10.0, side_b_value_at_trade=10.0,
            side_a_current_value=2.0, side_b_current_value=10.0),
        scorecard.TradeOutcome(
            transaction_id="unchanged", roster_a_id=1, roster_b_id=2,
            side_a_value_at_trade=10.0, side_b_value_at_trade=10.0,
            side_a_current_value=10.2, side_b_current_value=10.0),
    ]
    result = scorecard.trade_metric(outcomes, your_roster_id=1)
    assert result["gained_value"] == 1
    assert result["lost_value"] == 1
    assert result["unchanged"] == 1


def test_trade_metric_reads_the_correct_side_when_you_are_roster_b():
    outcomes = [
        scorecard.TradeOutcome(
            transaction_id="t1", roster_a_id=1, roster_b_id=2,
            side_a_value_at_trade=10.0, side_b_value_at_trade=10.0,
            side_a_current_value=10.0, side_b_current_value=25.0),
    ]
    result = scorecard.trade_metric(outcomes, your_roster_id=2)
    assert result["gained_value"] == 1


def test_trade_metric_empty_outcomes():
    result = scorecard.trade_metric([], your_roster_id=1)
    assert result == {
        "total_trades_in_league": 0, "your_trades": 0,
        "gained_value": 0, "lost_value": 0, "unchanged": 0,
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/engine/test_scorecard.py -k trade -v`
Expected: FAIL with `AttributeError: module 'ffdo.engine.scorecard' has no attribute 'TradeOutcome'`

- [ ] **Step 3: Write the implementation**

Append to `src/ffdo/engine/scorecard.py`:

```python
@dataclass(frozen=True, slots=True)
class TradeOutcome:
    transaction_id: str
    roster_a_id: int
    roster_b_id: int
    side_a_value_at_trade: float
    side_b_value_at_trade: float
    side_a_current_value: float
    side_b_current_value: float


def trade_metric(outcomes: Sequence[TradeOutcome], your_roster_id: int) -> dict:
    your_trades = [o for o in outcomes if your_roster_id in (o.roster_a_id, o.roster_b_id)]
    gained = lost = unchanged = 0
    for o in your_trades:
        if o.roster_a_id == your_roster_id:
            at_trade, current = o.side_a_value_at_trade, o.side_a_current_value
        else:
            at_trade, current = o.side_b_value_at_trade, o.side_b_current_value
        delta = current - at_trade
        if delta > 0.5:
            gained += 1
        elif delta < -0.5:
            lost += 1
        else:
            unchanged += 1
    return {
        "total_trades_in_league": len(outcomes),
        "your_trades": len(your_trades),
        "gained_value": gained, "lost_value": lost, "unchanged": unchanged,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/test_scorecard.py -v`
Expected: PASS (all Task 9 + Task 10 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/scorecard.py tests/engine/test_scorecard.py
git commit -m "feat: engine.scorecard.trade_metric -- gained/lost value since trade time"
```

---

### Task 11: `engine/scorecard.py` — waiver metric

**Files:**
- Modify: `src/ffdo/engine/scorecard.py`
- Modify: `docs/superpowers/specs/2026-09-13-outcome-scorecard-design.md`
- Test: `tests/engine/test_scorecard.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `scorecard.WaiverOutcome` (frozen dataclass: `won, recommended_bid, actual_bid`), `scorecard.waiver_metric(outcomes) -> dict`. Task 13 builds `WaiverOutcome`s from `WaiverLedgerEntry`s and calls this.

This task deliberately narrows the spec's waiver metric. The spec's table lists "average realized VOR gain vs. predicted" alongside win rate and bid efficiency -- but "realized" VOR gain would require re-running a full league-wide valuation for the historical week each claim happened in (the same complexity class as the lineup metric's points-left computation, but for VOR, which additionally needs full replacement-level context, not just two players' stats). `predicted_vor_gain` is already captured in the ledger for a future sub-project to use; this task ships win rate and bid efficiency only, both fully computable from data the ledger already has with no extra ingest.

- [ ] **Step 1: Update the spec to match this scope trim**

In `docs/superpowers/specs/2026-09-13-outcome-scorecard-design.md`, find this row in the Scorecard Computation table:

```
| **Waiver** | Claim win rate, average realized VOR gain vs. predicted, bid efficiency (recommended vs. actual amount paid) | `waiver_ledger` rows |
```

Replace it with:

```
| **Waiver** | Claim win rate, bid efficiency (recommended vs. actual amount paid) -- `predicted_vor_gain` is captured in the ledger but a "realized VOR gain" comparison was cut from this sub-project's scope (it needs a full historical league-wide revaluation, not just the ledger's own stored fields) and left for a future follow-up | `waiver_ledger` rows |
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/engine/test_scorecard.py`:

```python
def test_waiver_metric_win_rate_over_recommended_claims_only():
    outcomes = [
        scorecard.WaiverOutcome(won=True, recommended_bid=10.0, actual_bid=10),
        scorecard.WaiverOutcome(won=False, recommended_bid=5.0, actual_bid=5),
        scorecard.WaiverOutcome(won=True, recommended_bid=None, actual_bid=20),  # not our rec
    ]
    result = scorecard.waiver_metric(outcomes)
    assert result["recommended_claims"] == 2
    assert result["win_rate"] == 0.5


def test_waiver_metric_bid_delta_averages_actual_minus_recommended():
    outcomes = [
        scorecard.WaiverOutcome(won=True, recommended_bid=10.0, actual_bid=15),
        scorecard.WaiverOutcome(won=True, recommended_bid=10.0, actual_bid=5),
    ]
    result = scorecard.waiver_metric(outcomes)
    assert result["avg_bid_delta"] == 0.0


def test_waiver_metric_no_recommended_claims_returns_none_rates():
    outcomes = [scorecard.WaiverOutcome(won=True, recommended_bid=None, actual_bid=8)]
    result = scorecard.waiver_metric(outcomes)
    assert result["recommended_claims"] == 0
    assert result["win_rate"] is None
    assert result["avg_bid_delta"] is None


def test_waiver_metric_empty_outcomes():
    result = scorecard.waiver_metric([])
    assert result == {"recommended_claims": 0, "win_rate": None, "avg_bid_delta": None}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/engine/test_scorecard.py -k waiver -v`
Expected: FAIL with `AttributeError: module 'ffdo.engine.scorecard' has no attribute 'WaiverOutcome'`

- [ ] **Step 4: Write the implementation**

Append to `src/ffdo/engine/scorecard.py`:

```python
@dataclass(frozen=True, slots=True)
class WaiverOutcome:
    won: bool
    recommended_bid: float | None
    actual_bid: int


def waiver_metric(outcomes: Sequence[WaiverOutcome]) -> dict:
    recommended = [o for o in outcomes if o.recommended_bid is not None]
    win_rate = (sum(1 for o in recommended if o.won) / len(recommended)
               if recommended else None)
    bid_deltas = [o.actual_bid - o.recommended_bid for o in recommended]
    avg_bid_delta = sum(bid_deltas) / len(bid_deltas) if bid_deltas else None
    return {
        "recommended_claims": len(recommended),
        "win_rate": round(win_rate, 3) if win_rate is not None else None,
        "avg_bid_delta": round(avg_bid_delta, 1) if avg_bid_delta is not None else None,
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/test_scorecard.py -v`
Expected: PASS (all Task 9 + 10 + 11 tests)

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/engine/scorecard.py tests/engine/test_scorecard.py docs/superpowers/specs/2026-09-13-outcome-scorecard-design.md
git commit -m "feat: engine.scorecard.waiver_metric -- win rate + bid efficiency"
```

---

### Task 12: `engine/scorecard.py` — draft metric

**Files:**
- Modify: `src/ffdo/engine/scorecard.py`
- Test: `tests/engine/test_scorecard.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `scorecard.DraftOutcome` (frozen dataclass: `grade`), `scorecard.draft_metric(outcomes) -> dict`. Task 13 builds `DraftOutcome`s from `DraftPickLedgerEntry` rows filtered to the tracked roster and calls this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/engine/test_scorecard.py`:

```python
def test_draft_metric_counts_grades():
    outcomes = [
        scorecard.DraftOutcome(grade="GREAT"),
        scorecard.DraftOutcome(grade="GREAT"),
        scorecard.DraftOutcome(grade="GOOD"),
        scorecard.DraftOutcome(grade="POOR"),
        scorecard.DraftOutcome(grade=None),  # ungraded, e.g. auction with no bid amount
    ]
    result = scorecard.draft_metric(outcomes)
    assert result["picks_graded"] == 4
    assert result["grade_counts"] == {"GREAT": 2, "GOOD": 1, "FAIR": 0, "POOR": 1}


def test_draft_metric_empty_outcomes():
    result = scorecard.draft_metric([])
    assert result == {
        "picks_graded": 0,
        "grade_counts": {"GREAT": 0, "GOOD": 0, "FAIR": 0, "POOR": 0},
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/engine/test_scorecard.py -k draft -v`
Expected: FAIL with `AttributeError: module 'ffdo.engine.scorecard' has no attribute 'DraftOutcome'`

- [ ] **Step 3: Write the implementation**

Append to `src/ffdo/engine/scorecard.py`:

```python
@dataclass(frozen=True, slots=True)
class DraftOutcome:
    grade: str | None


def draft_metric(outcomes: Sequence[DraftOutcome]) -> dict:
    graded = [o.grade for o in outcomes if o.grade is not None]
    counts = {g: graded.count(g) for g in ("GREAT", "GOOD", "FAIR", "POOR")}
    return {"picks_graded": len(graded), "grade_counts": counts}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/test_scorecard.py -v`
Expected: PASS (all Task 9-12 tests, full file)

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/scorecard.py tests/engine/test_scorecard.py
git commit -m "feat: engine.scorecard.draft_metric -- grade distribution for your own picks"
```

---

### Task 13: Wire `GET /api/leagues/{league_key}/scorecard`

**Files:**
- Modify: `src/ffdo/api/app.py`
- Test: `tests/api/test_app.py`

**Interfaces:**
- Consumes: `scorecard.lineup_metric/trade_metric/waiver_metric/draft_metric` and their `*Outcome` dataclasses (Tasks 9-12); `_LINEUP_LEDGER.list_for_league`, `_TRADE_LEDGER.list_for_league`, `_WAIVER_LEDGER.list_for_league`, `_DRAFT_PICK_LEDGER.list_for_league` (Tasks 4, 7, 9); `historical_weekly_stats.fetch` (existing, #6); `trade_value_mod.evaluate_trade`/`ros_value_mod.roster_value` (existing, reused the same way `get_trades` already uses them).
- Produces: the `GET /scorecard` endpoint. Task 14 (UI) calls it.

- [ ] **Step 1: Fix `historical_weekly_stats.py`'s now-stale docstring**

This module's docstring currently claims:

```python
"""Real per-week historical stats, from /v1/stats/nfl/regular/<season>/<week>
-- confirmed live to work for any past week, not just the current one
(unlike ingest.sleeper.weekly_projections, which is live-current-week
only). Used ONLY by scripts/fit_faab_curve.py to reconstruct what a
historical waiver claim was actually worth at the time -- never called
from the live app path."""
```

That last sentence becomes false the moment this task's `get_scorecard` endpoint calls it. Update it in `src/ffdo/ingest/sleeper/historical_weekly_stats.py`:

```python
"""Real per-week historical stats, from /v1/stats/nfl/regular/<season>/<week>
-- confirmed live to work for any past week, not just the current one
(unlike ingest.sleeper.weekly_projections, which is live-current-week
only). Used by scripts/fit_faab_curve.py to reconstruct what a historical
waiver claim was actually worth at the time, and by api.app's
GET /scorecard endpoint to score realized lineup-ledger outcomes against
what was actually recommended."""
```

- [ ] **Step 2: Write the failing test**

Read `tests/api/test_app.py`'s existing conventions for a full-endpoint test (likely reusing a fixture similar to the one backing `get_trades`/`get_waivers`'s tests) before writing this one, so it matches the file's exact fixture-building style:

```python
def test_get_scorecard_returns_all_four_metrics(client_with_league):
    resp = client_with_league.get("/api/leagues/league1:L1:2026/scorecard")
    assert resp.status_code == 200
    body = resp.json()
    assert "lineup" in body
    assert "trade" in body
    assert "waiver" in body
    assert "draft" in body


def test_get_scorecard_is_sleeper_only(client_with_espn_league):
    resp = client_with_espn_league.get("/api/leagues/league2:E1:2026/scorecard")
    assert resp.status_code == 400
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_app.py -k scorecard -v`
Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 4: Write the implementation**

In `src/ffdo/api/app.py`, add the imports near the other engine/ingest imports:

```python
from ffdo.engine import scorecard as scorecard_mod
from ffdo.ingest.sleeper import historical_weekly_stats as historical_weekly_stats_mod
```

Add the new endpoint, placed after `get_waivers` and before the static-mount block at the end of `create_app`:

```python
    @app.get("/api/leagues/{league_key}/scorecard")
    def get_scorecard(league_key: str) -> dict:
        """How good FFDO's recommendations have actually been, across all
        four recommendation types this app makes. Each metric degrades
        independently rather than failing the whole request -- an ESPN
        league (Sleeper-only gate below covers the whole endpoint, since
        none of the four ledgers have an ESPN ingest path yet) or a league
        with no waiver/trade/draft history yet still gets a 200 with
        whatever subset is computable."""
        lg = _load_league(league_key)
        if lg.provider != "sleeper":
            raise HTTPException(status_code=400, detail="Scorecard is Sleeper-only for now")

        lineup_records = _LINEUP_LEDGER.list_for_league(lg.league_key)
        resolved_lineup = [r for r in lineup_records if r.followed is not None]

        # Only fetch the specific (season, week) pairs the resolved ledger
        # rows actually need -- never the whole season's history.
        weekly_points: dict[tuple[int, int], dict[str, dict[str, float]]] = {}
        if resolved_lineup:
            sleeper = client_mod.SleeperClient()
            try:
                for season, week in {(r.season, r.week) for r in resolved_lineup}:
                    weekly_points[(season, week)] = historical_weekly_stats_mod.fetch(
                        sleeper, season, week)
            except (httpx.HTTPError, RuntimeError):
                # A stats-feed outage degrades this one metric's
                # points-left figure to 0.0 rather than failing the whole
                # scorecard -- the weeks-followed counts above are
                # unaffected, since they don't need this data.
                weekly_points = {}
            finally:
                sleeper.close()

        lineup_outcomes = [
            scorecard_mod.LineupOutcome(
                season=r.season, week=r.week, recommended=r.recommended,
                actual=r.actual, followed=r.followed)
            for r in resolved_lineup
        ]
        lineup_result = scorecard_mod.lineup_metric(
            lineup_outcomes, weekly_points, lg.scoring_settings)

        trade_entries = _TRADE_LEDGER.list_for_league(lg.league_key)
        trade_outcomes = []
        if trade_entries:
            sleeper = client_mod.SleeperClient()
            try:
                nfl = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))
                through_week = _through_week(nfl)
                actuals = actuals_mod.points_so_far(sleeper, lg.provider_league_id, through_week)
            except (httpx.HTTPError, RuntimeError):
                actuals = {}
            finally:
                sleeper.close()
            for entry in trade_entries:
                current_a_delta = sum(
                    actuals.get(pid, 0.0) - entry.banked_a_at_trade.get(pid, 0.0)
                    for pid in entry.roster_a_gets)
                current_b_delta = sum(
                    actuals.get(pid, 0.0) - entry.banked_b_at_trade.get(pid, 0.0)
                    for pid in entry.roster_b_gets)
                trade_outcomes.append(scorecard_mod.TradeOutcome(
                    transaction_id=entry.transaction_id,
                    roster_a_id=entry.roster_a_id, roster_b_id=entry.roster_b_id,
                    side_a_value_at_trade=entry.side_a_value_at_trade,
                    side_b_value_at_trade=entry.side_b_value_at_trade,
                    side_a_current_value=entry.side_a_value_at_trade + current_a_delta,
                    side_b_current_value=entry.side_b_value_at_trade + current_b_delta))
        trade_result = scorecard_mod.trade_metric(
            trade_outcomes, your_roster_id=lg.roster_id if lg.roster_id is not None else -1)

        waiver_entries = _WAIVER_LEDGER.list_for_league(lg.league_key)
        waiver_outcomes = [
            scorecard_mod.WaiverOutcome(
                won=e.won, recommended_bid=e.recommended_bid, actual_bid=e.actual_bid)
            for e in waiver_entries
        ]
        waiver_result = scorecard_mod.waiver_metric(waiver_outcomes)

        draft_entries = _DRAFT_PICK_LEDGER.list_for_league(lg.league_key)
        your_draft_entries = (
            [e for e in draft_entries if e.roster_id == lg.roster_id]
            if lg.roster_id is not None else [])
        draft_outcomes = [scorecard_mod.DraftOutcome(grade=e.grade) for e in your_draft_entries]
        draft_result = scorecard_mod.draft_metric(draft_outcomes)

        return {
            "lineup": lineup_result, "trade": trade_result,
            "waiver": waiver_result, "draft": draft_result,
        }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_app.py -k scorecard -v`
Expected: PASS

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest -q`
Expected: every prior test still passes, plus everything new from Tasks 1-13

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/api/app.py tests/api/test_app.py src/ffdo/ingest/sleeper/historical_weekly_stats.py
git commit -m "feat: wire GET /scorecard -- composes all four outcome metrics"
```

---

### Task 14: Season screen Scorecard tab + README

**Files:**
- Modify: `src/ffdo/web/season/season.js`
- Modify: `src/ffdo/web/season/season.css`
- Modify: `README.md`

**Interfaces:**
- Consumes: `GET /api/leagues/{league_key}/scorecard` (Task 13).
- Produces: a 5th season-screen tab. Nothing later in this plan depends on this task.

- [ ] **Step 1: Add scorecard state and tab wiring**

In `src/ffdo/web/season/season.js`, the module-level state currently reads:

```javascript
let _lineupData = null;   // null until the Lineup tab has been opened at least once
let _tradesData = null;   // null until the Trades tab has been opened at least once
let _waiversData = null;  // null until the Waivers tab has been opened at least once
```

Add a fourth line:

```javascript
let _lineupData = null;   // null until the Lineup tab has been opened at least once
let _tradesData = null;   // null until the Trades tab has been opened at least once
let _waiversData = null;  // null until the Waivers tab has been opened at least once
let _scorecardData = null; // null until the Scorecard tab has been opened at least once
```

In `mountSeason`, the reset block currently reads:

```javascript
  _lineupData = null;
  _tradesData = null;
  _waiversData = null;
```

Add:

```javascript
  _lineupData = null;
  _tradesData = null;
  _waiversData = null;
  _scorecardData = null;
```

In `renderRightPanel`, the tab bar currently reads:

```javascript
  const tabBar = `
    <div class="panel-tabs">
      <button data-panel-tab="lineup" class="${_panel === "lineup" ? "on" : ""}">Lineup</button>
      <button data-panel-tab="power" class="${_panel === "power" ? "on" : ""}">Power ranking</button>
      ${showCapital ? `<button data-panel-tab="capital" class="${_panel === "capital" ? "on" : ""}">Draft capital</button>` : ""}
      <button data-panel-tab="trades" class="${_panel === "trades" ? "on" : ""}">Trades</button>
      <button data-panel-tab="waivers" class="${_panel === "waivers" ? "on" : ""}">Waivers</button>
    </div>`;
```

Add the Scorecard button after Waivers:

```javascript
  const tabBar = `
    <div class="panel-tabs">
      <button data-panel-tab="lineup" class="${_panel === "lineup" ? "on" : ""}">Lineup</button>
      <button data-panel-tab="power" class="${_panel === "power" ? "on" : ""}">Power ranking</button>
      ${showCapital ? `<button data-panel-tab="capital" class="${_panel === "capital" ? "on" : ""}">Draft capital</button>` : ""}
      <button data-panel-tab="trades" class="${_panel === "trades" ? "on" : ""}">Trades</button>
      <button data-panel-tab="waivers" class="${_panel === "waivers" ? "on" : ""}">Waivers</button>
      <button data-panel-tab="scorecard" class="${_panel === "scorecard" ? "on" : ""}">Scorecard</button>
    </div>`;
```

Right after the existing `if (_panel === "waivers") { ... }` block in `renderRightPanel`, add:

```javascript
  if (_panel === "scorecard") {
    if (_scorecardData === null) {
      loadScorecard();
      return tabBar + `<div class="lineup-loading">Loading scorecard&hellip;</div>`;
    }
    return tabBar + renderScorecard();
  }
```

- [ ] **Step 2: Add the load/render functions**

Find `loadWaivers`/`renderWaivers` in `season.js` (they follow the exact same shape as `loadTrades`/`renderTrades` before them) and add `loadScorecard`/`renderScorecard` right after them, following the identical pattern:

```javascript
async function loadScorecard() {
  const key = _key;
  const res = await fetch(`/api/leagues/${encodeURIComponent(key)}/scorecard`);
  // A league switch that happens while this request is in flight must not
  // let a stale response clobber the newly-reset (or still-loading)
  // _scorecardData -- same guard loadTrades/loadWaivers already use.
  if (key !== _key) return;
  if (!res.ok) {
    _scorecardData = { error: (await res.json().catch(() => ({}))).detail || "Couldn't load the scorecard" };
    render();
    return;
  }
  _scorecardData = await res.json();
  render();
}

function renderScorecard() {
  if (_scorecardData.error) {
    return `<div class="lineup-error">${escapeHtml(_scorecardData.error)}</div>`;
  }
  const l = _scorecardData.lineup;
  const t = _scorecardData.trade;
  const w = _scorecardData.waiver;
  const d = _scorecardData.draft;

  const lineupCard = `
    <div class="scorecard-card">
      <h3>Lineup</h3>
      <p>${l.weeks_resolved} weeks resolved &mdash; ${l.weeks_full} fully followed,
         ${l.weeks_partial} partial, ${l.weeks_none} not followed</p>
      <p>Points left on the bench when not followed: ${l.points_left_on_bench}</p>
    </div>`;

  const tradeCard = `
    <div class="scorecard-card">
      <h3>Trades</h3>
      <p>${t.total_trades_in_league} trades in the league, ${t.your_trades} involving you</p>
      <p>Gained value: ${t.gained_value} &middot; Lost value: ${t.lost_value} &middot; Unchanged: ${t.unchanged}</p>
    </div>`;

  const waiverCard = `
    <div class="scorecard-card">
      <h3>Waivers</h3>
      <p>${w.recommended_claims} recommended claims &mdash;
         win rate: ${w.win_rate === null ? "&mdash;" : (w.win_rate * 100).toFixed(0) + "%"}</p>
      <p>Avg bid vs. recommended: ${w.avg_bid_delta === null ? "&mdash;" : w.avg_bid_delta}</p>
    </div>`;

  const draftCard = `
    <div class="scorecard-card">
      <h3>Draft</h3>
      <p>${d.picks_graded} of your picks graded</p>
      <p>GREAT: ${d.grade_counts.GREAT} &middot; GOOD: ${d.grade_counts.GOOD} &middot;
         FAIR: ${d.grade_counts.FAIR} &middot; POOR: ${d.grade_counts.POOR}</p>
    </div>`;

  return `<div class="scorecard-grid">${lineupCard}${tradeCard}${waiverCard}${draftCard}</div>`;
}
```

- [ ] **Step 3: Add CSS**

In `src/ffdo/web/season/season.css`, add (near the other panel-specific rules such as `.lineup-row`/`.waiver-row`):

```css
.scorecard-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.scorecard-card {
  border: 1px solid var(--border, #ddd);
  border-radius: 8px;
  padding: 12px;
}

.scorecard-card h3 {
  margin: 0 0 8px;
  font-size: 14px;
}

.scorecard-card p {
  margin: 4px 0;
  font-size: 13px;
}
```

- [ ] **Step 4: Manually verify in a browser**

Start the app against a real tracked league with at least some lineup/trade/waiver history, open the season screen, click the Scorecard tab, and confirm all four cards render with real numbers (not `undefined` or a stack trace). Switch to a league with no waiver or draft-pick history yet and confirm those cards degrade gracefully (e.g. `win_rate: —`) rather than crashing the tab.

- [ ] **Step 5: Document in the README**

In `README.md`, the existing Waivers paragraph ends at line 27 (`... historical waiver-claim outcomes.`), immediately before `## Running it`. Add a new paragraph there:

```markdown
The **Scorecard** tab shows how good FFDO's own recommendations have
actually been -- weeks your lineup rec was followed and points left on the
bench when it wasn't, how your trades have played out since, your FAAB
claim win rate and bid efficiency, and a grade breakdown for your own
draft picks.

```

- [ ] **Step 6: Run the full test suite one more time**

Run: `uv run pytest -q`
Expected: all tests still pass (this task touches no Python)

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/web/season/season.js src/ffdo/web/season/season.css README.md
git commit -m "feat: season screen Scorecard tab -- lineup/trade/waiver/draft outcomes"
```

---

## Final Review Checklist (for the controller, not a task)

Per this project's established pattern, the whole-branch final review should specifically hunt for:

1. **The "live vs. final data" bug class** (bit #5's `GET /trades`): confirm every new week-bounded scan in this plan (`fetch_all_claims`, the scorecard's own week selection) uses `nfl.week`, never a stats-final variable.
2. **The bucket/binning-formula bug class** (bit #6's `FAAB_BID_CURVE`): `positional_cliff`'s index-finding (`next((i for i, (v, _) in enumerate(pool) if v <= level), len(pool))`) is the one place in this plan doing float-threshold indexing — re-verify it by hand against a real ranked list, not just the unit tests' synthetic fixtures.
3. **The draft-pick-ledger edge case** (Task 8): manually smoke-test a live or mock draft through to completion and confirm every pick lands in `draft_pick_record`, including the very last one.
4. A manual smoke test of `GET /scorecard` against a real tracked league with real lineup/trade/waiver/draft history, not just fixtures -- per the pattern that has caught the most serious bug in #3, #4, #5, and #6's per-task reviews, but never in automated review alone.
