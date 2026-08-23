# ESPN League Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ESPN as a second supported league provider (snake draft only), reusing the existing provider-agnostic engine/domain/board code untouched — all new work is in `ingest/`, `api/app.py`'s dispatch, `domain/models.py`'s `Session`, and the connect-form frontend.

**Architecture:** A new `ingest/espn/` subpackage mirrors the existing Sleeper adapters one-for-one (client, connect, league, draft, teams), each translating ESPN's wire format into the same provider-neutral dataclasses (`LeagueProfile`, `DraftState`, `TeamProfile`) a Sleeper league already produces. Every ESPN player ID is translated into Sleeper's ID space at the ingest boundary via a crosswalk, since Sleeper's player pool/stats/projections/ADP remain the valuation source regardless of provider. `api/app.py` branches once on `Session.provider` to pick which ingest path to call.

**Tech Stack:** Python 3.12 (uv-managed), FastAPI, httpx. No new dependencies. Frontend is the existing static `web/index.html` + `web/main.js` + `web/main.css`, no build step.

**Spec:** [docs/superpowers/specs/2026-08-23-espn-league-support-design.md](../specs/2026-08-23-espn-league-support-design.md) — read this in full before starting; it documents which values below were verified against a real, live ESPN league and which are intentionally incomplete (and why).

## Global Constraints

- Python 3.12, dependencies managed by `uv` — do not add new dependencies for this feature.
- `engine/`, `domain/models.py`, and `api/board.py` must NOT be modified by this plan — every dataclass ESPN adapters produce must already exist and be provider-neutral. If a task seems to need an engine change, stop and reconsider the adapter instead.
- Nothing above `ingest/espn/` may ever see a raw ESPN JSON key (`espn_s2`, `SWID`, `playerId`, `lineupSlotId`, `statId`, `proTeamId`, `teamId`, member/team identity fields) — adapters translate at the boundary, same rule the original design applies to Sleeper.
- ESPN player IDs must never reach a domain dataclass untranslated — every adapter that emits a `player_id` (draft picks, team rosters) must run it through the crosswalk first, dropping (not guessing) anything unmatched.
- MVP is snake-draft only. `ingest/espn/connect.py::resolve()` must reject a non-snake ESPN league with a `ConnectError`, not silently mishandle it.
- ESPN's real API host is `lm-api-reads.fantasy.espn.com` (NOT `fantasy.espn.com`, which redirects) and every request needs both the `espn_s2`/`SWID` cookie header AND a realistic browser `User-Agent` header — verified live, both are required.
- Real, sanitized ESPN API fixtures are already committed at `data/snapshots/2026-08-23-espn-league/` (`mSettings.json.gz`, `mTeam.json.gz`, `mDraftDetail.json.gz`, `espnPlayersDst.json.gz`, `espnPlayersSample.json.gz`) — use `ffdo.ingest.snapshot.load(name, snapshot_dir=...)` with `snapshot_dir` pointed at that directory (see Task 6 for the exact pattern). Do not re-fetch live data for tests.

---

### Task 1: `Session` gains provider identity (domain model + `SessionStore`)

**Files:**
- Modify: `src/ffdo/domain/models.py`
- Modify: `src/ffdo/api/session.py`
- Test: `tests/domain/test_models.py`
- Test: `tests/api/test_session.py`

**Interfaces:**
- Produces: `Session.provider: str = "sleeper"`, `Session.espn_s2: str | None = None`, `Session.swid: str | None = None` — consumed by `api/app.py` (Task 10) to pick a provider, and by `ingest/espn/connect.py` (Task 9) to populate an ESPN session.

- [ ] **Step 1: Write the failing tests**

Add to `tests/domain/test_models.py`, after `test_session_is_frozen_and_holds_the_connected_leagues_identity`:

```python
def test_session_defaults_provider_to_sleeper_with_no_espn_credentials():
    session = Session(
        username="tester", user_id="U1", league_id="L1", draft_id="D1",
        roster_id=3, league_name="Test League", season=2026, num_teams=12,
        budget=200,
        roster_positions=("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                          "BN", "BN", "BN", "BN", "BN"),
        scoring_settings={"rec": 0.5}, draft_type="auction",
        draft_status="pre_draft", rounds=13,
        connected_at="2026-08-22T00:00:00+00:00",
    )
    assert session.provider == "sleeper"
    assert session.espn_s2 is None
    assert session.swid is None


def test_session_accepts_espn_provider_and_cookies():
    session = Session(
        username="", user_id="{00000004-0000-0000-0000-000000000000}",
        league_id="1882997948", draft_id="1882997948", roster_id=7,
        league_name="Pigskin Pricing Experts", season=2026, num_teams=10,
        budget=200,
        roster_positions=("QB", "RB", "RB", "WR", "WR", "TE", "FLEX",
                          "DEF", "K", "BN", "BN", "BN", "BN", "BN", "BN", "IR"),
        scoring_settings={"pass_yd": 0.04}, draft_type="snake",
        draft_status="pre_draft", rounds=15,
        connected_at="2026-08-23T00:00:00+00:00",
        provider="espn", espn_s2="s2-value",
        swid="{00000004-0000-0000-0000-000000000000}",
    )
    assert session.provider == "espn"
    assert session.espn_s2 == "s2-value"
    assert session.swid == "{00000004-0000-0000-0000-000000000000}"
```

Add to `tests/api/test_session.py` (add `import json` at the top alongside the existing imports):

```python
def test_save_then_get_round_trips_espn_provider_and_cookies(tmp_path):
    store = SessionStore(tmp_path / "session.json")
    session = _session(provider="espn", espn_s2="s2-value",
                       swid="{00000004-0000-0000-0000-000000000000}")
    store.save(session)
    loaded = store.get()
    assert loaded.provider == "espn"
    assert loaded.espn_s2 == "s2-value"
    assert loaded.swid == "{00000004-0000-0000-0000-000000000000}"


def test_a_session_file_written_before_this_feature_loads_as_sleeper(tmp_path):
    """A pre-existing data/session.json has no provider/espn_s2/swid keys at
    all -- must load as a Sleeper session, not crash or silently misreport
    the provider."""
    path = tmp_path / "session.json"
    old_style = {
        "username": "tester", "user_id": "U1", "league_id": "L1", "draft_id": "D1",
        "roster_id": 3, "league_name": "Test League", "season": 2026, "num_teams": 12,
        "budget": 200,
        "roster_positions": ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                             "BN", "BN", "BN", "BN", "BN"],
        "scoring_settings": {"rec": 0.5}, "draft_type": "auction",
        "draft_status": "pre_draft", "rounds": 13,
        "connected_at": "2026-08-22T00:00:00+00:00",
    }
    path.write_text(json.dumps(old_style), encoding="utf-8")

    loaded = SessionStore(path).get()
    assert loaded.provider == "sleeper"
    assert loaded.espn_s2 is None
    assert loaded.swid is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/domain/test_models.py tests/api/test_session.py -v`
Expected: the two new `test_domain` tests FAIL with `TypeError: Session.__init__() got an unexpected keyword argument 'provider'`; the two new `test_session` tests FAIL similarly or with an `AttributeError`.

- [ ] **Step 3: Implement — `domain/models.py`**

In `src/ffdo/domain/models.py`, replace:

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
```

with:

```python
@dataclass(frozen=True, slots=True)
class Session:
    """A connected league/user/draft, as resolved by one of
    `ffdo.ingest.connect.resolve()` (Sleeper) or
    `ffdo.ingest.espn.connect.resolve()` (ESPN), and persisted by
    `ffdo.api.session.SessionStore`.
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
    provider: str = "sleeper"
    espn_s2: str | None = None
    swid: str | None = None
```

- [ ] **Step 4: Implement — `api/session.py`**

In `src/ffdo/api/session.py`, in `load()`, replace:

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
            )
```

with:

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
                provider=raw.get("provider", "sleeper"),
                espn_s2=raw.get("espn_s2"),
                swid=raw.get("swid"),
            )
```

In `save()`, replace:

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
        }
```

with:

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
            "provider": session.provider,
            "espn_s2": session.espn_s2,
            "swid": session.swid,
        }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/domain/test_models.py tests/api/test_session.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/domain/models.py src/ffdo/api/session.py tests/domain/test_models.py tests/api/test_session.py
git commit -m "feat: add provider identity and ESPN credentials to Session"
```

---

### Task 2: Extract shared HTTP retry logic (`ingest/http.py`)

Behavior-preserving refactor: `SleeperClient`'s retry/backoff loop moves into a shared, provider-agnostic helper that `EspnClient` (Task 4) will also use. `SleeperClient`'s existing tests must pass unchanged.

**Files:**
- Create: `src/ffdo/ingest/http.py`
- Modify: `src/ffdo/ingest/client.py`
- Test: `tests/ingest/test_http.py`

**Interfaces:**
- Produces: `get_json_with_retry(client: httpx.Client, url: str, *, headers: dict[str,str] | None = None, base_delay: float = 0.0, max_attempts: int = 4, sleep: Callable[[float], None]) -> Any`, consumed by `SleeperClient.get_json` (this task) and `EspnClient.get_json` (Task 4).
- `SleeperClient.get_json`/`SleeperClient.close` keep their exact existing signatures and behavior.

- [ ] **Step 1: Run the existing Sleeper client tests to confirm the baseline is green**

Run: `uv run pytest tests/ingest/test_client.py -v`
Expected: both existing tests PASS (this is the baseline the refactor must not break).

- [ ] **Step 2: Write the failing tests for the new shared helper**

Create `tests/ingest/test_http.py`:

```python
import httpx
import pytest

from ffdo.ingest.http import get_json_with_retry


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_returns_parsed_json_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    result = get_json_with_retry(_client(handler), "https://example.test/x",
                                 sleep=lambda *_a, **_kw: None)
    assert result == {"ok": True}


def test_passes_headers_through_to_the_request():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    get_json_with_retry(_client(handler), "https://example.test/x",
                        headers={"Cookie": "a=b"}, sleep=lambda *_a, **_kw: None)
    assert seen[0].headers["cookie"] == "a=b"


def test_retryable_status_then_success_makes_multiple_requests():
    responses = [httpx.Response(429), httpx.Response(503),
                httpx.Response(200, json={"ok": True})]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses[len(calls) - 1]

    result = get_json_with_retry(_client(handler), "https://example.test/x",
                                 sleep=lambda *_a, **_kw: None)
    assert result == {"ok": True}
    assert len(calls) == 3


def test_non_retryable_status_raises_immediately_after_one_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(httpx.HTTPStatusError):
        get_json_with_retry(_client(handler), "https://example.test/x",
                            sleep=lambda *_a, **_kw: None)
    assert len(calls) == 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/test_http.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.ingest.http'`

- [ ] **Step 4: Implement `ingest/http.py`**

```python
"""Generic GET-with-retry loop shared by every provider's HTTP client.

