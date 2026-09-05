# Roster & Standings View — Design

**Date:** 2026-09-03
**Status:** Approved, pending implementation
**Stacked on:** `claude/multi-league-fantasy-dashboard-e0d264` (PR #24, sub-project #1 — multi-league foundation). This is a stacked PR: it branches from #1 and merges after it.
**Prior art:**
- `docs/superpowers/specs/2026-09-02-multi-league-foundation-design.md` — the foundation this fills in (it explicitly defers "the 'current NFL week' service, standings math" and the `needs_attention` computation to this sub-project).
- Design mockups: `design/roster-standings/` (`Main.dc.html` redraft, `Dynasty.dc.html`) + published canvas `https://claude.ai/code/artifact/ab3258fd-6d7a-440f-8104-5f1f3d217953`.

## 0. Context: the larger initiative

Sub-project #2 of seven. The foundation (#1) shipped a multi-league store,
league-scoped routing, and an adaptive `#/league/{key}` screen that shows
the draft board pre-draft and a **"Season mode — coming soon" placeholder**
once the draft completes. This sub-project replaces that placeholder with a
real screen: your roster laid out with values, a league **power ranking**
(overall + by position group, with a starters-only vs full-roster toggle),
the provider's own standings alongside it, and — for dynasty/keeper leagues
— a **draft-capital** ranking.

Downstream dependencies this creates:
- **`engine/ros_value.roster_value(...)`** — the per-player valuation
  function. #2 implements it (rest-of-season for redraft, blended-projection
  × age-curve for dynasty). #4 (post-draft valuation model) replaces the
  whole function with a proper multi-year model; the signature and return
  type are frozen here so the swap is drop-in.
- **`ingest/nfl_state`** — the "current NFL week" service #3 (weekly optimal
  lineup) also needs.
- **`ingest/actuals`** — season-to-date actual points per player, reused by
  #3 and #7.

Not in #2: any recommendation, so **no decision ledger** (that starts at
#3). #2 is a read-only analytical view.

## 1. Purpose

Give a drafted league a screen that answers "how good is my roster, how
does it stack up, and where are my holes" — insight the provider apps
don't give: they show W-L and points; they don't show luck-adjusted roster
strength, positional depth, or (in dynasty) draft capital.

### 1.1 Goals

- **Power ranking, luck-adjusted.** Rank every team by starting-lineup
  value (the existing `engine/roster.team_lineup` VOR math, applied to
  *current* rosters), overall and per position group (QB/RB/WR/TE).
- **Starters-only vs full-roster toggle** that re-ranks every view
  (overall *and* each position) — the gap between the two is a team's
  depth quality.
- **Δ vs standings** — power-ranking spot minus standings spot, so a team
  can see it is 4th in the standings but 2nd in roster quality (or the
  reverse).
