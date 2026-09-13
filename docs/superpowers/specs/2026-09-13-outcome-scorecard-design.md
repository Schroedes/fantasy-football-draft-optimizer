# Outcome Scorecard — Design

## Overview

Sub-project #7 of the multi-league season companion initiative. Every recommendation FFDO makes — weekly lineup swaps (#3), trades (#5), waiver adds (#6), and (newly, this sub-project) draft picks — should be checkable against what actually happened. This sub-project builds that check: a **scorecard** that surfaces, per recommendation type, how good FFDO's advice has actually been.

**Explicitly deferred to a follow-up sub-project (#8):** auto-calibration — using the scorecard's data to automatically nudge the engine's tunable parameters. That requires real ledger data to calibrate against, which doesn't exist until this sub-project ships and runs for a while. Building both together was considered and rejected as materially oversized (6 separate bias-detection mechanisms, each needing bespoke statistical logic, plus safety rails for unattended parameter drift) relative to every other sub-project in this initiative.

## Scope

**In scope:**
- Two new decision ledgers: `waiver_ledger` (deferred from #6) and `draft_pick_ledger` (new — nothing today persists draft-day picks or their grades).
- A new engine capability: positional scarcity, added as a real (if initially inert) VOR adjustment, promoted via the same manual backtest-script pattern that validated `DURABILITY_WEIGHT` in #4 — not auto-calibrated.
- A scorecard view aggregating all four recommendation types: lineup, trade, waiver, draft.
- One new endpoint and one new season-screen tab.

**Out of scope (deferred to #8):**
- The `calibration_setting` / `calibration_adjustment_log` tables.
- Automatic, unattended nudging of any engine parameter based on measured bias.
- Any "visible/revertible adjustment log" UI.

## Data Model

### `waiver_ledger` (new — `src/ffdo/api/waiver_ledger.py`)

Mirrors `trade_ledger.py`'s write-once posture. One row per claim, recorded the first time it's seen. Unlike lineup (needs week-lock) or trade (never resolves, continuously revalued), a waiver claim's outcome is known synchronously — Sleeper's transaction feed reports `status: "complete"` (won) or a failed/cancelled status (lost) as soon as waivers process, so no separate `resolve()` step is needed.

```sql
CREATE TABLE IF NOT EXISTS waiver_record (
    league_key TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    roster_id INTEGER NOT NULL,
    add_player_id TEXT NOT NULL,
    drop_player_id TEXT,
    recommended_bid REAL,          -- NULL if this add wasn't one of our recommendations
    actual_bid INTEGER NOT NULL,
    predicted_vor_gain REAL,       -- NULL if not one of our recommendations
    won INTEGER NOT NULL,          -- 0/1, from the transaction's final status
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (league_key, transaction_id)
);
```

`record_if_absent(league_key, claim, *, recommended_bid, predicted_vor_gain)` — called from the same code path that already walks `waivers_mod.fetch_waivers(...)` in `GET /api/leagues/{league_key}/waivers`. `recommended_bid`/`predicted_vor_gain` are looked up by matching the claim's `add_player_id` against that week's `recommend_adds(...)` output at read time — `None` when the claimed player wasn't one of our suggestions (a real, expected case: users add players we didn't recommend).

### `draft_pick_ledger` (new — `src/ffdo/api/draft_pick_ledger.py`)

One row per real pick, recorded as the live draft board is polled — mirrors `board.py::_build_auction_history`/`_build_snake_history`, which already recompute a grade for every pick on every poll; this just persists that computation once per pick instead of discarding it. Keyed so re-polling mid-draft never duplicates a row.

```sql
CREATE TABLE IF NOT EXISTS draft_pick_record (
    league_key TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    pick_no INTEGER NOT NULL,
    round INTEGER NOT NULL,
    roster_id INTEGER,
    player_id TEXT NOT NULL,
    position TEXT,
    amount INTEGER,                -- auction only, NULL for snake
    grade TEXT,                    -- GREAT/GOOD/FAIR/POOR, from engine.grading; NULL if ungradable
    vor_at_pick REAL,
    predicted_survival REAL,       -- market.simulate_survival's estimate for this player, captured
                                    -- from the SAME poll's live survival dict, just before he was
                                    -- taken (NULL for auction drafts -- survival is a snake-only concept)
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (league_key, draft_id, pick_no)
);
```

`record_if_absent(league_key, draft_id, pick, grade, vor_at_pick, predicted_survival)` — called from the live-draft board-building endpoint (`app.py`, the function containing the `market.simulate_survival(...)` call at line 980), once per newly-appeared pick in `state.picks` on each poll. `predicted_survival` is `survival.get(pick.player_id)` read from that same poll's already-computed `survival` dict — no new engine call needed, just persistence of a value the endpoint already has in hand.

**Why this needs to be a new ledger and can't just reuse the live grading in `board.py`:** `DraftState` is fetched live from Sleeper and never persisted — once a draft completes and the season screen takes over, there's no stored record of what was picked, at what grade, or what the model predicted about survival. Nothing to show on a scorecard afterward without this.

## Positional Scarcity (new engine capability)

**Mechanism:** for each position, measure the replacement-level **cliff** — the average points gap between the replacement-level player (already computed by `replacement.py`'s ranked lists) and the next 3 players ranked below him at that position. A steep cliff (big gap) means the position is scarce: there's no comparable fallback once the replacement-level player is gone. A shallow cliff means depth is plentiful.

```python
# src/ffdo/engine/scarcity.py (new)
CLIFF_DEPTH = 3

def positional_cliff(ranked: Mapping[str, list[tuple[float, str]]], levels: Mapping[str, float]) -> dict[str, float]:
    """Points gap between replacement level and the next CLIFF_DEPTH players below it, per position."""
    ...

def scarcity_multiplier(cliff: Mapping[str, float], strength: float) -> dict[str, float]:
    """1.0 + strength * normalized_cliff[pos]. strength=0.0 -> every multiplier is 1.0 (no-op)."""
    ...
```

`vor.compute` gains a `scarcity_strength: float = SCARCITY_STRENGTH` kwarg (module-level default `0.0`, defined in `engine/vor.py`, same promoted-constant pattern as `DURABILITY_WEIGHT`):

```python
out[pid].vor = (adj_pts - levels[pos]) * scarcity_multiplier[pos]
```

Applied uniformly — including to already-negative VOR, where a multiplier > 1.0 makes a below-replacement player at a scarce position look *worse* than the same margin at a deep position, which is directionally correct (a scarce position has no cheap waiver-wire fallback, so a weak roster spot there costs more).

**Promotion path:** `scripts/backtest_scarcity.py`, extending `backtest/harness.py`'s existing sweep (`evaluate_season`), tests candidate `SCARCITY_STRENGTH` values against historical snapshots the same way `DURABILITY_WEIGHT` was validated in #4. Prints results for human review. `SCARCITY_STRENGTH` stays `0.0` (checked in, inert) unless a real backtest shows improvement — this sub-project does not commit to promoting it above zero; that's a judgment call for whoever reviews the backtest's printed output.

## Scorecard Computation

New `src/ffdo/engine/scorecard.py`, pure functions over ledger data (no I/O) — same separation as every other engine module:

| Type | Metric | Computed from |
|---|---|---|
| **Lineup** | Weeks fully/partially/not followed; estimated points left on the bench when not fully followed | `lineup_ledger` rows (existing `followed` status) joined at read time against final realized points for the recommended-vs-actual slots that differed — the ledger itself stores no points, only which players, so this join happens in `scorecard.py`, not by widening the ledger schema |
| **Trade** | Value-at-trade vs. current recomputed value, per team: how many of your trades have gained/lost value since | `trade_ledger` rows + the same read-time current-value recompute `GET /trades` already does (#5) |
| **Waiver** | Claim win rate, average realized VOR gain vs. predicted, bid efficiency (recommended vs. actual amount paid) | `waiver_ledger` rows |
| **Draft** | Grade distribution (GREAT/GOOD/FAIR/POOR) for your own picks, aggregated across all drafts this league (or all tracked leagues) has run | `draft_pick_ledger` rows, filtered to `roster_id == your roster` |

Each metric function takes ledger rows (already fetched by the API layer) plus whatever read-time data it needs (realized points, current valuations) and returns a plain dict — `api/app.py` composes these into one response, same pattern as every other season-screen endpoint.

## API & UI

**New endpoint:** `GET /api/leagues/{league_key}/scorecard` — returns all four metrics in one payload. Non-Sleeper leagues get whatever subset is computable (e.g., an ESPN league with no FAAB waivers gets `waiver: null`, same graceful-degradation posture as the waiver endpoint's own 400 for non-FAAB leagues, but scoped per-metric here rather than failing the whole request).

**New UI:** a 5th season-screen tab, **Scorecard**, alongside Lineup/Trades/Waivers — same lazy-load-on-open pattern (`_scorecardData`, `loadScorecard()`, `renderScorecard()`) as the other three.

## Testing

Standard TDD per sub-project's existing pattern: unit tests for `scarcity.py`'s cliff/multiplier math (including the `strength=0.0` no-op case), `waiver_ledger.py`/`draft_pick_ledger.py`'s CRUD + `record_if_absent` idempotency, `scorecard.py`'s four metric functions against hand-built ledger fixtures, and an integration test for the new endpoint. Per the pattern established in #3/#5 (the most consequential bugs in this initiative have come from manual smoke tests against real leagues, not automated review), the controller running SDD on this plan should manually smoke-test the scorecard endpoint against a real tracked league with real ledger history before considering any task involving it complete.

## Deferred / Open Items

- **Auto-calibration (sub-project #8):** the `calibration_setting`/`calibration_adjustment_log` tables, and per-knob bias detection for `K`, `DURABILITY_WEIGHT`, `DISCOUNT_RATE`, `PICK_UNCERTAINTY_DISCOUNT_RATE`, FAAB aggressiveness, `tau`, and `SCARCITY_STRENGTH`. Needs this sub-project's ledgers populated with real data first.
- **`SCARCITY_STRENGTH` promotion is not guaranteed.** If the backtest script shows no real improvement, it stays at `0.0` and the scarcity capability ships present-but-inert, exactly like `AGE_WEIGHT` sits today.
- **Draft-pick ledger's `your_roster_id` scoping:** grading applies to every pick in a draft (league-wide), but the scorecard's draft metric only surfaces the tracked user's own picks — mirrors the same "scope to the tracked user's own roster" fix already made in #3 (`4cff83f`).