Extracted from ffdo.ingest.client.SleeperClient once ffdo.ingest.espn.client
needed the identical retry/backoff behavior -- same discipline as the
rank_by_position/greedy_fill_slots extraction in engine/replacement.py: one
algorithm, two callers, verified behavior-preserving via SleeperClient's
existing tests passing unchanged before and after.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx


def get_json_with_retry(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    base_delay: float = 0.0,
    max_attempts: int = 4,
    sleep: Callable[[float], None],
) -> Any:
    last: Exception | None = None
    for attempt in range(max_attempts):
        try:
            resp = client.get(url, headers=headers)
        except httpx.TransportError as exc:
            last = exc
            if attempt == max_attempts - 1:
                break
            sleep(base_delay + 2**attempt)
            continue

        if resp.status_code == 429 or resp.status_code >= 500:
            last = httpx.HTTPStatusError(
                f"retryable {resp.status_code}",
                request=resp.request, response=resp,
            )
            if attempt == max_attempts - 1:
                break
            sleep(base_delay + 2**attempt)
            continue

        # Any other error status (404, 400, 401, 403, ...) is permanent:
        # fail fast rather than burning retry attempts and backoff time.
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"GET {url} failed after {max_attempts} attempts") from last
```

- [ ] **Step 5: Refactor `ingest/client.py` to use it**

Replace the full contents of `src/ffdo/ingest/client.py`:

```python
"""The only Sleeper-specific HTTP client. Everything else reads cache or snapshot."""

from __future__ import annotations

import time
from typing import Any

import httpx

from ffdo.ingest.http import get_json_with_retry

V1 = "https://api.sleeper.app/v1"
PROJECTIONS = "https://api.sleeper.app/projections/nfl"


