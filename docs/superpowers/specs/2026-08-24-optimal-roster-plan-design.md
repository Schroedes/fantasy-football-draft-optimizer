# Optimal Roster Plan — Design

**Date:** 2026-08-24
**Status:** Approved, pending implementation
**Prior art:** `docs/superpowers/specs/2026-08-24-auction-flex-bench-budget-split-design.md`
(the FLEX/BENCH split this feature builds directly on top of)

## 1. Purpose

Today's per-position budget strip tells you how much money is left for
each category, but every bid decision still requires you to mentally
combine that with "is this player actually good value" and "what else
could this money buy across my *other* open slots." That's exactly backwards
from how an auction should be played: the real question on every
nomination is "does buying this player, at this price, make my best
achievable roster better or worse." This feature computes that best
achievable roster directly — the specific combination of still-available
players, one per remaining slot, that maximizes total starting-lineup VOR
within your remaining budget — and surfaces it as its own panel.

### 1.1 Goals

- Given your remaining budget and remaining roster needs (dedicated
  positions, FLEX, BENCH — the same breakdown `positional_budget()`
  already computes), compute a concrete target roster: one specific
  player and target price per open slot.
- Maximize total VOR across the *starting* lineup (dedicated + FLEX
  slots) subject to the budget never being exceeded.
- Never suggest a plan that couldn't actually be executed: total plan
  cost must never exceed `your_dollars_left`, and every slot is always
  filled (see §1.2 — this is a guaranteed invariant, not best-effort).
- Respect a per-position cap so BENCH doesn't fill with redundant copies
  of one position: at most `2 × (dedicated slots for that position +
  flex slots that position is eligible for)` total players at any one
  position across the whole plan, counting players you've already
  drafted.
- Get meaningfully closer to the *true* budget-optimal combination than
  a naive single-pass greedy fill would — specifically, catch cases where
  locking in one expensive player early prevents affording two
  efficient players later that would have produced more total VOR for
  the same money.

### 1.2 Non-goals

- A provably-optimal solution (e.g. via an ILP solver). The player
  prices feeding this are themselves market estimates, not certainties —
  provable optimality against a guess isn't worth the added dependency,
  runtime cost, and loss of explainability. Greedy-fill plus a bounded
  local-search refinement (§6) is the target quality bar: close to
  optimal, fast, and each step is easy to reason about.
- Modeling an "unfillable slot" as a normal UI state. Every real league's
  offense player pool is far deeper than any one team's remaining slots,
  and the app already guarantees `your_dollars_left >= $1 × your remaining
  slots` (the same invariant `max_bid()` enforces) before this feature
  ever runs. Every slot is always filled. Tests assert this as an
  invariant; it is not something the UI needs a rendering path for.
- Any change to the nominated-player card. This feature ships as its own
  panel only (explicitly decided over embedding into the nomination
  flow).
- Any change to `positional_budget()`'s own output or the position-budget
  panel. This is a new, separate signal, not a replacement.
- Bench "upside" strategy (handcuffs, injury insurance quality) beyond
  what VOR already captures. The plan optimizes measured VOR; it doesn't
  model speculative bench value.

## 2. What's already reusable

The FLEX/BENCH split feature (previous spec) already computes, inside
`positional_budget()`: `dedicated_count[pos]`, `drafted_count[pos]`,
`dedicated_need[pos]`, `flex_positions` (union of positions eligible for
any flex slot in the league), `flex_eligible_leftover`/`non_flex_leftover`,
`flex_total`, `bench_total`, `flex_remaining`, `bench_remaining`. This
feature needs the exact same bookkeeping, so **§3 extracts it into a
shared helper** rather than recomputing it — the two features must never
drift apart on what "your remaining needs" means.

## 3. Extract `compute_roster_needs()`

New function in `src/ffdo/engine/auction.py`, factored out of
`positional_budget()`'s existing body (lines 97-132 today) with **zero
behavior change** — `positional_budget()` calls it and uses the returned
fields exactly as it uses its local variables today:

```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class RosterNeeds:
    drafted_count: dict[str, int]          # pos -> count you've already drafted
    dedicated_count: dict[str, int]        # pos -> dedicated slots the league has
    dedicated_need: dict[str, int]         # pos -> dedicated slots still open
    flex_positions: frozenset[str]         # union of positions eligible for any flex slot
    flex_total: int
    flex_remaining: int
    bench_total: int
    bench_remaining: int
    undetermined: int                      # your picks at non-offense positions (K/DST)


def compute_roster_needs(
    valued: Mapping[str, ValuedPlayer],
    state: DraftState,
    league,
    roster_id: int | None,
) -> RosterNeeds:
    """Your remaining roster needs, broken into dedicated/FLEX/BENCH.

    Shared by `positional_budget()` and `planner.optimal_plan()` so both
    features agree, by construction, on what "your remaining needs" means.
    """
    your_picks = ([p for p in state.picks if p.roster_id == roster_id]
                  if roster_id is not None else [])

    dedicated_count = {pos: league.roster_positions.count(pos)
                       for pos in OFFENSE_POSITIONS}
    drafted_count = dict.fromkeys(OFFENSE_POSITIONS, 0)
    undetermined = 0
    for pick in your_picks:
        vp = valued.get(pick.player_id)
        if vp is None or vp.profile.position not in OFFENSE_POSITIONS:
            undetermined += 1
            continue
        drafted_count[vp.profile.position] += 1

    dedicated_need = {
        pos: max(0, dedicated_count[pos] - min(dedicated_count[pos], drafted_count[pos]))
        for pos in OFFENSE_POSITIONS
    }
    flex_positions = frozenset(
        pos for slot in league.roster_positions if slot in FLEX_ELIGIBILITY
        for pos in FLEX_ELIGIBILITY[slot]
    )
    flex_eligible_leftover = sum(
        max(0, drafted_count[pos] - dedicated_count[pos])
        for pos in OFFENSE_POSITIONS if pos in flex_positions)
    non_flex_leftover = sum(
        max(0, drafted_count[pos] - dedicated_count[pos])
        for pos in OFFENSE_POSITIONS if pos not in flex_positions)

    flex_total = sum(1 for slot in league.roster_positions
                     if slot in FLEX_ELIGIBILITY)
    bench_total = league.roster_positions.count("BN")
    flex_remaining = max(0, flex_total - flex_eligible_leftover)
    bench_spill = max(0, flex_eligible_leftover - flex_total) + non_flex_leftover
    bench_remaining = max(0, bench_total - bench_spill - undetermined)

    return RosterNeeds(
        drafted_count=drafted_count, dedicated_count=dedicated_count,
        dedicated_need=dedicated_need, flex_positions=flex_positions,
        flex_total=flex_total, flex_remaining=flex_remaining,
        bench_total=bench_total, bench_remaining=bench_remaining,
        undetermined=undetermined,
    )
```

`positional_budget()` is rewritten to call this and use `needs.dedicated_need`,
`needs.flex_positions`, `needs.flex_remaining`, `needs.bench_remaining` in
place of its current locals — every existing `positional_budget()` test
must keep passing unchanged, since this is a pure refactor of that
function's internals.

## 4. Position caps

