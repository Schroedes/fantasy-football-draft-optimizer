# Command-Center Home — Design

## Overview

The last item from the multi-league season companion initiative's original
deferred-follow-ups list (deferred since sub-project #1's brainstorming,
2026-09-02). Today, `#/` silently auto-redirects into your last-viewed
league (or the first tracked one) — there's no view that shows every
tracked league at once. This adds one: a grid of cards, one per tracked
league, each showing your starting roster, power rank, and — reusing
every feature already shipped this initiative — flags for a suggested
lineup swap, a recommended waiver add, or a found trade target, so you
can tell which of your leagues needs you *right now* without opening
each one in turn.

## Card content

Each season-mode (post-draft) league's card shows:

- Team name, provider, format, record.
- Power rank — the exact same computation the season screen's own
  top-line "POWER RANK" tile already uses
  (`power_ranking.rank(..., position="OVR", scope="full")`), plus the
  delta vs. raw standings rank that tile already shows.
- **Sleeper leagues only**: this week's opponent name and both teams'
  projected scores, live-updating (see "Matchup & projected score"
  below). Omitted entirely for ESPN leagues and for a bye week.
- The full starting lineup, one row per slot (position chip + player
  name + this-week value), reusing the Lineup tab's own diff — if a
  slot has a suggested swap, that row shows the swap inline (current
  player struck through, suggested replacement below it in the same
  row) rather than a separate list.
- Up to three flag chips, each only shown when non-empty: lineup swaps
  suggested (amber), waiver adds recommended (green, FAAB leagues
  only), trade targets found (cyan, Sleeper leagues only). A league
  with nothing to flag shows no flags section at all — never an empty
  "0 flags" placeholder.

Validated against a real 3-card grid mockup (one flagged league, one
clean league, one pre-draft league) during brainstorming — the full
detailed card holds up fine at 3 cards per row; no need to fall back to
a slimmer summary-only card. (That mockup predates the matchup/score
row below — a follow-up mockup isn't required, since it's one more row
of text using the same design tokens as the rest of the card, not a new
layout question.)

## Matchup & projected score

Sleeper-only for this pass (see "Deferred / out of scope" for why ESPN
isn't included) — and **no win probability**, per an explicit scoping
decision: Sleeper's own `matchups/{week}` endpoint (confirmed against
their published API docs) returns only `points` and `custom_points`,
never a projected-points or win-probability field, so a probability
would mean building a real statistical model here, not exposing
already-available data. Deferred; can be picked up as its own follow-up
later if wanted.

What IS built:

- New ingest, `ingest/sleeper/matchups.py::current_matchup(sleeper, league_id, week) -> dict[int, int]`
  — fetches `matchups/{week}` (the same endpoint `actuals.points_so_far`
  already calls for historical weeks, just for the single current week)
  and groups rows by `matchup_id` to produce a `roster_id -> opponent_roster_id`
  map. A roster absent from every pairing that week (odd team count) has
  a bye; the home-summary endpoint reads that as "no matchup this week,"
  not an error.
- Both teams' projected scores reuse the exact same per-player
  weekly-value function the Lineup tab's `weekly_lineup` module already
  computes (a player's actual points if their game is final, else their
  weekly projection) — run once over your own starters and once over
  the opponent's starters. `rosters_mod.fetch()` already returns every
  roster in the league, not just yours, so the opponent's `starter_ids`
  are already in hand; no new per-roster fetch. No new scoring model —
  the same function, applied to a second roster.