class SleeperClient:
    """Sleeper asks callers to stay under 1000 requests/minute."""

    def __init__(
        self,
        base_delay: float = 0.0,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_delay = base_delay
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def get_json(self, url: str, max_attempts: int = 4) -> Any:
        return get_json_with_retry(
            self._client, url, base_delay=self._base_delay,
            max_attempts=max_attempts, sleep=time.sleep)

    def close(self) -> None:
        self._client.close()
```

Note: `client.py` keeps `import time` even though the retry loop itself moved to `http.py` — this is deliberate, not leftover. `tests/ingest/test_client.py`'s existing `monkeypatch.setattr("ffdo.ingest.client.time.sleep", ...)` resolves `ffdo.ingest.client.time` as the (shared, singleton) `time` module object and patches its `.sleep` attribute globally; that only works if `client.py` still imports `time` itself. Do not remove this import as part of "cleaning up unused imports."

- [ ] **Step 6: Run both test files to verify nothing broke**

Run: `uv run pytest tests/ingest/test_client.py tests/ingest/test_http.py -v`
Expected: all PASS — `test_client.py`'s two tests identical to Step 1's baseline, plus `test_http.py`'s four new tests.

- [ ] **Step 7: Run the full suite to confirm no wider regression**

Run: `uv run pytest -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/ffdo/ingest/http.py src/ffdo/ingest/client.py tests/ingest/test_http.py
git commit -m "refactor: extract shared HTTP retry logic into ingest/http.py"
```

---

### Task 3: `ingest/players.py` gains `espn_id_index()`

**Files:**
- Modify: `src/ffdo/ingest/players.py`
- Test: `tests/ingest/test_adapters.py`

**Interfaces:**
- Produces: `espn_id_index(raw: dict[str, Any]) -> dict[str, str]` (Sleeper `player_id` -> `espn_id`), consumed by `ingest/espn/crosswalk.py` (Task 5) via `api/app.py` (Task 10).

- [ ] **Step 1: Write the failing tests**

Add to `tests/ingest/test_adapters.py` (the file already imports `players`):

```python
def test_espn_id_index_maps_sleeper_id_to_espn_id_when_present():
    raw = {"1": {"position": "RB", "espn_id": 3117251},
          "2": {"position": "WR", "espn_id": None},
          "3": {"position": "TE"}}
    idx = players.espn_id_index(raw)
    assert idx == {"1": "3117251"}


def test_espn_id_index_skips_non_dict_records():
    raw = {"1": {"position": "RB", "espn_id": 42}, "shard_count": 4}
    idx = players.espn_id_index(raw)
    assert idx == {"1": "42"}


def test_espn_id_index_against_the_real_snapshot():
    """Verified 2026-08-23 (see the ESPN design doc, §4.1): Jahmyr Gibbs
    ("9221" in this snapshot, confirmed by test_players_parse_extracts_profile_fields
    above) has no espn_id despite being an obviously-active, highly relevant
    fantasy asset -- a real, documented coverage gap the crosswalk's
    fallback exists for, not a bug in this function."""
    idx = players.espn_id_index(snapshot.load("players_nfl"))
    assert "9221" not in idx
    assert len(idx) > 1000  # ~6,736 players verified to have espn_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/test_adapters.py -v -k espn_id_index`
Expected: FAIL with `AttributeError: module 'ffdo.ingest.players' has no attribute 'espn_id_index'`

- [ ] **Step 3: Implement**

In `src/ffdo/ingest/players.py`, add at the end of the file:

```python
def espn_id_index(raw: dict[str, Any]) -> dict[str, str]:
    """player_id (Sleeper) -> espn_id, for every record that has one.

    Coverage is real but incomplete -- verified 2026-08-23 against a real
    snapshot: ~46% of active skill-position players, skewed toward
    established veterans over recent draftees. See
    docs/superpowers/specs/2026-08-23-espn-league-support-design.md §4.1.
    """
    out: dict[str, str] = {}
    for player_id, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        espn_id = rec.get("espn_id")
        if espn_id:
            out[player_id] = str(espn_id)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/test_adapters.py -v`
Expected: all PASS, including the three new tests.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/players.py tests/ingest/test_adapters.py
git commit -m "feat: extract espn_id from Sleeper's player feed for the ESPN crosswalk"
```

---

### Task 4: `ingest/espn/client.py` — cookie-authenticated HTTP client

**Files:**
- Create: `src/ffdo/ingest/espn/__init__.py` (empty)
- Create: `src/ffdo/ingest/espn/client.py`
- Create: `tests/ingest/espn/__init__.py` (empty)
- Test: `tests/ingest/espn/test_client.py`

**Interfaces:**
- Consumes: `ffdo.ingest.http.get_json_with_retry` (Task 2).
- Produces: `EspnClient(espn_s2: str, swid: str, base_delay: float = 0.0, timeout: float = 30.0, transport: httpx.BaseTransport | None = None)` with `.get_json(url, max_attempts=4)` and `.close()`, consumed by `ingest/espn/connect.py` (Task 9).
- `BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"` — the confirmed-working host, consumed by `ingest/espn/connect.py` (Task 9) to build request URLs.

- [ ] **Step 1: Write the failing tests**

Create `src/ffdo/ingest/espn/__init__.py` (empty file) and `tests/ingest/espn/__init__.py` (empty file).

Create `tests/ingest/espn/test_client.py`:

```python
import httpx
import pytest

from ffdo.ingest.espn.client import EspnClient


def test_sends_cookie_and_user_agent_headers():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = EspnClient("s2value", "{00000001-0000-0000-0000-000000000000}",
                        base_delay=0, transport=httpx.MockTransport(handler))
    result = client.get_json("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/x")

    assert result == {"ok": True}
    assert len(seen) == 1
    cookie = seen[0].headers["cookie"]
    assert "espn_s2=s2value" in cookie
    assert "SWID={00000001-0000-0000-0000-000000000000}" in cookie
    assert "Mozilla" in seen[0].headers["user-agent"]


def test_retryable_status_then_success_makes_multiple_requests(monkeypatch):
    monkeypatch.setattr("ffdo.ingest.espn.client.time.sleep", lambda *_a, **_kw: None)
    responses = [httpx.Response(503), httpx.Response(200, json={"ok": True})]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses[len(calls) - 1]

    client = EspnClient("s2", "{00000001-0000-0000-0000-000000000000}",
                        base_delay=0, transport=httpx.MockTransport(handler))
    result = client.get_json("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/x")
    assert result == {"ok": True}
    assert len(calls) == 2


def test_non_retryable_status_raises_immediately():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client = EspnClient("bad", "{00000000-0000-0000-0000-000000000000}",
                        base_delay=0, transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        client.get_json("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/x")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/espn/test_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.ingest.espn.client'`

- [ ] **Step 3: Implement**

Create `src/ffdo/ingest/espn/client.py`:

```python
"""ESPN's fantasy API client. Cookie-authenticated -- there is no OAuth or
public API key flow for a private league. Everything above ingest/espn/
must never see espn_s2/SWID or any raw ESPN JSON key; adapters translate
at this boundary, same rule ffdo.ingest.client applies to Sleeper.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ffdo.ingest.http import get_json_with_retry

# Verified live 2026-08-23 against a real league: "fantasy.espn.com" (the
# host used throughout most public writeups) 302-redirects reads to
# https://www.espn.com/fantasy/ -- it no longer serves this API directly.
# This host is the one that actually returns data.
BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"

# A default/bare HTTP client User-Agent gets the same redirect treatment
# even on the correct host -- confirmed live; CloudFront in front of ESPN's
# API appears to filter on it.
_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class EspnClient:
    def __init__(
        self,
        espn_s2: str,
        swid: str,
        base_delay: float = 0.0,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._headers = {
            "Cookie": f"espn_s2={espn_s2}; SWID={swid}",
            "User-Agent": _USER_AGENT,
        }
        self._base_delay = base_delay
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def get_json(self, url: str, max_attempts: int = 4) -> Any:
        return get_json_with_retry(
            self._client, url, headers=self._headers,
            base_delay=self._base_delay, max_attempts=max_attempts,
            sleep=time.sleep)

    def close(self) -> None:
        self._client.close()
```

Note: `EspnClient` does not normalize `swid` (accept-with-or-without-braces) itself — it trusts its caller to have already normalized it. That normalization lives in `ingest/espn/connect.py` (Task 9), the one-time entry point where a user's raw pasted value first arrives, so every later reconnection (a fresh `EspnClient` built from an already-persisted `Session.swid` on each board poll) already has a normalized value and doesn't need to re-normalize.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/espn/test_client.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/espn/__init__.py src/ffdo/ingest/espn/client.py tests/ingest/espn/__init__.py tests/ingest/espn/test_client.py
git commit -m "feat: add ESPN cookie-authenticated HTTP client"
```

---

### Task 5: `ingest/espn/crosswalk.py` — player-ID resolution

This is the most novel logic in the feature. Read the design doc's §4 (all subsections) before starting — it documents exactly why two independent lookups (`espn_id`, then normalized name+position) feed the player crosswalk, why team defenses need a completely separate static table, and why `espn_players`' `defaultPositionId` values use different numbers than the roster-slot IDs in Task 6.

**Files:**
- Create: `src/ffdo/ingest/espn/crosswalk.py`
- Test: `tests/ingest/espn/test_crosswalk.py`

**Interfaces:**
- Consumes: `ffdo.domain.models.PlayerProfile`.
- Produces: `Crosswalk(espn_to_sleeper: Mapping[str,str], unmatched: tuple[str,...])`, `normalize_name(name: str) -> str`, `parse_player_pool(raw: list[dict]) -> dict[str, tuple[str,str,int]]` (espn_id -> (full_name, position, pro_team_id)), `build(espn_id_index: Mapping[str,str], profiles: Mapping[str,PlayerProfile], espn_players: Mapping[str, tuple[str,str,int]]) -> Crosswalk` — all consumed by `ingest/espn/draft.py` (Task 7), `ingest/espn/teams.py` (Task 8), and `ingest/espn/connect.py` (Task 9).

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/espn/test_crosswalk.py`:

```python
from ffdo.domain.models import PlayerProfile
from ffdo.ingest import snapshot
from ffdo.ingest.espn import crosswalk

ESPN_SNAPSHOT_DIR = snapshot.DEFAULT_SNAPSHOT_DIR.parent / "2026-08-23-espn-league"


def _profile(pid, first, last, position):
    return PlayerProfile(player_id=pid, first_name=first, last_name=last,
                         position=position, team="X", age=25, years_exp=3,
                         injury_status=None, active=True)


def test_normalize_name_strips_punctuation_and_suffixes():
    assert crosswalk.normalize_name("Ja'Marr Chase") == "jamarr chase"
    assert crosswalk.normalize_name("Odell Beckham Jr.") == "odell beckham"
    assert crosswalk.normalize_name("Michael Pittman III") == "michael pittman"


def test_build_resolves_via_espn_id_when_present():
    profiles = {"9999": _profile("9999", "Josh", "Allen", "QB")}
    espn_id_index = {"9999": "3918298"}
    espn_players = {"3918298": ("Josh Allen", "QB", 2)}

    cw = crosswalk.build(espn_id_index, profiles, espn_players)
    assert cw.espn_to_sleeper == {"3918298": "9999"}
    assert cw.unmatched == ()


def test_build_falls_back_to_normalized_name_and_position_match():
    profiles = {"1234": _profile("1234", "Ja'Marr", "Chase", "WR")}
    espn_players = {"555": ("Jamarr Chase", "WR", 4)}  # no espn_id hit

    cw = crosswalk.build({}, profiles, espn_players)
    assert cw.espn_to_sleeper == {"555": "1234"}


def test_build_excludes_an_ambiguous_fallback_match_rather_than_guessing():
    profiles = {
        "1": _profile("1", "John", "Smith", "WR"),
        "2": _profile("2", "John", "Smith", "WR"),
    }
    espn_players = {"999": ("John Smith", "WR", 4)}

    cw = crosswalk.build({}, profiles, espn_players)
    assert cw.espn_to_sleeper == {}
    assert cw.unmatched == ("999",)


def test_build_logs_a_warning_for_every_unmatched_player(caplog):
    """Explicit and logged, never silent -- the returned `unmatched` tuple
    is structured data a caller *can* act on, but this is what actually
    makes a real gap visible in the server's own logs without a caller
    having to remember to check it."""
    import logging
    with caplog.at_level(logging.WARNING, logger="ffdo.ingest.espn.crosswalk"):
        crosswalk.build({}, {}, {"999": ("Nobody Real", "WR", 4)})
    assert "999" in caplog.text


def test_build_excludes_a_player_with_no_match_at_all():
    cw = crosswalk.build({}, {}, {"999": ("Nobody Real", "WR", 4)})
    assert cw.unmatched == ("999",)


def test_build_resolves_a_team_defense_via_the_pro_team_id_table_not_name_matching():
    profiles = {}  # no individual-player match could ever apply to a defense
    espn_players = {"-16034": ("Texans D/ST", "DEF", 34)}

    cw = crosswalk.build({}, profiles, espn_players)
    assert cw.espn_to_sleeper == {"-16034": "HOU"}


def test_build_excludes_a_defense_whose_pro_team_id_has_no_table_entry():
    cw = crosswalk.build({}, {}, {"-99999": ("Made Up D/ST", "DEF", 99)})
    assert cw.unmatched == ("-99999",)


def test_parse_player_pool_against_the_real_dst_fixture():
    raw = snapshot.load("espnPlayersDst", snapshot_dir=ESPN_SNAPSHOT_DIR)
    pool = crosswalk.parse_player_pool(raw)
    assert len(pool) == 32
    assert pool["-16034"] == ("Texans D/ST", "DEF", 34)


def test_parse_player_pool_skips_unmapped_positions_in_a_broad_real_sample():
    """The real, unfiltered sample includes many non-fantasy-relevant IDP
    positions (LB, DE, CB, DT, ...) this project doesn't roster or value --
    they must be silently skipped, not misidentified as something else."""
    raw = snapshot.load("espnPlayersSample", snapshot_dir=ESPN_SNAPSHOT_DIR)
    pool = crosswalk.parse_player_pool(raw)
    assert 0 < len(pool) < len(raw)
    for full_name, position, _pro_team_id in pool.values():
        assert position in ("QB", "RB", "WR", "TE", "K", "DEF")


def test_espn_pro_team_id_table_matches_every_real_dst_entry_to_a_sleeper_abbreviation():
    """Full round-trip against real data: every one of the 32 real ESPN
    defenses must resolve to a Sleeper team abbreviation."""
    raw = snapshot.load("espnPlayersDst", snapshot_dir=ESPN_SNAPSHOT_DIR)
    pool = crosswalk.parse_player_pool(raw)
    cw = crosswalk.build({}, {}, pool)
    assert len(cw.espn_to_sleeper) == 32
    assert cw.unmatched == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/espn/test_crosswalk.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.ingest.espn.crosswalk'`

- [ ] **Step 3: Implement**

Create `src/ffdo/ingest/espn/crosswalk.py`:

```python
"""ESPN player_id -> Sleeper player_id resolution.

Two independent lookups feed the individual-player crosswalk: `espn_id`
already present on many Sleeper player records (free, no extra network
call, but only ~46% coverage on active skill players -- verified
2026-08-23), and a normalized-name+position fallback for everyone that
misses. Team defenses need a third, completely different lookup (verified
live 2026-08-23) since Sleeper represents them by team abbreviation, not a
numbered player, and ESPN represents them by a stable pro-team ID with no
useful "name" to normalize-match against.

See docs/superpowers/specs/2026-08-23-espn-league-support-design.md §4 for
the full rationale, including why this module's two ID tables
(ESPN_PLAYER_POSITION_ID_TO_POSITION here vs. ESPN_SLOT_ID_TO_POSITION in
ingest/espn/league.py) must not be confused with each other -- they use
different numbering despite some coincidentally-shared values.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ffdo.domain.models import PlayerProfile

_logger = logging.getLogger(__name__)

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str) -> str:
    """Lowercase; strip periods/apostrophes and suffixes; collapse whitespace."""
    cleaned = re.sub(r"[.']", "", name.lower())
    words = [w for w in cleaned.split() if w not in _SUFFIXES]
    return " ".join(words)


@dataclass(frozen=True, slots=True)
class Crosswalk:
    espn_to_sleeper: Mapping[str, str]
    unmatched: tuple[str, ...]


# Verified live 2026-08-23 against ESPN's real player-pool response
# (/seasons/{season}/players?view=kona_player_info): each entry's
# `defaultPositionId` uses ESPN's classic per-player position numbering,
# confirmed against six real players by name (Nick Chubb=2/RB, DeAndre
# Hopkins=3/WR, Travis Vokolek=4/TE, Geno Smith=1/QB, Dustin Hopkins=5/K,
# Falcons D/ST=16/DEF). This is NOT the same table as
# ingest.espn.league.ESPN_SLOT_ID_TO_POSITION (roster lineup slots) --
# they diverge (WR is slot 4 but position 3; TE is slot 6 but position 4).
ESPN_PLAYER_POSITION_ID_TO_POSITION: dict[int, str] = {
    1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF",
}

# Verified live 2026-08-23: fetched all 32 real ESPN team defenses
# (proTeamId + name) and cross-referenced each against Sleeper's own DEF
# entries in players_nfl.json.gz (which use the team abbreviation itself as
# player_id). ESPN's pro-team IDs skip 31/32 and place the Ravens/Texans at
# 33/34 -- an artifact of when those franchises were assigned IDs, not a
# bug in this table.
ESPN_PRO_TEAM_ID_TO_SLEEPER_DEF_ID: dict[int, str] = {
    1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR",
    15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
    21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA",
    27: "TB", 28: "WAS", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}


def parse_player_pool(raw: list[dict[str, Any]]) -> dict[str, tuple[str, str, int]]:
    """`raw` is the flat list of player objects ESPN's player-pool endpoint
    returns (verified live 2026-08-23 -- each entry has `id`, `fullName`,
    `defaultPositionId`, `proTeamId` directly on it, no wrapper). Entries
    at a position this project doesn't map (IDP categories like LB/DE/CB,
    which a real unfiltered response includes) are silently skipped, same
    as engine.vor excludes a position with no replacement level.
    """
    out: dict[str, tuple[str, str, int]] = {}
    for entry in raw:
        espn_id = entry.get("id")
        position = ESPN_PLAYER_POSITION_ID_TO_POSITION.get(entry.get("defaultPositionId"))
        full_name = entry.get("fullName")
        pro_team_id = entry.get("proTeamId")
        if espn_id is not None and position is not None and full_name:
            out[str(espn_id)] = (full_name, position, pro_team_id)
    return out


def build(
    espn_id_index: Mapping[str, str],
    profiles: Mapping[str, PlayerProfile],
    espn_players: Mapping[str, tuple[str, str, int]],
) -> Crosswalk:
    sleeper_by_espn_id = {espn_id: sleeper_id
                          for sleeper_id, espn_id in espn_id_index.items()}

    by_name_position: dict[tuple[str, str], list[str]] = {}
    for sleeper_id, profile in profiles.items():
        key = (normalize_name(profile.full_name), profile.position)
        by_name_position.setdefault(key, []).append(sleeper_id)

    resolved: dict[str, str] = {}
    unmatched: list[str] = []
    for espn_id, (full_name, position, pro_team_id) in espn_players.items():
        if position == "DEF":
            sleeper_id = ESPN_PRO_TEAM_ID_TO_SLEEPER_DEF_ID.get(pro_team_id)
            if sleeper_id is not None:
                resolved[espn_id] = sleeper_id
            else:
                unmatched.append(espn_id)
                _logger.warning(
                    "ESPN crosswalk: no Sleeper DEF for pro_team_id=%s "
                    "(espn_id=%s, %r)", pro_team_id, espn_id, full_name)
            continue

        sleeper_id = sleeper_by_espn_id.get(espn_id)
        if sleeper_id is not None:
            resolved[espn_id] = sleeper_id
            continue

        candidates = by_name_position.get((normalize_name(full_name), position), [])
        if len(candidates) == 1:
            resolved[espn_id] = candidates[0]
        else:
            unmatched.append(espn_id)
            # Explicit and logged, never silent -- same discipline the
            # original design applies to its market-calibration fallback.
            # `unmatched` (returned below) is the structured form a caller
            # can act on; this warning is what makes a real gap visible in
            # the server's own logs at connect/poll time, not just
            # theoretically inspectable.
            _logger.warning(
                "ESPN crosswalk: %s match for espn_id=%s (%r, %s) -- %d "
                "candidate(s) in Sleeper's pool",
                "ambiguous" if candidates else "no", espn_id, full_name,
                position, len(candidates))

    return Crosswalk(espn_to_sleeper=resolved, unmatched=tuple(unmatched))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/espn/test_crosswalk.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/espn/crosswalk.py tests/ingest/espn/test_crosswalk.py
git commit -m "feat: add ESPN player-ID crosswalk (espn_id, name fallback, team defenses)"
```

---

### Task 6: `ingest/espn/league.py` — `LeagueProfile` parser

**Files:**
- Create: `src/ffdo/ingest/espn/league.py`
- Test: `tests/ingest/espn/test_league.py`

**Interfaces:**
- Produces: `parse(raw: dict) -> LeagueProfile`, `draft_type(raw: dict) -> str`, `ESPN_SLOT_ID_TO_POSITION`, `ESPN_STAT_ID_TO_SLEEPER_KEY` — `parse`/`draft_type` consumed by `ingest/espn/connect.py` (Task 9).

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/espn/test_league.py`:

```python
from ffdo.ingest import snapshot
from ffdo.ingest.espn import league

ESPN_SNAPSHOT_DIR = snapshot.DEFAULT_SNAPSHOT_DIR.parent / "2026-08-23-espn-league"


def _msettings():
    return snapshot.load("mSettings", snapshot_dir=ESPN_SNAPSHOT_DIR)


def test_parse_reads_identity_and_size_from_the_real_league():
    lg = league.parse(_msettings())
    assert lg.league_id == "1882997948"
    assert lg.season == 2026
    assert lg.num_teams == 10
    assert lg.name == "Pigskin Pricing Experts"


def test_parse_builds_roster_positions_matching_the_real_lineup_slot_counts():
    """Verified live 2026-08-23: {"0":1,"2":2,"4":2,"6":1,"16":1,"17":1,
    "20":6,"21":1,"23":1}, every other slot at 0."""
    lg = league.parse(_msettings())
    from collections import Counter
    counts = Counter(lg.roster_positions)
    assert counts == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "DEF": 1,
                      "K": 1, "BN": 6, "IR": 1, "FLEX": 1}
    assert lg.roster_size == 16


