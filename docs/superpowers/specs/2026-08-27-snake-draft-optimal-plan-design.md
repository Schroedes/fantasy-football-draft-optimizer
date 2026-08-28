# Snake Draft Optimal Plan — Design

**Date:** 2026-08-27
**Status:** Approved, pending implementation
**Prior art:** `docs/superpowers/specs/2026-08-24-optimal-roster-plan-design.md`
(the auction version of this idea — budget-constrained instead of
pick-constrained); `src/ffdo/engine/market.py` (the existing survival
simulation this feature extends); `src/ffdo/engine/roster.py` (the
existing exact lineup-fit functions this feature reuses for final
scoring, and approximates for in-loop scoring).

## 1. Purpose

The snake board already shows, per player, a survival probability and a
per-position "cost of waiting" — but both use one flat, non-team-specific
horizon (`picks_until = league.num_teams`, i.e. "one full round," the
same number for every team and every future pick). Neither answers the
question a drafter actually has mid-draft: *given my specific remaining
picks, and everyone else drafting in between, what's my best achievable
team, and what should I expect to take at each of my future turns?*

This feature rolls the rest of the draft forward via simulation —
reusing the same Gumbel-max sampling `market.simulate_survival` already
uses — to estimate, for each of your remaining picks, the position and
player you're most likely to end up drafting, plus your team's expected
final starting-lineup value.

### 1.1 Goals

- Derive the actual sequence of pick numbers that belong to your roster
  for the rest of the draft, from the real snake order (not a flat
  one-round approximation).
- Simulate the whole remaining draft forward many times: at every one of
  your own future picks, choose greedily by a cheap need-weighted VOR
  heuristic; every stretch of opponent picks in between is removed in
  one batched Gumbel-max draw, reusing `market.py`'s existing mechanism
  rather than inventing new sampling logic.
- Report, per future pick: the most-likely position and specific player
  across simulations, with each one's hit rate (fraction of simulations
  that landed on it).
- Report your team's expected final starting-lineup VOR — scored with
  the *exact* `roster.team_lineup()` function once per simulated trial,
  not the cheap in-loop heuristic, so this one headline number stays as
  trustworthy as the auction feature's `total_plan_vor`.
- Fit inside the existing snake board's heavy-refresh cadence (the same
  `/api/board` call the rest of the snake payload already rides), *if*
  benchmarking against real data during implementation shows a
  statistically useful simulation count (~100-200+) fits in that budget
  — see §7. This is a target, not an unconditional commitment.

### 1.2 Non-goals

- The exact `roster.marginal_lineup_values()` function inside the
  simulation's inner loop. It re-fits your whole lineup per candidate;
  running it at every one of your ~12-15 future picks, in every one of
  hundreds of simulated trials, was estimated (before this design's
  batched-removal optimization) at multiple seconds *per trial* — clearly
  incompatible with any live-refresh budget. §4 covers the cheap
  approximation used instead, and why the final score stays exact anyway.
- A result before your first actual pick of the draft. There's no
  reliable signal for which seat (`draft_slot`) is yours until you've
  made at least one pick — every existing `DraftPick` already carries
  `draft_slot`, but before your first pick none of them are yours to
  read it from. The feature returns `None` in that window rather than
  guessing; the panel simply doesn't render yet (§5, §6).
- Modeling keeper picks, trades, or any non-standard snake order. Assumes
  a standard alternating snake (round 1: slot 1→N, round 2: N→1,
  alternating) and one pick per team per round — true for every league
  this app currently ingests (Sleeper, ESPN, mock drafts).
- Any change to `cost_of_waiting`'s existing flat one-round horizon or
  the position-level cost-of-waiting panel. This is a new, separate
  signal — not a replacement.
- A UI trigger button. Per discussion, this runs automatically on the
  existing heavy-refresh cadence, contingent on §7's benchmark. If that
  benchmark forces a lower simulation count than is statistically useful,
  the fallback is documented in §7, not a silent quality regression.

