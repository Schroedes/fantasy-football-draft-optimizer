# Fantasy Football Draft Optimizer — Design

**Date:** 2026-08-22
**Status:** Approved, pending implementation
**Hard deadline:** 2026-08-25 (P-Vegas Ballers auction draft)

## 1. Purpose

A local decision-support tool for fantasy football drafts. It does **not** name a
pick. It makes the scarcity math visible — what a player is worth to *this* roster
in *this* league, and what it costs to wait — and the user decides.

The tool answers one question the user cannot compute under a draft clock:

> Should I take the position that's running out, or the better player at a
> position that will still be there?

### 1.1 Goals

- Value players against the user's actual league scoring and roster requirements.
- Quantify positional scarcity as a number, not a vibe.
- Track a live draft and update as picks land.
- Support snake and auction formats.

### 1.2 Non-goals

- Naming a pick. The board informs; the user decides. A "model's lean" badge is
  built but disabled by default.
- Manual overrides of any kind (do-not-draft lists, value nudges, weight tuning).
  Explicitly declined by the user. This raises the bar on validation — see §8.
- Out-projecting the market from scratch. We adjust the market's projection; we
  do not replace it.
- Dynasty valuation. The user's dynasty leagues are established with no upcoming
  draft. The horizon abstraction is designed for it (§6.3); the curves are not fit.
- Nomination strategy in auction. It is advice, and advice is out of scope.
- In-season management, waivers, trades, lineup setting.

## 2. Context

Leagues are on Sleeper. The primary league — **P-Vegas Ballers**, `league_id`
`1315881559957458944` — is a 12-team, $200 auction with history back to 2019.
The user also plays in snake leagues, which are in scope.

### 2.1 Primary league facts (verified 2026-08-22)

| Property | Value |
|---|---|
| Teams | 12 |
| 2026 draft | `1315881559965835264`, type `auction`, budget $200, 13 rounds, status `pre_draft` |
| 2026 starters | `QB RB RB WR WR WR TE FLEX` (8) + 5 bench = 13 |
| Kickers / defenses | **None rostered** |
| Scoring keys | 49 custom, **byte-identical across 2021–2026** |
| Completed auctions | 2021, 2022, 2023, 2024, 2025 (~800 priced picks) |

**Roster configuration is not stable across seasons** and this materially affects
any use of historical prices:

| Season | Starters |
|---|---|
| 2021–2024 | `QB RB RB WR WR TE FLEX FLEX` |
| 2025 | `QB RB RB WR WR WR TE FLEX SUPER_FLEX` |
| 2026 | `QB RB RB WR WR WR TE FLEX` |

2025 was a superflex season. Room QB spend went from 8–11% (2021–2024) to
**24.2%**. Feeding 2025 QB prices into a 2026 QB model without normalizing would
be badly wrong. See §7.2.

## 3. Data sources

All data is Sleeper. No second provider in v1.

| Data | Endpoint | Documented | Notes |
|---|---|---|---|
| Players | `/v1/players/nfl` | Yes | 14.0 MB, 12,221 entries, 3,043 active skill players, 91.5% have `age`. Refresh daily. |
| Season stats | `/v1/stats/nfl/regular/<yr>` | **No** | ~1.8 MB/yr. Component stats + `gp`. Historical facts, fetch once. |
| Season projections + ADP | `/projections/nfl/<yr>?season_type=regular` | **No** | Component projections, plus ADP in every format. |
| League | `/v1/league/<id>` | Yes | `scoring_settings`, `roster_positions`, `previous_league_id`. |
| Draft + picks | `/v1/draft/<id>`, `/v1/draft/<id>/picks` | Yes | Auction picks carry `metadata.amount` (verified 100% populated). |

No auth. Rate limit 1000 calls/min; polling one draft at 3s is 20/min.

Two of five sources are undocumented and may change without notice. This drives
the adapter isolation rule in §4.

### 3.1 Season length varies

17 games in 2021–2023, 18 in 2024–2025. Availability rates **must** normalize per
season, never against a constant.

### 3.2 Historical projections are contaminated — do not use them

The projections endpoint returns *latest* state, not preseason state. For past
seasons the stored `pts_*` values have been overwritten with in-season
information.

Evidence, 2023:

| Player | Stored "projection" | Preseason ADP | Actual | Games |
|---|---|---|---|---|
| Nick Chubb | **absent** | 10.6 | 21.1 | 2 |
| J.K. Dobbins | **absent** | 51.3 | 10.7 | 1 |

Both players' rows retain their `adp_*` keys and `gp`, and carry **no
`pts_half_ppr`, `pts_ppr`, or `pts_std` key at all** — the projection was wiped
after their season-ending injuries, not merely revised. Across the whole 2023
file, 689 of 3,309 rows have a `pts_half_ppr` key and **zero** rows have it
equal to `0.0`. Chubb was drafted 10th overall; a player taken that early having
no stored projection, while his ADP survives intact, is only explicable
post-hoc.

(An earlier revision of this document reported these values as `0.0`. That was
an artifact of the probe script defaulting absent keys to zero, not of Sleeper's
data. The conclusion is unchanged and the mechanism is starker than first
described.)

Corroborating rank correlation against actual points:

| Season | Stored projection ρ | ADP ρ |
|---|---|---|
| 2023 | 0.814 | 0.655 |
| 2024 | 0.827 | 0.655 |
| 2025 | 0.776 | 0.643 |

Preseason projections and preseason ADP derive from the same information set and
cannot differ by 0.16. ADP's stability at ~0.65 across years is the signature of
a genuine, uncontaminated preseason signal.

**Consequences:**

1. The backtest baseline is **historical ADP**. Historical projections are never
   used as a preseason input.
2. **Current-season projections are unaffected** — the 2026 season has not begun,
   so there is no in-season information to leak. Live use is sound.
3. Ingest **must reject** any projection whose `last_modified` postdates that
   season's start rather than silently trusting it.

### 3.3 Snapshot policy

Contamination begins when games are played. The 2026 season opens **2026-09-09**,
so projections remain clean until then and capture is not urgent before the draft.

Two vintages are worth holding, because they answer different questions:

1. **Draft-day vintage** — the information set actually available at draft time.
   The correct baseline for evaluating *this draft's decisions*. Captured
   2026-08-22 to `data/snapshots/2026-08-22-draft-day/` (5.4 MB gzipped): 2026
   projections, players, stats 2021–2025, full league and draft history, plus the
   contaminated projection years preserved under `*_CONTAMINATED` filenames so
   §3.2 stays reproducible.
2. **Preseason-final vintage** — final consensus before kickoff. The correct
   baseline for evaluating *the market*. **Must be captured before 2026-09-09.**

Both should become annual steps so future backtests have trustworthy data instead
of the contaminated history documented in §3.2.

## 4. Architecture

```
web/       static board, no build step
api/       FastAPI — serves board state, polls the draft
engine/    value · market · scarcity · auction   (pure, no I/O)
ingest/    Sleeper adapters + SQLite cache       (all I/O, all wire formats)
domain/    plain dataclasses
```

Each layer depends only on the layer beneath it.

**The rule that holds this together: nothing above `ingest/` ever sees a Sleeper
JSON key.** Adapters translate wire format into `domain/` dataclasses at the
boundary. When an undocumented endpoint changes shape, the blast radius is one
adapter and its fixtures.

`engine/` is pure functions over dataclasses — no network, no clock, no I/O. This
is what makes the model testable and the backtest possible.

### 4.1 Stack

Python 3.12 managed by `uv` (installed at `C:\Users\basek\.local\bin\uv.exe`).
FastAPI + numpy/scipy. Frontend is a single auto-refreshing static page, no build
step. Node 24.18 is available for tooling if needed.

### 4.2 Cache

SQLite, one table per source, every row stamped with fetch time.

| Data | Refresh |
|---|---|
| Players | Daily |
| Season stats | Once — history does not change |
| Projections + ADP | On demand; automatically before a draft |
| League settings | Once per draft session |
| Draft picks | **Every 3s while drafting** |

Everything except the live draft feed works offline from cache, so a network
hiccup on draft night degrades to a stale-but-usable board rather than a blank
screen.

## 5. Domain model

Core dataclasses, all frozen:

- `PlayerProfile` — id, name, position, team, age, years_exp, injury status
- `SeasonStatLine` — player, season, games_played, season_length, component stats
- `SeasonProjection` — player, season, component projections, `last_modified`
- `MarketADP` — player, season, adp by format
- `LeagueProfile` — teams, `roster_positions`, `scoring_settings`, budget
- `DraftState` — type, picks so far, pick order, current pick, budgets remaining
- `ValuedPlayer` — profile + projected points + VOR + tier + adjustment audit trail

