# Trade Calculator + Decision Ledger — Design Spec

Sub-project #5 of the multi-league season companion initiative (stacked on
[#28](https://github.com/Schroedes/fantasy-football-draft-optimizer/pull/28),
sub-project #4). Prior sub-projects: #1 foundation, #2 roster/standings, #3
weekly lineup, #4 player valuation (real dynasty age curve, durability
backtest). This sub-project reuses #4's valuation engine (`ros_value`,
`ValuedPlayer.vor`) directly rather than inventing a new value scale.

## §0. Scope decision, made during brainstorming

The original 7-part decomposition names sub-project #5 as "Trade calculator
(+decision ledger)." During brainstorming, the user confirmed wanting three
capabilities: (a) evaluate a hypothetical trade, (b) detect and grade real
trades in a decision ledger, (c) proactively suggest trade targets based on
roster needs. (c) is materially different engine work (positional-need /
scarcity modeling across a whole league) from (a)/(b) (valuing a
already-specified set of assets on both sides), so it is **deferred to its
own future sub-project** and is out of scope here. This spec covers (a) and
(b) only.

## §1. Goals

1.1. Let a user build a hypothetical trade (their roster's players/picks for
     a chosen partner roster's players/picks) and see both sides' value on
     the same scale #4 already established (redraft/keeper VOR, dynasty
     annuity VOR) — no new player-valuation logic.
1.2. Give a future or already-owned draft pick a comparable value on that
     same scale, fit from real data, so it can appear in a trade alongside
     players — nothing in the codebase currently does this (draft picks are
     tracked only as ownership/ranking metadata, never valued).
1.3. Detect every real, completed trade in a tracked league (not just ones
     the tracked user is party to) and record a permanent decision-ledger
     entry for each, showing its value at the moment it happened and its
     value as of right now, continuously.

## §2. Non-goals

- **Trade-target suggestions** (§0) — a future sub-project.
- **Roster-context-aware fairness** (e.g. "Team B already has 3 good WRs, so
  this gain is worth less to them") — needs the same roster-needs modeling
  as trade-target suggestions; deferred with it.
- **A qualitative fairness verdict** ("fair" / "lopsided") with invented
  thresholds. Phase 1 shows the raw value math; inventing an arbitrary
  bucket boundary would repeat exactly the kind of ungrounded guess #4
  moved away from for the age curve.
- **Real NFL draft capital as a pick-value input.** Verified during
  brainstorming: Sleeper's player data has no real-NFL-draft-round/pick
  field at all (checked directly against real rookies). Pick value is
  fit from the user's own leagues' actual fantasy rookie-draft results
  instead (§4).
- **Multi-season trade tracking.** A trade's "current value" (§6) tracks
  realized production only through the end of the season the trade happened
  in. Beyond that season, a player's value blends into ordinary
  roster-value/aging dynamics no longer specifically attributable to the
  trade. Longer-horizon dynasty trade tracking is a future improvement
  (§10), not attempted here.
- **Cap space / contract modeling, team-context (QB situation, rebuild
  trajectory) modeling.** Same non-goals #4 already deferred for player
  valuation; a trade calculator inherits them unchanged.

## §3. Reused from #4, unchanged

- `engine.ros_value.roster_value(...) -> dict[str, ValuedPlayer]` — the
  source of every player's value in a trade. Already format-aware
  (redraft/keeper vs. dynasty) and already computed for the whole league's
  rosters during `_assemble_season`, so a trade endpoint can read
  already-known `ValuedPlayer.vor` values rather than recomputing anything.
- `domain.models.DraftPickAsset` (`season`, `round`, `projected_slot`,
  `current_owner_roster_id`, `original_roster_id`, `via_team_name`) — the
  existing pick-ownership model; §4 adds a **value**, it does not change
  this model's ownership/tracking role.
- `ingest.sleeper.traded_picks.capital(...)` — unchanged; still the source
  of which picks exist and who owns them.

## §4. Pick valuation

### §4.1. Why round-only isn't enough, and why real NFL draft data isn't available

Verified live against Sleeper's API during brainstorming: the user's two
tracked dynasty leagues (`GDK`, `Room Temp IQ`) both run **annual rookie-only
drafts** (a `linear`-type draft, distinct from the one-time `snake`/`auction`
startup draft), reachable by walking each league's `previous_league_id`
chain back through prior seasons. This means real
`(round, pick_in_round) -> player actually selected -> that player's real
subsequent production` data exists — genuinely finer than round-level, even
though the total sample (two leagues, two graded rookie classes each, one
full season of production apiece) is modest — roughly 70-120 picks with
realized production, far thinner than the age curve's multi-year, full-
league sample.

### §4.2. Fitting procedure

New offline script `scripts/fit_pick_value.py`, mirroring
`scripts/fit_age_curve.py`'s pattern exactly: takes a snapshot directory
(plus the tracked leagues to walk), prints a Python literal for manual
review, **never** auto-writes the checked-in constant. Steps:

1. For each tracked dynasty/keeper league, walk `previous_league_id` back
   through every prior season. For each season, fetch that league's drafts
   (`GET /league/{id}/drafts`) and keep only `linear`-type drafts old enough
   that at least one season of subsequent production exists for their
   picks — this is how a rookie draft is distinguished from a one-time
   startup draft (which is `snake` or `auction`).
2. For each pick in a kept draft, fetch `GET /draft/{draft_id}/picks` to
   resolve which real player was selected at that `(round, pick_no)`.
3. Look up that player's realized production (VOR, via the same historical
   stats/scoring pipeline #4 already built) for the season(s) immediately
   following the draft.
4. Group observations into three tiers, from most to least precise:
   - **Exact slot**: `(round, pick_no)` — used when this exact slot has at
     least `MIN_SAMPLE = 3` observations.
   - **Tertile within round**: split each round into early/mid/late thirds,
     scaled to that round's actual pick count (e.g. a 12-pick round splits
     roughly 1-4 / 5-8 / 9-12; a 10-pick round roughly 1-3 / 4-7 / 8-10) —
     used when the exact slot has fewer than `MIN_SAMPLE` observations but
     the tertile's combined observations meet `MIN_SAMPLE`.
   - **Round average**: used when even the tertile falls short of
     `MIN_SAMPLE`.
