# ESPN League Support — Design

**Date:** 2026-08-23
**Status:** Approved, pending implementation
**Prior art:** `docs/superpowers/specs/2026-08-22-fantasy-football-draft-optimizer-design.md`
(the original Sleeper-only design), `docs/superpowers/specs/2026-08-22-sleeper-league-main-screen-design.md`
(the connect/session flow this feature extends).

## 1. Purpose

Add ESPN as a second supported league provider, sitting alongside the existing
Sleeper support, with as little disruption to already-shipped code as
possible.

### 1.1 Goals

- Connect an ESPN league (league ID, season, and a private league's auth
  cookies) the same way the app already connects a Sleeper league.
- Track a live ESPN snake draft and render it on the existing board UI —
  same VOR/tiers, Cost of Waiting, and roster-rankings panel a Sleeper league
  already gets.
- Keep using Sleeper's player pool, season stats, and season
  projections/ADP as the valuation input **regardless of which provider the
  live draft comes from** — this is a hard requirement, not just a
  convenience: it's what makes the two providers share literally the same
  valuation engine with zero engine-level changes.

### 1.2 Non-goals

- ESPN auction support. This league is a snake draft; auction is a real
  future extension (ESPN supports it) but is out of scope here. The connect
  flow rejects a non-snake ESPN league explicitly rather than silently
  mishandling it.
- ESPN's own projections, ADP, or historical stats. Sleeper's data is
  authoritative for all of that, for both providers, per §1.1.
- Any provider beyond Sleeper and ESPN.
- A provider-plugin architecture. Two providers is exactly the case where
  that abstraction doesn't earn its keep yet; `api/app.py` branches on
  `session.provider` directly.

## 2. What's already provider-agnostic (and why this feature is small)

`domain/models.py`, every module under `engine/`, and `api/board.py` are
already pure — no I/O, no provider-specific keys, nothing that assumes
Sleeper. `ValuedPlayer`, `DraftState`, `DraftPick`, `LeagueProfile`,
`TeamProfile` are all provider-neutral dataclasses today. That means this
feature is almost entirely new `ingest/` code that produces the same
dataclasses a Sleeper league already produces — not a rework of the parts
that already work.

The one load-bearing consequence: every ID that flows into those dataclasses
from an ESPN source — most importantly `player_id` — **must be translated
into Sleeper's ID space at the ingest boundary**, before it ever reaches a
domain dataclass. Nothing above `ingest/` should ever need to know a player
came from ESPN.

## 3. Architecture

```
ingest/espn/
  client.py      EspnClient — cookie-authenticated HTTP client
  connect.py     resolve() — league_id + season + cookies -> Session
  league.py      league settings -> LeagueProfile (via §5, §6 crosswalks)
  draft.py       draft picks -> DraftState (player IDs translated here)
  teams.py       team/member data -> dict[int, TeamProfile]
  crosswalk.py   ESPN player_id -> Sleeper player_id resolution (§4)
```

Each file mirrors an existing Sleeper adapter one-for-one
(`ingest/league.py` ↔ `ingest/espn/league.py`, etc.), same responsibility,
same "translate wire format at the boundary" rule §4 of the original design
already establishes — extended here to mean *no raw ESPN JSON key crosses
out of `ingest/espn/` either*.

### 3.1 Shared HTTP client

`ingest/client.py`'s `SleeperClient` already implements a generic
retry/backoff loop (retry on 429/5xx, fail fast on other 4xx, exponential
backoff, bounded attempts). Rather than duplicate that loop for
`EspnClient`, extract it into a shared `ingest/http.py`:

```python
def get_json_with_retry(
    client: httpx.Client, url: str, *,
    headers: dict[str, str] | None = None,
    base_delay: float = 0.0, max_attempts: int = 4,
) -> Any: ...
```

`SleeperClient.get_json` and the new `EspnClient.get_json` both become thin
wrappers over this, each supplying their own base URL conventions and (for
ESPN) auth headers. This is the same kind of extraction Task 2 of the
roster-rankings feature did for `replacement.py`'s greedy-fill loop —
behavior-preserving for `SleeperClient`, verified by its existing test suite
passing unchanged before and after.

