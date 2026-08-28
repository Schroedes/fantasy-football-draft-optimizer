# Snake Draft Optimal Plan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Roll the rest of a snake draft forward via simulation to estimate, for each of your remaining picks, the position/player you're most likely to draft and your team's expected final starting-lineup VOR — surfaced as a new sidebar panel, alongside a layout fix so the whole left sidebar (nominated/rosters/history/plan panels) fits the viewport without page-level scrolling.

**Architecture:** A shared Gumbel-max primitive extracted from the existing `market.simulate_survival()` lets a new `engine/snake_plan.py` module remove whole stretches of opponent picks in one batched draw between each of your own simulated picks, which use a cheap need-weighted-VOR heuristic (not the expensive exact lineup-fit function). The result is wired into the existing `/api/board` snake payload the same way `survival`/`cost_of_waiting` already are, and rendered as a new panel. Four sidebar panels are converted from fixed/capped heights to a flexible layout that always exactly fills the available vertical space, eliminating page scroll.

**Tech Stack:** Python 3 (pytest, numpy), vanilla JS/CSS (no build step, no JS test runner in this repo).

**Spec:** `docs/superpowers/specs/2026-08-27-snake-draft-optimal-plan-design.md`

## Global Constraints

- `gone_this_stretch()`'s extraction from `simulate_survival()` must be behavior-preserving — every existing `tests/engine/test_market.py` test must pass unchanged, with the same seeded-RNG numbers (not just statistically similar), since it consumes `rng` identically to the code it replaces.
- The in-simulation pick heuristic (`_need_weights`) is a cheap approximation used only to steer simulated choices; `roster.team_lineup()` (the exact function) still scores each trial's FINAL roster once, so `expected_starting_vor` stays trustworthy.
- `_need_weights` must derive its position universe from `league.roster_positions` directly, not from a hardcoded `OFFENSE_POSITIONS` loop — DEF/K support was added to the app after this feature's design started, `vor.compute()` already produces real VOR for them with no engine change needed, and a hardcoded OFFENSE_POSITIONS-only heuristic would score every DEF/K candidate as `vor * 0.0` forever, making them permanently undraftable by the rollout even in leagues that require them.
- `simulate_snake_plan()` returns `None` (not a partial/guessed result) when your draft slot can't be determined yet (no `DraftPick` with your `roster_id`).
- The UI layout change must eliminate page-level (`body`) scrolling; individual panels may still scroll internally within their own allotted space — that's an accepted, existing pattern, not a regression.
- Run `uv run pytest` (full suite) before every commit; every commit must leave the suite green.

---

### Task 1: Extract `gone_this_stretch()` from `simulate_survival()`

**Files:**
- Modify: `src/ffdo/engine/market.py`
- Modify: `tests/engine/test_market.py`

**Interfaces:**
- Produces: `gone_this_stretch(ids: Sequence[str], adp: Mapping[str, float], take: int, tau: float, rng: np.random.Generator) -> frozenset[str]` — one Gumbel-max draw removing up to `take` ids from `ids`, weighted by ADP. Task 3's rollout calls this directly (once per gap between two of your own picks); `simulate_survival` is refactored to call it once per trial in place of its current inline body.

- [ ] **Step 1: Write a test proving the refactor target's shape**

Add to `tests/engine/test_market.py`:

```python
def test_gone_this_stretch_removes_exactly_take_players():
    adp = {f"p{i}": float(i + 1) for i in range(20)}
    rng = np.random.default_rng(5)
    gone = market.gone_this_stretch(list(adp), adp, take=4, tau=8.0, rng=rng)
    assert len(gone) == 4
    assert gone <= set(adp)


def test_gone_this_stretch_never_removes_players_without_adp():
    adp = {"p0": 1.0, "p1": 2.0}
    rng = np.random.default_rng(5)
    gone = market.gone_this_stretch(["p0", "p1", "no_adp"], adp, take=5, tau=8.0, rng=rng)
    assert "no_adp" not in gone
    assert gone == {"p0", "p1"}  # take clamps to eligible count, not requested count
```

- [ ] **Step 2: Run to verify these two tests fail**

Run: `export PATH="$PATH:/c/Users/basek/.local/bin"` then `uv run pytest tests/engine/test_market.py -v -k gone_this_stretch`

Expected: FAIL — `AttributeError: module 'ffdo.engine.market' has no attribute 'gone_this_stretch'`.

- [ ] **Step 3: Extract `gone_this_stretch()` and refactor `simulate_survival()` to use it**

Replace `simulate_survival()`'s body in `src/ffdo/engine/market.py` (currently lines 18-52) with the extraction. The new file section (replacing from `def simulate_survival` through the end of that function):

```python
def gone_this_stretch(
    ids: Sequence[str], adp: Mapping[str, float], take: int,
    tau: float, rng: np.random.Generator,
) -> frozenset[str]:
    """One Gumbel-max draw: up to `take` ids removed from `ids`, weighted
    by ADP (lower ADP -> more desirable -> more likely to be taken). Ids
    absent from `adp` are never drawn as "gone" -- the same limitation
    `simulate_survival` already has, not new here.
    """
    eligible = [pid for pid in ids if pid in adp]
    if take <= 0 or not eligible:
        return frozenset()
    take = min(take, len(eligible))
    logits = np.array([-adp[pid] / tau for pid in eligible])
    gumbel = rng.gumbel(size=len(eligible))
    gone_idx = np.argpartition(-(logits + gumbel), take - 1)[:take]
    return frozenset(eligible[i] for i in gone_idx)


def simulate_survival(
    adp: Mapping[str, float],
    available: Iterable[str],
    picks_until: int,
    *,
    sims: int = 2000,
    tau: float = 8.0,
    rng: np.random.Generator | None = None,
) -> dict[str, float]:
    """P(each available player is still there in `picks_until` picks).

    Each simulated pick draws from the remaining pool via Gumbel-max sampling
    (equivalent to Plackett-Luce), so exactly one player leaves per pick.
    """
    rng = rng or np.random.default_rng()
    ids = [pid for pid in available if pid in adp]
    if not ids or picks_until <= 0:
        return dict.fromkeys(ids, 1.0)

    n = len(ids)
    id_index = {pid: i for i, pid in enumerate(ids)}
    survived = np.zeros(n, dtype=np.int64)
    for _ in range(sims):
        gone = gone_this_stretch(ids, adp, picks_until, tau, rng)
        mask = np.ones(n, dtype=bool)
        for pid in gone:
            mask[id_index[pid]] = False
        survived += mask

    return {pid: float(survived[i]) / sims for i, pid in enumerate(ids)}
```