5. Print the result as a literal: `{round: {tier_key: value}}` where
   `tier_key` is either an exact `pick_no`, a tertile label
   (`"early"`/`"mid"`/`"late"`), or absent (round-level fallback only, keyed
   directly under the round).

`MIN_SAMPLE = 3` is a controller judgment call, not backtested (there is no
larger ground truth to validate it against, the same honest position #4's
`DISCOUNT_RATE` was documented with) — chosen because 1-2 data points is
indistinguishable from noise, while requiring more than 3 would leave almost
every tier falling through to round-average given this sample's size.

### §4.3. The checked-in constant and lookup

`domain/constants.py::PICK_VALUE_CURVE: Final[dict[int, dict]]`, structured
per round as `{round: {"exact": {pick_no: value}, "early": value,
"mid": value, "late": value, "round_avg": value}}` — every tier that has a
value populates its key; a tier with insufficient data is simply absent, and
lookup falls through slot → tertile → round average → (if the round itself
is entirely absent from the curve) `0.0`, mirroring the age curve's existing
"no data, no adjustment" convention.

`engine/pick_value.py::slot_value(pick: DraftPickAsset, curve: Mapping, *,
current_season: int, round_size: int,
discount_rate: float = PICK_UNCERTAINTY_DISCOUNT_RATE) -> float`.
`round_size` (the target league's own number of rookie-draft picks per
round, e.g. `league.num_teams`) is required at lookup time, separately from
whatever round sizes the historical drafts used at fit time (§4.2) —
tertile boundaries are always computed against the league the pick actually
belongs to, not the leagues the curve was fit from:

```python
def slot_value(pick, curve, *, current_season, round_size,
                discount_rate=PICK_UNCERTAINTY_DISCOUNT_RATE):
    round_data = curve.get(pick.round, {})
    base = None
    if pick.projected_slot is not None:
        base = round_data.get("exact", {}).get(pick.projected_slot)
        if base is None:
            tertile = _tertile_for(pick.projected_slot, round_size)
            base = round_data.get(tertile)
    if base is None:
        base = round_data.get("round_avg", 0.0)
    years_out = int(pick.season) - current_season
    return base / (1.0 + discount_rate) ** max(0, years_out)
```

