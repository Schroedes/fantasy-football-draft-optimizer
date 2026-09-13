# Waiver-Wire Recommender — Design Spec

Sub-project #6 of the multi-league season companion initiative (stacked on
[#29](https://github.com/Schroedes/fantasy-football-draft-optimizer/pull/29),
sub-project #5). Prior sub-projects: #1 foundation, #2 roster/standings, #3
weekly lineup, #4 player valuation, #5 trade calculator. This sub-project
reuses #4's valuation engine (`ros_value`, `ValuedPlayer.vor`) directly, the
same way #5 did.

## §0. Scope decisions, made during brainstorming

The original 7-part decomposition names this sub-project "Waiver-wire
recommender — add/drop + FAAB $ bid suggestion given remaining budget
(+decision ledger)." During brainstorming:

- The **decision ledger** (detecting real waiver claims and grading them
  later) is explicitly **deferred to a future fast-follow**, the same
  posture #5 took for its interactive trade-builder UI. This spec covers
  the recommender only.
- **FAAB leagues only.** Rolling-priority and no-waiver (rolling free
  agency) leagues are out of scope — they get a graceful 400, matching how
  `/lineup` already gates ESPN leagues.
- **The FAAB bid amount is fit from real historical bid data**, not a
  hand-authored heuristic — the user explicitly chose this over the
  simpler, faster option after the real-data feasibility was confirmed
  (hundreds of real historical FAAB claims exist across the tracked
  leagues, and Sleeper exposes real per-week historical stats needed to
  reconstruct what each historical claim was actually worth).
- **Positional roster caps are in scope**, added mid-brainstorming at the
  user's request: the recommender must not suggest stockpiling QB/TE/DEF/K
  beyond what a roster can usefully start, even when such a player has the
  highest raw VOR available in free agency. This is a mechanical
  roster-construction guardrail, not the deeper roster-needs/scarcity
  modeling already deferred to trade-target suggestions (a separate future
  sub-project) — it does not require modeling what any other team needs,
  only what the tracked user's own roster can structurally use.

## §1. Goals

1.1. Compute the free-agent pool for a league (every real player minus
     everyone's rostered players) and value it on the same VOR scale
     `roster_value` already produces for rostered players.
1.2. Recommend a bounded list of adds: a free agent worth adding, which of
     the user's own players to drop for it (or no drop, if a bench slot is
     open), and a suggested FAAB bid — respecting positional roster caps so
     a high-VOR QB/TE/DEF/K in free agency is only recommended as a
     like-for-like swap once the user's roster is already at its useful
     cap for that position, never as a pure bench stockpile.
1.3. Give a suggested FAAB bid a real dollar amount, fit from the user's
     own leagues' real historical waiver-claim outcomes — nothing in the
     codebase has ever modeled "how much of a remaining budget is this
     add worth" before.

## §2. Non-goals

- **The decision ledger** (§0) — a future fast-follow, exactly like #5's
  deferred trade-builder UI.
- **Rolling-priority and no-waiver leagues** (§0) — FAAB only for now.
- **Roster-needs/scarcity modeling across the whole league** — same
  non-goal #5 already deferred to a future trade-target-suggestions
  sub-project. The positional caps in this spec (§5) are a mechanical
  roster-construction rule applied only to the tracked user's own roster,
  not a model of what any other team needs or what the broader market for
  a player looks like.
- **Position-aware roster caps beyond QB/TE/DEF/K** — RB/WR are
  deliberately uncapped (§5); this spec does not attempt to model an
  "ideal" RB/WR bench depth, only that surplus bench value should
  preferentially land there rather than at shallow positions.
- **Contract/cap-space risk, team-context modeling** — carried over
  unchanged from #4's and #5's own non-goals lists; still not attempted
  for waiver valuation either.

## §3. Reused from #4/#5, unchanged

- `engine.ros_value.roster_value(...) -> dict[str, ValuedPlayer]` — the
  source of every player's value, free agents included. `roster_value`
  takes any `player_ids` iterable (confirmed during brainstorming — nothing
  about its signature is roster-specific), so it can be called directly
  with the free-agent pool's player IDs.
- `domain.constants.INJURY_OUT_STATUSES` — free agents in this set are
  excluded from recommendations regardless of projected value, the same
  convention `ros_value.py` already applies to rostered players.
- `ingest.sleeper.transactions`'s pattern (parsing a
  `/league/<id>/transactions/<week>` payload into a domain type) — §4's new
  `ingest.sleeper.waivers` module mirrors this file's shape exactly, just
  filtering to `type == "waiver"` instead of `"trade"`.