## 4. Player-ID crosswalk

### 4.1 Primary: `espn_id` already in Sleeper's player feed

Verified against the committed snapshot (`data/snapshots/2026-08-22-draft-day/players_nfl.json.gz`):
Sleeper's `/v1/players/nfl` payload carries an `espn_id` field per player.
Coverage is real but incomplete — 6,736 of 12,221 total players, and only
1,402 of 3,043 *active skill-position* (QB/RB/WR/TE) players (46%).
Notably, coverage skews toward established veterans: Josh Allen, Christian
McCaffrey, Tyreek Hill, and Patrick Mahomes all have it; Jahmyr Gibbs and
Ja'Marr Chase — both top-of-draft fantasy assets — do not. This field alone
is not sufficient.

`ingest/players.py` gains a small additional export, reading a field it
currently parses past and discards:

```python
def espn_id_index(raw: dict[str, Any]) -> dict[str, str]:
    """player_id (Sleeper) -> espn_id, for every record that has one."""
```

### 4.2 Fallback: normalized name + team + position match

For an ESPN `player_id` with no hit in `espn_id_index`, `ingest/espn/crosswalk.py`
falls back to matching Sleeper's `PlayerProfile` pool by:

```python
def normalize_name(name: str) -> str:
    """Lowercase; strip periods, apostrophes, and suffixes (Jr/Sr/II/III/IV);
    collapse whitespace. 'Ja'Marr Chase' -> 'jamarr chase'."""
```

Match key is `(normalize_name(full_name), position)`. A unique match
resolves the crosswalk entry. Zero matches or an ambiguous (>1) match is
**excluded, not guessed** — logged with enough detail (ESPN player_id, name,
position, team) that a human can extend the crosswalk by hand later. This is
the same "explicit and logged, never silent" rule the original design
applies to its market-calibration fallback (§7.2). Team is deliberately
*not* part of the match key — a recently-traded player is exactly the case
where the two providers' team fields are most likely to disagree, and excluding
on team mismatch would silently drop players mid-relevance.

### 4.3 Interface

```python
@dataclass(frozen=True, slots=True)
class Crosswalk:
    espn_to_sleeper: Mapping[str, str]
    unmatched: tuple[str, ...]   # ESPN player_ids that resolved to nothing

def build(
    espn_id_index: Mapping[str, str],           # sleeper_id -> espn_id
    profiles: Mapping[str, PlayerProfile],       # sleeper_id -> profile
    espn_players: Mapping[str, tuple[str, str]], # espn_id -> (full_name, position),
                                                  # read directly off this league's
                                                  # draft/roster response -- not a new
                                                  # domain dataclass, just the two fields
                                                  # the fallback match needs
) -> Crosswalk: ...
```

`ingest/espn/draft.py` and `ingest/espn/teams.py` both consume
`Crosswalk.espn_to_sleeper` to translate every `player_id` they emit. A
pick or roster slot whose ESPN player_id is in `unmatched` is dropped from
the resulting `DraftState`/`TeamProfile` player list — the same "no
meaningful VOR, so exclude" precedent `engine.vor.compute` already sets for
positions the league doesn't roster.

## 5. Scoring-settings crosswalk

