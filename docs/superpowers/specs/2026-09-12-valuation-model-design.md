# Post-Draft Player Valuation Model — Design

**Date:** 2026-09-12
**Status:** Approved, pending implementation
**Stacked on:** `claude/weekly-lineup` (PR #27, sub-project #3 — weekly optimal lineup). This is a stacked PR: it branches from #3 and merges after it.
**Prior art:**
- `docs/superpowers/specs/2026-09-03-roster-standings-view-design.md` — defines `engine/ros_value.roster_value(...)`, the seam this sub-project replaces the internals of, and `engine/dynasty_curve.py`, which this sub-project deletes and replaces.
- `docs/superpowers/plans/2026-08-22-fantasy-football-draft-optimizer.md` (Tasks 13–14) and `docs/superpowers/specs/2026-08-22-fantasy-football-draft-optimizer-design.md` §8 — the ORIGINAL, pre-multi-league age/durability adjustment system this sub-project repurposes and re-validates. Read this first: it already contains a real, completed scientific conclusion (§8.1) that this spec builds directly on rather than re-deriving from scratch.

## 0. Context: the larger initiative, and a significant discovery

Sub-project #4 of seven. #2 shipped a rest-of-season redraft value and a
deliberately coarse, hand-authored dynasty age-curve multiplier
(`engine/dynasty_curve.py`), explicitly documented as the seam #4 replaces.
#3 shipped a weekly lineup solver that reuses the same VOR engine, untouched.

While brainstorming this sub-project, a genuinely relevant discovery
surfaced: **a real, data-driven age-curve-fitting and durability-estimation
system already exists** (`engine/adjustments.py`, `backtest/harness.py`),
built 2026-08-22 as Tasks 13–14 of the *original* single-league draft-day
tool, entirely before the multi-league initiative began. It has been
completely dormant since — never imported by `vor.py`, `ros_value.py`, or
`weekly_lineup.py` — but it is not abandoned or half-finished. It reached a
real, documented conclusion: an out-of-sample backtest against real
historical Sleeper data (2021–2025, a frozen capture still on disk at
`data/snapshots/2026-08-22-draft-day/`) showed age adjustments made
**redraft** rank predictions *worse* (mean Spearman improvement −0.037,
negative in every one of 3 test seasons) and durability adjustments
**inconclusive** (a marginal +0.003 that satisfied the letter of the
promotion rule but was flagged as likely noise under a weight-sensitivity
sweep). Both `AGE_WEIGHT` and `DURABILITY_WEIGHT` were deliberately left at
`0.0`. This was a real scientific "no," not neglect.

This sub-project's relationship to that prior work, decided during
brainstorming:

- **The age-curve *data* (the fitted per-position, per-age delta-method
  curve) is repurposed for dynasty**, not redraft. The negative result was
  specifically about using age as an *additive points adjustment to boost
  this year's rank prediction* — a different question from "how much
  future production does this player have left," which is what a dynasty
  valuation actually needs. `fit_age_curve`'s underlying statistical
  method (consecutive-season PPG deltas, survivorship-bias-aware) is sound
  regardless of which question it answers; only its *application* changes.
- **Durability gets a second, more rigorous look for redraft specifically**
  — the prior result was inconclusive, not a clean "no," and this
  sub-project re-runs the backtest with a wider weight sweep against a
  refreshed/extended historical snapshot before deciding whether to
  finally wire it live.
- **A real multi-year dynasty computation replaces the old
  `dynasty_curve.multiplier()` single hand-authored ratio**, growing
  `roster_value`'s signature (previously frozen by #2, explicitly
  unfrozen here per the user's choice) to accept per-player history and
  the precomputed age curve.

## 1. Purpose

### 1.1 Goals

- **Real, data-driven dynasty valuation.** Replace `dynasty_curve.py`'s
  hardcoded per-position multiplier with a discounted multi-year annuity
  computed from real historical aging data, each player's own multi-season
  history, and a durability-based survival discount — collapsed back to a
  season-equivalent scale so it slots into the existing VOR pipeline
  exactly like a redraft value does.
- **A more rigorous durability re-backtest for redraft.** Re-run
  `backtest/harness.py` with a wider `DURABILITY_WEIGHT` sweep against a
  refreshed historical snapshot. The outcome (promote or don't) is not
  known in advance — this spec documents the *process*, and the plan
  records the *result* as a ruling once it actually runs.
- **If durability is promoted:** wire it live for the first time —
  `roster_value`'s redraft/keeper branch passes an `adjustments` mapping
  (built from `adjustments.build()`) into `vor.compute(...)`, which
  already accepts one and has simply never been given one.
- **Snapshot refresh tooling.** A script to re-capture
  `players_nfl`/`stats_{season}`/`projections_{season}_CONTAMINATED` from
  live Sleeper into a new dated snapshot directory, so the historical base
  this sub-project (and any future re-validation) depends on isn't frozen
  at a single 2026-08-22 capture forever.
- **A script to fit and check in the age curve** as a static data
  artifact (`domain/constants.py::DYNASTY_AGE_CURVE`), matching the
  `NFL_BYE_WEEKS` precedent — fit once, offline, from whatever snapshot is
  current; the live app only ever loads the checked-in result.
- **Live multi-season history ingest** for dynasty leagues — a new
  Sleeper ingest module fetching each rostered player's real historical
  stat lines on demand, long-TTL cached (finished seasons never change).

### 1.2 Non-goals

- **Contract / cap-space cut risk.** A real improvement flagged during
  brainstorming — modeling the odds a player gets cut for cap reasons
  regardless of on-field performance. No data source for this is
  identified yet. Recorded in §9 for a future sub-project.
- **Team-context modeling.** Whether a WR's own QB situation is
  improving (a young franchise QB) or declining (a QB about to retire,
  signaling a rebuild) meaningfully changes a dynasty asset's outlook.
  Also flagged as a real future improvement, also recorded in §9 — no
  data/model for "team trajectory" exists in this codebase today.
- **Age-dependent injury-risk growth.** The dynasty annuity's survival
  discount uses `expected_games_missed`'s existing single-season estimate
  as a *constant* across the whole multi-year horizon (a player's odds of
  missing games next year are assumed to hold for years 2–5 too), rather
  than modeling injury risk as itself increasing with age. This is a
  known simplification, not validated against data one way or the other
  — recorded in §9.
- **A live-wired durability adjustment is not a guaranteed deliverable.**
  It ships only if the re-backtest (§4.4) actually promotes it. If it
  doesn't, `DURABILITY_WEIGHT` stays `0.0` and no live wiring happens —
  the re-backtest and its documented conclusion are still real
  deliverables of this sub-project either way.
- **Automatic/scheduled snapshot refresh.** `scripts/refresh_snapshot.py`
  is a manually-run tool, not a cron job or background task.
- **Redraft's age handling does not change.** The existing pace-blend
  formula in `ros_value.py`'s redraft/keeper branch is untouched; only
  the dynasty branch's computation changes.
- **Frontend changes.** None. #2's roster panel and power rankings already
  render whatever `roster_value` returns — this sub-project only changes
  how dynasty values are *computed*, not how they're displayed.

### 1.3 Deployment stance

Unchanged: single-user, local-first, no auth. `DYNASTY_AGE_CURVE` is a
checked-in Python constant (not a runtime-fetched artifact). The
historical snapshot directories under `data/snapshots/` remain
git-ignored scratch (matching the existing `2026-08-22-draft-day`
directory's treatment) — reproducing them requires re-running
`scripts/refresh_snapshot.py`.

## 2. Architecture

```
Offline (run manually, not part of any live request):

  scripts/refresh_snapshot.py
    → fetches players_nfl, stats_{2021..current}, projections_{season}_CONTAMINATED
      from live Sleeper, writes data/snapshots/<date>/*.json.gz (same shape
      as the existing 2026-08-22-draft-day capture)

  scripts/fit_age_curve.py <snapshot-dir>
    → loads the snapshot, calls adj.fit_age_curve(...)
    → writes the result into src/ffdo/domain/constants.py as
      DYNASTY_AGE_CURVE: dict[str, dict[int, float]]  (checked in, hand-reviewed before commit)

  backtest/harness.py, extended:
    → sweep_durability_weights(season, weights: list[float]) -> list[dict]
    → run against the refreshed snapshot across more seasons/weights than
      the original Task 14 pass
    → RULING recorded in the plan's ledger: promote DURABILITY_WEIGHT or not

Live (per season-view request, dynasty leagues only):

  GET /api/leagues/{key}/season  (existing route, #2)
    │
    ├─ ... existing #2 wiring (rosters, actuals, projection anchor) ...
    │
    ├─ IF resolved_format == "dynasty":
    │     player_history.fetch(sleeper, all_pids, seasons=range(2021, current_season))
    │       → dict[str, list[SeasonStatLine]]        [_TTLCache, long TTL]
    │     (DYNASTY_AGE_CURVE imported directly, no fetch)
    │
    ├─ ros_value.roster_value(all_pids, league, resolved_format=…, season_proj=…,
    │       profiles=…, actuals=…, weeks_played=…,
    │       history=… (dynasty only, else None),          ← NEW
    │       age_curve=DYNASTY_AGE_CURVE (dynasty only, else None))  ← NEW
    │     redraft/keeper branch: UNCHANGED
    │       [IF durability promoted: adjustments.build(...) → vor.compute(adjustments=...)]
    │     dynasty branch: engine.dynasty_value.annuity_value(...) replaces dynasty_curve.multiplier(...)
    │
    └─ (rest of #2's assembly, unchanged)
```

### 2.1 New/changed domain types

No new frozen dataclasses — `SeasonStatLine` (existing, from Task 13) is
reused as-is for `history`. `domain/constants.py` gains:

```python
# Fit offline by scripts/fit_age_curve.py from data/snapshots/<date>/.
# {position: {age: mean_delta_ppg}} -- mean change in points-per-game from
# age N to age N+1, delta-method (survivorship-bias-aware). See
# engine/adjustments.py::fit_age_curve for the fitting method and
# docs/superpowers/specs/2026-09-12-valuation-model-design.md for why this
# reuses Task 13/14's infrastructure for a different purpose than its
# original (rejected) redraft use.
DYNASTY_AGE_CURVE: Final[dict[str, dict[int, float]]] = { ... }
```

## 3. Ingest layer

### 3.1 `src/ffdo/ingest/sleeper/player_history.py` (new)

```python
def fetch(
    sleeper: SleeperClient, player_ids: Iterable[str], seasons: Iterable[int],
) -> dict[str, list[SeasonStatLine]]:
```

- For each `season` in `seasons`: `GET {V1}/stats/nfl/regular/{season}`,
  parse via the EXISTING `ingest.stats.parse(raw, season)` (Task 13,
  unmodified — it's already a pure, provider-shape-agnostic parser that
  doesn't care whether its input came from a snapshot file or a live
  fetch).
- Sleeper's stats response covers every player league-wide in one call
  (confirmed by Task 13's own snapshot: ~8,229 entries) — not
  player-filtered. `player_ids` filters the PARSED result down to the
  players this call actually needs, not the request itself (one request
  per season regardless of roster size).
- Returns `{player_id: [SeasonStatLine, ...]}`, one entry per season that
  actually has data for that player (a rookie has fewer entries; that's
  fine, `fit_age_curve`/`expected_games_missed` both already tolerate
  sparse/short history).
- **Wired into `app.py` with a long-TTL cache** (`_player_history_cache_for`,
  a `_TTLCache(ttl_seconds=7*24*3600)` — a week; finished-season stats
  never change, unlike everything else this app caches for an hour or a
  day) — one cache entry per season, shared across every league/request
  that needs it, since historical stats are global, not league-scoped.

### 3.2 `scripts/refresh_snapshot.py` (new)

- CLI script: `uv run python scripts/refresh_snapshot.py [--seasons 2021-2026]`.
- Fetches `players_nfl` (`ingest.players.parse` — existing), `stats_{season}`
  for each season in range (`ingest.stats.parse` — existing),
  `projections_{season}_CONTAMINATED` for each PAST season
  (`ingest.projections.parse(..., allow_contaminated=True)` — existing).
- Writes gzipped JSON to `data/snapshots/<today's date>/`, same filenames
  and shape as the existing `2026-08-22-draft-day` capture, so
  `backtest/harness.py`'s `snapshot.load(name, snapshot_dir=...)` (already
  supports an override) works against either directory unmodified.
- Does NOT overwrite or delete the existing `2026-08-22-draft-day`
  snapshot — that capture stays as a stable historical reference; a
  refresh creates a new, separate dated directory.

### 3.3 `scripts/fit_age_curve.py` (new)

- CLI script: `uv run python scripts/fit_age_curve.py <snapshot-dir>`.
- Loads `players_nfl` + `stats_{season}` for every season present in that
  snapshot directory, calls `adj.fit_age_curve(history_by_player,
  profiles, STANDARD_HALF_PPR)` (existing, Task 13, unmodified — already
  takes exactly this shape of input).
- Prints the resulting `dict[str, dict[int, float]]` as a formatted
  Python literal to stdout, for a human to review and paste into
  `domain/constants.py::DYNASTY_AGE_CURVE` by hand (deliberately NOT an
  automatic file-write — a fitted statistical artifact that silently
  overwrites a checked-in constant on every run is a foot-gun; a human
  reviews the numbers before they become load-bearing).

## 4. Engine

### 4.1 `src/ffdo/engine/dynasty_value.py` (new) — replaces `dynasty_curve.py`

```python
HORIZON_YEARS: Final[int] = 5
DISCOUNT_RATE: Final[float] = 0.15  # per year; a common dynasty-community convention,
                                     # NOT backtested (no clean out-of-sample target exists
                                     # for "was this dynasty valuation right" the way
                                     # ADP-vs-actual-points exists for redraft -- see §9).

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
```

`current_season` (the league's own season, e.g. `league.season`) is
required, not defaulted — it feeds `SEASON_LENGTH[current_season]` for
the durability survival fraction below AND is threaded into
`expected_games_missed(..., current_season=current_season)` so its
recency-weighting of past seasons is computed relative to the real
current season rather than silently relying on that function's own
`current_season=2026` default happening to match. (This mirrors
`adjustments.build()`'s own `current_season` threading, which exists
for exactly this reason — see that function's docstring on
`_PROFILE_SNAPSHOT_SEASON` vs. `current_season`.)

Steps (per the brainstorming-approved design):
1. If `profile.age is None`: return `current_full` unchanged (no age data,
   no adjustment possible — matches `adjustments.build()`'s existing
   `if ... and prof.age is not None` guard convention).
2. `survival = 1.0 - (expected_games_missed(history, profile.position, current_season=current_season) / SEASON_LENGTH[current_season])`
   — a single per-position durability estimate (Task 13's existing
   function, unmodified), held CONSTANT across every horizon year (§1.2's
   documented simplification — no age-dependent injury growth).
3. Start the running totals with year 0 itself (`current_full`,
   undiscounted, weight `1.0`) — **not** added on top at the end (see the
   correctness note below): `total_value = current_full`, `total_weight
   = 1.0`. Then for `year in 1..horizon_years`:
   `cumulative_delta += age_curve.get(profile.position, {}).get(profile.age + year, 0.0)`
   `year_value = max(0.0, current_full + cumulative_delta)`
   `weight = survival / (1 + discount_rate) ** year`
   accumulate `total_value += year_value * weight` and
   `total_weight += weight`.
4. Return `total_value / total_weight`
   — a single weighted average across years 0..horizon (year 0 always
   included with weight 1.0), landing back on the same season-points
   scale every other `ValuedPlayer` uses.

   **Correctness note (caught during plan-writing, not just brainstorming
   — worth stating precisely so an implementer doesn't reintroduce it):**
   an earlier draft of this formula computed `current_full +
   (discounted_sum / weight_sum over years 1..horizon)` — i.e. added year
   0 ON TOP of an average of the OTHER years, rather than folding year 0
   INTO the average. That version silently doubles the dynasty value
   whenever `age_curve` has no data for a player's future ages (a very
   real case — an empty/sparse curve is exactly what a player near the
   edge of the training data's age range, or before the curve is ever
   populated, would see): with no curve data, `year_value == current_full`
   for every future year too, so "current_full + average(a bunch of
   current_full's)" collapses to `current_full + current_full`, not
   `current_full`. The corrected step 3/4 above folds year 0 into the SAME
   weighted average as every other year, which means "no curve data at
   all" correctly degrades to `dynasty_value == current_full` (a graceful,
   inert no-op) instead of silently doubling every dynasty value in the
   app. **This is the exact formula to implement — do not use the
   "current_full + average(1..horizon)" version.**
5. A missing `age_curve` entry for a given future age (out of the
   training data's observed range — very young or very old) contributes
   `0.0` to `cumulative_delta` for that year (flat, not declining) —
   same "no data, no adjustment" fallback convention `adjustments.build()`
   already uses; a documented limitation for players near the edges of
   the training data's age range, not a bug.

`dynasty_curve.py` and its test file are deleted.

### 4.2 `src/ffdo/engine/ros_value.py` (modified)

```python
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
    history: Mapping[str, Sequence[SeasonStatLine]] | None = None,     # NEW
    age_curve: Mapping[str, Mapping[int, float]] | None = None,        # NEW
) -> dict[str, ValuedPlayer]:
```

- Redraft/keeper branch: **byte-for-byte unchanged**, ignores `history`/
  `age_curve` entirely.
- Dynasty branch changes from
  `current_full * dynasty_curve.multiplier(profile.position, profile.age, profile.years_exp)`
  to:
  ```python
  value_pts[pid] = dynasty_value.annuity_value(
      current_full, profile, history.get(pid, ()) if history else (),
      age_curve or {}, current_season=league.season)
  ```
- **If durability is promoted (§4.4):** the redraft/keeper branch gains an
  adjustments pass before calling `vor.compute`. `adj.build(...)` needs a
  `replacement_ppg: Mapping[str, float]` (points-PER-GAME by position,
  per `backtest/harness.py`'s existing usage) — `roster_value` doesn't
  have this on hand today; it would need to derive it from
  `engine.replacement.replacement_levels(value_pts, positions, league)`
  (season-TOTAL replacement level per position, already computed
  internally by `vor.compute` but not currently exposed to callers)
  divided by the games remaining in the season. This glue is
  **deliberately left for the plan's implementer to nail down precisely**
  rather than specified as exact code here, since whether this code path
  is even written at all depends entirely on the re-backtest's outcome —
  speccing exact code for a conditional path that may never ship is
  wasted precision. The shape is: build `adjustments` once per call,
  pass it to `vor.compute(value_pts, profiles, league, adjustments=built)`
  in place of the current no-adjustments call. **This entire paragraph is
  conditional** — if durability isn't promoted, `roster_value`'s redraft
  branch has zero changes beyond accepting (and ignoring) the two new
  keyword args.
- `roster_value`'s signature was previously documented as "frozen" in
  #2's spec. This sub-project is the explicitly-planned exception (#2's
  own spec named #4 as the one that replaces this seam's internals) —
  extending rather than replacing the signature keeps every existing
  redraft/keeper call site (the `/season` endpoint, `/lineup`'s
  `weekly_value` — wait, `weekly_lineup.py` doesn't call `roster_value` at
  all, it has its own `weekly_value`, unaffected either way) working with
  the two new params defaulting to `None`.

### 4.3 `src/ffdo/engine/adjustments.py` (unmodified)

No changes. `fit_age_curve`, `expected_games_missed`, `build`,
`AGE_WEIGHT`, `DURABILITY_WEIGHT` are all reused exactly as Task 13 left
them. `AGE_WEIGHT` stays `0.0` permanently (this sub-project doesn't
revisit the redraft age question — see §0) — only `DURABILITY_WEIGHT` is
a live candidate for promotion.

### 4.4 `src/ffdo/backtest/harness.py` (extended) — the durability re-backtest

- New function `sweep_durability_weights(season: int, weights: Sequence[float]) -> list[dict]`
  — calls `evaluate_season(season, age_weight=0.0, durability_weight=w)`
  for each `w`, returning the list of results. (`evaluate_season` itself
  is unmodified — Task 14 already built exactly the right primitive;
  this just runs it across more values.)
- The actual re-backtest run (wider sweep — e.g. `[0.0, 0.25, 0.5, 0.75,
  1.0, 1.5, 2.0, 3.0]` — against whatever seasons the refreshed snapshot
  covers, likely still 2023–2025 plus 2026 partial-season data if
  available by the time this is implemented) happens during plan
  execution, not specified here as a fixed procedure — the plan's
  implementer runs it, and the CONCLUSION (promote a specific weight, or
  confirm `0.0`) is a ledger `Ruling`, exactly like Task 14's original
  conclusion was a documented spec finding rather than a pre-decided
  plan step.
- If promoted, `DURABILITY_WEIGHT` in `adjustments.py` is updated to the
  chosen value (still the ONE line-change Task 13 already anticipated:
  "Promoted above zero only on out-of-sample improvement").

## 5. API (`src/ffdo/api/app.py`)

- New cache: `_player_history_cache_for(season) -> _TTLCache` (7-day TTL,
  keyed by season — mirrors `_weekly_proj_cache_for`'s pattern from #3 but
  with a much longer TTL since finished-season stats are immutable).
- In `_assemble_season` (or wherever the dynasty branch's inputs are
  gathered — read the current file, this function has grown across #2
  and #3's final-review fix waves): **only when
  `lg.resolved_format == "dynasty"`**, fetch
  `player_history.fetch(sleeper, all_pids, seasons=range(2021,
  lg.season))` (each season individually cached) and pass it plus the
  imported `DYNASTY_AGE_CURVE` into `roster_value(...)`. Redraft/keeper
  leagues pass neither (both default `None`, zero extra fetches).
- No response shape changes — `/season`'s JSON contract is identical;
  only the numbers behind a dynasty league's `your_roster.players[].value`
  and the power-ranking values change.
- Error handling: a `player_history.fetch` failure follows the SAME
  `except (httpx.HTTPError, RuntimeError)` → 502 pattern every other
  provider call in this endpoint already uses (this is core, load-bearing
  data for a dynasty league's values — no graceful-degradation case here,
  unlike #3's unofficial schedule endpoint).

## 6. Testing

`uv run pytest` green at every task.

### Engine (`tests/engine/`)

- `test_dynasty_value.py` (new, replaces `test_dynasty_curve.py`) —
  hand-built fixtures: a young player with a positive age-curve delta
  shows annuity value ABOVE `current_full`; an old player with negative
  deltas shows it BELOW; `age is None` returns `current_full` unchanged;
  an empty `age_curve`/missing position returns `current_full` unchanged
  (all deltas fall back to 0.0); a short/empty `history` still produces a
  sane result via `expected_games_missed`'s existing no-history prior
  fallback; the result is always `>= 0` even with strongly negative
  cumulative deltas (the `max(0.0, ...)` floor).
- `test_ros_value.py` (extended) — the dynasty branch with real
  `history`/`age_curve` inputs produces a value that differs from calling
  it with both `None` (proving the new params actually thread through);
  redraft/keeper branch produces IDENTICAL output whether or not
  `history`/`age_curve` are passed (proving the "byte-for-byte unchanged"
  claim, not just asserting it in a docstring).
- `test_adjustments.py` — unmodified (Task 13's tests already cover
  `fit_age_curve`/`expected_games_missed`/`build` as pure functions; this
  sub-project doesn't change their internals).
- `test_harness.py` (extended) — a new test for `sweep_durability_weights`
  proving it returns one result per weight, using the existing real
  snapshot (same pattern as the file's existing real-data tests).

### Ingest (`tests/ingest/sleeper/`)

- `test_player_history.py` (new) — `MockTransport`-based: fetches the
  right seasons, filters to the requested `player_ids`, tolerates a
  player with no history at all (empty list, not a KeyError), parses via
  the real (unmodified) `ingest.stats.parse`.

### API (`tests/api/`)

- `test_season_endpoint.py` (extended) — a dynasty league's `your_roster`
  values differ from the SAME roster/projections in a redraft league
  (extends #2's existing cross-format guard test to prove the NEW dynasty
  computation, not just the OLD `dynasty_curve.multiplier`, is what's
  producing the difference); a redraft league's `/season` response is
  byte-identical whether or not player-history data would be available
  (proving the "redraft skips this fetch entirely" claim — e.g. via a
  recording client that would error if `/stats/` were ever called for a
  redraft league).

### Scripts

No automated tests for `refresh_snapshot.py`/`fit_age_curve.py`
(one-shot manual tools, matching this repo's convention of not testing
`scripts/seed_dev_league.py` either) — manual verification: run
`fit_age_curve.py` against the existing `2026-08-22-draft-day` snapshot
and confirm the printed curve has sane shape (per-position dicts,
plausible delta magnitudes, no NaN/inf).

## 7. Config / repo changes

- New files: `ingest/sleeper/player_history.py`, `engine/dynasty_value.py`,
  `scripts/refresh_snapshot.py`, `scripts/fit_age_curve.py` + their tests.
- Deleted: `engine/dynasty_curve.py`, `tests/engine/test_dynasty_curve.py`.
- Modified: `engine/ros_value.py`, `domain/constants.py` (add
  `DYNASTY_AGE_CURVE`, possibly update `DURABILITY_WEIGHT` pending §4.4's
  outcome), `backtest/harness.py`, `api/app.py`.
- No new runtime dependencies. `backtest/harness.py` already depends on
  `numpy` (existing, from Task 14) — no new dependency for the sweep
  extension.

## 8. Open questions / deferred to plan execution

- **The durability re-backtest's actual result is unknown until the plan
  runs it.** This spec documents the process (§4.4) and both possible
  outcomes' consequences (§4.2's conditional paragraph); the plan's
  implementer records the real conclusion as a ledger `Ruling`.
- **`HORIZON_YEARS=5` / `DISCOUNT_RATE=0.15` are not backtested** — no
  clean out-of-sample target exists for validating a multi-year dynasty
  valuation the way ADP-vs-actual-points validates a redraft one. These
  are documented, reasonable defaults, explicitly open to being revisited
  once real usage or a future sub-project provides a way to check them.

## 9. Future improvements (recorded per user request, not in scope here)

- **Contract / cap-space cut risk** — model the odds a player is cut for
  salary-cap reasons independent of performance. No data source
  identified yet (would likely need an external contracts feed this
  codebase has no ingest path for today).
- **Team-context modeling** — a dynasty asset's outlook depends on more
  than the player alone: an ascending young QB makes his pass-catchers
  more valuable going forward; a QB nearing retirement with no clear
  successor signals a team entering a rebuild. No model or data source
  for "team trajectory" exists in this codebase.
- **Age-dependent injury-risk growth** — the annuity's survival discount
  currently holds a single durability estimate constant across the whole
  horizon; a more realistic model would increase injury risk with
  projected future age, but this needs its own backtest-style validation
  before being trusted (matching this whole sub-project's philosophy of
  not shipping an unvalidated adjustment).