## 2. Your future pick sequence — new snake pick-order math

No existing code derives "which pick numbers are mine, for the rest of
the draft" (confirmed absent from `engine/`, `ingest/`, and `domain/` —
the only existing `picks_until` is the flat constant in `app.py:481`).
Each `DraftPick` (`domain/models.py`) already carries `draft_slot`, and
`ingest/mock_draft.py`'s `slot_to_roster_id` confirms `draft_slot` is
the stable "seat" a roster occupies for the whole draft — so your seat
can be read directly off any one of your own already-made picks:

```python
def _your_draft_slot(state: DraftState, roster_id: int | None) -> int | None:
    """Your seat, read off any pick you've already made. None if you
    haven't picked yet (or roster_id is unset) -- there's no other
    signal for which seat is yours before that."""
    if roster_id is None:
        return None
    return next((p.draft_slot for p in state.picks if p.roster_id == roster_id), None)
```

Given a seat and the total team count, the pick number for any round
follows the standard snake formula (round 1 goes seat 1→N, round 2
reverses N→1, alternating every round):

```python
def _pick_no_for(round_no: int, draft_slot: int, num_teams: int) -> int:
    pick_in_round = draft_slot if round_no % 2 == 1 else num_teams - draft_slot + 1
    return (round_no - 1) * num_teams + pick_in_round


def _slot_for_pick(pick_no: int, num_teams: int) -> int:
    round_no = (pick_no - 1) // num_teams + 1
    pick_in_round = (pick_no - 1) % num_teams + 1
    return pick_in_round if round_no % 2 == 1 else num_teams - pick_in_round + 1
```

Your remaining pick numbers, for `your_picks_made` already made out of
`league.roster_size` total rounds:

```python
your_future_pick_nos = [
    _pick_no_for(r, your_draft_slot, league.num_teams)
    for r in range(your_picks_made + 1, league.roster_size + 1)
]
```

This is a well-established, near-universal convention (Sleeper, ESPN,
and every mock-draft source this app ingests all use it) — not the kind
of undocumented-API uncertainty the ESPN integration had to verify live.
Still, as a cheap sanity check worth doing during implementation: replay
a real completed draft's picks and confirm `_slot_for_pick(p.pick_no,
league.num_teams) == p.draft_slot` for every real `DraftPick` in a
fixture, rather than trusting the formula on faith alone.

## 3. Shared Gumbel-max primitive — extract from `simulate_survival`

`simulate_survival` (`market.py:18-52`) already does, once per simulated
trial: draw Gumbel noise, perturb ADP-derived logits, take the top-`k`
as "gone." The rollout in §4 needs the exact same single-draw operation,
called *multiple times per trial* (once per gap between two of your
picks) against a *shrinking* pool — rather than once per trial against a
fixed pool. Extract it as a small, behavior-preserving refactor:

```python
def _gone_this_stretch(
    ids: Sequence[str], adp: Mapping[str, float], take: int,
    tau: float, rng: np.random.Generator,
) -> frozenset[str]:
    """One Gumbel-max draw: up to `take` ids removed from `ids`, weighted
    by ADP (lower ADP -> more desirable -> more likely to be taken).
    Ids absent from `adp` are never drawn as "gone" -- same limitation
    `simulate_survival` already has, not new here."""
    eligible = [pid for pid in ids if pid in adp]
    if take <= 0 or not eligible:
        return frozenset()
    take = min(take, len(eligible))
    logits = np.array([-adp[pid] / tau for pid in eligible])
    gumbel = rng.gumbel(size=len(eligible))
    gone_idx = np.argpartition(-(logits + gumbel), take - 1)[:take]
    return frozenset(eligible[i] for i in gone_idx)