def test_parse_builds_scoring_settings_matching_the_real_scoring_items():
    lg = league.parse(_msettings())
    assert lg.scoring_settings == {
        "pass_yd": 0.04, "pass_td": 4.0, "pass_int": -2.0,
        "rush_yd": 0.1, "rush_td": 6.0,
        "rec_yd": 0.1, "rec_td": 6.0, "rec": 1.0,
        "fum_lost": -2.0,
    }


def test_parse_reads_auction_budget_even_for_a_snake_league():
    """ESPN always includes draftSettings.auctionBudget regardless of draft
    type; this league's real value is 200. Harmless to carry through --
    the snake board path never reads LeagueProfile.budget for pricing."""
    lg = league.parse(_msettings())
    assert lg.budget == 200


def test_draft_type_reads_snake_from_the_real_league():
    assert league.draft_type(_msettings()) == "snake"


def test_roster_positions_raises_on_an_unmapped_nonzero_slot():
    """A silently-dropped roster slot would under-count roster size and
    corrupt replacement-level math -- must fail loudly instead."""
    import pytest
    raw = _msettings()
    modified = {
        **raw,
        "settings": {
            **raw["settings"],
            "rosterSettings": {
                **raw["settings"]["rosterSettings"],
                "lineupSlotCounts": {
                    **raw["settings"]["rosterSettings"]["lineupSlotCounts"],
                    "7": 1,  # a real ESPN slot id (OP/superflex-like) this table doesn't map
                },
            },
        },
    }
    with pytest.raises(ValueError, match="lineup slot"):
        league.parse(modified)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/espn/test_league.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.ingest.espn.league'`

- [ ] **Step 3: Implement**

Create `src/ffdo/ingest/espn/league.py`:

```python
"""Translates ESPN's `mSettings` view into LeagueProfile."""

from __future__ import annotations

from typing import Any

from ffdo.domain.models import LeagueProfile

# Verified live 2026-08-23 against a real league's
# settings.rosterSettings.lineupSlotCounts. Note "DEF", not ESPN's own
# "D/ST" label -- this must match Sleeper's PlayerProfile.position
# vocabulary, since Sleeper's player pool is the valuation source
# regardless of provider (Sleeper's own DEF entries use "DEF").
ESPN_SLOT_ID_TO_POSITION: dict[int, str] = {
    0: "QB", 2: "RB", 4: "WR", 6: "TE", 23: "FLEX",
    16: "DEF", 17: "K", 20: "BN", 21: "IR",
}

# Verified live 2026-08-23 against a real league's
# settings.scoringSettings.scoringItems. Only the core offense categories
# are mapped so far; defense/kicking point-bracket categories
# (recognizable by a pointsOverrides dict keyed to "16") are documented,
# intentional follow-up work -- see the design doc §5 for why that's not a
# blocker to this feature.
ESPN_STAT_ID_TO_SLEEPER_KEY: dict[int, str] = {
    3: "pass_yd", 4: "pass_td", 20: "pass_int",
    24: "rush_yd", 25: "rush_td",
    42: "rec_yd", 43: "rec_td", 53: "rec",
    72: "fum_lost",
}


def _roster_positions(lineup_slot_counts: dict[str, int]) -> tuple[str, ...]:
    positions: list[str] = []
    for slot_id_str, count in lineup_slot_counts.items():
        if count <= 0:
            continue
        position = ESPN_SLOT_ID_TO_POSITION.get(int(slot_id_str))
        if position is None:
            raise ValueError(
                f"ESPN lineup slot id {slot_id_str} (count={count}) has no "
                "entry in ESPN_SLOT_ID_TO_POSITION -- an unsupported roster "
                "slot type, not something safe to silently drop")
        positions.extend([position] * count)
    return tuple(positions)


def _scoring_settings(scoring_items: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in scoring_items:
        key = ESPN_STAT_ID_TO_SLEEPER_KEY.get(item["statId"])
        if key is not None:
            out[key] = float(item["points"])
    return out


def draft_type(raw: dict[str, Any]) -> str:
    """'SNAKE' -> 'snake', matching DraftState.draft_type's vocabulary."""
    espn_type = (raw["settings"].get("draftSettings") or {}).get("type", "")
    return espn_type.lower()


def parse(raw: dict[str, Any]) -> LeagueProfile:
    settings = raw["settings"]
    draft_settings = settings.get("draftSettings") or {}
    return LeagueProfile(
        league_id=str(raw["id"]),
        season=int(raw["seasonId"]),
        num_teams=int(settings["size"]),
        roster_positions=_roster_positions(settings["rosterSettings"]["lineupSlotCounts"]),
        scoring_settings=_scoring_settings(settings["scoringSettings"]["scoringItems"]),
        budget=draft_settings.get("auctionBudget"),
        name=settings.get("name") or "",
        status="",  # ESPN's `status` is a nested object, not a simple
                    # string like Sleeper's -- no clean single-string
                    # equivalent exists, so this is left blank rather than
                    # coerced into something misleading.
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/espn/test_league.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/espn/league.py tests/ingest/espn/test_league.py
git commit -m "feat: parse ESPN league settings into LeagueProfile"
```

---

### Task 7: `ingest/espn/draft.py` — `DraftState` parser

**Files:**
- Create: `src/ffdo/ingest/espn/draft.py`
- Test: `tests/ingest/espn/test_draft.py`

**Interfaces:**
- Consumes: `ffdo.ingest.espn.crosswalk.Crosswalk` (Task 5).
- Produces: `parse(raw: dict, crosswalk: Crosswalk) -> DraftState`, consumed by `ingest/espn/connect.py` (Task 9).

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/espn/test_draft.py`:

```python
from ffdo.ingest import snapshot
from ffdo.ingest.espn import draft
from ffdo.ingest.espn.crosswalk import Crosswalk

ESPN_SNAPSHOT_DIR = snapshot.DEFAULT_SNAPSHOT_DIR.parent / "2026-08-23-espn-league"


def _mdraft_detail():
    return snapshot.load("mDraftDetail", snapshot_dir=ESPN_SNAPSHOT_DIR)


def test_a_pre_draft_league_produces_correct_metadata_and_zero_picks():
    """Verified live 2026-08-23: this real league's draft hasn't started --
    ESPN pre-populates the *entire* 150-slot picks array with playerId: -1
    placeholders. None of them are real picks."""
    state = draft.parse(_mdraft_detail(), Crosswalk(espn_to_sleeper={}, unmatched=()))
    assert state.picks == ()
    assert state.num_teams == 10
    assert state.rounds == 15
    assert state.draft_type == "snake"
    assert state.status == "pre_draft"
    assert state.draft_id == "1882997948"


def _synthetic_raw(picks):
    return {
        "id": 1882997948,
        "settings": {"size": 2, "draftSettings": {"type": "SNAKE", "auctionBudget": 200}},
        "draftDetail": {"drafted": False, "inProgress": True, "picks": picks},
    }


def test_filters_out_unplayed_placeholder_picks():
    raw = _synthetic_raw([
        {"playerId": -1, "teamId": 1, "roundId": 1, "roundPickNumber": 1,
         "overallPickNumber": 1, "bidAmount": 0},
        {"playerId": 4984, "teamId": 2, "roundId": 1, "roundPickNumber": 2,
         "overallPickNumber": 2, "bidAmount": 0},
    ])
    cw = Crosswalk(espn_to_sleeper={"4984": "sleeper-4984"}, unmatched=())

    state = draft.parse(raw, cw)

    assert len(state.picks) == 1
    pick = state.picks[0]
    assert pick.player_id == "sleeper-4984"
    assert pick.pick_no == 2
    assert pick.round == 1
    assert pick.draft_slot == 2
    assert pick.roster_id == 2
    assert pick.amount is None  # bidAmount 0 -> None, matching snake semantics
    assert pick.picked_by is None


def test_excludes_a_pick_whose_player_the_crosswalk_could_not_match():
    raw = _synthetic_raw([
        {"playerId": 99999, "teamId": 1, "roundId": 1, "roundPickNumber": 1,
         "overallPickNumber": 1, "bidAmount": 0},
    ])
    cw = Crosswalk(espn_to_sleeper={}, unmatched=("99999",))

    state = draft.parse(raw, cw)
    assert state.picks == ()


def test_carries_a_real_auction_bid_amount_through_when_present():
    """The parser is generic even though this project only connects ESPN
    snake leagues in this pass -- exercising the auction field costs
    nothing extra and proves it isn't silently dropped."""
    raw = _synthetic_raw([
        {"playerId": 4984, "teamId": 1, "roundId": 1, "roundPickNumber": 1,
         "overallPickNumber": 1, "bidAmount": 55},
    ])
    cw = Crosswalk(espn_to_sleeper={"4984": "sleeper-4984"}, unmatched=())

    state = draft.parse(raw, cw)
    assert state.picks[0].amount == 55
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/espn/test_draft.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.ingest.espn.draft'`

- [ ] **Step 3: Implement**

Create `src/ffdo/ingest/espn/draft.py`:

```python
"""Translates ESPN's `mDraftDetail` view into DraftState."""

from __future__ import annotations

from typing import Any

from ffdo.domain.models import DraftPick, DraftState
from ffdo.ingest.espn.crosswalk import Crosswalk


def parse(raw: dict[str, Any], crosswalk: Crosswalk) -> DraftState:
    settings = raw["settings"]
    draft_settings = settings.get("draftSettings") or {}
    detail = raw["draftDetail"]
    all_picks = detail.get("picks", [])
    num_teams = int(settings["size"])
    rounds = len(all_picks) // num_teams if num_teams else 0

    if detail.get("drafted"):
        status = "complete"
    elif detail.get("inProgress"):
        status = "drafting"
    else:
        status = "pre_draft"

    parsed: list[DraftPick] = []
    for p in all_picks:
        espn_player_id = p.get("playerId")
        # ESPN pre-populates the *entire* draft with placeholder picks
        # before it starts -- an unplayed slot carries playerId: -1
        # (verified live 2026-08-23; a team defense's REAL id is a large
        # negative number like -16034, so this check must be an exact
        # equality against -1, never `<= 0`).
        if espn_player_id is None or espn_player_id == -1:
            continue
        sleeper_player_id = crosswalk.espn_to_sleeper.get(str(espn_player_id))
        if sleeper_player_id is None:
            continue
        bid_amount = p.get("bidAmount")
        parsed.append(DraftPick(
            pick_no=int(p["overallPickNumber"]),
            round=int(p["roundId"]),
            draft_slot=int(p["roundPickNumber"]),
            roster_id=int(p["teamId"]),
            picked_by=None,
            player_id=sleeper_player_id,
            amount=int(bid_amount) if bid_amount else None,
        ))

    return DraftState(
        draft_id=str(raw["id"]),
        draft_type=draft_settings.get("type", "").lower(),
        status=status,
        num_teams=num_teams,
        rounds=rounds,
        budget=draft_settings.get("auctionBudget"),
        picks=tuple(parsed),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/espn/test_draft.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/espn/draft.py tests/ingest/espn/test_draft.py
git commit -m "feat: parse ESPN draft picks into DraftState, filtering placeholder picks"
```

---

### Task 8: `ingest/espn/teams.py` — `TeamProfile` parser + SWID lookup

**Files:**
- Create: `src/ffdo/ingest/espn/teams.py`
- Test: `tests/ingest/espn/test_teams.py`

**Interfaces:**
- Produces: `parse(raw: dict) -> dict[int, TeamProfile]`, `find_roster_id(raw: dict, swid: str) -> int | None`, both consumed by `ingest/espn/connect.py` (Task 9).

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/espn/test_teams.py`:

```python
from ffdo.ingest import snapshot
from ffdo.ingest.espn import teams

ESPN_SNAPSHOT_DIR = snapshot.DEFAULT_SNAPSHOT_DIR.parent / "2026-08-23-espn-league"


def _mteam():
    return snapshot.load("mTeam", snapshot_dir=ESPN_SNAPSHOT_DIR)


def test_parse_builds_a_team_profile_per_real_team():
    out = teams.parse(_mteam())
    assert len(out) == 10
    assert all(isinstance(rid, int) for rid in out)


def test_parse_still_names_a_team_with_no_owner():
    """The real league's team id 4 has an empty owners list (an unclaimed
    slot) -- must still produce a named team, not be skipped or crash."""
    out = teams.parse(_mteam())
    assert out[4].roster_id == 4
    assert out[4].display_name


def test_find_roster_id_resolves_the_swid_that_owns_a_team():
    """Verified against the real league: the sanitized fixture's synthetic
    stand-in for the real connecting user's SWID resolves to teamId 7."""
    roster_id = teams.find_roster_id(
        _mteam(), "{00000004-0000-0000-0000-000000000000}")
    assert roster_id == 7


