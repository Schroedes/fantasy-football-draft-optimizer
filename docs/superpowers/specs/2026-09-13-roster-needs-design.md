# Roster Needs & Trade Depth-Impact Preview — Design

## Overview

Phase 1 of the deferred "proactive trade-target suggestions" follow-up (item #2 from the multi-league season companion initiative's deferred list). Builds a formal, computable "roster need" concept — nothing like it exists today beyond `_roster_callout`'s single-line "thin at X" heuristic — and surfaces it as a live before/after preview inside the Trade Machine (shipped this session, PR #36), so a trade that looks good on raw value doesn't silently gut a position's depth, for either side of the deal.

**Explicitly deferred to Phase 2:** counter-offer suggestions (what to ask for instead, given an incoming offer) and a league-wide "who should I target" browse view. Both need a genuinely different kind of logic — ranking *other teams'* players as plausible targets, not just describing your own roster's needs — and depend on this phase's scoring existing and being real first.

## Need Scoring

For each of QB/RB/WR/TE, combine two signals into one severity score, computed by a new `engine/roster_needs.py`:

**Weakness** — the team's starter-scope position rank, reusing `engine/power_ranking.py`'s existing ranking (already shown on the Power Ranking tab), normalized: `weakness = (rank - 1) / (num_teams - 1)`, so 0 = best team at that position, 1 = worst.

**Thinness** — how much bench room this league's own construction actually expects at this position, reusing `engine/waiver_value.py::position_cap` (the `POSITION_CAP_EXTRA` concept from sub-project #6: `{"QB": 1, "TE": 1, "DEF": 0, "K": 0}`, RB/WR uncapped):

```python
starting_reach = dedicated_slots + flex_eligible_slots   # same calc position_cap() does internally
cap = position_cap(position, league)                      # None for RB/WR (uncapped)
your_count = number of rostered players at this position

if cap is not None:
    if cap <= starting_reach:
        # This league expects zero bench depth here (DEF/K) -- never
        # penalize for lacking a backup nobody expects you to roster.
        thinness = 0.0 if your_count >= starting_reach else 1.0
    else:
        thinness = clamp(1.0 - (your_count - starting_reach) / (cap - starting_reach), 0.0, 1.0)
else:
    # RB/WR: no fixed "ideal" bench count exists for an uncapped position,
    # so thinness decays smoothly with each extra player instead of
    # demanding a specific target.
    extra = max(0, your_count - starting_reach)
    thinness = 1.0 / (1.0 + extra)
```

This was the one point revised after user review: the original design normalized thinness against an arbitrary absolute bench count, which would flag a position as "thin" purely because a league's roster construction leaves little bench room anywhere — not because that position is actually under-supported relative to what the league itself expects.

**Combined:** `severity_score = 0.5 * weakness + 0.5 * thinness`, bucketed into **Severe** (≥ 0.66) / **Moderate** (≥ 0.33) / **Fine** (below) — the same hand-set-threshold bucketing style already used in `engine/grading.py`'s GREAT/GOOD/FAIR/POOR (no real outcome data exists to fit this against, same as that existing code).

**Signature:**
```python
@dataclass(frozen=True, slots=True)
class NeedScore:
    rank: int          # 1 = best team at this position, num_teams = worst
    severity: str       # "Severe" | "Moderate" | "Fine"

def position_needs(
    entry: RosterEntry,             # the roster to score -- real or a hypothetical (see below)
    all_rosters: list[RosterEntry],  # every team in the league, for the ranking comparison
    valued: Mapping[str, ValuedPlayer],
    league,
) -> dict[str, NeedScore]:          # keyed "QB"/"RB"/"WR"/"TE"
```

Ranking a **hypothetical** roster (e.g. "you, after this trade") works by substituting `entry`'s own starter-scope position value into the *current* ranked list at its `roster_id`'s slot, re-sorting, and reading off the new rank — every other team's value stays frozen at its real, current value. This is a deliberate scope boundary: a full league-wide re-simulation (the partner's own ranking changing everyone else's relative position too) is out of scope. The hypothetical roster itself is a `RosterEntry` built via `dataclasses.replace(real_entry, player_ids=hypothetical_ids)` — `position_needs`/`team_value` only ever read `.player_ids` and `.roster_id`, so this needs no new type.

**Refactor along the way:** `engine/power_ranking.py::_team_value` becomes public (`team_value`), since `position_needs` is a second real caller now, not a one-off duplication.

## Trade Machine Integration

`POST /api/leagues/{league_key}/trade/evaluate` (existing, already live-called by the Trade Machine modal on every debounced update) gains two new response fields — no new endpoint, since everything needed (your roster, the partner's roster, every roster in the league, valuations) is already fetched there:

```json
{
  "side_a_value": 155.6, "side_b_value": 85.7, "differential": 69.9, "differential_pct": 0.815,
  "needs_before": {
    "you":     {"QB": {"rank": 5, "severity": "Fine"}, "RB": {"rank": 2, "severity": "Fine"}, "WR": {...}, "TE": {"rank": 11, "severity": "Severe"}},
    "partner": {"QB": {...}, "RB": {...}, "WR": {...}, "TE": {...}}
  },
  "needs_after": {
    "you":     {"QB": {...}, "RB": {...}, "WR": {...}, "TE": {"rank": 12, "severity": "Severe"}},
    "partner": {"QB": {...}, "RB": {...}, "WR": {...}, "TE": {...}}
  }
}
```

`needs_after.you` is computed from `(your current roster − side_a's players) ∪ side_b's players`; `needs_after.partner` symmetrically from `(partner's current roster − side_b's players) ∪ side_a's players`. Draft picks never affect this — they aren't rostered players, so they don't change position counts either direction.

**UI:** the Trade Machine modal (`season.js`) gains a "Roster needs" panel below the existing value scoreboard, two columns (Your team / Partner), one row per position showing rank and severity before → after — e.g. `TE: 11th → 12th (Fine → Severe)` — colored red when severity worsens, green when it improves, muted when unchanged, reusing the same `--green`/`--red`/`--muted` tokens already in the modal. This directly answers the motivating concern: a trade that clears your own TE need can still be visibly a bad idea if it hands the partner a rank jump that makes them a rival superteam.

## Testing

TDD unit tests for `engine/roster_needs.py::position_needs`, covering: the DEF/K binary-thinness case (0 extra never penalized), the QB/TE partial-cap case (thinness scales between starting_reach and cap), the RB/WR uncapped decay case, weakness rank normalization, and the hypothetical-roster substitution (confirming other teams' values stay frozen). An integration test on the extended `POST /trade/evaluate` confirming `needs_before`/`needs_after` appear for both `you` and `partner`, and shift correctly for a known trade scenario built from real fixture data. Frontend verified live against a real tracked dynasty/keeper league, same as the Trade Machine itself was.