`PICK_UNCERTAINTY_DISCOUNT_RATE: Final[float] = 0.20` (per year) — a
separate constant from #4's `dynasty_value.DISCOUNT_RATE` (0.15). The two
discount different things: `DISCOUNT_RATE` discounts a *known* player's
future production (time value + normal aging uncertainty); this constant
discounts *not knowing who the pick even becomes yet* — a materially larger
source of uncertainty for a pick more than a year out, hence the higher
default. Also not backtested (no ground truth exists for "value assigned at
trade time" vs. "value once realized" to validate against) — documented as
a controller judgment call, same honest posture as `DISCOUNT_RATE`.

## §5. Trade evaluator

`engine/trade_value.py::evaluate_trade(side_a, side_b, *, league, valued_players, pick_curve, current_season) -> TradeEvaluation`

Where `side_a`/`side_b` are each `{"player_ids": Sequence[str], "picks":
Sequence[DraftPickAsset]}`. For each side:

```
side_value = sum(valued_players[pid].vor for pid in side.player_ids
                  if pid in valued_players)
           + sum(pick_value.slot_value(pick, pick_curve, current_season=current_season)
                  for pick in side.picks)
```

`TradeEvaluation` carries `side_a_value`, `side_b_value`,
`differential = side_a_value - side_b_value`, and
`differential_pct = differential / min(side_a_value, side_b_value)` (guarded
against division by zero: `None` if either side's value is `0.0`). No
fairness label — the raw numbers are the entire output (§2).

For a pure redraft league, `side.picks` is always empty (redraft has no
persistent picks) — this degenerates cleanly to players-only, no
special-casing needed.

## §6. Real-trade decision ledger

### §6.1. Detection

New `ingest/sleeper/transactions.py::fetch_trades(sleeper, league_id, through_week) -> list[TradeTransaction]`.
Verified live against Sleeper's API during brainstorming:
`GET /league/{id}/transactions/{week}` returns, among other transaction
types, `type == "trade"` records with `status` (`"complete"` for trades that
actually happened), `roster_ids` (both parties), `adds`/`drops` (player_id →
roster_id, both directions), `draft_picks` (each with `round`, `season`,
`roster_id`/`owner_id`/`previous_owner_id`), a stable `transaction_id`, and
`created`/`status_updated` timestamps — everything the ledger needs, with no
gaps.

`TradeTransaction` (new domain model): `transaction_id: str`, `season: int`,
`week: int`, `roster_a_id: int`, `roster_b_id: int`,
`roster_a_gets: list[str]` (player_ids), `roster_b_gets: list[str]`,
`picks_to_a: list[DraftPickAsset]`, `picks_to_b: list[DraftPickAsset]`,
`traded_at: datetime` (from `created`).

Detection is opportunistic, the same way the lineup ledger is: it runs when
a user loads the Trades tab, scanning transactions for the current season
(all weeks up to the current week) and recording any `transaction_id` not
already present. **Every completed trade in the league is recorded**, not
only ones the tracked user is party to — this was an explicit requirement:
the ledger shows the whole league's trade history, not just the tracked
user's own trades.

### §6.2. Recording

New `api/trade_ledger.py`, following `lineup_ledger.py`'s pattern (stdlib
`sqlite3`, `data/ffdo.db`, no ORM, corrupt-DB tolerance = treat as empty).
One table, `trade_record`, PK `(league_key, transaction_id)`. On first
observation of a new `transaction_id`:

- Snapshot each involved player's **current season-cumulative banked
  points** (the same `actuals` value `ros_value.roster_value` already
  reads) at recording time — this is the baseline the "current value" in
  §6.3 subtracts against.
- Snapshot the trade's value via `trade_value.evaluate_trade(...)` at that
  moment (both sides, using the pick curve as of trade time).
- Store `traded_at`, `season`, both rosters' player/pick lists, the
  trade-time per-side values, and the per-player banked-points-at-trade
  snapshot (needed for §6.3 forever, so must never be overwritten once
  written — write-once, exactly like the lineup ledger's
  `record_if_absent`).

### §6.3. Continuous "current value" (not a one-time resolution)

Rather than a one-time "resolve at season end" event, every ledger entry
computes a **current value** live, every time it's read:

- **Players**: `current_banked - banked_at_trade_snapshot`, per player, for
  each player still attributable to that trade — the realized production
  gained by each side since the trade, which keeps changing week to week
  and naturally stops changing once that season's games are done (§2's
  non-goal: not tracked into a later season).