- **Your roster panel** — every player by lineup slot with a
  rest-of-season / dynasty value, positional-strength bars ranked `/12`, a
  bench-value total, and a plain-language callout ("thin at TE", "2 empty
  roster spots").
- **Values track real performance.** A player underperforming his
  preseason projection has a visibly lower value by midseason; an
  overperformer's is higher. Achieved by blending the preseason projection
  with season-to-date scoring pace, weighting toward pace as the season
  progresses.
- **Redraft vs dynasty value differently.** Redraft = rest-of-season
  (games remaining matter). Dynasty = full-season talent × an
  age/experience curve (this-season games are irrelevant to a dynasty
  asset). Driven by the league's `resolved_format` (respects the manual
  override from #1).
- **Draft-capital ranking** (dynasty/keeper, Sleeper only) — which picks
  each team owns, where each will roughly fall (reverse of current
  standings, following the *original* team), traded picks flagged with
  "via <team>". Side-by-side with the player power ranking, not blended.
- **The provider's own standings**, displayed as-is (W-L, PF, PA) — we do
  not recompute records.
- One endpoint, `GET /api/leagues/{league_key}/season`, returns everything
  the screen needs in one call; tab/toggle switches are pure client-side.

### 1.2 Non-goals

- **A blended power + draft-capital score.** Side-by-side rankings ship in
  #2; a blended number needs a real pick-value curve, which is #4's work.
  A marked "coming" affordance sits on the draft-capital tab.
- **The real dynasty valuation model.** #2's age curve
  (`engine/dynasty_curve`) and #4's model differ: #2 is a single
  hardcoded per-position multiplier; #4 does multi-year projections,
  breakout curves, contract/keeper cost, etc. #2's `roster_value` is the
  seam #4 replaces.
- **Weekly projections / an optimal-lineup solver.** That is #3. #2 uses
  season-total projections (blended with actuals); it never projects a
  single week.
- **Live in-game scoring.** The screen fetches on load and on an explicit
  refresh; it does not poll during games.
- **A decision ledger.** #2 recommends nothing.
- **ESPN parity verification.** The ESPN roster/actuals ingest ships to a
  documented shape (from community API docs) but is **not verified against
  a live ESPN league** in this sub-project — same posture as the
  foundation's ESPN fan API. Sleeper is the proven path; a follow-up
  validates ESPN. Draft capital is Sleeper-only regardless.
- **Historical / trend views.** Power ranking is a point-in-time snapshot.

### 1.3 Deployment stance

Unchanged from #1: single-user, local-first, no auth. All new state is
transient (fetched per request) or cached in-process; nothing new is
persisted to `data/ffdo.db`.

## 2. Architecture

```
GET /api/leagues/{league_key}/season
  │
  ├─ _load_league(key)  ─────────────  TrackedLeague: resolved_format, scoring_settings,
  │                                    roster_positions, num_teams, roster_id, season, provider
  ├─ nfl_state.current_week(sleeper) ─  Sleeper /state/nfl        [_TTLCache ~1h]
  ├─ players_cache.get(...)  ────────  Sleeper player pool         [_TTLCache 24h, reused from board]
  ├─ _season_proj_anchor_for(season) ─  preseason projection anchor  [_TTLCache 24h; see §4.3]
  │
  ├─ rosters.fetch(provider, league) ─  Sleeper /league/{id}/rosters + /users
  │                                     │  → list[RosterEntry]  (player_ids, starter_ids, W-L, PF, PA)
  │                                     └─ ESPN: mTeam + mRoster (+ crosswalk to Sleeper ids)
  ├─ actuals.points_so_far(...)  ────  Sleeper /league/{id}/matchups/1..week (players_points)
  │                                     └─ ESPN: mRoster appliedStatTotal per period
  │                                        → {player_id: points_banked}
  │
  ├─ ros_value.roster_value(all_rostered_pids, league, resolved_format=…,   ← THE SWAPPABLE SEAM (#4)
  │       season_proj=anchor, profiles=…, actuals=…, weeks_played=…)
  │       → {pid: ValuedPlayer}   (VOR on one scale, format-aware)
  │
  ├─ power_ranking.rank(rosters, valued, league, standings_rank, position=…, scope=…)
  │       → list[PowerRow]  ×  {OVR, QB, RB, WR, TE}  ×  {starters, full}   (10 arrays, one rank() call each)
  │
  ├─ traded_picks.capital(sleeper, league_id, standings_order)   [Sleeper + dynasty/keeper only, else None]
  │       → list[DraftPickAsset]
  │
  └─ assemble → { nfl_week, your_roster, power_ranking, standings, draft_capital }

Frontend:  web/season/season.js  exports mountSeason(container, leagueKey, meta)
           board.js's refresh() calls it (instead of the placeholder) when draft_status == "complete"
```

### 2.1 New domain types (`src/ffdo/domain/models.py`)

All `@dataclass(frozen=True, slots=True)`.

```python
@dataclass(frozen=True, slots=True)
class NflWeek:
    season: int
    week: int              # the week whose games are next / in progress (Sleeper display_week)
    season_type: str       # "pre" | "regular" | "post"
    complete: bool          # regular season over — view freezes values, shows "final"

@dataclass(frozen=True, slots=True)
class RosterEntry:
    roster_id: int
    team_name: str
    player_ids: tuple[str, ...]       # Sleeper player ids (ESPN ids crosswalked before this)
    starter_ids: tuple[str, ...]      # subset of player_ids the provider has in starting slots
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float

@dataclass(frozen=True, slots=True)
class PowerRow:
    roster_id: int
    team_name: str
    is_you: bool
    value: float              # starting-lineup VOR for the (position, scope) this row belongs to
    bench_value: float        # 0 under scope="starters"
    power_rank: int
    standings_rank: int
    # delta = standings_rank - power_rank  (derived, not stored)

@dataclass(frozen=True, slots=True)
class DraftPickAsset:
    season: int
    round: int
    projected_slot: int | None       # 1..num_teams for the next draft year; None for years beyond
    current_owner_roster_id: int
    original_roster_id: int
    via_team_name: str | None        # set when current_owner != original
```

`ValuedPlayer` (existing) is what `roster_value` returns — the ranking code
never sees a raw projection.

## 3. Ingest layer

Every module is one parser tested against `httpx.MockTransport` fixtures
(the established `tests/ingest/` pattern). No existing ingest module
changes.

### 3.1 `src/ffdo/ingest/nfl_state.py` (new)

- `current_week(sleeper: SleeperClient) -> NflWeek` — `GET /v1/state/nfl`.
  `week` from `display_week` (falls back to `week`), `season` (int),
  `season_type`. `complete = season_type == "post" or week > 18`.
- Wired into `app.py` behind a module-level `_nfl_state_cache = _TTLCache(ttl_seconds=3600)`.
- ESPN needs no separate call — its week comes from `mSettings.status.currentMatchupPeriod`
  in the roster fetch (§3.3). `nfl_state.current_week` is Sleeper-only; the
  ESPN branch builds its own `NflWeek` from the settings blob.

### 3.2 `src/ffdo/ingest/rosters.py` (new) — Sleeper

- `fetch(sleeper: SleeperClient, league_id: str) -> list[RosterEntry]` —
  `GET /v1/league/{id}/rosters` + `GET /v1/league/{id}/users`.
  - `player_ids` = `roster["players"]` (list of Sleeper ids; may be `None`
    for a never-set roster → `()`).
  - `starter_ids` = `[pid for pid in roster["starters"] if pid not in ("0", None)]`
    (Sleeper puts `"0"` in unfilled starting slots).
  - `wins`/`losses`/`ties` from `roster["settings"]`.
  - `points_for` = `settings["fpts"] + settings.get("fpts_decimal", 0) / 100`.
  - `points_against` = `settings["fpts_against"] + settings.get("fpts_against_decimal", 0) / 100`.
  - `team_name` via the same display-name resolution as `ingest/teams.parse`
    (metadata `team_name` → user `display_name` → `f"Team {roster_id}"`).
    Factor that name lookup into a shared helper `teams._display_names(users)`
    so both `teams.parse` and `rosters.fetch` use it (small refactor of
    existing code, in scope).

### 3.3 `src/ffdo/ingest/espn/rosters.py` (new) — ESPN

- `fetch(espn: EspnClient, league_id: str, season: int, crosswalk) -> tuple[list[RosterEntry], NflWeek]`
  — `?view=mTeam&view=mRoster&view=mSettings`.
  - Per team (`raw["teams"]`): `roster_id` = `team["id"]`, name from
    `team["name"]` (fallback `location + nickname`), record from
    `team["record"]["overall"]` (`wins`/`losses`/`ties`,
    `pointsFor`/`pointsAgainst`).
  - Roster entries under `team["roster"]["entries"]`: ESPN player id
    `entry["playerId"]` → Sleeper id via `crosswalk` (drop entries the
    crosswalk can't map — logged, not fatal). `starter_ids` = entries whose
    `lineupSlotId` is not in `{20, 21}` (BN, IR).
  - `NflWeek` from `raw["settings"]["status"]["currentMatchupPeriod"]`,
    `season`, `season_type = "regular"` unless `currentMatchupPeriod >`
    `settings["scheduleSettings"]["matchupPeriodCount"]` → `"post"`.
- Uses the same `_espn_crosswalk_cache_for(season)` the board endpoint uses.

### 3.4 `src/ffdo/ingest/actuals.py` (new) — Sleeper

- `points_so_far(sleeper: SleeperClient, league_id: str, through_week: int) -> dict[str, float]`
  — for `w` in `range(1, through_week + 1)`: `GET /v1/league/{id}/matchups/{w}`;
  each entry has `players_points: {player_id: float}` already scored under
  the league's settings. Sum per player across weeks. `through_week` is
  `nfl_week.week - 1` when the current week's games have not all completed,
  else `nfl_week.week` (conservative: only count fully-scored weeks; the
  frontend labels values "through week N").
- A week that 404s or returns `[]` (season not that far along) contributes
  nothing and does not raise.
- The endpoint passes this `through_week` as `weeks_played` to
  `ros_value.roster_value` (§4.1) and surfaces it in the response as
  `nfl_week.values_through_week` (an endpoint-assembled field, not on the
  frozen `NflWeek` type — the type carries the *upcoming* week; this is the
  last *fully scored* one).

### 3.5 `src/ffdo/ingest/espn/actuals.py` (new) — ESPN

- `points_so_far(mroster_raw: dict, through_week: int) -> dict[str, float]`
  — pure parse of the already-fetched `mRoster` view: each entry's
  `playerPoolEntry["appliedStatTotal"]` is season-to-date; per-period
  detail is in `entry["playerPoolEntry"]["player"]["stats"]` filtered to
  `statSourceId == 0` (actual) and `scoringPeriodId <= through_week`. Sum
  those. Crosswalk the ids as in §3.3. No extra HTTP call.

### 3.6 `src/ffdo/ingest/sleeper/traded_picks.py` (new)

> New subpackage `src/ffdo/ingest/sleeper/` with `__init__.py`, mirroring
> `src/ffdo/ingest/espn/`. Only this one module lives there for now.

- `capital(sleeper: SleeperClient, league_id: str, num_teams: int, rounds: int, standings_order: list[int], draft_years: tuple[int, ...]) -> list[DraftPickAsset]`
  - `standings_order` is roster_ids **worst-to-best** (reverse standings =
    projected draft order for the *next* year).
  - `GET /v1/league/{id}/traded_picks` → entries `{season, round, roster_id
    (original), owner_id (current), previous_owner_id}`. Season comes back
    as a string year.
  - Build the full ownership set: for each `year` in `draft_years`, each
    `round` in `1..rounds`, each `roster_id` in `1..num_teams`, start with
    `current_owner = original = roster_id`; then apply every matching
    traded-pick entry in order (`owner_id` wins as the final holder — a
    chain A→B→C is represented by two entries and the last `owner_id` is
    C).
  - `projected_slot`: for `year == min(draft_years)` (the next draft),
    `standings_order.index(original_roster_id) + 1`; `None` for later
    years.
  - `via_team_name` = the current owner's team name when
    `current_owner != original`, else `None` (caller passes a
    `roster_id -> name` map, or the endpoint fills names in after).
  - `draft_years` default: `(nfl_week.season + 1, nfl_week.season + 2)`.
    `rounds` from the league's draft settings (`settings.draft_rounds`),
    default 4 for a startup-style dynasty rookie draft — read from the
    stored `TrackedLeague.raw_settings` where present.
- **Guard:** the endpoint calls this only when
  `resolved_format in ("dynasty", "keeper")` **and** `provider == "sleeper"`.
  A 404, a `RuntimeError` from the retry wrapper, or `[]` with no keeper
  draft configured → the endpoint sets `draft_capital = None` and the
  frontend hides the tab. Never fatal.

## 4. Engine

Pure functions, no I/O, tested with hand-built fixtures (`tests/engine/`
pattern). `engine/vor` and `engine/roster` are reused **unchanged**.

### 4.1 `src/ffdo/engine/ros_value.py` (new) — the swappable seam

```python
def roster_value(
    player_ids: Iterable[str],
    league,                                  # duck-typed: .scoring_settings, .roster_positions
    *,
    resolved_format: str,                    # "redraft" | "keeper" | "dynasty"
    season_proj: Mapping[str, SeasonProjection],
    profiles: Mapping[str, PlayerProfile],
    actuals: Mapping[str, float],            # league-scored points already banked
    weeks_played: int,
    season_weeks: int = 18,
    injury_out: Callable[[PlayerProfile], bool] | None = None,
) -> dict[str, ValuedPlayer]:
```

Steps:
1. `preseason_pts[pid] = score_stats(season_proj[pid].stats, league.scoring_settings)`
   for every `pid` in `player_ids` that has a projection **and** a profile.
2. **Blend with pace:**
   ```
   banked        = actuals.get(pid, 0.0)
   pace_full     = (banked / weeks_played) * season_weeks   if weeks_played > 0 else preseason
   w             = weeks_played / (weeks_played + K)         # K = 4  (module constant, documented)
   current_full  = preseason * (1 - w) + pace_full * w
   ```
3. **Format branch:**
   - `redraft` / `keeper`: `value_pts = max(0.0, current_full - banked)` (rest-of-season).
   - `dynasty`: `value_pts = current_full * dynasty_curve.multiplier(profile.position, profile.age, profile.years_exp)`.
4. **Injury zero-out:** if `injury_out(profile)` (default: `not profile.active`
   or `profile.injury_status` in `{"IR", "PUP", "Out", "Sus"}` — a small
   documented set), `value_pts = 0.0`.
5. `vor.compute(value_pts_by_pid, profiles, league)` → `{pid: ValuedPlayer}`.
   (Tiers are not needed here; skip `assign_tiers`.)

**Contract frozen for #4:** the signature above and the `dict[str,
ValuedPlayer]` return are the interface #4's replacement must honour.
`power_ranking` imports `roster_value` by name and never inspects its
internals.

### 4.2 `src/ffdo/engine/dynasty_curve.py` (new) — marked placeholder

```python
def multiplier(position: str, age: int | None, years_exp: int | None) -> float:
```

- Per-position peak-age and decay, hardcoded. Rough shape (documented in
  the module as provisional, to be replaced by #4):
  - `RB`: peak 23–26 → 1.10; falls ~0.06/yr after 27; floor 0.55 at 31+.
  - `WR`: peak 25–28 → 1.08; falls ~0.04/yr after 29; floor 0.6.
  - `TE`: peak 25–29 → 1.05; gentle decline; floor 0.7.
  - `QB`: peak 26–33 → 1.05; very gentle; floor 0.75.
  - `K` / `DEF`: always `1.0` (age-irrelevant).
- `years_exp == 0` (rookie): ×0.92 on top (still ramping into a role);
  `years_exp == 1`: ×0.98. `years_exp >= 2`: no adjustment.
- `age is None` → treat as the position's peak (no penalty).
- Result clamped to `[0.55, 1.15]`.
- A module docstring states plainly: this is a coarse stand-in; #4 replaces
  it with a data-driven aging model.

### 4.3 Projection anchor — `app.py` wiring

`ros_value` needs a **preseason** projection anchor, but Sleeper wipes /
overwrites projections after kickoff and `ingest/projections.parse` raises
`ContaminatedProjectionError` for post-kickoff data.

- New `app.py` helper: `_season_proj_anchor_for(season)` — a per-season
  `_TTLCache(24h)` whose loader:
  1. Tries `_load_projections(sleeper, season)` (the existing clean path).
     If it returns without raising (pre-kickoff, or a snapshot exists),
     that is the anchor.
  2. On `ContaminatedProjectionError`, retries `proj_mod.parse(raw, season,
     allow_contaminated=True)` and logs a one-line warning ("using
     post-kickoff projections as the season anchor — install a preseason
     snapshot for accuracy"). The blend's shift toward actual pace makes
     this degrade gracefully.
- A future improvement (noted, not built): snapshot the projections feed
  the first time a league is tracked before kickoff, into
  `data/snapshots/`, and prefer it here. Out of scope for #2.

### 4.4 `src/ffdo/engine/power_ranking.py` (new)

```python
def rank(
    rosters: list[RosterEntry],
    valued: Mapping[str, ValuedPlayer],
    league,                               # .roster_positions, .starting_slots, .num_teams
    standings_rank: Mapping[int, int],    # roster_id -> provider standings position (1 = best)
    *,
    position: str,                        # "OVR" | "QB" | "RB" | "WR" | "TE"
    scope: str,                           # "starters" | "full"
) -> list[PowerRow]:
```

- Per team, build `team_valued = {pid: valued[pid] for pid in roster.player_ids if pid in valued}`.
- **`position == "OVR"`:**
  - `scope == "full"` → `lineup = team_lineup(team_valued, league)`;
    `value = lineup.starting_vor + lineup.bench_vor`;
    `bench_value = lineup.bench_vor`.
  - `scope == "starters"` → `value = team_lineup(team_valued, league).starting_vor`;
    `bench_value = 0.0`.
- **`position in {QB,RB,WR,TE}`:**
  - `scope == "full"` → `value = sum(vp.vor for pid, vp in team_valued.items()
    if vp.profile.position == position)`; `bench_value` = that sum minus
    the starters' sum.
  - `scope == "starters"` → run `team_lineup(team_valued, league)` to get
    `lineup.starters`, then `value = sum(vp.vor for pid, vp in
    team_valued.items() if pid in lineup.starters and vp.profile.position
    == position)`. (Counts a WR started in FLEX toward WR — correct: it is
    a started WR.) `bench_value = 0.0`.
- Sort teams by `value` desc → `power_rank = index + 1`. Ties broken by
  `roster_id` for determinism.
- `standings_rank` from the map; `is_you` from `league.roster_id`.
- Returns the sorted `list[PowerRow]`. The endpoint calls `rank` 10 times
  (`OVR/QB/RB/WR/TE` × `starters/full`) and packs the arrays.

### 4.5 Standings rank

`standings_rank` is computed from the `RosterEntry` list in the endpoint,
**mirroring the provider's own tiebreak**: sort by `(wins, points_for)`
desc (Sleeper's and ESPN's default). This is only for the Δ column and the
`is_you` standings position; the `standings` array in the response carries
the raw provider numbers untouched so the UI can show the real table.

## 5. API endpoint (`src/ffdo/api/app.py`)

### 5.1 `GET /api/leagues/{league_key}/season`

Response:

```jsonc
{
  "nfl_week": { "season": 2026, "week": 10, "season_type": "regular", "complete": false,
                "values_through_week": 9 },
  "your_roster": {
    "roster_id": 7, "team_name": "Schroedes",
    "wins": 6, "losses": 3, "ties": 0, "points_for": 1284.6, "points_against": 1244.0,
    "power_rank": 2, "standings_rank": 4,
    "positional_rank": { "QB": 3, "RB": 1, "WR": 5, "TE": 11 },     // from the "starters" OVR-scope by default
    "bench_value": 41.2,
    "callout": "thin at TE" | "2 empty roster spots" | null,
    "players": [
      { "player_id": "4034", "name": "Bijan Robinson", "position": "RB", "team": "ATL",
        "slot": "RB", "starter": true, "value": 88.0,
        "age": 24, "bye_week": 12, "injury_status": null }
    ]
  },
  "power_ranking": {
    "overall":    { "starters": [PowerRowJSON, ...], "full": [ ... ] },
    "by_position": {
      "QB": { "starters": [...], "full": [...] }, "RB": {...}, "WR": {...}, "TE": {...}
    }
  },
  "standings": [
    { "roster_id": 1, "team_name": "The Money League", "wins": 8, "losses": 1, "ties": 0,
      "points_for": 1410.2, "points_against": 1201.0 }
  ],
  "draft_capital": null | [
    { "power_rank": 1, "roster_id": 11, "team_name": "Full Rebuild Mode", "is_you": false,
      "picks": {
        "2027": [ { "label": "1.02", "round": 1, "projected_slot": 2, "via_team_name": null } ],
        "2028": [ { "label": "R1", "round": 1, "projected_slot": null, "via_team_name": "Bench Depth" } ]
      } }
  ]
}
```

- `PowerRowJSON` = `{ roster_id, team_name, is_you, value, bench_value,
  power_rank, standings_rank, delta }` where `delta = standings_rank -
  power_rank`.
- `player.bye_week` from the player profile's team → a static
  `NFL_BYE_WEEKS[season]` map in `domain/constants.py` (a dozen entries;
  updated yearly — noted in the module).
- `player.value` is the dynasty value or the ROS value per format —
  whichever `roster_value` produced.
- `callout` logic (endpoint): if any `roster_positions` starting slot has
  0 rostered eligible players → `"N empty roster spots"`; else if a
  starting position has exactly 1 rostered player whose `vor > 0` → `"thin
  at {POS}"`; else `null`.
- `positional_rank` in `your_roster` uses the `starters` scope (the
  default the screen opens on).
- `draft_capital`, when present, is **ranked by pick count then earliest
  projected slot** (a simple, transparent ordering — not a valuation).
  `is_you` flagged. The `picks` object is keyed by draft *year* as a
  string — `str(nfl_week.season + 1)` and `+ 2` (an in-season view's next
  rookie draft is the following offseason; the mockups' year labels are
  illustrative and the real keys come from `draft_years`, §3.6).

### 5.2 Caching & freshness

| data | cache |
|---|---|
| Sleeper player pool | `players_cache` — reused, 24h |
| projection anchor | `_season_proj_anchor_for(season)` — new, 24h |
| NFL week | `_nfl_state_cache` — new, 1h |
| ESPN crosswalk / player pool | `_espn_crosswalk_cache_for` / `_espn_player_pool_cache_for` — reused |
| rosters, standings, actuals, traded picks | **not cached** — fetched fresh every call |

A "refresh" button on the screen just re-hits `/season`. No polling.

### 5.3 Errors

| situation | response |
|---|---|
| unknown `league_key` | 404 `{"detail": "League not tracked"}` (via `_load_league`) |
| ESPN league, missing/expired cookies | 400 (reuses `_require_espn_credential("the season view")`) |
| provider HTTP failure / exhausted retries | 502 `{"detail": "Couldn't reach {provider}, try again"}` — `except (httpx.HTTPError, RuntimeError)` (the foundation's pattern) |
| `draft_status != "complete"` | still 200 with a full payload — the frontend's adaptive screen decides board-vs-season from live status, same as today |
| a rostered player absent from `profiles` / `valued` | silently omitted from ranking math (they contribute 0) — logged at debug |
| dynasty league, traded-picks fetch fails | `draft_capital: null`, everything else normal |

### 5.4 `needs_attention` (foundation follow-up)

The foundation's `GET /api/leagues` returns `needs_attention: false` as a
placeholder "for #2+". #2 computes it: **`true` when the league's own
roster has fewer players than `roster_size`, or a starting slot is
unfilled.** (Lineup-not-set and bye-week starters are #3's territory — a
lineup problem, not a roster problem.) This is a cheap check in the
`GET /api/leagues` handler using a lightweight per-league roster count;
to avoid an N-provider-call fan-out on every switcher render, it is
computed from `TrackedLeague.roster_size` vs. a count cached for ~10 min
per league (`_roster_count_cache`). If the cache is cold, `needs_attention`
stays `false` until the season screen has been opened once (which warms
it). Documented as best-effort.

## 6. Frontend (`src/ffdo/web/`)

### 6.1 New module

- `web/season/season.js` — `export function mountSeason(container, leagueKey, meta)`.
- `web/season/season.css` — the two-panel layout, tables, bars, chips.
- `web/board/board.js`'s `refresh()`: where it currently calls
  `renderSeasonMode(_container, _meta)` on `draft_status == "complete"`, it
  instead does `const m = await import("../season/season.js"); m.mountSeason(_container, _leagueKey, _meta)`.
  `renderSeasonMode` (the placeholder) is deleted. The dynamic import is
  wrapped in the same try/catch the board import already has.
- `escapeHtml` — a local copy in `season.js` (as `board.js` and `app.js`
  each have), used on every provider string (team names, player names).

### 6.2 Screen (matches `design/roster-standings/`)

Two-panel, inside the shell's `#view` under the persistent switcher:

- **Sub-strip:** `NFL Week {week} · {season} · {redraft: "rest-of-season values" | dynasty: "dynasty values (age-adjusted)"}` + a refresh button. `nfl_week.complete` → `"Season complete — final"`.
- **Left panel (~440px) — your team:** team name + `record · Nth of {num_teams} · PF`; `power rank {n} / {delta:+} vs standings`; four positional-strength bars (`{pos}` · bar to rank percentile · `{n}th /12`, colored: top-3 green, bottom-3 red); roster grouped by slot — `slot chip · name · team · bye · value`, starters bright / bench muted, negative-value players red; `bench_value` total; the `callout`.
- **Right panel:**
  - **Redraft:** no top tab bar. Position sub-tabs `Overall / QB / RB / WR / TE` + `Starters only ⇄ Full roster` toggle + the table `# · Team · {value col} · Rec · PF · Δ`. Your row tinted, `YOU` badge. Δ only shown on the Overall tab (position tabs show `·`).
  - **Dynasty:** top tab bar `[ Power ranking | Draft capital ]`. Power ranking tab = the redraft content. Draft capital tab = `# · Team · {next year} picks · {year+1} picks`, pick chips (own = accent-tint, traded = amber with `via <team>`), a `blended power + capital score — coming` ghost affordance, and a legend line.
- **Tab / toggle switching is pure client-side** — all 10 ranking arrays
  are in the one `/season` payload. Only the refresh button refetches.
- **Empty states:** `players: []` or all-zero values → "Roster not
  available yet"; `draft_capital` present but empty → "No tradeable picks
  in this league".

### 6.3 Removed

`renderSeasonMode` and its placeholder markup/CSS in `board.js` /
`app.css`.

## 7. Testing

`uv run pytest` green at every task.

### Ingest (`tests/ingest/`, `MockTransport`)

- `test_nfl_state.py` — `/state/nfl` → `NflWeek`; `season_type == "post"` and `week > 18` → `complete`.
- `test_rosters.py` — starter/`"0"` filtering; `fpts` + `fpts_decimal` combine; `players: None` → `()`; team-name resolution (metadata → display_name → fallback); the `teams._display_names` refactor doesn't regress `test_teams.py`.
- `tests/ingest/espn/test_rosters.py` — `mTeam`/`mRoster` parse; crosswalk-miss dropped not fatal; `lineupSlotId` 20/21 excluded from starters; `NflWeek` from `currentMatchupPeriod`.
- `test_actuals.py` — sums `players_points` over weeks 1..N; a week returning `[]`/404 contributes nothing; `through_week` cutoff respected.
- `tests/ingest/espn/test_actuals.py` — `appliedStatTotal` / per-period sum from `mRoster`; `scoringPeriodId` cutoff.
- `tests/ingest/sleeper/test_traded_picks.py` — implicit ownership (every roster owns its own untraded picks); a single trade; a **chain trade A→B→C lands on C**; `projected_slot` follows the **original** roster's reverse-standings position and is `None` beyond the next draft year; `via_team_name` only on moved picks; 404 / `RuntimeError` / `[]` → `[]`.

### Engine (`tests/engine/`, hand-built fixtures)

- `test_ros_value.py` —
  - **blend:** week 2 result ≈ preseason; week 12, an underperformer (banked pace well under projection) → `current_full` and VOR **below** the pure-preseason baseline; an overperformer → above.
  - **format:** same player + `actuals` + `profile`, `resolved_format="redraft"` subtracts banked → lower ROS; `"dynasty"` uses `current_full × multiplier`, does not subtract banked.
  - **injury:** `active=False` → value 0 regardless of projection.
  - **seam contract:** a signature/return-shape assertion (a test that constructs the exact kwargs and asserts `dict[str, ValuedPlayer]`) so #4's replacement has a pinned target.
- `test_dynasty_curve.py` — 24-yo RB multiplier > 30-yo RB at equal inputs; `years_exp == 0` nudged down; `age is None` → no penalty; output always in `[0.55, 1.15]`; `K`/`DEF` → `1.0`.
- `test_power_ranking.py` —
  - `position="OVR", scope="starters"` orders by `team_lineup(...).starting_vor`.
  - flipping `scope` to `"full"` **changes the order** (a deep team with a strong bench climbs).
  - `position="RB", scope="starters"` counts a WR-in-FLEX toward WR, an RB-in-FLEX toward RB, and **excludes** benched RBs; `scope="full"` includes them → different order.
  - `delta = standings_rank - power_rank`, sign correct (roster better than record → positive).
  - `is_you` set from `league.roster_id`; tie-break by `roster_id` is deterministic.

### API (`tests/api/`, `TestClient` + real `LeagueStore`)

- `test_season_endpoint.py` —
  - a tracked completed-draft Sleeper league → well-formed payload; `power_ranking.overall.starters` and `.full` and all four `by_position` present; `your_roster.players` non-empty with `value`/`bye_week`/`starter`.
  - **redraft** league → `draft_capital: null`; **dynasty** league (Sleeper, with a mocked `/traded_picks`) → a list, `is_you` flagged, chip labels like `"1.02"`.
  - 404 unknown key; 502 on a recording client that raises `httpx.ConnectError`; `draft_status != "complete"` still 200.
  - **cross-format guard:** the *same* mocked roster + projections + actuals in a `resolved_format="redraft"` league vs a `"dynasty"` league produces **different** `your_roster.players[i].value` and a **different** `power_ranking.overall.starters` order — proves `resolved_format` threads through `roster_value`.
  - `needs_attention` in `GET /api/leagues` flips to `true` for a league whose roster is short of `roster_size` (once warmed).

### Frontend

No automated tests (repo convention). Manual verification via `run`
against a real tracked league (redraft + dynasty), plus a controller
browser smoke: placeholder → real screen on a completed league; redraft
right panel (no top tabs) vs dynasty (`Power ranking | Draft capital`
tabs); position + scope toggles re-rank without a network call; refresh
button refetches; `escapeHtml` on a team name containing markup.

## 8. Config / repo changes

- New files only (§3, §4, §6) + new tests. No new dependencies.
- `src/ffdo/domain/constants.py` gains `NFL_BYE_WEEKS: dict[int, dict[str, int]]`
  (per-season, team-abbrev → bye week) — a hand-maintained table, ~32
  entries for 2026, with a comment to update it each August.
- New subpackage `src/ffdo/ingest/sleeper/` (`__init__.py` + `traded_picks.py`).
- `README.md` — one line under the app description noting the season view.
- No `.gitignore` change (nothing new persisted).

## 9. Open questions / deferred

- **ESPN not live-validated** — §1.2. Ship to the documented shape;
  a follow-up connects a real ESPN dynasty/redraft league and fixes the
  parser against reality.
- **Preseason projection snapshot** — §4.3. #2 uses `allow_contaminated`
  as the mid-season fallback; a proper pre-kickoff snapshot into
  `data/snapshots/` is a later improvement (and would also serve #7's
  calibration).
- **The dynasty age curve is coarse** — §4.2. It is explicitly a
  placeholder; #4 owns the real model. The `roster_value` seam is what
  makes that swap clean.
- **`NFL_BYE_WEEKS` is hand-maintained** — acceptable for a personal tool;
  an ingest from the schedule endpoint is possible later if it drifts.
- **Blended power + capital score** — the marked fast-follow; needs #4's
  pick-value curve.