- `engine.vor.compute` + a `LeagueProfile`-shaped reference pool, the same
  approach `scripts/fit_pick_value.py` used to compute historical VOR for
  players outside any specific league's live roster context.

## §4. FAAB bid curve fitting

### §4.1. Why point-in-time value, not season-end value

A team's FAAB bid reflects what a player was worth **at the moment of the
claim** — how much production they were expected to provide for the
*remainder* of that season, not their full-season total. Verified during
brainstorming: Sleeper exposes real historical stats at week granularity
(`GET /stats/nfl/regular/<season>/<week>`, confirmed live against a real
past week), so a historical claim's "realized value" can be reconstructed
correctly as the sum of the added player's real scored production from the
week *after* the claim through the end of that season — not their
season-total stat line, which would overstate value for a late-season
pickup and understate the bid-per-value ratio for an early one.

### §4.2. Fitting procedure

New offline script `scripts/fit_faab_curve.py`, following the same posture
as every other fitted-curve script in this project (`fit_age_curve.py`,
`fit_pick_value.py`): prints a Python literal for manual review, never
auto-writes the checked-in constant.

1. For each tracked league with real waiver history, fetch every week's
   transactions for each historical season, keeping only
   `type == "waiver", status == "complete"` entries (mirrors
   `ingest.sleeper.transactions`'s trade filter, applied to the sibling
   transaction type) — each carries `settings.waiver_bid` (the real dollar
   amount), `adds` (player_id → roster_id), `created` (timestamp), and the
   week it happened in.