- **Picks**: re-run `pick_value.slot_value(...)` with **today's** inputs
  (today's `projected_slot` if the draft has drawn closer, `current_season`
  for the discount) rather than the trade-time snapshot — a future pick's
  value legitimately drifts as time passes and its landing spot becomes
  clearer, even before it is actually used in a real rookie draft.

There is no `resolved`/`pending` flag. A pick-involving trade's player
portion still updates normally; only the pick portion is inherently
"current as of today's best estimate" forever, since a pick has no realized
outcome until a real player is attached to it in a future draft (out of
scope to track that conversion — §10).

### §6.4. Display

The ledger view is a **chronological list** of every trade in the league
(not a ranked leaderboard — considered and explicitly declined during
brainstorming), each entry showing: both rosters, what each side gave up,
trade-time value for both sides, and current value for both sides,
recomputed on every page load.

## §7. API

- `POST /api/leagues/{league_key}/trade/evaluate` — hypothetical evaluator.
  Body: `{partner_roster_id: int, side_a: {player_ids: [...], picks: [...]},
  side_b: {player_ids: [...], picks: [...]}}` where side A is always the
  tracked user's roster (§2: not opened to arbitrary two-team hypotheticals
  — considered and explicitly declined during brainstorming). Response:
  `TradeEvaluation` as JSON.
- `GET /api/leagues/{league_key}/trades` — the ledger. Triggers detection
  (§6.1) as a side effect of being called, then returns every recorded
  trade for the league with trade-time and current values.

## §8. Frontend

New **Trades** tab in `web/season/season.js`, alongside the existing
Lineup/Power ranking/Draft capital tabs, following the same
`data-panel-tab` + `render()`-switch pattern #3 already established. Two
sections: a trade builder (pick players/picks from your roster and a chosen
partner roster, live value totals via the evaluate endpoint as you build)
and the ledger list below it.

## §9. Testing approach

- `scripts/fit_pick_value.py` run for real against the user's actual league
  history (same posture as `fit_age_curve.py` — a real run producing real
  output, reviewed before being committed to `PICK_VALUE_CURVE`).
- `engine/pick_value.py`, `engine/trade_value.py`: unit tests with synthetic
  curves, covering the slot → tertile → round-average fallback chain
  explicitly (a curve populated at only one tier per test case), the
  discount formula's `years_out` behavior (0, 1, and multi-year-out cases),
  and — learning directly from #4's post-mortem — **at least one test that
  checks real magnitude against a real fitted curve's actual values, not
  only direction/bounds against a synthetic curve**, since that gap is
  exactly what let two real bugs into #4's shipped formula undetected until
  the final whole-branch review.
- `api/trade_ledger.py`: unit tests following `lineup_ledger.py`'s existing
  test patterns (write-once idempotency, corrupt-DB tolerance).
- Manual browser smoke test against a real dynasty league (the same `GDK`
  league used for #4's smoke test, which — per the live API calls made
  during brainstorming — has real historical trades already in it) to
  confirm the ledger populates with real trades and the evaluator produces
  sane numbers for a real hypothetical trade.

## §10. Future improvements (not attempted here)

- **Trade-target suggestions** (§0) — a full sub-project of its own,
  needing roster-needs/positional-scarcity modeling across the league.
- **Real NFL draft capital as a pick-value input**, if a future sub-project
  adds an external real-draft-data ingest — would let pick values be fit
  from a much larger sample than the user's own two leagues' rookie-draft
  history, and would work for users without established dynasty leagues at
  all.
- **Broader cross-league pick-value data sourcing** — if the user gains
  access to more dynasty leagues' history (their own or shared data), the
  same fitting procedure directly benefits from a larger sample without any
  design change.
- **Multi-season trade tracking** for dynasty leagues, where a trade's true
  impact may only be visible years later.
- **Tracking a traded pick's actual conversion** into a real player once
  that rookie draft happens, so a pick-involving trade could eventually get
  a true realized-outcome grade instead of staying a live-estimate forever.
- **Roster-context-aware fairness** and a **qualitative fairness verdict**
  once enough real graded trades exist in the ledger to calibrate
  thresholds against real outcomes, rather than inventing them.
- **Contract / cap-space risk and team-context modeling** — carried over
  unchanged from #4's own §9 future-improvements list; still not attempted
  for trade valuation either.