def test_find_roster_id_returns_none_for_an_unknown_swid():
    assert teams.find_roster_id(
        _mteam(), "{ffffffff-0000-0000-0000-000000000000}") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/espn/test_teams.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.ingest.espn.teams'`

- [ ] **Step 3: Implement**

Create `src/ffdo/ingest/espn/teams.py`:

```python
"""Translates ESPN's `mTeam` view into team identity."""

from __future__ import annotations

from typing import Any

from ffdo.domain.models import TeamProfile


def parse(raw: dict[str, Any]) -> dict[int, TeamProfile]:
    out: dict[int, TeamProfile] = {}
    for t in raw.get("teams", []):
        team_id = t.get("id")
        if team_id is None:
            continue
        name = t.get("name") or " ".join(
            p for p in (t.get("location"), t.get("nickname")) if p) or f"Team {team_id}"
        out[int(team_id)] = TeamProfile(roster_id=int(team_id), display_name=name)
    return out


def find_roster_id(raw: dict[str, Any], swid: str) -> int | None:
    """`raw` is the mTeam view. `swid` should already be normalized to
    include braces (see ingest/espn/connect.py). Mirrors Sleeper's
    ingest.league.find_roster_id."""
    for t in raw.get("teams", []):
        owners = t.get("owners") or []
        if swid in owners:
            team_id = t.get("id")
            return int(team_id) if team_id is not None else None
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/espn/test_teams.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/espn/teams.py tests/ingest/espn/test_teams.py
git commit -m "feat: parse ESPN team identity and resolve SWID to a roster_id"
```

---

### Task 9: `ingest/espn/connect.py` — orchestration

**Files:**
- Create: `src/ffdo/ingest/espn/connect.py`
- Test: `tests/ingest/espn/test_connect.py`

**Interfaces:**
- Consumes: `ffdo.ingest.espn.client.EspnClient`/`BASE` (Task 4), `ffdo.ingest.espn.crosswalk.build`/`parse_player_pool` (Task 5), `ffdo.ingest.espn.league.parse`/`draft_type` (Task 6), `ffdo.ingest.espn.draft.parse` (Task 7), `ffdo.ingest.espn.teams.parse`/`find_roster_id` (Task 8), `ffdo.domain.models.PlayerProfile`/`Session`.
- Produces: `resolve(league_id, season, espn_s2, swid, profiles, espn_id_index, *, now=None, transport=None) -> Session`, `normalize_swid(swid: str) -> str`, `ConnectError`, consumed by `api/app.py` (Task 10).

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/espn/test_connect.py`:

```python
from datetime import datetime, timezone

import httpx
import pytest

from ffdo.ingest import snapshot
from ffdo.ingest.espn import connect

ESPN_SNAPSHOT_DIR = snapshot.DEFAULT_SNAPSHOT_DIR.parent / "2026-08-23-espn-league"
YOUR_SWID = "{00000004-0000-0000-0000-000000000000}"


def _combined_league_response():
    """The real API returns one merged object when multiple `view=` params
    are requested together; these fixtures were captured as three separate
    single-view requests during design validation (their shared top-level
    fields -- id/seasonId/status/etc. -- are identical across all three
    since they're views of the same league object), so recombine them here
    to match connect.py's actual combined-view request shape."""
    settings_raw = snapshot.load("mSettings", snapshot_dir=ESPN_SNAPSHOT_DIR)
    team_raw = snapshot.load("mTeam", snapshot_dir=ESPN_SNAPSHOT_DIR)
    draft_raw = snapshot.load("mDraftDetail", snapshot_dir=ESPN_SNAPSHOT_DIR)
    return {**settings_raw, **team_raw, **draft_raw}


def _dst_pool():
    return snapshot.load("espnPlayersDst", snapshot_dir=ESPN_SNAPSHOT_DIR)


def _handler(league_response=None):
    league_response = league_response or _combined_league_response()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "players?view=kona_player_info" in url:
            return httpx.Response(200, json=_dst_pool())
        if "leagues/1882997948" in url:
            return httpx.Response(200, json=league_response)
        raise AssertionError(f"unexpected URL: {url}")
    return handler


def test_resolve_returns_a_fully_populated_session_for_the_real_pre_draft_league():
    session = connect.resolve(
        "1882997948", 2026, "s2value", YOUR_SWID,
        profiles={}, espn_id_index={},
        transport=httpx.MockTransport(_handler()),
        now=lambda: datetime(2026, 8, 23, tzinfo=timezone.utc))

    assert session.provider == "espn"
    assert session.league_id == "1882997948"
    assert session.draft_id == "1882997948"
    assert session.roster_id == 7
    assert session.league_name == "Pigskin Pricing Experts"
    assert session.num_teams == 10
    assert session.draft_type == "snake"
    assert session.draft_status == "pre_draft"
    assert session.rounds == 15
    assert session.espn_s2 == "s2value"
    assert session.swid == YOUR_SWID
    assert session.connected_at == "2026-08-23T00:00:00+00:00"


def test_resolve_normalizes_a_swid_pasted_without_braces():
    bare_swid = YOUR_SWID.strip("{}")
    session = connect.resolve(
        "1882997948", 2026, "s2value", bare_swid,
        profiles={}, espn_id_index={},
        transport=httpx.MockTransport(_handler()))
    assert session.roster_id == 7
    assert session.swid == YOUR_SWID


def test_resolve_raises_when_the_league_is_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(connect.ConnectError, match="League not found"):
        connect.resolve("bad-league", 2026, "s2", YOUR_SWID, {}, {},
                        transport=httpx.MockTransport(handler))


def test_resolve_raises_when_the_draft_type_is_not_snake():
    raw = _combined_league_response()
    auction_raw = {
        **raw,
        "settings": {**raw["settings"], "draftSettings":
                    {**raw["settings"]["draftSettings"], "type": "AUCTION"}},
    }
    with pytest.raises(connect.ConnectError, match="auction"):
        connect.resolve("1882997948", 2026, "s2", YOUR_SWID, {}, {},
                        transport=httpx.MockTransport(_handler(auction_raw)))


def test_resolve_raises_when_the_swid_matches_no_team():
    unknown_swid = "{ffffffff-0000-0000-0000-000000000000}"
    with pytest.raises(connect.ConnectError, match="not a member"):
        connect.resolve("1882997948", 2026, "s2", unknown_swid, {}, {},
                        transport=httpx.MockTransport(_handler()))


def test_normalize_swid_adds_missing_braces():
    assert connect.normalize_swid("ABCD-1234") == "{ABCD-1234}"
    assert connect.normalize_swid("{ABCD-1234}") == "{ABCD-1234}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/espn/test_connect.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffdo.ingest.espn.connect'`

- [ ] **Step 3: Implement**

Create `src/ffdo/ingest/espn/connect.py`:

```python
"""Resolves an ESPN league ID + season + cookies into a connected Session.

Mirrors ffdo.ingest.connect's Sleeper flow: one-time orchestration when the
main screen's connect form is submitted, not run on every board poll.
Unlike the Sleeper flow (which receives an already-constructed,
credential-free SleeperClient), this module owns constructing its own
EspnClient internally, since it also needs the raw espn_s2/swid strings to
persist into the returned Session for later board polls to reuse.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from ffdo.domain.models import PlayerProfile, Session
from ffdo.ingest.espn import crosswalk as crosswalk_mod
from ffdo.ingest.espn import draft as draft_mod
from ffdo.ingest.espn import league as league_mod
from ffdo.ingest.espn import teams as teams_mod
from ffdo.ingest.espn.client import BASE, EspnClient


class ConnectError(Exception):
    """A user-facing reason resolve() could not connect an ESPN league."""


def normalize_swid(swid: str) -> str:
    """ESPN's own cookie jar wraps SWID in curly braces; a user pasting the
    raw value verbatim might paste it with or without them. Always store
    (and send) the braced form, since that's the form verified live
    against the real API."""
    swid = swid.strip()
    if not swid.startswith("{"):
        swid = "{" + swid
    if not swid.endswith("}"):
        swid = swid + "}"
    return swid


def resolve(
    league_id: str,
    season: int,
    espn_s2: str,
    swid: str,
    profiles: dict[str, PlayerProfile],
    espn_id_index: dict[str, str],
    *,
    now: Callable[[], datetime] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Session:
    now = now or (lambda: datetime.now(timezone.utc))
    swid = normalize_swid(swid)

    espn = EspnClient(espn_s2, swid, base_delay=0, transport=transport)
    try:
        try:
            raw = espn.get_json(
                f"{BASE}/seasons/{season}/segments/0/leagues/{league_id}"
                "?view=mSettings&view=mTeam&view=mDraftDetail")
        except httpx.HTTPStatusError as exc:
            raise ConnectError("League not found") from exc

        league = league_mod.parse(raw)

        if league_mod.draft_type(raw) != "snake":
            raise ConnectError("ESPN auction support isn't built yet")

        roster_id = teams_mod.find_roster_id(raw, swid)
        if roster_id is None:
            raise ConnectError("This SWID is not a member of that league")

        player_pool_raw = espn.get_json(
            f"{BASE}/seasons/{season}/players?view=kona_player_info")
        espn_players = crosswalk_mod.parse_player_pool(player_pool_raw)
        cw = crosswalk_mod.build(espn_id_index, profiles, espn_players)

        state = draft_mod.parse(raw, cw)
    finally:
        espn.close()

    return Session(
        username="",
        user_id=swid,
        league_id=league.league_id,
        draft_id=league.league_id,
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
        provider="espn",
        espn_s2=espn_s2,
        swid=swid,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/espn/test_connect.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `uv run pytest -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/ingest/espn/connect.py tests/ingest/espn/test_connect.py