Add `Sequence` to the existing `from collections.abc import Iterable, Mapping` import line (becomes `from collections.abc import Iterable, Mapping, Sequence`).

This is behavior-preserving by construction: `gone_this_stretch` consumes `rng` with exactly one `rng.gumbel(size=len(eligible))` call, the same single call `simulate_survival`'s old inline loop body made per iteration, with `eligible == ids` in this call path (the outer function already pre-filters to ADP-bearing ids before calling), so the sequence of random draws — and therefore every existing test's numbers, including the tight `abs=0.001` tolerance in `test_exactly_one_player_is_taken_per_pick` — are unaffected. `cost_of_waiting()` (below `simulate_survival()` in the file) is untouched.

- [ ] **Step 4: Run the new tests, then the full `test_market.py` file**

Run: `uv run pytest tests/engine/test_market.py -v`

Expected: PASS — all 2 new tests, and all 6 pre-existing tests unchanged.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`

Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/engine/market.py tests/engine/test_market.py
git commit -m "refactor: extract gone_this_stretch() from simulate_survival()"
```

---

### Task 2: Snake pick-order math and the cheap need-weight heuristic

**Files:**
- Create: `src/ffdo/engine/snake_plan.py`
- Test: `tests/engine/test_snake_plan.py` (new file)

**Interfaces:**
- Produces: `_your_draft_slot(state, roster_id) -> int | None`; `_pick_no_for(round_no, draft_slot, num_teams) -> int`; `_slot_for_pick(pick_no, num_teams) -> int`; `_need_weights(sim_roster: Mapping[str, ValuedPlayer], league) -> dict[str, float]`. Task 3's rollout uses all four.

- [ ] **Step 1: Write the pick-order and need-weight tests**

Create `tests/engine/test_snake_plan.py`:

```python
import numpy as np
import pytest

from ffdo.domain.models import DraftPick, DraftState, LeagueProfile, PlayerProfile, ValuedPlayer
from ffdo.engine import snake_plan


def _league_multi(roster_positions, n=12, budget=None):
    return LeagueProfile(league_id="x", season=2026, num_teams=n,
                         roster_positions=roster_positions,
                         scoring_settings={}, budget=budget)


def _valued_positions(position_vors):
    """position_vors: dict[pid] -> (position, vor)."""
    out = {}
    for pid, (pos, v) in position_vors.items():
        prof = PlayerProfile(player_id=pid, first_name="P", last_name=pid,
                             position=pos, team="X", age=25, years_exp=3,
                             injury_status=None, active=True)
        out[pid] = ValuedPlayer(profile=prof, projected_points=0.0,
                                adjusted_points=0.0, vor=v, tier=1,
                                adjustments={})
    return out


def test_pick_no_for_standard_snake_order():
    assert snake_plan._pick_no_for(1, draft_slot=1, num_teams=4) == 1
    assert snake_plan._pick_no_for(1, draft_slot=4, num_teams=4) == 4
    assert snake_plan._pick_no_for(2, draft_slot=4, num_teams=4) == 5  # round 2 reverses
    assert snake_plan._pick_no_for(2, draft_slot=1, num_teams=4) == 8
    assert snake_plan._pick_no_for(3, draft_slot=1, num_teams=4) == 9  # round 3 reverses back


def test_slot_for_pick_round_trips_pick_no_for():
    for round_no in range(1, 6):
        for slot in range(1, 5):
            pick_no = snake_plan._pick_no_for(round_no, slot, num_teams=4)
            assert snake_plan._slot_for_pick(pick_no, num_teams=4) == slot


def test_your_draft_slot_reads_off_your_own_pick():
    picks = (DraftPick(pick_no=3, round=1, draft_slot=3, roster_id=7, picked_by="u",
                       player_id="p1", amount=None),)
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=10, rounds=15, budget=None, picks=picks)
    assert snake_plan._your_draft_slot(state, roster_id=7) == 3


def test_your_draft_slot_is_none_before_your_first_pick():
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=10, rounds=15, budget=None, picks=())
    assert snake_plan._your_draft_slot(state, roster_id=7) is None
    assert snake_plan._your_draft_slot(state, roster_id=None) is None


def test_need_weights_full_for_open_dedicated_slot():
    league = _league_multi(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN"))
    weights = snake_plan._need_weights({}, league)
    assert weights["QB"] == 1.0
    assert weights["RB"] == 1.0


def test_need_weights_reduced_once_only_flex_room_remains():
    league = _league_multi(("RB", "FLEX", "BN"))
    roster = _valued_positions({"rb1": ("RB", 50.0)})  # dedicated RB filled
    weights = snake_plan._need_weights(roster, league)
    assert weights["RB"] == 0.85


def test_need_weights_low_once_fully_staffed():
    league = _league_multi(("RB", "BN"))  # no flex at all in this league
    roster = _valued_positions({"rb1": ("RB", 50.0)})
    weights = snake_plan._need_weights(roster, league)
    assert weights["RB"] == 0.15


def test_need_weights_covers_def_and_k_not_just_offense_positions():
    """DEF/K aren't in OFFENSE_POSITIONS -- this proves the position
    universe is genuinely derived from league.roster_positions, not
    hardcoded, so a league that rosters them doesn't leave them silently
    unweighted (and therefore unpickable -- see the rollout-level
    regression test in Task 3)."""
    league = _league_multi(("QB", "DEF", "K", "BN"))
    weights = snake_plan._need_weights({}, league)
    assert weights["DEF"] == 1.0
    assert weights["K"] == 1.0


def test_need_weights_def_and_k_are_never_flex_eligible():
    league = _league_multi(("DEF", "K", "FLEX", "BN"))
    roster = _valued_positions({"def1": ("DEF", 50.0), "k1": ("K", 20.0)})
    weights = snake_plan._need_weights(roster, league)
    # Both dedicated slots are now filled; DEF/K never appear in any
    # FLEX_ELIGIBILITY value set, so the open FLEX slot must NOT grant
    # them the 0.85 flex-eligible weight -- straight to bench-tier 0.15.
    assert weights["DEF"] == 0.15
    assert weights["K"] == 0.15
```

