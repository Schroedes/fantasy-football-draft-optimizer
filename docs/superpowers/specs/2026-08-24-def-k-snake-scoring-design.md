# DEF/K Snake Draft Scoring — Design

**Date:** 2026-08-24
**Status:** Approved, pending implementation
**Prior art:** `src/ffdo/engine/scoring.py`, `src/ffdo/domain/constants.py`
(`is_offense_scoring_key`, `STANDARD_HALF_PPR`); ESPN scoring crosswalk in
`src/ffdo/ingest/espn/league.py` (`ESPN_STAT_ID_TO_SLEEPER_KEY`), whose
docstring already flags defense/kicking categories as deferred follow-up
from the original ESPN league support work.

## 1. Purpose

DEF and K are currently invisible to valuation in both snake pipelines
(Sleeper and ESPN). `score_stats()` only recognizes offensive component
stats (`is_offense_scoring_key`), so every DEF/K player scores exactly 0
points regardless of a league's `scoring_settings` — they're technically
present in the player pool (Sleeper's projections endpoint returns them,
ESPN's roster-slot and player-ID crosswalks already handle them), but VOR,
tiers, and lineup value all treat them as worthless. The board's frontend
compounds this: position filter buttons, row-color CSS, and the live
roster-summary cells are hard-coded to QB/RB/WR/TE, so even a nonzero VOR
wouldn't render correctly today.

This feature makes DEF/K score and display correctly for **snake drafts on
both providers** — the two leagues you actually play in roster both
positions with customized scoring, so `scoring_settings` must stay fully
dynamic (already true architecturally; no hard-coded weight table).

### 1.1 Goals

- `score_stats()` recognizes standard defensive and kicking component stats,
  in addition to offense, so a league's real `scoring_settings` weights for
  those categories actually apply.
- ESPN's `ESPN_STAT_ID_TO_SLEEPER_KEY` crosswalk covers the defense/kicking
  `statId` entries needed to translate a real ESPN league's scoring
  settings into the same key vocabulary Sleeper uses.
- The board UI (filter buttons, row coloring, live roster-summary cells)
  displays DEF/K the same way it displays offensive positions.
- DEF/K flow through VOR, replacement level, tiers, and lineup value with
  **no engine changes** beyond scoring — `replacement.py`, `roster.py`, and
  `vor.py` are already position-agnostic (they derive everything from
  `league.roster_positions` and whatever positions appear in the scored
  player pool), so this is additive there, not a rewrite.

### 1.2 Non-goals

- **Auction.** `engine/auction.py`'s `OFFENSE_POSITIONS` constant, and
  everything gated on it (positional budgeting, nomination roster-fit),
  stays untouched. DEF/K remain unpriced for auction.
- **Points-allowed scoring for DEF.** See §3.2 — excluded, not approximated.
- **Per-position miss-rate tuning** in `engine/adjustments.py`. DEF/K fall
  through to the existing `_DEFAULT_MISS_RATE` fallback (0.15) like any
  other position without a dedicated entry. Nothing here prevents adding
  dedicated DEF/K miss rates later; it's just not part of this change.
- **IDP (individual defensive player) scoring.** Out of scope; team defense
  (`DEF`) is a single roster entry per team, unrelated to IDP leagues.

## 2. Current behavior (for contrast)

`engine/scoring.py`:

```python
def score_stats(stats, weights):
    return sum(
        float(stats.get(key, 0.0)) * float(weight)
        for key, weight in weights.items()
        if is_offense_scoring_key(key)
    )
```

`is_offense_scoring_key` (`domain/constants.py`) recognizes only offense
prefixes (`pass_`, `rush_`, `rec_`, `bonus_`) and a small bare-key allowlist
(`rec`, `fum`, `fum_lost`, `st_td`). Any defensive or kicking key — `sack`,
`int`, `fgm_40_49`, `xpm`, etc. — is silently excluded, so a DEF/K player's
`adjusted_points` is always `0.0` regardless of what the league's
`scoring_settings` actually weight those keys at.

`ESPN_STAT_ID_TO_SLEEPER_KEY` maps 8 offense `statId`s; the ~35 remaining
entries in ESPN's `scoringItems` (defense/kicking categories, several using
a `pointsOverrides` dict keyed to lineup slot `"16"`, ESPN's D/ST slot) are
unmapped, so `_scoring_settings()` drops them from the parsed
`LeagueProfile.scoring_settings` entirely.

`board.js`/`index.html` hard-code `["QB", "RB", "WR", "TE"]` in three
places: the toolbar filter buttons, the live roster-summary `by_position`
cells, and (transitively, via `board.css`) row coloring keyed to a
`--{pos}` CSS variable per position.

## 3. Backend: scoring key classification

### 3.1 New classifiers

`domain/constants.py` gains two new functions alongside
`is_offense_scoring_key`, same shape:

```python
_KICKING_PREFIXES: Final[tuple[str, ...]] = ("fgm_", "fgmiss_")
_KICKING_BARE: Final[frozenset[str]] = frozenset({"fgm", "fga", "fgmiss", "xpm", "xpa", "xpmiss"})

def is_kicking_scoring_key(key: str) -> bool:
    return key.startswith(_KICKING_PREFIXES) or key in _KICKING_BARE
```

```python
_DEFENSE_BARE: Final[frozenset[str]] = frozenset({
    "sack", "int", "fum_rec", "blk_kick", "safe",
    "def_td", "def_st_td", "def_kr_td", "def_pr_td", "def_fum_td",
})

def is_defense_scoring_key(key: str) -> bool:
    return key in _DEFENSE_BARE
```

`score_stats()` ORs all three:

```python
if is_offense_scoring_key(key) or is_defense_scoring_key(key) or is_kicking_scoring_key(key):
```

The exact bare-key sets above are a starting point from a real 2025/2026
Sleeper data sample pulled during design, **not final** — implementation
pins them down the same way `STANDARD_HALF_PPR` was verified: recompute
`score_stats()` against Sleeper's own precomputed `pts_half_ppr` for real
2025 DEF and K stat lines and adjust the key sets until it reproduces
those totals (golden test, §6). In particular, forced-fumble credit
appears under more than one key name in the raw data (`ff` vs.
`def_st_ff`) and only one is the one Sleeper's own scoring actually uses —
that ambiguity gets resolved empirically, not guessed here.

No position parameter is added (per Approach A, approved) — safety comes
from the fact that a WR's stat dict never contains `sack`, and a DEF's
never contains `rec_yd`; the three classifiers can never fire on the same
key for the same player, the same reasoning `score_stats`'s existing
docstring already gives for offense/defense-only keys like `fum_rec`.

### 3.2 Points-allowed: excluded, not approximated

Sleeper's season *projections* for points-allowed brackets
(`pts_allow_0`, `pts_allow_1_6`, ...) look like placeholder noise rather
than a real weekly distribution — e.g. the top-projected 2026 DEF shows
real turnover projections (52 sacks, 15 INTs) alongside `pts_allow_0: 1.0`
and `gp: 1.0`, inconsistent with a genuine per-week bracket forecast for a
projected starter. `is_defense_scoring_key` deliberately does not
recognize any `pts_allow_*` or `yds_allow_*` key, so a league's
points-allowed weights go unused for projection-based valuation — the same
"exclude rather than guess wrong" philosophy `vor.compute` already applies
to positions with no replacement level. This gets a code comment at the
classifier, not just this doc, so a future reader isn't left wondering why
a configured scoring weight has no effect.

Actual-stats-based features (if any exist later, e.g. backtest or grading
against realized points) are unaffected by this decision and could use
`pts_allow_*` directly if needed — this exclusion is specific to
projection-based scoring, where the input data doesn't support it.

## 4. ESPN scoring crosswalk

`ingest/espn/league.py`'s `ESPN_STAT_ID_TO_SLEEPER_KEY` gets the
defense/kicking `statId` entries added, resolving the deferred work its own
docstring already points at. This requires live verification against your
real ESPN league's `mSettings.scoringSettings.scoringItems` during
implementation — decoding which `statId` values correspond to which
Sleeper-vocabulary key, including the `pointsOverrides`-keyed entries for
categories that score differently for D/ST than for an individual (ESPN's
mechanism, not Sleeper's). No table is guessed in this doc; implementation
fetches the real settings the same way the original crosswalk work did
(§9 of the ESPN design doc's fixture-capture approach).

`_scoring_settings()` itself needs no logic change — it already does a
generic `statId -> key` lookup and drops anything unmapped; adding entries
to the table is the entire change.

## 5. Frontend display

`src/ffdo/web/board/index.html` (filters nav, currently lines 127-131):

```html
<button data-pos="DEF">DEF</button>
<button data-pos="K">K</button>
```

No JS change needed for the filter itself — `board.js:483` already binds
via `document.querySelectorAll("#filters button[data-pos]")`, generic over
whatever buttons exist.

`src/ffdo/web/board/board.css` (currently lines 15-18, 281-284): add
`--def` and `--k` color variables, and matching `tr.pos-DEF` / `tr.pos-K`
row-color rules, following the existing `--qb`/`--rb`/`--wr`/`--te` pattern
exactly.

`src/ffdo/web/board/board.js`, `renderRosters()` (currently line 384): the
`posCells` position list `["QB", "RB", "WR", "TE"]` becomes
`["QB", "RB", "WR", "TE", "DEF", "K"]`. The per-cell template already
handles a missing/undefined `by_position` entry gracefully (`v !== undefined
? ... : "—"`), so a roster with no DEF/K drafted yet renders `—` with no
special-casing.

`renderPositionBudget()` (auction-only, gated on `d.format !== "snake"`) is
explicitly untouched — §1.2.

## 6. Testing

Golden tests, same pattern as `STANDARD_HALF_PPR`'s existing verification:

1. `tests/domain/test_constants.py` (or extend the existing
   `is_offense_scoring_key` parametrized test file) — `is_defense_scoring_key`
   and `is_kicking_scoring_key` classify the real key samples pulled during
   design correctly, and reject offense keys / precomputed fields
   (`pts_half_ppr`, `adp_*`, `rank_std`, etc.) the same way the existing
   offense test rejects `fum_rec`.
2. `tests/engine/test_scoring.py` — `score_stats()` recomputes a real 2025
   DEF stat line and a real 2025 K stat line against Sleeper's own
   `pts_half_ppr`/`pts_std`, within the same tolerance the offense golden
   test uses, confirming points-allowed exclusion is the *only* material
   gap (i.e. the reproduction is close except for the documented
   points-allowed shortfall, not silently wrong elsewhere).
3. `tests/engine/test_vor.py` — a league fixture with `DEF`/`K` roster slots
   produces nonzero, position-differentiated VOR for DEF/K entries (proves
   the existing position-agnostic replacement/VOR pipeline picks them up
   with no changes there).
4. `tests/ingest/espn/test_league.py` — extend the scoring-settings parse
   test with the new `statId` entries once the live crosswalk research
   (§4) lands, against the real captured `mSettings` fixture.
5. No new JS test infrastructure exists in this repo for `board.js`; the
   frontend change is verified manually against a live snake board for a
   league that rosters DEF/K (per `superpowers:verification-before-completion`
   discipline — run the app, confirm the filter buttons, row colors, and
   roster-summary cells all show DEF/K correctly, not just that the code
   compiles).

## 7. Risks

| Risk | Mitigation |
|---|---|
| Guessed defense/kicking key set (§3.1) doesn't match what the real leagues' `scoring_settings` actually weight | Golden test against real Sleeper `pts_half_ppr` (§6.2) catches drift before shipping; key sets are adjusted to match, not assumed correct from the design-time sample |
| ESPN `statId` crosswalk for defense/kicking wrong or incomplete, same risk the original ESPN adapter already carries for offense | Same mitigation already proven there: live fixture capture + golden test against a real, independently-verifiable point total, adapter isolation limits blast radius to `ingest/espn/league.py` |
| Points-allowed exclusion (§3.2) surprises a user who expects their league's `pts_allow` weights to affect DEF valuation | Documented at the classifier (code comment) and in this spec; not a silent gap |
| Frontend hard-coded position lists missed in a fourth spot not found during design | Manual verification against a live board (§6.5) before calling this done, not just the backend test suite passing |