git commit -m "feat: orchestrate ESPN league connect flow into a Session"
```

---

### Task 10: Wire ESPN into `api/app.py`

**Files:**
- Modify: `src/ffdo/api/app.py`

**Interfaces:**
- Consumes: `ffdo.ingest.espn.connect.resolve`/`ConnectError` (Task 9), `ffdo.ingest.espn.client.EspnClient`/`BASE` (Task 4), `ffdo.ingest.espn.crosswalk.parse_player_pool`/`build` (Task 5), `ffdo.ingest.espn.league.parse` (Task 6), `ffdo.ingest.espn.draft.parse` (Task 7), `ffdo.ingest.espn.teams.parse` (Task 8), `ffdo.ingest.players.espn_id_index` (Task 3).

No new automated test for the endpoint wiring itself — matches this project's existing convention (see `tests/api/test_app.py`, which tests only small pure helpers, not the live `/api/connect`/`/api/board` endpoints; the roster-rankings feature's equivalent app.py wiring task followed the same pattern). Verified instead by a live manual connect against the real ESPN league (Step 6 below).

- [ ] **Step 1: Add the ESPN import**

In `src/ffdo/api/app.py`, inside `create_app()`, replace:

```python
    from ffdo.ingest import teams as teams_mod
```

with:

```python
    from ffdo.ingest import teams as teams_mod
    from ffdo.ingest.espn import connect as espn_connect_mod
```

- [ ] **Step 2: Cache ESPN's player pool the same way Sleeper's players/projections are cached**

`get_board()` is polled every 3 seconds. ESPN's player-pool endpoint returns
thousands of entries (2,616 in the real, only-partially-filtered sample
captured during design) — fetching it on every poll would hammer ESPN's
API for no reason (the pool changes about as rarely as Sleeper's own global
player list does) and risks the connected account being rate-limited or
flagged mid-draft. Cache it exactly like `projections_caches`, keyed by
season.

Add, alongside the existing `projections_caches`/`teams_caches` declarations:

```python
    # Same reasoning as projections_caches/teams_caches: ESPN's player pool
    # is season-scoped and expensive to re-fetch (thousands of entries), and
    # get_board() is polled every 3s during a live draft -- an unkeyed or
    # uncached fetch here would hammer ESPN's API for data that barely
    # changes within a draft session.
    espn_player_pool_caches: dict[int, _TTLCache] = {}

    def _espn_player_pool_cache_for(season: int) -> _TTLCache:
        return espn_player_pool_caches.setdefault(season, _TTLCache(ttl_seconds=3600))
```

- [ ] **Step 3: Make `_load_players` expose the raw payload's `espn_id`s too**

Replace:

```python
    def _load_players(sleeper: client_mod.SleeperClient) -> dict:
        return players_mod.parse(sleeper.get_json(f"{client_mod.V1}/players/nfl"))
```

with:

```python
    def _load_players(sleeper: client_mod.SleeperClient) -> tuple[dict, dict]:
        """Returns (profiles, espn_id_index). Both are derived from the same
        raw fetch so ESPN connect's crosswalk doesn't need a second,
        separately-cached request for data players_cache already has."""
        raw = sleeper.get_json(f"{client_mod.V1}/players/nfl")
        return players_mod.parse(raw), players_mod.espn_id_index(raw)
```

Update every existing call site that destructures the old single-value return. In `get_board()`, replace:

```python
            profiles = players_cache.get(lambda: _load_players(sleeper))
```

with:

```python
            profiles, _espn_id_index = players_cache.get(lambda: _load_players(sleeper))
```

In `_warm_caches()`, replace:

```python
            players_cache.get(lambda: _load_players(sleeper))
```

with:

```python
            players_cache.get(lambda: _load_players(sleeper))  # warms the cache; return value unused here
```

(No functional change needed at this call site beyond the comment — `_warm_caches` already discards the return value.)

- [ ] **Step 4: Branch `/api/connect` and `/api/board` on `provider`**

Replace the `connect_league` endpoint:

```python
    @app.post("/api/connect")
    def connect_league(payload: dict, background_tasks: BackgroundTasks) -> dict:
        league_id = str(payload.get("league_id", "")).strip()
        username = str(payload.get("username", "")).strip()
        if not league_id or not username:
            raise HTTPException(
                status_code=400, detail="League ID and username are required")

        sleeper = client_mod.SleeperClient()
        try:
            session = connect_mod.resolve(sleeper, league_id, username)
        except connect_mod.ConnectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            sleeper.close()

        _SESSION_STORE.save(session)
        background_tasks.add_task(_warm_caches, session.season, session.league_id)
        return asdict(session)
```

with:

```python
    @app.post("/api/connect")
    def connect_league(payload: dict, background_tasks: BackgroundTasks) -> dict:
        provider = str(payload.get("provider") or "sleeper").strip().lower()

        if provider == "espn":
            league_id = str(payload.get("league_id", "")).strip()
            espn_s2 = str(payload.get("espn_s2", "")).strip()
            swid = str(payload.get("swid", "")).strip()
            if not league_id or not payload.get("season") or not espn_s2 or not swid:
                raise HTTPException(
                    status_code=400,
                    detail="League ID, season, espn_s2, and SWID are required")
            try:
                season = int(payload["season"])
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Season must be a year")

            sleeper = client_mod.SleeperClient()
            try:
                profiles, espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            finally:
                sleeper.close()

            try:
                session = espn_connect_mod.resolve(
                    league_id, season, espn_s2, swid, profiles, espn_id_index)
            except espn_connect_mod.ConnectError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            league_id = str(payload.get("league_id", "")).strip()
            username = str(payload.get("username", "")).strip()
            if not league_id or not username:
                raise HTTPException(
                    status_code=400, detail="League ID and username are required")

            sleeper = client_mod.SleeperClient()
            try:
                session = connect_mod.resolve(sleeper, league_id, username)
            except connect_mod.ConnectError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                sleeper.close()

        _SESSION_STORE.save(session)
        background_tasks.add_task(_warm_caches, session.season, session.league_id)
        return asdict(session)
```

Replace the start of `get_board`:

```python
    @app.get("/api/board")
    def get_board() -> dict:
        league_id = _league_id()
        draft_id = _draft_id()
        sleeper = client_mod.SleeperClient()
        try:
            lg = league_mod.parse(
                sleeper.get_json(f"{client_mod.V1}/league/{league_id}"))
            profiles, _espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            proj, adp_data = _projections_cache_for(lg.season).get(
                lambda: _load_projections(sleeper, lg.season))
            state = draft_mod.parse(
                sleeper.get_json(f"{client_mod.V1}/draft/{draft_id}"),
                sleeper.get_json(f"{client_mod.V1}/draft/{draft_id}/picks"))
            teams = _teams_cache_for(league_id).get(lambda: _load_teams(sleeper, league_id))
        finally:
            sleeper.close()
```

with:

```python
    @app.get("/api/board")
    def get_board() -> dict:
        session = _SESSION_STORE.get()
        provider = session.provider if session is not None else "sleeper"

        if provider == "espn":
            if session is None or session.espn_s2 is None or session.swid is None:
                raise HTTPException(
                    status_code=400,
                    detail="No connected ESPN session -- connect from the main screen first")

            sleeper = client_mod.SleeperClient()
            try:
                profiles, espn_id_index = players_cache.get(lambda: _load_players(sleeper))
                proj, adp_data = _projections_cache_for(session.season).get(
                    lambda: _load_projections(sleeper, session.season))
            finally:
                sleeper.close()

            espn = espn_client_mod.EspnClient(session.espn_s2, session.swid)
            try:
                raw = espn.get_json(
                    f"{espn_client_mod.BASE}/seasons/{session.season}/segments/0/"
                    f"leagues/{session.league_id}?view=mSettings&view=mTeam&view=mDraftDetail")
                player_pool_raw = _espn_player_pool_cache_for(session.season).get(
                    lambda: espn.get_json(
                        f"{espn_client_mod.BASE}/seasons/{session.season}/players"
                        "?view=kona_player_info"))
            finally:
                espn.close()

            lg = espn_league_mod.parse(raw)
            espn_players = espn_crosswalk_mod.parse_player_pool(player_pool_raw)
            cw = espn_crosswalk_mod.build(espn_id_index, profiles, espn_players)
            state = espn_draft_mod.parse(raw, cw)
            teams = espn_teams_mod.parse(raw)
        else:
            league_id = _league_id()
            draft_id = _draft_id()
            sleeper = client_mod.SleeperClient()
            try:
                lg = league_mod.parse(
                    sleeper.get_json(f"{client_mod.V1}/league/{league_id}"))
                profiles, _espn_id_index = players_cache.get(lambda: _load_players(sleeper))
                proj, adp_data = _projections_cache_for(lg.season).get(
                    lambda: _load_projections(sleeper, lg.season))
                state = draft_mod.parse(
                    sleeper.get_json(f"{client_mod.V1}/draft/{draft_id}"),
                    sleeper.get_json(f"{client_mod.V1}/draft/{draft_id}/picks"))
                teams = _teams_cache_for(league_id).get(lambda: _load_teams(sleeper, league_id))
            finally:
                sleeper.close()
```

The rest of `get_board()` (the budget fallback, scoring/VOR computation, auction/snake branch) is unchanged — it already only reads `lg`/`state`/`teams`/`profiles`/`proj`/`adp_data`, all now populated identically regardless of provider.

Add the four new ESPN imports alongside the existing ones at the top of `create_app()` (next to the `espn_connect_mod` import added in Step 1):

```python
    from ffdo.ingest.espn import client as espn_client_mod
    from ffdo.ingest.espn import crosswalk as espn_crosswalk_mod
    from ffdo.ingest.espn import draft as espn_draft_mod
    from ffdo.ingest.espn import league as espn_league_mod
    from ffdo.ingest.espn import teams as espn_teams_mod
