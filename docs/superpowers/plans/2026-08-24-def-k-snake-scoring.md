# DEF/K Snake Draft Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make DEF and K score correctly (and display correctly) in snake drafts on both Sleeper and ESPN, in both cases using each league's own dynamic `scoring_settings` rather than a hard-coded weight table.

**Architecture:** Extend the existing single `score_stats()` key classifier (`is_offense_scoring_key`) with two siblings, `is_defense_scoring_key` and `is_kicking_scoring_key`, ORed together — no new parameters, no call-site changes (Approach A from the design spec). The VOR/replacement/tier engine needs no changes; it already derives everything from `league.roster_positions` and whatever positions appear in the scored player pool. ESPN's `ESPN_STAT_ID_TO_SLEEPER_KEY` crosswalk gets new entries for the defense/kicking `statId`s present in the real connected league's settings, plus a bug fix: several of those entries carry their real point value in `pointsOverrides["16"]` (ESPN's D/ST-slot override), not the base `points` field the crosswalk currently reads exclusively.

**Tech Stack:** Python 3.12, pytest, vanilla JS/CSS (no build step) for the board frontend.

**Spec:** [docs/superpowers/specs/2026-08-24-def-k-snake-scoring-design.md](../specs/2026-08-24-def-k-snake-scoring-design.md)

## Global Constraints

- Auction (`engine/auction.py`, `OFFENSE_POSITIONS`) is explicitly untouched — DEF/K stay unpriced for auction (spec §1.2).
- DEF points-allowed/yards-allowed brackets (`pts_allow_*`, `yds_allow_*`) are excluded from scoring entirely, not approximated (spec §3.2) — never add these keys to `is_defense_scoring_key` or to the ESPN crosswalk table.
- No position parameter is added to `score_stats()` — classification is by key name alone (spec §3.1, Approach A).
- Every new/changed behavior gets a test before the implementation that makes it pass (TDD, per repo convention — every existing engine/ingest module already follows this).

---

## Task 1: Defense and kicking scoring key classifiers

**Files:**
- Modify: `src/ffdo/domain/constants.py` (add after `is_offense_scoring_key`, currently ending line 29)
- Modify: `src/ffdo/engine/scoring.py` (the `if is_offense_scoring_key(key)` filter, currently line 24)
- Test: `tests/domain/test_models.py` (extend the existing `test_offense_scoring_key_classification` parametrize block, currently lines 35-42)
- Test: `tests/engine/test_scoring.py` (new tests, alongside the existing ones)

**Interfaces:**
- Produces: `is_defense_scoring_key(key: str) -> bool`, `is_kicking_scoring_key(key: str) -> bool` in `ffdo.domain.constants`, same shape as the existing `is_offense_scoring_key`.
- Consumes: nothing new — same `Mapping[str, float]` stats/weights shapes `score_stats()` already uses.

- [ ] **Step 1: Write the failing classifier tests**

Add to `tests/domain/test_models.py`, extending the existing import and parametrize list (do not remove any existing cases):

```python
from ffdo.domain.constants import (
    SEASON_LENGTH, is_defense_scoring_key, is_kicking_scoring_key,
    is_offense_scoring_key,
)
```

```python
@pytest.mark.parametrize("key,expected", [
    ("sack", True), ("int", True), ("fum_rec", True), ("blk_kick", True),
    ("safe", True), ("ff", True), ("def_td", True), ("def_st_td", True),
    ("def_kr_td", True), ("def_pr_td", True), ("def_fum_td", True),
    ("rec_td", False), ("pts_allow_0", False), ("pts_allow_35p", False),
    ("yds_allow_0_100", False), ("fgm_40_49", False),
    # Deliberately excluded even though it's a real defense-adjacent key --
    # ESPN's equivalent category (statId 63) has no D/ST-slot override,
    # meaning it doesn't apply to team defense; keeping it excluded from
    # offense too (already true via `_DEFENSIVE_ONLY`) avoids crediting
    # anyone for it. See Task 3 and constants.py's `_DEFENSIVE_ONLY` comment.
    ("fum_rec_td", False),
])
def test_defense_scoring_key_classification(key, expected):
    assert is_defense_scoring_key(key) is expected


@pytest.mark.parametrize("key,expected", [
    ("fgm_20_29", True), ("fgm_40_49", True), ("fgm_50p", True),
    ("fgm_60p", True), ("fgmiss_50p", True), ("fgm", True), ("fga", True),
    ("fgmiss", True), ("xpm", True), ("xpa", True), ("xpmiss", True),
    ("sack", False), ("rush_yd", False), ("pts_allow_0", False),
])
def test_kicking_scoring_key_classification(key, expected):
    assert is_kicking_scoring_key(key) is expected
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
uv run pytest tests/domain/test_models.py -k "defense_scoring_key or kicking_scoring_key" -v
```
Expected: FAIL with `ImportError` (`is_defense_scoring_key` doesn't exist yet).

- [ ] **Step 3: Implement the classifiers**

In `src/ffdo/domain/constants.py`, immediately after `is_offense_scoring_key` (after the current line 29, before the `STANDARD_HALF_PPR` block):

```python
# Points-allowed and yards-allowed brackets (`pts_allow_*`, `yds_allow_*`)
# are deliberately NOT recognized here. Sleeper's season *projections* for
# these brackets look like placeholder noise rather than a real weekly
# forecast -- e.g. the top-projected 2026 DEF unit shows real turnover
# projections (52 sacks, 15 INTs) alongside `pts_allow_0: 1.0` and `gp:
# 1.0`, inconsistent with a genuine per-week bracket forecast for a
# projected starter. A league's points/yards-allowed scoring weights go
# unused for projection-based valuation as a result -- excluded rather
# than guessed wrong, the same philosophy `vor.compute` already applies to
# a position with no replacement level. See design doc §3.2.
_DEFENSE_BARE: Final[frozenset[str]] = frozenset({
    "sack", "int", "fum_rec", "blk_kick", "safe", "ff",
    "def_td", "def_st_td", "def_kr_td", "def_pr_td", "def_fum_td",
})


def is_defense_scoring_key(key: str) -> bool:
    """True if `key` scores for a team-defense (`DEF`) player."""
    return key in _DEFENSE_BARE


_KICKING_PREFIXES: Final[tuple[str, ...]] = ("fgm_", "fgmiss_")
_KICKING_BARE: Final[frozenset[str]] = frozenset({
    "fgm", "fga", "fgmiss", "xpm", "xpa", "xpmiss",
})


def is_kicking_scoring_key(key: str) -> bool:
    """True if `key` scores for a kicker (`K`) player.

    Prefix-matching `fgm_`/`fgmiss_` sweeps in every distance bracket
    (`fgm_20_29`, `fgm_50p`, `fgm_60p`, ...) plus a few non-scoring
    magnitude fields (`fgm_yds`, `fgm_lng`, `fgm_pct`) that happen to share
    the prefix. That over-match is harmless: `score_stats` only sums keys a
    league's `scoring_settings` actually assigns a weight to, and no real
    league scores kicking by total FG yardage or long-FG distance rather
    than by make/miss count -- so those extra keys are never weighted in
    practice.
    """
    return key.startswith(_KICKING_PREFIXES) or key in _KICKING_BARE
```

- [ ] **Step 4: Run the classifier tests to verify they pass**

```bash
uv run pytest tests/domain/test_models.py -k "defense_scoring_key or kicking_scoring_key" -v
```
Expected: PASS, both parametrized blocks, all cases.

- [ ] **Step 5: Write the failing `score_stats` tests**

Add to `tests/engine/test_scoring.py`:

```python
def test_score_stats_credits_defensive_categories():
    """A DEF player's turnover/sack production must now score, unlike
    today where score_stats returns 0.0 for every non-offense key."""
    got = scoring.score_stats(
        {"sack": 3, "int": 2, "fum_rec": 1},
        {"sack": 1.0, "int": 2.0, "fum_rec": 2.0},
    )
    assert got == 3 * 1.0 + 2 * 2.0 + 1 * 2.0


def test_score_stats_credits_kicking_categories():
    got = scoring.score_stats(
        {"fgm_40_49": 2, "fgm_50p": 1, "xpm": 3, "fgmiss": 1},
        {"fgm_40_49": 4.0, "fgm_50p": 5.0, "xpm": 1.0, "fgmiss": -1.0},
    )
    assert got == 2 * 4.0 + 1 * 5.0 + 3 * 1.0 + 1 * -1.0


def test_score_stats_still_excludes_points_allowed():
    """Points-allowed weights are configured but must have no effect --
    see design doc §3.2 and the `_DEFENSE_BARE` comment in constants.py."""
    got = scoring.score_stats(
        {"sack": 1, "pts_allow_0": 1},
        {"sack": 1.0, "pts_allow_0": 5.0},
    )
    assert got == 1.0
```

Also confirm (do not edit) that the existing `test_score_stats_ignores_defensive_keys_for_offense` in the same file still encodes the required behavior: `fum_rec_td` must stay excluded even though it's a real defense-adjacent stat, because it's not in `_DEFENSE_BARE` (see Step 3's comment and Task 3 for why).

- [ ] **Step 6: Run the new `score_stats` tests to verify they fail**

```bash
uv run pytest tests/engine/test_scoring.py -k "defensive_categories or kicking_categories or excludes_points_allowed" -v
```
Expected: FAIL (all three assert `0.0 == <nonzero>`, since `score_stats` doesn't recognize these keys yet).

- [ ] **Step 7: Update `score_stats` to recognize all three classifiers**

In `src/ffdo/engine/scoring.py`, change:

```python
from ffdo.domain.constants import is_offense_scoring_key


def score_stats(stats: Mapping[str, float], weights: Mapping[str, float]) -> float:
    """Total offensive fantasy points for `stats` under `weights`.

    Scoring keys that only apply to defensive or kicking units are ignored,
    so a league's DEF/K rules never leak into a skill player's total.
    """
    return sum(
        float(stats.get(key, 0.0)) * float(weight)
        for key, weight in weights.items()
        if is_offense_scoring_key(key)
    )
```

to:

```python
from ffdo.domain.constants import (
    is_defense_scoring_key, is_kicking_scoring_key, is_offense_scoring_key,
)


def score_stats(stats: Mapping[str, float], weights: Mapping[str, float]) -> float:
    """Total fantasy points for `stats` under `weights`, across offense,
    defense, and kicking scoring categories.

    A key is summed if any of the three classifiers recognizes it. This is
    safe with no position awareness because Sleeper's raw stat dicts are
    inherently position-specific -- a WR's stats never contain `sack`, a
    DEF's never contain `rec_yd` -- so the same key can never fire for two
    different positions' players.
    """
    return sum(
        float(stats.get(key, 0.0)) * float(weight)
        for key, weight in weights.items()
        if is_offense_scoring_key(key)
        or is_defense_scoring_key(key)
        or is_kicking_scoring_key(key)
    )
```

- [ ] **Step 8: Run the full scoring test file to verify everything passes**

```bash
uv run pytest tests/engine/test_scoring.py -v
```
Expected: PASS, all tests including the pre-existing `test_golden_reproduces_sleeper_half_ppr_2025`, `test_league_scoring_diverges_from_preset_on_fumbles`, and `test_score_stats_ignores_defensive_keys_for_offense` (unchanged, still green).

- [ ] **Step 9: Commit**

```bash
git add src/ffdo/domain/constants.py src/ffdo/engine/scoring.py tests/domain/test_models.py tests/engine/test_scoring.py
git commit -m "Recognize defense and kicking scoring keys in score_stats"
```

---

## Task 2: Prove the VOR/replacement pipeline picks up DEF/K unchanged

**Files:**
- Test: `tests/engine/test_vor.py` (new test, alongside the existing ones)

**Interfaces:**
- Consumes: `vor.compute`, `vor.assign_tiers` (existing, unchanged signatures), `LeagueProfile`, `PlayerProfile` (existing, unchanged).
- Produces: nothing new — this task is a regression/coverage test proving no engine code changes are needed here, per spec §1.1's claim.

- [ ] **Step 1: Write the failing test**

Add to `tests/engine/test_vor.py`:

```python
def test_def_and_k_get_position_differentiated_vor():
    """No engine change should be needed for DEF/K -- replacement.py and
    vor.py already derive everything from league.roster_positions and
    whatever positions appear in the scored pool. This proves it."""
    points = {
        "def0": 140.0, "def1": 110.0, "def2": 90.0,
        "k0": 130.0, "k1": 100.0, "k2": 80.0,
    }
    profiles = _profiles({
        "def0": "DEF", "def1": "DEF", "def2": "DEF",
        "k0": "K", "k1": "K", "k2": "K",
    })
    league = LeagueProfile(
        league_id="x", season=2026, num_teams=2,
        roster_positions=("DEF", "K", "BN"),
        scoring_settings={}, budget=200,
    )
    valued = vor.compute(points, profiles, league)

    # 2 teams x 1 DEF slot => replacement DEF is the 3rd best (90.0)
    assert valued["def0"].vor == 140.0 - 90.0
    # 2 teams x 1 K slot => replacement K is the 3rd best (80.0)
    assert valued["k0"].vor == 130.0 - 80.0
    # Replacement levels are computed independently per position -- a DEF's
    # VOR must not be measured against the K replacement level or vice versa.
    assert valued["def0"].vor != valued["k0"].vor - (130.0 - 90.0)
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest tests/engine/test_vor.py -k def_and_k_get_position_differentiated_vor -v
```
Expected: FAIL — `KeyError: 'def0'` (today, `DEF`/`K` positions are absent from `levels` for no structural reason other than never having been exercised, so first confirm this actually is the failure mode, not a pass; see Step 3).

- [ ] **Step 3: Investigate before "implementing"**

This is the one step in the plan where the expected outcome is the test passing with **zero production code changes** — the spec's claim (§1.1) is that `replacement.py`/`vor.py` are already position-agnostic. If Step 2's run instead fails with an assertion mismatch (not a `KeyError`) or passes immediately, that confirms the claim; if it fails for a structural reason (e.g. a hard-coded position list somewhere in `replacement.py` or `vor.py`), stop and re-read `engine/replacement.py` and `engine/vor.py` in full before writing any fix — that would be new information contradicting the spec, not something to patch blindly.

- [ ] **Step 4: Run it again to confirm it passes with no source changes**

```bash
uv run pytest tests/engine/test_vor.py -v
```
Expected: PASS, all tests including the new one, with `engine/replacement.py` and `engine/vor.py` untouched.

- [ ] **Step 5: Commit**

```bash
git add tests/engine/test_vor.py
git commit -m "Add coverage proving VOR/replacement is position-agnostic for DEF/K"
```

---

## Task 3: ESPN defense/kicking scoring crosswalk

**Files:**
- Modify: `src/ffdo/ingest/espn/league.py` (`ESPN_STAT_ID_TO_SLEEPER_KEY`, currently lines 25-30; `_scoring_settings`, currently lines 48-54)
- Test: `tests/ingest/espn/test_league.py` (extend `test_parse_builds_scoring_settings_matching_the_real_scoring_items`, currently lines 30-37; add new tests)

**Interfaces:**
- Consumes: the real captured fixture `data/snapshots/2026-08-23-espn-league/mSettings.json.gz` (already committed), via `tests/ingest/espn/test_league.py`'s existing `_msettings()` helper.
- Produces: `ESPN_STAT_ID_TO_SLEEPER_KEY` gains new entries; `league.parse(...).scoring_settings` gains the corresponding keys for the real connected league.

**Important finding from design-time fixture inspection:** several defense/kicking `statId` entries in the real fixture carry `points: 0.0` with the actual weight in `pointsOverrides: {"16": <value>}` — e.g. `statId 95` (defensive interceptions) is `{"points": 0.0, "pointsOverrides": {"16": 2.0}}`. `_scoring_settings()` currently reads only `item["points"]`, so without a fix these categories would silently parse to a weight of **0.0** — not just unmapped, actively wrong. This must be fixed as part of this task, not just the table extended.

- [ ] **Step 1: Write the failing scoring-settings test**

Replace the existing test body in `tests/ingest/espn/test_league.py` (currently lines 30-37) with the extended expected dict:

```python
def test_parse_builds_scoring_settings_matching_the_real_scoring_items():
    lg = league.parse(_msettings())
    assert lg.scoring_settings == {
        "pass_yd": 0.04, "pass_td": 4.0, "pass_int": -2.0,
        "rush_yd": 0.1, "rush_td": 6.0,
        "rec_yd": 0.1, "rec_td": 6.0, "rec": 1.0,
        "fum_lost": -2.0,
        "fgm_40_49": 4.0, "fgm_20_29": 3.0, "fgm_60p": 6.0,
        "fgmiss": -1.0, "xpm": 1.0,
        "sack": 1.0, "int": 2.0, "fum_rec": 2.0, "blk_kick": 2.0, "safe": 1.0,
        "def_kr_td": 6.0, "def_pr_td": 6.0, "def_td": 6.0, "def_fum_td": 6.0,
    }
```

Also add a dedicated regression test for the `pointsOverrides` bug, right after it:

```python
def test_scoring_settings_prefer_the_dst_slot_override_over_base_points():
    """statId 95 (defensive interceptions) is {"points": 0.0,
    "pointsOverrides": {"16": 2.0}} in the real fixture -- reading only
    `points` (as the code did before this test was added) silently
    produces a weight of 0.0, not just an unmapped key."""
    lg = league.parse(_msettings())
    assert lg.scoring_settings["int"] == 2.0
    assert lg.scoring_settings["sack"] == 1.0
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/ingest/espn/test_league.py -k "scoring_settings" -v
```
Expected: FAIL — the first test's actual dict is missing every new key; the second raises `KeyError: 'int'`.

- [ ] **Step 3: Fix `_scoring_settings` to prefer the D/ST-slot override, and extend the crosswalk table**

In `src/ffdo/ingest/espn/league.py`, replace the `ESPN_STAT_ID_TO_SLEEPER_KEY` block (currently lines 19-30) with:

```python
# Verified live 2026-08-23 against a real league's
# settings.scoringSettings.scoringItems.
#
# Several defense/kicking categories carry their real weight in
# `pointsOverrides["16"]` (ESPN's D/ST lineup-slot id) rather than the
# base `points` field -- `_scoring_settings` below reads the override when
# present. Categories investigated and deliberately left unmapped:
#   - statId 63 (fumbleRecoveredForTD): no D/ST-slot override in the real
#     fixture, meaning it doesn't apply to team defense; Sleeper's
#     `fum_rec_td` key is already excluded from offense scoring too (see
#     `domain.constants._DEFENSIVE_ONLY`), so mapping it here would credit
#     no one and risk colliding with a future offense-side use of the key.
#   - statId 93 (defensiveBlockedKickForTouchdowns): would collide with
#     statId 103's `def_td` mapping below (both score 6.0 in the real
#     league) with no distinct Sleeper key confirmed for this narrower
#     category -- left out rather than silently overwritten or guessed.
#   - statId 198, 209: not identified against the community-documented
#     ESPN stat ID reference (github.com/cwendt94/espn-api); worth
#     revisiting if a golden-test comparison against a real graded week
#     shows a material gap for this league.
#   - statId 19, 26, 44 (2pt conversions), 206 (defensive 2pt return): a
#     pre-existing offense-scoring gap (19/26/44) and a niche category with
#     no confirmed Sleeper-vocabulary equivalent (206) -- out of scope for
#     this DEF/K feature.
#   - Every points-allowed/yards-allowed bracket statId (89-92, 121-136):
#     deliberately excluded, matching `is_defense_scoring_key`'s exclusion
#     of `pts_allow_*`/`yds_allow_*` -- see design doc §3.2.
ESPN_STAT_ID_TO_SLEEPER_KEY: dict[int, str] = {
    3: "pass_yd", 4: "pass_td", 20: "pass_int",
    24: "rush_yd", 25: "rush_td",
    42: "rec_yd", 43: "rec_td", 53: "rec",
    72: "fum_lost",
    # Kicking
    77: "fgm_40_49",
    # ESPN's "under 40" bucket doesn't distinguish 20-29 from 30-39 yards;
    # this crosswalk maps it to fgm_20_29 only, so a real 30-39 yard make
    # is under-counted. Known simplification -- revisit if a golden-test
    # comparison against a real graded week shows this matters.
    80: "fgm_20_29",
    201: "fgm_60p",
    85: "fgmiss", 86: "xpm",
    # Defense
    95: "int", 96: "fum_rec", 97: "blk_kick", 98: "safe", 99: "sack",
    101: "def_kr_td", 102: "def_pr_td", 103: "def_td", 104: "def_fum_td",
}
```

Then replace `_scoring_settings` (currently lines 48-54):

```python
def _scoring_settings(scoring_items: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in scoring_items:
        key = ESPN_STAT_ID_TO_SLEEPER_KEY.get(item["statId"])
        if key is not None:
            # The D/ST lineup slot (16) can carry a different weight than
            # the base `points` field via `pointsOverrides` -- e.g. a real
            # defensive-interception entry is `{"points": 0.0,
            # "pointsOverrides": {"16": 2.0}}`. Prefer the override when
            # present; it's how ESPN encodes "this category scores
            # differently (or only) for a team defense."
            override = item.get("pointsOverrides", {}).get("16")
            out[key] = float(override if override is not None else item["points"])
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/ingest/espn/test_league.py -v
```
Expected: PASS, all tests in the file, including `test_parse_builds_roster_positions_matching_the_real_lineup_slot_counts` and `test_roster_positions_raises_on_an_unmapped_nonzero_slot` (unaffected by this change).

- [ ] **Step 5: Write a sanity test using real Sleeper actuals through the parsed ESPN league's scoring**

This is explicitly a sanity/plausibility check, not a golden/exact-reproduction test — there's no captured ESPN box-score fixture giving an independent ground-truth point total to reproduce (unlike the offense `pts_half_ppr` golden test in `test_scoring.py`, which has one). It catches gross errors (wrong sign, a multiplier bug) without claiming false precision.

Add two imports to the top of `tests/ingest/espn/test_league.py`, alongside the existing `from ffdo.ingest import snapshot` / `from ffdo.ingest.espn import league`:

```python
from ffdo.engine import scoring
from ffdo.ingest import players, stats
```

Then add the test itself. `snapshot.load("players_nfl")` and `snapshot.load("stats_2025")` read from the *default* snapshot dir (`data/snapshots/2026-08-22-draft-day`), not `ESPN_SNAPSHOT_DIR` — that's correct here, since real Sleeper actuals are what's being fed through the ESPN league's parsed scoring settings; only `_msettings()` needs the ESPN-specific directory:

```python
def test_real_league_scoring_produces_plausible_def_and_k_totals():
    """Sanity check, not a golden test -- no independent ESPN point total
    exists in our fixtures to reproduce exactly. Denver's real 2025 season
    (68 sacks, 10 INTs, 3 fumble recoveries, 1 blocked kick, 1 defensive
    TD -- points-allowed excluded per design doc §3.2) and Brandon
    Aubrey's real 2025 season (36/42 field goals, 47/48 extra points)
    should both land as a clearly positive, plausible season total under
    this real league's parsed scoring, not zero or a wildly implausible
    number."""
    lg = league.parse(_msettings())
    profiles = players.parse(snapshot.load("players_nfl"))
    lines = stats.parse(snapshot.load("stats_2025"), 2025)

    den_pts = scoring.score_stats(lines["DEN"].stats, lg.scoring_settings)
    assert 20.0 <= den_pts <= 250.0

    aubrey_id = next(
        pid for pid, p in profiles.items()
        if p.position == "K" and p.first_name == "Brandon" and p.last_name == "Aubrey"
    )
    k_pts = scoring.score_stats(lines[aubrey_id].stats, lg.scoring_settings)
    assert 80.0 <= k_pts <= 250.0
```

- [ ] **Step 6: Run it to verify it fails first, then passes**

```bash
uv run pytest tests/ingest/espn/test_league.py -k plausible_def_and_k -v
```
Expected: FAIL before Step 3's fix is in place (or if run standalone against a clean checkout), PASS after. Since Steps 3-4 already landed the fix, this should already PASS on first run here — if it doesn't, treat that as a real bug to investigate (e.g. `lines["DEN"]` missing, or a points-allowed leak), not something to loosen the bounds around.

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/ingest/espn/league.py tests/ingest/espn/test_league.py
git commit -m "Add ESPN defense/kicking scoring crosswalk, fix D/ST pointsOverrides read"
```

---

## Task 4: Frontend display — filters, row colors, roster summary

**Files:**
- Modify: `src/ffdo/web/board/index.html` (filter buttons, currently lines 127-131)
- Modify: `src/ffdo/web/board/board.css` (color variables, currently lines 15-18; row-color rules, currently lines 281-284)
- Modify: `src/ffdo/web/board/board.js` (`renderRosters`, `posCells`, currently line 384)

**Interfaces:**
- Consumes: `by_position` payload keys from the board API (already includes any position present in a team's `TeamLineup.by_position`, per `engine/roster.py:team_lineup` — no backend change needed for this task, it's purely about what the frontend chooses to render).

No automated test infrastructure exists in this repo for `board.js`/`index.html`/`board.css` (confirmed: no `tests/web` directory, no JS test runner in `pyproject.toml`). This task is verified manually in Task 5, per `superpowers:verification-before-completion` discipline — there is no faster, equally-honest substitute for actually looking at the running board.

- [ ] **Step 1: Add DEF/K filter buttons**

In `src/ffdo/web/board/index.html`, change (currently lines 127-131):

```html
<button data-pos="ALL" class="on">All</button>
<button data-pos="QB">QB</button>
<button data-pos="RB">RB</button>
<button data-pos="WR">WR</button>
<button data-pos="TE">TE</button>
```

to:

```html
<button data-pos="ALL" class="on">All</button>
<button data-pos="QB">QB</button>
<button data-pos="RB">RB</button>
<button data-pos="WR">WR</button>
<button data-pos="TE">TE</button>
<button data-pos="DEF">DEF</button>
<button data-pos="K">K</button>
```

No JS change needed here — `board.js`'s filter binding (`document.querySelectorAll("#filters button[data-pos]")`) is already generic over whatever buttons exist in the DOM.

- [ ] **Step 2: Add DEF/K color variables and row-color rules**

In `src/ffdo/web/board/board.css`, change (currently lines 15-18):

```css
  --qb: #818CF8;
  --rb: #34D399;
  --wr: #22D3EE;
  --te: #FBBF24;
```

to:

```css
  --qb: #818CF8;
  --rb: #34D399;
  --wr: #22D3EE;
  --te: #FBBF24;
  --def: #F472B6;
  --k: #A78BFA;
```

And change (currently lines 281-284):

```css
tr.pos-QB td.pos-cell { color: var(--qb); }
tr.pos-RB td.pos-cell { color: var(--rb); }
tr.pos-WR td.pos-cell { color: var(--wr); }
tr.pos-TE td.pos-cell { color: var(--te); }
```

to:

```css
tr.pos-QB td.pos-cell { color: var(--qb); }
tr.pos-RB td.pos-cell { color: var(--rb); }
tr.pos-WR td.pos-cell { color: var(--wr); }
tr.pos-TE td.pos-cell { color: var(--te); }
tr.pos-DEF td.pos-cell { color: var(--def); }
tr.pos-K td.pos-cell { color: var(--k); }
```

- [ ] **Step 3: Extend the live roster-summary cells**

In `src/ffdo/web/board/board.js`, `renderRosters()`, change (currently line 384):

```javascript
const posCells = ["QB", "RB", "WR", "TE"].map(pos => {
```

to:

```javascript
const posCells = ["QB", "RB", "WR", "TE", "DEF", "K"].map(pos => {
```

The per-cell template two lines below already reads `var(--${pos.toLowerCase()}, var(--muted))` and handles a missing `by_position` entry with `v !== undefined ? ... : "—"` — no other change needed in this function.

- [ ] **Step 4: Commit**

```bash
git add src/ffdo/web/board/index.html src/ffdo/web/board/board.css src/ffdo/web/board/board.js
git commit -m "Show DEF/K in the board filters, row colors, and roster summary"
```

---

## Task 5: End-to-end manual verification

**Files:** none (verification only, per `superpowers:verification-before-completion` — passing tests confirm scoring/plumbing correctness, not that the feature is visible and usable in the real app).

- [ ] **Step 1: Run the full test suite**

```bash
uv run pytest
```
Expected: PASS, no failures, no skips introduced by this change.

- [ ] **Step 2: Start the server against a snake league that rosters DEF/K**

```bash
FFDO_LEAGUE_ID=1882997948 FFDO_DRAFT_ID=1882997948 uv run uvicorn ffdo.api.app:app --port 8000
```

(The ESPN test league's `league_id`/`draft_id` are the same value, `1882997948`, per the connected-league memory from the original ESPN support work. Substitute your real Sleeper snake league/draft ID instead if you'd rather verify that provider first — both should work identically since the backend change is provider-agnostic once `LeagueProfile.scoring_settings` is populated correctly.)

- [ ] **Step 3: Open the board and verify DEF/K end-to-end**

Open `http://localhost:8000` in a browser and confirm:
- The `DEF` and `K` filter buttons appear in the toolbar and, when clicked, filter the table to only those positions.
- DEF/K rows in the main table are colored (pink/violet, per Step 2 of Task 4) rather than the default muted text color.
- DEF/K players have nonzero `VOR`/`Tier`/`Fair $` values, not all zeros or blanks.
- If any roster has drafted a DEF or K, the live roster-summary panel shows a DEF and/or K value in that team's row, not a blank space where QB/RB/WR/TE currently stop.

- [ ] **Step 4: Note the result**

If Step 3 surfaces a real gap (e.g. a fourth hard-coded position list not found during design, or a visibly wrong DEF/K value), stop and fix it with its own test before considering this plan complete — don't note it as a known limitation unless it's the already-documented points-allowed exclusion (spec §3.2).
