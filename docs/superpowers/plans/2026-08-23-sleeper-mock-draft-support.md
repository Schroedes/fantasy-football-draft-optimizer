# Sleeper Mock Draft Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the main screen connect to a live Sleeper mock draft (a draft with no `league_id`) alongside the existing real-league flow, so the app's live polling, scoring, and roster tracking can be verified against real Sleeper data on demand instead of only once a year.

**Architecture:** A new `ffdo.ingest.mock_draft` module holds pure translation functions (roster shape from `settings.slots_*` counts, scoring from a `metadata.scoring_type` preset, live roster_id resolution from `draft_order`, and — critically — backfilling each pick's `roster_id` from `draft_slot`, since Sleeper never populates it for mock-draft picks). `ffdo.ingest.connect` gains `resolve_mock()` alongside the existing `resolve()`, both converging on the same `Session`. `POST /api/connect` accepts either `{league_id, username}` or `{draft_id, username}`. `get_board()` branches on whether the connected `league_id` is empty (mock mode) and re-derives everything live from a single `/draft/<id>` fetch — no engine code changes.

**Tech Stack:** Same as the existing app — Python 3.12, FastAPI, httpx, pytest, vanilla JS/CSS. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-23-sleeper-mock-draft-support-design.md`

## Global Constraints

- No new dependencies.
- Every claim about mock drafts' JSON shape in this plan is backed by real captured API responses (embedded verbatim as test fixtures below) — not guessed. Where the spec described `backfill_roster_ids` as operating on `tuple[DraftPick, ...]`, this plan simplifies it to operate on the raw pick dicts *before* `ffdo.ingest.draft.parse()` runs — functionally identical, avoids reconstructing frozen `DraftPick` instances, and keeps `draft.parse()` itself completely untouched.
- `Session.is_mock: bool` is a required field (no default), consistent with how `rounds` was added in the prior branch — not defaulted like `LeagueProfile.name`/`status` were, since only 3 call sites need updating here (not 8).
- Any `scoring_type` outside `{ppr, half_ppr, standard}`, or any `slots_*` settings key outside the known set, fails explicitly (`MockDraftError` → `ConnectError`) rather than guessing.
- `ffdo.engine.*` (scoring, vor, replacement, auction, market) is NOT modified — this feature's job is producing a correct `LeagueProfile` + `DraftState` + `roster_id` for the mock case, nothing downstream changes.
- Follow existing conventions exactly: `from __future__ import annotations`, frozen `slots=True` dataclasses, lazy `from ffdo.X import Y as Y_mod` imports inside `create_app()`, `httpx.MockTransport`-based tests only (no live network calls in the suite).

---

## Task 1: `Session.is_mock` field

**Files:**
- Modify: `src/ffdo/domain/models.py`
- Test: `tests/domain/test_models.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Session.is_mock: bool` (new last field). Used by Tasks 3, 4, 5, 6.

- [ ] **Step 1: Write the failing test**

In `tests/domain/test_models.py`, update the existing `Session` construction inside `test_session_is_frozen_and_holds_the_connected_leagues_identity` to include `is_mock=False` alongside the other fields (add it right after `rounds=13,`):

```python
        draft_status="pre_draft", rounds=13, is_mock=False,
```

Add a new test right after it:

```python
def test_session_is_mock_defaults_to_nothing_it_must_be_explicit():
    """is_mock has no default -- every Session construction site must say
    explicitly whether it's a real league or a mock draft, the same way
    `rounds` was made required rather than guessable."""
    with pytest.raises(TypeError):
        Session(
            username="tester", user_id="U1", league_id="L1", draft_id="D1",
            roster_id=3, league_name="Test League", season=2026, num_teams=12,
            budget=200,
            roster_positions=("QB", "BN"),
            scoring_settings={"rec": 0.5}, draft_type="auction",
            draft_status="pre_draft", rounds=13,
            connected_at="2026-08-22T00:00:00+00:00",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/domain/test_models.py -v`
Expected: FAIL — `TypeError: Session.__init__() missing 1 required positional argument: 'is_mock'` on the FIRST test (the one you just edited), and the new test fails because no `TypeError` was expected to come from a *missing argument that doesn't exist yet* — once you add the field, re-run to confirm both settle correctly (see Step 4).

- [ ] **Step 3: Implement**

In `src/ffdo/domain/models.py`, add `is_mock: bool` as the new last field of `Session`:

```python
@dataclass(frozen=True, slots=True)
class Session:
    """A connected Sleeper league/user/draft, as resolved by
    `ffdo.ingest.connect.resolve()` and persisted by `ffdo.api.session.SessionStore`.
    """
    username: str
    user_id: str
    league_id: str
    draft_id: str
    roster_id: int | None
    league_name: str
    season: int
    num_teams: int
    budget: int | None
    roster_positions: tuple[str, ...]
    scoring_settings: Mapping[str, float]
    draft_type: str
    draft_status: str
    rounds: int
    connected_at: str
    is_mock: bool
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/domain/test_models.py -v`
Expected: PASS — both tests, including the new one (now `is_mock` is genuinely required, so omitting it raises `TypeError` as asserted).

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/domain/models.py tests/domain/test_models.py
git commit -m "feat: add Session.is_mock to distinguish mock drafts from real leagues"
```

---

## Task 2: `ffdo.ingest.mock_draft` — the core translation logic

This is the task that carries the feature's actual correctness. Every fixture below is real, captured Sleeper API output (draft_id `1397145756879605760`, a live mock draft, fetched before and after the connecting user joined a slot — and one real scoring_type value pulled from the same user's actual league history for the negative test).

**Files:**
- Create: `src/ffdo/ingest/mock_draft.py`
- Test: `tests/ingest/test_mock_draft.py`

**Interfaces:**
- Consumes: `ffdo.domain.constants.STANDARD_HALF_PPR` (existing), `ffdo.domain.models.LeagueProfile` (existing).
- Produces:
  - `MockDraftError(Exception)`
  - `is_mock_draft(draft_raw: dict) -> bool`
  - `roster_positions_from_slots(settings: dict) -> tuple[str, ...]`
  - `scoring_settings_for_preset(scoring_type: str) -> Mapping[str, float]`
  - `build_league_profile(draft_raw: dict) -> LeagueProfile`
  - `resolve_roster_id(draft_raw: dict, user_id: str) -> int | None`
  - `backfill_roster_ids(picks_raw: list[dict], draft_raw: dict) -> list[dict]`

  Used by Task 3 (`connect.resolve_mock`) and Task 5 (`app.get_board`'s mock branch).

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/test_mock_draft.py`:

```python
import pytest

from ffdo.domain.constants import STANDARD_HALF_PPR
from ffdo.ingest import mock_draft

# Real captured /v1/draft/<id> response, BEFORE the connecting user joined a
# slot (draft_order is null -- no one has claimed a seat yet).
MOCK_DRAFT_PRE_DRAFT = {
    "created": 1787468015451,
    "creators": ["461997611847512064"],
    "draft_id": "1397145756879605760",
    "draft_order": None,
    "last_message_id": "1397145756879605760",
    "last_message_time": 1787468015451,
    "last_picked": None,
    "league_id": None,
    "metadata": {"description": "", "name": "", "scoring_type": "ppr"},
    "season": "2026",
    "season_type": "regular",
    "settings": {
        "autostart": 0, "cpu_autopick": 1, "pick_timer": 120, "rounds": 15,
        "slots_def": 1, "slots_flex": 2, "slots_k": 1, "slots_qb": 1,
        "slots_rb": 2, "slots_te": 1, "slots_wr": 2, "teams": 10,
    },
    "slot_to_roster_id": {str(i): i for i in range(1, 11)},
    "sport": "nfl", "start_time": None, "status": "pre_draft", "type": "snake",
}

# The SAME draft, re-fetched after the user joined slot 1 and drafted twice.
# Note scoring_type changed from "ppr" to "half_ppr" between the two fetches
# -- confirmed real behavior (the mock's settings were adjusted before the
# draft started), not a fixture inconsistency. This is exactly why scoring
# must be re-derived fresh on every poll, never cached from connect time.
MOCK_DRAFT_MID_DRAFT = {
    **MOCK_DRAFT_PRE_DRAFT,
    "draft_order": {"461997611847512064": 1},
    "last_message_id": "1397150696637247488",
    "last_message_time": 1787469193185,
    "last_picked": 1787469226495,
    "metadata": {"description": "", "name": "", "scoring_type": "half_ppr",
                 "show_team_names": "0"},
    "start_time": 1787469193178, "status": "drafting",
}

# Real /v1/draft/<id>/picks payload, trimmed to the picks that matter for
# the backfill test: pick_no 1 is the connecting human's own first-round
# pick (draft_slot 1); pick_no 11 is a CPU pick in round 2, where snake
# order reverses draft_slot back to 10 -- proving the backfill keys off
# draft_slot, not position-in-the-list or round-parity assumptions.
MOCK_DRAFT_PICKS_RAW = [
    {"draft_id": "1397145756879605760", "draft_slot": 1, "pick_no": 1,
     "picked_by": "461997611847512064", "player_id": "9221",
     "roster_id": None, "round": 1,
     "metadata": {"position": "RB"}},
    {"draft_id": "1397145756879605760", "draft_slot": 2, "pick_no": 2,
     "picked_by": "", "player_id": "9509", "roster_id": None, "round": 1,
     "metadata": {"position": "RB"}},
    {"draft_id": "1397145756879605760", "draft_slot": 10, "pick_no": 11,
     "picked_by": "", "player_id": "4866", "roster_id": None, "round": 2,
     "metadata": {"position": "RB"}},
]


def test_is_mock_draft_is_true_when_league_id_is_null():
    assert mock_draft.is_mock_draft(MOCK_DRAFT_PRE_DRAFT) is True


def test_is_mock_draft_is_false_for_a_real_league_draft():
    real_draft = {**MOCK_DRAFT_PRE_DRAFT, "league_id": "1389375982783180800"}
    assert mock_draft.is_mock_draft(real_draft) is False


def test_roster_positions_from_slots_matches_the_real_mock_draft():
    """settings has no slots_bn -- bench must be inferred as
    rounds - sum(starters): 1+2+2+1+1+1+2 = 10 starters, 15 rounds -> 5 bench."""
    positions = mock_draft.roster_positions_from_slots(MOCK_DRAFT_PRE_DRAFT["settings"])
    assert positions.count("QB") == 1
    assert positions.count("RB") == 2
    assert positions.count("WR") == 2
    assert positions.count("TE") == 1
    assert positions.count("K") == 1
    assert positions.count("DEF") == 1
    assert positions.count("FLEX") == 2
    assert positions.count("BN") == 5
    assert len(positions) == 15  # matches settings["rounds"]


def test_roster_positions_from_slots_uses_explicit_slots_bn_when_present():
    """Real league drafts DO carry slots_bn explicitly (confirmed against
    the user's actual "Chopped" league draft) -- when present it must win
    over the rounds-minus-starters inference."""
    settings = {
        "slots_qb": 1, "slots_rb": 2, "slots_wr": 2, "slots_te": 1,
        "slots_flex": 2, "slots_bn": 6, "rounds": 14, "teams": 19,
    }
    positions = mock_draft.roster_positions_from_slots(settings)
    assert positions.count("BN") == 6
    assert len(positions) == 14


def test_roster_positions_from_slots_handles_super_flex():
    settings = {"slots_qb": 1, "slots_super_flex": 1, "slots_bn": 2, "rounds": 4}
    positions = mock_draft.roster_positions_from_slots(settings)
    assert positions.count("QB") == 1
    assert positions.count("SUPER_FLEX") == 1
    assert positions.count("BN") == 2


def test_roster_positions_from_slots_raises_for_an_unrecognized_slot_key():
    settings = {"slots_qb": 1, "slots_idp_flex": 1, "rounds": 5}
    with pytest.raises(mock_draft.MockDraftError, match="slots_idp_flex"):
        mock_draft.roster_positions_from_slots(settings)


def test_scoring_settings_for_half_ppr_matches_the_verified_constant():
    assert mock_draft.scoring_settings_for_preset("half_ppr") == dict(STANDARD_HALF_PPR)


def test_scoring_settings_for_ppr_is_half_ppr_with_full_reception_credit():
    settings = mock_draft.scoring_settings_for_preset("ppr")
    assert settings["rec"] == 1.0
    assert settings["rec_yd"] == STANDARD_HALF_PPR["rec_yd"]
    assert settings["pass_td"] == STANDARD_HALF_PPR["pass_td"]


def test_scoring_settings_for_standard_has_no_reception_credit():
    settings = mock_draft.scoring_settings_for_preset("standard")
    assert settings["rec"] == 0.0
    assert settings["rush_td"] == STANDARD_HALF_PPR["rush_td"]


def test_scoring_settings_for_preset_rejects_dynasty_2qb():
    """dynasty_2qb is a real scoring_type value (confirmed against the
    user's actual league history) -- it encodes roster format, not a PPR
    level, and this app has no dynasty support. Must fail explicitly, not
    silently approximate."""
    with pytest.raises(mock_draft.MockDraftError, match="dynasty_2qb"):
        mock_draft.scoring_settings_for_preset("dynasty_2qb")


def test_scoring_settings_for_preset_rejects_unknown_values():
    with pytest.raises(mock_draft.MockDraftError, match="whatever_this_is"):
        mock_draft.scoring_settings_for_preset("whatever_this_is")


def test_build_league_profile_from_the_real_mock_draft():
    lg = mock_draft.build_league_profile(MOCK_DRAFT_MID_DRAFT)
    assert lg.league_id == ""
    assert lg.season == 2026
    assert lg.num_teams == 10
    assert lg.budget is None  # snake mock, no budget field
    assert lg.status == "drafting"
    assert lg.scoring_settings["rec"] == 0.5  # half_ppr, per MOCK_DRAFT_MID_DRAFT
    assert len(lg.roster_positions) == 15


def test_resolve_roster_id_is_none_before_the_user_has_joined_a_slot():
    assert mock_draft.resolve_roster_id(
        MOCK_DRAFT_PRE_DRAFT, "461997611847512064") is None


def test_resolve_roster_id_after_joining_slot_1():
    assert mock_draft.resolve_roster_id(
        MOCK_DRAFT_MID_DRAFT, "461997611847512064") == 1


def test_resolve_roster_id_is_none_for_a_user_who_never_joined():
    assert mock_draft.resolve_roster_id(
        MOCK_DRAFT_MID_DRAFT, "someone-else-entirely") is None


def test_backfill_roster_ids_corrects_every_picks_null_roster_id():
    """The core regression guard: Sleeper returns roster_id: null for every
    mock-draft pick, even the connecting human's own pick_no 1. Without this
    fix, every 'your roster' computation downstream (your_spent,
    your_slots_left, auction.positional_budget) silently stays stuck at the
    fresh-roster fallback forever."""
    backfilled = mock_draft.backfill_roster_ids(
        MOCK_DRAFT_PICKS_RAW, MOCK_DRAFT_MID_DRAFT)

    by_pick_no = {p["pick_no"]: p for p in backfilled}
    assert by_pick_no[1]["roster_id"] == 1  # draft_slot 1 -> roster_id 1 (the human)
    assert by_pick_no[2]["roster_id"] == 2  # draft_slot 2 -> roster_id 2
    # Round 2, snake-reversed: draft_slot 10 -> roster_id 10, NOT related to
    # pick_no or round in any other way -- proves the mapping is purely
    # draft_slot -> slot_to_roster_id, ignoring everything else.
    assert by_pick_no[11]["roster_id"] == 10


def test_backfill_roster_ids_does_not_mutate_the_input():
    original = [dict(p) for p in MOCK_DRAFT_PICKS_RAW]
    mock_draft.backfill_roster_ids(MOCK_DRAFT_PICKS_RAW, MOCK_DRAFT_MID_DRAFT)
    assert MOCK_DRAFT_PICKS_RAW == original
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_mock_draft.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.ingest.mock_draft'`.

- [ ] **Step 3: Implement**

Create `src/ffdo/ingest/mock_draft.py`:

```python
"""Translates a Sleeper mock draft's raw /v1/draft/<id> shape into the same
domain types a real league produces.

A mock draft has no league_id, no /league/<id>/rosters, and a coarse
`metadata.scoring_type` preset instead of a real scoring_settings dict.
Every function here is pure -- called both when a mock draft is first
connected (ffdo.ingest.connect.resolve_mock) and on every subsequent board
poll (ffdo.api.app.get_board), so there is exactly one implementation of
each rule rather than two that can drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ffdo.domain.constants import STANDARD_HALF_PPR
from ffdo.domain.models import LeagueProfile


class MockDraftError(Exception):
    """A user-facing reason a mock draft could not be translated."""


_SLOT_POSITION_MAP: dict[str, str] = {
    "slots_qb": "QB",
    "slots_rb": "RB",
    "slots_wr": "WR",
    "slots_te": "TE",
    "slots_k": "K",
    "slots_def": "DEF",
    "slots_flex": "FLEX",
    "slots_super_flex": "SUPER_FLEX",
}
_KNOWN_SLOT_KEYS = frozenset(_SLOT_POSITION_MAP) | {"slots_bn"}

_SUPPORTED_SCORING_PRESETS = ("ppr", "half_ppr", "standard")


def is_mock_draft(draft_raw: dict[str, Any]) -> bool:
    return draft_raw.get("league_id") is None


def roster_positions_from_slots(settings: dict[str, Any]) -> tuple[str, ...]:
    unrecognized = sorted(
        k for k in settings
        if k.startswith("slots_") and k not in _KNOWN_SLOT_KEYS)
    if unrecognized:
        raise MockDraftError(
            f"Unsupported roster slot type for this mock draft: {unrecognized[0]}")

    starters: list[str] = []
    for key, position in _SLOT_POSITION_MAP.items():
        starters.extend([position] * int(settings.get(key, 0)))

    if "slots_bn" in settings:
        bench_count = int(settings["slots_bn"])
    else:
        bench_count = max(0, int(settings.get("rounds", 0)) - len(starters))

    return tuple(starters) + ("BN",) * bench_count


def scoring_settings_for_preset(scoring_type: str) -> Mapping[str, float]:
    if scoring_type == "half_ppr":
        return dict(STANDARD_HALF_PPR)
    if scoring_type == "ppr":
        return {**STANDARD_HALF_PPR, "rec": 1.0}
    if scoring_type == "standard":
        return {**STANDARD_HALF_PPR, "rec": 0.0}
    raise MockDraftError(
        f"Unsupported scoring type for this mock draft: {scoring_type} "
        f"(supported: {', '.join(_SUPPORTED_SCORING_PRESETS)})")


def build_league_profile(draft_raw: dict[str, Any]) -> LeagueProfile:
    settings = draft_raw.get("settings") or {}
    metadata = draft_raw.get("metadata") or {}
    return LeagueProfile(
        league_id="",
        season=int(draft_raw["season"]),
        num_teams=int(settings["teams"]),
        roster_positions=roster_positions_from_slots(settings),
        scoring_settings=scoring_settings_for_preset(metadata.get("scoring_type", "")),
        budget=settings.get("budget"),
        name=metadata.get("name") or "",
        status=draft_raw["status"],
    )


def resolve_roster_id(draft_raw: dict[str, Any], user_id: str) -> int | None:
    draft_order = draft_raw.get("draft_order") or {}
    slot = draft_order.get(user_id)
    if slot is None:
        return None
    slot_to_roster_id = draft_raw.get("slot_to_roster_id") or {}
    roster_id = slot_to_roster_id.get(str(slot))
    return int(roster_id) if roster_id is not None else None


def backfill_roster_ids(
    picks_raw: list[dict[str, Any]],
    draft_raw: dict[str, Any],
) -> list[dict[str, Any]]:
    """Sleeper never populates a mock-draft pick's roster_id -- confirmed
    even for the connecting human's own picks. draft_slot mapped through
    slot_to_roster_id is the only reliable signal. Returns new pick dicts;
    does not mutate the input."""
    slot_to_roster_id = draft_raw.get("slot_to_roster_id") or {}
    return [
        {**pick, "roster_id": slot_to_roster_id.get(str(pick.get("draft_slot")))}
        for pick in picks_raw
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/test_mock_draft.py -v`
Expected: PASS — all 16 tests.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/mock_draft.py tests/ingest/test_mock_draft.py
git commit -m "feat: add ffdo.ingest.mock_draft to translate Sleeper mock drafts"
```

---

## Task 3: `ffdo.ingest.connect.resolve_mock`

**Files:**
- Modify: `src/ffdo/ingest/connect.py`
- Test: `tests/ingest/test_connect.py`

**Interfaces:**
- Consumes: `mock_draft.is_mock_draft`, `mock_draft.build_league_profile`, `mock_draft.resolve_roster_id`, `mock_draft.MockDraftError` (Task 2); `Session.is_mock` (Task 1).
- Produces: `connect.resolve_mock(sleeper, draft_id, username, *, now=None) -> Session`. Used by Task 5 (`/api/connect`'s dual-mode dispatch).

- [ ] **Step 1: Write the failing tests**

Add to `tests/ingest/test_connect.py` (append after the existing tests; add `from ffdo.ingest import mock_draft` to the imports at the top). Reuse the exact real fixtures from Task 2 — copy `MOCK_DRAFT_PRE_DRAFT` and `MOCK_DRAFT_MID_DRAFT` verbatim from `tests/ingest/test_mock_draft.py` into this file too (small, deliberate duplication — these are two independent test files for two independent modules, and importing test fixtures across test files is a worse coupling than repeating ~15 lines of literal JSON):

```python
from ffdo.ingest import mock_draft

MOCK_DRAFT_PRE_DRAFT = {
    "created": 1787468015451,
    "creators": ["461997611847512064"],
    "draft_id": "1397145756879605760",
    "draft_order": None,
    "last_message_id": "1397145756879605760",
    "last_message_time": 1787468015451,
    "last_picked": None,
    "league_id": None,
    "metadata": {"description": "", "name": "", "scoring_type": "ppr"},
    "season": "2026",
    "season_type": "regular",
    "settings": {
        "autostart": 0, "cpu_autopick": 1, "pick_timer": 120, "rounds": 15,
        "slots_def": 1, "slots_flex": 2, "slots_k": 1, "slots_qb": 1,
        "slots_rb": 2, "slots_te": 1, "slots_wr": 2, "teams": 10,
    },
    "slot_to_roster_id": {str(i): i for i in range(1, 11)},
    "sport": "nfl", "start_time": None, "status": "pre_draft", "type": "snake",
}
MOCK_DRAFT_MID_DRAFT = {
    **MOCK_DRAFT_PRE_DRAFT,
    "draft_order": {"461997611847512064": 1},
    "metadata": {"description": "", "name": "", "scoring_type": "half_ppr",
                 "show_team_names": "0"},
    "status": "drafting",
}
MOCK_USER_RAW = {"user_id": "461997611847512064", "display_name": "Schroedes",
                 "username": "schroedes"}


def _mock_client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def test_resolve_mock_returns_a_fully_populated_session():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/draft/1397145756879605760"):
            return httpx.Response(200, json=MOCK_DRAFT_MID_DRAFT)
        if url.endswith("/user/schroedes"):
            return httpx.Response(200, json=MOCK_USER_RAW)
        raise AssertionError(f"unexpected URL: {url}")

    session = connect.resolve_mock(
        _mock_client(handler), "1397145756879605760", "schroedes",
        now=lambda: datetime(2026, 8, 22, tzinfo=timezone.utc))

    assert session.is_mock is True
    assert session.league_id == ""
    assert session.draft_id == "1397145756879605760"
    assert session.username == "schroedes"
    assert session.user_id == "461997611847512064"
    assert session.roster_id == 1  # joined slot 1
    assert session.season == 2026
    assert session.num_teams == 10
    assert session.budget is None  # snake mock
    assert session.scoring_settings["rec"] == 0.5  # half_ppr
    assert session.draft_type == "snake"
    assert session.draft_status == "drafting"
    assert session.rounds == 15
    assert session.connected_at == "2026-08-22T00:00:00+00:00"


def test_resolve_mock_allows_connecting_before_the_draft_starts():
    """roster_id must be None, not an error, when draft_order has no entry
    for this user yet -- connecting is allowed anytime."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/draft/1397145756879605760"):
            return httpx.Response(200, json=MOCK_DRAFT_PRE_DRAFT)
        if url.endswith("/user/schroedes"):
            return httpx.Response(200, json=MOCK_USER_RAW)
        raise AssertionError(f"unexpected URL: {url}")

    session = connect.resolve_mock(
        _mock_client(handler), "1397145756879605760", "schroedes")
    assert session.roster_id is None


def test_resolve_mock_raises_when_the_draft_is_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(connect.ConnectError, match="Mock draft not found"):
        connect.resolve_mock(_mock_client(handler), "bad-id", "schroedes")


def test_resolve_mock_rejects_a_real_league_draft():
    real_draft = {**MOCK_DRAFT_MID_DRAFT, "league_id": "1389375982783180800"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=real_draft)

    with pytest.raises(connect.ConnectError, match="real league draft"):
        connect.resolve_mock(_mock_client(handler), "D1", "schroedes")


def test_resolve_mock_raises_when_username_is_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/user/ghost"):
            return httpx.Response(404, json={"error": "not found"})
        if url.endswith("/draft/1397145756879605760"):
            return httpx.Response(200, json=MOCK_DRAFT_MID_DRAFT)
        raise AssertionError(f"unexpected URL: {url}")

    with pytest.raises(connect.ConnectError, match="Username not found"):
        connect.resolve_mock(_mock_client(handler), "1397145756879605760", "ghost")


def test_resolve_mock_raises_for_an_unsupported_scoring_preset():
    dynasty_draft = {
        **MOCK_DRAFT_MID_DRAFT,
        "metadata": {**MOCK_DRAFT_MID_DRAFT["metadata"], "scoring_type": "dynasty_2qb"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=dynasty_draft)

    with pytest.raises(connect.ConnectError, match="dynasty_2qb"):
        connect.resolve_mock(_mock_client(handler), "D1", "schroedes")
```

Also add `is_mock=False` to the existing `test_resolve_returns_a_fully_populated_session` test's assertions (append a line):

```python
    assert session.is_mock is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_connect.py -v`
Expected: FAIL — `AttributeError: module 'ffdo.ingest.connect' has no attribute 'resolve_mock'`, plus the existing `is_mock` assertion fails since `resolve()` doesn't set it yet.

- [ ] **Step 3: Implement**

In `src/ffdo/ingest/connect.py`, add the import and the `is_mock=False` field to the existing `resolve()`'s `Session(...)` call, then add `resolve_mock()`:

Change the imports block:
```python
from ffdo.domain.models import Session
from ffdo.ingest import draft as draft_mod
from ffdo.ingest import league as league_mod
from ffdo.ingest import mock_draft
from ffdo.ingest import user as user_mod
from ffdo.ingest.client import V1, SleeperClient
```

In the existing `resolve()`'s return statement, add `is_mock=False,` as the last argument:
```python
    return Session(
        username=username,
        user_id=user_id,
        league_id=league.league_id,
        draft_id=draft_id,
        roster_id=roster_id,
        league_name=league.name,
        season=league.season,
        num_teams=league.num_teams,
        budget=league.budget,
        roster_positions=league.roster_positions,
        scoring_settings=league.scoring_settings,
        draft_type=state.draft_type,
        draft_status=state.status,
        rounds=state.rounds,
        connected_at=now().isoformat(),
        is_mock=False,
    )
```

Add `resolve_mock()` at the end of the file:
```python
def resolve_mock(
    sleeper: SleeperClient,
    draft_id: str,
    username: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> Session:
    now = now or (lambda: datetime.now(timezone.utc))

    try:
        draft_raw = sleeper.get_json(f"{V1}/draft/{draft_id}")
    except httpx.HTTPStatusError as exc:
        raise ConnectError("Mock draft not found") from exc

    if not mock_draft.is_mock_draft(draft_raw):
        raise ConnectError(
            "This looks like a real league draft — use the League ID + "
            "Username form instead")

    try:
        lg = mock_draft.build_league_profile(draft_raw)
    except mock_draft.MockDraftError as exc:
        raise ConnectError(str(exc)) from exc

    try:
        user_raw = sleeper.get_json(f"{V1}/user/{username}")
    except httpx.HTTPStatusError as exc:
        raise ConnectError("Username not found") from exc
    user_id, _display_name = user_mod.parse(user_raw)

    roster_id = mock_draft.resolve_roster_id(draft_raw, user_id)
    settings = draft_raw.get("settings") or {}

    return Session(
        username=username,
        user_id=user_id,
        league_id="",
        draft_id=draft_id,
        roster_id=roster_id,
        league_name=lg.name,
        season=lg.season,
        num_teams=lg.num_teams,
        budget=lg.budget,
        roster_positions=lg.roster_positions,
        scoring_settings=lg.scoring_settings,
        draft_type=draft_raw["type"],
        draft_status=draft_raw["status"],
        rounds=int(settings.get("rounds", 0)),
        connected_at=now().isoformat(),
        is_mock=True,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/test_connect.py -v`
Expected: PASS — all tests, including the 6 new ones and the updated `is_mock` assertion.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/connect.py tests/ingest/test_connect.py
git commit -m "feat: add connect.resolve_mock to connect a Sleeper mock draft"
```

---

## Task 4: `SessionStore` round-trips `is_mock`

**Files:**
- Modify: `src/ffdo/api/session.py`
- Test: `tests/api/test_session.py`

**Interfaces:**
- Consumes: `Session.is_mock` (Task 1).
- Produces: `SessionStore.save()`/`load()` now round-trip `is_mock`. Used by Task 5.

- [ ] **Step 1: Write the failing tests**

In `tests/api/test_session.py`, add `is_mock=False,` to the `_session()` helper's `base` dict (right after `rounds=13,`):

```python
        draft_status="pre_draft", rounds=13, is_mock=False,
```

Add a new test after `test_save_then_get_round_trips_rounds`:

```python
def test_save_then_get_round_trips_is_mock(tmp_path):
    store = SessionStore(tmp_path / "session.json")
    session = _session(is_mock=True, league_id="")
    store.save(session)
    assert store.get().is_mock is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_session.py -v`
Expected: FAIL — `TypeError: Session.__init__() missing 1 required positional argument: 'is_mock'`.

- [ ] **Step 3: Implement**

In `src/ffdo/api/session.py`, add `is_mock` to both `load()`'s `Session(...)` reconstruction and `save()`'s payload dict, as the new last field in each:

In `load()`:
```python
            return Session(
                username=raw["username"],
                user_id=raw["user_id"],
                league_id=raw["league_id"],
                draft_id=raw["draft_id"],
                roster_id=raw["roster_id"],
                league_name=raw["league_name"],
                season=raw["season"],
                num_teams=raw["num_teams"],
                budget=raw["budget"],
                roster_positions=tuple(raw["roster_positions"]),
                scoring_settings=raw["scoring_settings"],
                draft_type=raw["draft_type"],
                draft_status=raw["draft_status"],
                rounds=raw["rounds"],
                connected_at=raw["connected_at"],
                is_mock=raw["is_mock"],
            )
```

In `save()`:
```python
        payload = {
            "username": session.username,
            "user_id": session.user_id,
            "league_id": session.league_id,
            "draft_id": session.draft_id,
            "roster_id": session.roster_id,
            "league_name": session.league_name,
            "season": session.season,
            "num_teams": session.num_teams,
            "budget": session.budget,
            "roster_positions": list(session.roster_positions),
            "scoring_settings": dict(session.scoring_settings),
            "draft_type": session.draft_type,
            "draft_status": session.draft_status,
            "rounds": session.rounds,
            "connected_at": session.connected_at,
            "is_mock": session.is_mock,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_session.py -v`
Expected: PASS — all tests, including the new one.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/api/session.py tests/api/test_session.py
git commit -m "feat: round-trip Session.is_mock through SessionStore"
```

---

## Task 5: `app.py` — dual-mode `/api/connect` and `get_board()`'s mock branch

This is the integration task — depends on Tasks 1-4 all being merged first.

**Files:**
- Modify: `src/ffdo/api/app.py`
- Test: `tests/api/test_app.py`

**Interfaces:**
- Consumes: `connect.resolve_mock` (Task 3), `mock_draft.build_league_profile`/`resolve_roster_id`/`backfill_roster_ids` (Task 2), `Session.is_mock` (Task 1).
- Produces: `POST /api/connect` accepts `{draft_id, username}` as an alternative to `{league_id, username}`; `GET /api/board`'s response gains a top-level `"is_mock"` key; `get_board()` correctly serves mock-draft boards. Terminal for the backend — Task 6 (frontend) consumes the `is_mock` key and the dual-payload `/api/connect` contract.

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_app.py`. First, extend the top-level `_session()` helper's `base` dict to include `is_mock=False,` (right after `rounds=13,`):

```python
        draft_status="pre_draft", rounds=13, is_mock=False,
```

Add `import re` is NOT needed in the test file. Add these tests after `test_connect_endpoint_rejects_a_blank_league_id_or_username`:

```python
def test_connect_endpoint_rejects_both_league_id_and_draft_id_together():
    client = TestClient(create_app())

    res = client.post("/api/connect", json={
        "league_id": "L1", "draft_id": "D1", "username": "tester"})

    assert res.status_code == 400


def test_connect_endpoint_rejects_neither_league_id_nor_draft_id():
    client = TestClient(create_app())

    res = client.post("/api/connect", json={"username": "tester"})

    assert res.status_code == 400


def test_connect_endpoint_routes_a_draft_id_payload_to_resolve_mock(monkeypatch):
    fake_mock_session = _session(
        league_id="", draft_id="1397145756879605760", is_mock=True)
    captured = {}

    def fake_resolve_mock(sleeper, draft_id, username):
        captured["draft_id"] = draft_id
        return fake_mock_session

    monkeypatch.setattr("ffdo.ingest.connect.resolve_mock", fake_resolve_mock)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/connect", json={
        "draft_id": "https://sleeper.app/draft/nfl/1397145756879605760",
        "username": "schroedes"})

    assert res.status_code == 200
    assert res.json()["is_mock"] is True
    # The share URL's trailing numeric ID must reach resolve_mock() bare,
    # not the whole pasted URL.
    assert captured["draft_id"] == "1397145756879605760"


def test_connect_endpoint_accepts_a_bare_draft_id_without_a_url(monkeypatch):
    captured = {}

    def fake_resolve_mock(sleeper, draft_id, username):
        captured["draft_id"] = draft_id
        return _session(league_id="", is_mock=True)

    monkeypatch.setattr("ffdo.ingest.connect.resolve_mock", fake_resolve_mock)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    client.post("/api/connect", json={"draft_id": "1397145756879605760",
                                      "username": "schroedes"})

    assert captured["draft_id"] == "1397145756879605760"


def test_league_id_is_empty_string_for_a_connected_mock_session(monkeypatch, tmp_path):
    """get_board()'s mock-vs-real branch is driven entirely by whether
    _league_id() returns a falsy value -- this is the one fact that whole
    dispatch depends on, so it gets its own direct test."""
    store = SessionStore(tmp_path / "session.json")
    store.save(_session(league_id="", is_mock=True))
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    assert _league_id() == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_app.py -v`
Expected: FAIL — `TypeError: Session.__init__() missing 1 required positional argument: 'is_mock'` on the helper, and `AttributeError`/`400 != 200` on the new draft_id-routing tests since `/api/connect` doesn't accept `draft_id` yet.

- [ ] **Step 3: Implement**

In `src/ffdo/api/app.py`:

Add `import re` to the top-level imports:
```python
from __future__ import annotations

import os
import re
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable
```

Add a small module-level helper right after the `_TTLCache` class (before `def create_app()`):
```python
_TRAILING_DRAFT_ID_RE = re.compile(r"(\d+)/?$")


def _extract_draft_id(value: str) -> str:
    """Accepts either a bare draft ID or a pasted share URL like
    https://sleeper.app/draft/nfl/1397145756879605760 -- the trailing digit
    run is the ID either way."""
    match = _TRAILING_DRAFT_ID_RE.search(value)
    return match.group(1) if match else value
```

Add the `mock_draft` import to `create_app()`'s lazy-import block:
```python
    from ffdo.api import board as board_mod
    from ffdo.engine import auction, scoring, vor
    from ffdo.ingest import client as client_mod
    from ffdo.ingest import connect as connect_mod
    from ffdo.ingest import draft as draft_mod
    from ffdo.ingest import league as league_mod
    from ffdo.ingest import mock_draft as mock_draft_mod
    from ffdo.ingest import players as players_mod
    from ffdo.ingest import projections as proj_mod
```

Replace the `/api/connect` handler:
```python
    @app.post("/api/connect")
    def connect_league(payload: dict, background_tasks: BackgroundTasks) -> dict:
        league_id = str(payload.get("league_id", "")).strip()
        draft_id_input = str(payload.get("draft_id", "")).strip()
        username = str(payload.get("username", "")).strip()

        if bool(league_id) == bool(draft_id_input):
            raise HTTPException(
                status_code=400,
                detail="Provide exactly one of league_id or draft_id")
        if not username:
            raise HTTPException(status_code=400, detail="Username is required")

        sleeper = client_mod.SleeperClient()
        try:
            if league_id:
                session = connect_mod.resolve(sleeper, league_id, username)
            else:
                session = connect_mod.resolve_mock(
                    sleeper, _extract_draft_id(draft_id_input), username)
        except connect_mod.ConnectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            sleeper.close()

        _SESSION_STORE.save(session)
        background_tasks.add_task(_warm_caches, session.season)
        return asdict(session)
```

Replace `get_board()`:
```python
    @app.get("/api/board")
    def get_board() -> dict:
        league_id = _league_id()
        draft_id = _draft_id()
        is_mock = not league_id
        sleeper = client_mod.SleeperClient()
        try:
            if is_mock:
                draft_meta = sleeper.get_json(f"{client_mod.V1}/draft/{draft_id}")
                lg = mock_draft_mod.build_league_profile(draft_meta)
                picks_raw = mock_draft_mod.backfill_roster_ids(
                    sleeper.get_json(f"{client_mod.V1}/draft/{draft_id}/picks"),
                    draft_meta)
            else:
                lg = league_mod.parse(
                    sleeper.get_json(f"{client_mod.V1}/league/{league_id}"))
                draft_meta = sleeper.get_json(f"{client_mod.V1}/draft/{draft_id}")
                picks_raw = sleeper.get_json(f"{client_mod.V1}/draft/{draft_id}/picks")

            profiles = players_cache.get(lambda: _load_players(sleeper))
            proj, adp_data = _projections_cache_for(lg.season).get(
                lambda: _load_projections(sleeper, lg.season))
            state = draft_mod.parse(draft_meta, picks_raw)
        finally:
            sleeper.close()

        # Sleeper's /league/<id> settings carry no auction budget field for
        # this league -- the budget lives on the draft object instead (see
        # ffdo.ingest.draft.parse). Fall back to the draft's budget so the
        # engine's league.num_teams * league.budget math never hits a
        # league.budget of None.
        if lg.budget is None:
            lg = replace(lg, budget=state.budget)

        # Sleeper's projections endpoint does not actually honor the
        # position[] query filter server-side (confirmed against the live
        # API) -- it returns every position it has projections for,
        # including FB/CB/K/DEF, none of which this league rosters.
        # `vor.compute` now structurally excludes any position without a
        # replacement level derived from `league.roster_positions` (see
        # ffdo.engine.vor), so no position allowlist is needed here; scoring
        # a few extra positions that get excluded downstream is cheap.
        points = {pid: scoring.score_stats(p.stats, lg.scoring_settings)
                  for pid, p in proj.items() if pid in profiles}
        points = _active_only(points, profiles)
        valued = vor.assign_tiers(vor.compute(points, profiles, lg))

        if is_mock:
            # draft_order (and therefore roster_id) can only appear AFTER
            # connecting, so it must be re-resolved live from the same
            # draft_meta fetched above every poll -- never trusted from the
            # persisted session's static roster_id.
            session = _SESSION_STORE.get()
            roster_id = (mock_draft_mod.resolve_roster_id(draft_meta, session.user_id)
                        if session is not None else None)
        else:
            roster_id = _roster_id()

        if state.draft_type == "auction":
            baseline = auction.baseline_prices(valued, lg)
            board = board_mod.build_auction_board(
                lg, state, valued, baseline, roster_id=roster_id)
        else:
            from ffdo.engine import market
            available = {pid for pid in valued if pid not in state.drafted_player_ids()}
            adp_means = {pid: a.adp["half_ppr"] for pid, a in adp_data.items()
                        if a.adp.get("half_ppr", 999) < 999}
            picks_until = lg.num_teams  # conservative: one full round
            survival = market.simulate_survival(adp_means, available, picks_until)
            cow = market.cost_of_waiting(valued, survival, available)
            board = board_mod.build_snake_board(lg, state, valued, survival, cow)

        board["is_mock"] = is_mock
        return board
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_app.py -v`
Expected: PASS — all tests, including the new ones.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/api/app.py tests/api/test_app.py
git commit -m "feat: dual-mode /api/connect and a mock-draft branch in get_board()"
```

---

## Task 6: Frontend — League/Mock Draft toggle and MOCK badges

No automated tests (no frontend test framework exists in this repo, consistent with every prior frontend task). Verified manually per Step 4.

**Files:**
- Modify: `src/ffdo/web/index.html`
- Modify: `src/ffdo/web/main.js`
- Modify: `src/ffdo/web/main.css`
- Modify: `src/ffdo/web/board/index.html`
- Modify: `src/ffdo/web/board/board.js`
- Modify: `src/ffdo/web/board/board.css`

**Interfaces:**
- Consumes: `POST /api/connect` accepting `{draft_id, username}` and `GET /api/board`'s `"is_mock"` key (Task 5).
- Produces: the main screen's mode toggle and both pages' MOCK badges. Terminal — nothing downstream consumes this task.

- [ ] **Step 1: Update `src/ffdo/web/index.html`**

Replace the `#connect-form` section:
```html
  <section id="connect-form" class="card">
    <div class="format-toggle" id="connect-mode-toggle">
      <button data-mode="league" class="on">League</button>
      <button data-mode="mock">Mock Draft</button>
    </div>
    <div id="league-fields">
      <h1>Connect your league</h1>
      <label class="field">
        <span>Sleeper League ID</span>
        <input id="league-id-input" type="text" placeholder="e.g. 1315881559957458944" autocomplete="off">
      </label>
    </div>
    <div id="mock-fields" hidden>
      <h1>Connect a mock draft</h1>
      <label class="field">
        <span>Draft link or ID</span>
        <input id="draft-id-input" type="text" placeholder="e.g. https://sleeper.app/draft/nfl/1397145756879605760" autocomplete="off">
      </label>
    </div>
    <label class="field">
      <span>Sleeper Username</span>
      <input id="username-input" type="text" placeholder="e.g. yourusername" autocomplete="off">
    </label>
    <p id="connect-error" class="error-msg" hidden></p>
    <button id="connect-btn">Connect</button>
  </section>
```

Replace the `.card-head` block inside `.league-card` (add a badge row):
```html
        <div class="card-head">
          <div class="league-identity">
            <span class="eyebrow">League</span>
            <h2 id="league-name">&mdash;</h2>
            <span class="league-id-tag" id="league-id-tag"></span>
          </div>
          <div class="badge-row">
            <span id="mock-badge" class="mock-badge" hidden>MOCK DRAFT</span>
            <span id="status-badge" class="status-badge"></span>
          </div>
        </div>
```

- [ ] **Step 2: Update `src/ffdo/web/main.css`**

Add after the `.error-msg` rule:
```css
.badge-row { display: flex; align-items: center; gap: 8px; }
.mock-badge {
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 1px;
  color: var(--red);
  background: color-mix(in oklch, var(--red) 14%, transparent);
  border: 1px solid color-mix(in oklch, var(--red) 40%, transparent);
  padding: 6px 10px;
  border-radius: 6px;
  white-space: nowrap;
}
```

Add after `#connect-form h1 { margin: 0; font-size: 20px; }`:
```css
#connect-mode-toggle { align-self: flex-start; margin-bottom: 4px; }
```

- [ ] **Step 3: Update `src/ffdo/web/main.js`**

Add `connectMode: "league",` to the `state` object:
```js
let state = {
  session: null,
  format: null,
  connecting: false,
  readinessTimer: null,
  connectMode: "league",
};
```

Add the mode-toggle wiring near the bottom, right before `document.getElementById("connect-btn").addEventListener("click", connect);`:
```js
document.querySelectorAll("#connect-mode-toggle button").forEach(btn =>
  btn.addEventListener("click", () => {
    state.connectMode = btn.dataset.mode;
    document.querySelectorAll("#connect-mode-toggle button").forEach(b =>
      b.classList.toggle("on", b.dataset.mode === state.connectMode));
    document.getElementById("league-fields").hidden = state.connectMode !== "league";
    document.getElementById("mock-fields").hidden = state.connectMode !== "mock";
  }));
```

In `renderConnected()`, add this line right after the `stat-format` line:
```js
  document.getElementById("mock-badge").hidden = !s.is_mock;
```

Replace `connect()` in full:
```js
async function connect() {
  const username = document.getElementById("username-input").value.trim();
  const errorEl = document.getElementById("connect-error");
  errorEl.hidden = true;

  const payload = { username };
  if (state.connectMode === "mock") {
    const draftId = document.getElementById("draft-id-input").value.trim();
    if (!draftId || !username) {
      errorEl.textContent = "Draft link/ID and username are both required.";
      errorEl.hidden = false;
      return;
    }
    payload.draft_id = draftId;
  } else {
    const leagueId = document.getElementById("league-id-input").value.trim();
    if (!leagueId || !username) {
      errorEl.textContent = "League ID and username are both required.";
      errorEl.hidden = false;
      return;
    }
    payload.league_id = leagueId;
  }
  if (state.connecting) return;

  state.connecting = true;
  const btn = document.getElementById("connect-btn");
  btn.disabled = true;
  btn.textContent = "Connecting…";

  try {
    const res = await fetch("/api/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      errorEl.textContent = body.detail || "Could not connect.";
      errorEl.hidden = false;
      return;
    }
    state.session = body;
    state.format = body.draft_type;
    showConnected();
    pollReadiness();
  } catch (err) {
    errorEl.textContent = "Network error — check your connection and try again.";
    errorEl.hidden = false;
    console.error("connect failed", err);
  } finally {
    state.connecting = false;
    btn.disabled = false;
    btn.textContent = "Connect";
  }
}
```

- [ ] **Step 4: Update `src/ffdo/web/board/index.html`**

In the `.brand` block, add a MOCK badge after `#brand-tag`:
```html
  <div class="brand">
    <span class="brand-name">FFDO</span>
    <span id="brand-tag" class="brand-tag">/ AUCTION</span>
    <span id="mock-badge" class="mock-badge" hidden>MOCK</span>
  </div>
```

- [ ] **Step 5: Update `src/ffdo/web/board/board.css`**

Add near the `.brand-tag` rule:
```css
.mock-badge {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 1px;
  color: var(--red);
  background: color-mix(in oklch, var(--red) 16%, transparent);
  border: 1px solid color-mix(in oklch, var(--red) 40%, transparent);
  padding: 3px 8px;
  border-radius: 5px;
}
```

- [ ] **Step 6: Update `src/ffdo/web/board/board.js`**

In `render()`, add this line right after the `brand-tag` line:
```js
  document.getElementById("mock-badge").hidden = !d.is_mock;
```

- [ ] **Step 7: Manual verification in-browser against the real mock draft**

Run: `uv run uvicorn ffdo.api.app:app --port 8000`

Using the `run` skill (or a browser directly):
1. Open `http://localhost:8000/` — confirm the League/Mock Draft toggle renders, defaulting to League.
2. Click "Mock Draft" — confirm the form swaps to the draft-link field.
3. Paste `https://sleeper.app/draft/nfl/1397145756879605760` and a valid Sleeper username, click Connect.
4. Confirm the connected view shows the real MOCK DRAFT badge, the real roster shape (1 QB / 2 RB / 2 WR / 1 TE / 2 FLEX / 1 DEF / 1 K / 5 BN), and "N scoring keys synced" reflecting whatever the draft's current `scoring_type` resolves to.
5. Click "Enter draft room" — confirm `/board` loads, shows the MOCK badge in the header, and (if you're connected as the user who owns a drafted slot) your $ left / slots filled reflect your actual mock-draft picks, not a fresh/zero roster.
6. Make another pick in the real mock draft (in another tab, on sleeper.app), wait for the next 3s poll, and confirm the board updates — this is the feature validating itself against the live data it was built around.

- [ ] **Step 8: Commit**

```bash
git add src/ffdo/web/index.html src/ffdo/web/main.js src/ffdo/web/main.css src/ffdo/web/board/index.html src/ffdo/web/board/board.js src/ffdo/web/board/board.css
git commit -m "feat: add League/Mock Draft toggle and MOCK badges to the frontend"
```