```

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -v`
Expected: all PASS.

- [ ] **Step 6: Manually verify against the real ESPN league**

Run: `uv run uvicorn ffdo.api.app:app --port 8000`

In another terminal, connect using the real league (this actually calls ESPN live — reuse the same league ID/season/cookies validated during design, asking your human partner for fresh `espn_s2`/`SWID` values if the ones used during design have since expired):

```bash
curl -s -X POST http://localhost:8000/api/connect -H "Content-Type: application/json" -d '{"provider":"espn","league_id":"1882997948","season":2026,"espn_s2":"<value>","swid":"<value>"}'
```

Expected: a 200 response with a JSON `Session` body, `"provider":"espn"`, `"roster_id":7`, `"league_name":"Pigskin Pricing Experts"`.

Then: `curl -s http://localhost:8000/api/board | head -c 2000`

Expected: valid JSON with `"format":"snake"`, a `players` array, and a `rosters` array (the roster-rankings panel) — confirming the whole existing board pipeline runs unmodified against ESPN-sourced league/draft/team data. Stop the server with Ctrl-C when done. If the connect step fails with an auth error, the cookies have likely expired since design validation — ask your human partner for fresh ones rather than guessing.

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/api/app.py
git commit -m "feat: wire ESPN provider into the connect and board endpoints"
```

---

### Task 11: Connect form — provider toggle

**Files:**
- Modify: `src/ffdo/web/index.html`
- Modify: `src/ffdo/web/main.js`
- Modify: `src/ffdo/web/main.css`

No automated test (static markup/JS, no test runner in this project, matching how the roster-rankings feature's equivalent frontend tasks were verified). Verified manually in a browser (Step 5).

- [ ] **Step 1: Add the provider toggle and ESPN-only fields to the connect form**

In `src/ffdo/web/index.html`, replace:

```html
  <section id="connect-form" class="card">
    <h1>Connect your league</h1>
    <label class="field">
      <span>Sleeper League ID</span>
      <input id="league-id-input" type="text" placeholder="e.g. 1315881559957458944" autocomplete="off">
    </label>
    <label class="field">
      <span>Sleeper Username</span>
      <input id="username-input" type="text" placeholder="e.g. yourusername" autocomplete="off">
    </label>
    <p id="connect-error" class="error-msg" hidden></p>
    <button id="connect-btn">Connect</button>
  </section>
```

with:

```html
  <section id="connect-form" class="card">
    <h1>Connect your league</h1>
    <div class="provider-toggle" id="provider-toggle">
      <button data-provider="sleeper" class="on">Sleeper</button>
      <button data-provider="espn">ESPN</button>
    </div>
    <div id="sleeper-fields">
      <label class="field">
        <span>Sleeper League ID</span>
        <input id="league-id-input" type="text" placeholder="e.g. 1315881559957458944" autocomplete="off">
      </label>
      <label class="field">
        <span>Sleeper Username</span>
        <input id="username-input" type="text" placeholder="e.g. yourusername" autocomplete="off">
      </label>
    </div>
    <div id="espn-fields" hidden>
      <label class="field">
        <span>ESPN League ID</span>
        <input id="espn-league-id-input" type="text" placeholder="e.g. 1882997948" autocomplete="off">
      </label>
      <label class="field">
        <span>Season</span>
        <input id="espn-season-input" type="text" placeholder="e.g. 2026" autocomplete="off">
      </label>
      <label class="field">
        <span>espn_s2 cookie</span>
        <input id="espn-s2-input" type="text" placeholder="paste the espn_s2 cookie value" autocomplete="off">
      </label>
      <label class="field">
        <span>SWID cookie</span>
        <input id="espn-swid-input" type="text" placeholder="e.g. {XXXXXXXX-XXXX-...}" autocomplete="off">
      </label>
      <p class="note">Private league? Log into fantasy.espn.com, open your browser's devtools, and find these two cookie values under Storage/Application &rarr; Cookies &rarr; fantasy.espn.com.</p>
    </div>
    <p id="connect-error" class="error-msg" hidden></p>
    <button id="connect-btn">Connect</button>
  </section>
```

- [ ] **Step 2: Wire the provider toggle and update `connect()` in `main.js`**

In `src/ffdo/web/main.js`, add `provider: "sleeper",` to the `state` object:

```javascript
let state = {
  session: null,
  format: null,
  connecting: false,
  readinessTimer: null,
  provider: "sleeper",
};
```

Replace the `connect()` function:

```javascript
async function connect() {
  const leagueId = document.getElementById("league-id-input").value.trim();
  const username = document.getElementById("username-input").value.trim();
  const errorEl = document.getElementById("connect-error");
  errorEl.hidden = true;

  if (!leagueId || !username) {
    errorEl.textContent = "League ID and username are both required.";
    errorEl.hidden = false;
    return;
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
      body: JSON.stringify({ league_id: leagueId, username }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      errorEl.textContent = body.detail || "Could not connect to that league.";
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

document.getElementById("connect-btn").addEventListener("click", connect);
```

with:

```javascript
function connectPayload() {
  if (state.provider === "espn") {
    return {
      provider: "espn",
      league_id: document.getElementById("espn-league-id-input").value.trim(),
      season: document.getElementById("espn-season-input").value.trim(),
      espn_s2: document.getElementById("espn-s2-input").value.trim(),
      swid: document.getElementById("espn-swid-input").value.trim(),
    };
  }
  return {
    provider: "sleeper",
    league_id: document.getElementById("league-id-input").value.trim(),
    username: document.getElementById("username-input").value.trim(),
  };
}

function connectPayloadIsComplete(payload) {
  if (payload.provider === "espn") {
    return payload.league_id && payload.season && payload.espn_s2 && payload.swid;
  }
  return payload.league_id && payload.username;
}

async function connect() {
  const payload = connectPayload();
  const errorEl = document.getElementById("connect-error");
  errorEl.hidden = true;

  if (!connectPayloadIsComplete(payload)) {
    errorEl.textContent = payload.provider === "espn"
      ? "League ID, season, espn_s2, and SWID are all required."
      : "League ID and username are both required.";
    errorEl.hidden = false;
    return;
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
      errorEl.textContent = body.detail || "Could not connect to that league.";
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

document.getElementById("connect-btn").addEventListener("click", connect);

document.querySelectorAll("#provider-toggle button").forEach(btn =>
  btn.addEventListener("click", () => {
    state.provider = btn.dataset.provider;
    document.querySelectorAll("#provider-toggle button").forEach(b =>
      b.classList.toggle("on", b === btn));
    document.getElementById("sleeper-fields").hidden = state.provider !== "sleeper";
    document.getElementById("espn-fields").hidden = state.provider !== "espn";
    document.getElementById("connect-error").hidden = true;
  }));
```

- [ ] **Step 3: Style the provider toggle**

In `src/ffdo/web/main.css`, add after the `.error-msg` rule:

```css
.provider-toggle {
  display: flex;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 3px;
  gap: 2px;
  align-self: flex-start;
}
.provider-toggle button {
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: 0.5px;
  padding: 8px 16px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
}
.provider-toggle button.on { background: var(--accent); color: #0a0f14; }
#sleeper-fields, #espn-fields { display: flex; flex-direction: column; gap: 16px; }
```

- [ ] **Step 4: Verify well-formedness**

Read `src/ffdo/web/index.html` back in full and confirm: every opened tag is closed, `#sleeper-fields`/`#espn-fields` each wrap exactly their own two/four fields, no duplicate element ids.

- [ ] **Step 5: Manually verify in a browser**

Run: `uv run uvicorn ffdo.api.app:app --port 8000`

Open `http://localhost:8000`. Confirm:
- The form defaults to showing the Sleeper fields (league ID, username), "Sleeper" toggle highlighted.
- Clicking "ESPN" swaps to the four ESPN fields (league ID, season, espn_s2, SWID) with the devtools note visible, and highlights "ESPN" instead.
- Clicking back to "Sleeper" swaps back and hides the ESPN fields.
- Submitting either form with fields missing shows the correct provider-specific error message.
- (If you have a fresh, unexpired `espn_s2`/`SWID` handy) submitting the ESPN form with real values against the real league connects successfully and shows the connected-league view exactly as a Sleeper connection would.

Stop the server with Ctrl-C when done.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/web/index.html src/ffdo/web/main.js src/ffdo/web/main.css
git commit -m "feat: add ESPN provider toggle to the connect form"
```

---

### Task 12: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the complete test suite**

Run: `uv run pytest -v`
Expected: all PASS, no skips, no warnings beyond the pre-existing `StarletteDeprecationWarning` (unrelated to this feature, present before it started).

- [ ] **Step 2: Confirm no leftover debug output or stray files**

Run `git status --short` from the repo root and confirm nothing unexpected is untracked. Re-read `src/ffdo/ingest/espn/connect.py` and `src/ffdo/api/app.py` once end-to-end for stray `print()`/`console.log`/commented-out code.

- [ ] **Step 3: Remind your human partner about the temp validation files**

The design work for this feature captured real ESPN API responses at `C:\Users\basek\AppData\Local\Temp\espn_validation\` on the controlling machine (outside this repo). Per your human partner's explicit instruction, these should be deleted now that this plan's implementation is complete — but you (the implementer) may not be running on that machine. State this clearly in your final report to the controller rather than attempting the deletion yourself if you can't confirm you're on the right machine; the controller session has a memory note (`project_espn_validation_temp_cleanup`) tracking this obligation.

- [ ] **Step 4: Final commit if anything was cleaned up in Step 2**

```bash
git add -A
git commit -m "chore: final cleanup pass on ESPN league support feature"
```

(Skip this step entirely if Step 2 found nothing to change.)