```

`simulate_survival` is rewritten to call this once per `sims` iteration
in place of its current inline body — same computation, same result,
verified by its own existing test suite passing unchanged (same
discipline as `compute_roster_needs()`'s extraction in the auction
feature: a pure relocation, not a behavior change).

## 4. The cheap in-loop pick heuristic

Running `roster.marginal_lineup_values()` per candidate, per your-pick,
per trial is the one thing this design explicitly avoids (§1.2). Instead,
after each of your simulated picks, recompute a cheap per-position
weight from your simulated roster so far, mirroring the same
dedicated→flex→bench priority `position_caps()` already established for
the auction feature, without re-running a full lineup fit:

**DEF/K note (added after this spec's initial approval):** a separate
branch shipped scoring and VOR support for team-DEF and K positions
(`is_defense_scoring_key`/`is_kicking_scoring_key` in
`domain/constants.py`, and `vor.compute()`'s own test suite proves it
needed zero engine changes — VOR is already derived generically from
`league.roster_positions` and whatever positions appear in the scored
pool). That means `valued` — and therefore this rollout's candidate
pool — now genuinely contains DEF/K players with real VOR whenever a
connected league rosters them. `_need_weights` below is written against
`league.roster_positions` directly rather than hardcoded to
`OFFENSE_POSITIONS`, specifically so DEF/K (and any other literal
dedicated slot type a league might roster) get weighted the same way a
real offense position does — never flex-eligible (matching real fantasy
rules; they never appear in `FLEX_ELIGIBILITY`'s value sets), but fully
weighted while their own dedicated slot is open, same as QB/RB/WR/TE:

```python
def _need_weights(sim_roster: Mapping[str, ValuedPlayer], league) -> dict[str, float]:
    """Cheap stand-in for "do I still need this position": full weight
    while a dedicated starting slot is open, reduced weight once only
    FLEX-eligible room remains, low (bench-only) weight otherwise. Covers
    every position this league actually rosters -- not hardcoded to
    OFFENSE_POSITIONS -- so DEF/K get weighted the same way a real
    offense position does, rather than being silently unpickable. Not a
    replacement for marginal_lineup_values -- that still scores each
    trial's FINAL roster (see _to_trial_result in Sec. 5); this only
    steers the in-simulation pick, where the exact version is too
    expensive to run at every pick of every trial.
    """
    pos_counts: dict[str, int] = {}
    for vp in sim_roster.values():
        pos_counts[vp.profile.position] = pos_counts.get(vp.profile.position, 0) + 1

    # Every literal position this league has a dedicated slot for --
    # OFFENSE_POSITIONS is always included even at zero dedicated slots
    # (e.g. a league with no dedicated QB slot, QB only via superflex),
    # so those positions still get a real bench-tier weight instead of
    # being absent from the dict entirely.
    dedicated_positions = frozenset(
        slot for slot in league.roster_positions
        if slot not in FLEX_ELIGIBILITY and slot != "BN"
    ) | OFFENSE_POSITIONS
    dedicated_counts = {pos: league.roster_positions.count(pos) for pos in dedicated_positions}

    flex_positions = frozenset(
        pos for slot in league.roster_positions if slot in FLEX_ELIGIBILITY
        for pos in FLEX_ELIGIBILITY[slot]
    )
    flex_total = sum(1 for slot in league.roster_positions if slot in FLEX_ELIGIBILITY)
    flex_used = sum(max(0, pos_counts.get(pos, 0) - dedicated_counts.get(pos, 0))
                    for pos in flex_positions)
    flex_open = flex_total - flex_used

    weights: dict[str, float] = {}
    for pos in dedicated_positions:
        dedicated_open = dedicated_counts[pos] - min(pos_counts.get(pos, 0), dedicated_counts[pos])
        if dedicated_open > 0:
            weights[pos] = 1.0
        elif pos in flex_positions and flex_open > 0:
            weights[pos] = 0.85
        else:
            weights[pos] = 0.15
    return weights