- [ ] **Step 2: Run to verify these tests fail**

Run: `uv run pytest tests/engine/test_snake_plan.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.engine.snake_plan'`.

- [ ] **Step 3: Create `snake_plan.py` with the pick-order math and need-weight heuristic**

```python
"""Simulate the rest of a snake draft forward to estimate your best
achievable team, accounting for who's likely to survive to each of your
future picks."""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.constants import OFFENSE_POSITIONS
from ffdo.domain.models import DraftState, ValuedPlayer
from ffdo.engine.replacement import FLEX_ELIGIBILITY


def _your_draft_slot(state: DraftState, roster_id: int | None) -> int | None:
    """Your seat, read off any pick you've already made. None if you
    haven't picked yet (or roster_id is unset) -- there's no other
    signal for which seat is yours before that."""
    if roster_id is None:
        return None
    return next((p.draft_slot for p in state.picks if p.roster_id == roster_id), None)


def _pick_no_for(round_no: int, draft_slot: int, num_teams: int) -> int:
    pick_in_round = draft_slot if round_no % 2 == 1 else num_teams - draft_slot + 1
    return (round_no - 1) * num_teams + pick_in_round


def _slot_for_pick(pick_no: int, num_teams: int) -> int:
    round_no = (pick_no - 1) // num_teams + 1
    pick_in_round = (pick_no - 1) % num_teams + 1
    return pick_in_round if round_no % 2 == 1 else num_teams - pick_in_round + 1


def _need_weights(sim_roster: Mapping[str, ValuedPlayer], league) -> dict[str, float]:
    """Cheap stand-in for "do I still need this position": full weight
    while a dedicated starting slot is open, reduced weight once only
    FLEX-eligible room remains, low (bench-only) weight otherwise. Covers
    every position this league actually rosters -- not hardcoded to
    OFFENSE_POSITIONS -- so DEF/K (real dedicated slots, never
    flex-eligible) get weighted the same way a real offense position
    does, rather than being silently unpickable (they score `vor * 0.0`
    forever if absent from this dict, since the caller looks them up via
    `weights.get(position, 0.0)`). Not a replacement for
    roster.marginal_lineup_values -- that still scores each trial's FINAL
    roster (see Task 3's simulate_snake_plan); this only steers the
    in-simulation pick, where the exact version is too expensive to run
    at every pick of every trial.
    """
    pos_counts: dict[str, int] = {}
    for vp in sim_roster.values():
        pos_counts[vp.profile.position] = pos_counts.get(vp.profile.position, 0) + 1

    # Every literal position this league has a dedicated slot for.
    # OFFENSE_POSITIONS is always included even at zero dedicated slots
    # (e.g. no dedicated QB slot, QB only via superflex), so those
    # positions still get a real bench-tier weight instead of being
    # absent from the dict entirely.
    dedicated_positions = frozenset(
        slot for slot in league.roster_positions
        if slot not in FLEX_ELIGIBILITY and slot != "BN"
    ) | OFFENSE_POSITIONS
    dedicated_counts = {pos: league.roster_positions.count(pos) for pos in dedicated_positions}

    flex_positions = frozenset(
        pos for slot in league.roster_positions if slot in FLEX_ELIGIBILITY
        for pos in FLEX_ELIGIBILITY[slot]
    )
    flex_total = sum(1 for slot in league.roster_positions if slot in FLEX_ELIGIBILITY)
    flex_used = sum(max(0, pos_counts.get(pos, 0) - dedicated_counts.get(pos, 0))
                    for pos in flex_positions)
    flex_open = flex_total - flex_used

    weights: dict[str, float] = {}
    for pos in dedicated_positions:
        dedicated_open = dedicated_counts[pos] - min(pos_counts.get(pos, 0), dedicated_counts[pos])
        if dedicated_open > 0:
            weights[pos] = 1.0
        elif pos in flex_positions and flex_open > 0:
            weights[pos] = 0.85
        else:
            weights[pos] = 0.15
    return weights
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/test_snake_plan.py -v`

Expected: PASS — all 9 tests.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`

Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/engine/snake_plan.py tests/engine/test_snake_plan.py
git commit -m "feat: add snake pick-order math and the need-weighted pick heuristic"
```

---

### Task 3: The rollout — `simulate_snake_plan()`, plus the required performance benchmark

**Files:**
- Modify: `src/ffdo/engine/snake_plan.py`
- Modify: `tests/engine/test_snake_plan.py`

**Interfaces:**
- Consumes: `gone_this_stretch()` (Task 1), `_your_draft_slot`/`_pick_no_for`/`_need_weights` (Task 2), `roster.team_lineup()` (existing).
- Produces: `simulate_snake_plan(valued, adp, state, league, roster_id, *, sims=200, tau=8.0, rng=None) -> dict | None`. Task 4 (API wiring) calls this directly. Output shape: `{"picks": [{"pick_no": int, "picks_from_now": int, "most_likely_position": str, "position_hit_rate": float, "most_likely_player_id": str, "most_likely_player_name": str, "player_hit_rate": float}, ...], "expected_starting_vor": float, "sims_run": int}`, or `None`.

- [ ] **Step 1: Write the rollout tests**

Add to `tests/engine/test_snake_plan.py`:

```python
from ffdo.engine import roster as roster_engine


def _empty_available_state(num_teams=12, rounds=15):
    return DraftState(draft_id="d", draft_type="snake", status="drafting",
                      num_teams=num_teams, rounds=rounds, budget=None, picks=())


def test_simulate_snake_plan_returns_none_before_your_first_pick():
    league = _league_multi(("QB", "RB", "BN"), n=2)
    state = _empty_available_state(num_teams=2, rounds=3)
    result = snake_plan.simulate_snake_plan({}, {}, state, league, roster_id=1)
    assert result is None


def test_simulate_snake_plan_is_deterministic_with_a_seeded_rng():
    league = _league_multi(("QB", "RB", "BN"), n=2)
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u",
                       player_id="qb1", amount=None),)
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=2, rounds=3, budget=None, picks=picks)
    valued = _valued_positions({
        "qb1": ("QB", 40.0), "rb1": ("RB", 50.0), "rb2": ("RB", 45.0),
        "wr1": ("WR", 30.0), "filler": ("RB", 5.0),
    })
    adp = {"rb1": 2.0, "rb2": 3.0, "wr1": 4.0, "filler": 40.0}

    result_a = snake_plan.simulate_snake_plan(
        valued, adp, state, league, roster_id=1, sims=20, rng=np.random.default_rng(7))
    result_b = snake_plan.simulate_snake_plan(
        valued, adp, state, league, roster_id=1, sims=20, rng=np.random.default_rng(7))
    assert result_a == result_b


def test_simulate_snake_plan_output_shape():
    league = _league_multi(("QB", "RB", "BN"), n=2)
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u",
                       player_id="qb1", amount=None),)
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=2, rounds=3, budget=None, picks=picks)
    # num_teams=2 means draft_slot 1's remaining picks (rounds 2, 3) land at
    # pick_no 4 then 5 -- a stretch of 2 opponent picks before pick 4, then a
    # back-to-back turn (0-gap) into pick 5. Simulating both of your future
    # picks therefore needs at least 4 distinct non-drafted players (2 for
    # the opponent stretch + 1 for each of your two picks); only 3 would
    # starve the second pick every trial, so a 4th (filler2, deliberately
    # absent from adp so it always survives the opponent-stretch draw) is
    # required for the pool to cover demand.
    valued = _valued_positions({
        "qb1": ("QB", 40.0), "rb1": ("RB", 50.0), "rb2": ("RB", 45.0),
        "filler": ("RB", 5.0), "filler2": ("RB", 1.0),
    })
    adp = {"rb1": 2.0, "rb2": 3.0, "filler": 40.0}

    result = snake_plan.simulate_snake_plan(
        valued, adp, state, league, roster_id=1, sims=20, rng=np.random.default_rng(1))

    assert result is not None
    assert len(result["picks"]) == 2  # rounds 2 and 3 remain
    for p in result["picks"]:
        assert 0.0 <= p["position_hit_rate"] <= 1.0
        assert 0.0 <= p["player_hit_rate"] <= 1.0
    assert result["sims_run"] == 20


def test_need_weighting_changes_the_simulated_pick_vs_raw_vor():
    """Prove the heuristic actually drives the rollout's choice, not just
    that it runs -- without need-weighting, raw VOR alone would pick the
    QB (VOR 100) over the RB (VOR 50); with it, the RB wins because QB is
    already fully staffed and RB is your only remaining real need. A
    single-team league with no gap before your next pick keeps this
    deterministic regardless of adp/rng."""
    league = _league_multi(("QB", "RB", "BN"), n=1)
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u",
                       player_id="qb_drafted", amount=None),)
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=1, rounds=3, budget=None, picks=picks)
    valued = _valued_positions({
        "qb_drafted": ("QB", 40.0), "qb_great": ("QB", 100.0),
        "rb_good": ("RB", 50.0), "filler": ("RB", 5.0),
    })
    adp = {"qb_great": 1.0, "rb_good": 5.0, "filler": 50.0}

    result = snake_plan.simulate_snake_plan(
        valued, adp, state, league, roster_id=1, sims=20, rng=np.random.default_rng(0))

    assert result is not None
    assert result["picks"][0]["most_likely_position"] == "RB"


def test_simulate_snake_plan_scores_final_roster_with_the_exact_function():
    """expected_starting_vor must come from roster.team_lineup(), not the
    cheap heuristic -- verified by checking it's a real, sane VOR number
    given the fixture's players, not just present."""
    league = _league_multi(("QB", "BN"), n=1)
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u",
                       player_id="qb1", amount=None),)
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=1, rounds=2, budget=None, picks=picks)
    valued = _valued_positions({"qb1": ("QB", 40.0), "filler": ("RB", 5.0)})
    adp = {"filler": 10.0}

    result = snake_plan.simulate_snake_plan(
        valued, adp, state, league, roster_id=1, sims=10, rng=np.random.default_rng(2))

    # Only qb1 can ever start (BN never counts toward starting_vor); filler
    # always ends up on the single BN slot regardless of the heuristic.
    assert result["expected_starting_vor"] == pytest.approx(40.0, abs=0.01)


def test_simulate_snake_plan_can_draft_a_kicker_when_its_the_only_real_need():
    """DEF/K must be pickable by the rollout, not silently invisible --
    regression test for the gap found when DEF/K scoring/VOR support was
    added to the rest of the app (see domain/constants.py's
    is_defense_scoring_key/is_kicking_scoring_key and vor.compute()'s own
    "no engine change needed" test) after this feature's design assumed
    OFFENSE_POSITIONS-only candidates. Single-team, no-gap setup keeps
    this deterministic regardless of adp/rng, same trick as the
    need-weighting test above."""
    league = _league_multi(("QB", "K", "BN"), n=1)
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u",
                       player_id="qb1", amount=None),)
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=1, rounds=3, budget=None, picks=picks)
    valued = _valued_positions({
        "qb1": ("QB", 40.0), "k_great": ("K", 20.0), "filler": ("RB", 5.0),
    })
    adp = {"k_great": 5.0, "filler": 50.0}

    result = snake_plan.simulate_snake_plan(
        valued, adp, state, league, roster_id=1, sims=20, rng=np.random.default_rng(0))

    assert result is not None
    assert result["picks"][0]["most_likely_position"] == "K"
```

- [ ] **Step 2: Run to verify these tests fail**

Run: `uv run pytest tests/engine/test_snake_plan.py -v -k simulate_snake_plan`

Expected: FAIL — `AttributeError: module 'ffdo.engine.snake_plan' has no attribute 'simulate_snake_plan'`.

- [ ] **Step 3: Implement `simulate_snake_plan()`**

Add to `src/ffdo/engine/snake_plan.py`. First, replace the import block Task 2 added (everything from `from collections.abc import Mapping` through `from ffdo.engine.replacement import FLEX_ELIGIBILITY` — four lines, directly below the `from __future__ import annotations` line, which stays as-is) with:

```python
from collections import Counter
from collections.abc import Mapping

import numpy as np

from ffdo.domain.constants import OFFENSE_POSITIONS
from ffdo.domain.models import DraftState, ValuedPlayer
from ffdo.engine import roster as roster_engine
from ffdo.engine.market import gone_this_stretch
from ffdo.engine.replacement import FLEX_ELIGIBILITY
```

Then append, after `_need_weights`:

```python
def _current_starting_vor(
    state: DraftState, valued: Mapping[str, ValuedPlayer], league, roster_id: int | None,
) -> float:
    your_roster = {p.player_id: valued[p.player_id] for p in state.picks
                   if p.roster_id == roster_id and p.player_id in valued}
    return roster_engine.team_lineup(your_roster, league).starting_vor


def simulate_snake_plan(
    valued: Mapping[str, ValuedPlayer],
    adp: Mapping[str, float],
    state: DraftState,
    league,
    roster_id: int | None,
    *,
    sims: int = 200,
    tau: float = 8.0,
    rng: np.random.Generator | None = None,
) -> dict | None:
    """Roll the rest of the draft forward `sims` times. At each of YOUR
    future picks, take the cheap need-weighted-VOR choice (_need_weights);
    each stretch of opponent picks in between is removed in one batched
    Gumbel-max draw (market.gone_this_stretch), the same mechanism
    simulate_survival already uses. Returns None if your draft slot can't
    be determined yet -- no result before your first real pick.
    """
    your_draft_slot = _your_draft_slot(state, roster_id)
    if your_draft_slot is None:
        return None

    rng = rng or np.random.default_rng()
    your_picks_made = sum(1 for p in state.picks if p.roster_id == roster_id)
    your_future_pick_nos = [
        _pick_no_for(r, your_draft_slot, league.num_teams)
        for r in range(your_picks_made + 1, league.roster_size + 1)
    ]
    if not your_future_pick_nos:
        return {
            "picks": [],
            "expected_starting_vor": round(_current_starting_vor(state, valued, league, roster_id), 1),
            "sims_run": 0,
        }

    drafted = state.drafted_player_ids()
    available_ids = [pid for pid in valued if pid not in drafted]
    your_current_roster = {p.player_id: valued[p.player_id] for p in state.picks
                           if p.roster_id == roster_id and p.player_id in valued}
    next_pick_no = max((p.pick_no for p in state.picks), default=0) + 1

    position_tallies = [Counter() for _ in your_future_pick_nos]
    player_tallies = [Counter() for _ in your_future_pick_nos]
    final_vors: list[float] = []

    for _ in range(sims):
        sim_available = set(available_ids)
        sim_roster = dict(your_current_roster)
        cursor = next_pick_no

        for i, your_pick_no in enumerate(your_future_pick_nos):
            gap = your_pick_no - cursor
            if gap > 0:
                gone = gone_this_stretch(list(sim_available), adp, gap, tau, rng)
                sim_available -= gone
            if not sim_available:
                break

            weights = _need_weights(sim_roster, league)
            best_id = max(
                sim_available,
                key=lambda pid: valued[pid].vor * weights.get(valued[pid].profile.position, 0.0),
            )
            sim_roster[best_id] = valued[best_id]
            sim_available.discard(best_id)
            position_tallies[i][valued[best_id].profile.position] += 1
            player_tallies[i][best_id] += 1
            cursor = your_pick_no + 1

        final_vors.append(roster_engine.team_lineup(sim_roster, league).starting_vor)

    picks = []
    for i, pick_no in enumerate(your_future_pick_nos):
        if not position_tallies[i]:
            continue
        top_pos, pos_n = position_tallies[i].most_common(1)[0]
        top_player, player_n = player_tallies[i].most_common(1)[0]
        picks.append({
            "pick_no": pick_no,
            "picks_from_now": i + 1,
            "most_likely_position": top_pos,
            "position_hit_rate": round(pos_n / sims, 3),
            "most_likely_player_id": top_player,
            "most_likely_player_name": valued[top_player].profile.full_name,
            "player_hit_rate": round(player_n / sims, 3),
        })

    return {
        "picks": picks,
        "expected_starting_vor": round(sum(final_vors) / len(final_vors), 1) if final_vors else 0.0,
        "sims_run": sims,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/test_snake_plan.py -v`

Expected: PASS — all 15 tests in the file (9 from Task 2 plus 6 added in this task).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`

Expected: PASS, no regressions.

- [ ] **Step 6: Required performance benchmark — do not skip**

This step decides the shipped `sims` default and confirms (or refutes) that this feature can run on the existing 3-second heavy-refresh cadence, per spec §7. Write a throwaway benchmark script (in the scratchpad directory, NOT committed to the repo) that:

1. Loads a real committed fixture with a realistic player pool and ADP data — check `data/snapshots/` for an existing snapshot this repo's other tests already load (e.g. via `ffdo.ingest.snapshot`), and reuse the same one `tests/engine/test_auction.py`'s `test_replaying_a_real_auction_keeps_inflation_sane` or similar already loads, rather than fabricating synthetic data — a benchmark against a tiny synthetic fixture would not be representative.
2. Constructs a realistic mid-draft `DraftState` (some picks already made, including at least one of "your" picks so `simulate_snake_plan` doesn't short-circuit to `None`), a `LeagueProfile` with a realistic `roster_size` (13-16) and `num_teams` (10-14), and ADP data for the available pool.
3. Times `simulate_snake_plan(..., sims=200)` wall-clock (`time.perf_counter()`), run 3 times, report the median.

Report the measured time. Then:

- **If the median time is comfortably under ~2.5 seconds** (leaving headroom for the rest of `/api/board`'s existing heavy work — check what the auction board's existing heavy-refresh work costs today as a rough reference point if easily available, otherwise use 2.5s as a conservative standalone budget): keep `sims=200` as the default. Note the measured number in your task report.
- **If it's over that but a smaller `sims` value (not below 100 — below that, hit-rate percentages are mostly noise per spec §7) fits comfortably**: change the `sims: int = 200` default in `simulate_snake_plan()`'s signature to the largest value that fits, re-run the full test suite (the tests above use explicit `sims=` values so this default change shouldn't break them — confirm this is true), and note the new default and measured time in your report.
- **If even `sims=100` doesn't fit in the budget**: STOP. Do not proceed to Task 4. Report BLOCKED with the measured numbers. The pre-approved fallback (spec §7) is an on-demand button with a dedicated endpoint instead of the automatic heavy-refresh cadence — this requires a design change the controller/user should confirm before more work is built on top of an approach that won't ship as planned, not something to decide unilaterally mid-task.

Delete the benchmark script when done (it's a one-time measurement, not a permanent test — a real performance regression test would need `pytest-benchmark` or similar infrastructure this repo doesn't have, which is out of scope here).

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/engine/snake_plan.py tests/engine/test_snake_plan.py
git commit -m "feat: add simulate_snake_plan() -- roll the rest of the draft forward"
```