`ValuedPlayer` carries the audit trail deliberately: with no manual overrides, the
user needs to see *why* a number is what it is.

## 6. Value model

Five stages, each a pure function. Output is one number per player: **VOR**.

### 6.1 Stage 1 — Rescore under league rules

```
projected_points = Σ  component_stat[k] × league.scoring_settings[k]
                  k
```

Projection component keys and `scoring_settings` keys share Sleeper's vocabulary.
Sleeper pre-computes bonus-eligible receptions (`bonus_rec_te`, `bonus_rec_rb`),
so TE-premium falls out with no special case.

This matters because Sleeper's own board uses one of three fixed presets
(`pts_ppr` / `pts_half_ppr` / `pts_std`). This league has 49 custom scoring keys.

**Measured caveat, added 2026-08-22.** For *this* league the rescore turns out to
buy almost nothing at draft time, and the spec previously overstated it. The
league's only two divergences from Sleeper's half-PPR preset are `fum: -1` (all
fumbles, not just lost) and `st_td: 6`. Both keys are **entirely absent from
Sleeper's projections** — verified across the 2026 file: `fum` appears in 0 rows,
`st_td` in 0 rows — because nobody projects total fumbles or return touchdowns.
So the rescore reproduces the preset for this league, and the "fumble-prone QBs
are overvalued by up to 11 points" finding, which was measured on *historical*
stats, does not reach a draft decision.

The layer stays, for two reasons: it is required for the user's other leagues
(TE premium, bonus tiers, and 6-point passing TDs all have real projection
inputs), and it is what makes §11's golden test possible. Deriving a projected
fumble rate from historical data would recover the edge, but that is a model
adjustment and belongs under §8's default-off, backtest-gated discipline rather
than smuggled into ingest.

**Golden test:** rescoring with standard PPR weights must reproduce Sleeper's own
`pts_ppr` within tolerance. If our arithmetic reproduces theirs on the preset, it
can be trusted on custom settings. This is the first test written.

### 6.2 Stage 2 — Availability

Sleeper projects **every player at a full healthy season** (`gp` = 18 for all).
Real availability is a distribution, and it is the input their board structurally
ignores.

The naive correction — multiplying by `mean(gp)/season_length` — is wrong for
redraft. Waivers exist. A player who misses five games does not cost five games
of his production; he costs the gap between him and his replacement:

```
availability_cost = (player_ppg − replacement_ppg) × E[games_missed]
```

This reuses replacement level from §6.4, giving the model one coherent notion of
"what else could I have had." It correctly makes injury risk cheap for marginal
players and severe for elite ones.

`E[games_missed]` comes from a Beta-Binomial fit on historical `gp`,
recency-weighted, shrunk toward a position-level prior so rookies and second-year
players do not produce wild estimates from one season. Rates normalize per season
length (§3.1).

### 6.3 Stage 3 — Age

Position-specific curves fit by the **delta method**: for every player-pair of
consecutive seasons, take the change in points-per-game from age *a* to *a+1*,
then average by age within position. Cross-sectional averages are severely
survivorship-biased; deltas are less so.

Sample: 2021–2025, ~550 relevant players/season ≈ 2,750 player-seasons, ~2,200
consecutive-season pairs. Serviceable, not generous.

**Honest ceiling:** the market already prices age. Our edge exists only if the
market *systematically under-discounts* it. Age therefore receives no hand-tuned
weight — it is fit, then must earn its weight in §8 or be set to zero.

The stage takes a `horizon` parameter. In redraft, age is a decline-risk discount
on the coming season. The dynasty branch (multi-year asset curve) is the same
seam, unfitted and unbuilt in v1.

### 6.4 Stage 4 — Replacement level

Computed from the league, not from a rule of thumb. Read `roster_positions` and
team count, greedily fill every starting lineup league-wide by adjusted points,
and set replacement at each position to the best player who did not make a
starting lineup.

This handles FLEX allocation endogenously and superflex correctly — QB demand
roughly doubles, replacement QB collapses, every QB's VOR rises, with no special
case. Same machinery, different `roster_positions`.