ESPN expresses scoring rules as `{statId: points}` pairs; Sleeper stat
*component* data (which is what's actually being scored, per §1.1) uses
named keys (`rec`, `pass_yd`, `pass_td`, `fum`, ...). A league's
`LeagueProfile.scoring_settings` must end up in Sleeper's vocabulary
regardless of provider, or `engine.scoring.score_stats()` silently scores
nothing for every rule it doesn't recognize.

`ingest/espn/league.py` needs a static table:

```python
ESPN_STAT_ID_TO_SLEEPER_KEY: dict[int, str] = {
    # e.g. { ...: "pass_yd", ...: "pass_td", ...: "rec", ...: "rec_yd", ... }
}
```

**This table's concrete `statId` values are not yet pinned down with
confidence** — ESPN's stat IDs are stable and reasonably well
cross-referenced in the fantasy-dev community (the `espn-api` Python
package's source is a checkable public reference), but asserting specific
numbers here without a real response to check them against risks shipping
silently-wrong scoring. **Building and verifying this table against your
real league's actual `mSettings` response is the first implementation
task** (§9), before any parser is written against it. The golden test this
project already runs for Sleeper (§6.1 of the original design: rescoring
must reproduce a known-good point total) applies here too, once we have a
real ESPN scoring settings response and a player whose actual fantasy score
in that league is independently knowable.

## 6. Roster-slot crosswalk

ESPN expresses lineup slots as numeric `lineupSlotId` codes. These are
widely and consistently documented in public references (unlike the
scoring stat IDs, which vary more in how thoroughly they're covered):

```python
ESPN_SLOT_ID_TO_POSITION: dict[int, str] = {
    0: "QB", 2: "RB", 4: "WR", 6: "TE", 23: "FLEX",
    16: "D/ST", 17: "K", 20: "BN", 21: "IR",
}
```

`ingest/espn/league.py` uses this to build `LeagueProfile.roster_positions`
as the same string-tuple vocabulary (`("QB","RB","RB","WR","WR","WR","TE",
"FLEX","BN",...)`) `engine.replacement`'s `FLEX_ELIGIBILITY` already
understands — standard RB/WR/TE flex maps directly onto the existing
`"FLEX"` key with zero engine changes. **Known limitation:** an ESPN
league using an exotic flex type (e.g. a superflex-like "OP" slot) has no
corresponding `FLEX_ELIGIBILITY` entry and would need one added — out of
scope unless your league actually uses one. This table, unlike §5's, is
low-risk enough to ship as-is; still confirm it against your league's real
`mSettings` response during Task 0 as a matter of the same verify-before-trust
discipline, not because it's expected to be wrong.

## 7. Connect flow and auth

### 7.1 `Session` additions

```python
@dataclass(frozen=True, slots=True)
class Session:
    ...  # all existing fields unchanged
    provider: str = "sleeper"       # "sleeper" | "espn"
    espn_s2: str | None = None
    swid: str | None = None
```

Defaults preserve backward compatibility with an existing on-disk
`data/session.json` written before this feature (a Sleeper session with no
`provider` key loads as `provider="sleeper"`). `SessionStore.load()`/`save()`
both need updating to round-trip the three new fields.

For an ESPN session: `user_id` stores the member GUID (see §7.2), `username`
stores the ESPN display name if the API exposes one conveniently (best
effort, not load-bearing), `draft_id` is synthesized as the league_id itself
(ESPN has no separate draft-resource ID the way Sleeper does — the draft is
just a view on the league object) — document this synthesis inline where
it happens so it isn't mistaken for a real ESPN identifier later.

### 7.2 Identifying "your" team without a username field

ESPN has no username-based lookup analogous to Sleeper's `/user/<username>`.
Instead, the `SWID` cookie *is* your identity: it's a GUID that also appears
in the league's member list (the `mTeam` view's `members[].id`). Cross-referencing
that GUID against `teams[].owners` gives the roster_id that's "yours" — the
same role Sleeper's `find_roster_id(rosters_raw, user_id)` already plays.
`ingest/espn/connect.py` implements the equivalent lookup; no separate
"username" input is needed on the connect form for ESPN.

### 7.3 Connect form

The existing connect form (`web/index.html` + `web/main.js`) gains a
provider toggle. Sleeper keeps its current two fields (league ID,
username). ESPN shows four: league ID, season, `espn_s2`, `SWID` — with a
short inline note on where to find them (browser devtools → Application/Storage
→ Cookies → `fantasy.espn.com`, after logging in). `SWID` is typically
wrapped in curly braces by ESPN's cookie jar (`{XXXXXXXX-XXXX-...}`); the
connect handler should accept it with or without the braces and normalize,
since a user pasting the raw cookie value verbatim is the expected case, and
rejecting on a formatting technicality would be a bad first impression.

### 7.4 Draft-type boundary

`ingest/espn/connect.py::resolve()` reads the league's draft settings; if
the draft type isn't snake, it raises `ConnectError("ESPN auction support
isn't built yet")` — the same user-facing error pattern
`ingest/connect.py` already uses for "league not found" / "username not
found" / "user not a member." This keeps the MVP boundary honest and visible
rather than letting an auction league silently hit snake-only code paths.

## 8. Live draft polling

`api/app.py`'s `get_board()` currently does two independent things: (a)
fetch players/projections from Sleeper (season-scoped, not league-scoped —
completely unaffected by provider) and (b) fetch league settings + draft
state + team identity from the connected provider. Only (b) branches:

```python
provider = session.provider if session else "sleeper"

if provider == "espn":
    espn = espn_client_mod.EspnClient(session.espn_s2, session.swid)
    try:
        lg = espn_league_mod.parse(espn.get_json(...), crosswalk)
        state = espn_draft_mod.parse(espn.get_json(...), crosswalk)
        teams = espn_teams_mod.parse(espn.get_json(...), crosswalk)
    finally:
        espn.close()
else:
    ...  # existing Sleeper path, unchanged
```

`points`, `valued`, `market.simulate_survival`, `market.cost_of_waiting`,
`board_mod.build_snake_board` — everything downstream of `lg`/`state`/`teams`
— is called identically regardless of provider, because they only ever see
Sleeper-ID-keyed, provider-neutral dataclasses. ADP for the survival
simulation stays Sleeper's aggregate ADP for both providers too, per §1.1 —
no ESPN-specific ADP is ever fetched or used.

An ESPN session has no env-var zero-config fallback the way Sleeper's
`FFDO_LEAGUE_ID` does (cookies aren't reasonably expressed as a couple of
env vars a user would hand-type) — `get_board()` requires a connected
`Session` when `provider == "espn"`.

## 9. Testing

Same convention as the rest of this project: real fixture JSON, captured
from your actual ESPN league, committed for fixture-based tests; no live
network in CI. Concretely, in build order:

1. **Fixture capture** (first task, blocks everything else touching real
   shapes): once your league ID and cookies are available, capture real
   `mSettings`/`mTeam`/`mDraftDetail` responses, save them the same way
   `data/snapshots/2026-08-22-draft-day/` holds Sleeper's. This is what
   pins down §5's scoring crosswalk and confirms §6's slot table against
   your league's actual settings.
2. **Crosswalk unit tests** — `espn_id` hit, fallback match hit (name
   variants: apostrophe, suffix), fallback ambiguous (two candidates, must
   exclude and log), fallback miss (must exclude and log).
3. **Scoring golden test** — same shape as the original design's §6.1: score
   a known player's actual stat line under the real captured scoring
   settings and confirm it matches ESPN's own displayed point total for that
   player in that league (independently knowable from the ESPN UI).
4. **Adapter tests** for `espn/league.py`, `espn/draft.py`, `espn/teams.py`
   — fixture-based, mirroring the existing `tests/ingest/test_adapters.py`
   pattern.
5. **`SleeperClient` regression** — the `ingest/http.py` extraction (§3.1)
   must leave `SleeperClient`'s existing test suite passing unchanged,
   verified before/after exactly as the roster-rankings feature's
   `replacement.py` refactor was.
6. **End-to-end connect + board smoke test** against your real league once
   fixtures and adapters are in place.

## 10. Risks

| Risk | Mitigation |
|---|---|
| ESPN scoring `statId` table wrong or incomplete | Fixture capture + golden test against a real, independently-verifiable point total (§5, §9) before shipping; adapter isolation limits blast radius to one file |
| ESPN API shape drifts (undocumented) | Same adapter isolation as every Sleeper adapter (§4 of the original design); fixture-based tests catch drift at the boundary |
| `espn_id` coverage gap (54% of active skill players) | Name+team+position fallback (§4.2), explicit exclusion + logging on no/ambiguous match rather than a silent wrong VOR |
| `SleeperClient` regression from the `ingest/http.py` extraction | Existing Sleeper test suite is the safety net, run before and after, same discipline as the recent `replacement.py` refactor |
| ESPN league turns out to be auction | Rejected explicitly at connect time (§7.4), not silently mishandled |
| Cookie credentials stored in plaintext `data/session.json` | Consistent with this app's existing single-local-user threat model (already gitignored, already true of the Sleeper session's identifying data); not a new class of risk for a local draft-day tool |