New helper, also in `auction.py` (or co-located with `RosterNeeds` —
implementation's call), computing how many *more* players of each
position the plan is allowed to add:

```python
def position_caps(league, needs: RosterNeeds) -> dict[str, int]:
    """How many MORE players of each position the plan may add.

    Cap = 2 x (dedicated slots for that position + flex slot instances
    that position is eligible for), counting what you've already
    drafted against the cap. Dedicated and FLEX assignments can never
    exceed this by construction (see the worked proof in the spec
    discussion) -- it only ever actually constrains BENCH picks.
    """
    remaining: dict[str, int] = {}
    for pos in OFFENSE_POSITIONS:
        flex_slots_for_pos = sum(
            1 for slot in league.roster_positions
            if slot in FLEX_ELIGIBILITY and pos in FLEX_ELIGIBILITY[slot]
        )
        starting_spots = needs.dedicated_count[pos] + flex_slots_for_pos
        cap = 2 * starting_spots
        remaining[pos] = max(0, cap - needs.drafted_count[pos])
    return remaining
```

## 5. `optimal_plan()` — Phase 1: greedy VOR-ranked fill

New file `src/ffdo/engine/planner.py`:

```python
"""Budget-constrained optimal roster planning."""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.constants import OFFENSE_POSITIONS
from ffdo.domain.models import DraftState, ValuedPlayer
from ffdo.engine.auction import MIN_BID, RosterNeeds, compute_roster_needs, position_caps


def optimal_plan(
    valued: Mapping[str, ValuedPlayer],
    baseline: Mapping[str, float],
    factor: float,
    state: DraftState,
    league,
    roster_id: int | None,
    your_dollars_left: float,
) -> dict:
    """The specific, budget-affordable roster that maximizes starting VOR.

    Two phases: a single VOR-ranked greedy pass builds an initial plan
    (§5), then a bounded pairwise-swap local search (§6) improves it --
    catching cases where an early expensive pick blocked a better later
    combination. Every slot is always filled (see spec §1.2); this is
    treated as an invariant, not best-effort.
    """
    needs = compute_roster_needs(valued, state, league, roster_id)
    caps = position_caps(league, needs)
    drafted = state.drafted_player_ids()
    available = sorted(
        (vp for pid, vp in valued.items()
         if pid not in drafted and vp.profile.position in OFFENSE_POSITIONS),
        key=lambda vp: vp.vor, reverse=True,
    )

    remaining_dedicated = dict(needs.dedicated_need)
    remaining_flex = needs.flex_remaining
    remaining_bench = needs.bench_remaining
    remaining_cap = dict(caps)
    total_slots_left = (sum(remaining_dedicated.values())
                        + remaining_flex + remaining_bench)
    budget_left = your_dollars_left

    def price_of(vp: ValuedPlayer) -> float:
        return max(MIN_BID, baseline.get(vp.profile.player_id, 1.0) * factor)

    plan: list[dict] = []
    used_ids: set[str] = set()

    for vp in available:
        if total_slots_left == 0:
            break
        pos = vp.profile.position
        if remaining_cap.get(pos, 0) <= 0:
            continue

        price = price_of(vp)
        reserve_for_others = MIN_BID * (total_slots_left - 1)
        if price > budget_left - reserve_for_others:
            continue

        if remaining_dedicated.get(pos, 0) > 0:
            slot_type, category = "dedicated", pos
            remaining_dedicated[pos] -= 1
        elif remaining_flex > 0 and pos in needs.flex_positions:
            slot_type, category = "flex", "FLEX"
            remaining_flex -= 1
        elif remaining_bench > 0:
            slot_type, category = "bench", "BENCH"
            remaining_bench -= 1
        else:
            continue

        plan.append({
            "category": category, "type": slot_type,
            "eligible_position": pos, "player_id": vp.profile.player_id,
            "name": vp.profile.full_name, "target_price": price, "vor": vp.vor,
        })
        used_ids.add(vp.profile.player_id)
        budget_left -= price
        total_slots_left -= 1
        remaining_cap[pos] -= 1

    return _refine(plan, available, used_ids, baseline, factor,
                   your_dollars_left, needs, caps)
```

Notes on this pass, since they matter for the tests in §7:

- `available` is sorted once, VOR descending, and walked to completion
  (not stopped early once slots fill) — a cheap late-VOR player can and
  should still be picked up for a BENCH slot near the end of the list;
  this is what makes a second "fill the leftovers" pass unnecessary
  (§1.2's "every slot always filled" invariant depends on walking the
  full list, not stopping once a first attempt is exhausted).
- `reserve_for_others` mirrors `max_bid()`'s existing $1-per-remaining-slot
  reserve logic exactly, applied per-candidate during the plan build
  rather than once at the end.
- Every `plan` entry always has a `category` — for dedicated slots it's
  the exact position (`"QB"`, `"RB"`, ...); for FLEX and BENCH it's the
  literal string `"FLEX"`/`"BENCH"`, with `eligible_position` carrying the
  actual drafted player's position (needed because a FLEX or BENCH slot's
  occupant's position isn't implied by the category the way a dedicated
  slot's is).

## 6. `optimal_plan()` — Phase 2: pairwise swap refinement

Appended to the same file, called as `_refine(...)` at the end of §5's
function:

```python
MAX_SWAP_ITERATIONS = 200
CANDIDATES_PER_SLOT = 15


def _refine(
    plan: list[dict],
    available: list[ValuedPlayer],
    used_ids: set[str],
    baseline: Mapping[str, float],
    factor: float,
    your_dollars_left: float,
    needs: RosterNeeds,
    caps: dict[str, int],
) -> dict:
    """Bounded local search: repeatedly swap a pair of planned slots for a
    higher-combined-VOR pair of legal replacements at no higher combined
    cost, until no improving swap exists or the iteration cap is hit.

    This is what catches "an early expensive pick blocked two efficient
    players that together beat it" -- the single greedy pass in Phase 1
    can't see that after the fact; this pass can, within its search
    radius (top `CANDIDATES_PER_SLOT` unplanned players by VOR per slot).
    """
    def is_legal(vp: ValuedPlayer, slot: dict) -> bool:
        pos = vp.profile.position
        if slot["type"] == "dedicated":
            return pos == slot["category"]
        if slot["type"] == "flex":
            return pos in needs.flex_positions
        return True  # bench: any offense position is legal

    def price_of(vp: ValuedPlayer) -> float:
        return max(MIN_BID, baseline.get(vp.profile.player_id, 1.0) * factor)

    # position counts currently used by the plan, for cap bookkeeping on swap
    pos_counts = dict.fromkeys(needs.dedicated_count, 0)
    for slot in plan:
        pos_counts[slot["eligible_position"]] = pos_counts.get(slot["eligible_position"], 0) + 1

    for _ in range(MAX_SWAP_ITERATIONS):
        improved = False
        for i, slot_a in enumerate(plan):
            candidates_a = [vp for vp in available
                            if vp.profile.player_id not in used_ids
                            and is_legal(vp, slot_a)][:CANDIDATES_PER_SLOT]
            for j, slot_b in enumerate(plan):
                if j <= i:
                    continue
                candidates_b = [vp for vp in available
                                if vp.profile.player_id not in used_ids
                                and is_legal(vp, slot_b)][:CANDIDATES_PER_SLOT]

                current_price = slot_a["target_price"] + slot_b["target_price"]
                current_vor = slot_a["vor"] + slot_b["vor"]

                best = None  # (vor_gain, ca, cb)
                for ca in candidates_a:
                    for cb in candidates_b:
                        if ca.profile.player_id == cb.profile.player_id:
                            continue
                        pa, pb = price_of(ca), price_of(cb)
                        if pa + pb > current_price:
                            continue
                        pos_a_new, pos_b_new = ca.profile.position, cb.profile.position
                        if not _caps_ok(pos_counts, slot_a["eligible_position"], pos_a_new,
                                        slot_b["eligible_position"], pos_b_new, caps):
                            continue
                        vor_gain = (ca.vor + cb.vor) - current_vor
                        if vor_gain > 0 and (best is None or vor_gain > best[0]):
                            best = (vor_gain, ca, cb)

                if best is not None:
                    _, ca, cb = best
                    used_ids.discard(slot_a["player_id"])
                    used_ids.discard(slot_b["player_id"])
                    pos_counts[slot_a["eligible_position"]] -= 1
                    pos_counts[slot_b["eligible_position"]] -= 1
                    _apply(slot_a, ca)
                    _apply(slot_b, cb)
                    used_ids.add(ca.profile.player_id)
                    used_ids.add(cb.profile.player_id)
                    pos_counts[ca.profile.position] = pos_counts.get(ca.profile.position, 0) + 1
                    pos_counts[cb.profile.position] = pos_counts.get(cb.profile.position, 0) + 1
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break

    return _to_output(plan, your_dollars_left)
```

(`_apply`, `_caps_ok`, and `_to_output` are small, self-explanatory
helpers the implementation plan will spell out in full, not left as
prose.)

Key properties this section's tests (§7) must lock in:

- A swap is only taken if `pa + pb <= current_price` — the pair's combined
  cost never increases, which is what guarantees the running total never
  exceeds `your_dollars_left` without needing to track a separate global
  budget ledger during refinement.
- `_caps_ok` re-validates both positions' caps *after* the hypothetical
  swap (accounting for the fact that `slot_a`/`slot_b`'s old positions
  are freed and the new ones are added) before accepting it.
- First-improvement, not best-of-all-pairs: the moment an improving swap
  is found, it's applied and the scan restarts from the top. This is
  simpler to reason about and test than tracking the single best swap
  across the whole plan every iteration, and converges to the same class
  of local optimum.
- `MAX_SWAP_ITERATIONS` bounds worst-case runtime; in practice convergence
  (no more improving swaps found) happens in far fewer iterations for
  roster sizes this small (~15 slots).

## 7. Output shape

```python
{
  "slots": [
    {"category": "QB", "type": "dedicated", "eligible_position": "QB",
     "player_id": "...", "name": "...", "target_price": 42.3, "vor": 55.0},
    {"category": "FLEX", "type": "flex", "eligible_position": "WR",
     "player_id": "...", "name": "...", "target_price": 18.5, "vor": 30.2},
    {"category": "BENCH", "type": "bench", "eligible_position": "RB",
     "player_id": "...", "name": "...", "target_price": 1.0, "vor": 4.1},
    # ... one entry per slot, always filled (spec §1.2)
  ],
  "total_plan_vor": 312.4,       # sum over "dedicated" + "flex" slots only
  "total_plan_cost": 187.0,      # sum over every slot, including bench
  "dollars_left_after_plan": 13.0,  # your_dollars_left - total_plan_cost
}
```

`src/ffdo/api/board.py`'s `build_auction_board()` calls
`planner.optimal_plan(valued, baseline, factor, state, league, roster_id,
your_dollars_left)` alongside its existing `auction.positional_budget(...)`
call (both need the same inputs, already in scope at that point — see
`board.py:174-175`) and adds the result to the payload as a new top-level
`"optimal_plan"` key, sibling to `"budget"`. Computed on the same
recompute cadence as `positional_budget()` — the heavy `/api/board`
rebuild, not the lightweight `/api/board/live` nomination/bid poll (the
split this repo already made for exactly this kind of cost reason).

## 8. UI

New sidebar card in `src/ffdo/web/board/index.html`, following the
existing `#rosters`/`#history` `<aside>` pattern (a titled card with a
subtitle and a scrollable row list), placed after `#history`:

```html
<aside id="optimal-plan">
  <div class="plan-head">
    <h2>Optimal plan</h2>
    <span class="plan-sub">the best affordable roster within your remaining budget</span>
  </div>
  <div class="plan-totals">
    <span><b id="plan-vor">&mdash;</b> VOR</span>
    <span><b id="plan-cost">&mdash;</b> spent</span>
    <span><b id="plan-left">&mdash;</b> left</span>
  </div>
  <div id="plan-rows"></div>
</aside>
```

New `renderOptimalPlan()` in `board.js`, called from `render()`
(`board.js:113-152`) right after the existing `renderPositionBudget();`
call (line 145) — same "hidden when not auction format or data missing"
guard `renderPositionBudget()` and `renderCow()` already use:

```javascript
function renderOptimalPlan() {
  const el = document.getElementById("optimal-plan");
  const d = state.data;
  const plan = d && d.optimal_plan;
  if (d.format === "snake" || !plan) {
    el.hidden = true;
    return;
  }
  el.hidden = false;

  document.getElementById("plan-vor").textContent = plan.total_plan_vor;
  document.getElementById("plan-cost").textContent = `$${plan.total_plan_cost}`;
  document.getElementById("plan-left").textContent = `$${plan.dollars_left_after_plan}`;

  document.getElementById("plan-rows").innerHTML = plan.slots.map(slot => {
    const posColor = `var(--${slot.eligible_position.toLowerCase()}, var(--muted))`;
    return `<div class="plan-row">
      <span class="plan-category">${slot.category}</span>
      <span class="plan-name" style="color:${posColor}">${slot.name}</span>
      <span class="plan-price">$${slot.target_price}</span>
      <span class="plan-vor-val">${slot.vor} VOR</span>
    </div>`;
  }).join("");
}
```

`board.css` gains `.plan-head`, `.plan-sub`, `.plan-totals`, `.plan-row`,
`.plan-category`, `.plan-name`, `.plan-price`, `.plan-vor-val` — styled
consistently with `.history-row`/`.rosters-head` (`board.css:150-184`),
not introducing a new visual language.

## 9. Testing

`tests/engine/test_planner.py` (new file), plus one refactor-safety check
against the existing `tests/engine/test_auction.py`:

1. **`compute_roster_needs()` extraction is behavior-preserving**: every
   existing `positional_budget()` test in `test_auction.py` must keep
   passing unchanged after the extraction (§3) — this is the safety net
   for the refactor, same discipline the FLEX/BENCH work applied to its
   own restructuring.
2. **Total plan cost never exceeds budget**: construct a league/roster
   scenario, assert `total_plan_cost <= your_dollars_left` and (per §1.2)
   that `dollars_left_after_plan >= 0`.
3. **Every slot is always filled**: assert no `plan["slots"]` entry has
   `player_id is None` even in a thin-pool scenario, given a pool
   comfortably deeper than the slot count (the realistic case per §1.2).
4. **Position caps are respected**: construct a scenario with a very deep
   single-position pool (e.g. 20 available RBs) and a small RB cap;
   assert the plan never contains more RBs than the cap allows.
5. **Dedicated slots hold the correct position**: for every `"dedicated"`
   type entry, `eligible_position` matches the slot's `category`.
6. **The swap pass fixes the "expensive stud blocks two good WRs" case**
   — the key regression test given how this design arrived at Phase 2:
   construct a small available pool where greedy-alone (Phase 1 only,
   called directly without `_refine`) picks one high-VOR/high-price
   player that prevents affording two other players whose combined VOR
   is higher for less-or-equal combined price; assert the *refined* plan
   contains the higher-total-VOR pair instead.
7. **A no-improvement case leaves the plan unchanged**: construct a
   scenario where Phase 1's output is already locally optimal (no legal
   swap improves total VOR); assert `_refine` returns the same slot
   assignments Phase 1 produced.
8. **API wiring**: `tests/api/test_board.py` gets one new test asserting
   `build_auction_board()`'s payload includes a top-level `"optimal_plan"`
   key shaped per §7, for a small constructed league/roster.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Swap search misses an improving swap outside its `CANDIDATES_PER_SLOT` window | Bounded by design (§1.2 non-goals: not provably optimal); window is VOR-ranked, so the highest-value missed swaps are the least likely to be missed |
| `_refine`'s O(slots² × CANDIDATES_PER_SLOT²) search is too slow on a very large roster | Roster sizes in this domain are small (~15-20 slots); `MAX_SWAP_ITERATIONS` bounds worst case regardless |
| `compute_roster_needs()` extraction subtly changes `positional_budget()`'s behavior | §9 item 1 -- full existing test suite for that function is the safety net, run before/after |
| A league genuinely thin enough to violate the "always filled" invariant (§1.2) | Treated as a real bug if it happens (assert, don't silently degrade) rather than designed around, since it shouldn't occur given real player-pool depth |