This league rosters no K or DEF, which the same machinery handles by their simple
absence from `roster_positions`.

### 6.5 Stage 5 — VOR

```
VOR = adjusted_points − replacement_points[position]
```

Every downstream consumer — tiers, scarcity, cost of waiting, auction dollars —
reads VOR and nothing else.

## 7. Market model

Value asks *what is he worth*. Market asks *when will he be gone*. Keeping them
separate is what makes the central question answerable — the answer lives in the
gap between them. Collapsing them into a single ranking, which is what drafting
off Sleeper's board does, destroys the signal.

### 7.1 Survival by simulation

The closed form `P(available at pick n) = 1 − Φ((n − μ)/σ)` fails precisely where
it matters: independent Gaussians allow two players at the same pick, cannot
condition on who has already gone, and are blind to positional runs.

Instead, **simulate the intervening picks**. For each pick between now and the
user's next, sample from the available pool weighted by proximity to current ADP
(Plackett-Luce via vectorized Gumbel-max). Run ~2,000 simulations and count
survival frequency.

This yields, without special-casing: exactly one player per pick, live
conditioning on real board state, and emergent positional runs. Cost is ~2,000 ×
~24 picks over a few hundred candidates — milliseconds in numpy, well inside a
3-second refresh.

Opponent modeling is ADP-only in v1. Weighting opponents by observed positional
need is a designed-for extension that must earn its way in via §8.

### 7.2 Room calibration

Sleeper ADP is a mean over millions of drafts. The user's question is about
twelve specific people. Their five completed auctions (~800 priced picks) let us
measure how this room deviates from consensus.

**Calibration must use residuals against each season's own baseline, never raw
dollar share.** Compute fair value under *that season's* `roster_positions`, then
measure the room's deviation from it. The baseline absorbs the 2025 superflex
change (§2.1), making residuals comparable across seasons. Raw share is not.

Where history is thin, fall back to a global prior. **The fallback is explicit and
logged, never silent.**

### 7.3 Cost of Waiting

The number the user asked for, read directly off the same simulation:

```
CoW(pos) = E[VOR of best available @ pos, now]
         − E[VOR of best available @ pos, my next pick]
```

| Pos | Best now | E[best @ next pick] | Cost of waiting |
|---|---|---|---|
| WR | 51.2 | 33.4 | **17.8** |
| RB | 42.0 | 38.1 | 3.9 |
| TE | 28.5 | 27.9 | 0.6 |

Read: the RB cliff is a myth *in this spot* — the tier is deep, waiting costs ~4
points. The WR cliff is real and costs ~18. The board shows the tradeoff priced,
rather than asserting a pick.

### 7.4 Supporting signals

- **Tiers** — VOR gap detection within position; break where a gap exceeds
  *k* × median gap. Makes "deep stash" legible at a glance.
- **Tier survival** — expected count remaining at the user's next pick.
- **Run detection** — binomial test on recent picks against baseline positional
  rate. Observed, not predicted.
- **Roster slots** — unfilled starting slots and the marginal value of filling
  each. Displayed, never prescriptive.

## 8. Validation

The user declined all manual overrides. That is only defensible if every
adjustment has *earned* its weight, so each one is gated by out-of-sample
backtest.

**Protocol.** For 2023, 2024, 2025, using only data available before that season:
compute adjusted value, correlate against points actually scored. Baseline is
**historical ADP** (§3.2), which scores ρ≈0.65 and is also what the room actually
drafts on — so beating it is the operational definition of edge. Score with
Spearman ρ and MAE per position.

**Default-off risk control.** Age and durability weights default to **zero**. The
model ships as pure market-anchored VOR, which is honest and safe. The backtest
*promotes* a weight only on out-of-sample improvement. If time runs out, the safe
default ships. No tuning marathon, and no unvalidated adjustment can reach the
board by accident.

Any adjustment that fails to beat baseline is reported as failed in this document.

### 8.1 Backtest results (Task 14, run 2026-08-22; age rows corrected 2026-08-22)

`ffdo.backtest.harness.evaluate_season` was run for 2023, 2024, 2025 across the
weight combinations the harness's gate script specifies. `improvement` is
`model_rho - baseline_rho` (Spearman ρ against actual season points).