---

### Task 4: Wire `simulate_snake_plan()` into the snake board API

**Files:**
- Modify: `src/ffdo/api/app.py`
- Modify: `src/ffdo/api/board.py`
- Test: `tests/api/test_snake_board.py`

**Interfaces:**
- Consumes: `snake_plan.simulate_snake_plan(valued, adp, state, league, roster_id) -> dict | None` (Task 3).
- Produces: `build_snake_board(...)`'s returned payload gains a top-level `"snake_plan"` key (present and possibly `None`-valued when the feature returns `None` -- unlike the auction Optimal Plan feature, which omits its key entirely when there's nothing to show, this one is always present so the frontend can distinguish "still loading" from "not available yet before your first pick" cleanly with a single `if (!plan)` check either way; document this explicitly since it's a deliberate difference from the auction precedent, not an inconsistency). The new parameter has a default of `None` so every pre-existing call in this file's 9 other tests (none of which pass it) keeps working unchanged.

- [ ] **Step 1: Write the wiring test**

Add to `tests/api/test_snake_board.py`, using this file's existing module-level `_league()`/`_valued()`/`_state()` helpers exactly as every other test in the file already does:

```python
def test_build_snake_board_includes_snake_plan_key():
    valued = _valued()
    survival = {pid: 0.5 for pid in valued}
    plan = {
        "picks": [{"pick_no": 15, "picks_from_now": 1, "most_likely_position": "RB",
                   "position_hit_rate": 0.6, "most_likely_player_id": "RB0",
                   "most_likely_player_name": "RB 0", "player_hit_rate": 0.4}],
        "expected_starting_vor": 250.0, "sims_run": 200,
    }

    out = board.build_snake_board(_league(), _state(), valued, survival, {}, plan)
    assert out["snake_plan"] == plan

    out_none = board.build_snake_board(_league(), _state(), valued, survival, {})
    assert out_none["snake_plan"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/api/test_snake_board.py -v -k snake_plan`