```

A position entirely absent from this dict (never rostered by the
league at all) still resolves safely at the call site via
`weights.get(position, 0.0)` — correctly zero, since there's no slot for
it anywhere.

The in-simulation pick is then `argmax` over currently-available
candidates of `vp.vor * weights[vp.profile.position]` — an `O(available)`
scan, no per-candidate lineup re-fit.

## 5. The rollout — `src/ffdo/engine/snake_plan.py`

New module, mirroring `planner.py`'s role for the auction feature:

```python
def simulate_snake_plan(
    valued: Mapping[str, ValuedPlayer],
    adp: Mapping[str, float],
    state: DraftState,
    league,
    roster_id: int | None,
    *,
    sims: int = 200,  # placeholder -- tuned empirically per Sec. 7
    tau: float = 8.0,
    rng: np.random.Generator | None = None,
) -> dict | None:
    """Roll the rest of the draft forward `sims` times. At each of YOUR
    future picks, take the cheap need-weighted-VOR choice (Sec. 4); each
    stretch of opponent picks in between is removed in one batched
    Gumbel-max draw (Sec. 3), the same mechanism `simulate_survival`
    already uses. Returns None if your draft slot can't be determined
    yet (Sec. 1.2) -- no result before your first real pick.
    """
    your_draft_slot = _your_draft_slot(state, roster_id)
    if your_draft_slot is None:
        return None

    rng = rng or np.random.default_rng()
    your_picks_made = sum(1 for p in state.picks if p.roster_id == roster_id)
    your_future_pick_nos = [
        _pick_no_for(r, your_draft_slot, league.num_teams)
        for r in range(your_picks_made + 1, league.roster_size + 1)
    ]
    if not your_future_pick_nos:
        return {"picks": [], "expected_starting_vor": _current_starting_vor(state, valued, league, roster_id),
                "sims_run": 0}

    drafted = state.drafted_player_ids()
    available_ids = [pid for pid in valued if pid not in drafted]
    your_current_roster = {p.player_id: valued[p.player_id] for p in state.picks
                           if p.roster_id == roster_id and p.player_id in valued}
    next_pick_no = max((p.pick_no for p in state.picks), default=0) + 1

    position_tallies = [Counter() for _ in your_future_pick_nos]
    player_tallies = [Counter() for _ in your_future_pick_nos]
    final_vors: list[float] = []

    for _ in range(sims):
        sim_available = set(available_ids)
        sim_roster = dict(your_current_roster)
        cursor = next_pick_no

        for i, your_pick_no in enumerate(your_future_pick_nos):
            gap = your_pick_no - cursor
            if gap > 0:
                gone = _gone_this_stretch(list(sim_available), adp, gap, tau, rng)
                sim_available -= gone
            if not sim_available:
                break

            weights = _need_weights(sim_roster, league)
            best_id = max(
                sim_available,
                key=lambda pid: valued[pid].vor * weights.get(valued[pid].profile.position, 0.0),
            )
            sim_roster[best_id] = valued[best_id]
            sim_available.discard(best_id)
            position_tallies[i][valued[best_id].profile.position] += 1
            player_tallies[i][best_id] += 1
            cursor = your_pick_no + 1

        final_vors.append(roster_engine.team_lineup(sim_roster, league).starting_vor)

    picks = []
    for i, pick_no in enumerate(your_future_pick_nos):
        if not position_tallies[i]:
            continue  # this pick slot was never reached in any trial (pool exhausted)
        top_pos, pos_n = position_tallies[i].most_common(1)[0]
        top_player, player_n = player_tallies[i].most_common(1)[0]
        picks.append({
            "pick_no": pick_no,
            "picks_from_now": i + 1,
            "most_likely_position": top_pos,
            "position_hit_rate": round(pos_n / sims, 3),
            "most_likely_player_id": top_player,
            "most_likely_player_name": valued[top_player].profile.full_name,
            "player_hit_rate": round(player_n / sims, 3),
        })

    return {
        "picks": picks,
        "expected_starting_vor": round(sum(final_vors) / len(final_vors), 1) if final_vors else 0.0,
        "sims_run": sims,
    }