2. Sort a season's waiver transactions chronologically by `created`.
   Walk them in order, maintaining a running `spent_by_roster: dict[int,
   float]` starting at `0.0` for every roster. For each transaction:
   - `remaining_before = league_waiver_budget - spent_by_roster[roster_id]`
     (the roster's real remaining budget at the moment of this specific
     claim, not season-end).
   - Record `(remaining_before, bid_amount, player_id, week, season)` as
     one fitting observation.
   - `spent_by_roster[roster_id] += bid_amount` (updates the running total
     for this roster's *next* transaction in the walk).
3. For each observation, compute the added player's realized VOR gain:
   sum their real scored production (`score_stats`, `STANDARD_HALF_PPR`,
   the same fixed comparability standard `fit_age_curve.py`/
   `fit_pick_value.py` already use) over weeks `week+1` through that
   season's last week, then run it through `vor.compute` against a
   `LeagueProfile`-shaped reference pool (same construction
   `fit_pick_value.py` uses) to get a VOR-equivalent number, not raw
   points — keeping this curve on the same scale the live recommender
   itself will read at recommendation time.
4. Compute `bid_pct = bid_amount / remaining_before` for every observation
   (guarding `remaining_before <= 0`: skip that observation, it carries no
   usable ratio). Bin observations by realized VOR gain into buckets
   (reusing this project's established `MIN_SAMPLE`-gated averaging
   pattern — a bucket only enters the curve once it has at least
   `MIN_SAMPLE = 3` observations, the same threshold `fit_pick_value.py`
   uses, for the same reason: 1-2 points is noise, and this real sample
   is modest).
5. Print the result as `FAAB_BID_CURVE: Final[dict[int, float]]`, keyed by
   a VOR-gain bucket boundary (e.g. buckets of width 10 VOR: `0`, `10`,
   `20`, ...) mapping to the average observed `bid_pct` for that bucket.

### §4.3. The checked-in constant and lookup

`domain/constants.py::FAAB_BID_CURVE: Final[dict[int, float]]` — bucket
lower-bound (an `int`, VOR-gain floor) to `bid_pct` (a `float` in `[0, 1]`,
though real bids occasionally exceed 100% of a team's *remaining* budget
mid-season if a later refill or league rule allows overspend — the curve
does not clamp this away, since it would be discarding real signal about
how aggressively a real league bids for a strong add).

`engine/waiver_value.py::suggested_bid(vor_gain: float, remaining_budget:
float, curve: Mapping[int, float], *, bucket_width: int = 10) -> float`:

```python
def suggested_bid(vor_gain, remaining_budget, curve, *, bucket_width=10):
    if not curve:
        return 0.0
    bucket = math.floor(vor_gain / bucket_width) * bucket_width
    available_buckets = sorted(curve)
    # Fall to the nearest bucket at or below vor_gain's own bucket -- a
    # gap in the curve (a VOR-gain range with no MIN_SAMPLE-qualifying
    # observations) degrades to the best evidence available below it,
    # never above it: extrapolating a stronger add's bid rate onto a
    # weaker one would systematically over-recommend spending.
    candidates = [b for b in available_buckets if b <= bucket]
    if not candidates:
        return 0.0
    pct = curve[max(candidates)]
    return round(remaining_budget * pct, 0)
```

Not backtested against a ground truth beyond the historical bids it was
fit from (there is no further "was this bid amount right" signal to
validate against, the same honest position #4's `DISCOUNT_RATE` and #5's
`PICK_UNCERTAINTY_DISCOUNT_RATE` are documented with) — but unlike those
two constants, this one IS fit from real data end-to-end, not a judgment
call, since real historical bid outcomes exist to fit it from.

**Correctness note (caught during implementation's task review):** an
earlier version of this bucket computation used `(int(vor_gain) //
bucket_width) * bucket_width`. Python's `int()` truncates toward zero,
not toward negative infinity, so for a negative non-integer `vor_gain`
(e.g. `-100.5`) this gives `int(-100.5) // 10 * 10 == -100` — one full
`bucket_width` too high, when the correct floor-based bucket is
`math.floor(-100.5 / 10) * 10 == -110`. Since real VOR values are
essentially never exact integers and most of `FAAB_BID_CURVE`'s real
buckets are negative, this silently mis-binned the majority of the
curve's real fitting observations, not just lookups at inference time —
the same identical bug existed in `scripts/fit_faab_curve.py`'s own
bucketing code, since both were written from this same formula. Both
were corrected to `math.floor(vor_gain / bucket_width) * bucket_width`,
and the curve was re-fit from scratch with the corrected formula (see
the SDD ledger's Task 5 fix entry for the before/after bucket values).

## §5. Free-agent pool, positional caps, and the add/drop recommendation

### §5.1. Free-agent pool

`engine/waiver_value.py::free_agents(all_player_ids: Iterable[str],
rosters: Sequence[RosterEntry]) -> set[str]`:

```python
def free_agents(all_player_ids, rosters):
    rostered = {pid for r in rosters for pid in r.player_ids}
    return set(all_player_ids) - rostered
```

`all_player_ids` comes from the existing player-universe ingest
(`ingest.players`), already loaded by every endpoint that needs profiles.

### §5.2. Positional roster caps

Confirmed during brainstorming with the user's own worked examples:

```python
POSITION_CAP_EXTRA: Final[dict[str, int]] = {"QB": 1, "TE": 1, "DEF": 0, "K": 0}
# RB and WR are absent from this mapping -- absent means uncapped (§5.3
# checks membership before applying any cap at all).
```

`engine/waiver_value.py::position_cap(position: str, league) -> int | None`:

```python
def position_cap(position, league):
    if position not in POSITION_CAP_EXTRA:
        return None  # uncapped -- RB, WR, and any position not listed
    dedicated = sum(1 for s in league.roster_positions if s == position)
    flex_eligible = sum(
        1 for s in league.roster_positions
        if s in FLEX_ELIGIBILITY and position in FLEX_ELIGIBILITY[s])
    return dedicated + flex_eligible + POSITION_CAP_EXTRA[position]
```

(`FLEX_ELIGIBILITY` is the existing mapping already defined in
`engine/replacement.py` — reused unchanged, not redefined here.)

Verified against the user's own examples during brainstorming: a 1-TE +
1-FLEX(TE-eligible) league gives `dedicated=1, flex_eligible=1,
EXTRA=1` → cap `3`. A standard (non-superflex) league gives QB
`dedicated=1, flex_eligible=0, EXTRA=1` → cap `2`. DEF/K in any standard
league give `dedicated=1, flex_eligible=0, EXTRA=0` → cap `1` (exactly what
you already start, no bench insurance at all).

### §5.3. The add/drop recommendation

`engine/waiver_value.py::recommend_adds(free_agent_ids, your_roster_ids,
valued: Mapping[str, ValuedPlayer], profiles, league, faab_curve,
remaining_budget, *, top_n: int = 10) -> list[WaiverRecommendation]`

For each free agent (excluding anyone in `INJURY_OUT_STATUSES`, §3):

1. Compute `cap = position_cap(free_agent.position, league)`.
2. If `cap is None` (RB/WR, uncapped) OR the user's current roster count at
   that position is below `cap`: the comparison pool is **every player on
   the user's bench** (any position) — an open bench slot, or a
   below-cap position, means no positional constraint applies, and the
   drop candidate (if any) is simply the user's single worst-VOR bench
   player.
3. If the user's roster is already **at or above** `cap` for that
   position: the comparison pool narrows to **only the user's rostered
   players at that same position** — the free agent must beat the user's
   worst player at that specific position to be recommended at all. This
   is what stops a high-VOR free-agent QB from ever being recommended as a
   3rd QB once the user is already carrying 2 in a standard league — it
   can still be recommended as a straight swap for the user's own worst
   QB, just never as a pure add.
4. If no roster slot is open and the comparison pool is empty (impossible
   in practice — the user always has at least one player at a position
   they're at-cap for) or the free agent doesn't clear the comparison
   pool's worst player by a real margin (`MIN_VOR_GAIN = 5.0`, a
   controller judgment call, not backtested — flagged the same honest way
   as this spec's other undetermined constants), skip this free agent.
5. Otherwise, emit a `WaiverRecommendation`: the free agent, the drop
   candidate (`None` if an open slot absorbed the add with no drop
   needed), the free agent's VOR gain over the drop candidate (or over
   replacement level, if no drop), and `suggested_bid(vor_gain,
   remaining_budget, faab_curve)` (§4.3).

Sort by VOR gain descending, return the top `top_n`.

`WaiverRecommendation` (new frozen dataclass in `domain/models.py`):
`free_agent_id: str`, `drop_player_id: str | None`, `vor_gain: float`,
`suggested_bid: float`.

## §6. Live remaining-budget computation

`ingest/sleeper/waivers.py::remaining_budget(sleeper, league_id, *,
waiver_budget: int, through_week: int) -> dict[int, float]` — the SAME
chronological running-spend walk §4.2 describes for historical fitting,
applied to the CURRENT season's transactions through the current week, to
get every roster's real remaining budget right now. This function is
genuinely shared logic between the offline fitting script and the live
recommender (not a duplicate) — both need "walk this season's waiver
transactions in order, track cumulative spend per roster."

## §7. API

`GET /api/leagues/{league_key}/waivers` — Sleeper-only for now (§0), 400
for any other provider, 400 if `lg.resolved_format`'s league doesn't use
FAAB (`raw_settings`'s `waiver_type` — Sleeper's FAAB waiver type is `2`;
any other value gets the graceful 400). Response: the tracked user's
current remaining budget, plus the top-N `WaiverRecommendation` list as
JSON.

## §8. Frontend

New **Waivers** tab in `web/season/season.js`, following the exact
tab/lazy-load pattern §5/#9's Trades tab already established
(`data-panel-tab`, a module-level cache variable, a `loadWaivers()`
mirroring `loadTrades()`'s stale-response guard, a `renderWaivers()`
reusing existing `.lineup-row`/`.lineup-list` CSS classes where the shape
fits).

## §9. Testing approach

- `scripts/fit_faab_curve.py` run for real against the user's actual
  league history (same posture as every other fitting script — a real run
  producing real output, reviewed before being committed to
  `FAAB_BID_CURVE`).
- `engine/waiver_value.py`: unit tests covering the free-agent-pool
  computation, the positional-cap formula against the user's own worked
  examples (§5.2), the cap-vs-uncapped branching in `recommend_adds`
  (§5.3, both branches), the `suggested_bid` bucket-fallback behavior
  (including the empty-curve and gap-in-curve cases) — and, learning
  directly from #4's and #5's post-mortems, **at least one test that
  checks real magnitude against the real fitted `FAAB_BID_CURVE`, not
  only direction/bounds against a synthetic curve**.
- `ingest/sleeper/waivers.py`: unit tests with a fake Sleeper client,
  covering the chronological running-spend walk (order-dependence is the
  one thing most likely to be silently wrong here — a test must record
  transactions out of chronological order and confirm the walk still
  processes them by `created` timestamp, not feed order).
- Manual browser smoke test against a real FAAB league (GDK, already
  tracked, confirmed during #5's brainstorming to have deep real
  transaction history including many real FAAB claims) to confirm the
  Waivers tab populates with sane real recommendations and bid amounts.

## §10. Future improvements (not attempted here)

- **The decision ledger** (§0/§2) — detect real waiver claims, grade them
  later against realized production, mirroring #3/#5's ledger patterns.
- **Rolling-priority and no-waiver league support.**
- **Roster-needs/scarcity-aware recommendations** across the whole
  league, alongside the already-deferred trade-target-suggestions work —
  the positional caps in this spec are deliberately mechanical
  (what CAN your roster use), not a market-aware model of what you
  SHOULD prioritize given your competitors' rosters.
- **Position-aware caps for RB/WR**, if real usage ever suggests bench
  bloat there is actually a problem worth modeling, rather than the
  "let surplus land here" default this spec takes at the user's own
  request.
- **Multi-week bid trend modeling** (e.g., bids escalate as the trade
  deadline or playoffs approach) — `FAAB_BID_CURVE` is fit as a single
  season-long average, not week-sensitive.
