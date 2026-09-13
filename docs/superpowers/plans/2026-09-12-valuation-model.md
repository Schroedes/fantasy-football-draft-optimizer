# Post-Draft Player Valuation Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `engine/dynasty_curve.py`'s hardcoded per-position dynasty multiplier with a real, data-driven multi-year annuity valuation, and re-validate (with more rigor than the original one-day pass) whether a durability/injury-risk adjustment should finally be wired live into redraft valuation.

**Architecture:** Repurposes a real but completely dormant statistical system (`engine/adjustments.py`'s `fit_age_curve`/`expected_games_missed`, `backtest/harness.py`) built for a different, already-rejected purpose (a redraft points-adjustment) as the empirical backbone for a NEW dynasty multi-year value computation (`engine/dynasty_value.py`). Two new offline scripts refresh the historical snapshot this all depends on and fit/print the age curve for a human to check into `domain/constants.py`. A new live ingest module fetches real multi-season player history for dynasty leagues only. `engine/ros_value.py`'s previously-frozen signature grows (a deliberate, plan-approved exception) to thread the new inputs through; the redraft/keeper branch is otherwise untouched unless the re-run backtest promotes durability, in which case one additional conditional code path gets written.

**Tech Stack:** Python 3.12, FastAPI, `httpx` (`MockTransport` in tests), `numpy` (existing dependency, used by `backtest/harness.py`), pytest. Two new one-shot CLI scripts, no new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-09-12-valuation-model-design.md`

## Global Constraints

- **Python** `>=3.12`. **No new runtime dependencies.**
- **Stacked PR:** this branch (`claude/valuation-model`) is stacked on `claude/weekly-lineup` (PR #27). Base every task on that branch's HEAD.
- **A dormant system is being repurposed, not built from scratch.** `engine/adjustments.py` (`fit_age_curve`, `expected_games_missed`, `build`, `AGE_WEIGHT`, `DURABILITY_WEIGHT`) and `backtest/harness.py` (`evaluate_season`) already exist, are already tested, and must not be modified except for Task 7's pure addition (`sweep_durability_weights`) and (conditionally) `DURABILITY_WEIGHT`'s value in Task 8. Read them before writing anything that touches them.
- **`AGE_WEIGHT` stays `0.0` permanently.** This plan does not revisit the redraft age-adjustment question (already answered: negative result). Only `DURABILITY_WEIGHT` is a live candidate for promotion, and only via Task 8's mechanical decision procedure — never a subjective call.
- **The corrected annuity formula, verbatim (do not use an "current_full + average(years 1..N)" variant — that one silently doubles the value whenever `age_curve` has no data for a player's future ages, which is a real, common case, not an edge case):** fold year 0 (`current_full`, weight `1.0`) into the SAME weighted average as every other year. See Task 4 for the exact code.
- **`engine.roster.team_lineup`, `engine.replacement.greedy_fill_slots`/`replacement_levels`, `engine.vor.compute` are not modified** except that Task 8 (conditionally) passes a new `adjustments` argument to an existing `vor.compute` call site — the function itself already accepts one and needs no changes.
- **`roster_value`'s signature grows, not breaks.** `history`/`age_curve` are new keyword-only params defaulting to `None`; every existing call site (the `/season` endpoint before this plan touches it) continues to work unmodified until Task 9 explicitly updates it.
- **Error contract for the new live ingest:** a `player_history` fetch failure follows the same `except (httpx.HTTPError, RuntimeError)` → 502 pattern every other provider call in `get_season` already uses. This is core, load-bearing data for a dynasty league — no graceful-degradation case (unlike sub-project #3's unofficial schedule endpoint).
- **Two offline scripts, no automated tests for either** (matching this repo's existing convention for `scripts/seed_dev_league.py` — a one-shot manual tool, not unit-tested). Both get concrete, mechanical manual-verification steps instead.
- **Commit after every task.** Prefix: `feat:` / `refactor:` / `test:` / `chore:`.
- **`uv run pytest` green at the end of every task.** The one acceptable warning is the pre-existing `StarletteDeprecationWarning` about `starlette.testclient`.

---

## File Structure

**Created:**
- `scripts/refresh_snapshot.py` — re-captures `players_nfl`/`stats_{season}`/`projections_{season}_CONTAMINATED` from live Sleeper into a new dated `data/snapshots/<date>/` directory.
- `scripts/fit_age_curve.py` — loads a snapshot directory, fits the age curve via the existing `adjustments.fit_age_curve`, prints a `DYNASTY_AGE_CURVE` Python literal to stdout for manual review.
- `src/ffdo/ingest/sleeper/player_history.py` — `fetch_season(sleeper, season) -> dict[str, SeasonStatLine]`, `history_for(player_ids, season_stats) -> dict[str, list[SeasonStatLine]]`.
- `src/ffdo/engine/dynasty_value.py` — `annuity_value(current_full, profile, history, age_curve, *, current_season, horizon_years=5, discount_rate=0.15) -> float`. Replaces `dynasty_curve.py`.
- Tests: `tests/ingest/sleeper/test_player_history.py`, `tests/engine/test_dynasty_value.py`, `tests/domain/test_constants.py` (may already exist from an earlier sub-project — extend, don't overwrite, if so).

**Modified:**
- `src/ffdo/engine/ros_value.py` — `roster_value` gains `history`/`age_curve` kwargs (default `None`); dynasty branch calls `dynasty_value.annuity_value(...)` instead of `dynasty_curve.multiplier(...)`.
- `src/ffdo/domain/constants.py` — add `DYNASTY_AGE_CURVE: Final[dict[str, dict[int, float]]]` (real data, from running `scripts/fit_age_curve.py`); possibly update `DURABILITY_WEIGHT` in `engine/adjustments.py` (not `constants.py` — that constant lives in `adjustments.py` itself) pending Task 8's outcome.
- `src/ffdo/backtest/harness.py` — add `sweep_durability_weights(season, weights) -> list[dict]`.
- `src/ffdo/engine/adjustments.py` — `DURABILITY_WEIGHT` value only, only if Task 8 promotes it. No other changes.
- `src/ffdo/api/app.py` — new `player_history_caches`/`_player_history_cache_for`; both `get_season` (Sleeper) and `_season_espn` fetch `season_stats`/`history` for dynasty leagues; `_assemble_season` gains a `history` parameter and threads it (+ `DYNASTY_AGE_CURVE`) into `roster_value`.
- `README.md` — one line noting dynasty values are now a real multi-year model.

**Deleted:**
- `src/ffdo/engine/dynasty_curve.py`
- `tests/engine/test_dynasty_curve.py`

---

## Task 1: `scripts/refresh_snapshot.py`

**Files:**
- Create: `scripts/refresh_snapshot.py`

> No automated test (one-shot manual tool, matches `scripts/seed_dev_league.py`'s existing convention). Manual verification in Step 3.

**Interfaces:**
- Consumes: `ffdo.ingest.client.{V1, PROJECTIONS, SleeperClient}` (existing).
- Produces: a new `data/snapshots/<today's-date>/` directory containing `players_nfl.json.gz`, `stats_{season}.json.gz` per requested season, `projections_{season}_CONTAMINATED.json.gz` per requested season — same filenames/shape as the existing `data/snapshots/2026-08-22-draft-day/` capture, so `ffdo.ingest.snapshot.load(name, snapshot_dir=...)` (already supports an override) reads either directory unmodified.

- [ ] **Step 1: Write the script**

Create `scripts/refresh_snapshot.py`:

```python
#!/usr/bin/env python
"""Re-captures players_nfl/stats_{season}/projections_{season}_CONTAMINATED
from live Sleeper into a new dated snapshot directory, so backtest/harness.py
and scripts/fit_age_curve.py aren't stuck depending on a single frozen
2026-08-22 capture forever. Does NOT touch the existing
data/snapshots/2026-08-22-draft-day/ directory -- writes a new, separate
dated directory instead, so that capture stays available as a stable
historical reference.

Usage: uv run python scripts/refresh_snapshot.py [--seasons 2021-2026]
"""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import date
from pathlib import Path
from typing import Any

from ffdo.ingest.client import PROJECTIONS, V1, SleeperClient

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(data, fh)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seasons", default="2021-2026",
                        help="inclusive season range, e.g. 2021-2026")
    args = parser.parse_args()
    start_str, end_str = args.seasons.split("-")
    seasons = range(int(start_str), int(end_str) + 1)

    out_dir = _REPO_ROOT / "data" / "snapshots" / date.today().isoformat()
    sleeper = SleeperClient()
    try:
        print("Fetching players_nfl...")
        _write(out_dir / "players_nfl.json.gz", sleeper.get_json(f"{V1}/players/nfl"))

        for season in seasons:
            print(f"Fetching stats_{season}...")
            _write(out_dir / f"stats_{season}.json.gz",
                  sleeper.get_json(f"{V1}/stats/nfl/regular/{season}"))

        for season in seasons:
            # This script always fetches whatever Sleeper currently serves --
            # the _CONTAMINATED suffix documents that these files are meant
            # for allow_contaminated=True offline analysis use only, matching
            # the existing 2026-08-22-draft-day capture's own naming and
            # ingest/projections.py's contamination guard.
            print(f"Fetching projections_{season}...")
            _write(out_dir / f"projections_{season}_CONTAMINATED.json.gz",
                  sleeper.get_json(
                      f"{PROJECTIONS}/{season}"
                      "?season_type=regular&position[]=QB&position[]=RB"
                      "&position[]=WR&position[]=TE&position[]=DEF&position[]=K"))
    finally:
        sleeper.close()

    print(f"Snapshot written to {out_dir}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it for real**

Run: `uv run python scripts/refresh_snapshot.py --seasons 2021-2026`

This makes real network calls to live Sleeper endpoints (one for players, one per season for stats, one per season for projections — about 18 calls total for the default range) and will take a minute or two. Expected: prints progress lines, ends with `Snapshot written to .../data/snapshots/<today>/`.

- [ ] **Step 3: Verify the output (manual)**

Run: `ls data/snapshots/<today's date>/` — expect `players_nfl.json.gz`, `stats_2021.json.gz` through `stats_2026.json.gz`, `projections_2021_CONTAMINATED.json.gz` through `projections_2026_CONTAMINATED.json.gz` (7 stats files, 6 projections files — 2021 has no prior-year projections to contaminate in the same sense but the script fetches it anyway for uniformity, that's fine).