- **Live updates**: Sleeper's own `points` field updates through the day
  as games play, and every ingest call behind it is already
  server-side TTL-cached (see the Endpoint section's caching note), so
  `home.js` re-polls each season-mode league's `home-summary` on a 60s
  interval while the home screen stays mounted — matching the reasoning
  already used for `schedule_caches`' own 60s TTL elsewhere in this
  codebase ("short enough to matter at kickoff, long enough not to
  hammer anything"). The interval clears on unmount, the same lifecycle
  discipline `board.js`'s existing live-draft poller already follows.

## Pre-draft cards

A league whose draft hasn't completed gets a minimal card: team name,
provider, format, and its `draft_status` as plain text (e.g. "Pre-draft"
or "Drafting now"). **No draft-start countdown** — Sleeper's own draft
start-time isn't ingested anywhere in this codebase today (confirmed by
grep; nothing tracks `start_time`/`scheduled` for a draft), so a
countdown would need new ingest work. Out of scope for this pass;
flagged as a possible future follow-up, not invented here. Clicking a
pre-draft card opens that league's existing draft board.

Pre-draft cards render immediately from data `GET /api/leagues` (the
existing switcher endpoint) already returns — they never call the new
per-league summary endpoint at all, since there's nothing for it to
compute yet.

## Endpoint: `GET /api/leagues/{league_key}/home-summary`

One endpoint per league, not one combined response for the whole grid —
confirmed during brainstorming specifically so the frontend can fire
every tracked league's request in parallel and let each card resolve
independently, rather than one request blocking the whole grid on
whichever league is slowest (or letting one league's provider outage
take down every card at once).

Composes, by calling the same internal functions each existing tab's
endpoint already calls — no new computation invented:

- Power rank: `power_ranking.rank(..., position="OVR", scope="full")`.
- Starting roster + swap diff: the same `weekly_lineup` diff logic
  `GET /api/leagues/{key}/lineup` already computes.
- Waiver count: `waiver_value.recommend_adds`, only when the league is
  FAAB (same gate the existing Waivers tab applies — skipped entirely
  otherwise, not computed-and-hidden).
- Trade target count: `trade_targets.suggest_for_team` against every
  other team, only when `lg.provider == "sleeper"` (same gate the
  existing Targets tab/endpoints apply).
- Matchup + projected scores: `matchups.current_matchup` plus the
  reused per-player weekly-value function (see "Matchup & projected
  score" below), only when `lg.provider == "sleeper"`.

Response shape:

```json
{
  "league_key": "sleeper:L1:2026",
  "name": "Nacua Cheese & Coker Cola",
  "provider": "sleeper",
  "resolved_format": "redraft",
  "record": {"wins": 0, "losses": 0, "ties": 0},
  "power_rank": {"value": 1, "of": 12, "delta_vs_standings": 7},
  "matchup": {
    "opponent_name": "America's Next Top Waddle",
    "your_projected": 118.4,
    "opponent_projected": 109.2
  },
  "starters": [
    {"slot_label": "QB", "name": "Dak Prescott", "value": null,
     "status": "missed", "swap_to": null},
    {"slot_label": "RB", "name": "Jahmyr Gibbs", "value": 11.1,
     "status": "match", "swap_to": null},
    {"slot_label": "FLEX", "name": "Michael Pittman", "value": null,
     "status": "suggested_swap", "swap_to": "Travis Kelce"}
  ],
  "flags": {
    "lineup_swaps": 1,
    "waiver_adds": 2,
    "trade_targets": 1
  }
}
```

`status`/`swap_to` on a starter row reuse the Lineup tab's own diff
vocabulary (`match` / `suggested_swap` / `missed`) directly — no new
status enum invented. A league with no swap suggested has every row at
`status: "match"`.

Sleeper-only/non-FAAB leagues simply omit the corresponding count from
`flags` (not `0` — omitted, so the frontend's "no flags" check is a
single "is this object empty" test, matching how the rest of this
codebase already treats a category that doesn't apply as absent rather
than zeroed). `matchup` is `null` for an ESPN league, and `null` for a
Sleeper league on a bye week that week — both render as "no matchup
section" on the card, the same absent-not-zeroed treatment.

On a provider outage for this one league, the endpoint returns `502`
exactly like every other per-league endpoint in this codebase already
does — the frontend catches it per-card (see below), not per-grid.

## Frontend

- New `web/home/home.js` + `home.css`, following `board.js`/`season.js`'s
  existing house style: plain ES module exporting `mount()`, no
  framework, module-level singleton state (only one home screen is ever
  mounted at a time).
- `app.js`'s router: `#/` now renders home instead of auto-redirecting
  into the last-viewed league. The existing "last-viewed league"
  convenience isn't lost — home's own switcher-adjacent affordance (a
  "Continue: <league name>" link, using the same `localStorage` key
  `app.js` already writes on every league visit) takes you straight
  there in one click when you do want to jump back in.
- The switcher `<select>` in the top bar is unchanged — still there for
  quick direct jumps once you're inside a league. A small "Home" link
  next to it returns to the grid from anywhere.
- `mount()` calls the existing `GET /api/leagues` once, renders every
  card immediately (pre-draft cards fully rendered from that response
  alone; season-mode cards in a loading skeleton), then fires
  `GET /api/leagues/{key}/home-summary` for every season-mode league in
  parallel and fills each card in as its own response lands.

## Card ordering

Alphabetical by league name — stable across visits, cards don't reshuffle
position between one load and the next just because a flag appeared or
cleared.

## Flag click-through

Each flag chip is its own link, not just decoration on the card:

- "N waiver adds" → opens that league directly on the **Waivers** tab.
- "N trade target(s)" → opens directly on the **Targets** tab.
- The lineup-swap flag (or any struck-through starter row) → opens
  directly on the **Lineup** tab.
- Clicking the card body itself (not a flag) → opens the league at its
  normal default tab (Lineup), same as today's switcher.

## Error handling

- No tracked leagues at all: home redirects to `#/connect`, exactly like
  today's `#/` does — unchanged behavior for a brand-new user.
- One league's `home-summary` fails (`502`, network error): only that
  card shows a compact inline error with a retry button that re-fires
  just that league's request. Every other card is unaffected — mirrors
  how a single tab failing today doesn't break the rest of the season
  screen.

## Testing

- Backend: `tests/api/test_home_summary_endpoint.py`. Reuses the
  existing shared minimal fixture (`_ROSTERS`/`_PROJ`/`_PLAYERS` from
  `test_season_endpoint.py`) for the no-flags/empty-response-shape path,
  a hand-built local fixture (following Trade Targets' own established
  pattern: literal hand-picked values via a `_vp()`-style helper,
  bypassing the real valuation pipeline, so expected values can be
  hand-verified rather than hand-derived through the blended-projection
  pipeline) for a case producing a genuine swap + waiver + trade-target
  flag, plus the Sleeper-only and non-FAAB gating cases (flags key
  omits the gated category entirely). Also: a two-team fixture with a
  real `matchup_id` pairing for the matchup/projected-score case, an
  odd-team-count fixture (one roster with no pairing that week) for the
  bye-week `matchup: null` case, and an ESPN-league case confirming
  `matchup` is `null` there too.
- New `tests/ingest/test_sleeper_matchups.py` for
  `current_matchup` directly: a normal even-league pairing, and the
  odd-team-count bye case, both against a hand-built `matchups/{week}`
  response fixture (not a live call).
- Frontend: no JS test framework exists in this repo (confirmed — no
  `package.json`, no JS test runner). Verified live against the real
  dev server (`ffdo-api`) and real tracked leagues — the grid renders,
  cards fill in as their data arrives, a flag click lands on the right
  tab, a simulated per-league failure (mocked `fetch`, same technique
  used to verify Trade Targets' click-through) shows only that one card
  in an error state, and the matchup/score row visibly updates after
  the 60s poll fires (checked by watching a network log across two poll
  cycles, not by waiting for a real game to be live).

## Deferred / out of scope

- A live draft-start countdown on pre-draft cards — needs new ingest
  (Sleeper's draft `start_time`/`scheduled_time` isn't tracked
  anywhere in this codebase today). Worth a small follow-up once
  someone wants it.
- Combining all leagues' summaries into one request — explicitly
  rejected in favor of per-league parallel requests, for both load-time
  and failure-isolation reasons (see Endpoint section above).
- Win probability on the matchup row — explicitly scoped out. Neither
  provider exposes it via their read API (confirmed against Sleeper's
  own docs for this pass); building it would mean a new statistical
  model estimating score variance, which is real new work, not a data
  pull. A candidate future follow-up, not started here.
- Matchup/projected score for ESPN leagues — would need new research
  into ESPN's matchup-pairing data (a different API view than the
  `mRoster` payload this codebase currently fetches, which has no
  matchup pairing in it). Follows this initiative's established
  precedent (Trade Targets, Waivers) of shipping Sleeper-only and
  degrading ESPN gracefully rather than blocking on unverified ESPN
  research.