```

(`_current_starting_vor` for the zero-future-picks edge case, and the
exact `Counter`/typing imports, are implementation detail the plan will
spell out in full — not left as prose.)

## 6. Output shape and wiring

```python
{
  "picks": [
    {"pick_no": 47, "picks_from_now": 3, "most_likely_position": "RB",
     "position_hit_rate": 0.62, "most_likely_player_id": "...",
     "most_likely_player_name": "...", "player_hit_rate": 0.31},
    # ... one entry per remaining pick, or fewer if pool exhaustion cut a trial short
  ],
  "expected_starting_vor": 812.4,
  "sims_run": 200,
}
```
The payload's `"snake_plan"` key is always present, but its value is
`null` before your first real pick (when `simulate_snake_plan()` returns
`None`) — unlike the auction Optimal Plan feature, which omits its key
entirely when there's nothing to show, this one is deliberately always
present so the frontend can distinguish "not available yet" from "key
missing" with a single `if (!plan)` check either way, without needing to
special-case `in`-checks. A deliberate difference from the auction
precedent, not an inconsistency with it.

**Wiring follows the existing `survival`/`cost_of_waiting` pattern** —
both are already computed in `api/app.py` (not inside `build_snake_board`
itself) and passed in as arguments. `snake_plan` follows the same shape:
`app.py` (near `app.py:478-486`) gains

```python
snake_plan = snake_plan_mod.simulate_snake_plan(valued, adp_means, state, lg, roster_id)
board = board_mod.build_snake_board(
    lg, state, valued, survival, cow, snake_plan, roster_id=roster_id, teams=teams)
```

and `build_snake_board()` (`api/board.py:235-292`) gains a
`snake_plan: dict | None` parameter, added to the returned dict as a new
top-level `"snake_plan"` key (sibling to `"cost_of_waiting"`).
`adp_means` is already computed at `app.py:479-480` for the existing
survival call — no new data-fetching needed, just reused.

## 7. Performance validation — required before finalizing `sims`/cadence

This is a required implementation step, not optional polish. Before
committing to running this on the 3-second heavy-refresh cadence:

1. Benchmark `simulate_snake_plan` against real fixture data (a
   real mid-draft `DraftState`, real `valued`/`adp` from a committed
   snapshot) at a candidate `sims` value, measuring wall-clock time.
2. If a `sims` value that gives statistically meaningful hit rates
   (~100-200+ trials — below that, a "62%" position hit-rate is mostly
   noise) fits comfortably inside the existing heavy-refresh budget
   alongside everything else `/api/board` already computes, ship it on
   that cadence, per §1.1's goal.
3. **If it doesn't fit**: fall back to the on-demand-button design
   discussed and set aside during brainstorming (a dedicated endpoint,
   button-triggered, targeting a 10-15 second response) rather than
   shipping a panel that's either too slow for the live poll cycle or
   too noisy (low `sims`) to trust. This fallback is pre-approved, not a
   new design decision to revisit if the benchmark forces the issue.

## 8. UI

New sidebar card in `src/ffdo/web/board/index.html`, following the same
`<aside>` pattern as `#rosters`/`#history`/`#optimal-plan`, snake-only:

```html
<aside id="snake-plan">
  <div class="snakeplan-head">
    <h2>Draft plan</h2>
    <span class="snakeplan-sub">most likely pick at each of your remaining turns, simulated forward</span>
  </div>
  <div class="snakeplan-totals">
    <span><b id="snakeplan-vor">&mdash;</b> expected starting VOR</span>
  </div>
  <div id="snakeplan-rows"></div>
</aside>
```

New `renderSnakePlan()` in `board.js`, called from `render()` alongside
`renderCow()` — same "hidden when not snake format or data missing"
guard those already use. Each row: pick number / picks-from-now, most
likely position + player name (escaped via the file's existing
`escapeHtml()` helper — the auction panel's final-review fix already
established this convention, apply it here from the start rather than
needing a follow-up fix), and both hit-rate percentages.

