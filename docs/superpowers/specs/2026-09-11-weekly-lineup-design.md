# Weekly Optimal Lineup — Design

**Date:** 2026-09-11
**Status:** Approved, pending implementation
**Stacked on:** `claude/roster-standings-view` (PR #25, sub-project #2 — roster & standings view). This is a stacked PR: it branches from #2 and merges after it.
**Prior art:**
- `docs/superpowers/specs/2026-09-03-roster-standings-view-design.md` — defines `ingest/nfl_state`, the `/season` endpoint, and the `engine/vor` + `engine/roster.team_lineup` machinery this sub-project reuses. Also flags `NFL_BYE_WEEKS` as a hand-maintained placeholder and notes "an ingest from the schedule endpoint is possible later" — this sub-project finds and uses exactly that endpoint (see §9).

## 0. Context: the larger initiative

Sub-project #3 of seven. #2 gave every drafted league a season-long view:
power rankings, your roster's rest-of-season value, standings, draft
capital. This sub-project answers a narrower, more urgent question: **is
my lineup optimal for the week that's about to lock, and if not, what
should I change before I can't anymore.**

Unlike #2, this is a *recommendation*, not just an analysis — so it's the
first sub-project to carry the **decision ledger** the original
brainstorming scoped in from day one for #3/#5/#6: every recommendation
persists what was recommended, what the user actually had set, and
(resolved once the week locks) whether they followed it.

No new seam for #4. The per-player valuation input changes (this week's
projected points instead of rest-of-season VOR) but the machinery
consuming it — `engine/vor.compute`, `engine/roster`'s greedy slot-fill —
is the same code #2 already froze into shape, extended with a slot-aware
variant (see §4.1) rather than replaced.

## 1. Purpose

### 1.1 Goals

- **This week's optimal lineup.** For the current NFL week, rank every
  rostered, eligible player by weekly value-over-replacement and solve the
  best possible starting lineup, using the same VOR/FLEX-aware greedy
  algorithm #2 already ships for season-long rankings — just fed weekly
  projected points instead of season totals.
- **Diff against your actual current lineup, slot by slot.** Every
  starting slot is one of:
  - **Match** — your current starter is also optimal.
  - **Suggested swap** — a better option exists and hasn't locked yet.
    Actionable, shown with the point-projection gap.
  - **Missed** — a better option existed but its game already locked
    before you started them. Not actionable; a record of what happened.
- **Lock-aware.** A player whose NFL game has already started or finished
  is never suggested as a swap candidate, and a current starter whose own
  game already locked is never flagged for benching (you can't swap out
  someone who already played). Lock state comes from Sleeper's own live
  per-game status — not a computed kickoff-time comparison.
- **Decision ledger.** The first time a week's lineup view is opened, the
  recommendation (the optimal lineup) and what you actually had set at
  that moment are persisted. Once every game that week is complete, the
  same record is resolved against your *final* actual starters:
  fully/partially/not followed.
- **Third tab on the season screen**, `[Lineup | Power ranking | Draft
  capital]`, shown first — the most time-sensitive panel, not gated by
  format (unlike Draft capital).

### 1.2 Non-goals

- **No write-back to the provider.** Recommend only. A future sub-project
  could add "apply this lineup" as its own scoped write-action feature;
  out of scope here.
- **No multi-week preview.** Current/upcoming week only — matchups and
  injury news shift too much for a lineup call to hold value further out.
- **No opponent/matchup-strength adjustment.** Pure weekly-VOR ranking,
  same posture as #2 ignoring strength of schedule for season values.
- **No precise kickoff-time countdown.** Locks are per-game `status`
  (`pre_game` vs. anything else), not a clock. Good enough to answer "can
  I still swap this player" without needing exact timestamps Sleeper's
  unofficial schedule feed doesn't even expose (see §3.2).
- **ESPN**: same posture as #2 — ships if `rosters.fetch`'s existing ESPN
  branch and profile data carry through cleanly, but the weekly
  projections and schedule/lock feeds are Sleeper-only regardless of
  provider (matches #2's precedent: valuation inputs always come from
  Sleeper). Not separately verified against a live ESPN league.
