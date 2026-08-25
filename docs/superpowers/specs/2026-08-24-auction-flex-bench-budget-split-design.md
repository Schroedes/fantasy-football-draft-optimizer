# Auction Flex/Bench Budget Split — Design

**Date:** 2026-08-24
**Status:** Approved, pending implementation
**Prior art:** `src/ffdo/engine/auction.py` (`positional_budget()`, shipped as
part of the original auction support)

## 1. Purpose

`positional_budget()` currently lumps every FLEX-eligible roster slot and
every bench (`"BN"`) slot into one `flex_bench_reserve` bucket, priced flat
at the $1 minimum bid. That's wrong: a FLEX slot is a starting lineup spot
that scores points every week and should be budgeted like a real position;
bench is depth/injury insurance and should stay a low-priority reserve. This
change splits the combined bucket into two independently priced categories,
`flex` and `bench`.

### 1.1 Goals

- `flex` is priced from real remaining market value — the best players still
  available at any flex-eligible position, after dedicated-position budgets
  have already claimed their share — not a flat floor.
- `bench` keeps today's behavior: a flat $1/slot reserve.
- No player's value is double-counted between a dedicated-position budget
  and the flex budget.
- The `by_position` API payload exposes `flex` and `bench` as dict entries
  shaped identically to the existing QB/RB/WR/TE entries
  (`{"recommended": float, "slots_open": int}`), so the UI can treat `flex`
  as a fifth position row.

### 1.2 Non-goals

- Distinguishing between different flex *types* (`FLEX` vs `WRRB_FLEX` vs
  `REC_FLEX` vs `SUPER_FLEX`) when a league mixes more than one. All flex
  slots in a league are still combined into one `flex` budget category, the
  same way the current code already combines them for slot-counting
  purposes. A league with more than one distinct flex slot type is rare and
  not something this feature needs to model separately.
- Any change to `baseline_prices()`, `inflation_factor()`, or `max_bid()`.
  Those are unaffected.
- Any change to how negative-VOR players are priced. They already floor to
  exactly `MIN_BID` ($1) via `baseline_prices()`; this design inherits that
  floor unchanged (see §5).

## 2. Current behavior (for contrast)

`src/ffdo/engine/auction.py:115-118`:

```python
flex_bench_total = sum(
    1 for slot in league.roster_positions
    if slot in FLEX_ELIGIBILITY or slot == "BN")
flex_bench_remaining = max(0, flex_bench_total - leftover - undetermined)
```

Priced at `raw_reserve = MIN_BID * flex_bench_remaining` (line 135) and
surfaced as `flex_bench_reserve` / `flex_bench_slots_open` (lines 146-147).
No downstream code distinguishes flex from bench.

## 3. Slot accounting

Two independent slot counts replace `flex_bench_total`:

```python
flex_total = sum(1 for slot in league.roster_positions
                 if slot in FLEX_ELIGIBILITY)
bench_total = league.roster_positions.count("BN")
```