Run: `uv run python -c "from ffdo.ingest import snapshot; d = snapshot.load('stats_2025', __import__('pathlib').Path('data/snapshots/<today's date>')); print(len(d), 'players')"` (substitute the real date) — expect a number in the thousands (matching the existing capture's ~8,229 for `stats_2023`), not zero, not an error.

Run: `uv run pytest -q` — expect PASS (this task adds no test-suite-visible code, only a script; confirms nothing else broke).

- [ ] **Step 4: Commit**

```bash
git add scripts/refresh_snapshot.py
git commit -m "feat: scripts/refresh_snapshot.py -- re-capture historical Sleeper data on demand"
```

(Do NOT `git add` the `data/snapshots/<date>/` directory itself — `data/snapshots/` is git-ignored, same as the existing `2026-08-22-draft-day` capture's treatment. Confirm with `git status` that the new snapshot directory does not appear as untracked-to-be-added; if it does, check `.gitignore` covers `data/snapshots/` before committing.)

---

## Task 2: `scripts/fit_age_curve.py`

**Files:**
- Create: `scripts/fit_age_curve.py`

> No automated test (same reasoning as Task 1). Manual verification in Step 3, using the snapshot Task 1 just produced.

**Interfaces:**
- Consumes: `ffdo.engine.adjustments.fit_age_curve` (existing, unmodified), `ffdo.ingest.{players, stats, snapshot}` (existing, unmodified), `ffdo.domain.constants.STANDARD_HALF_PPR` (existing).
- Produces: printed stdout output shaped like a `DYNASTY_AGE_CURVE: Final[dict[str, dict[int, float]]] = {...}` Python literal — this printed text is what Task 3 pastes into `domain/constants.py`.

- [ ] **Step 1: Write the script**

Create `scripts/fit_age_curve.py`:

```python
#!/usr/bin/env python
"""Fits DYNASTY_AGE_CURVE from a historical snapshot directory -- prints the
result as a Python literal for a human to review and paste into
domain/constants.py by hand. Deliberately does NOT write the file itself: a
statistically-fit artifact silently overwriting a checked-in constant on
every run is a foot-gun a human should catch, not automate past.

Usage: uv run python scripts/fit_age_curve.py <snapshot-dir>
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from ffdo.domain.constants import STANDARD_HALF_PPR
from ffdo.domain.models import SeasonStatLine
from ffdo.engine import adjustments
from ffdo.ingest import players as players_mod
from ffdo.ingest import snapshot
from ffdo.ingest import stats as stats_mod

_STATS_FILE_RE = re.compile(r"^stats_(\d{4})\.json\.gz$")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot_dir", type=Path)
    args = parser.parse_args()

    profiles = players_mod.parse(snapshot.load("players_nfl", args.snapshot_dir))

    seasons = sorted(
        int(m.group(1)) for f in args.snapshot_dir.glob("stats_*.json.gz")
        if (m := _STATS_FILE_RE.match(f.name))
    )
    history_by_player: dict[str, list[SeasonStatLine]] = {}
    for season in seasons:
        for pid, line in stats_mod.parse(
                snapshot.load(f"stats_{season}", args.snapshot_dir), season).items():
            history_by_player.setdefault(pid, []).append(line)

    curve = adjustments.fit_age_curve(history_by_player, profiles, STANDARD_HALF_PPR)

    print(f"# Fit from {args.snapshot_dir.name}, seasons {seasons}")
    print("DYNASTY_AGE_CURVE: Final[dict[str, dict[int, float]]] = {")
    for position in sorted(curve):
        ages = curve[position]
        print(f'    "{position}": {{')
        for age in sorted(ages):
            print(f"        {age}: {round(ages[age], 4)},")
        print("    },")
    print("}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against Task 1's fresh snapshot**

Run: `uv run python scripts/fit_age_curve.py data/snapshots/<today's date>/` (the exact directory Task 1 produced).

Expected: prints a `# Fit from ...` comment line followed by a `DYNASTY_AGE_CURVE = {...}` literal, one sub-dict per offensive position (`QB`, `RB`, `WR`, `TE` — `fit_age_curve` only ever populates positions that appear in `profiles`, and only for players with `age is not None`, so don't be surprised if `K`/`DEF` are absent or sparse).

- [ ] **Step 3: Sanity-check the output (manual)**

Read the printed curve. Confirm:
- Every position present has a `dict[int, float]` of plausible ages (roughly 20–40) mapped to plausible PPG deltas (single-digit magnitudes, not thousands — if you see huge numbers, something upstream is scoring wrong, stop and investigate before proceeding to Task 3).
- No `NaN`/`inf` anywhere in the printed values.
- RB and WR typically show a visible downward trend at higher ages (this is not guaranteed by the data, but its complete absence in every position would be a red flag worth double-checking against `adjustments.py`'s existing, already-tested `fit_age_curve` logic — if this script's output looks wrong, the bug is far more likely in THIS script's data assembly than in the unmodified, already-tested `fit_age_curve` function itself).

If anything looks wrong, do not proceed to Task 3 — fix this script first (it's new code; `fit_age_curve` itself is pre-existing and already unit-tested).

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (no test-suite-visible code added).

- [ ] **Step 5: Commit**

```bash
git add scripts/fit_age_curve.py
git commit -m "feat: scripts/fit_age_curve.py -- fit and print the dynasty age curve for review"
```

---

## Task 3: Populate `domain/constants.py::DYNASTY_AGE_CURVE`

**Files:**
- Modify: `src/ffdo/domain/constants.py`
- Test: `tests/domain/test_constants.py`

**Interfaces:**
- Consumes: the real, printed output from Task 2, Step 2.
- Produces: `DYNASTY_AGE_CURVE: Final[dict[str, dict[int, float]]]` — a real, data-derived constant Task 6 imports and Task 9 wires into the live app.

This task has no hardcoded numbers here — the exact values are runtime-determined (they come from real historical data fetched and fit in Tasks 1–2). This is not a placeholder: the STEPS below are fully concrete and mechanical; only the DATA VALUES are determined by actually running the prior tasks' code, the same way Task 8's durability re-backtest result can't be known until it's actually run.

- [ ] **Step 1: Write a structural test first**

Add to `tests/domain/test_constants.py` (check if this file exists from an earlier sub-project; if not, create it):

```python
from ffdo.domain.constants import DYNASTY_AGE_CURVE


def test_dynasty_age_curve_covers_the_core_offensive_positions():
    for position in ("QB", "RB", "WR", "TE"):
        assert position in DYNASTY_AGE_CURVE
        assert len(DYNASTY_AGE_CURVE[position]) > 0


def test_dynasty_age_curve_values_are_finite_and_plausible():
    for position, by_age in DYNASTY_AGE_CURVE.items():
        for age, delta in by_age.items():
            assert isinstance(age, int)
            assert 15 < age < 50   # a sanity bound, not a hard business rule
            assert -50.0 < delta < 50.0   # PPG deltas this large would indicate a scoring bug upstream
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/domain/test_constants.py -v -k dynasty_age_curve`
Expected: FAIL — `ImportError: cannot import name 'DYNASTY_AGE_CURVE'`.

- [ ] **Step 3: Paste in the real fitted curve**

In `src/ffdo/domain/constants.py`, add (near `NFL_BYE_WEEKS`, using the SAME `Final[...]` import already present in this file):

```python
# Empirically fit from real historical Sleeper stats via
# scripts/fit_age_curve.py -- mean change in points-per-game (under
# STANDARD_HALF_PPR scoring) from age N to age N+1, delta-method
# (survivorship-bias-aware; see engine/adjustments.py::fit_age_curve).
# Repurposed here for dynasty multi-year valuation (engine/dynasty_value.py)
# from its ORIGINAL use as a rejected redraft points-adjustment -- see
# docs/superpowers/specs/2026-09-12-valuation-model-design.md §0 for why
# a "no" for one question doesn't invalidate the data for a different one.
# Re-fit periodically via scripts/refresh_snapshot.py + scripts/fit_age_curve.py
# as more seasons of real data accumulate.
DYNASTY_AGE_CURVE: Final[dict[str, dict[int, float]]] = {
    # <<< PASTE Task 2 Step 2's printed dict body here, exactly as printed >>>
}
```

Replace the placeholder comment line with the EXACT printed output from Task 2, Step 2 (the full `{"QB": {...}, "RB": {...}, ...}` body — everything between the printed script's opening `{` and closing `}`, with the outer `DYNASTY_AGE_CURVE: Final[...] = {` / `}` wrapper you're adding here, not duplicated from the script's own print statements).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/domain/test_constants.py -v`
Expected: PASS, both new tests.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/domain/constants.py tests/domain/test_constants.py
git commit -m "feat: DYNASTY_AGE_CURVE -- real, data-fit dynasty age curve"
```

---

## Task 4: `engine/dynasty_value.py`

**Files:**
- Create: `src/ffdo/engine/dynasty_value.py`
- Test: `tests/engine/test_dynasty_value.py`

**Interfaces:**
- Consumes: `ffdo.domain.constants.SEASON_LENGTH` (existing), `ffdo.domain.models.{PlayerProfile, SeasonStatLine}` (existing), `ffdo.engine.adjustments.expected_games_missed` (existing, unmodified).
- Produces: `annuity_value(current_full: float, profile: PlayerProfile, history: Sequence[SeasonStatLine], age_curve: Mapping[str, Mapping[int, float]], *, current_season: int, horizon_years: int = 5, discount_rate: float = 0.15) -> float`.

**Critical correctness note, read before writing the implementation:** the formula MUST fold year 0 (`current_full`, weight `1.0`) into the SAME weighted average as every other projected year — do NOT compute `current_full + average(years 1..horizon)` as a sum of two separate pieces. That variant silently DOUBLES the result whenever `age_curve` has no data for a player's future ages (a common case — a player near the edge of the training data's observed age range, or simply before the curve has much depth at a position). The correct formula, verified: with an empty `age_curve`, every projected year's value equals `current_full` exactly, so the correct weighted-average-including-year-0 formula returns `current_full` unchanged (a graceful no-op) — the buggy sum-of-two-pieces variant would return `2 * current_full` instead. Step 1's test locks this in.

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/test_dynasty_value.py`:

```python
import pytest

from ffdo.domain.constants import SEASON_LENGTH
from ffdo.domain.models import PlayerProfile, SeasonStatLine
from ffdo.engine import dynasty_value


def _profile(pid="p", pos="RB", age=26, exp=4, active=True, injury=None):
    return PlayerProfile(player_id=pid, first_name="A", last_name="B",
                         position=pos, team="X", age=age, years_exp=exp,
                         injury_status=injury, active=active)


def _line(season, gp, **stats):
    return SeasonStatLine(player_id="p", season=season, games_played=gp,
                          season_length=SEASON_LENGTH[season], stats=stats)


def test_no_age_returns_current_full_unchanged():
    profile = _profile(age=None)
    result = dynasty_value.annuity_value(150.0, profile, [], {}, current_season=2026)
    assert result == 150.0


def test_empty_age_curve_leaves_value_unchanged_regardless_of_durability():
    """The critical regression test for the doubling bug described above.
    An empty curve means "no data on how this player's value changes with
    age" -- the correct behavior is a no-op (dynasty value == current_full),
    not a distortion in either direction, and this must hold EVEN THOUGH
    `expected_games_missed([], ...)` still returns a nonzero prior-based
    estimate (empty history is not the same as zero durability risk) --
    the no-op property comes from the curve being empty, not from
    durability being zero."""
    profile = _profile(pos="RB", age=26)
    result = dynasty_value.annuity_value(150.0, profile, [], {}, current_season=2026)
    assert result == pytest.approx(150.0)


def test_missing_position_in_curve_behaves_like_an_empty_curve():
    profile = _profile(pos="RB", age=26)
    curve = {"WR": {27: 5.0, 28: 5.0, 29: 5.0, 30: 5.0, 31: 5.0}}
    result = dynasty_value.annuity_value(150.0, profile, [], curve, current_season=2026)
    assert result == pytest.approx(150.0)


def test_young_player_with_positive_curve_deltas_scores_above_current_full():
    profile = _profile(pos="WR", age=24)
    curve = {"WR": {25: 3.0, 26: 3.0, 27: 2.0, 28: 1.0, 29: 0.0}}
    result = dynasty_value.annuity_value(100.0, profile, [], curve, current_season=2026)
    assert result > 100.0


def test_old_player_with_negative_curve_deltas_scores_below_current_full():
    profile = _profile(pos="RB", age=30)
    curve = {"RB": {31: -3.0, 32: -3.0, 33: -3.0, 34: -3.0, 35: -3.0}}
    result = dynasty_value.annuity_value(100.0, profile, [], curve, current_season=2026)
    assert result < 100.0


def test_result_is_never_negative_even_with_strongly_negative_deltas():
    profile = _profile(pos="RB", age=33)
    curve = {"RB": {34: -1000.0, 35: -1000.0, 36: -1000.0, 37: -1000.0, 38: -1000.0}}
    result = dynasty_value.annuity_value(50.0, profile, [], curve, current_season=2026)
    assert 0.0 <= result < 50.0


def test_horizon_years_and_discount_rate_are_overridable():
    profile = _profile(pos="WR", age=24)
    curve = {"WR": {25: 10.0, 26: 10.0}}
    short_horizon = dynasty_value.annuity_value(
        100.0, profile, [], curve, current_season=2026, horizon_years=2)
    # With only a 2-year horizon and the curve only defined for ages 25/26,
    # extending the horizon further (with zero-delta fallback beyond age 26)
    # must not raise and must still return a sane, positive-improvement value.
    long_horizon = dynasty_value.annuity_value(
        100.0, profile, [], curve, current_season=2026, horizon_years=5)
    assert short_horizon > 100.0
    assert long_horizon > 100.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_dynasty_value.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.engine.dynasty_value'`.

- [ ] **Step 3: Write the implementation**

Create `src/ffdo/engine/dynasty_value.py`:

```python
"""Real, multi-year dynasty value -- replaces dynasty_curve.py's coarse,
hand-authored per-position multiplier.

Projects a player's blended current-year value forward `horizon_years`
using the empirically-fit per-position age-delta curve
(engine.adjustments.fit_age_curve, repurposed here from its ORIGINAL,
rejected use as a redraft points-adjustment -- see
docs/superpowers/specs/2026-09-12-valuation-model-design.md §0 for why a
"no" for that question doesn't invalidate the same data for THIS one: not
"does this improve this year's rank prediction" but "how much future
production does this asset have left"). Discounts each future year by a
fixed per-year rate and a durability-based survival fraction, then
collapses the whole multi-year picture back into a single season-scale
annuity-equivalent value so it slots into engine.vor.compute exactly like
a redraft value does.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from ffdo.domain.constants import SEASON_LENGTH
from ffdo.domain.models import PlayerProfile, SeasonStatLine
from ffdo.engine.adjustments import expected_games_missed

HORIZON_YEARS: Final[int] = 5
DISCOUNT_RATE: Final[float] = 0.15  # per year; a common dynasty-community
                                     # convention, NOT backtested -- no clean
                                     # out-of-sample target exists for "was
                                     # this dynasty valuation right" the way
                                     # ADP-vs-actual-points exists for redraft.


def annuity_value(
    current_full: float,
    profile: PlayerProfile,
    history: Sequence[SeasonStatLine],
    age_curve: Mapping[str, Mapping[int, float]],
    *,
    current_season: int,
    horizon_years: int = HORIZON_YEARS,
    discount_rate: float = DISCOUNT_RATE,
) -> float:
    if profile.age is None:
        return current_full

    season_length = SEASON_LENGTH[current_season]
    missed = expected_games_missed(history, profile.position, current_season=current_season)
    survival = 1.0 - (missed / season_length)

    position_curve = age_curve.get(profile.position, {})
    # Year 0 (this season, undiscounted) is folded into the SAME weighted
    # average as every projected year -- see this module's docstring and
    # the plan's Task 4 note for why summing year 0 on top of an average
    # of the other years (instead of folding it in) silently doubles the
    # result whenever `age_curve` has no data for this player's future ages.
    total_value = current_full
    total_weight = 1.0
    cumulative_delta = 0.0
    for year in range(1, horizon_years + 1):
        cumulative_delta += position_curve.get(profile.age + year, 0.0)
        year_value = max(0.0, current_full + cumulative_delta)
        weight = survival / (1.0 + discount_rate) ** year
        total_value += year_value * weight
        total_weight += weight

    return total_value / total_weight
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_dynasty_value.py -v`
Expected: PASS, all 7 tests.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/engine/dynasty_value.py tests/engine/test_dynasty_value.py
git commit -m "feat: dynasty_value.annuity_value -- real multi-year dynasty valuation"
```

---

## Task 5: `ingest/sleeper/player_history.py`

**Files:**
- Create: `src/ffdo/ingest/sleeper/player_history.py`
- Test: `tests/ingest/sleeper/test_player_history.py`

**Interfaces:**
- Consumes: `ffdo.ingest.client.{V1, SleeperClient}` (existing), `ffdo.ingest.stats.parse` (existing, unmodified), `ffdo.domain.models.SeasonStatLine` (existing).
- Produces: `fetch_season(sleeper, season: int) -> dict[str, SeasonStatLine]`, `history_for(player_ids: Iterable[str], season_stats: Mapping[int, Mapping[str, SeasonStatLine]]) -> dict[str, list[SeasonStatLine]]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/sleeper/test_player_history.py`:

```python
import httpx

from ffdo.domain.models import SeasonStatLine
from ffdo.ingest.client import V1, SleeperClient
from ffdo.ingest.sleeper import player_history


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def test_fetch_season_hits_the_right_url_and_parses():
    def handler(request):
        assert str(request.url) == f"{V1}/stats/nfl/regular/2023"
        return httpx.Response(200, json={"p1": {"gp": 16, "rush_yd": 1000.0}})

    out = player_history.fetch_season(_client(handler), 2023)
    assert out["p1"].season == 2023
    assert out["p1"].games_played == 16
    assert out["p1"].stats["rush_yd"] == 1000.0


def test_history_for_filters_to_requested_players_across_seasons():
    season_stats = {
        2022: {
            "p1": SeasonStatLine(player_id="p1", season=2022, games_played=16,
                                 season_length=17, stats={}),
            "p2": SeasonStatLine(player_id="p2", season=2022, games_played=10,
                                 season_length=17, stats={}),
        },
        2023: {
            "p1": SeasonStatLine(player_id="p1", season=2023, games_played=17,
                                 season_length=17, stats={}),
        },
    }
    out = player_history.history_for(["p1"], season_stats)
    assert set(out) == {"p1"}
    assert {line.season for line in out["p1"]} == {2022, 2023}


def test_history_for_excludes_players_not_requested():
    season_stats = {
        2022: {"p2": SeasonStatLine(player_id="p2", season=2022, games_played=10,
                                    season_length=17, stats={})},
    }
    out = player_history.history_for(["p1"], season_stats)
    assert "p2" not in out


def test_history_for_returns_an_empty_list_for_a_player_with_no_seasons():
    out = player_history.history_for(["ghost"], {2022: {}})
    assert out == {"ghost": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/sleeper/test_player_history.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.ingest.sleeper.player_history'`.

- [ ] **Step 3: Write the implementation**

Create `src/ffdo/ingest/sleeper/player_history.py`:

```python
"""Multi-season historical stat lines per player, from Sleeper's
/v1/stats/nfl/regular/<season> endpoint -- the same wire format
ingest.stats.parse (already existing, unmodified) already translates, just
fetched live instead of from a frozen snapshot.

Sleeper's stats endpoint returns every player league-wide in one call, not
filtered by player_id -- `fetch_season` is therefore the unit that's
actually cacheable (one entry per season, shared across every league and
request that needs it, since finished-season stats never change).
`history_for` is a pure, uncached reshaping step: given some
already-fetched seasons' full-league stats, pick out just the requested
players across however many of those seasons they actually appear in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ffdo.domain.models import SeasonStatLine
from ffdo.ingest import stats as stats_mod
from ffdo.ingest.client import V1, SleeperClient


def fetch_season(sleeper: SleeperClient, season: int) -> dict[str, SeasonStatLine]:
    raw = sleeper.get_json(f"{V1}/stats/nfl/regular/{season}")
    return stats_mod.parse(raw, season)


def history_for(
    player_ids: Iterable[str],
    season_stats: Mapping[int, Mapping[str, SeasonStatLine]],
) -> dict[str, list[SeasonStatLine]]:
    ids = set(player_ids)
    out: dict[str, list[SeasonStatLine]] = {pid: [] for pid in ids}
    for lines in season_stats.values():
        for pid, line in lines.items():
            if pid in ids:
                out[pid].append(line)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/sleeper/test_player_history.py -v`
Expected: PASS, all 4 tests.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/ingest/sleeper/player_history.py tests/ingest/sleeper/test_player_history.py
git commit -m "feat: ingest.sleeper.player_history -- live multi-season stat fetch"
```

---

## Task 6: `engine/ros_value.py` — thread `history`/`age_curve`, delete `dynasty_curve.py`

**Files:**
- Modify: `src/ffdo/engine/ros_value.py`
- Modify: `tests/engine/test_ros_value.py`
- Delete: `src/ffdo/engine/dynasty_curve.py`
- Delete: `tests/engine/test_dynasty_curve.py`

**Interfaces:**
- Consumes: `ffdo.engine.dynasty_value.annuity_value` (Task 4).
- Produces: `roster_value(player_ids, league, *, resolved_format, season_proj, profiles, actuals, weeks_played, season_weeks=18, history: Mapping[str, Sequence[SeasonStatLine]] | None = None, age_curve: Mapping[str, Mapping[int, float]] | None = None) -> dict[str, ValuedPlayer]` — the two new params are additive; every existing call passing only the original params is unaffected.

- [ ] **Step 1: Write the failing tests**

Add to `tests/engine/test_ros_value.py` (this file already has `_league`, `_profile`, `_proj`, `_RB_STATS` helpers from earlier work — read the file first, reuse them, don't redefine):

```python
def test_dynasty_uses_the_real_age_curve_when_provided():
    profiles = {"rb": _profile("rb", "RB", age=24)}
    proj = {"rb": _proj("rb", _RB_STATS)}
    kw = dict(season_proj=proj, profiles=profiles, actuals={}, weeks_played=0)
    no_curve = ros_value.roster_value(["rb"], _league(), resolved_format="dynasty", **kw)
    curve = {"RB": {25: 10.0, 26: 10.0, 27: 10.0, 28: 10.0, 29: 10.0}}
    with_curve = ros_value.roster_value(
        ["rb"], _league(), resolved_format="dynasty", age_curve=curve, **kw)
    assert with_curve["rb"].projected_points > no_curve["rb"].projected_points


def test_dynasty_with_no_curve_or_history_matches_current_full_not_double_it():
    """Regression for the dynasty_value doubling bug (see Task 4) --
    verified end-to-end through roster_value, not just in dynasty_value's
    own unit tests."""
    profiles = {"rb": _profile("rb", "RB", age=24)}
    proj = {"rb": _proj("rb", _RB_STATS)}
    valued = ros_value.roster_value(
        ["rb"], _league(), resolved_format="dynasty", season_proj=proj,
        profiles=profiles, actuals={}, weeks_played=0)
    # _RB_STATS scores ~250 pts under _league()'s scoring (see the existing
    # comment on _RB_STATS in this file) -- with no curve/history, dynasty
    # value must equal that ~250, not ~500.
    assert valued["rb"].projected_points < 300.0


def test_redraft_branch_ignores_history_and_age_curve_entirely():
    profiles = {"rb": _profile("rb", "RB", age=24)}
    proj = {"rb": _proj("rb", _RB_STATS)}
    kw = dict(season_proj=proj, profiles=profiles, actuals={"rb": 50.0}, weeks_played=5)
    without = ros_value.roster_value(["rb"], _league(), resolved_format="redraft", **kw)
    with_extra = ros_value.roster_value(
        ["rb"], _league(), resolved_format="redraft",
        history={"rb": []}, age_curve={"RB": {25: 999.0}}, **kw)
    assert without["rb"].projected_points == with_extra["rb"].projected_points
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_ros_value.py -v -k "curve or double_it or ignores"`
Expected: FAIL — `TypeError: roster_value() got an unexpected keyword argument 'age_curve'`.

- [ ] **Step 3: Modify the implementation**

Replace `src/ffdo/engine/ros_value.py` in full with:

```python
"""Per-player value for the season screen -- THE SWAPPABLE SEAM.

Sub-project #4 replaced dynasty_curve.py's hand-authored multiplier with a
real, data-driven multi-year model (engine.dynasty_value.annuity_value)
while keeping this function's external call shape backward compatible:
`history`/`age_curve` default to `None`, so every existing redraft/keeper
call site is unaffected.

What it does today:
  current_full = blend(preseason projection, season-to-date pace),
                 weight shifting toward pace as weeks_played grows
  redraft/keeper: value = max(0, current_full - banked)   [rest of season]
  dynasty:        value = dynasty_value.annuity_value(current_full, ...)
                          [a real multi-year, discounted, data-driven model]
Then engine.vor.compute puts it on a value-over-replacement scale.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ffdo.domain.constants import INJURY_OUT_STATUSES
from ffdo.domain.models import (
    PlayerProfile, SeasonProjection, SeasonStatLine, ValuedPlayer,
)
from ffdo.engine import dynasty_value, vor
from ffdo.engine.scoring import score_stats

K = 4  # pace-blend half-life: at weeks_played == K, pace and preseason weigh equally


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
    history: Mapping[str, Sequence[SeasonStatLine]] | None = None,
    age_curve: Mapping[str, Mapping[int, float]] | None = None,
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

        if not profile.active or profile.injury_status in INJURY_OUT_STATUSES:
            value_pts[pid] = 0.0
        elif is_dynasty:
            value_pts[pid] = dynasty_value.annuity_value(
                current_full, profile,
                (history or {}).get(pid, ()), age_curve or {},
                current_season=league.season)
        else:
            value_pts[pid] = max(0.0, current_full - banked)

    return vor.compute(value_pts, profiles, league)
```

- [ ] **Step 4: Delete the old dynasty curve and its test**

```bash
git rm src/ffdo/engine/dynasty_curve.py tests/engine/test_dynasty_curve.py
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_ros_value.py -v`
Expected: PASS — all tests in this file, including the 3 new ones and every pre-existing one (`test_redraft_subtracts_banked_dynasty_does_not` in particular must still pass unmodified: with no `history`/`age_curve` passed, dynasty now returns `current_full` unchanged via the corrected annuity formula, which is still strictly greater than `redraft = current_full - banked` for any positive `banked` — verify this holds rather than assuming it, since this is exactly the kind of test that could pass "for the wrong reason").

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/engine/ros_value.py tests/engine/test_ros_value.py
git commit -m "feat: ros_value dynasty branch uses the real annuity model; delete dynasty_curve.py"
```

---

## Task 7: `backtest/harness.py` — `sweep_durability_weights`

**Files:**
- Modify: `src/ffdo/backtest/harness.py`
- Modify: `tests/backtest/test_harness.py`

**Interfaces:**
- Consumes: `evaluate_season` (existing, unmodified, same file).
- Produces: `sweep_durability_weights(season: int, weights: Sequence[float]) -> list[dict]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/backtest/test_harness.py` (this file already runs against the REAL committed snapshot at `data/snapshots/2026-08-22-draft-day/` via `harness.evaluate_season`'s default `snapshot_dir` — no fixture setup needed, same as its existing tests):

```python
def test_sweep_durability_weights_returns_one_result_per_weight():
    results = harness.sweep_durability_weights(2025, [0.0, 0.5, 1.0])
    assert len(results) == 3
    assert results[0]["improvement"] == pytest.approx(0.0, abs=1e-9)  # weight 0.0 is always a no-op baseline
    assert all("model_rho" in r for r in results)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/backtest/test_harness.py -v -k sweep`
Expected: FAIL — `AttributeError: module 'ffdo.backtest.harness' has no attribute 'sweep_durability_weights'`.

- [ ] **Step 3: Write the implementation**

Add to `src/ffdo/backtest/harness.py`, after `evaluate_season`:

```python
def sweep_durability_weights(season: int, weights: Sequence[float]) -> list[dict]:
    return [evaluate_season(season, age_weight=0.0, durability_weight=w) for w in weights]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/backtest/test_harness.py -v`
Expected: PASS, including this new test and every pre-existing test in the file.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/backtest/harness.py tests/backtest/test_harness.py
git commit -m "feat: harness.sweep_durability_weights -- run evaluate_season across a weight range"
```

---

## Task 8: Durability re-backtest, ruling, and conditional live wiring

**Files:**
- Modify: `src/ffdo/engine/adjustments.py` (possibly, only `DURABILITY_WEIGHT`'s value)
- Modify: `src/ffdo/engine/ros_value.py` (possibly, only if promoted)
- Modify: `tests/engine/test_ros_value.py` (possibly, only if promoted)

> This task's OUTCOME is genuinely unknown until it runs — it is not a coding task with a pre-known answer, the same way Task 3's actual curve values weren't known until Tasks 1–2 ran. Follow the mechanical decision procedure below exactly; do not substitute your own judgment about whether a result "feels" real or like noise. Record the full result table and your decision as a `Ruling:` in the SDD ledger.

**Interfaces:**
- Consumes: `harness.sweep_durability_weights` (Task 7), `adjustments.build`/`adjustments.DURABILITY_WEIGHT` (existing, unmodified so far), `engine.replacement.replacement_levels` (existing, unmodified) — only touched if promotion happens.

- [ ] **Step 1: Run the sweep**

Determine the seasons to test: the same seasons `tests/backtest/test_harness.py`'s existing parametrized test already validates against (`[2023, 2024, 2025]`) are the safe minimum — every one has a complete, real, already-verified actual-outcomes dataset. If Task 1's refreshed snapshot ALSO contains a `stats_2026` file with enough real games played by the time this task runs to be a meaningful full-season proxy (check `games_played` values in that file — if the vast majority of active players show 0 or 1 games played, the season is too early to use, skip it), you may add 2026 as a fourth season; otherwise stick to the three known-good ones. Document which seasons you actually used and why in this task's report.

Run (adjust the season list per the above):

```bash
uv run python -c "
from ffdo.backtest import harness
weights = [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0]
for season in [2023, 2024, 2025]:
    print(f'--- {season} ---')
    for r in harness.sweep_durability_weights(season, weights):
        print(r)
"
```

- [ ] **Step 2: Apply the mechanical decision procedure**

For each weight `w > 0.0` in the sweep:
1. Compute `mean_improvement(w)` = the average of `improvement` across every tested season for that weight.
2. `w` is a **candidate** only if BOTH:
   - `mean_improvement(w) > 0`, AND
   - `improvement > 0` in **every individual tested season** for that weight (not just on average — this matches the ORIGINAL Task 14 rule exactly, deliberately not relaxed).
3. Among candidates, `w` must ALSO pass a **stability check**: at least one of its immediate neighbors in the swept weight list (`weights[i-1]` or `weights[i+1]`) must ALSO be a candidate by the same two criteria above. This directly targets the exact failure mode the ORIGINAL Task 14 run flagged ("non-monotonic... flips sign at 2.0... consistent with harness noise, not real signal") — a single isolated spike surrounded by non-candidates on both sides does not promote.
4. If one or more weights survive all three checks, the weight to promote is the **smallest** surviving candidate (the most conservative effect size that still cleared the bar).
5. If no weight survives, `DURABILITY_WEIGHT` stays `0.0`.

- [ ] **Step 3: Ledger the ruling**

Write a `Ruling:` entry (in whatever ledger this plan is being executed under — the SDD skill's `progress.md` if using subagent-driven-development) recording: the full sweep table (every weight × every season's `improvement`), which weights were candidates, which passed the stability check, and the final decision. This is the permanent record — the numbers from Task 14's original one-day run are already in the design spec's prior-art section; this new run's numbers belong in the ledger, not silently discarded.

- [ ] **Step 4a — IF NOT PROMOTED:** stop here.

No code changes. `DURABILITY_WEIGHT` stays `0.0` in `engine/adjustments.py`, unmodified. Run `uv run pytest -q` to confirm nothing regressed (expect the same count as after Task 7), then commit just the ledger update (if the ledger is a tracked file in this repo; if it's the git-ignored SDD workspace, there is nothing to commit — say so in your report and move to Task 9).

- [ ] **Step 4b — IF PROMOTED:** wire it live.

In `src/ffdo/engine/adjustments.py`, change only:

```python
DURABILITY_WEIGHT: float = <the promoted weight from Step 2>
```

In `src/ffdo/engine/ros_value.py`, add the conditional adjustments pass. Read the current file first (Task 6 just rewrote it) — the ONLY changes are two new imports and the final `return` statement; everything else in the file (the docstring, `_blended_full_season`, the `value_pts` loop) stays exactly as Task 6 left it. Here is the complete file with those two changes applied:

```python
"""Per-player value for the season screen -- THE SWAPPABLE SEAM.

Sub-project #4 replaced dynasty_curve.py's hand-authored multiplier with a
real, data-driven multi-year model (engine.dynasty_value.annuity_value)
while keeping this function's external call shape backward compatible:
`history`/`age_curve` default to `None`, so every existing redraft/keeper
call site is unaffected.

What it does today:
  current_full = blend(preseason projection, season-to-date pace),
                 weight shifting toward pace as weeks_played grows
  redraft/keeper: value = max(0, current_full - banked)   [rest of season]
  dynasty:        value = dynasty_value.annuity_value(current_full, ...)
                          [a real multi-year, discounted, data-driven model]
Then engine.vor.compute puts it on a value-over-replacement scale
(redraft/keeper also folds in a durability adjustment once promoted --
see engine/adjustments.py -- via vor.compute's existing `adjustments` kwarg).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ffdo.domain.constants import INJURY_OUT_STATUSES
from ffdo.domain.models import (
    PlayerProfile, SeasonProjection, SeasonStatLine, ValuedPlayer,
)
from ffdo.engine import adjustments, dynasty_value, vor
from ffdo.engine.replacement import replacement_levels
from ffdo.engine.scoring import score_stats

K = 4  # pace-blend half-life: at weeks_played == K, pace and preseason weigh equally


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
    history: Mapping[str, Sequence[SeasonStatLine]] | None = None,
    age_curve: Mapping[str, Mapping[int, float]] | None = None,
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

        if not profile.active or profile.injury_status in INJURY_OUT_STATUSES:
            value_pts[pid] = 0.0
        elif is_dynasty:
            value_pts[pid] = dynasty_value.annuity_value(
                current_full, profile,
                (history or {}).get(pid, ()), age_curve or {},
                current_season=league.season)
        else:
            value_pts[pid] = max(0.0, current_full - banked)

    if not is_dynasty and adjustments.DURABILITY_WEIGHT:
        positions = {pid: profiles[pid].position for pid in value_pts if pid in profiles}
        season_replacement = replacement_levels(value_pts, positions, league)
        replacement_ppg = {pos: level / season_weeks for pos, level in season_replacement.items()}
        built = adjustments.build(
            profiles, history or {}, value_pts, replacement_ppg,
            durability_weight=adjustments.DURABILITY_WEIGHT,
            current_season=league.season)
        return vor.compute(value_pts, profiles, league, adjustments=built)

    return vor.compute(value_pts, profiles, league)
```

Add `SeasonStatLine` to this file's existing `from ffdo.domain.models import ...` import line (it currently imports `LeagueProfile, PlayerProfile, SeasonProjection` — add `SeasonStatLine` alongside them), then add this test:

```python
def test_durability_adjustment_applies_when_promoted_and_history_shows_missed_games():
    """Only meaningful once DURABILITY_WEIGHT is nonzero -- this test (and
    the code it exercises) only exist in the committed diff if Task 8
    actually promoted it (Step 4b's branch); if Task 8 took Step 4a, this
    test is not added at all.

    The exact promoted weight's magnitude isn't knowable at plan-writing
    time, so this asserts a DIRECTION (a fragile player scores lower once
    durability is live), not an exact value -- this is a real, complete,
    runnable test, not a placeholder; only the underlying DURABILITY_WEIGHT
    constant it depends on is determined by an earlier step."""
    profiles = {"rb": _profile("rb", "RB", age=26)}
    proj = {"rb": _proj("rb", _RB_STATS)}
    fragile_history = {"rb": [
        SeasonStatLine(player_id="rb", season=2023, games_played=8,
                       season_length=17, stats={}),
        SeasonStatLine(player_id="rb", season=2024, games_played=9,
                       season_length=18, stats={}),
        SeasonStatLine(player_id="rb", season=2025, games_played=10,
                       season_length=18, stats={}),
    ]}
    with_history = ros_value.roster_value(
        ["rb"], _league(), resolved_format="redraft", season_proj=proj,
        profiles=profiles, actuals={}, weeks_played=0, history=fragile_history)
    without_history = ros_value.roster_value(
        ["rb"], _league(), resolved_format="redraft", season_proj=proj,
        profiles=profiles, actuals={}, weeks_played=0)
    assert with_history["rb"].vor < without_history["rb"].vor
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

If not promoted (Step 4a): commit only the ledger update, if tracked.
If promoted (Step 4b):

```bash
git add src/ffdo/engine/adjustments.py src/ffdo/engine/ros_value.py tests/engine/test_ros_value.py
git commit -m "feat: promote DURABILITY_WEIGHT and wire it live into redraft valuation"
```

---

## Task 9: `api/app.py` wiring

**Files:**
- Modify: `src/ffdo/api/app.py`
- Modify: `tests/api/test_season_endpoint.py`

**Interfaces:**
- Consumes: `player_history.{fetch_season, history_for}` (Task 5), `DYNASTY_AGE_CURVE` (Task 3), `roster_value`'s new `history`/`age_curve` params (Task 6).

- [ ] **Step 1: Read the current file structure first**

Read `src/ffdo/api/app.py`'s `_assemble_season` function and both of its call sites (inside `get_season` and inside `_season_espn`) before editing — this file has grown across sub-projects #2 and #3's final-review fix waves, so verify the line numbers/exact surrounding code below against the real current file rather than assuming they haven't shifted.

- [ ] **Step 2: Write the failing tests**

Add to `tests/api/test_season_endpoint.py` (this file already has `_tracked`, `_ROSTERS`, `_USERS`, `_PLAYERS`, `_STATE`, `_PROJ`, `_MATCHUPS` fixtures from earlier sub-projects, and imports `LeagueStore`, `V1`, `TestClient`, `create_app`, `app_mod` — read the file first to confirm these are still there under the same names before reusing them). These two tests use their own self-contained fake client rather than the file's shared `_recording_client` helper, so they don't depend on guessing that helper's exact current shape:

```python
def test_dynasty_league_fetches_player_history_and_uses_the_real_curve(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="dynasty"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    stats_2025 = {"p_rb": {"gp": 16, "rush_yd": 1200.0}}
    resp = {
        f"{V1}/state/nfl": _STATE,
        f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS,
        f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS,
        "/projections/": _PROJ,
        "/matchups/": _MATCHUPS,
        f"{V1}/stats/nfl/regular/2025": stats_2025,
    }

    class _DynastyClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            for key, val in resp.items():
                if key in url:
                    return val
            if "/stats/nfl/regular/" in url:
                return {}   # every other historical season -- no data, that's fine
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _DynastyClient)
    res_dynasty = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season")
    assert res_dynasty.status_code == 200

    store2 = LeagueStore(tmp_path / "ffdo2.db")
    store2.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store2)
    res_redraft = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season")
    assert res_redraft.status_code == 200

    dynasty_val = next(p["value"] for p in res_dynasty.json()["your_roster"]["players"]
                       if p["player_id"] == "p_rb")
    redraft_val = next(p["value"] for p in res_redraft.json()["your_roster"]["players"]
                       if p["player_id"] == "p_rb")
    # p_rb has real banked actuals in _MATCHUPS (90.0), so redraft's
    # max(0, current_full - banked) is strictly below current_full, while
    # dynasty's annuity_value degrades gracefully toward current_full when
    # the real DYNASTY_AGE_CURVE has no entry at this exact age -- the two
    # must differ regardless of the curve's precise fitted values, since a
    # real multi-year adjustment coincidentally landing on exactly +90.0 is
    # not a real risk.
    assert dynasty_val != redraft_val


def test_redraft_league_never_calls_the_stats_endpoint(monkeypatch, tmp_path):
    """A redraft league must not fetch player history at all -- no wasted
    calls for the common case."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    resp = {
        f"{V1}/state/nfl": _STATE,
        f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS,
        f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS,
        "/projections/": _PROJ,
        "/matchups/": _MATCHUPS,
    }
    calls: list[str] = []

    class _RecordingClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            calls.append(url)
            for key, val in resp.items():
                if key in url:
                    return val
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _RecordingClient)
    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season")
    assert res.status_code == 200
    assert not any("/stats/nfl/regular/" in c for c in calls), (
        f"redraft league must never fetch player history: {calls}")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_season_endpoint.py -v -k "player_history or stats_endpoint"`
Expected: FAIL (the wiring doesn't exist yet — either a fetch error, or the dynasty/redraft values not differing as expected).

- [ ] **Step 4: Wire it in**

In `src/ffdo/api/app.py`:

1. Add `DYNASTY_AGE_CURVE` to the existing constants import line:
   ```python
   from ffdo.domain.constants import DYNASTY_AGE_CURVE, NFL_BYE_WEEKS, SEASON_LENGTH
   ```

2. Inside `create_app()`, alongside the existing `from ffdo.ingest.sleeper import traded_picks as traded_picks_mod` line, add:
   ```python
   from ffdo.ingest.sleeper import player_history as player_history_mod
   ```

3. Alongside the existing cache-dict declarations (`roster_count_caches`, `weekly_proj_caches`, `schedule_caches`), add:
   ```python
   player_history_caches: dict[int, _TTLCache] = {}
   ```

4. Alongside the existing `_roster_count_cache_for`/`_weekly_proj_cache_for` helpers, add:
   ```python
   def _player_history_cache_for(season: int) -> _TTLCache:
       # A week, not the hour/day TTLs used elsewhere in this file --
       # finished-season stats never change, so this can cache far longer.
       return player_history_caches.setdefault(season, _TTLCache(ttl_seconds=7 * 24 * 3600))
   ```

5. Change `_assemble_season`'s signature to accept a `history` parameter, and thread it into the `roster_value` call:
   ```python
   def _assemble_season(lg, nfl, through_week, rosters, profiles, proj_anchor,
                        actuals, standings_rank, capital, history) -> dict:
       """..."""  # keep the existing docstring
       all_pids = {pid for r in rosters for pid in r.player_ids}
       valued = ros_value_mod.roster_value(
           all_pids, lg,
           resolved_format=lg.resolved_format,
           season_proj=proj_anchor,
           profiles=profiles,
           actuals=actuals,
           weeks_played=through_week,
           season_weeks=_season_weeks(lg.season),
           history=history,
           age_curve=DYNASTY_AGE_CURVE if history is not None else None)
       # ... rest of the function unchanged
   ```

6. In `get_season` (the Sleeper branch), inside the existing `try:` block, right after the `capital` if-block (before the `except`/`finally`), add:
   ```python
   history = None
   if lg.resolved_format == "dynasty":
       season_stats = {
           season: _player_history_cache_for(season).get(
               lambda season=season: player_history_mod.fetch_season(sleeper, season))
           for season in range(2021, lg.season)
       }
       all_pids_for_history = {pid for r in rosters for pid in r.player_ids}
       history = player_history_mod.history_for(all_pids_for_history, season_stats)
   ```
   Then update this branch's `_assemble_season` call to pass `history`:
   ```python
   return _assemble_season(lg, nfl, through_week, rosters, profiles,
                           proj_anchor, actuals, _standings_rank(rosters),
                           capital, history)
   ```

7. In `_season_espn`, the Sleeper client (used for `profiles`/`proj_anchor`) closes BEFORE `rosters` is known (ESPN rosters come later, from ESPN's own endpoint + crosswalk) — so fetch `season_stats` (the cacheable per-season data, independent of which players are needed) while `sleeper` is still open, alongside `proj_anchor`:
   ```python
   season_stats = None
   if lg.resolved_format == "dynasty":
       season_stats = {
           season: _player_history_cache_for(season).get(
               lambda season=season: player_history_mod.fetch_season(sleeper, season))
           for season in range(2021, lg.season)
       }
   ```
   Then, AFTER `rosters` is fetched from ESPN (later in the same function, once `all_pids` for the roster is knowable), compute `history` from the already-fetched `season_stats` (no more Sleeper I/O needed at this point — `history_for` is pure):
   ```python
   history = None
   if season_stats is not None:
       all_pids_for_history = {pid for r in rosters for pid in r.player_ids}
       history = player_history_mod.history_for(all_pids_for_history, season_stats)
   ```
   Update this branch's `_assemble_season` call:
   ```python
   return _assemble_season(lg, nfl, through_week, rosters, profiles,
                           proj_anchor, actuals, _standings_rank(rosters), None, history)
   ```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_season_endpoint.py -v`
Expected: PASS, all tests in this file including the 2 new ones.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/api/app.py tests/api/test_season_endpoint.py
git commit -m "feat: wire real dynasty valuation into the /season endpoint"
```

---

## Task 10: README + browser smoke test

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update `README.md`**

Find the existing paragraph (added in sub-project #2) describing the season screen's dynasty values, and add one sentence to it (or immediately after):

```markdown
Dynasty values are a real, multi-year model: this week's projected value
is projected forward using an age curve fit from real historical Sleeper
data (not a hand-authored guess), discounted for both time and
injury/durability risk, and collapsed back into a single season-scale
number.
```

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, full count from all 9 prior tasks (10th task's own commit won't change the count).

- [ ] **Step 3: Browser smoke (manual — the executor or controller)**

Start the server against the pinned dev league (`uv run python scripts/seed_dev_league.py Schroedes`, then `uv run uvicorn ffdo.api.app:app --port 8150`). If the pinned dev league is not a dynasty/keeper league, this step needs a dynasty league tracked instead — check via `GET /api/leagues` first.

For a dynasty (or keeper — `_draft_capital_payload`'s dynasty/keeper gate is unrelated to this plan, but `roster_value`'s dynasty branch triggers only on `resolved_format == "dynasty"` exactly, not "keeper") league:
- Open the season screen. Confirm the roster panel's player values render (no 500, no missing values) — these numbers are now coming from the real annuity model, not the deleted `dynasty_curve.multiplier`.
- Check devtools network tab: confirm `/season` succeeds and doesn't time out (the new live multi-season history fetch adds real network calls the first time; confirm it completes in a reasonable time, and reload the page to confirm the second load is fast — the 7-day `_player_history_cache_for` cache should make it instant).
- No console errors.

For a redraft league (if one is also tracked): confirm the season screen still renders correctly and loads noticeably faster than the dynasty league (no history fetch at all for redraft).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "chore: document the real dynasty valuation model in the README"
```

---

## Self-Review Notes (for the plan author, not a task)

- **Spec coverage:** §1.1's five goals map to Tasks 1 (refresh), 2+3 (age curve fit + check-in), 4 (dynasty annuity), 5+9 (live history ingest + wiring), 7+8 (durability re-backtest + conditional wiring) respectively. §1.2's non-goals are respected: no contract/cap modeling, no team-context modeling, no age-dependent injury growth, no automatic snapshot refresh (Task 1 is manually run), no redraft age-adjustment revisit (`AGE_WEIGHT` untouched), no frontend changes (none planned). §9's future improvements are recorded in the spec, not attempted here.
- **The formula bug caught during spec-writing (the "current_full + average(1..horizon)" doubling bug) is carried through correctly**: Task 4's implementation code, its critical-note comment, and its dedicated regression test all reflect the CORRECTED formula; Task 6's `test_dynasty_with_no_curve_or_history_matches_current_full_not_double_it` re-verifies it end-to-end through `roster_value`, not just in isolation.
- **Type/signature consistency check:** `annuity_value`'s signature in Task 4 (`current_full, profile, history, age_curve, *, current_season, horizon_years=5, discount_rate=0.15`) matches exactly how Task 6's `ros_value.py` calls it. `roster_value`'s new `history`/`age_curve` params (Task 6) match exactly how Task 9's `app.py` wiring passes them. `player_history.fetch_season`/`history_for` (Task 5) match exactly how Task 9 calls them (`fetch_season(sleeper, season)`, `history_for(player_ids, season_stats)`). `DYNASTY_AGE_CURVE`'s shape (`dict[str, dict[int, float]]`, Task 3) matches what Task 4's `annuity_value` and Task 9's wiring both expect.
- **Task 8's genuine uncertainty is handled honestly** — Step 4a/4b are both fully specified (not a placeholder), and Task 8's own test addition is explicitly marked as needing completion against the ACTUAL promoted weight rather than given fake precision for an unknowable number, mirroring how Task 3 handles the unknowable age-curve values (concrete steps, runtime-determined data).