**Correction:** the age rows below were re-measured after fixing a train/apply
mismatch in `adjustments.py`: `fit_age_curve` correctly keys its training data
by the player's age *during each historical season* (computed from the single,
fixed 2026 `players_nfl` snapshot — there is no per-season profile history),
but `build()`'s lookup was using the player's raw 2026-anchored profile age
regardless of what season the backtest was evaluating. Since `players_nfl` is
always anchored to 2026, `build()` now shifts the lookup age by
`(2026 - current_season)` before querying the curve — a no-op for live 2026
use, and load-bearing for anything backtested against 2023-2025. Durability's
row is unaffected: its code path never touches the age curve.

| age_weight | dur_weight | 2023 | 2024 | 2025 | mean improvement |
|---|---|---|---|---|---|
| 0.0 | 0.0 | 0.0000 | 0.0000 | 0.0000 | +0.0000 |
| 0.0 | 1.0 | +0.0036 | +0.0036 | +0.0017 | +0.0030 |
| 1.0 | 0.0 | −0.0402 | −0.0415 | −0.0289 | −0.0369 |
| 1.0 | 1.0 | −0.0506 | −0.0359 | −0.0271 | −0.0379 |

(Pre-fix age numbers, for the record: 1.0/0.0 → −0.0182 / −0.0383 / −0.0263,
mean −0.0276; 1.0/1.0 → −0.0191 / −0.0400 / −0.0266, mean −0.0286. The
conclusion — age fails outright — was already correct before the fix; the fix
makes the *measurement* correct too, and the failure is now more decisive.)

Baseline ρ itself fell each year under the harness's actual filter (0.6776 /
0.4535 / 0.2267) — a real, verified effect of Sleeper's ADP data covering an
increasingly deep pool of marginal players over calendar time, not a defect;
see the comment in `tests/backtest/test_harness.py` for the full root-cause
trace. `improvement` (the promotion signal) is unaffected by this, since it
compares model to baseline within the same season/filter.

**Age.** Fails outright: negative improvement in all three seasons at
age_weight=1.0 (mean −0.0369, post train/apply-mismatch fix). **Stays at 0.0.**

**Durability.** At durability_weight=1.0, improvement is positive in all
three seasons (+0.0036, +0.0036, +0.0017), which literally satisfies "mean
improvement positive across all three seasons." A robustness sweep was run
before promoting on that basis:

| dur_weight | 2023 | 2024 | 2025 | mean |
|---|---|---|---|---|
| 0.5 | +0.0046 | +0.0051 | +0.0044 | +0.0047 |
| 1.0 | +0.0036 | +0.0036 | +0.0017 | +0.0030 |
| 2.0 | −0.0464 | −0.0385 | −0.0309 | −0.0386 |
| 5.0 | −0.3044 | −0.1752 | −0.0687 | −0.1828 |

The effect is not monotonic (0.5 outperforms 1.0) and flips sign between 1.0
and 2.0 — inconsistent with a genuine, generalizable dose-response signal,
and more consistent with an artifact of the backtest harness's synthetic
pseudo-points scale (ADP rank mapped onto a `linspace(300, 20)` proxy, not
real projections) and hardcoded `replacement_ppg`. The magnitude at the
literally-qualifying weight (+0.003 mean ρ) is also negligible relative to
baseline ρ (~0.45).

