# Sleeper mock draft support — design spec

Date: 2026-08-23

## Problem

The app currently only connects to real Sleeper leagues (league ID +
username, resolved via `ffdo.ingest.connect.resolve()`). Testing the app's
live behavior — does the board actually update correctly as picks come in,
does inflation/max-bid track correctly, does the connect flow handle edge
cases — currently requires either a real draft (rare, high-stakes, wrong
time to be debugging) or hand-built HTTP-mocked test fixtures (useful for
unit tests, but never proves the app works against Sleeper's *actual* live
API).

Sleeper's mock drafts are real, live, pollable Sleeper API objects — same
`/v1/draft/<id>` and `/v1/draft/<id>/picks` endpoints the app already uses
— that let one person draft against 9 CPU-controlled opponents, live, on
demand, as many times as wanted. Supporting them turns "does the real-time
polling actually work" from a once-a-year question into an anytime one.

This spec covers connecting to a mock draft through the existing main
screen and having it flow through the existing session/board pipeline,
with the differences from a real league's data shape handled correctly.

## Research: how mock drafts actually differ from real league drafts

Verified against a real mock draft (`draft_id 1397145756879605760`,
`https://sleeper.app/draft/nfl/1397145756879605760`) at two points — before
the human joined a slot, and mid-draft after joining slot 1 and picking
twice — and against a real user's (`Schroedes`, `user_id
461997611847512064`) actual league-draft history for contrast. Every claim
below is against real captured API responses, not documentation (Sleeper
does not publicly document mock drafts at all).

**1. `league_id` is `null`.** A mock draft has no league until (per
Sleeper's own support docs) it's explicitly "converted" into one after the
fact — that conversion is out of scope here. This breaks the existing
`connect.resolve()` chain at its first step: there is no league to fetch,
no `/league/<id>/rosters` to resolve a roster from.

**2. Mock drafts never appear in `/v1/user/<id>/drafts/<sport>/<season>`.**
Confirmed: the user's 5 real league drafts all appeared there, with real
`league_id`s; the mock draft never did, at any point (pre-draft or
mid-draft). The share link (`draft_id`) is the *only* way to reach a mock
draft via the API — there is no "list my mock drafts" endpoint, so no UI
should attempt to offer one.

**3. Scoring is a coarse preset, not a settings dict.** Real league drafts
carry `metadata.scoring_type` (a label) *and* the league object separately
carries a full `scoring_settings` dict of per-stat weights. Mock drafts
have no league object, so only the label is available. Confirmed real
values: `"ppr"`, `"half_ppr"` (×3 real leagues), `"dynasty_2qb"` (×2 real
leagues, encodes roster format, not just scoring). `"standard"` was not
observed directly but follows the same bare-word pattern.
`ffdo.domain.constants.STANDARD_HALF_PPR` is already a verified `rec: 0.5`
weights table — `ppr` and `standard` are the same table with `rec` swapped
to `1.0` / `0.0`. Anything else (`dynasty_2qb`, `idp`, `custom`, unknown) is
explicitly unsupported, not guessed.

**4. `scoring_type` can change between polls of the same draft.** The same
mock draft read `"ppr"` before the human joined and `"half_ppr"` once
drafting started (presumably the creator changed mock settings before
starting). This isn't a bug to work around — it's confirmation that scoring
must be re-derived fresh on every poll, exactly like a real league's
`scoring_settings` already is (`get_board()` re-fetches the league on every
request today; nothing here is a new pattern, just a new source).

**5. `roster_positions` must be assembled from `settings.slots_*` counts,
not read as a list.** Confirmed keys across both mock and real drafts:
`slots_qb`, `slots_rb`, `slots_wr`, `slots_te`, `slots_k`, `slots_def`,
`slots_flex`, `slots_super_flex`, `slots_bn`. The mock draft's `settings`
had no `slots_bn` at all (both before and during drafting); the user's real
league drafts always had it explicit. Bench must fall back to
`rounds − sum(starter slots)` when `slots_bn` is absent.

**6. `draft_order` only lists humans who joined a slot, keyed by
`user_id`.** Confirmed: `draft_order` was `null` before the human joined,
then became `{"461997611847512064": 1}` after joining slot 1 — a single
entry, even though the mock has 10 teams total (the other 9 are
CPU-filled and never appear in `draft_order`). `slot_to_roster_id` (e.g.
`{"1": 1, ..., "10": 10}`) is always present and maps a slot number to a
roster_id label. Together: `roster_id = slot_to_roster_id[str(draft_order[user_id])]`
when the user has joined a slot, else unresolvable (`None`) — expected and
not an error, since one can connect before a draft starts.

**7. Every pick's `roster_id` is `null`, even the human's own picks.**
Confirmed directly from `/v1/draft/1397145756879605760/picks`: the human's
own `pick_no: 1` (`picked_by: "461997611847512064"`) has `"roster_id":
null`, same as every CPU pick. Sleeper does not populate per-pick roster
ownership for mock drafts at all. The only reliable signal is
`draft_slot` (confirmed correct via snake order: `pick_no: 11` reverses to
`draft_slot: 10`, etc.), mapped through the same `slot_to_roster_id`.
**Without correcting this, every "your roster" computation in the app
(`your_spent`, `your_slots_left`, `your_dollars_left`, and the newly-added
`auction.positional_budget()`) silently stays stuck at the "fresh roster"
fallback forever, even after the connected user has drafted half a team** —
this is the most important finding in this spec and the one piece of
production behavior that would have shipped subtly broken without live
verification.

**8. `picked_by` is `""` (empty string, not `null`) for CPU auto-picks**,
and only populated for a pick the connected human actually made. Not
relied on for roster identification (draft_slot is the reliable signal);
noted so a future reader isn't confused by it.

## Non-goals

- **Converting a mock draft into a real league.** Sleeper supports this
  natively; this app has no reason to.
- **A "browse your mock drafts" list.** Confirmed impossible via the API
  (finding #2) — the only entry point is a pasted link/ID.
- **`idp`, `dynasty_2qb`, `custom`, or any other non-ppr-family scoring
  type.** Connecting to a mock draft with one of these fails with a clear,
  named error rather than silently approximating.
- **Auction-format mock drafts are not specifically verified** (the
  available live example was snake) — the design generalizes from the
  confirmed shape (`settings.budget` lives in the same place for auction
  real-league drafts, which mock drafts share the same object shape with),
  but this should get real-draft verification during implementation, not
  assumed correct from research alone.
- **A per-team ("Team 1", "Team 2", ...) display panel.** Explicitly
  requested as a near-term follow-up, not this pass. The one thing this
  spec commits to now: nothing in this feature should choke or blank out
  if a mock-draft roster has no real display name — there simply isn't a
  UI surface that shows per-roster identity yet for that to matter against.
- **Live-network tests in the automated suite.** Matches the existing
  all-`httpx.MockTransport` convention (no test in this repo makes a real
  network call). Fixtures for the new tests are built from the real
  captured JSON in this spec's Research section, not guessed shapes.

## Architecture

```
Main screen: "League" / "Mock Draft" toggle above the connect form
  Mock Draft mode: "Draft link or ID" + "Username" fields
  → POST /api/connect {draft_id, username}   (or {league_id, username} — same endpoint)
      → ffdo.ingest.connect.resolve_mock(...)
          → ffdo.ingest.mock_draft.*  (pure translation functions, shared
            with get_board()'s live per-poll path — one source of truth)
      → SessionStore.save(session)  (session.is_mock = True, league_id = "")
  → GET /api/session / GET /api/readiness   (unchanged, session-agnostic)
  → "Enter draft room" → /board
       → GET /api/board: league_id is empty ⇒ mock branch
            → single GET /draft/<id> fetch replaces BOTH the /league/<id>
              and /league/<id>/rosters calls a real league needs (mock
              drafts need fewer live calls per poll, not more)
            → mock_draft.build_league_profile(draft_raw) → LeagueProfile
            → mock_draft.resolve_roster_id(draft_raw, session.user_id)
              (re-resolved live every poll — draft_order can only appear
              after connecting, so it must never be trusted from the
              persisted Session)
            → mock_draft.backfill_roster_ids(picks, draft_raw) before
              picks reach board.py / auction.positional_budget()
```

## `ffdo/ingest/mock_draft.py` (new)

Pure translation functions, no I/O beyond what's passed in — same
convention as every other `ingest/*` module. Callable from both
`connect.resolve_mock()` (one-time, at connect) and `app.get_board()`
(every poll) so there is exactly one implementation of each rule, not two
that can drift.

- `is_mock_draft(draft_raw: dict) -> bool` — `draft_raw.get("league_id") is None`.
- `roster_positions_from_slots(settings: dict) -> tuple[str, ...]` — reads
  `slots_qb/rb/wr/te/k/def/flex/super_flex` (position code repeated once
  per slot count; `flex`→`"FLEX"`, `super_flex`→`"SUPER_FLEX"`, matching
  `ffdo.engine.replacement.FLEX_ELIGIBILITY`'s existing keys). Bench:
  `slots_bn` if present, else `rounds − sum(the above)`. Any other
  `slots_*` key present in `settings` (e.g. a flex type this function
  doesn't recognize) raises `MockDraftError` naming the unrecognized key —
  explicit refusal over a silently wrong roster shape.
- `scoring_settings_for_preset(scoring_type: str) -> Mapping[str, float]` —
  `"half_ppr"` → `STANDARD_HALF_PPR` as-is; `"ppr"` → `STANDARD_HALF_PPR`
  with `rec` replaced by `1.0`; `"standard"` → `STANDARD_HALF_PPR` with
  `rec` replaced by `0.0`. Anything else raises `MockDraftError` naming the
  unsupported preset and listing the three supported ones.
- `build_league_profile(draft_raw: dict) -> LeagueProfile` — assembles the
  above two plus `season=int(draft_raw["season"])`,
  `num_teams=int(draft_raw["settings"]["teams"])`,
  `budget=draft_raw["settings"].get("budget")` (`None` for snake, present
  for auction — same field the real-draft path already reads),
  `name=draft_raw["metadata"].get("name") or ""`,
  `status=draft_raw["status"]`, `league_id=""`.
- `resolve_roster_id(draft_raw: dict, user_id: str) -> int | None` —
  `draft_order = draft_raw.get("draft_order") or {}`; if `user_id` is a
  key, `slot_to_roster_id[str(draft_order[user_id])]`; else `None`.
- `backfill_roster_ids(picks: tuple[DraftPick, ...], draft_raw: dict) -> tuple[DraftPick, ...]` —
  for mock drafts only (caller's responsibility to check
  `is_mock_draft()` first — this function doesn't re-check, since it's
  always called from a context that already knows), returns new
  `DraftPick`s (frozen dataclass — `dataclasses.replace`) with
  `roster_id = slot_to_roster_id.get(str(pick.draft_slot))` regardless of
  the raw (always-`None`-for-mocks) value.
- `MockDraftError(Exception)` — mirrors `ConnectError`'s role: a
  user-facing reason resolution or live derivation couldn't proceed.

## `ffdo/ingest/connect.py` changes

- `resolve_mock(sleeper: SleeperClient, draft_id: str, username: str, *, now=None) -> Session`:
  1. `GET /v1/draft/{draft_id}`. 404/permanent HTTP error →
     `ConnectError("Mock draft not found")`.
  2. If `not mock_draft.is_mock_draft(draft_raw)` → `ConnectError("This looks like a real league draft — use the League ID + Username form instead")`.
  3. `lg = mock_draft.build_league_profile(draft_raw)` — a `MockDraftError`
     here (unsupported scoring/slot type) is re-raised as `ConnectError`
     with the same message (the connect endpoint only knows about
     `ConnectError`; `MockDraftError` is an ingest-layer detail).
  4. `GET /v1/user/{username}` → `user.parse()`, same as the real-league
     path, same 404 → `ConnectError("Username not found")`.
  5. `roster_id = mock_draft.resolve_roster_id(draft_raw, user_id)` — `None`
     is fine, not an error.
  6. Build and return a `Session` with `is_mock=True`, `league_id=""`,
     `draft_id=draft_id`, and the rest from `lg` / the draft object
     (`draft_type=draft_raw["type"]`, `draft_status=draft_raw["status"]`,
     `rounds=draft_raw["settings"]["rounds"]`).
- A share URL like `https://sleeper.app/draft/nfl/1397145756879605760`
  needs its trailing numeric ID extracted before reaching `resolve_mock()`
  — this parsing happens once, at the API boundary (see `/api/connect`
  below), not duplicated into `resolve_mock()` itself, so `resolve_mock()`
  always takes a bare ID like every other `ingest` function takes already-
  clean input.

## `ffdo/domain/models.py` changes

- `Session` gains `is_mock: bool` as the new last field, after `connected_at`
  — purely additive, no ordering constraint since nothing has shipped
  depending on a fixed tail yet. Session already gained `rounds` in the
  prior branch's final-review fix — no re-adding needed, just reusing it
  for the mock path too.

## `ffdo/api/session.py` changes

- `SessionStore.save()`/`load()` round-trip the new `is_mock` field, same
  pattern as every existing field.

## `ffdo/api/app.py` changes

- `POST /api/connect`: payload now accepts either `{league_id, username}`
  or `{draft_id, username}` (mutually exclusive — 400 if neither or both
  are present). A `draft_id` payload value may be a bare ID or a full
  share URL (e.g. pasted verbatim from the browser) — extract the trailing
  digit run via a small regex (`re.search(r"(\d+)/?$", value)`) before
  calling `connect_mod.resolve_mock()`. Routes to `connect_mod.resolve()`
  or `connect_mod.resolve_mock()` accordingly; both paths converge on the
  same `_SESSION_STORE.save()` + `background_tasks.add_task(_warm_caches, ...)`
  + `asdict(session)` response as today.
- `get_board()` branches on `league_id = _league_id()`: falsy (mock mode,
  since a real Sleeper league_id is always a non-empty numeric string) →
  fetch `/draft/<id>` once, build `lg` via
  `mock_draft.build_league_profile()`, resolve `roster_id` live via
  `mock_draft.resolve_roster_id()` (overriding whatever `_roster_id()`
  would otherwise return from the persisted session — the live value is
  authoritative), fetch `/draft/<id>/picks` and run them through
  `mock_draft.backfill_roster_ids()` before `draft_mod.parse()` builds the
  `DraftState` that reaches `board_mod.build_auction_board()` /
  `build_snake_board()`. Everything downstream of that point (scoring,
  VOR, tiering, auction baseline, snake survival) is completely unchanged
  — the mock branch's whole job is producing a correct `LeagueProfile` +
  `DraftState` + `roster_id`, not touching the engine.
- `players_cache`/`projections_caches` (season-keyed, from the prior
  branch) work unchanged — a mock draft's `season` field feeds the same
  cache keying as a real league's.

## Frontend changes

- Main screen: a "League" / "Mock Draft" toggle above the connect form
  (mirrors the existing Auction/Snake format toggle's visual style).
  Mock Draft mode shows one "Draft link or ID" input + the existing
  Username input. Submits `{draft_id: <raw input value>, username}` to
  `/api/connect` — the backend does the URL-vs-bare-ID normalization
  (previous section), so the frontend just passes through whatever was
  typed or pasted.
- Connected view: an amber **"MOCK DRAFT"** badge next to (not replacing)
  the existing status badge, shown whenever `session.is_mock` is true —
  distinct enough that a mock session is never mistaken for a real league
  at a glance.
- `/board`'s header strip (`web/board/index.html` / `board.js`): the same
  small "MOCK" indicator, sourced from `/api/board`'s response (add a
  top-level `"is_mock": bool` key to both `build_auction_board()`'s and
  `build_snake_board()`'s return dict — cheap, and it's the one place the
  board page can learn this without a second fetch).

## Error handling

| Condition | Message |
|---|---|
| Draft ID/link doesn't resolve (404) | "Mock draft not found" |
| Pasted a real league's draft (`league_id` present) into Mock Draft mode | "This looks like a real league draft — use the League ID + Username form instead" |
| `scoring_type` not in `{ppr, half_ppr, standard}` | "Unsupported scoring type for this mock draft: `{scoring_type}` (supported: ppr, half_ppr, standard)" |
| Unrecognized `slots_*` key | "Unsupported roster slot type for this mock draft: `{key}`" |
| Username doesn't resolve | "Username not found" (unchanged, shared with the real-league path) |
| User hasn't joined a slot yet (`draft_order` has no entry) | *Not an error* — connects successfully with `roster_id = None`, same fresh-roster fallback the board already shows for a real league with no `FFDO_ROSTER_ID` set. |

## Testing

- `tests/ingest/test_mock_draft.py` (new): unit tests for every pure
  function in `mock_draft.py`, using fixtures built from the real captured
  JSON in this spec's Research section (both the pre-draft and mid-draft
  snapshots of `draft_id 1397145756879605760`) — not synthesized shapes.
  Covers: roster_positions assembly including the `slots_bn`-absent
  fallback and the unrecognized-key error; all three scoring presets plus
  the unsupported-preset error (using the real `dynasty_2qb` value pulled
  from the user's actual league history as the negative case); roster_id
  resolution both before and after `draft_order` populates (both real
  states were captured); `backfill_roster_ids()` against the real 19-pick
  snapshot, asserting every pick's corrected `roster_id` matches its
  `draft_slot`'s entry in `slot_to_roster_id` (including confirming
  `pick_no: 1`, previously `roster_id: null`, resolves to `1`).
- `tests/ingest/test_connect.py`: add `resolve_mock()` tests
  (`httpx.MockTransport`, same pattern as the existing `resolve()` tests)
  covering the happy path, "not actually a mock" rejection, and the
  unsupported-scoring/slot errors surfacing as `ConnectError`.
- `tests/api/test_app.py`: extend `/api/connect` tests for the
  `draft_id`-payload branch (including URL-vs-bare-ID normalization) and
  the mutually-exclusive-payload 400 case; extend `get_board()` tests
  (via the existing fake-`SleeperClient` pattern) for the mock branch,
  specifically asserting the board's `your_spent`/`your_slots_left`
  reflect a pick whose raw `roster_id` was `null` — this is the direct
  regression guard for finding #7, the one that would have shipped silent.
- Manual verification: reconnect to the same live mock draft
  (`1397145756879605760`) once built, make another pick, and confirm the
  board's "your" numbers update on the next poll — this is the feature
  validating itself against the exact live data it was designed around.