- **No re-derivation of `NFL_BYE_WEEKS`.** §3.2's schedule feed makes this
  possible (bye = a team absent from that week's game list) and #2's own
  spec flagged it as a future option, but folding it in here is scope
  creep on a sub-project that only needs the *current* week's data, not a
  full-season table. Noted as a ripe, cheap follow-up in §9.

### 1.3 Deployment stance

Unchanged: single-user, local-first, no auth. The one new piece of
persisted state is the decision ledger itself (§5), the first thing this
initiative writes to `data/ffdo.db` beyond league tracking.

## 2. Architecture

```
GET /api/leagues/{league_key}/lineup
  │
  ├─ _load_league(key)  ─────────  TrackedLeague (as #2)
  ├─ nfl_state.current_week(...)  ─  reused from #2, same _nfl_state_cache
  ├─ players_cache.get(...)  ─────  reused from #2 (24h)
  ├─ rosters.fetch(...)  ─────────  reused from #2 -- the full rostered pool (player_ids) to value
  ├─ rosters.raw_starters(sleeper, league_id, roster_id)  ─  NEW: the tracked user's OWN starters,
  │                                 positionally aligned to league.starting_slots (see note below)
  │
  ├─ weekly_projections.fetch(sleeper, season, week)  ─  NEW: this week's projected points
  ├─ schedule.week_games(sleeper, season, week)  ────────  NEW: per-team lock + bye status
  │
  ├─ engine.weekly_lineup.weekly_value(rostered_pids, league, weekly_points=…, profiles=…,
  │       bye_teams=…)                                    ← reuses vor.compute, excludes bye/hard-out
  │       → {pid: ValuedPlayer}   (this week's VOR, not rest-of-season)
  │
  ├─ engine.weekly_lineup.optimal_slots(valued, league)   ← slot-aware sibling of team_lineup (§4.1)
  │       → {slot_index: player_id | None}   (aligned to league.starting_slots)
  │
  ├─ engine.weekly_lineup.diff(current_starters, optimal_slots, locked_teams, valued, profiles, league)
  │       → list[SlotDiff]
  │
  ├─ ledger.record_or_resolve(league_key, season, week, diff, week_locked)  ─  NEW: data/ffdo.db
  │
  └─ assemble → { nfl_week, diff, swaps_suggested, week_locked, ledger }

Frontend:  web/season/season.js gains a "Lineup" tab, fetching /lineup lazily
           when that tab is first opened (not on initial /season load)
```

### 2.1 New domain types (`src/ffdo/domain/models.py`)

```python
@dataclass(frozen=True, slots=True)
class WeeklyProjection:
    player_id: str
    season: int
    week: int
    stats: Mapping[str, float]        # same stat-key shape as SeasonProjection.stats

@dataclass(frozen=True, slots=True)
class SlotDiff:
    slot_index: int                   # index into league.starting_slots (NOT roster_positions --
                                       # BN entries are never part of a starting-lineup solve)
    slot_label: str                   # "RB", "FLEX", etc. -- starting_slots[slot_index]
    status: str                       # "match" | "suggested_swap" | "missed"
    current_player_id: str | None
    optimal_player_id: str | None
    delta: float                      # optimal value - current value; 0.0 when status == "match"
```

No `last_modified` on `WeeklyProjection` — unlike season projections, a
weekly projection updating right up to kickoff is freshness, not
contamination, so there's no contamination guard to feed a timestamp to
(see §3.1).

**Why a new `rosters.raw_starters` and not #2's `RosterEntry.starter_ids`:**
#2's `rosters.fetch` deliberately *compacts* the starters list — its own
spec says so explicitly: `starter_ids = [pid for pid in roster["starters"]
if pid not in ("0", None)]`. That's correct for #2's needs (power ranking
only ever asks "is this player in the starting set," never "which specific
slot") but it throws away positional alignment, and a slot-by-slot diff
needs exactly that alignment back. Sleeper's raw `roster["starters"]`
array *is* positionally aligned to the league's non-`"BN"` slots in order
(length always equals the starting-slot count, `"0"` filling any unfilled
slot) — `raw_starters` is a new, narrow function that fetches the same
`/league/{id}/rosters` endpoint #2 already calls and returns that raw
array for one `roster_id` (the tracked user's own; nobody else's slot
alignment is needed for a personal lineup diff), mapping `"0"` to `None`.
`rosters.fetch` and `RosterEntry` are unmodified — this is additive, not a
change to code #2 already shipped and reviewed.

`domain/constants.py` gains `INJURY_OUT_STATUSES = frozenset({"IR", "PUP",
"Out", "Sus"})`, promoted out of `engine/ros_value.py`'s private
`_INJURY_OUT` (#2) so both modules share one definition instead of two
copies drifting apart. `ros_value.py`'s import updates; its own behavior
is unchanged.

## 3. Ingest layer

### 3.1 `src/ffdo/ingest/sleeper/weekly_projections.py` (new)

- `fetch(sleeper: SleeperClient, season: int, week: int) -> dict[str, WeeklyProjection]`
  — `GET https://api.sleeper.app/projections/nfl/{season}/{week}?season_type=regular&position[]=QB&position[]=RB&position[]=WR&position[]=TE&position[]=DEF&position[]=K`
  (same position filter list `_load_projections` already uses). Confirmed
  live 2026-09-11 — returns the same `stats` shape `scoring.score_stats`
  already knows how to score.
- **No contamination guard.** `ingest/projections.py`'s
  `ContaminatedProjectionError` exists because *season-total* projections
  get silently wiped/overwritten post-kickoff, which is a data-integrity
  problem for a number meant to represent "the whole year." A *weekly*
  projection for week N is inherently meant to be refined right up to
  kickoff — Tuesday's week-10 projection being less accurate than
  Sunday-morning's isn't corruption, it's the projection doing its job.
  This module is a plain parser with no guard.

### 3.2 `src/ffdo/ingest/sleeper/schedule.py` (new)

- `week_games(sleeper: SleeperClient, season: int, week: int) -> list[dict]`
  — `GET https://api.sleeper.app/schedule/nfl/regular/{season}`, filtered
  to `week == week`. **Unofficial, undocumented endpoint** — not in
  Sleeper's published API docs, same risk category as #2's ESPN
  crosswalk. Confirmed live and working 2026-09-11: returns
  `{status, date, home, away, week, game_id}` per game, where `status` is
  `"pre_game"` before kickoff and transitions to (at least) `"complete"`
  once the game ends — this is the live signal locks are built on, not a
  computed date/time comparison. The `date` field is calendar-day only (no
  time-of-day), which is why lock detection uses `status`, not `date`.
- `locked_teams(games: list[dict]) -> frozenset[str]` — team abbreviations
  (home or away) of every game whose `status != "pre_game"`.
- `bye_teams(games: list[dict], all_teams: frozenset[str]) -> frozenset[str]`
  — `all_teams - {every team appearing in `games`}`. (`all_teams` comes
  from the player-profile pool's distinct `team` values, same source #2
  already loads.)
- If this unofficial endpoint ever disappears or changes shape, the
  failure mode is graceful, not fatal: §4.1 treats "no lock data available"
  as "nothing is locked yet" (the more conservative assumption — it will
  still suggest swaps for players who may have already played, worse UX
  but not wrong data, vs. treating everyone as locked and suggesting
  nothing). Logged as a warning, never raised past the endpoint.

## 4. Engine

### 4.1 `src/ffdo/engine/weekly_lineup.py` (new)

```python
def weekly_value(
    player_ids: Iterable[str],
    league,
    *,
    weekly_points: Mapping[str, WeeklyProjection],
    profiles: Mapping[str, PlayerProfile],
    bye_teams: frozenset[str],
) -> dict[str, ValuedPlayer]:
```

- Score each player's `WeeklyProjection.stats` via the existing
  `scoring.score_stats(stats, league.scoring_settings)`.
- **Exclude** (not zero-and-keep, unlike #2's rest-of-season value)
  players whose `profile.team in bye_teams` or `profile.injury_status in
  INJURY_OUT_STATUSES` — they cannot play this week at all, so they must
  not be eligible to fill a slot, not merely score low. (#2's zero-out
  approach is right for a rest-of-season asset that might recover later;
  wrong here, where "can this player take the field this Sunday" is a
  hard yes/no.)
- Feed the remaining scored points into `vor.compute(points, profiles,
  league)` unchanged — this week's VOR, on the same scale `team_lineup`
  already expects.

```python
def optimal_slots(
    valued: Mapping[str, ValuedPlayer],
    league,
) -> dict[int, str | None]:
```

- The same greedy dedicated-then-FLEX walk `engine/replacement
  .greedy_fill_slots` already performs (rank by VOR, dedicated slots claim
  first, FLEX slots take the best remaining eligible player), but walking
  `league.starting_slots` **in its original index order** and recording
  which specific index each player fills, rather than returning only the
  aggregate starters set `greedy_fill_slots` returns today. Produces the
  identical starter *set* `team_lineup` would (same ranking, same
  algorithm, `iterations=1`) — this is a slot-labeled view of the same
  computation, not a different one. `None` at an index the roster can't
  fill (fewer eligible players than starting slots at that position this
  week — a real possibility once bye/hard-out exclusion thins the pool).
  `engine/roster.team_lineup` and `engine/replacement.greedy_fill_slots`
  are **not modified** — this is new, additive code alongside them, not a
  signature change to anything #2 or draft day already depends on.

```python
def diff(
    current_starters: tuple[str | None, ...],   # from rosters.raw_starters -- positionally
                                                 # aligned to league.starting_slots
    optimal: Mapping[int, str | None],
    locked_teams: frozenset[str],
    valued: Mapping[str, ValuedPlayer],
    profiles: Mapping[str, PlayerProfile],
    league,
) -> list[SlotDiff]:
```

- Per slot index: `current = current_starters[i]`, `best = optimal.get(i)`.
- `current == best` (including both `None`) → **match**.
- Otherwise, the classification hinges on whether it's still actionable —
  whether the thing standing between "what you have" and "what's optimal"
  has already locked:
  - `current is not None` and `profiles[current].team in locked_teams` →
    **missed** (you can't bench someone who already played).
  - `current is None` (an empty slot) and `best is not None` and
    `profiles[best].team in locked_teams` → **missed** (the one player who
    could have filled this slot already played; too late now).
  - Otherwise → **suggested_swap** (still time to act).
- `delta = current_value - best_value` is negative-space framed as a gain:
  `best_value - current_value`, where each side's `value` comes from
  scoring that player's own weekly projection directly (0.0 if they have
  no projection row at all — e.g. a bye-week player, who Sleeper simply
  omits from the weekly feed) — **independent of whether that player was
  excluded from `optimal_slots`'s candidate pool.** Exclusion (§4.1) only
  governs who's eligible to be *recommended*; it must not stop the API
  from showing what a bye/injured current starter was actually projected
  for, which is exactly the number that makes a "missed" row legible as
  "you started someone with 0 projected points instead of this."
  `delta = 0.0` when `status == "match"`.
- A `None` current starter with `best` also `None` (no eligible player
  exists league-wide for that slot this week) is a **match** — nothing
  this sub-project can suggest, not a missed opportunity.

## 5. Decision ledger

New SQLite table (`data/ffdo.db`, via a small new store — same connection
pattern as `LeagueStore`, in `src/ffdo/api/store.py` or a new sibling
module `src/ffdo/api/lineup_ledger.py`):

```sql
CREATE TABLE lineup_recommendation (
    league_key TEXT NOT NULL,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    recommended_json TEXT NOT NULL,   -- the optimal_slots result + values, at first view
    actual_json TEXT NOT NULL,        -- current_starters, at first view
    recorded_at TEXT NOT NULL,
    followed TEXT,                    -- NULL | "full" | "partial" | "none"
    resolved_at TEXT,
    PRIMARY KEY (league_key, season, week)
);
```

- **Written once, on first view of that (league, week)'s lineup tab.**
  Not updated on subsequent views that same week, even as projections
  refine — the ledger's job is "what did we tell you, and what did you
  have set, the first time you looked," not a continuously-overwritten
  snapshot. (If the user never opens the Lineup tab for a given week, no
  row is written — there is nothing to grade.)
- **Resolved lazily.** Every `GET /lineup` call for a week whose row
  exists with `followed IS NULL` checks whether every game that week is
  now `"complete"` (§3.2). If so: refetch `raw_starters` (now final),
  compare slot-by-slot against `recommended_json`, and set
  `followed` = `"full"` (every slot matches), `"partial"` (some but not
  all), or `"none"` (matches only where the recommendation and the
  original actual already agreed) — plus `resolved_at`. No background
  job; resolution piggybacks on the next real request, same pattern
  `needs_attention`'s cache-warming uses in #2.
- Not surfaced in the UI in v1 beyond raw existence (no "your score this
  season" rollup screen — that's #7's scorecard). This sub-project only
  needs the row to exist and resolve correctly; #7 reads it later.

## 6. API endpoint (`src/ffdo/api/app.py`)

### 6.1 `GET /api/leagues/{league_key}/lineup`

```jsonc
{
  "nfl_week": { "season": 2026, "week": 10 },
  "week_locked": false,
  "swaps_suggested": 2,
  "diff": [
    { "slot_index": 0, "slot_label": "QB", "status": "match",
      "current": { "player_id": "421", "name": "...", "team": "...", "value": 18.2 },
      "optimal": null, "delta": 0.0 },
    { "slot_index": 4, "slot_label": "FLEX", "status": "suggested_swap",
      "current": { "player_id": "88", "name": "...", "team": "...", "value": 6.1 },
      "optimal": { "player_id": "412", "name": "...", "team": "...", "value": 11.3 },
      "delta": 5.2 },
    { "slot_index": 2, "slot_label": "RB", "status": "missed",
      "current": { "player_id": "902", "name": "...", "team": "...", "value": 2.0 },
      "optimal": { "player_id": "77", "name": "...", "team": "...", "value": 14.4 },
      "delta": 12.4 }
  ],
  "ledger": { "recorded_at": "2026-11-05T14:03:00Z", "followed": null }
}
```

- `optimal` is `null` on a `match` row (nothing to show — current already
  is optimal); populated on `suggested_swap`/`missed`.
- `current` is `null` only for a genuinely empty provider slot with no fix
  available (§4.1's `None`/`None` match case).
- `slot_label` is `league.starting_slots[slot_index]` — the real,
  provider-configured slot name (`"FLEX"`, `"SUPER_FLEX"`, etc.), not the
  position-only approximation #2's roster panel shows (see §10).
- `swaps_suggested` = count of `status == "suggested_swap"` rows — the
  sub-strip's "{N} swaps suggested" headline count.
- Fetched **lazily** by the frontend when the Lineup tab is first opened,
  not bundled into `/season`'s payload — different refresh cadence
  (§5's caching), and `/season` shouldn't grow to fetch weekly
  projections + schedule data on every season-screen load when most opens
  won't even visit the Lineup tab.

### 6.2 Caching

| data | cache |
|---|---|
| weekly projections | new `_weekly_proj_cache_for(season, week)`, `_TTLCache(900s)` — shorter than #2's 24h caches; this data is meant to move through the week |
| schedule/lock status | new `_schedule_cache_for(season, week)`, `_TTLCache(900s)` — locks change on game day, a 15-minute window is a reasonable staleness bound for "can I still swap this player" |
| current starters (rosters.fetch) | **not cached**, same as #2 — always fresh |

### 6.3 Errors

Same posture as `/season` (#2 §5.3): 404 unknown league, 400 ESPN missing
credential, 502 `except (httpx.HTTPError, RuntimeError)` on a provider
outage. `draft_status != "complete"` → this endpoint isn't reachable yet
(no Lineup tab exists pre-season-mode); not a case this endpoint needs to
handle specially. A week with `week_locked: true` still returns 200 with
the full diff (frozen against the ledger's recorded recommendation) —
useful as a "here's what happened" retrospective, not just a live tool.

## 7. Frontend (`src/ffdo/web/season/`)

- `season.js`'s right-panel tab bar becomes `[Lineup | Power ranking |
  Draft capital]` (dynasty/keeper) or `[Lineup | Power ranking]`
  (everything else) — **Lineup first**, shown by default when the season
  screen opens, ahead of Power ranking.
- Lazy-loaded: `/lineup` is fetched only when the Lineup tab is (or
  starts as) active, not as part of `mountSeason`'s initial `/season`
  fetch.
- Layout: header line `"Week {N} lineup — {swaps_suggested} swaps
  suggested"` (or `"Week {N} — locked, review below"` when
  `week_locked`). Below it, the slot list in `starting_slots` order:
  `slot chip · current player · value` on a **match** row (plain);
  `slot chip · current player (struck / muted) → optimal player · +N.N
  pts` on a **suggested_swap** row (highlighted); the same shape but
  greyed with a "missed" marker instead of an arrow on a **missed** row.
- Empty diff (`diff: []`, only possible if the roster somehow has zero
  starting slots) → same "Roster not available" empty state #2 already
  established for its own roster panel.

## 8. Testing

`uv run pytest` green at every task.

### Ingest (`tests/ingest/sleeper/`, `MockTransport`)

- `test_weekly_projections.py` — position-filtered URL, `stats` shape
  parses into `WeeklyProjection`; no contamination guard fires regardless
  of `last_modified`-shaped input (there is no such field to check).
- `test_schedule.py` — `week_games` filters to the requested week;
  `locked_teams` includes both home/away of a non-`pre_game` game and
  excludes both sides of a `pre_game` one; `bye_teams` = the full team set
  minus everyone appearing in that week's games.

### Engine (`tests/engine/`, hand-built fixtures)

- `test_weekly_lineup.py` —
  - `weekly_value` excludes a bye-team player and a hard-out player
    entirely (not present in the output, not zeroed) while a merely
    "Questionable" player is included normally.
  - `optimal_slots` produces the same starter set `team_lineup` would for
    an identical roster/league (cross-check against `engine/roster
    .team_lineup`'s existing behavior) — proves the slot-aware version
    isn't a second, divergent algorithm.
  - a FLEX slot's optimal occupant is whichever FLEX-eligible player has
    the highest weekly VOR among those not already claimed by a dedicated
    slot — mirrors #2's existing FLEX-attribution test precedent.
  - `diff`: current == optimal → `match`; current != optimal + current's
    team unlocked → `suggested_swap` with correct `delta`; current !=
    optimal + current's team locked → `missed`; both `None` → `match`.

### API (`tests/api/`)

- `test_lineup_endpoint.py` —
  - well-formed payload for a tracked, drafted league: `diff` covers every
    `starting_slots` index, `swaps_suggested` matches the count of
    `suggested_swap` rows.
  - a mocked "week complete" schedule response → `week_locked: true`.
  - ledger: first call creates a `lineup_recommendation` row; a second
    call the same week does **not** overwrite `recommended_json`/
    `actual_json` (only `followed`/`resolved_at` can change, and only once
    the week is locked); a call with all games `complete` sets `followed`
    correctly for a fully-matching, partially-matching, and
    zero-matching final roster.
  - 404/502/400 error-contract parity with `/season`.

### Frontend

No automated tests (repo convention). Manual verification via `run`
against a real tracked league with real players on both a Thursday-game
team and a Sunday-game team mid-week (to see live lock state differ),
plus a controller browser smoke: Lineup tab loads lazily (not on initial
season-screen open), suggested swaps render with the right delta sign,
a locked player's slot never appears as swappable, tab order is `[Lineup
| Power ranking | ...]`.

## 9. Config / repo changes

- New files only (§3, §4, §5, §7) + new tests. No new dependencies.
- New table `lineup_recommendation` in `data/ffdo.db` (no schema
  migration tooling in this repo yet — same posture as #1's one-shot
  `session.json` migration; a fresh `CREATE TABLE IF NOT EXISTS` at
  startup, matching `LeagueStore`'s existing pattern).
- `domain/constants.py` gains `INJURY_OUT_STATUSES` (promoted from
  `ros_value.py`, §2.1).
- `README.md` — one line noting the Lineup tab.

## 10. Open questions / deferred

- **`ingest/sleeper/schedule.py` is an unofficial, undocumented Sleeper
  endpoint** — same risk category as #2's ESPN crosswalk. Confirmed live
  2026-09-11; if it ever breaks, §3.2 degrades to "nothing is locked yet"
  rather than failing the endpoint.
- **`NFL_BYE_WEEKS` (#2) could now be schedule-derived instead of
  hand-maintained** — this sub-project's schedule ingest returns the
  *full season's* games in one call (confirmed: 273 games, all weeks), not
  just the current week, so computing every week's bye teams from it and
  replacing #2's placeholder table is now cheap. Not done here (out of
  this sub-project's actual need, which is current-week-only), but flagged
  as a ripe, low-cost follow-up that would resolve #2's final-review
  finding about that table's honesty.
- **Ledger "follow rate" scorecard** — this sub-project only writes and
  resolves the row; #7 (outcome tracking & calibration) is what surfaces
  it to the user.
- **Slot-level `optimal_slots` vs. #2's `_your_roster_payload` slot
  labeling** — #2's final review flagged that its own roster panel shows
  `slot: position` rather than the provider's real FLEX/SUPER_FLEX label
  (a documented, accepted Minor gap). This sub-project's API response
  surfaces `slot_label` from `league.starting_slots[slot_index]` directly
  — the *real* label, unlike #2's roster panel. Not reconciling #2's
  display in this sub-project (separate screen, separate gap), noting the
  inconsistency exists between the two panels.