`leftover` (extra picks at a dedicated position beyond that position's
dedicated slot count — e.g. a 3rd RB when the league has 2 RB slots) and
`undetermined` (picks at a position outside `OFFENSE_POSITIONS`, e.g. K/DST)
are both still computed exactly as today. Their role changes: `leftover`
picks are flex-eligible by definition (they're QB/RB/WR/TE), so they reduce
flex need first; only once flex is fully absorbed does the excess spill into
bench. `undetermined` picks are never flex-eligible (no `FLEX_ELIGIBILITY`
entry admits K/DST), so they only ever reduce bench need:

```python
flex_remaining = max(0, flex_total - leftover)
bench_spill = max(0, leftover - flex_total)
bench_remaining = max(0, bench_total - bench_spill - undetermined)
```

This is a direct generalization of the existing single-bucket subtraction,
not new logic — it just resolves the ambiguity of "which bucket does this
extra pick count against" that the combined bucket never had to answer.

## 4. Pricing

### 4.1 Dedicated positions (unchanged in method, restructured to expose leftovers)

For each `pos` in `OFFENSE_POSITIONS`, the existing top-N-by-price selection
is kept, but the sorted candidate pool is now retained past the cut so its
remainder can feed the flex pool:

```python
raw: dict[str, float] = {}
pos_leftover_pool: dict[str, list[float]] = {}
for pos in OFFENSE_POSITIONS:
    need = dedicated_need[pos]
    pool = sorted(
        (max(MIN_BID, baseline.get(vp.profile.player_id, 1.0) * factor)
         for vp in available if vp.profile.position == pos),
        reverse=True,
    )
    raw[pos] = sum(pool[:need])
    pos_leftover_pool[pos] = pool[need:]
```

`raw[pos]` is numerically identical to what today's code produces — only
the retained remainder (`pool[need:]`) is new.

### 4.2 Flex

Union the eligible positions across every flex slot type present in the
league (normally just `{RB, WR, TE}`; includes `QB` for a superflex league),
combine those positions' leftover pools, re-sort descending, and take the
top `flex_remaining`:

```python
flex_positions = {
    pos for slot in league.roster_positions if slot in FLEX_ELIGIBILITY
    for pos in FLEX_ELIGIBILITY[slot]
}
flex_candidates = sorted(
    (price for pos in flex_positions for price in pos_leftover_pool.get(pos, [])),
    reverse=True,
)[:flex_remaining]
raw_flex = sum(flex_candidates)
```

Because each dedicated pool's top `need` players were already removed
before this union runs, no player is counted in both a dedicated budget and
the flex budget.

### 4.3 Bench

Unchanged in method — flat reserve at the minimum bid:

```python
raw_bench = MIN_BID * bench_remaining
```

### 4.4 Scaling and output shape

```python
total_raw = sum(raw.values()) + raw_flex + raw_bench
scale = your_dollars_left / total_raw if total_raw > 0 else 0.0

out = {
    pos: {"recommended": round(raw[pos] * scale, 1), "slots_open": dedicated_need[pos]}
    for pos in OFFENSE_POSITIONS
}
out["FLEX"] = {"recommended": round(raw_flex * scale, 1), "slots_open": flex_remaining}
out["BENCH"] = {"recommended": round(raw_bench * scale, 1), "slots_open": bench_remaining}
```

`flex_bench_reserve` / `flex_bench_slots_open` are removed from the payload
entirely — replaced by `FLEX` / `BENCH`, each shaped like the QB/RB/WR/TE
entries. Uppercase keys match the existing `OFFENSE_POSITIONS` casing
convention (`"QB"`, `"RB"`, ...) rather than introducing a mixed-case
exception — this also lets `board.js` (§6) treat `FLEX` as just another
entry in the same position list with no special-casing. This is a breaking
key rename in `by_position`; the only consumers are `board.js` (§6) and the
test suite (§7), both updated in this change.

## 5. Negative VOR

No new handling needed. `baseline_prices()` already clamps negative VOR to
$0 surplus before pricing, so any such player's price is exactly `MIN_BID`
regardless of which pool it appears in. Since every pool (dedicated and
flex) is sorted descending and only the top N are summed, a negative-VOR
player only affects a budget when the position is thin enough that fewer
than N better options remain — the same behavior the dedicated-position
budgets already have today. Flex can never price below bench for a given
slot, since $1 is a floor, not a discount.

## 6. UI

`src/ffdo/web/board/board.js`, `renderPositionBudget()` (currently lines
214-246):

- Extend the rendered position list from `["QB", "RB", "WR", "TE"]` to
  `["QB", "RB", "WR", "TE", "FLEX"]`. The existing per-row template already
  looks up color via `var(--${pos.toLowerCase()}, var(--muted))`, which
  falls back to the muted color for `FLEX` with no CSS change required (no
  `--flex` variable is defined in `board.css`).
- `maxAmount` calculation drops the `byPos.flex_bench_reserve` term (no
  longer exists) — `FLEX` is now just another entry in the `positions` map,
  already covered by `...positions.map(pos => byPos[pos].recommended)`.
- Replace the `flex_bench_reserve`/`flex_bench_slots_open` reserve block
  (lines 241-245) with the equivalent read from `byPos.BENCH.recommended`
  and `byPos.BENCH.slots_open`, keeping the existing low-emphasis
  `.posbudget-reserve` styling and singular/plural slot-count wording,
  relabeled "Bench reserve".

No `board.css` changes are required — `.posbudget-row` (used for the new
FLEX row) and `.posbudget-reserve` (used for the bench line) already exist
and are reused as-is.

## 7. Testing

`tests/engine/test_auction.py` — three existing tests reference the old key
shape and must be updated to the new one, no new test files needed:

1. `test_positional_budget_need_and_slot_invariant` (currently line 106):
   replace `result["flex_bench_slots_open"]` with
   `result["FLEX"]["slots_open"] + result["BENCH"]["slots_open"]` in the
   total-slots invariant; the league fixture (`FLEX`, `BN`, `BN`) yields
   `FLEX.slots_open == 1`, `BENCH.slots_open == 2`.
2. `test_positional_budget_scales_to_your_dollars_left` (currently line
   134): replace `byPos.flex_bench_reserve` with
   `result["FLEX"]["recommended"] + result["BENCH"]["recommended"]` in the
   total.
3. `test_extra_drafted_players_reduce_flex_bench_not_dedicated_need`
   (currently line 157): league is `("RB", "FLEX", "BN")` with 2 RB picks
   against 1 dedicated RB slot — 1 leftover. Per §3, leftover reduces flex
   first: `FLEX.slots_open == 0` (1 flex slot fully absorbed by the
   leftover), `BENCH.slots_open == 1` (unaffected, since leftover was fully
   absorbed by flex and never spilled). Rename/update the assertions
   accordingly; the docstring's claim ("must have used a FLEX/bench slot")
   still holds, just now attributable specifically to the flex slot.

`tests/api/test_board.py` also references the old key shape at the API
layer (it calls `positional_budget()` indirectly through
`build_auction_board()`) and needs the same rename:

4. `test_board_includes_positional_budget_recommendation` (currently line
   252): the `set(by_pos)` assertion becomes
   `{"QB", "RB", "WR", "TE", "FLEX", "BENCH"}`; the `total` sum replaces
   `by_pos["flex_bench_reserve"]` with
   `by_pos["FLEX"]["recommended"] + by_pos["BENCH"]["recommended"]`.
5. `test_positional_budget_slot_invariant_holds_for_a_real_roster`
   (currently line 274): `slots_accounted` replaces
   `by_pos["flex_bench_slots_open"]` with
   `by_pos["FLEX"]["slots_open"] + by_pos["BENCH"]["slots_open"]`.

New coverage worth adding (not in current suite, needed to lock in the new
pricing behavior):

6. A test asserting flex pricing pulls from leftover dedicated-position
   pools rather than flooring at `MIN_BID`: a league with a thin dedicated
   need and a deep available pool at a flex-eligible position should show
   `FLEX.recommended` meaningfully above `BENCH.recommended` for the same
   `slots_open` count of 1 each.
7. A test asserting no double-counting: construct a case where, if the flex
   pool included a player already claimed by a dedicated budget, the total
   raw value would exceed the sum of actually-distinct top players; assert
   the total matches the distinct-player sum.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Breaking API key rename (`flex_bench_*` → `flex`/`bench`) breaks an external consumer | Only consumer is this repo's own `board.js` and test suite, both updated in this change; no external API contract exists for this local tool |
| Flex pricing now depends on iteration order/composition of `OFFENSE_POSITIONS` leftover pools | `OFFENSE_POSITIONS` is a fixed, small, well-known tuple (`QB/RB/WR/TE`); no ordering sensitivity in the union+sort approach |
| Superflex leagues (`SUPER_FLEX` includes QB) pull QB leftovers into the flex pool, which a league without any dedicated QB slot never populates | Falls out naturally from `pos_leftover_pool.get(pos, [])` defaulting to empty — no special-casing needed |