## 9. Testing

`tests/engine/test_snake_plan.py` (new file), plus targeted additions
elsewhere:

1. **Pick-order math**: `_pick_no_for`/`_slot_for_pick` round-trip
   correctly for several `num_teams`/`draft_slot`/`round` combinations,
   including the odd/even reversal boundary.
2. **`_gone_this_stretch` extraction is behavior-preserving**: every
   existing `simulate_survival` test in `tests/engine/test_market.py`
   passes unchanged after the refactor.
3. **`_need_weights`**: constructed roster scenarios assert `1.0` for an
   open dedicated slot, `0.85` for flex-only-open, `0.15` once fully
   staffed at that position -- including at least one DEF/K scenario,
   not just QB/RB/WR/TE, proving the position universe is genuinely
   derived from `league.roster_positions` and not silently limited to
   `OFFENSE_POSITIONS`.
3a. **DEF/K are actually pickable**: a scenario where a kicker (or
   defense) is your only remaining real need must result in the rollout
   drafting one -- the regression test for the exact gap this note (§4)
   exists to close. Without it, DEF/K would score `vor * 0.0` forever
   and never win the in-simulation argmax, even in a league that
   requires them.
4. **`simulate_snake_plan` returns `None`** before your first real pick
   (no `DraftPick` with your `roster_id` yet).
5. **Determinism with a seeded `rng`**: same seed, same inputs, same
   output — required for the tests below to assert exact values rather
   than statistical ranges.
6. **Output shape**: one `picks` entry per remaining pick (given a
   pool deep enough that no trial exhausts it), hit rates in `[0, 1]`,
   `sims_run` matches the parameter.
7. **The heuristic actually helps**: a constructed scenario where
   need-weighting changes the outcome versus a naive "always highest raw
   VOR" policy — e.g. a roster already stacked at one position, with a
   clearly-better-by-need alternative available — asserting
   `expected_starting_vor` under `_need_weights` beats the naive policy.
   This is the same "prove the mechanism helps, don't just assert it
   runs" discipline the auction feature's swap-refinement regression
   test already established.
8. **API wiring**: `build_snake_board()`'s `"snake_plan"` key equals
   whatever was passed in, for both a real dict and `None` -- the key
   itself is always present either way (§6).

## 10. Risks

| Risk | Mitigation |
|---|---|
| Simulation too slow for the 3-second cadence at a useful `sims` count | §7's benchmark-first requirement, with a pre-approved on-demand-button fallback already specified, not left to improvise later |
| `_need_weights`' cheap heuristic diverges meaningfully from what `marginal_lineup_values` would actually pick, skewing the reported hit rates | Mitigated by scope: the heuristic only drives in-simulation choices; the headline `expected_starting_vor` is still scored by the exact function once per trial. Test 7 (§9) locks in that the heuristic is directionally correct, not just "runs" |
| Players absent from `adp` are never removed by simulated opponent picks, understating how contested they are | Inherited limitation from `simulate_survival`'s existing design (§3) — not new, not silently different from the survival numbers already shown elsewhere on the board |
| A league with a non-standard snake order (not currently supported by any real ingest path) silently produces a wrong pick sequence | Explicitly out of scope (§1.2); the formula matches every provider this app currently ingests |
| Pool exhaustion deep in a very long/deep draft leaves some trials shorter than others | Handled per-slot: a pick-slot index with zero tallies across all trials is omitted from the output (§5) rather than reported with a misleading 0% hit rate |
| DEF/K (added to the app after this spec's initial approval) never get drafted by the rollout because the pick heuristic doesn't recognize their position | §4's `_need_weights` is derived from `league.roster_positions` directly rather than hardcoded to `OFFENSE_POSITIONS`, specifically to close this gap; §9 test 3a is the regression test proving it |