Expected: FAIL — `KeyError: 'snake_plan'` (the payload doesn't have this key yet).

- [ ] **Step 3: Wire `snake_plan` into `build_snake_board()`**

In `src/ffdo/api/board.py`, `build_snake_board()`'s current signature is:

```python
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
```

Add `snake_plan: dict | None = None,` as a new parameter right after `cost_of_waiting` and *before* the `*` — not keyword-only, so the new test can pass it positionally (as written in Step 1) while every pre-existing call in this file (which omits it entirely) still works unchanged via the default:

```python
def build_snake_board(
    league,
    state: DraftState,
    valued: Mapping[str, ValuedPlayer],
    survival: Mapping[str, float],
    cost_of_waiting: Mapping[str, Mapping[str, float]],
    snake_plan: dict | None = None,
    *,
    roster_id: int | None = None,
    teams: Mapping[int, TeamProfile] | None = None,
) -> dict:
```

Add `"snake_plan": snake_plan,` to the returned dict, as a sibling of `"cost_of_waiting"`.

- [ ] **Step 4: Wire the call site in `app.py`**

In `src/ffdo/api/app.py`, in the snake branch (currently lines 476-485), add the `simulate_snake_plan` call and pass its result through:

```python
        else:
            from ffdo.engine import market
            from ffdo.engine import snake_plan as snake_plan_mod
            available = {pid for pid in valued if pid not in state.drafted_player_ids()}
            adp_means = {pid: a.adp["half_ppr"] for pid, a in adp_data.items()
                        if a.adp.get("half_ppr", 999) < 999}
            picks_until = lg.num_teams  # conservative: one full round
            survival = market.simulate_survival(adp_means, available, picks_until)
            cow = market.cost_of_waiting(valued, survival, available)
            plan = snake_plan_mod.simulate_snake_plan(valued, adp_means, state, lg, roster_id)
            board = board_mod.build_snake_board(
                lg, state, valued, survival, cow, plan, roster_id=roster_id, teams=teams)
```

(`adp_means` is already computed here for the survival call — reused directly, no new data fetching.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_snake_board.py -v`

Expected: PASS — the new test plus every pre-existing test in the file unchanged.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`

Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/api/app.py src/ffdo/api/board.py tests/api/test_snake_board.py
git commit -m "feat: wire simulate_snake_plan() into the snake board API payload"
```

---

### Task 5: UI — the snake-plan panel, and the sidebar layout fix

**Files:**
- Modify: `src/ffdo/web/board/index.html`
- Modify: `src/ffdo/web/board/board.js`
- Modify: `src/ffdo/web/board/board.css`

**Interfaces:**
- Consumes: `state.data.snake_plan` (Task 4's payload shape: `{"picks": [...], "expected_starting_vor": number, "sims_run": number}` or `null`), each pick `{"pick_no", "picks_from_now", "most_likely_position", "position_hit_rate", "most_likely_player_id", "most_likely_player_name", "player_hit_rate"}`.
- Produces: no new interface — `renderSnakePlan()` is a void function called from `render()`, following the exact pattern `renderCow()`/`renderOptimalPlan()` already establish.

This task has two parts: (A) build the new panel, and (B) fix the sidebar/cow/position-budget layout so the whole left column fits the viewport without page scrolling — both empirically validated together, since the new panel is itself one more thing that has to fit into that layout. Do both in this task, verified together in Task 6.

- [ ] **Step 1: Add the snake-plan panel markup**

In `src/ffdo/web/board/index.html`, insert a new `<aside>` right after the existing `</aside>` that closes `#optimal-plan` (this panel is snake-only, `#optimal-plan` is auction-only — they're siblings that never both show, same as `#cow`/`#position-budget`):

```html
  <aside id="snake-plan">
    <div class="snakeplan-head">
      <h2>Draft plan</h2>
      <span class="snakeplan-sub">most likely pick at each of your remaining turns, simulated forward</span>
    </div>
    <div class="snakeplan-totals">
      <span><b id="snakeplan-vor">&mdash;</b> expected starting VOR</span>
    </div>
    <div id="snakeplan-rows"></div>
  </aside>
```

- [ ] **Step 2: Add `renderSnakePlan()` and hook it into `render()`**

In `src/ffdo/web/board/board.js`, add this new function near `renderOptimalPlan()`:

```javascript
function renderSnakePlan() {
  const el = document.getElementById("snake-plan");
  const d = state.data;
  const plan = d && d.snake_plan;
  if (d.format !== "snake" || !plan) {
    el.hidden = true;
    return;
  }
  el.hidden = false;

  document.getElementById("snakeplan-vor").textContent = plan.expected_starting_vor;

  document.getElementById("snakeplan-rows").innerHTML = plan.picks.map(p => {
    const posColor = `var(--${p.most_likely_position.toLowerCase()}, var(--muted))`;
    return `<div class="snakeplan-row">
      <span class="snakeplan-pickno">#${p.picks_from_now}</span>
      <span class="snakeplan-pos" style="color:${posColor}">${p.most_likely_position}</span>
      <span class="snakeplan-name">${escapeHtml(p.most_likely_player_name)}</span>
      <span class="snakeplan-rate">${Math.round(p.player_hit_rate * 100)}%</span>
    </div>`;
  }).join("");
}
```

Then, in `render()`, add a call to `renderSnakePlan();` immediately after the existing `renderCow();` line (both are snake-only, so grouping them keeps the format-specific calls together).

- [ ] **Step 3: Compact `#cow` and `#position-budget` so they take less vertical height**

These two full-width bars sit above the sidebar and eat into the same vertical budget — this was the actual root cause found empirically (not just the sidebar panels themselves). In `src/ffdo/web/board/board.css`, replace the `#cow-rows`/`.cow-row` block:

```css
#cow-rows { display: grid; grid-template-columns: repeat(2, 1fr); gap: 2px 14px; }
.cow-row { display: flex; align-items: center; gap: 10px; padding: 7px 6px; border-radius: 7px; }
.cow-row:hover { background: var(--surface-2); }
.cow-pos { width: 32px; font-family: var(--font-mono); font-size: 13px; font-weight: 700; flex-shrink: 0; }
.cow-stat { display: flex; flex-direction: column; gap: 1px; width: 62px; flex-shrink: 0; }
.cow-stat .label { font-size: 8.5px; }
.cow-stat b { font-family: var(--font-mono); font-size: 12.5px; font-weight: 600; }
.cow-stat.next b { color: var(--muted); }
.cow-cost { flex: 1; display: flex; flex-direction: column; gap: 3px; justify-content: center; min-width: 0; }
.cow-cost-line { display: flex; align-items: baseline; gap: 6px; }
.cow-cost-num { font-family: var(--font-mono); font-size: 15px; font-weight: 700; }
.cow-cost-label { font-size: 9.5px; color: var(--faint); }
.cow-bar-track { height: 5px; background: var(--surface-2); border-radius: 3px; overflow: hidden; }
.cow-bar-fill { height: 100%; border-radius: 3px; }
.cow-tag { width: 40px; text-align: right; font-family: var(--font-mono); font-size: 9.5px; font-weight: 600; letter-spacing: .3px; flex-shrink: 0; }
```

(this replaces the entire existing `#cow-rows`/`.cow-row`/`.cow-pos`/`.cow-stat`/`.cow-stat .label`/`.cow-stat b`/`.cow-stat.next b`/`.cow-cost`/`.cow-cost-line`/`.cow-cost-num`/`.cow-cost-label`/`.cow-bar-track`/`.cow-bar-fill`/`.cow-tag` rule block — same selectors, new values, laying the 4-5 position rows out as a 2-column grid instead of one stacked column, roughly halving the section's height).

Replace the `#posbudget-rows`/`.posbudget-row`/`.posbudget-reserve` block the same way:

```css
#posbudget-rows { display: grid; grid-template-columns: repeat(3, 1fr); gap: 2px 14px; }
.posbudget-row { display: flex; align-items: center; gap: 10px; padding: 7px 6px; border-radius: 7px; }
.posbudget-row:hover { background: var(--surface-2); }
.posbudget-pos { width: 32px; font-family: var(--font-mono); font-size: 13px; font-weight: 700; flex-shrink: 0; }
.posbudget-amount { font-family: var(--font-mono); font-size: 15px; font-weight: 700; width: 52px; flex-shrink: 0; }
.posbudget-slots { font-size: 9.5px; color: var(--faint); width: 66px; flex-shrink: 0; }
.posbudget-bar-track { flex: 1; min-width: 24px; height: 5px; background: var(--surface-2); border-radius: 3px; overflow: hidden; }
.posbudget-bar-fill { height: 100%; border-radius: 3px; background: var(--accent); }
.posbudget-reserve {
  grid-column: 1 / -1;
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 8px 6px; margin-top: 4px; border-top: 1px solid var(--border);
  font-size: 11.5px; color: var(--faint);
}
```

(5 position rows lay out 3-per-row instead of stacked; `.posbudget-reserve` gets `grid-column: 1 / -1` so it still spans the full width as a single summary line below the grid, not squeezed into one column cell.)

- [ ] **Step 4: Make `body` fill the viewport with no page-level scroll**

Replace `body`'s rule in `board.css` (currently just `margin/padding/background/color/font/min-height:100vh`):

```css
body {
  margin: 0;
  padding: 20px 28px 28px;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.4 var(--font-sans);
  height: 100vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
```

- [ ] **Step 5: Let `#layout` fill the remaining vertical space, and give `#sidebar` a safety-net scroll**

Replace the `/* ---- layout ---- */` block:

```css
/* ---- layout ---- */
#layout { display: flex; gap: 18px; align-items: flex-start; flex: 1; min-height: 0; }
#sidebar {
  display: flex; flex-direction: column; gap: 12px; width: 320px; flex-shrink: 0;
  align-self: stretch; min-height: 0; overflow-y: auto;
}
```

(`#board-panel` is untouched — it keeps its existing `align-self: flex-start` default from not being overridden, so its own `#board-scroll { max-height: 78vh }` self-contained scroll behavior is unaffected by this change.)

- [ ] **Step 6: Make the four content panels share the sidebar's space instead of stacking to their full natural height**

Replace `#rosters`'s, `#history`'s, and `#optimal-plan`'s rules (each currently `flex-shrink: 0; ...`) to grow/shrink together and share whatever space is left after `#nominated` (which keeps its existing fixed `min-height: 200px`, untouched):

```css
#rosters {
  flex: 1 1 0; min-height: 130px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.rosters-head { flex-shrink: 0; }
```

```css
#history {
  flex: 1 1 0; min-height: 130px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.history-head { flex-shrink: 0; }
```

```css
#optimal-plan {
  flex: 1 1 0; min-height: 130px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.plan-head { flex-shrink: 0; }
```

(Only the first line of each block changes -- `flex-shrink: 0;` becomes `flex: 1 1 0; min-height: 130px;` -- plus one new `.rosters-head`/`.history-head`/`.plan-head { flex-shrink: 0; }` rule per panel so the header text itself never gets squeezed by the flex distribution, only the rows list below it does.)

Give the new `#snake-plan` panel (added in Step 1) the same treatment from the start, rather than the old fixed-height pattern:

```css
#snake-plan {
  flex: 1 1 0; min-height: 130px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.snakeplan-head { flex-shrink: 0; }
.snakeplan-head h2 { font-size: 13px; font-weight: 600; margin: 0 0 2px; }
.snakeplan-sub {
  display: block; font-size: 11px; color: var(--faint);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.snakeplan-totals { flex-shrink: 0; display: flex; gap: 14px; padding: 6px 4px 10px; font-size: 11.5px; color: var(--faint); }
.snakeplan-totals b { font-family: var(--font-mono); font-size: 13px; color: var(--text); font-weight: 700; }
#snakeplan-rows { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow-y: auto; }
.snakeplan-row { display: flex; align-items: center; gap: 10px; padding: 8px 4px; border-radius: 7px; }
.snakeplan-row:hover { background: var(--surface-2); }
.snakeplan-pickno { width: 28px; font-family: var(--font-mono); font-size: 11px; color: var(--faint); flex-shrink: 0; }
.snakeplan-pos { width: 32px; font-family: var(--font-mono); font-size: 11px; font-weight: 700; flex-shrink: 0; }
.snakeplan-name { flex: 1; min-width: 0; font-size: 12.5px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.snakeplan-rate { font-family: var(--font-mono); font-size: 11px; color: var(--faint); width: 40px; text-align: right; flex-shrink: 0; }
```

- [ ] **Step 7: Replace each panel's fixed row-list cap with a flexible one, and truncate the existing subtitles**

Replace `#rosters-rows { display: flex; flex-direction: column; max-height: 360px; overflow-y: auto; }` with:

```css
#rosters-rows { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow-y: auto; }
```

Replace `#history-rows { display: flex; flex-direction: column; max-height: 360px; overflow-y: auto; }` with:

```css
#history-rows { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow-y: auto; }
```

Replace `#plan-rows { display: flex; flex-direction: column; max-height: 360px; overflow-y: auto; }` with:

```css
#plan-rows { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow-y: auto; }
```

Truncate the three existing subtitles the same way `.snakeplan-sub` already is (Step 6) — replace each of these three single-line rules:

```css
.rosters-sub { display: block; font-size: 11px; color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.history-sub { display: block; font-size: 11px; color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.plan-sub { display: block; font-size: 11px; color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
```

(each replaces the existing one-line `font-size: 11px; color: var(--faint);` rule for that selector.)

- [ ] **Step 8: Keep the existing mobile layout working**

The `@media (max-width: 900px)` block at the end of the file switches `#layout` to a stacked column — with `body` now `overflow: hidden`, that stacked content would be clipped instead of scrollable on narrow viewports. Replace the media query block:

```css
@media (max-width: 900px) {
  /* Stacked layout can't fit table + sidebar in one screen -- let the
     whole layout column scroll here instead of the desktop no-scroll
     constraint, which would otherwise clip content below the fold. */
  body { overflow: visible; height: auto; }
  #layout { flex-direction: column; overflow: visible; }
  #sidebar { width: 100%; overflow: visible; }
}
```

- [ ] **Step 9: Syntax-check the JS**

Run: `node --check src/ffdo/web/board/board.js` (or a careful manual read if node is unavailable).

Expected: no output (success), or a careful manual read confirming valid syntax.

- [ ] **Step 10: Commit**

```bash
git add src/ffdo/web/board/index.html src/ffdo/web/board/board.js src/ffdo/web/board/board.css
git commit -m "feat: add snake draft plan panel, fix sidebar layout to avoid page scroll"
```

---

### Task 6: Manual browser verification

**Files:** none (verification only)

**Interfaces:**
- Consumes: the running app at `http://localhost:8000`, backed by Tasks 1-5's changes.

- [ ] **Step 1: Start the dev server**

Run: `uv run uvicorn ffdo.api.app:app --port 8000` (background/long-running). If port 8000 is already in use by another process, use a different port (e.g. `--port 8010`) rather than stopping whatever's already running there — it may be a real, in-use session.

- [ ] **Step 2: Verify the snake board**

Navigate to the board with a snake-format league connected (check `README.md`'s env-var instructions — `FFDO_LEAGUE_ID`/`FFDO_DRAFT_ID` pointed at a snake draft, or use the repo's existing mock-draft/snapshot fixtures if a live snake league isn't available). Confirm:
- The new "Draft plan" panel renders in the sidebar with real pick rows (position, player name, hit rate %) and an expected-starting-VOR header stat, once your draft slot is determined (after your first pick) -- and stays cleanly hidden (not showing broken/empty content) before that.
- No JS console errors.

- [ ] **Step 3: Verify the layout fits without page scrolling**

At a normal desktop browser window size (e.g. resize to at least 1280x900), confirm the page itself does not need to scroll to see the bottom of the sidebar -- check via the browser's own scrollbar presence, or run in the console: `document.body.scrollHeight > document.body.clientHeight` should be `false` (or the visual absence of a page-level scrollbar). Confirm `#cow`/`#position-budget` (whichever is active for the connected league's format) renders as a compact multi-column grid, not a single tall stack. If a panel's own internal row-list needs its own scroll (expected for a league with many teams/picks), confirm that's a small internal scrollbar within that one card, not the whole page.

- [ ] **Step 4: Verify the auction board still works**

Switch to (or separately load) an auction-format league and confirm the existing Position Budget strip and Optimal Plan panel still render correctly with the new compact grid layout -- this plan's layout changes apply to both formats, so both need checking, not just snake.

- [ ] **Step 5: Stop the dev server**

No commit for this task -- manual verification only.
