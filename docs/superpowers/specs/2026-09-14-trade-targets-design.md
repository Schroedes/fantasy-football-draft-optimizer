# Proactive Trade Targets — Design (Phase 2)

## Overview

Phase 2 of the deferred "proactive trade-target suggestions" follow-up (item #2 from the multi-league season companion initiative's deferred list). Phase 1 (shipped, PR #37) built `engine/roster_needs.py` — a per-position weakness+thinness severity score — and surfaced it as a before/after preview inside the Trade Machine. Phase 2 builds the piece Phase 1 deliberately deferred: identifying *specific players on other teams* worth trading for, and what to realistically offer for them. This covers both halves originally scoped together and deferred as a pair:

- **(C) Counter-offer suggestions** — while building a trade in the Trade Machine, always-visible suggestions for additional players to ask the selected partner for.
- **(D) League-wide browse** — a new season-screen tab ranking plausible trade targets across every other team, with no partner or offer pre-selected.

Both are powered by the same new engine logic, at different scopes.

## Target + Offer Pairing Algorithm

For a fixed "your team" and one candidate "their team," `engine/trade_targets.py::suggest_for_team` produces ranked suggestions. Each suggestion pairs a **target** (what you'd ask for) with an **offer package** (what you'd give), both computed against **hypothetical** post-selection rosters — the same substitution pattern Phase 1's `roster_needs.position_needs` already uses, so suggestions stay coherent with whatever is already checked in an in-progress Trade Machine session (relevant to (C); (D) has nothing pre-selected, so this is a no-op there).

**1. Two-way fit (which position to target/offer):**
- *Target position*: one of your worst-need positions (`roster_needs` severity Moderate/Severe) where their team has real surplus (severity Fine *and* they roster ≥2 players there, so giving one away doesn't gut their own starting lineup).
- *Offer position*: one of your surplus positions (severity Fine, ≥2 rostered) where their team has a real need (Moderate/Severe) — prioritized first, since offering something that fixes *their* need too is what makes a real opponent plausibly say yes.

**2. Target pick:** their best (highest-value) player at the target position, excluding anyone already selected in the in-progress trade.

**3. Offer package (value-matched, not fixed-size):** start with your single best-available player at the offer position. If the resulting trade's raw differential (via the existing `engine/trade_value.evaluate_trade`) isn't within a "fair" band, try adding ONE of your own draft picks next (dynasty/keeper leagues only — redraft leagues never have picks to offer, so this branch is simply unreachable there, no special-casing needed) before reaching for a second full player. Cap the offer package at 2 players — real trades rarely involve more per side, and if no pick or second player closes the gap, drop this pairing rather than surface a lopsided one.

**4. Filters — a suggestion must pass BOTH to surface:**
- **Fairness** (so a real opponent would plausibly accept): the offer package's raw trade differential vs. the target, via `evaluate_trade`, falls within the "Fair trade" or "Slight edge" bands already used in the Trade Machine scoreboard (magnitude under ~20 — the same thresholds already shown there). "Lopsided" is rejected outright regardless of how good it'd be for you.
- **Net value for you**: accounting for backfilling any freed roster spot(s) — an offer of N players for M target players (N > M) opens `N - M` spots — with the single best available free agent by raw VOR (position-agnostic, reusing `waiver_value.free_agents`), your total roster value after the trade (`power_ranking.team_value(entry, valued, league, position="OVR", scope="full")` — reused directly, no new computation) must exceed your total roster value before it.

**5. Ranking:** suggestions that pass both filters are ranked by net value gain (the Section 4 delta), descending.

## Data Model

```python
@dataclass(frozen=True, slots=True)
class TradeSuggestion:
    partner_roster_id: int
    partner_team_name: str
    target_player_ids: tuple[str, ...]
    offer_player_ids: tuple[str, ...]
    offer_pick_labels: tuple[str, ...]     # empty for redraft/keeper leagues
    target_value: float
    offer_value: float
    net_value_gain: float                  # your total roster value: after - before
    differential: float                    # target_value - offer_value, for display
```

`suggest_for_team(your_roster, their_roster, valued, league, free_agent_ids, all_picks_by_roster, *, already_selected_yours, already_selected_theirs) -> list[TradeSuggestion]` returns every passing suggestion for one partner team, already ranked. `already_selected_yours`/`already_selected_theirs` are player-id sets already checked in an in-progress trade (empty for (D)'s cold-start browse), excluded from further target/offer candidacy and folded into the hypothetical roster the needs/value computations run against.

## Endpoints

Both compose `suggest_for_team`, just at different scopes — no duplicated logic between (C) and (D):

- **`POST /api/leagues/{league_key}/trade/suggestions`** — same request shape as the existing `POST /trade/evaluate` (`{partner_roster_id, side_a, side_b}`), so it reflects whatever's already selected in the live Trade Machine session. Calls `suggest_for_team` once, for the one named partner. Powers (C).
- **`GET /api/leagues/{league_key}/trade-targets`** — no body; uses your current real roster (nothing pre-selected). Calls `suggest_for_team` once per other team in the league, merges every team's passing suggestions into one list, sorts by `net_value_gain` descending, returns the top 10. Powers (D).

## UI

**(C)** adds a "Suggested additions" panel to the existing Trade Machine modal, below the roster-needs panel from Phase 1. Always visible once a partner is selected (per the earlier design decision), live-updating on the same debounce as the rest of the modal. Each suggestion shows the target player(s), the offer package (players and/or picks), and the value differential; a one-click "Add to trade" checks the corresponding boxes in the existing player/pick pickers rather than requiring manual re-selection.

**(D)** gets its own new season-screen tab, **Targets**, alongside Lineup/Power ranking/Draft capital/Trades/Waivers/Scorecard. A ranked list, one row per suggestion: target player + their team name, the offer package, and a one-line "why" (e.g. "fills your TE need — they're deep at TE and thin at WR, where you have surplus"). Clicking a row opens the Trade Machine modal with that partner selected and both sides of the suggestion pre-checked.

## Testing

TDD unit tests for `engine/trade_targets.py::suggest_for_team` covering: the two-way position-fit selection, the offer-package value-matching (single player passes / pick added to bridge / second player added / no combination found so the pairing is dropped), both filters independently (a fair-but-value-negative pairing rejected; a value-positive-but-lopsided pairing rejected), and the dynasty-vs-redraft pick-availability branch (redraft leagues never produce a pick-bridging offer, since their pick lists are always empty). Integration tests on both new endpoints, reusing the existing fixture conventions from `test_trade_endpoints.py`. Frontend verified live against a real tracked league, same as every other UI piece in this initiative.

## Deferred

- **Better draft-pick valuation in trades generally** (not specific to this feature) — flagged by the user as a known future need, out of scope for this phase. `PICK_VALUE_CURVE` (from sub-project #5) stays as-is here.
- Everything else from the original four-piece Phase 1/2 split remains shipped: need scoring and the Trade Machine depth-impact preview (Phase 1, PR #37).