Per §8's design intent — "no unvalidated adjustment can reach the board by
accident" — and per Task 14's own framing that the promotion clause states a
necessary, not sufficient, condition ("a weight *may* only be promoted... if
[positive in all three seasons]") with the final call left as "a manual,
documented decision the controller reviews," **DURABILITY_WEIGHT stays at
0.0**: the qualifying result is real but too small and too fragile to
"earned its place," per §8's own standard. **Both `AGE_WEIGHT` and
`DURABILITY_WEIGHT` ship at 0.0.** The model remains pure market-anchored
VOR, exactly as it does when validation finds nothing to promote — a
successful, complete outcome per this task's own instructions, not a
shortfall.

## 9. Auction engine

Same value model, different question. Only the layer above VOR changes.

### 9.1 Baseline dollar values

```
discretionary = (n_teams × budget) − (n_teams × roster_size × $1)
rostered      = top (n_teams × roster_size) players by VOR
$/VOR         = discretionary / Σ VOR over rostered players, VOR > 0 only
price(p)      = $1 + VOR(p) × $/VOR
```

Two details commonly botched: clamp negative VOR to zero before summing, or deep
bench players deflate the scale; and reserve the $1-per-slot floor up front, or
the model generates prices the league cannot pay.

Because VOR already derives from this league's replacement levels, these dollars
are league-specific automatically.

### 9.2 Live inflation

Auction's answer to Cost of Waiting.

```
inflation = (total_budget − spent) / Σ baseline_price(undrafted rostered-tier players)
```

If the room has spent $800 on players worth $700, then $1,600 chases $1,700 of
value — factor 0.94, everything remaining is 6% below fair. The inverse is more
common and more dangerous: a bargain-hunting early room leaves too much money
chasing too little value, and late studs go for absurd prices.

Updates live from `metadata.amount`, verified populated on 100% of auction picks
across all five historical drafts.

### 9.3 Displayed per player

| Field | Meaning |
|---|---|
| Baseline $ | Fair price in an efficient market |
| Adjusted $ | Baseline × current inflation — fair price in this room, right now |
| Your max $ | `remaining_budget − (unfilled_slots − 1)` — hard roster-completion ceiling |

Plus budget state: dollars left, slots left, $/slot vs league average. Board sorts
by surplus (`adjusted $ − current bid`).

## 10. Board

Auction is primary; snake is the secondary path.

**Auction board.** Budget strip across the top: dollars left, slots left, $/slot
vs room average, current inflation factor. Nominated-player card: baseline $,
adjusted $, your max $, VOR, tier, age and durability flags. Main table sorted by
surplus.

**Snake board.** The Cost of Waiting table takes top billing — it is the only
thing on screen the user cannot compute in their head. Ranked board beneath it
with VOR, tier, ADP, and P(survive to next pick). Run-detection banner when
triggered.

Both refresh on a 3-second poll of the draft feed. No named pick anywhere; the
"model's lean" badge exists but is disabled.

## 11. Testing

- **Golden test** — rescore reproduces `pts_ppr` under standard PPR weights (§6.1).
- **Fixture-based ingest tests** — recorded JSON from the real league; no network
  in CI. Fixtures come from the §3.3 snapshot.
- **Auction replay** — replay all five completed auctions (2021–2025) pick by
  pick and assert inflation tracks sanely and budgets reconcile. Five real
  integration tests, free.
- **Contamination guard** — assert ingest rejects a projection whose
  `last_modified` postdates its season start. This bug class is silent and fatal.
- **Backtest harness** — runs as a test, not a notebook.
- **Property tests** on engine purity: replacement level monotone in team count,
  VOR invariant to scoring rescale, dollar values summing to the league budget.

## 12. Build phases

Draft is **2026-08-25**, three days out.

| Phase | Deliverable | Target |
|---|---|---|
| ① | Ingest + cache + domain models, fixtures from real league | Day 1 |
| ② | Value model + golden test | Day 1 |
| ③ | Auction engine + inflation, validated by replay | Day 2 |
| ④ | Auction board — **must be live for Aug 25** | Day 2 |
| ⑤ | Snake: survival sim + CoW + snake board | Day 3 |
| ⑥ | Backtest harness → promote or zero age/durability weights | Day 3, timeboxed |

Phase ④ is the hard commitment. Phases ⑤–⑥ serve the user's snake leagues and
model validation; both degrade safely (⑥'s default-off weights mean skipping it
ships a sound model).

A preseason-final snapshot (§3.3) must be captured before 2026-09-09, after the
draft and outside this phase plan.

## 13. Risks

| Risk | Mitigation |
|---|---|
| Undocumented endpoint changes shape mid-draft | Adapter isolation (§4); local cache serves last-known-good; snapshot exists |
| Contaminated data silently poisoning the model | Explicit `last_modified` rejection + guard test (§11); ADP-only backtest baseline |
| 2025 superflex skewing room calibration | Residual-against-own-season-baseline calibration (§7.2) |
| Three-day timeline | Phase ④ is the only hard commitment; ⑤–⑥ degrade safely |
| Age/durability adjustments turn out worthless | Default-off weights (§8); model is sound without them |
| Simulation too slow for a 3s refresh | Vectorized numpy; budget is milliseconds against a seconds-scale target |
| No override escape hatch during the draft | Deliberate user choice; mitigated by the `ValuedPlayer` audit trail (§5) |
