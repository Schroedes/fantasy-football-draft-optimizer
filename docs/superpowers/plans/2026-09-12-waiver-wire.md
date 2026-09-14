# Waiver-Wire Recommender Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recommend which free agents to add, what to drop for each, and how much FAAB to bid — bid amounts fit from the user's own leagues' real historical waiver-claim outcomes, add/drop logic respecting positional roster caps.

**Architecture:** A new `ingest/sleeper/waivers.py` parses real waiver transactions and computes remaining FAAB budget via a chronological running-spend walk (shared between offline fitting and the live endpoint). A new `ingest/sleeper/historical_weekly_stats.py` fetches real per-week historical stats, needed to reconstruct what a historical claim was actually worth at the time. `scripts/fit_faab_curve.py` combines both to fit `domain/constants.py::FAAB_BID_CURVE` (mirrors #4/#5's fitted-curve pattern exactly). `engine/waiver_value.py` computes the free-agent pool, positional caps, and the cap-aware add/drop recommendation. One new endpoint; one new frontend tab.

**Tech Stack:** Python 3.12, FastAPI, pytest, vanilla JS — unchanged from the rest of this codebase.

**Spec:** `docs/superpowers/specs/2026-09-12-waiver-wire-design.md`

## Global Constraints

- FAAB leagues only (Sleeper `waiver_type == 2`, confirmed live against all three tracked leagues). Any other provider or waiver type gets a 400, matching `/lineup`'s ESPN gate.
- No decision ledger in this plan — deferred to a future fast-follow (spec §0, §2).
- `MIN_SAMPLE = 3` for `FAAB_BID_CURVE` bucket population — same threshold and rationale as #5's `fit_pick_value.py` (1-2 points is noise for this size of real sample).
- `POSITION_CAP_EXTRA = {"QB": 1, "TE": 1, "DEF": 0, "K": 0}` (RB/WR absent = uncapped) — exact values confirmed against the user's own worked examples during brainstorming (spec §5.2).
- `MIN_VOR_GAIN = 5.0` — a controller judgment call for the minimum VOR edge before recommending an add at all, not backtested (spec §5.3).
- **Learned from sub-project #5's most consequential bug**: a "week" bound used for scoring/actuals (`_through_week`, meaning "last week with FINAL stats") is NOT the same as a "week" bound used for scanning transaction history (which must include the current, still-in-progress week, since a waiver claim can happen before that week's stats are final). Every task touching both must use the correct one explicitly — `nfl.week` (live current week) for any transaction-scanning bound, never the stats-final `through_week` variable for that purpose. This exact confusion produced a Critical bug in #5 that a manual smoke test had to catch; do not reintroduce it here.
- Every task's tests must include at least one check of real magnitude against real fitted data for the curve-fitting-derived module (`engine/waiver_value.py::suggested_bid`, Task 5) specifically — the same scoping ruling #5's pre-flight scan made for its own equivalent constraint applies here: this requirement is about the module analogous to #4's `annuity_value`/#5's `slot_value`, not every task in the plan.
- `roster_value`, `ValuedPlayer`, `RosterEntry`, `INJURY_OUT_STATUSES`, `FLEX_ELIGIBILITY` are reused unchanged — this plan only ADDS to the valuation surface.

---

### Task 1: `ingest/sleeper/waivers.py` — real waiver transaction parsing + running-spend walk

**Files:**
- Create: `src/ffdo/ingest/sleeper/waivers.py`
- Modify: `src/ffdo/domain/models.py` (add `WaiverClaim`)
- Test: `tests/ingest/sleeper/test_waivers.py`

**Interfaces:**
- Consumes: `ffdo.ingest.client.{V1, SleeperClient}` (existing).
- Produces: `WaiverClaim(transaction_id: str, season: int, week: int, roster_id: int, player_id: str, bid_amount: float, created_ms: int)` (new frozen dataclass in `domain/models.py`), `fetch_waivers(sleeper, league_id, *, season, through_week) -> list[WaiverClaim]` (consumed by both Task 3 and Task 8), `remaining_budget(claims: Sequence[WaiverClaim], *, waiver_budget: float) -> dict[int, float]` (consumed by Task 8's live endpoint only — Task 3 needs each claim's *intermediate* remaining-budget state, not just the final total, so it re-walks chronologically inline rather than calling this function; see Task 3's note).

- [ ] **Step 1: Write the failing tests**

```python
# tests/ingest/sleeper/test_waivers.py
from ffdo.domain.models import WaiverClaim
from ffdo.ingest.sleeper import waivers


class _FakeClient:
    def __init__(self, by_week):
        self._by_week = by_week

    def get_json(self, url):
        for week, payload in self._by_week.items():
            if f"/transactions/{week}" in url:
                return payload
        raise AssertionError(f"unexpected URL: {url}")


def test_fetch_waivers_filters_to_completed_waivers_only():
    client = _FakeClient({
        1: [
            {"type": "waiver", "status": "complete", "transaction_id": "w1",
             "roster_ids": [3], "adds": {"p1": 3},
             "settings": {"waiver_bid": 12}, "created": 1000},
            {"type": "trade", "status": "complete", "transaction_id": "t1",
             "roster_ids": [2, 3], "adds": {"p9": 2}, "created": 900},
            {"type": "waiver", "status": "failed", "transaction_id": "w2",
             "roster_ids": [4], "adds": {"p2": 4},
             "settings": {"waiver_bid": 5}, "created": 1100},
            {"type": "free_agent", "status": "complete", "transaction_id": "f1",
             "roster_ids": [5], "adds": {"p3": 5}, "created": 1200},
        ],
    })
    result = waivers.fetch_waivers(client, "L1", season=2026, through_week=1)
    assert len(result) == 1
    assert result[0].transaction_id == "w1"


def test_fetch_waivers_parses_the_real_fields():
    client = _FakeClient({
        1: [{"type": "waiver", "status": "complete", "transaction_id": "w1",
             "roster_ids": [3], "adds": {"p1": 3},
             "settings": {"waiver_bid": 12}, "created": 1700000000000}],
    })
    result = waivers.fetch_waivers(client, "L1", season=2026, through_week=1)
    claim = result[0]
    assert claim.season == 2026
    assert claim.week == 1
    assert claim.roster_id == 3
    assert claim.player_id == "p1"
    assert claim.bid_amount == 12.0
    assert claim.created_ms == 1700000000000


def test_fetch_waivers_defaults_a_missing_bid_to_zero():
    """A `free_agent`-shaped waiver claim (uncontested, no bid) still has
    type=='waiver' on some leagues -- settings/waiver_bid can be absent or
    None rather than 0. Must not crash, must default to 0.0."""
    client = _FakeClient({
        1: [{"type": "waiver", "status": "complete", "transaction_id": "w1",
             "roster_ids": [3], "adds": {"p1": 3}, "settings": None,
             "created": 1000}],
    })
    result = waivers.fetch_waivers(client, "L1", season=2026, through_week=1)
    assert result[0].bid_amount == 0.0


def test_fetch_waivers_scans_every_week_through_the_given_week():
    client = _FakeClient({
        1: [{"type": "waiver", "status": "complete", "transaction_id": "w1",
             "roster_ids": [3], "adds": {"p1": 3},
             "settings": {"waiver_bid": 1}, "created": 1000}],
        2: [{"type": "waiver", "status": "complete", "transaction_id": "w2",
             "roster_ids": [3], "adds": {"p2": 3},
             "settings": {"waiver_bid": 2}, "created": 2000}],
    })
    result = waivers.fetch_waivers(client, "L1", season=2026, through_week=2)
    assert {c.transaction_id for c in result} == {"w1", "w2"}


def test_remaining_budget_walks_claims_in_chronological_order_not_feed_order():
    """The single most important property of this function: claims are
    fed in REVERSED chronological order here (created=3000 first,
    created=1000 second) -- if remaining_budget sorted by feed order
    instead of created_ms, the running total would be computed wrong."""
    claims = [
        WaiverClaim(transaction_id="later", season=2026, week=2, roster_id=5,
                   player_id="p2", bid_amount=30.0, created_ms=3000),
        WaiverClaim(transaction_id="earlier", season=2026, week=1, roster_id=5,
                   player_id="p1", bid_amount=10.0, created_ms=1000),
    ]
    result = waivers.remaining_budget(claims, waiver_budget=100.0)
    # After both claims, roster 5 has spent 10 + 30 = 40 total, regardless
    # of the order they were passed in -- the function must sort them.
    assert result[5] == 60.0


def test_remaining_budget_tracks_each_roster_independently():
    claims = [
        WaiverClaim(transaction_id="a", season=2026, week=1, roster_id=1,
                   player_id="p1", bid_amount=20.0, created_ms=1000),
        WaiverClaim(transaction_id="b", season=2026, week=1, roster_id=2,
                   player_id="p2", bid_amount=5.0, created_ms=1100),
    ]
    result = waivers.remaining_budget(claims, waiver_budget=100.0)
    assert result[1] == 80.0
    assert result[2] == 95.0


def test_remaining_budget_defaults_untouched_rosters_to_the_full_budget():
    result = waivers.remaining_budget([], waiver_budget=150.0)
    assert result == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/sleeper/test_waivers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.ingest.sleeper.waivers'`

- [ ] **Step 3: Add `WaiverClaim` to `domain/models.py`**

Append to `src/ffdo/domain/models.py`:

```python
@dataclass(frozen=True, slots=True)
class WaiverClaim:
    transaction_id: str
    season: int
    week: int
    roster_id: int
    player_id: str
    bid_amount: float
    created_ms: int
```

- [ ] **Step 4: Write the implementation**

```python
# src/ffdo/ingest/sleeper/waivers.py
"""Real completed FAAB waiver claims, from /league/<id>/transactions/<week>
-- the sibling of ingest.sleeper.transactions.fetch_trades, filtered to
type == "waiver" instead of "trade". Also provides the chronological
running-FAAB-spend walk shared by scripts/fit_faab_curve.py (historical
fitting) and the live /waivers endpoint (current remaining budget) --
both need "walk this season's waiver claims in order, track cumulative
spend per roster," so it lives here once rather than being duplicated.
"""

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
                bid_amount=float(bid), created_ms=int(raw["created"])))
    return out


def remaining_budget(
    claims: Sequence[WaiverClaim], *, waiver_budget: float,
) -> dict[int, float]:
    spent: dict[int, float] = {}
    for claim in sorted(claims, key=lambda c: c.created_ms):
        spent[claim.roster_id] = spent.get(claim.roster_id, 0.0) + claim.bid_amount
    return {roster_id: waiver_budget - total for roster_id, total in spent.items()}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/sleeper/test_waivers.py -v`
Expected: PASS, all 7 tests

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/ingest/sleeper/waivers.py src/ffdo/domain/models.py tests/ingest/sleeper/test_waivers.py
git commit -m "feat: ingest.sleeper.waivers -- real FAAB claim detection + running-spend walk"
```

---

### Task 2: `ingest/sleeper/historical_weekly_stats.py` — real per-week historical stats

**Files:**
- Create: `src/ffdo/ingest/sleeper/historical_weekly_stats.py`
- Test: `tests/ingest/sleeper/test_historical_weekly_stats.py`

**Interfaces:**
- Consumes: `ffdo.ingest.client.{V1, SleeperClient}` (existing).
- Produces: `fetch(sleeper, season: int, week: int) -> dict[str, dict[str, float]]` (player_id -> raw numeric stat dict for that one week) -- consumed by Task 3's fitting script only.

- [ ] **Step 1: Write the failing tests**

```python
# tests/ingest/sleeper/test_historical_weekly_stats.py
from ffdo.ingest.sleeper import historical_weekly_stats


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    def get_json(self, url):
        assert "/stats/nfl/regular/2025/6" in url
        return self._payload


def test_fetch_parses_numeric_stats_only():
    client = _FakeClient({
        "p1": {"pts_half_ppr": 14.2, "rec_yd": 55, "player_active": True},
        "p2": "not a dict",
    })
    result = historical_weekly_stats.fetch(client, 2025, 6)
    assert result == {"p1": {"pts_half_ppr": 14.2, "rec_yd": 55.0}}


def test_fetch_returns_empty_for_a_bye_week_player_with_no_stats():
    client = _FakeClient({"p1": {}})
    result = historical_weekly_stats.fetch(client, 2025, 6)
    assert result == {"p1": {}}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/sleeper/test_historical_weekly_stats.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# src/ffdo/ingest/sleeper/historical_weekly_stats.py
"""Real per-week historical stats, from /v1/stats/nfl/regular/<season>/<week>
-- confirmed live to work for any past week, not just the current one
(unlike ingest.sleeper.weekly_projections, which is live-current-week
only). Used ONLY by scripts/fit_faab_curve.py to reconstruct what a
historical waiver claim was actually worth at the time -- never called
from the live app path."""

from __future__ import annotations

from ffdo.ingest.client import V1, SleeperClient


def fetch(sleeper: SleeperClient, season: int, week: int) -> dict[str, dict[str, float]]:
    raw = sleeper.get_json(f"{V1}/stats/nfl/regular/{season}/{week}")
    out: dict[str, dict[str, float]] = {}
    for player_id, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        out[player_id] = {
            k: float(v) for k, v in rec.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/sleeper/test_historical_weekly_stats.py -v`
Expected: PASS, both tests

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/ingest/sleeper/historical_weekly_stats.py tests/ingest/sleeper/test_historical_weekly_stats.py
git commit -m "feat: ingest.sleeper.historical_weekly_stats -- real per-week historical stats"
```

---

### Task 3: `scripts/fit_faab_curve.py` — fit and run for real

**Files:**
- Create: `scripts/fit_faab_curve.py`

**Interfaces:**
- Consumes: `waivers.{fetch_waivers, remaining_budget, WaiverClaim}` (Task 1), `historical_weekly_stats.fetch` (Task 2), `ffdo.engine.scoring.score_stats`, `ffdo.engine.vor.compute`, `ffdo.domain.constants.STANDARD_HALF_PPR`, `ffdo.domain.models.LeagueProfile`, `ffdo.ingest.players`.
- Produces: a printed Python literal for `FAAB_BID_CURVE`, `{vor_gain_bucket_floor: bid_pct}` -- reviewed by a human, never auto-written (same posture as every other fitting script in this project).

No pytest tests for this task -- a one-off script judged by running it for real, same posture as `fit_age_curve.py`/`fit_pick_value.py`.

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python
"""Fits FAAB_BID_CURVE from the user's own tracked leagues' real waiver-
claim history -- prints the result as a Python literal for review, never
writes the file itself (same posture as fit_age_curve.py/fit_pick_value.py).

Usage: uv run python scripts/fit_faab_curve.py <league-id> <season> [<league-id> <season> ...]

Each <league-id>/<season> pair identifies one real historical season to
fit from (a league_id is season-specific on Sleeper, unlike a league's
persistent identity -- pass the exact league_id for each season you want
included, not just the current-season id).
"""

from __future__ import annotations

import argparse
import sys

from ffdo.domain.constants import STANDARD_HALF_PPR, SEASON_LENGTH
from ffdo.domain.models import LeagueProfile
from ffdo.engine import vor
from ffdo.engine.scoring import score_stats
from ffdo.ingest import players as players_mod
from ffdo.ingest.client import V1, SleeperClient
from ffdo.ingest.sleeper import historical_weekly_stats, waivers

MIN_SAMPLE = 3
BUCKET_WIDTH = 10

_REFERENCE_ROSTER = (
    "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX",
    "BN", "BN", "BN", "BN", "BN", "BN", "BN",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fit FAAB_BID_CURVE from real historical waiver claims")
    parser.add_argument("league_season_pairs", nargs="+",
                        help="alternating league_id season league_id season ...")
    args = parser.parse_args()
    pairs = args.league_season_pairs
    if len(pairs) % 2 != 0:
        print("Pass league_id/season pairs, e.g. L1 2025 L2 2025", file=sys.stderr)
        sys.exit(1)

    sleeper = SleeperClient()
    try:
        profiles = players_mod.parse(sleeper.get_json(f"{V1}/players/nfl"))

        observations: list[tuple[float, float]] = []  # (vor_gain, bid_pct)
        for i in range(0, len(pairs), 2):
            league_id, season = pairs[i], int(pairs[i + 1])
            league_raw = sleeper.get_json(f"{V1}/league/{league_id}")
            waiver_budget = float(league_raw.get("settings", {}).get("waiver_budget") or 0)
            if waiver_budget <= 0:
                print(f"skipping {league_id}/{season}: no FAAB budget configured",
                      file=sys.stderr)
                continue
            season_weeks = len(SEASON_LENGTH) and SEASON_LENGTH.get(season, 18)
            claims = waivers.fetch_waivers(sleeper, league_id, season=season,
                                            through_week=season_weeks)
            # Cache each week's stats ONCE per league-season and reuse
            # across every claim that needs it, rather than re-fetching
            # the same week many times over -- a league-season with dozens
            # of claims spread across the year would otherwise make many
            # redundant live HTTP calls for the same (season, week) pair.
            week_stats_cache: dict[int, dict] = {}

            def _week_stats(wk: int) -> dict:
                if wk not in week_stats_cache:
                    week_stats_cache[wk] = historical_weekly_stats.fetch(sleeper, season, wk)
                return week_stats_cache[wk]
            # This re-walks chronologically rather than calling
            # waivers.remaining_budget(): that function only returns each
            # roster's FINAL remaining total after every claim, but fitting
            # needs the INTERMEDIATE remaining-before-THIS-claim value at
            # every step, one fitting observation per claim -- a different
            # need from the live endpoint's "what's left right now."
            budgets_after_each: dict[int, float] = {}
            for claim in sorted(claims, key=lambda c: c.created_ms):
                remaining_before = waiver_budget - budgets_after_each.get(claim.roster_id, 0.0)
                if remaining_before <= 0:
                    budgets_after_each[claim.roster_id] = (
                        budgets_after_each.get(claim.roster_id, 0.0) + claim.bid_amount)
                    continue
                bid_pct = claim.bid_amount / remaining_before

                rest_of_season_points: dict[str, float] = {}
                for wk in range(claim.week + 1, season_weeks + 1):
                    week_stats = _week_stats(wk)
                    for pid, stat_line in week_stats.items():
                        rest_of_season_points[pid] = (
                            rest_of_season_points.get(pid, 0.0)
                            + score_stats(stat_line, STANDARD_HALF_PPR))

                reference = LeagueProfile(
                    league_id="reference", season=season, num_teams=12,
                    roster_positions=_REFERENCE_ROSTER,
                    scoring_settings=STANDARD_HALF_PPR, budget=None)
                valued = vor.compute(rest_of_season_points, profiles, reference)
                vp = valued.get(claim.player_id)
                if vp is None:
                    budgets_after_each[claim.roster_id] = (
                        budgets_after_each.get(claim.roster_id, 0.0) + claim.bid_amount)
                    continue

                observations.append((vp.vor, bid_pct))
                budgets_after_each[claim.roster_id] = (
                    budgets_after_each.get(claim.roster_id, 0.0) + claim.bid_amount)
    finally:
        sleeper.close()

    if not observations:
        print("No usable waiver observations found.", file=sys.stderr)
        sys.exit(1)

    buckets: dict[int, list[float]] = {}
    for vor_gain, bid_pct in observations:
        bucket = (int(vor_gain) // BUCKET_WIDTH) * BUCKET_WIDTH
        buckets.setdefault(bucket, []).append(bid_pct)

    curve = {
        bucket: round(sum(pcts) / len(pcts), 4)
        for bucket, pcts in buckets.items()
        if len(pcts) >= MIN_SAMPLE
    }

    print(f"# Fit from {len(observations)} real waiver observations across "
          f"{len(pairs) // 2} league-seasons, MIN_SAMPLE={MIN_SAMPLE}, "
          f"BUCKET_WIDTH={BUCKET_WIDTH}")
    print("FAAB_BID_CURVE: Final[dict[int, float]] = {")
    for bucket in sorted(curve):
        print(f"    {bucket}: {curve[bucket]},")
    print("}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it for real**

Run against the user's real tracked leagues' current-season league_ids
(each `league_id` is season-specific on Sleeper, so pass this season's ids
for this season's data — the script does not walk `previous_league_id`
chains itself, unlike Task 1/2 of #5's plan, since waiver history for
past seasons would need each PAST season's own league_id, which the
implementer should discover the same way #5's brainstorming did, by
reading `previous_league_id` from each league's `/league/<id>` response
and passing that season's id explicitly):

```bash
uv run python scripts/fit_faab_curve.py 1315881559957458944 2026 1312210128811872256 2026 1312145369592766464 2026
```

If time/API-load permits and richer history is wanted, also pass each
league's PRIOR season's league_id (discoverable via that league's own
`previous_league_id`, the same chain-walk #5's brainstorming used) —
document in the report exactly which league-seasons were actually used
and why, the same way #5's Task 2 report did.

- [ ] **Step 3: Sanity-check the output**

Confirm: every printed `bid_pct` is a plausible fraction (mostly `0.0` to
somewhere around `1.0`-`3.0` — a real bid CAN exceed 100% of a team's
*remaining* budget if a strong add appears when little budget is left, so
do not treat a value over `1.0` as an error without checking the
underlying observations first); higher VOR-gain buckets generally show
higher `bid_pct` than lower ones (not strictly monotonic — small-sample
noise between adjacent buckets is expected and must NOT be hand-corrected,
same rule as #5's pick-value curve); no `NaN`/`inf`.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions (this task adds no tests of its own)

- [ ] **Step 5: Commit**

```bash
git add scripts/fit_faab_curve.py
git commit -m "feat: scripts/fit_faab_curve.py -- fit and print the FAAB bid curve for review"
```

---

### Task 4: `domain/constants.py::FAAB_BID_CURVE`

**Files:**
- Modify: `src/ffdo/domain/constants.py`
- Test: `tests/domain/test_constants.py` (append)

**Interfaces:**
- Consumes: Task 3's real printed output (pasted in verbatim).
- Produces: `FAAB_BID_CURVE: Final[dict[int, float]]`, consumed by Task 5's `waiver_value.suggested_bid`.

- [ ] **Step 1: Write the failing tests**

```python
# appended to tests/domain/test_constants.py
from ffdo.domain.constants import FAAB_BID_CURVE


def test_faab_bid_curve_keys_are_non_negative_ints():
    for bucket in FAAB_BID_CURVE:
        assert isinstance(bucket, int) and bucket >= 0


def test_faab_bid_curve_values_are_plausible_bid_fractions():
    for pct in FAAB_BID_CURVE.values():
        assert 0.0 <= pct < 5.0


def test_faab_bid_curve_is_not_empty():
    assert len(FAAB_BID_CURVE) > 0
```

**Important — read before running:** the exact numeric bound in
`test_faab_bid_curve_values_are_plausible_bid_fractions` (`< 5.0`) is a
generous guess made before the real fit exists, deliberately loose to
avoid repeating sub-project #5's Task 3 failure (a too-tight bound
rejecting legitimate real data). If the REAL fitted data produced by
Task 3 contains any value that fails this bound, do not silently loosen
it further and do not hand-edit the data — stop and report BLOCKED with
the real values that failed, the same way #5's Task 3 correctly did,
so the controller can rule on it with the real numbers in hand.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/domain/test_constants.py -v -k faab_bid_curve`
Expected: FAIL with `ImportError: cannot import name 'FAAB_BID_CURVE'`

- [ ] **Step 3: Paste in the real constant**

In `src/ffdo/domain/constants.py`, near `DYNASTY_AGE_CURVE`/
`PICK_VALUE_CURVE` (same file, adjacent section — all three are
fitted-from-real-data valuation curves), add:

```python
# Fit from Task 3's real run -- paste the EXACT printed output here,
# including its leading comment line recording how many observations/
# league-seasons/MIN_SAMPLE/BUCKET_WIDTH it was fit from. Do not hand-edit
# the pasted values.
FAAB_BID_CURVE: Final[dict[int, float]] = {
    # <-- paste Task 3's real printed dict body here -->
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/domain/test_constants.py -v -k faab_bid_curve`
Expected: PASS, all 3 tests

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/domain/constants.py tests/domain/test_constants.py
git commit -m "feat: FAAB_BID_CURVE -- real, data-fit waiver bid suggestion curve"
```

---

### Task 5: `engine/waiver_value.py::suggested_bid`

**Files:**
- Create: `src/ffdo/engine/waiver_value.py`
- Test: `tests/engine/test_waiver_value.py`

**Interfaces:**
- Consumes: `FAAB_BID_CURVE`'s shape (Task 4, but most tests use synthetic curves; one test per Global Constraints uses the real constant).
- Produces: `suggested_bid(vor_gain: float, remaining_budget: float, curve: Mapping[int, float], *, bucket_width: int = 10) -> float` — consumed by Task 7's `recommend_adds`.

This is the first function in this file; later tasks append to the same
module rather than creating new files, since `free_agents`/`position_cap`/
`recommend_adds` (Tasks 6-7) are small and share this module's imports.

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_waiver_value.py
import pytest

from ffdo.domain.constants import FAAB_BID_CURVE
from ffdo.engine import waiver_value


def test_exact_bucket_used_when_present():
    curve = {0: 0.05, 10: 0.15, 20: 0.30}
    result = waiver_value.suggested_bid(22.0, 100.0, curve)
    assert result == pytest.approx(30.0)  # bucket 20 -> 0.30 * 100


def test_falls_back_to_nearest_bucket_below_when_exact_bucket_missing():
    curve = {0: 0.05, 20: 0.30}
    result = waiver_value.suggested_bid(15.0, 100.0, curve)
    # bucket for 15.0 is 10 (missing) -- falls to bucket 0, not up to 20
    assert result == pytest.approx(5.0)


def test_never_extrapolates_upward_to_a_higher_bucket():
    curve = {0: 0.05}
    result = waiver_value.suggested_bid(999.0, 100.0, curve)
    # only bucket 0 exists and is <= bucket(999) -- uses it, does not
    # invent a value for an unseen high-VOR bucket
    assert result == pytest.approx(5.0)


def test_returns_zero_when_no_bucket_at_or_below_exists():
    curve = {50: 0.40}
    result = waiver_value.suggested_bid(10.0, 100.0, curve)
    assert result == 0.0


def test_empty_curve_returns_zero():
    result = waiver_value.suggested_bid(50.0, 100.0, {})
    assert result == 0.0


def test_custom_bucket_width_is_respected():
    curve = {0: 0.10, 5: 0.50}
    result = waiver_value.suggested_bid(7.0, 100.0, curve, bucket_width=5)
    assert result == pytest.approx(50.0)


def test_real_curve_higher_vor_gain_bucket_never_suggests_less_than_the_lowest_bucket():
    """Real-magnitude check against the actual fitted curve (Global
    Constraints: the curve-fitting-derived module needs at least one test
    against real data, not just synthetic fixtures -- the exact regression
    class that let bugs into #4's and #5's shipped formulas)."""
    if len(FAAB_BID_CURVE) < 2:
        pytest.skip("real curve has fewer than 2 populated buckets")
    lowest_bucket = min(FAAB_BID_CURVE)
    highest_bucket = max(FAAB_BID_CURVE)
    low_bid = waiver_value.suggested_bid(float(lowest_bucket), 100.0, FAAB_BID_CURVE)
    high_bid = waiver_value.suggested_bid(float(highest_bucket) + 5.0, 100.0, FAAB_BID_CURVE)
    assert high_bid >= low_bid
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_waiver_value.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.engine.waiver_value'`

- [ ] **Step 3: Write the implementation**

```python
# src/ffdo/engine/waiver_value.py
"""Free-agent pool, positional roster caps, and the FAAB bid suggestion --
the swappable seam for sub-project #6, the way engine/pick_value.py and
engine/trade_value.py were for #5.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Final

from ffdo.engine.replacement import FLEX_ELIGIBILITY

POSITION_CAP_EXTRA: Final[dict[str, int]] = {"QB": 1, "TE": 1, "DEF": 0, "K": 0}


def suggested_bid(
    vor_gain: float,
    remaining_budget: float,
    curve: Mapping[int, float],
    *,
    bucket_width: int = 10,
) -> float:
    if not curve:
        return 0.0
    bucket = (int(vor_gain) // bucket_width) * bucket_width
    candidates = [b for b in curve if b <= bucket]
    if not candidates:
        return 0.0
    pct = curve[max(candidates)]
    return round(remaining_budget * pct, 0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_waiver_value.py -v`
Expected: PASS, all 7 tests (or 6 + 1 skip if the real curve has fewer
than 2 populated buckets — acceptable, not a failure)

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/engine/waiver_value.py tests/engine/test_waiver_value.py
git commit -m "feat: engine.waiver_value.suggested_bid -- real FAAB bid suggestion"
```

---

### Task 6: `engine/waiver_value.py::{free_agents, position_cap}`

**Files:**
- Modify: `src/ffdo/engine/waiver_value.py` (append)
- Modify: `tests/engine/test_waiver_value.py` (append)

**Interfaces:**
- Consumes: `ffdo.domain.models.RosterEntry` (existing), `ffdo.engine.replacement.FLEX_ELIGIBILITY` (existing, imported in Task 5).
- Produces: `free_agents(all_player_ids: Iterable[str], rosters: Sequence[RosterEntry]) -> set[str]`, `position_cap(position: str, league) -> int | None` — both consumed by Task 7's `recommend_adds`.

- [ ] **Step 1: Write the failing tests**

```python
# appended to tests/engine/test_waiver_value.py
from ffdo.domain.models import RosterEntry


def _roster(roster_id, player_ids):
    return RosterEntry(roster_id=roster_id, team_name=f"Team {roster_id}",
                       player_ids=tuple(player_ids), starter_ids=(),
                       wins=0, losses=0, ties=0, points_for=0.0, points_against=0.0)


def _league(roster_positions):
    class _L:
        pass
    lg = _L()
    lg.roster_positions = roster_positions
    return lg


def test_free_agents_excludes_every_rostered_player():
    rosters = [_roster(1, ["p1", "p2"]), _roster(2, ["p3"])]
    result = waiver_value.free_agents(["p1", "p2", "p3", "p4", "p5"], rosters)
    assert result == {"p4", "p5"}


def test_free_agents_with_no_rosters_returns_everyone():
    result = waiver_value.free_agents(["p1", "p2"], [])
    assert result == {"p1", "p2"}


def test_position_cap_uncapped_position_returns_none():
    league = _league(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"))
    assert waiver_value.position_cap("RB", league) is None
    assert waiver_value.position_cap("WR", league) is None


def test_position_cap_te_with_one_te_eligible_flex_matches_the_users_own_example():
    """The user's own worked example from brainstorming: a 1-TE + 1-FLEX
    (TE-eligible) league should cap TE at exactly 3."""
    league = _league(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"))
    assert waiver_value.position_cap("TE", league) == 3


def test_position_cap_qb_standard_non_superflex_matches_the_users_own_example():
    league = _league(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"))
    assert waiver_value.position_cap("QB", league) == 2


def test_position_cap_def_and_k_have_zero_extra():
    league = _league(("QB", "RB", "RB", "WR", "WR", "TE", "DEF", "K", "BN"))
    assert waiver_value.position_cap("DEF", league) == 1
    assert waiver_value.position_cap("K", league) == 1


def test_position_cap_superflex_widens_the_qb_cap():
    league = _league(("QB", "SUPER_FLEX", "RB", "WR", "TE", "BN", "BN"))
    # 1 dedicated QB slot + 1 SUPER_FLEX (QB-eligible) + 1 extra = 3
    assert waiver_value.position_cap("QB", league) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_waiver_value.py -v -k "free_agents or position_cap"`
Expected: FAIL with `AttributeError: module 'ffdo.engine.waiver_value' has no attribute 'free_agents'`

- [ ] **Step 3: Write the implementation**

Append to `src/ffdo/engine/waiver_value.py`:

```python
def free_agents(all_player_ids: Iterable[str], rosters: Sequence) -> set[str]:
    rostered = {pid for r in rosters for pid in r.player_ids}
    return set(all_player_ids) - rostered


def position_cap(position: str, league) -> int | None:
    if position not in POSITION_CAP_EXTRA:
        return None
    dedicated = sum(1 for s in league.roster_positions if s == position)
    flex_eligible = sum(
        1 for s in league.roster_positions
        if s in FLEX_ELIGIBILITY and position in FLEX_ELIGIBILITY[s])
    return dedicated + flex_eligible + POSITION_CAP_EXTRA[position]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_waiver_value.py -v -k "free_agents or position_cap"`
Expected: PASS, all 7 tests

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/engine/waiver_value.py tests/engine/test_waiver_value.py
git commit -m "feat: engine.waiver_value.{free_agents,position_cap} -- free-agent pool + roster caps"
```

---

### Task 7: `engine/waiver_value.py::recommend_adds`

**Files:**
- Modify: `src/ffdo/engine/waiver_value.py` (append)
- Modify: `src/ffdo/domain/models.py` (add `WaiverRecommendation`)
- Modify: `tests/engine/test_waiver_value.py` (append)

**Interfaces:**
- Consumes: `suggested_bid` (Task 5), `free_agents`/`position_cap` (Task 6), `ValuedPlayer`/`RosterEntry`/`PlayerProfile`/`INJURY_OUT_STATUSES` (existing).
- Produces: `domain.models.WaiverRecommendation` (new), `recommend_adds(free_agent_ids, your_roster_ids, valued: Mapping[str, ValuedPlayer], profiles: Mapping[str, PlayerProfile], league, faab_curve: Mapping[int, float], remaining_budget: float, *, top_n: int = 10, min_vor_gain: float = 5.0) -> list[WaiverRecommendation]` — consumed by Task 8's API wiring.

- [ ] **Step 1: Write the failing tests**

```python
# appended to tests/engine/test_waiver_value.py
from ffdo.domain.models import PlayerProfile, ValuedPlayer


def _profile(pid, pos, injury=None, active=True):
    return PlayerProfile(player_id=pid, first_name="A", last_name="B",
                         position=pos, team="X", age=25, years_exp=3,
                         injury_status=injury, active=active)


def _valued(pid, pos, vor_val, injury=None, active=True):
    return ValuedPlayer(profile=_profile(pid, pos, injury, active),
                        projected_points=vor_val + 50.0, adjusted_points=vor_val + 50.0,
                        vor=vor_val, tier=1, adjustments={})


def test_recommends_a_clear_upgrade_over_worst_bench_player_with_open_position():
    profiles = {"fa1": _profile("fa1", "WR"), "bench1": _profile("bench1", "RB")}
    valued = {"fa1": _valued("fa1", "WR", 40.0), "bench1": _valued("bench1", "RB", 5.0)}
    league = _league(("QB", "RB", "RB", "WR", "WR", "TE", "BN", "BN"))
    result = waiver_value.recommend_adds(
        ["fa1"], ["bench1"], valued, profiles, league,
        faab_curve={0: 0.1, 30: 0.5}, remaining_budget=100.0)
    assert len(result) == 1
    assert result[0].free_agent_id == "fa1"
    assert result[0].drop_player_id == "bench1"
    assert result[0].vor_gain == pytest.approx(35.0)
    assert result[0].suggested_bid == pytest.approx(50.0)  # bucket 30 -> 0.5 * 100


def test_skips_a_free_agent_that_does_not_clear_min_vor_gain():
    profiles = {"fa1": _profile("fa1", "WR"), "bench1": _profile("bench1", "RB")}
    valued = {"fa1": _valued("fa1", "WR", 8.0), "bench1": _valued("bench1", "RB", 5.0)}
    league = _league(("QB", "RB", "WR", "BN"))
    result = waiver_value.recommend_adds(
        ["fa1"], ["bench1"], valued, profiles, league,
        faab_curve={0: 0.1}, remaining_budget=100.0, min_vor_gain=5.0)
    assert result == []  # only 3.0 VOR gain, below the 5.0 minimum


def test_excludes_an_injured_out_free_agent_entirely():
    profiles = {"fa1": _profile("fa1", "WR", injury="IR"), "bench1": _profile("bench1", "RB")}
    valued = {"fa1": _valued("fa1", "WR", 100.0, injury="IR"),
              "bench1": _valued("bench1", "RB", 1.0)}
    league = _league(("QB", "RB", "WR", "BN"))
    result = waiver_value.recommend_adds(
        ["fa1"], ["bench1"], valued, profiles, league,
        faab_curve={0: 0.1}, remaining_budget=100.0)
    assert result == []


def test_at_position_cap_only_compares_against_the_same_position():
    """The core scenario from brainstorming: a standard league already
    rostering 2 QBs (at the cap of dedicated=1 + extra=1 = 2) must not
    have a high-VOR free-agent QB compared against the worst BENCH player
    overall (a low-VOR kicker) -- only against the user's worst QB."""
    profiles = {
        "fa_qb": _profile("fa_qb", "QB"),
        "my_qb1": _profile("my_qb1", "QB"),
        "my_qb2": _profile("my_qb2", "QB"),
        "my_k": _profile("my_k", "K"),
    }
    valued = {
        "fa_qb": _valued("fa_qb", "QB", 20.0),
        "my_qb1": _valued("my_qb1", "QB", 15.0),
        "my_qb2": _valued("my_qb2", "QB", 3.0),   # worst QB
        "my_k": _valued("my_k", "K", -50.0),      # worst bench player overall
    }
    league = _league(("QB", "RB", "WR", "K", "BN", "BN"))
    result = waiver_value.recommend_adds(
        ["fa_qb"], ["my_qb1", "my_qb2", "my_k"], valued, profiles, league,
        faab_curve={0: 0.1, 10: 0.3}, remaining_budget=100.0)
    assert len(result) == 1
    assert result[0].drop_player_id == "my_qb2"   # worst QB, not my_k
    assert result[0].vor_gain == pytest.approx(17.0)  # 20.0 - 3.0, not 20.0 - (-50.0)


def test_no_existing_players_at_all_means_a_pure_add_with_no_drop():
    """Genuine edge case: an empty roster (e.g. right after first sync,
    before any players are rostered) has no drop candidate at all -- not
    a normal scenario (real rosters are kept full), but must not crash
    and must not invent a drop candidate that doesn't exist."""
    profiles = {"fa_qb": _profile("fa_qb", "QB")}
    valued = {"fa_qb": _valued("fa_qb", "QB", 20.0)}
    league = _league(("QB", "RB", "WR", "BN", "BN"))
    result = waiver_value.recommend_adds(
        ["fa_qb"], [], valued, profiles, league,
        faab_curve={0: 0.1, 10: 0.3}, remaining_budget=100.0)
    assert len(result) == 1
    assert result[0].drop_player_id is None
    assert result[0].vor_gain == pytest.approx(20.0)


def test_sorted_by_vor_gain_descending_and_capped_at_top_n():
    profiles = {f"fa{i}": _profile(f"fa{i}", "WR") for i in range(3)}
    profiles["bench1"] = _profile("bench1", "RB")
    valued = {f"fa{i}": _valued(f"fa{i}", "WR", float(10 * (i + 1))) for i in range(3)}
    valued["bench1"] = _valued("bench1", "RB", 0.0)
    league = _league(("QB", "RB", "WR", "BN"))
    result = waiver_value.recommend_adds(
        [f"fa{i}" for i in range(3)], ["bench1"], valued, profiles, league,
        faab_curve={0: 0.1}, remaining_budget=100.0, top_n=2)
    assert len(result) == 2
    assert result[0].free_agent_id == "fa2"  # highest VOR (30.0) first
    assert result[1].free_agent_id == "fa1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_waiver_value.py -v -k recommend_adds`
Expected: FAIL with `AttributeError: module 'ffdo.engine.waiver_value' has no attribute 'recommend_adds'`

- [ ] **Step 3: Add `WaiverRecommendation` to `domain/models.py`**

Append to `src/ffdo/domain/models.py`:

```python
@dataclass(frozen=True, slots=True)
class WaiverRecommendation:
    free_agent_id: str
    drop_player_id: str | None
    vor_gain: float
    suggested_bid: float
```

- [ ] **Step 4: Write the implementation**

Append to `src/ffdo/engine/waiver_value.py` (add
`from ffdo.domain.constants import INJURY_OUT_STATUSES` and
`from ffdo.domain.models import PlayerProfile, ValuedPlayer,
WaiverRecommendation` to the file's imports):

```python
def recommend_adds(
    free_agent_ids: Iterable[str],
    your_roster_ids: Iterable[str],
    valued: Mapping[str, "ValuedPlayer"],
    profiles: Mapping[str, "PlayerProfile"],
    league,
    faab_curve: Mapping[int, float],
    remaining_budget: float,
    *,
    top_n: int = 10,
    min_vor_gain: float = 5.0,
) -> list["WaiverRecommendation"]:
    your_ids = list(your_roster_ids)
    your_by_position: dict[str, list[str]] = {}
    for pid in your_ids:
        prof = profiles.get(pid)
        if prof is not None:
            your_by_position.setdefault(prof.position, []).append(pid)

    def _worst(pids: Sequence[str]) -> str | None:
        candidates = [pid for pid in pids if pid in valued]
        if not candidates:
            return None
        return min(candidates, key=lambda pid: valued[pid].vor)

    out: list[WaiverRecommendation] = []
    for fa_id in free_agent_ids:
        prof = profiles.get(fa_id)
        vp = valued.get(fa_id)
        if prof is None or vp is None:
            continue
        if not prof.active or prof.injury_status in INJURY_OUT_STATUSES:
            continue

        cap = position_cap(prof.position, league)
        at_cap = cap is not None and len(your_by_position.get(prof.position, [])) >= cap

        if at_cap:
            drop_id = _worst(your_by_position.get(prof.position, []))
        else:
            drop_id = _worst(your_ids)
        baseline = valued[drop_id].vor if drop_id is not None else 0.0

        vor_gain = vp.vor - baseline
        if vor_gain < min_vor_gain:
            continue

        out.append(WaiverRecommendation(
            free_agent_id=fa_id, drop_player_id=drop_id, vor_gain=vor_gain,
            suggested_bid=suggested_bid(vor_gain, remaining_budget, faab_curve)))

    out.sort(key=lambda r: r.vor_gain, reverse=True)
    return out[:top_n]
```

`drop_id` is only ever `None` when `_worst` finds no candidate at all
(an empty `your_ids`, or an empty position group at cap) — real fantasy
rosters are kept full in practice, so this is a genuine edge case (e.g.
right after first roster sync), not a routine "open slot" the common path
needs to special-case.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_waiver_value.py -v -k recommend_adds`
Expected: PASS, all 6 tests

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/engine/waiver_value.py src/ffdo/domain/models.py tests/engine/test_waiver_value.py
git commit -m "feat: engine.waiver_value.recommend_adds -- cap-aware add/drop/bid recommendation"
```

---

### Task 8: `api/app.py` wiring — `GET /waivers`

**Files:**
- Modify: `src/ffdo/api/app.py`
- Test: `tests/api/test_waiver_endpoint.py`

**Interfaces:**
- Consumes: `waiver_value.recommend_adds` (Task 7), `waivers.{fetch_waivers, remaining_budget}` (Task 1), `FAAB_BID_CURVE` (Task 4), plus existing `ros_value_mod.roster_value`, `rosters_mod.fetch`, `_load_league`, `_load_players`, `client_mod.SleeperClient`, `_through_week`, `nfl_state_cache`, `_season_proj_anchor_for`, `actuals_mod.points_so_far`, `ingest.players` (all existing, read `get_lineup`/`get_trades` for exact usage before wiring).

**Design note, critical (Global Constraints):** `remaining_budget`'s
`fetch_waivers(..., through_week=...)` call MUST use `nfl.week` (the live
current week), never the stats-final `through_week` variable computed by
`_through_week(nfl)` — a waiver claim can happen during the current,
still-in-progress week, exactly the same class of bug #5's `GET /trades`
shipped with and had to fix after a manual smoke test caught it. Do not
reuse the `through_week` variable for this call.

- [ ] **Step 1: Read the current file structure first**

Read `_load_league`, `get_lineup`, `get_trades`, and the constants/ingest
import blocks in `src/ffdo/api/app.py` before editing — this file has
grown across five prior sub-projects, so confirm helper names and exact
call shapes against the real current file rather than assuming they
haven't shifted since #5.

- [ ] **Step 2: Write the failing tests**

```python
# tests/api/test_waiver_endpoint.py
from fastapi.testclient import TestClient

from ffdo.api.app import V1, app_mod, create_app
from ffdo.api.store import LeagueStore

from tests.api.test_season_endpoint import (
    _MATCHUPS, _PLAYERS, _PROJ, _ROSTERS, _STATE, _USERS, _tracked)


def test_waivers_endpoint_returns_recommendations_for_a_faab_league(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
        f"{V1}/league/L1": {"settings": {"waiver_type": 2, "waiver_budget": 100}},
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
    res = client.get("/api/leagues/sleeper:L1:2026/waivers")
    assert res.status_code == 200
    data = res.json()
    assert "remaining_budget" in data
    assert "recommendations" in data


def test_waivers_endpoint_400s_for_a_non_faab_league(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
        f"{V1}/league/L1": {"settings": {"waiver_type": 1}},  # 1 = rolling priority
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
    res = client.get("/api/leagues/sleeper:L1:2026/waivers")
    assert res.status_code == 400


def test_waivers_endpoint_counts_a_claim_in_the_current_in_progress_week(monkeypatch, tmp_path):
    """Regression, proactive: sub-project #5's GET /trades shipped with a
    bug where the stats-final `through_week` (nfl.week - 1) was reused as
    the transaction-scan bound, silently missing anything that happened
    during the current, still-in-progress week. This must use nfl.week
    directly. _STATE's week is 10 and _tracked's default roster_id is 1
    (both confirmed directly against tests/api/test_season_endpoint.py's
    real fixtures, not assumed)."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))  # roster_id=1 by default
    monkeypatch.setattr(app_mod, "_STORE", store)

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
        f"{V1}/league/L1": {"settings": {"waiver_type": 2, "waiver_budget": 100}},
    }
    current_week_claim = [{
        "type": "waiver", "status": "complete", "transaction_id": "w-current",
        "roster_ids": [1], "adds": {"some_pid": 1},
        "settings": {"waiver_bid": 15}, "created": 1000,
    }]

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            if "/transactions/10" in url:  # _STATE's week == 10
                return current_week_claim
            if "/transactions/" in url:
                return []
            for key, val in resp.items():
                if key in url:
                    return val
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeClient)
    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/waivers")
    assert res.status_code == 200
    # roster 1's remaining budget must reflect the current-week $15 spend
    # (100 - 15 = 85) -- if the endpoint used the stats-final bound
    # instead of nfl.week, /transactions/10 would never be requested and
    # remaining_budget would incorrectly still show the full 100.
    assert res.json()["remaining_budget"] == pytest.approx(85.0)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_waiver_endpoint.py -v`
Expected: FAIL (404s — the route doesn't exist yet)

- [ ] **Step 4: Wire it in**

In `src/ffdo/api/app.py`:

1. Add `FAAB_BID_CURVE` to the existing constants import line.
2. Alongside the existing `from ffdo.ingest.sleeper import transactions as
   transactions_mod` line, add:
   ```python
   from ffdo.ingest.sleeper import waivers as waivers_mod
   from ffdo.engine import waiver_value as waiver_value_mod
   ```
3. Add the endpoint (following `get_trades`'s fully self-contained fetch
   pattern):

   ```python
   @app.get("/api/leagues/{league_key}/waivers")
   def get_waivers(league_key: str) -> dict:
       """Free-agent add/drop + FAAB bid recommendations. FAAB leagues
       only (Sleeper waiver_type == 2) -- a 400 for any other waiver type
       or provider, matching /lineup's ESPN gate."""
       lg = _load_league(league_key)
       if lg.provider != "sleeper":
           raise HTTPException(status_code=400, detail="Waivers is Sleeper-only for now")

       sleeper = client_mod.SleeperClient()
       try:
           league_raw = sleeper.get_json(f"{client_mod.V1}/league/{lg.provider_league_id}")
           settings = league_raw.get("settings") or {}
           if settings.get("waiver_type") != 2:
               raise HTTPException(
                   status_code=400, detail="Waivers is FAAB-leagues-only for now")
           waiver_budget = float(settings.get("waiver_budget") or 0)

           nfl = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))
           profiles, _espn_id_index = players_cache.get(lambda: _load_players(sleeper))
           proj_anchor = _season_proj_anchor_for(lg.season).get(
               lambda: _load_projection_anchor(sleeper, lg.season))
           rosters = rosters_mod.fetch(sleeper, lg.provider_league_id)
           through_week = _through_week(nfl)
           actuals = actuals_mod.points_so_far(sleeper, lg.provider_league_id, through_week)

           # nfl.week, NOT through_week -- a waiver claim can happen during
           # the current, still-in-progress week (see this task's design
           # note: reusing the stats-final bound here reproduces #5's
           # GET /trades bug).
           claims = waivers_mod.fetch_waivers(
               sleeper, lg.provider_league_id, season=lg.season, through_week=nfl.week)
           budgets = waivers_mod.remaining_budget(claims, waiver_budget=waiver_budget)

           all_player_ids = set(profiles)
       except HTTPException:
           raise
       except (httpx.HTTPError, RuntimeError) as exc:
           raise HTTPException(status_code=502, detail="Couldn't reach Sleeper, try again") from exc
       finally:
           sleeper.close()

       you = next((r for r in rosters if r.roster_id == lg.roster_id), None)
       if you is None:
           return {"remaining_budget": waiver_budget, "recommendations": []}

       free_agent_ids = waiver_value_mod.free_agents(all_player_ids, rosters)
       your_remaining = budgets.get(lg.roster_id, waiver_budget)

       valued = ros_value_mod.roster_value(
           set(you.player_ids) | free_agent_ids, lg,
           resolved_format=lg.resolved_format, season_proj=proj_anchor,
           profiles=profiles, actuals=actuals, weeks_played=through_week,
           season_weeks=_season_weeks(lg.season))

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

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_waiver_endpoint.py -v`
Expected: PASS, all 3 tests

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/api/app.py tests/api/test_waiver_endpoint.py
git commit -m "feat: wire waiver recommender into the API"
```

---

### Task 9: Frontend — Waivers tab

**Files:**
- Modify: `src/ffdo/web/season/season.js`

**Interfaces:**
- Consumes: `GET /api/leagues/{key}/waivers` (Task 8).

No automated test for this task (no JS test runner in this codebase,
matching #5's Trades tab precedent). Verified manually in Task 10.

- [ ] **Step 1: Read the current file**

Read `src/ffdo/web/season/season.js` in full — confirm `_panel`,
`renderRightPanel()`'s tab-bar construction, and the
`loadTrades`/`renderTrades`/`_tradesData` lazy-load pattern (#5's most
recent addition) are still exactly as described here before editing.

- [ ] **Step 2: Add the tab and lazy-load state**

Add `_waiversData = null;` alongside `_tradesData = null;`, reset it in
`mountSeason()` the same way. Add a `data-panel-tab="waivers"` button to
`renderRightPanel()`'s tab bar — shown **conditionally**, only when the
already-fetched `_data` indicates a FAAB league. Since `GET /season`
doesn't currently return waiver-type info, show the tab unconditionally
(matching the Trades tab's own unconditional-button precedent) and let a
400 response from `loadWaivers()` render as this tab's own error state,
rather than adding new season-payload plumbing to conditionally hide it.

Add a branch in the panel dispatch identical in shape to `_panel === "trades"`.

- [ ] **Step 3: Write `loadWaivers()`**

Mirrors `loadTrades()`'s stale-response guard exactly (same `myKey`
capture-before-first-await pattern):

```javascript
async function loadWaivers() {
  const myKey = _key;
  let result;
  try {
    const res = await fetch(`/api/leagues/${encodeURIComponent(myKey)}/waivers`);
    if (_key !== myKey) return;
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      if (_key !== myKey) return;
      result = { error: body.detail || "Couldn't load waivers" };
    } else {
      const data = await res.json();
      if (_key !== myKey) return;
      result = data;
    }
  } catch (e) {
    if (_key !== myKey) return;
    result = { error: "Couldn't load waivers" };
  }
  _waiversData = result;
  render();
}
```

- [ ] **Step 4: Write `renderWaivers()`**

```javascript
function renderWaivers() {
  if (_waiversData.error) {
    return `<div class="lineup-error">${escapeHtml(_waiversData.error)}</div>`;
  }
  const header = `<div class="lineup-header">Remaining budget: $${_waiversData.remaining_budget}</div>`;
  if (_waiversData.recommendations.length === 0) {
    return header + `<div class="lineup-empty">No recommended adds right now</div>`;
  }
  const rows = _waiversData.recommendations.map(r => {
    const drop = r.drop_player_id
      ? `drop ${escapeHtml(r.drop_player_id)}`
      : "open bench slot, no drop needed";
    return `<div class="lineup-row">
      <div>Add ${escapeHtml(r.free_agent_id)} (${drop}) &mdash; +${r.vor_gain} VOR</div>
      <div>Suggested bid: $${r.suggested_bid}</div>
    </div>`;
  }).join("");
  return header + `<div class="lineup-list">${rows}</div>`;
}
```

Reuses `.lineup-row`/`.lineup-list`/`.lineup-error`/`.lineup-empty`/
`.lineup-header` — confirm these classes exist in `season.css` and fit
this shape before assuming so (the same check #5's Task 9 made).

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/web/season/season.js
git commit -m "feat: season screen Waivers tab -- FAAB add/drop/bid recommendations"
```

---

### Task 10: README + browser smoke test

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update `README.md`**

Find the existing paragraph describing the Trades tab (added in #5) and
add immediately after it:

```markdown
The **Waivers** tab (FAAB leagues only) recommends free-agent adds, which
bench player to drop for each, and how much to bid -- respecting roster
construction (it won't suggest stockpiling QBs or TEs beyond what you can
usefully start), with bid amounts fit from your own leagues' real
historical waiver-claim outcomes.
```

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, full count from all 9 prior tasks unchanged by this task

- [ ] **Step 3: Browser smoke (manual — the executor or controller)**

Start the server against a real FAAB league (GDK,
`sleeper:1312210128811872256:2026`, `waiver_type: 2` confirmed live during
brainstorming, with real, deep waiver history).

- Open the season screen, switch to the Waivers tab. Confirm real
  recommendations render (no 500), with a sane remaining-budget number and
  plausible suggested bids.
- Confirm a rostered-at-cap position (if GDK's own roster happens to be at
  cap for QB/TE/DEF/K) produces a like-for-like swap recommendation rather
  than a pure add, if a real scenario naturally exercises this — if not,
  construct one via `POST`-equivalent reasoning or a direct
  `engine.waiver_value.recommend_adds` call against real data to confirm
  the cap logic fires correctly for at least one real case.
- Confirm a non-FAAB or ESPN league's Waivers tab shows a clear error
  state, not a crash.
- No new console errors beyond the pre-existing, unrelated `board.js`
  live-refresh warning already noted in #4/#5's smoke tests.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "chore: document the Waivers tab in the README"
```
