# Fantasy Football Draft Optimizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local web board that values NFL players against a specific Sleeper league's scoring and roster rules, tracks a live draft, and shows the cost of waiting (snake) or live price inflation (auction) — without ever naming a pick.

**Architecture:** Five layers, each depending only on the one beneath: `domain/` (frozen dataclasses) → `ingest/` (Sleeper adapters + SQLite cache, the only code that sees Sleeper JSON keys) → `engine/` (pure functions: value, market, scarcity, auction) → `api/` (FastAPI) → `web/` (static page, no build step). Purity in `engine/` is what makes the backtest and the auction replay possible.

**Tech Stack:** Python 3.12 via `uv`, pytest, numpy, scipy, FastAPI, uvicorn, httpx. Frontend is vanilla HTML/CSS/JS, no build step.

**Spec:** `docs/superpowers/specs/2026-08-22-fantasy-football-draft-optimizer-design.md`

## Global Constraints

- **Python 3.12**, managed by `uv`. Binary at `C:\Users\basek\.local\bin\uv.exe` (not on PATH in all shells — invoke by full path if `uv` is not found).
- **Package root:** `src/ffdo/`. Tests in `tests/`, mirroring package structure.
- **All commands run from the worktree root**, `.claude/worktrees/fantasy-football-draft-tool-f2a9fd`.
- **Never use Sleeper's precomputed `pts_ppr` / `pts_half_ppr` / `pts_std` as a value input.** Sleeper changed the `pts_half_ppr` definition between 2021 and 2023 (2021 included `fum: -1`, 2023+ does not). Always rescore from component stats. Using them as a *golden-test target for 2025 only* is the one permitted exception (Task 5).
- **Never use historical projections (2022–2025) as a preseason input.** They are contaminated with in-season data — Nick Chubb's 2023 row carries no `pts_*` key at all against a preseason ADP of 10.6; the projection was wiped after his week-2 injury. Historical **ADP** is clean and is the only permitted historical market baseline.
- **`SEASON_LENGTH = {2021: 17, 2022: 17, 2023: 17, 2024: 18, 2025: 18, 2026: 18}`** — never divide by a constant.
- **Age and durability adjustment weights default to `0.0`.** They may only become non-zero if Task 12's backtest shows out-of-sample improvement over the ADP baseline.
- **Primary league:** `league_id = "1315881559957458944"`, **2026 draft:** `draft_id = "1315881559965835264"` (type `auction`, budget 200, 12 teams, 13 rounds).
- **Offline fixtures:** `data/snapshots/2026-08-22-draft-day/*.json.gz` (gzipped raw Sleeper JSON). **No test may hit the network.**
- Sleeper base URLs: `https://api.sleeper.app/v1/...` for players/stats/league/draft; `https://api.sleeper.app/projections/nfl/<season>?season_type=regular` for projections (no `/v1`).
- **Commit after every task.** Do not push.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/ffdo/domain/models.py` | Frozen dataclasses shared by every layer |
| `src/ffdo/domain/constants.py` | `SEASON_LENGTH`, position sets, scoring key classification |
| `src/ffdo/ingest/client.py` | HTTP with retry + rate limiting; the only place `httpx` is used |
| `src/ffdo/ingest/cache.py` | SQLite read-through cache with TTL |
| `src/ffdo/ingest/snapshot.py` | Loads gzipped snapshot files as a fixture source |
| `src/ffdo/ingest/players.py` | `/v1/players/nfl` → `PlayerProfile` |
| `src/ffdo/ingest/stats.py` | `/v1/stats/nfl/regular/<yr>` → `SeasonStatLine` |
| `src/ffdo/ingest/projections.py` | projections → `SeasonProjection` + `MarketADP`, **with contamination guard** |
| `src/ffdo/ingest/league.py` | `/v1/league/<id>` → `LeagueProfile` |
| `src/ffdo/ingest/draft.py` | `/v1/draft/<id>` + `/picks` → `DraftState` |
| `src/ffdo/engine/scoring.py` | Rescore component stats under league scoring settings |
| `src/ffdo/engine/replacement.py` | Replacement level from `roster_positions` |
| `src/ffdo/engine/vor.py` | VOR + tier assignment |
| `src/ffdo/engine/auction.py` | Baseline dollars, live inflation, max bid |
| `src/ffdo/engine/market.py` | Draft simulation, survival probability, cost of waiting |
| `src/ffdo/engine/adjustments.py` | Age curve + durability estimators (default-off) |
| `src/ffdo/backtest/harness.py` | Out-of-sample scoring against ADP baseline |
| `src/ffdo/api/app.py` | FastAPI app, board-state endpoints, draft poller |
| `src/ffdo/web/index.html` + `board.js` + `board.css` | The board |

---

### Task 1: Project scaffold and domain models

This task must complete and be reviewed **before any other task starts**. Every
later task imports these types; two agents inventing their own `PlayerProfile`
is worse than the work itself.

**Files:**
- Create: `pyproject.toml`, `.gitignore`
- Create: `src/ffdo/__init__.py`, `src/ffdo/domain/__init__.py`
- Create: `src/ffdo/domain/constants.py`, `src/ffdo/domain/models.py`
- Test: `tests/domain/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: every dataclass and constant below. These names are final.

- [ ] **Step 1: Initialize the project**

```bash
uv init --package --name ffdo --python 3.12 .
uv add numpy scipy fastapi uvicorn httpx
uv add --dev pytest pytest-cov
```

If `uv` is not found, use `"$HOME/.local/bin/uv"`.

- [ ] **Step 2: Write `.gitignore`**

```
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.coverage
*.sqlite
```

Note: `data/snapshots/` is committed deliberately — do not ignore it.

- [ ] **Step 3: Write `src/ffdo/domain/constants.py`**

```python
"""Constants that encode verified facts about Sleeper's data."""

from typing import Final

# Regular-season game count by season. The NFL moved 17 -> 18 games in 2024.
# Availability rates MUST normalize against this, never a constant.
SEASON_LENGTH: Final[dict[int, int]] = {
    2021: 17, 2022: 17, 2023: 17, 2024: 18, 2025: 18, 2026: 18,
}

OFFENSE_POSITIONS: Final[frozenset[str]] = frozenset({"QB", "RB", "WR", "TE"})

# Scoring keys credited to defensive/special-teams units, never to an
# offensive player, even when the key appears in a league's scoring settings.
_DEFENSIVE_ONLY: Final[frozenset[str]] = frozenset({"fum_rec", "fum_rec_td"})

_OFFENSE_PREFIXES: Final[tuple[str, ...]] = ("pass_", "rush_", "rec_", "bonus_")

# Bare keys (no prefix) that still score for an offensive player. `st_td`
# is included because Sleeper credits return touchdowns to the returner,
# which is verified by the Task 5 golden test.
_OFFENSE_BARE: Final[frozenset[str]] = frozenset({"rec", "fum", "fum_lost", "st_td"})


def is_offense_scoring_key(key: str) -> bool:
    """True if `key` scores for an offensive player."""
    if key in _DEFENSIVE_ONLY:
        return False
    return key.startswith(_OFFENSE_PREFIXES) or key in _OFFENSE_BARE


# Verified to reproduce Sleeper's 2025 `pts_half_ppr` for >=98% of players
# scoring 50+ points. Used ONLY as a golden-test target (Task 5).
STANDARD_HALF_PPR: Final[dict[str, float]] = {
    "pass_yd": 0.04, "pass_td": 4, "pass_int": -1, "pass_2pt": 2,
    "rush_yd": 0.1, "rush_td": 6, "rush_2pt": 2,
    "rec": 0.5, "rec_yd": 0.1, "rec_td": 6, "rec_2pt": 2,
    "fum_lost": -2, "st_td": 6,
}
```

- [ ] **Step 4: Write the failing test for domain models**

```python
# tests/domain/test_models.py
import dataclasses
import pytest

from ffdo.domain.constants import SEASON_LENGTH, is_offense_scoring_key
from ffdo.domain.models import (
    DraftPick, DraftState, LeagueProfile, MarketADP,
    PlayerProfile, SeasonProjection, SeasonStatLine,
)


def _player(**kw):
    base = dict(player_id="9221", first_name="Jahmyr", last_name="Gibbs",
                position="RB", team="DET", age=24, years_exp=3,
                injury_status=None, active=True)
    return PlayerProfile(**{**base, **kw})


def test_player_profile_is_frozen():
    p = _player()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.age = 25


def test_player_full_name():
    assert _player().full_name == "Jahmyr Gibbs"


def test_season_length_covers_history_and_current():
    assert SEASON_LENGTH[2023] == 17
    assert SEASON_LENGTH[2024] == 18
    assert SEASON_LENGTH[2026] == 18


@pytest.mark.parametrize("key,expected", [
    ("rec", True), ("rec_yd", True), ("rush_td", True), ("pass_yd", True),
    ("fum", True), ("fum_lost", True), ("st_td", True), ("bonus_rec_te", True),
    ("fum_rec", False), ("fum_rec_td", False),
    ("pts_allow_0", False), ("fgm_40_49", False), ("sack", False),
])
def test_offense_scoring_key_classification(key, expected):
    assert is_offense_scoring_key(key) is expected


def test_league_profile_derives_starting_slots_and_roster_size():
    lg = LeagueProfile(
        league_id="1315881559957458944", season=2026, num_teams=12,
        roster_positions=("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                          "BN", "BN", "BN", "BN", "BN"),
        scoring_settings={"rec": 0.5}, budget=200,
    )
    assert lg.starting_slots == ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX")
    assert lg.roster_size == 13


def test_draft_state_reports_spend_per_roster():
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=4, roster_id=9,
                  picked_by="437507358097141760", player_id="11566", amount=42),
        DraftPick(pick_no=2, round=1, draft_slot=5, roster_id=3,
                  picked_by="x", player_id="4034", amount=58),
        DraftPick(pick_no=3, round=1, draft_slot=6, roster_id=9,
                  picked_by="437507358097141760", player_id="6786", amount=10),
    )
    st = DraftState(draft_id="d", draft_type="auction", status="drafting",
                    num_teams=12, rounds=13, budget=200, picks=picks)
    assert st.spent_by_roster()[9] == 52
    assert st.drafted_player_ids() == frozenset({"11566", "4034", "6786"})


def test_stat_line_and_projection_and_adp_construct():
    s = SeasonStatLine(player_id="9221", season=2025, games_played=17,
                       season_length=18, stats={"rush_yd": 1200.0})
    assert s.games_missed == 1
    SeasonProjection(player_id="9221", season=2026,
                     stats={"rush_yd": 1100.0}, last_modified=None)
    MarketADP(player_id="9221", season=2026, adp={"half_ppr": 1.8})
```

- [ ] **Step 5: Run the test to verify it fails**

Run: `uv run pytest tests/domain/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.domain.models'`

- [ ] **Step 6: Write `src/ffdo/domain/models.py`**

```python
"""Frozen types shared by every layer. No I/O, no Sleeper JSON keys."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PlayerProfile:
    player_id: str
    first_name: str
    last_name: str
    position: str
    team: str | None
    age: int | None
    years_exp: int | None
    injury_status: str | None
    active: bool

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


@dataclass(frozen=True, slots=True)
class SeasonStatLine:
    player_id: str
    season: int
    games_played: int
    season_length: int
    stats: Mapping[str, float]

    @property
    def games_missed(self) -> int:
        return max(0, self.season_length - self.games_played)


@dataclass(frozen=True, slots=True)
class SeasonProjection:
    player_id: str
    season: int
    stats: Mapping[str, float]
    last_modified: datetime | None


@dataclass(frozen=True, slots=True)
class MarketADP:
    player_id: str
    season: int
    adp: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class LeagueProfile:
    league_id: str
    season: int
    num_teams: int
    roster_positions: tuple[str, ...]
    scoring_settings: Mapping[str, float]
    budget: int | None

    @property
    def starting_slots(self) -> tuple[str, ...]:
        return tuple(p for p in self.roster_positions if p != "BN")

    @property
    def roster_size(self) -> int:
        return len(self.roster_positions)


@dataclass(frozen=True, slots=True)
class DraftPick:
    pick_no: int
    round: int
    draft_slot: int
    roster_id: int | None
    picked_by: str | None
    player_id: str
    amount: int | None


@dataclass(frozen=True, slots=True)
class DraftState:
    draft_id: str
    draft_type: str
    status: str
    num_teams: int
    rounds: int
    budget: int | None
    picks: tuple[DraftPick, ...]

    def drafted_player_ids(self) -> frozenset[str]:
        return frozenset(p.player_id for p in self.picks)

    def spent_by_roster(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for p in self.picks:
            if p.roster_id is None or p.amount is None:
                continue
            out[p.roster_id] = out.get(p.roster_id, 0) + p.amount
        return out


@dataclass(frozen=True, slots=True)
class ValuedPlayer:
    profile: PlayerProfile
    projected_points: float
    adjusted_points: float
    vor: float
    tier: int
    adjustments: Mapping[str, float]
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `uv run pytest tests/domain/test_models.py -v`
Expected: PASS, all tests.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .gitignore uv.lock src/ tests/
git commit -m "feat: project scaffold and domain models"
```

---

### Task 2: Snapshot loader and HTTP client

**Files:**
- Create: `src/ffdo/ingest/__init__.py`, `src/ffdo/ingest/snapshot.py`, `src/ffdo/ingest/client.py`
- Test: `tests/ingest/test_snapshot.py`

**Interfaces:**
- Consumes: nothing from Task 1 beyond package layout.
- Produces:
  - `snapshot.load(name: str, snapshot_dir: Path | None = None) -> Any` — reads `<name>.json.gz` from the snapshot directory and returns parsed JSON. Default dir is `data/snapshots/2026-08-22-draft-day`.
  - `snapshot.DEFAULT_SNAPSHOT_DIR: Path`
  - `client.SleeperClient(base_delay: float = 0.0)` with `get_json(url: str) -> Any`, retrying on 429/5xx with exponential backoff, max 4 attempts.

- [ ] **Step 1: Write the failing test**

```python
# tests/ingest/test_snapshot.py
from ffdo.ingest import snapshot


def test_loads_players_snapshot():
    players = snapshot.load("players_nfl")
    assert isinstance(players, dict)
    assert len(players) > 10_000
    assert players["9221"]["last_name"] == "Gibbs"


def test_loads_league_history_snapshot():
    hist = snapshot.load("league_history")
    assert hist["leagues"]["2026"]["league_id"] == "1315881559957458944"
    assert hist["drafts"]["2025"]["meta"]["type"] == "auction"
    assert len(hist["drafts"]["2025"]["picks"]) == 168


def test_loads_every_season_of_stats():
    for season in (2021, 2022, 2023, 2024, 2025):
        stats = snapshot.load(f"stats_{season}")
        assert len(stats) > 1_000
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/ingest/test_snapshot.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.ingest'`

- [ ] **Step 3: Write `src/ffdo/ingest/snapshot.py`**

```python
"""Reads the committed preseason snapshot. Fixture source for all tests."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SNAPSHOT_DIR = _REPO_ROOT / "data" / "snapshots" / "2026-08-22-draft-day"


def load(name: str, snapshot_dir: Path | None = None) -> Any:
    path = (snapshot_dir or DEFAULT_SNAPSHOT_DIR) / f"{name}.json.gz"
    if not path.exists():
        raise FileNotFoundError(f"no snapshot {name!r} at {path}")
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)
```

If `_REPO_ROOT` resolves incorrectly, adjust `parents[N]` so the path lands on
the worktree root; verify with `uv run python -c "from ffdo.ingest import snapshot; print(snapshot.DEFAULT_SNAPSHOT_DIR)"`.

- [ ] **Step 4: Write `src/ffdo/ingest/client.py`**

```python
"""The only module that performs HTTP. Everything else reads cache or snapshot."""

from __future__ import annotations

import time
from typing import Any

import httpx

V1 = "https://api.sleeper.app/v1"
PROJECTIONS = "https://api.sleeper.app/projections/nfl"


class SleeperClient:
    """Sleeper asks callers to stay under 1000 requests/minute."""

    def __init__(self, base_delay: float = 0.0, timeout: float = 30.0) -> None:
        self._base_delay = base_delay
        self._client = httpx.Client(timeout=timeout)

    def get_json(self, url: str, max_attempts: int = 4) -> Any:
        last: Exception | None = None
        for attempt in range(max_attempts):
            try:
                resp = self._client.get(url)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"retryable {resp.status_code}",
                        request=resp.request, response=resp,
                    )
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last = exc
                if attempt == max_attempts - 1:
                    break
                time.sleep(self._base_delay + 2**attempt)
        raise RuntimeError(f"GET {url} failed after {max_attempts} attempts") from last

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/ingest/test_snapshot.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/ingest/ tests/ingest/
git commit -m "feat: snapshot loader and Sleeper HTTP client"
```

---

### Task 3: Player, stats, and league adapters

**Files:**
- Create: `src/ffdo/ingest/players.py`, `src/ffdo/ingest/stats.py`, `src/ffdo/ingest/league.py`
- Test: `tests/ingest/test_adapters.py`

**Interfaces:**
- Consumes: `PlayerProfile`, `SeasonStatLine`, `LeagueProfile`, `SEASON_LENGTH`, `snapshot.load`.
- Produces:
  - `players.parse(raw: dict) -> dict[str, PlayerProfile]`
  - `stats.parse(raw: dict, season: int) -> dict[str, SeasonStatLine]`
  - `league.parse(raw: dict) -> LeagueProfile`

- [ ] **Step 1: Write the failing test**

```python
# tests/ingest/test_adapters.py
from ffdo.ingest import league, players, snapshot, stats


def test_players_parse_extracts_profile_fields():
    parsed = players.parse(snapshot.load("players_nfl"))
    gibbs = parsed["9221"]
    assert gibbs.full_name == "Jahmyr Gibbs"
    assert gibbs.position == "RB"
    assert gibbs.team == "DET"
    assert gibbs.age == 24
    assert gibbs.active is True


def test_players_parse_tolerates_missing_age():
    raw = {"1": {"first_name": "A", "last_name": "B", "position": "WR",
                 "team": None, "age": None, "years_exp": None,
                 "injury_status": None, "active": False}}
    assert players.parse(raw)["1"].age is None


def test_stats_parse_sets_season_length_from_table():
    parsed = stats.parse(snapshot.load("stats_2023"), 2023)
    assert all(s.season_length == 17 for s in parsed.values())
    parsed_2025 = stats.parse(snapshot.load("stats_2025"), 2025)
    assert all(s.season_length == 18 for s in parsed_2025.values())


def test_stats_parse_keeps_component_stats_and_games_played():
    parsed = stats.parse(snapshot.load("stats_2025"), 2025)
    gibbs = parsed["9221"]
    assert gibbs.games_played > 0
    assert "rush_yd" in gibbs.stats


def test_league_parse_reads_roster_and_scoring():
    raw = snapshot.load("league_history")["leagues"]["2026"]
    lg = league.parse(raw)
    assert lg.num_teams == 12
    assert lg.starting_slots == ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX")
    assert lg.roster_size == 13
    assert lg.scoring_settings["rec"] == 0.5
    # This league penalises ALL fumbles, not just lost ones. It is the single
    # scoring rule that diverges from Sleeper's half-PPR preset.
    assert lg.scoring_settings["fum"] == -1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/ingest/test_adapters.py -v`
Expected: FAIL — `ImportError: cannot import name 'players'`

- [ ] **Step 3: Write `src/ffdo/ingest/players.py`**

```python
"""Translates /v1/players/nfl into PlayerProfile. Sleeper keys stop here."""

from __future__ import annotations

from typing import Any

from ffdo.domain.models import PlayerProfile


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse(raw: dict[str, Any]) -> dict[str, PlayerProfile]:
    out: dict[str, PlayerProfile] = {}
    for player_id, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        position = rec.get("position")
        if position is None:
            continue
        out[player_id] = PlayerProfile(
            player_id=player_id,
            first_name=rec.get("first_name") or "",
            last_name=rec.get("last_name") or "",
            position=position,
            team=rec.get("team"),
            age=_as_int(rec.get("age")),
            years_exp=_as_int(rec.get("years_exp")),
            injury_status=rec.get("injury_status") or None,
            active=bool(rec.get("active")),
        )
    return out
```

- [ ] **Step 4: Write `src/ffdo/ingest/stats.py`**

```python
"""Translates /v1/stats/nfl/regular/<season> into SeasonStatLine."""

from __future__ import annotations

from typing import Any

from ffdo.domain.constants import SEASON_LENGTH
from ffdo.domain.models import SeasonStatLine


def parse(raw: dict[str, Any], season: int) -> dict[str, SeasonStatLine]:
    length = SEASON_LENGTH[season]
    out: dict[str, SeasonStatLine] = {}
    for player_id, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        numeric = {k: float(v) for k, v in rec.items()
                   if isinstance(v, (int, float))}
        out[player_id] = SeasonStatLine(
            player_id=player_id,
            season=season,
            games_played=int(numeric.get("gp", 0)),
            season_length=length,
            stats=numeric,
        )
    return out
```

- [ ] **Step 5: Write `src/ffdo/ingest/league.py`**

```python
"""Translates /v1/league/<id> into LeagueProfile."""

from __future__ import annotations

from typing import Any

from ffdo.domain.models import LeagueProfile


def parse(raw: dict[str, Any]) -> LeagueProfile:
    settings = raw.get("settings") or {}
    return LeagueProfile(
        league_id=raw["league_id"],
        season=int(raw["season"]),
        num_teams=int(settings.get("num_teams") or raw.get("total_rosters")),
        roster_positions=tuple(raw["roster_positions"]),
        scoring_settings={k: float(v)
                          for k, v in (raw.get("scoring_settings") or {}).items()},
        budget=settings.get("budget"),
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/ingest/test_adapters.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/ingest/ tests/ingest/
git commit -m "feat: player, stats, and league adapters"
```

---

### Task 4: Projection adapter with contamination guard

The guard is the point of this task. Sleeper's projections endpoint returns
*latest* state, not preseason state — for past seasons, `pts_*` values have been
overwritten with in-season data. This bug class is silent and fatal, so ingest
must refuse rather than trust.

**Files:**
- Create: `src/ffdo/ingest/projections.py`
- Test: `tests/ingest/test_projections.py`

**Interfaces:**
- Consumes: `SeasonProjection`, `MarketADP`.
- Produces:
  - `projections.ContaminatedProjectionError(Exception)`
  - `projections.SEASON_START: dict[int, datetime]`
  - `projections.parse(raw: list, season: int, *, allow_contaminated: bool = False) -> tuple[dict[str, SeasonProjection], dict[str, MarketADP]]`

- [ ] **Step 1: Write the failing test**

```python
# tests/ingest/test_projections.py
import pytest

from ffdo.ingest import projections, snapshot


def test_parses_current_season_projections_and_adp():
    proj, adp = projections.parse(snapshot.load("projections_2026"), 2026)
    assert len(proj) > 500
    gibbs = proj["9221"]
    assert gibbs.season == 2026
    assert gibbs.stats["rush_yd"] > 0
    assert adp["9221"].adp["half_ppr"] < 10


def test_rejects_contaminated_historical_projections():
    raw = snapshot.load("projections_2023_CONTAMINATED")
    with pytest.raises(projections.ContaminatedProjectionError):
        projections.parse(raw, 2023)


def test_contaminated_projections_readable_when_explicitly_allowed():
    raw = snapshot.load("projections_2023_CONTAMINATED")
    proj, adp = projections.parse(raw, 2023, allow_contaminated=True)
    # Nick Chubb: preseason ADP 10.6, stored "projection" 0.0 after a
    # week-2 season-ending injury. Proof the stored values are post-hoc.
    chubb = next(p for pid, p in proj.items() if adp[pid].adp.get("half_ppr", 999) < 11
                 and p.stats.get("pts_half_ppr") == 0.0)
    assert chubb.stats["pts_half_ppr"] == 0.0


def test_adp_is_preserved_even_for_contaminated_seasons():
    """ADP is fixed at draft time and is the ONLY clean historical signal."""
    _, adp = projections.parse(
        snapshot.load("projections_2023_CONTAMINATED"), 2023,
        allow_contaminated=True)
    ranked = sorted((v.adp["half_ppr"], k) for k, v in adp.items()
                    if v.adp.get("half_ppr", 999) < 999)
    assert len(ranked) > 100
    assert ranked[0][0] < 5
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/ingest/test_projections.py -v`
Expected: FAIL — `ImportError: cannot import name 'projections'`

- [ ] **Step 3: Write `src/ffdo/ingest/projections.py`**

```python
"""Projections + ADP, with a hard guard against post-season contamination.

Sleeper's projections endpoint returns the LATEST state of a projection, not
its preseason state. For completed seasons the points have been overwritten
with in-season information: Nick Chubb's 2023 row carries no pts_* key at all
despite a preseason ADP of 10.6, because he tore his knee in week 2. The
projection was wiped, not revised.

ADP is unaffected -- it is fixed at draft time -- and is therefore the only
historical market signal this project trusts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ffdo.domain.models import MarketADP, SeasonProjection

# NFL regular seasons open in the first full week of September.
SEASON_START: dict[int, datetime] = {
    2021: datetime(2021, 9, 9, tzinfo=UTC),
    2022: datetime(2022, 9, 8, tzinfo=UTC),
    2023: datetime(2023, 9, 7, tzinfo=UTC),
    2024: datetime(2024, 9, 5, tzinfo=UTC),
    2025: datetime(2025, 9, 4, tzinfo=UTC),
    2026: datetime(2026, 9, 9, tzinfo=UTC),
}

_ADP_PREFIX = "adp_"


class ContaminatedProjectionError(RuntimeError):
    """Raised when projection data postdates its own season's kickoff."""


def _last_modified(rows: list[dict[str, Any]]) -> datetime | None:
    stamps = [r["last_modified"] for r in rows if r.get("last_modified")]
    if not stamps:
        return None
    return datetime.fromtimestamp(max(stamps) / 1000, tz=UTC)


def parse(
    raw: list[dict[str, Any]],
    season: int,
    *,
    allow_contaminated: bool = False,
) -> tuple[dict[str, SeasonProjection], dict[str, MarketADP]]:
    modified = _last_modified(raw)
    kickoff = SEASON_START.get(season)
    if (not allow_contaminated and modified and kickoff and modified > kickoff):
        raise ContaminatedProjectionError(
            f"{season} projections last modified {modified.date()}, after "
            f"kickoff {kickoff.date()}; points are post-hoc. Use ADP instead, "
            f"or pass allow_contaminated=True to inspect deliberately."
        )

    proj: dict[str, SeasonProjection] = {}
    adp: dict[str, MarketADP] = {}
    for row in raw:
        player_id = row.get("player_id")
        stats = row.get("stats") or {}
        if not player_id or not stats:
            continue
        numeric = {k: float(v) for k, v in stats.items()
                   if isinstance(v, (int, float))}
        proj[player_id] = SeasonProjection(
            player_id=player_id, season=season,
            stats={k: v for k, v in numeric.items()
                   if not k.startswith(_ADP_PREFIX)},
            last_modified=modified,
        )
        adp[player_id] = MarketADP(
            player_id=player_id, season=season,
            adp={k[len(_ADP_PREFIX):]: v for k, v in numeric.items()
                 if k.startswith(_ADP_PREFIX)},
        )
    return proj, adp
```

Note: the snapshot rows may not carry a top-level `player_id`. If the test
fails on missing ids, read it from `row["player"]["player_id"]` as a fallback
and keep both paths.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/ingest/test_projections.py -v`
Expected: PASS, including the guard test.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/projections.py tests/ingest/test_projections.py
git commit -m "feat: projection adapter with contamination guard"
```

---

### Task 5: Scoring rescore and the golden test

**Files:**
- Create: `src/ffdo/engine/__init__.py`, `src/ffdo/engine/scoring.py`
- Test: `tests/engine/test_scoring.py`

**Interfaces:**
- Consumes: `is_offense_scoring_key`, `STANDARD_HALF_PPR`, `LeagueProfile`.
- Produces: `scoring.score_stats(stats: Mapping[str, float], weights: Mapping[str, float]) -> float`

- [ ] **Step 1: Write the failing test**

The golden test is the load-bearing one: if our arithmetic reproduces Sleeper's
own preset, it can be trusted on the league's custom settings.

```python
# tests/engine/test_scoring.py
from ffdo.domain.constants import STANDARD_HALF_PPR
from ffdo.ingest import league, players, snapshot, stats
from ffdo.engine import scoring

OFFENSE = {"QB", "RB", "WR", "TE"}


def test_score_stats_applies_weights():
    assert scoring.score_stats({"rec": 4, "rec_yd": 50}, {"rec": 0.5, "rec_yd": 0.1}) == 7.0


def test_score_stats_ignores_defensive_keys_for_offense():
    """A WR must not be credited a defensive fumble-recovery touchdown."""
    got = scoring.score_stats({"rec_td": 1, "fum_rec_td": 1},
                              {"rec_td": 6, "fum_rec_td": 6})
    assert got == 6.0


def test_golden_reproduces_sleeper_half_ppr_2025():
    """Our rescore must reproduce Sleeper's own 2025 pts_half_ppr.

    Verified: >=98% of players scoring 50+ points match within 0.01.
    Do NOT extend this to earlier seasons -- Sleeper changed the preset
    definition between 2021 and 2023 (2021 counted `fum` at -1, 2023+ does not).
    """
    profiles = players.parse(snapshot.load("players_nfl"))
    lines = stats.parse(snapshot.load("stats_2025"), 2025)

    total = matched = 0
    for player_id, line in lines.items():
        prof = profiles.get(player_id)
        if prof is None or prof.position not in OFFENSE:
            continue
        sleeper_pts = line.stats.get("pts_half_ppr", 0.0)
        if sleeper_pts < 50:
            continue
        total += 1
        if abs(scoring.score_stats(line.stats, STANDARD_HALF_PPR) - sleeper_pts) < 0.01:
            matched += 1

    assert total > 200
    assert matched / total >= 0.98, f"only {matched}/{total} reproduced"


def test_league_scoring_diverges_from_preset_on_fumbles():
    """This league penalises ALL fumbles (`fum: -1`) on top of `fum_lost: -2`.

    Sleeper's own board shows the preset, so fumble-prone QBs are systematically
    overvalued there. This is the concrete edge the rescore layer buys.
    """
    lg = league.parse(snapshot.load("league_history")["leagues"]["2026"])
    profiles = players.parse(snapshot.load("players_nfl"))
    lines = stats.parse(snapshot.load("stats_2025"), 2025)

    deltas = []
    for player_id, line in lines.items():
        prof = profiles.get(player_id)
        if prof is None or prof.position != "QB":
            continue
        if line.stats.get("pts_half_ppr", 0.0) < 200:
            continue
        preset = scoring.score_stats(line.stats, STANDARD_HALF_PPR)
        actual = scoring.score_stats(line.stats, lg.scoring_settings)
        deltas.append(actual - preset)

    assert deltas
    assert min(deltas) <= -8.0, "expected fumble-prone QBs to lose 8+ points"
    assert max(deltas) <= 0.0, "league scoring can only reduce QB value here"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/engine/test_scoring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.engine'`

- [ ] **Step 3: Write `src/ffdo/engine/scoring.py`**

```python
"""Recompute fantasy points from component stats.

Sleeper's precomputed pts_* fields are NOT stable across seasons -- the
half-PPR preset counted `fum` at -1 in 2021 and stopped by 2023. Component
stats are raw facts and do not drift, so everything is rescored from them.
"""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.constants import is_offense_scoring_key


def score_stats(stats: Mapping[str, float], weights: Mapping[str, float]) -> float:
    """Total offensive fantasy points for `stats` under `weights`.

    Scoring keys that only apply to defensive or kicking units are ignored,
    so a league's DEF/K rules never leak into a skill player's total.
    """
    return sum(
        float(stats.get(key, 0.0)) * float(weight)
        for key, weight in weights.items()
        if is_offense_scoring_key(key)
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/test_scoring.py -v`
Expected: PASS. If the golden test lands between 95% and 98%, inspect the
misses: they should be return specialists (`st_td`) or players with stat
corrections. Do not lower the threshold without recording the reason here.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/ tests/engine/
git commit -m "feat: scoring rescore with Sleeper golden test"
```

---

### Task 6: Replacement level

**Files:**
- Create: `src/ffdo/engine/replacement.py`
- Test: `tests/engine/test_replacement.py`

**Interfaces:**
- Consumes: `LeagueProfile`.
- Produces: `replacement.replacement_levels(points: Mapping[str, float], positions: Mapping[str, str], league: LeagueProfile) -> dict[str, float]`

`points` maps player_id → projected points; `positions` maps player_id → position.
Returns position → replacement-level points.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_replacement.py
from ffdo.domain.models import LeagueProfile
from ffdo.engine import replacement


def _league(roster_positions, num_teams=12):
    return LeagueProfile(league_id="x", season=2026, num_teams=num_teams,
                         roster_positions=tuple(roster_positions),
                         scoring_settings={}, budget=200)


def _pool():
    """40 RBs and 40 WRs on a clean descending scale, plus 20 QBs."""
    points, positions = {}, {}
    for i in range(40):
        points[f"rb{i}"] = 300.0 - i * 5
        positions[f"rb{i}"] = "RB"
        points[f"wr{i}"] = 290.0 - i * 5
        positions[f"wr{i}"] = "WR"
    for i in range(20):
        points[f"qb{i}"] = 400.0 - i * 10
        positions[f"qb{i}"] = "QB"
    return points, positions


def test_replacement_falls_as_starting_demand_rises():
    points, positions = _pool()
    shallow = replacement.replacement_levels(
        points, positions, _league(["QB", "RB", "WR", "BN"]))
    deep = replacement.replacement_levels(
        points, positions, _league(["QB", "RB", "RB", "RB", "WR", "BN"]))
    assert deep["RB"] < shallow["RB"]


def test_superflex_collapses_qb_replacement():
    points, positions = _pool()
    one_qb = replacement.replacement_levels(
        points, positions, _league(["QB", "RB", "RB", "WR", "WR", "BN"]))
    superflex = replacement.replacement_levels(
        points, positions,
        _league(["QB", "RB", "RB", "WR", "WR", "SUPER_FLEX", "BN"]))
    assert superflex["QB"] < one_qb["QB"]


def test_flex_demand_is_allocated_across_eligible_positions():
    points, positions = _pool()
    no_flex = replacement.replacement_levels(
        points, positions, _league(["RB", "WR", "BN"]))
    with_flex = replacement.replacement_levels(
        points, positions, _league(["RB", "WR", "FLEX", "BN"]))
    assert with_flex["RB"] <= no_flex["RB"]
    assert with_flex["WR"] <= no_flex["WR"]


def test_positions_absent_from_roster_get_no_level():
    points, positions = _pool()
    levels = replacement.replacement_levels(
        points, positions, _league(["QB", "RB", "WR", "BN"]))
    assert "TE" not in levels


def test_replacement_level_is_monotone_in_team_count():
    points, positions = _pool()
    small = replacement.replacement_levels(
        points, positions, _league(["QB", "RB", "WR", "BN"], num_teams=8))
    big = replacement.replacement_levels(
        points, positions, _league(["QB", "RB", "WR", "BN"], num_teams=12))
    assert big["RB"] < small["RB"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/engine/test_replacement.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/ffdo/engine/replacement.py`**

```python
"""Replacement level derived from the league's actual starting requirements.

Every starting lineup in the league is filled greedily by projected points;
replacement level at a position is the best player who did not make one. This
handles FLEX allocation and superflex with no special cases -- the only input
that changes is `roster_positions`.
"""

from __future__ import annotations

from collections.abc import Mapping

FLEX_ELIGIBILITY: dict[str, frozenset[str]] = {
    "FLEX": frozenset({"RB", "WR", "TE"}),
    "WRRB_FLEX": frozenset({"RB", "WR"}),
    "REC_FLEX": frozenset({"WR", "TE"}),
    "SUPER_FLEX": frozenset({"QB", "RB", "WR", "TE"}),
}


def replacement_levels(
    points: Mapping[str, float],
    positions: Mapping[str, str],
    league,
) -> dict[str, float]:
    slots = league.starting_slots
    ranked: dict[str, list[tuple[float, str]]] = {}
    for player_id, pts in points.items():
        pos = positions.get(player_id)
        if pos is None:
            continue
        ranked.setdefault(pos, []).append((pts, player_id))
    for pos in ranked:
        ranked[pos].sort(reverse=True)

    cursor = dict.fromkeys(ranked, 0)
    taken: set[str] = set()

    # Dedicated slots first: they have no discretion, so they must claim their
    # players before flex slots choose from what is left.
    dedicated = [s for s in slots if s not in FLEX_ELIGIBILITY]
    flex = [s for s in slots if s in FLEX_ELIGIBILITY]

    for slot in dedicated:
        for _ in range(league.num_teams):
            pool = ranked.get(slot, [])
            if cursor.get(slot, 0) < len(pool):
                taken.add(pool[cursor[slot]][1])
                cursor[slot] += 1

    for slot in flex:
        eligible = FLEX_ELIGIBILITY[slot]
        for _ in range(league.num_teams):
            best: tuple[float, str] | None = None
            for pos in eligible:
                pool = ranked.get(pos, [])
                idx = cursor.get(pos, 0)
                if idx < len(pool) and (best is None or pool[idx][0] > best[0]):
                    best = pool[idx]
            if best is None:
                break
            pos = positions[best[1]]
            taken.add(best[1])
            cursor[pos] += 1

    rostered_positions = {p for s in slots
                          for p in (FLEX_ELIGIBILITY.get(s) or {s})}

    levels: dict[str, float] = {}
    for pos in rostered_positions:
        pool = ranked.get(pos, [])
        idx = cursor.get(pos, 0)
        levels[pos] = pool[idx][0] if idx < len(pool) else (
            pool[-1][0] if pool else 0.0)
    return levels
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/test_replacement.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/replacement.py tests/engine/test_replacement.py
git commit -m "feat: league-derived replacement levels"
```

---

### Task 7: VOR and tiers

**Files:**
- Create: `src/ffdo/engine/vor.py`
- Test: `tests/engine/test_vor.py`

**Interfaces:**
- Consumes: `PlayerProfile`, `ValuedPlayer`, `replacement.replacement_levels`.
- Produces:
  - `vor.compute(points, profiles, league, *, adjustments=None) -> dict[str, ValuedPlayer]`
  - `vor.assign_tiers(valued: dict[str, ValuedPlayer], *, gap_multiple: float = 1.75) -> dict[str, ValuedPlayer]`

`adjustments` maps player_id → mapping of adjustment name → point delta, and is
recorded on `ValuedPlayer.adjustments` as an audit trail.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_vor.py
from ffdo.domain.models import LeagueProfile, PlayerProfile
from ffdo.engine import vor


def _profiles(spec):
    return {pid: PlayerProfile(player_id=pid, first_name="P", last_name=pid,
                               position=pos, team="X", age=25, years_exp=3,
                               injury_status=None, active=True)
            for pid, pos in spec.items()}


def _league(n=2):
    return LeagueProfile(league_id="x", season=2026, num_teams=n,
                         roster_positions=("RB", "WR", "BN"),
                         scoring_settings={}, budget=200)


def test_vor_is_points_above_replacement():
    points = {f"rb{i}": 200.0 - 10 * i for i in range(6)}
    profiles = _profiles({f"rb{i}": "RB" for i in range(6)})
    points.update({f"wr{i}": 150.0 - 10 * i for i in range(6)})
    profiles.update(_profiles({f"wr{i}": "WR" for i in range(6)}))

    valued = vor.compute(points, profiles, _league())
    # 2 teams x 1 RB slot => replacement RB is the 3rd best (180.0)
    assert valued["rb0"].vor == 200.0 - 180.0


def test_adjustments_are_applied_and_recorded():
    points = {"rb0": 200.0, "rb1": 190.0, "rb2": 180.0,
              "wr0": 150.0, "wr1": 140.0, "wr2": 130.0}
    profiles = _profiles({"rb0": "RB", "rb1": "RB", "rb2": "RB",
                          "wr0": "WR", "wr1": "WR", "wr2": "WR"})
    valued = vor.compute(points, profiles, _league(),
                         adjustments={"rb0": {"durability": -12.0}})
    assert valued["rb0"].adjusted_points == 188.0
    assert valued["rb0"].projected_points == 200.0
    assert valued["rb0"].adjustments["durability"] == -12.0


def test_tiers_break_on_large_gaps():
    points = {"a": 100.0, "b": 99.0, "c": 98.0,  # tier 1
              "d": 60.0, "e": 59.0}              # tier 2 after a big gap
    profiles = _profiles({k: "RB" for k in points})
    valued = vor.assign_tiers(vor.compute(points, profiles, _league(n=1)))
    assert valued["a"].tier == valued["b"].tier == valued["c"].tier
    assert valued["d"].tier == valued["e"].tier
    assert valued["d"].tier > valued["a"].tier


def test_tiers_are_assigned_within_position_not_across():
    points = {"rb0": 300.0, "rb1": 299.0, "wr0": 100.0, "wr1": 99.0}
    profiles = _profiles({"rb0": "RB", "rb1": "RB", "wr0": "WR", "wr1": "WR"})
    valued = vor.assign_tiers(vor.compute(points, profiles, _league(n=1)))
    assert valued["rb0"].tier == 1
    assert valued["wr0"].tier == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/engine/test_vor.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/ffdo/engine/vor.py`**

```python
"""Value over replacement, plus tier detection by gap clustering."""

from __future__ import annotations

from collections.abc import Mapping
from statistics import median

from ffdo.domain.models import PlayerProfile, ValuedPlayer
from ffdo.engine.replacement import replacement_levels


def compute(
    points: Mapping[str, float],
    profiles: Mapping[str, PlayerProfile],
    league,
    *,
    adjustments: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, ValuedPlayer]:
    adjustments = adjustments or {}
    adjusted = {
        pid: pts + sum(adjustments.get(pid, {}).values())
        for pid, pts in points.items()
        if pid in profiles
    }
    positions = {pid: profiles[pid].position for pid in adjusted}
    levels = replacement_levels(adjusted, positions, league)

    out: dict[str, ValuedPlayer] = {}
    for pid, adj_pts in adjusted.items():
        pos = positions[pid]
        out[pid] = ValuedPlayer(
            profile=profiles[pid],
            projected_points=points[pid],
            adjusted_points=adj_pts,
            vor=adj_pts - levels.get(pos, 0.0),
            tier=0,
            adjustments=dict(adjustments.get(pid, {})),
        )
    return out


def assign_tiers(
    valued: Mapping[str, ValuedPlayer],
    *,
    gap_multiple: float = 1.75,
) -> dict[str, ValuedPlayer]:
    """Tier breaks fall where a VOR gap exceeds `gap_multiple` x median gap."""
    by_position: dict[str, list[ValuedPlayer]] = {}
    for vp in valued.values():
        by_position.setdefault(vp.profile.position, []).append(vp)

    out: dict[str, ValuedPlayer] = {}
    for players in by_position.values():
        players.sort(key=lambda v: v.vor, reverse=True)
        gaps = [players[i].vor - players[i + 1].vor
                for i in range(len(players) - 1)]
        threshold = (median(g for g in gaps if g >= 0) * gap_multiple
                     if gaps else 0.0)
        tier = 1
        for i, vp in enumerate(players):
            if i > 0 and threshold > 0 and gaps[i - 1] > threshold:
                tier += 1
            out[vp.profile.player_id] = ValuedPlayer(
                profile=vp.profile,
                projected_points=vp.projected_points,
                adjusted_points=vp.adjusted_points,
                vor=vp.vor, tier=tier, adjustments=vp.adjustments,
            )
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/test_vor.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/vor.py tests/engine/test_vor.py
git commit -m "feat: VOR computation and tier detection"
```

---

### Task 8: Draft adapter

**Files:**
- Create: `src/ffdo/ingest/draft.py`
- Test: `tests/ingest/test_draft.py`

**Interfaces:**
- Consumes: `DraftPick`, `DraftState`.
- Produces: `draft.parse(meta: dict, picks: list[dict]) -> DraftState`

`metadata.amount` arrives as a **string** and must be parsed to `int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/ingest/test_draft.py
from ffdo.ingest import draft, snapshot


def _hist():
    return snapshot.load("league_history")["drafts"]


def test_parses_auction_amounts_from_string_metadata():
    d = _hist()["2025"]
    state = draft.parse(d["meta"], d["picks"])
    assert state.draft_type == "auction"
    assert state.budget == 200
    assert len(state.picks) == 168
    assert all(isinstance(p.amount, int) for p in state.picks)


def test_every_historical_auction_reconciles_to_the_budget():
    """12 rosters x $200 must equal total spend, in every completed season."""
    for season in ("2021", "2022", "2023", "2024", "2025"):
        d = _hist()[season]
        state = draft.parse(d["meta"], d["picks"])
        spend = state.spent_by_roster()
        assert len(spend) == 12, f"{season}: expected 12 rosters"
        assert sum(spend.values()) == 12 * 200, f"{season}: budget mismatch"


def test_pre_draft_state_has_no_picks():
    lg = snapshot.load("league_history")
    state = draft.parse(lg["drafts"]["2025"]["meta"], [])
    assert state.picks == ()
    assert state.drafted_player_ids() == frozenset()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/ingest/test_draft.py -v`
Expected: FAIL — `ImportError: cannot import name 'draft'`

- [ ] **Step 3: Write `src/ffdo/ingest/draft.py`**

```python
"""Translates /v1/draft/<id> and /picks into DraftState."""

from __future__ import annotations

from typing import Any

from ffdo.domain.models import DraftPick, DraftState


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse(meta: dict[str, Any], picks: list[dict[str, Any]]) -> DraftState:
    settings = meta.get("settings") or {}
    parsed = tuple(
        DraftPick(
            pick_no=int(p["pick_no"]),
            round=int(p["round"]),
            draft_slot=int(p["draft_slot"]),
            roster_id=_as_int(p.get("roster_id")),
            picked_by=p.get("picked_by") or None,
            player_id=str(p["player_id"]),
            # Auction amounts arrive as strings, e.g. "42".
            amount=_as_int((p.get("metadata") or {}).get("amount")),
        )
        for p in picks
    )
    return DraftState(
        draft_id=meta["draft_id"],
        draft_type=meta["type"],
        status=meta["status"],
        num_teams=int(settings.get("teams", 0)),
        rounds=int(settings.get("rounds", 0)),
        budget=settings.get("budget"),
        picks=parsed,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/ingest/test_draft.py -v`
Expected: PASS — all five historical auctions reconcile to $2400.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/ingest/draft.py tests/ingest/test_draft.py
git commit -m "feat: draft adapter with auction amount parsing"
```

---

### Task 9: Auction engine — dollars, inflation, max bid

**Files:**
- Create: `src/ffdo/engine/auction.py`
- Test: `tests/engine/test_auction.py`

**Interfaces:**
- Consumes: `ValuedPlayer`, `LeagueProfile`, `DraftState`.
- Produces:
  - `auction.baseline_prices(valued, league) -> dict[str, float]`
  - `auction.inflation_factor(baseline, state, league) -> float`
  - `auction.max_bid(spent: int, slots_filled: int, league) -> int`

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_auction.py
import pytest

from ffdo.domain.models import LeagueProfile, PlayerProfile, ValuedPlayer
from ffdo.engine import auction
from ffdo.ingest import draft, snapshot


def _league(n=12, budget=200, roster=13):
    return LeagueProfile(league_id="x", season=2026, num_teams=n,
                         roster_positions=("RB",) * roster,
                         scoring_settings={}, budget=budget)


def _valued(vors):
    out = {}
    for pid, v in vors.items():
        prof = PlayerProfile(player_id=pid, first_name="P", last_name=pid,
                             position="RB", team="X", age=25, years_exp=3,
                             injury_status=None, active=True)
        out[pid] = ValuedPlayer(profile=prof, projected_points=0.0,
                                adjusted_points=0.0, vor=v, tier=1,
                                adjustments={})
    return out


def test_prices_never_fall_below_one_dollar():
    valued = _valued({f"p{i}": 100.0 - i * 20 for i in range(10)})
    prices = auction.baseline_prices(valued, _league(n=2, roster=3))
    assert all(p >= 1.0 for p in prices.values())


def test_total_baseline_spend_matches_league_budget():
    valued = _valued({f"p{i}": max(0.0, 200.0 - i * 4) for i in range(160)})
    league = _league()
    prices = auction.baseline_prices(valued, league)
    rostered = sorted(prices.values(), reverse=True)[:league.num_teams * league.roster_size]
    assert sum(rostered) == pytest.approx(league.num_teams * league.budget, rel=0.02)


def test_negative_vor_does_not_deflate_the_scale():
    """Clamping negative VOR to zero is what keeps the dollar scale honest."""
    with_negatives = _valued({f"p{i}": 100.0 - i * 10 for i in range(30)})
    prices = auction.baseline_prices(with_negatives, _league(n=2, roster=3))
    assert prices["p0"] > prices["p5"]
    assert all(p >= 1.0 for p in prices.values())


def test_inflation_above_one_when_room_underspends():
    valued = _valued({f"p{i}": 100.0 - i for i in range(60)})
    league = _league(n=2, roster=3)
    baseline = auction.baseline_prices(valued, league)
    empty = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 2, "rounds": 3, "budget": 200}}, [])
    assert auction.inflation_factor(baseline, empty, league) == pytest.approx(1.0, rel=0.05)


def test_max_bid_reserves_a_dollar_for_every_unfilled_slot():
    league = _league()
    assert auction.max_bid(spent=0, slots_filled=0, league=league) == 200 - 12
    assert auction.max_bid(spent=150, slots_filled=12, league=league) == 50


def test_replaying_a_real_auction_keeps_inflation_sane():
    """Replay 2025 pick by pick. Inflation must stay in a plausible band."""
    hist = snapshot.load("league_history")["drafts"]["2025"]
    state = draft.parse(hist["meta"], hist["picks"])
    league = LeagueProfile(league_id="x", season=2025, num_teams=12,
                           roster_positions=("RB",) * 14,
                           scoring_settings={}, budget=200)
    valued = _valued({p.player_id: 150.0 - i * 0.8
                      for i, p in enumerate(state.picks)})
    baseline = auction.baseline_prices(valued, league)

    for cut in range(0, len(state.picks), 20):
        partial = draft.parse(hist["meta"], hist["picks"][:cut])
        factor = auction.inflation_factor(baseline, partial, league)
        assert 0.2 < factor < 5.0, f"implausible inflation {factor} at pick {cut}"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/engine/test_auction.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/ffdo/engine/auction.py`**

```python
"""Auction valuation: baseline dollars, live inflation, and max bid."""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.models import DraftState, ValuedPlayer

MIN_BID = 1


def baseline_prices(
    valued: Mapping[str, ValuedPlayer],
    league,
) -> dict[str, float]:
    """Fair prices in an efficient market, summing to the league budget.

    Negative VOR is clamped to zero before summing; without that, deep bench
    players deflate the dollar-per-VOR scale for everyone.
    """
    total_slots = league.num_teams * league.roster_size
    discretionary = league.num_teams * league.budget - total_slots * MIN_BID

    surplus = sorted(
        ((pid, max(0.0, vp.vor)) for pid, vp in valued.items()),
        key=lambda kv: kv[1], reverse=True,
    )[:total_slots]
    total_vor = sum(v for _, v in surplus)
    per_vor = discretionary / total_vor if total_vor > 0 else 0.0

    return {pid: MIN_BID + max(0.0, vp.vor) * per_vor
            for pid, vp in valued.items()}


def inflation_factor(
    baseline: Mapping[str, float],
    state: DraftState,
    league,
) -> float:
    """Remaining money divided by remaining value.

    Above 1.0 means the room has underspent and everything left will cost more
    than fair. Below 1.0 means bargains are available.
    """
    total_budget = league.num_teams * league.budget
    spent = sum(state.spent_by_roster().values())
    drafted = state.drafted_player_ids()

    remaining_money = total_budget - spent
    total_slots = league.num_teams * league.roster_size
    slots_left = max(1, total_slots - len(drafted))

    remaining = sorted(
        (price for pid, price in baseline.items() if pid not in drafted),
        reverse=True,
    )[:slots_left]
    remaining_value = sum(remaining)
    if remaining_value <= 0:
        return 1.0
    return remaining_money / remaining_value


def max_bid(spent: int, slots_filled: int, league) -> int:
    """The most you can bid and still fill every remaining roster slot."""
    remaining_budget = league.budget - spent
    slots_left = league.roster_size - slots_filled
    if slots_left <= 0:
        return 0
    return max(0, remaining_budget - (slots_left - 1) * MIN_BID)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/test_auction.py -v`
Expected: PASS, including the 2025 replay.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/auction.py tests/engine/test_auction.py
git commit -m "feat: auction dollars, live inflation, and max bid"
```

---

### Task 10: API and auction board

This is the hard deadline deliverable. It must be usable on 2026-08-25.

**Files:**
- Create: `src/ffdo/api/__init__.py`, `src/ffdo/api/app.py`, `src/ffdo/api/board.py`
- Create: `src/ffdo/web/index.html`, `src/ffdo/web/board.css`, `src/ffdo/web/board.js`
- Test: `tests/api/test_board.py`

**Interfaces:**
- Consumes: every adapter and engine module above.
- Produces:
  - `board.build_auction_board(league, state, valued, baseline) -> dict` returning
    `{"format": "auction", "inflation": float, "budget": {...}, "players": [...]}`
    where each player row is
    `{"player_id", "name", "position", "team", "age", "vor", "tier", "baseline", "adjusted", "drafted"}`.
  - FastAPI routes `GET /api/board` and `GET /healthz`; static mount at `/`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_board.py
from ffdo.api import board
from ffdo.domain.models import LeagueProfile, PlayerProfile, ValuedPlayer
from ffdo.ingest import draft, snapshot


def _league():
    return LeagueProfile(league_id="x", season=2025, num_teams=12,
                         roster_positions=("RB",) * 14,
                         scoring_settings={}, budget=200)


def _valued(ids):
    out = {}
    for i, pid in enumerate(ids):
        prof = PlayerProfile(player_id=pid, first_name="P", last_name=str(i),
                             position="RB", team="X", age=25, years_exp=3,
                             injury_status=None, active=True)
        out[pid] = ValuedPlayer(profile=prof, projected_points=100.0,
                                adjusted_points=100.0, vor=100.0 - i,
                                tier=1, adjustments={})
    return out


def test_board_marks_drafted_players_and_reports_inflation():
    hist = snapshot.load("league_history")["drafts"]["2025"]
    state = draft.parse(hist["meta"], hist["picks"][:40])
    ids = [p.player_id for p in draft.parse(hist["meta"], hist["picks"]).picks]
    valued = _valued(ids)
    baseline = {pid: 20.0 for pid in ids}

    out = board.build_auction_board(_league(), state, valued, baseline)
    assert out["format"] == "auction"
    assert isinstance(out["inflation"], float)
    drafted = {r["player_id"] for r in out["players"] if r["drafted"]}
    assert drafted == state.drafted_player_ids()


def test_board_rows_are_sorted_by_value_descending():
    ids = [f"p{i}" for i in range(10)]
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 14, "budget": 200}}, [])
    out = board.build_auction_board(_league(), state, _valued(ids),
                                    {pid: 10.0 for pid in ids})
    vors = [r["vor"] for r in out["players"]]
    assert vors == sorted(vors, reverse=True)


def test_healthz_returns_ok():
    from fastapi.testclient import TestClient
    from ffdo.api.app import create_app
    client = TestClient(create_app())
    assert client.get("/healthz").json() == {"status": "ok"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_board.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.api'`

- [ ] **Step 3: Write `src/ffdo/api/board.py`**

```python
"""Shapes engine output into the JSON the board renders."""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.models import DraftState, ValuedPlayer
from ffdo.engine import auction


def build_auction_board(
    league,
    state: DraftState,
    valued: Mapping[str, ValuedPlayer],
    baseline: Mapping[str, float],
) -> dict:
    factor = auction.inflation_factor(baseline, state, league)
    drafted = state.drafted_player_ids()
    spent = state.spent_by_roster()

    rows = []
    for pid, vp in valued.items():
        base = baseline.get(pid, 1.0)
        rows.append({
            "player_id": pid,
            "name": vp.profile.full_name,
            "position": vp.profile.position,
            "team": vp.profile.team,
            "age": vp.profile.age,
            "vor": round(vp.vor, 1),
            "tier": vp.tier,
            "baseline": round(base, 1),
            "adjusted": round(base * factor, 1),
            "drafted": pid in drafted,
        })
    rows.sort(key=lambda r: r["vor"], reverse=True)

    return {
        "format": "auction",
        "inflation": round(factor, 3),
        "budget": {
            "total": league.num_teams * league.budget,
            "spent": sum(spent.values()),
            "by_roster": spent,
        },
        "picks_made": len(state.picks),
        "players": rows,
    }
```

- [ ] **Step 4: Write `src/ffdo/api/app.py`**

```python
"""FastAPI app. Serves board state and the static board."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app() -> FastAPI:
    app = FastAPI(title="ffdo")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
```

- [ ] **Step 5: Wire the live board endpoint**

Add to `create_app()`, before the static mount. The league and draft ids come
from the Global Constraints.

```python
    from ffdo.api import board as board_mod
    from ffdo.engine import auction, scoring, vor
    from ffdo.ingest import client as client_mod
    from ffdo.ingest import draft as draft_mod
    from ffdo.ingest import league as league_mod
    from ffdo.ingest import players as players_mod
    from ffdo.ingest import projections as proj_mod

    LEAGUE_ID = "1315881559957458944"
    DRAFT_ID = "1315881559965835264"

    @app.get("/api/board")
    def get_board() -> dict:
        sleeper = client_mod.SleeperClient()
        try:
            lg = league_mod.parse(
                sleeper.get_json(f"{client_mod.V1}/league/{LEAGUE_ID}"))
            profiles = players_mod.parse(
                sleeper.get_json(f"{client_mod.V1}/players/nfl"))
            proj, _adp = proj_mod.parse(
                sleeper.get_json(
                    f"{client_mod.PROJECTIONS}/{lg.season}"
                    "?season_type=regular&position[]=QB&position[]=RB"
                    "&position[]=WR&position[]=TE"),
                lg.season)
            state = draft_mod.parse(
                sleeper.get_json(f"{client_mod.V1}/draft/{DRAFT_ID}"),
                sleeper.get_json(f"{client_mod.V1}/draft/{DRAFT_ID}/picks"))
        finally:
            sleeper.close()

        points = {pid: scoring.score_stats(p.stats, lg.scoring_settings)
                  for pid, p in proj.items() if pid in profiles}
        valued = vor.assign_tiers(vor.compute(points, profiles, lg))
        baseline = auction.baseline_prices(valued, lg)
        return board_mod.build_auction_board(lg, state, valued, baseline)
```

Caching is deliberately omitted here — one poll fetches 14 MB of player data.
Add an in-process TTL cache (players 24h, projections 1h, draft 3s) before draft
day; a bare `functools.lru_cache` keyed on a time bucket is sufficient.

- [ ] **Step 6: Write `src/ffdo/web/index.html`**

```html
<!doctype html>
<meta charset="utf-8">
<title>Draft Board</title>
<link rel="stylesheet" href="/board.css">
<header id="strip">
  <div><span class="label">Inflation</span><b id="inflation">—</b></div>
  <div><span class="label">Spent</span><b id="spent">—</b></div>
  <div><span class="label">Picks</span><b id="picks">—</b></div>
  <div><span class="label">Updated</span><b id="updated">—</b></div>
</header>
<nav id="filters">
  <button data-pos="ALL" class="on">All</button>
  <button data-pos="QB">QB</button>
  <button data-pos="RB">RB</button>
  <button data-pos="WR">WR</button>
  <button data-pos="TE">TE</button>
  <label><input type="checkbox" id="hide-drafted" checked> Hide drafted</label>
</nav>
<table id="board">
  <thead><tr>
    <th>Player</th><th>Pos</th><th>Tm</th><th>Age</th>
    <th>Tier</th><th>VOR</th><th>Base $</th><th>Adj $</th>
  </tr></thead>
  <tbody></tbody>
</table>
<script src="/board.js"></script>
```

- [ ] **Step 7: Write `src/ffdo/web/board.css`**

```css
:root { color-scheme: light dark; --edge: #8884; }
body { font: 14px/1.4 system-ui, sans-serif; margin: 0; padding: 1rem; }
#strip { display: flex; gap: 2rem; padding: .75rem 0; border-bottom: 2px solid var(--edge); }
#strip .label { display: block; font-size: .7rem; text-transform: uppercase; opacity: .6; }
#strip b { font-size: 1.5rem; font-variant-numeric: tabular-nums; }
#filters { display: flex; gap: .5rem; align-items: center; padding: .75rem 0; }
#filters button { padding: .3rem .8rem; cursor: pointer; border: 1px solid var(--edge);
  background: transparent; color: inherit; border-radius: 4px; }
#filters button.on { background: currentColor; filter: invert(1); }
table { border-collapse: collapse; width: 100%; }
th, td { padding: .35rem .6rem; text-align: right; border-bottom: 1px solid var(--edge); }
th:first-child, td:first-child { text-align: left; }
td { font-variant-numeric: tabular-nums; }
tr.drafted { opacity: .3; text-decoration: line-through; }
tr.tier-break td { border-top: 2px solid currentColor; }
```

- [ ] **Step 8: Write `src/ffdo/web/board.js`**

```js
let state = { pos: "ALL", hideDrafted: true, data: null };

async function refresh() {
  try {
    const res = await fetch("/api/board");
    state.data = await res.json();
    render();
  } catch (err) {
    document.getElementById("updated").textContent = "error";
  }
}

function render() {
  const d = state.data;
  if (!d) return;
  document.getElementById("inflation").textContent = d.inflation.toFixed(2);
  document.getElementById("spent").textContent = `$${d.budget.spent}/${d.budget.total}`;
  document.getElementById("picks").textContent = d.picks_made;
  document.getElementById("updated").textContent = new Date().toLocaleTimeString();

  const rows = d.players.filter(p =>
    (state.pos === "ALL" || p.position === state.pos) &&
    !(state.hideDrafted && p.drafted));

  let lastTier = null;
  document.querySelector("#board tbody").innerHTML = rows.map(p => {
    const brk = p.tier !== lastTier && lastTier !== null ? " tier-break" : "";
    lastTier = p.tier;
    return `<tr class="${p.drafted ? "drafted" : ""}${brk}">
      <td>${p.name}</td><td>${p.position}</td><td>${p.team ?? ""}</td>
      <td>${p.age ?? ""}</td><td>${p.tier}</td><td>${p.vor}</td>
      <td>$${p.baseline}</td><td><b>$${p.adjusted}</b></td></tr>`;
  }).join("");
}

document.querySelectorAll("#filters button").forEach(b =>
  b.addEventListener("click", () => {
    document.querySelectorAll("#filters button").forEach(x => x.classList.remove("on"));
    b.classList.add("on");
    state.pos = b.dataset.pos;
    render();
  }));
document.getElementById("hide-drafted").addEventListener("change", e => {
  state.hideDrafted = e.target.checked;
  render();
});

refresh();
setInterval(refresh, 3000);
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_board.py -v`
Expected: PASS.

- [ ] **Step 10: Smoke-test the live server**

```bash
uv run uvicorn ffdo.api.app:app --port 8000
```

Open `http://localhost:8000`. Confirm the table populates, position filters
work, and the inflation figure renders. The 2026 draft is `pre_draft`, so
expect `inflation` near 1.0 and zero picks.

- [ ] **Step 11: Commit**

```bash
git add src/ffdo/api/ src/ffdo/web/ tests/api/
git commit -m "feat: FastAPI board endpoint and auction draft board"
```

---

### Task 11: Snake market model — survival simulation and cost of waiting

**Files:**
- Create: `src/ffdo/engine/market.py`
- Test: `tests/engine/test_market.py`

**Interfaces:**
- Consumes: `ValuedPlayer`, `MarketADP`.
- Produces:
  - `market.simulate_survival(adp, available, picks_until, *, sims=2000, tau=8.0, rng=None) -> dict[str, float]`
  - `market.cost_of_waiting(valued, survival, available) -> dict[str, dict[str, float]]`
    returning per position `{"best_now", "expected_next", "cost"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_market.py
import numpy as np
import pytest

from ffdo.domain.models import PlayerProfile, ValuedPlayer
from ffdo.engine import market


def _valued(spec):
    out = {}
    for pid, (pos, v) in spec.items():
        prof = PlayerProfile(player_id=pid, first_name="P", last_name=pid,
                             position=pos, team="X", age=25, years_exp=3,
                             injury_status=None, active=True)
        out[pid] = ValuedPlayer(profile=prof, projected_points=v,
                                adjusted_points=v, vor=v, tier=1,
                                adjustments={})
    return out


def test_survival_probabilities_are_bounded():
    adp = {f"p{i}": float(i + 1) for i in range(40)}
    surv = market.simulate_survival(adp, set(adp), picks_until=10,
                                    sims=500, rng=np.random.default_rng(0))
    assert all(0.0 <= v <= 1.0 for v in surv.values())


def test_early_adp_players_are_less_likely_to_survive():
    adp = {f"p{i}": float(i + 1) for i in range(40)}
    surv = market.simulate_survival(adp, set(adp), picks_until=12,
                                    sims=2000, rng=np.random.default_rng(0))
    assert surv["p0"] < surv["p30"]


def test_exactly_one_player_is_taken_per_pick():
    """The whole reason for simulating rather than using independent Gaussians."""
    adp = {f"p{i}": float(i + 1) for i in range(40)}
    surv = market.simulate_survival(adp, set(adp), picks_until=10,
                                    sims=1000, rng=np.random.default_rng(1))
    expected_gone = sum(1 - v for v in surv.values())
    assert expected_gone == pytest.approx(10.0, abs=0.001)


def test_longer_waits_reduce_survival():
    adp = {f"p{i}": float(i + 1) for i in range(40)}
    rng = np.random.default_rng(3)
    short = market.simulate_survival(adp, set(adp), picks_until=5, sims=2000, rng=rng)
    long = market.simulate_survival(adp, set(adp), picks_until=20, sims=2000,
                                    rng=np.random.default_rng(3))
    assert long["p10"] < short["p10"]


def test_cost_of_waiting_is_higher_for_a_thin_position():
    """One elite WR and a cliff behind him; RB is deep and flat."""
    valued = _valued({
        "wr_elite": ("WR", 90.0),
        **{f"wr{i}": ("WR", 20.0) for i in range(10)},
        **{f"rb{i}": ("RB", 60.0 - i) for i in range(10)},
    })
    survival = {"wr_elite": 0.05, **{f"wr{i}": 0.9 for i in range(10)},
                **{f"rb{i}": 0.9 for i in range(10)}}
    cow = market.cost_of_waiting(valued, survival, set(valued))
    assert cow["WR"]["cost"] > cow["RB"]["cost"]
    assert cow["WR"]["best_now"] == 90.0


def test_cost_of_waiting_ignores_drafted_players():
    valued = _valued({"a": ("RB", 100.0), "b": ("RB", 50.0)})
    cow = market.cost_of_waiting(valued, {"b": 0.5}, available={"b"})
    assert cow["RB"]["best_now"] == 50.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/engine/test_market.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/ffdo/engine/market.py`**

```python
"""When will he be gone -- and what does waiting cost?

Survival is simulated rather than solved in closed form. Independent Gaussians
would let two players occupy the same pick, could not condition on who has
already gone, and would be blind to positional runs, which is exactly the
phenomenon this tool exists to surface.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import numpy as np

from ffdo.domain.models import ValuedPlayer


def simulate_survival(
    adp: Mapping[str, float],
    available: Iterable[str],
    picks_until: int,
    *,
    sims: int = 2000,
    tau: float = 8.0,
    rng: np.random.Generator | None = None,
) -> dict[str, float]:
    """P(each available player is still there in `picks_until` picks).

    Each simulated pick draws from the remaining pool via Gumbel-max sampling
    (equivalent to Plackett-Luce), so exactly one player leaves per pick.
    """
    rng = rng or np.random.default_rng()
    ids = [pid for pid in available if pid in adp]
    if not ids or picks_until <= 0:
        return dict.fromkeys(ids, 1.0)

    values = np.array([adp[pid] for pid in ids], dtype=float)
    # Lower ADP => more desirable => higher log-weight.
    logits = -values / tau
    n = len(ids)
    take = min(picks_until, n)

    survived = np.zeros(n, dtype=np.int64)
    for _ in range(sims):
        gumbel = rng.gumbel(size=n)
        # Top-k by perturbed logit is an exact sample without replacement.
        gone = np.argpartition(-(logits + gumbel), take - 1)[:take]
        mask = np.ones(n, dtype=bool)
        mask[gone] = False
        survived += mask

    return {pid: float(survived[i]) / sims for i, pid in enumerate(ids)}


def cost_of_waiting(
    valued: Mapping[str, ValuedPlayer],
    survival: Mapping[str, float],
    available: Iterable[str],
) -> dict[str, dict[str, float]]:
    """Per position: best VOR now, expected best VOR at the next pick, and the gap.

    Expected best is computed over the ordered pool: a player is the best
    survivor exactly when he survives and everyone above him does not.
    """
    pool = set(available)
    by_position: dict[str, list[ValuedPlayer]] = {}
    for pid, vp in valued.items():
        if pid in pool:
            by_position.setdefault(vp.profile.position, []).append(vp)

    out: dict[str, dict[str, float]] = {}
    for position, players in by_position.items():
        players.sort(key=lambda v: v.vor, reverse=True)
        best_now = players[0].vor

        expected = 0.0
        none_better = 1.0
        for vp in players:
            p = survival.get(vp.profile.player_id, 0.0)
            expected += vp.vor * p * none_better
            none_better *= 1.0 - p

        out[position] = {
            "best_now": round(best_now, 2),
            "expected_next": round(expected, 2),
            "cost": round(best_now - expected, 2),
        }
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/test_market.py -v`
Expected: PASS.

- [ ] **Step 5: Check the simulation is fast enough for a 3-second refresh**

```bash
uv run python -c "
import time, numpy as np
from ffdo.engine import market
adp = {f'p{i}': float(i+1) for i in range(400)}
t = time.perf_counter()
market.simulate_survival(adp, set(adp), picks_until=24, sims=2000,
                         rng=np.random.default_rng(0))
print(f'{time.perf_counter()-t:.3f}s')
"
```

Expected: well under 1 second. If it exceeds 1s, lower `sims` to 1000 and
record the change here.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/engine/market.py tests/engine/test_market.py
git commit -m "feat: draft survival simulation and cost of waiting"
```

---

### Task 12: Snake board

**Files:**
- Modify: `src/ffdo/api/board.py` (add `build_snake_board`)
- Modify: `src/ffdo/api/app.py` (route dispatches on `state.draft_type`)
- Modify: `src/ffdo/web/index.html`, `board.js`, `board.css` (cost-of-waiting panel)
- Test: `tests/api/test_snake_board.py`

**Interfaces:**
- Produces: `board.build_snake_board(league, state, valued, survival, cow) -> dict`
  returning `{"format": "snake", "cost_of_waiting": {...}, "players": [...]}`
  where each row adds `"survival"` (float 0–1) to the auction row shape minus
  the dollar fields.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_snake_board.py
from ffdo.api import board
from ffdo.domain.models import LeagueProfile, PlayerProfile, ValuedPlayer
from ffdo.ingest import draft


def _league():
    return LeagueProfile(league_id="x", season=2026, num_teams=12,
                         roster_positions=("QB", "RB", "RB", "WR", "WR", "BN"),
                         scoring_settings={}, budget=None)


def _valued():
    out = {}
    for i in range(6):
        for pos in ("RB", "WR"):
            pid = f"{pos}{i}"
            prof = PlayerProfile(player_id=pid, first_name=pos, last_name=str(i),
                                 position=pos, team="X", age=25, years_exp=3,
                                 injury_status=None, active=True)
            out[pid] = ValuedPlayer(profile=prof, projected_points=100.0 - i * 10,
                                    adjusted_points=100.0 - i * 10,
                                    vor=100.0 - i * 10, tier=1, adjustments={})
    return out


def _state():
    return draft.parse({"draft_id": "d", "type": "snake", "status": "drafting",
                        "settings": {"teams": 12, "rounds": 6}}, [])


def test_snake_board_exposes_cost_of_waiting_and_survival():
    valued = _valued()
    survival = {pid: 0.5 for pid in valued}
    cow = {"RB": {"best_now": 100.0, "expected_next": 80.0, "cost": 20.0},
           "WR": {"best_now": 100.0, "expected_next": 95.0, "cost": 5.0}}
    out = board.build_snake_board(_league(), _state(), valued, survival, cow)
    assert out["format"] == "snake"
    assert out["cost_of_waiting"]["RB"]["cost"] == 20.0
    assert all("survival" in r for r in out["players"])


def test_snake_board_has_no_dollar_fields():
    out = board.build_snake_board(_league(), _state(), _valued(),
                                  {pid: 0.5 for pid in _valued()}, {})
    assert "baseline" not in out["players"][0]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_snake_board.py -v`
Expected: FAIL — `AttributeError: module 'ffdo.api.board' has no attribute 'build_snake_board'`

- [ ] **Step 3: Add `build_snake_board` to `src/ffdo/api/board.py`**

```python
def build_snake_board(
    league,
    state: DraftState,
    valued: Mapping[str, ValuedPlayer],
    survival: Mapping[str, float],
    cost_of_waiting: Mapping[str, Mapping[str, float]],
) -> dict:
    drafted = state.drafted_player_ids()
    rows = [
        {
            "player_id": pid,
            "name": vp.profile.full_name,
            "position": vp.profile.position,
            "team": vp.profile.team,
            "age": vp.profile.age,
            "vor": round(vp.vor, 1),
            "tier": vp.tier,
            "survival": round(survival.get(pid, 0.0), 3),
            "drafted": pid in drafted,
        }
        for pid, vp in valued.items()
    ]
    rows.sort(key=lambda r: r["vor"], reverse=True)
    return {
        "format": "snake",
        "cost_of_waiting": dict(cost_of_waiting),
        "picks_made": len(state.picks),
        "players": rows,
    }
```

- [ ] **Step 4: Dispatch on draft type in `app.py`**

In `get_board()`, replace the final two lines with:

```python
        if state.draft_type == "auction":
            baseline = auction.baseline_prices(valued, lg)
            return board_mod.build_auction_board(lg, state, valued, baseline)

        from ffdo.engine import market
        available = {pid for pid in valued if pid not in state.drafted_player_ids()}
        adp_means = {pid: a.adp["half_ppr"] for pid, a in _adp.items()
                     if a.adp.get("half_ppr", 999) < 999}
        picks_until = lg.num_teams  # conservative: one full round
        survival = market.simulate_survival(adp_means, available, picks_until)
        cow = market.cost_of_waiting(valued, survival, available)
        return board_mod.build_snake_board(lg, state, valued, survival, cow)
```

Note `_adp` must no longer be discarded — rename the `proj_mod.parse` unpack
target from `_adp` to `adp_data` and use it here.

- [ ] **Step 5: Add the cost-of-waiting panel to the web board**

In `index.html`, insert after `</header>`:

```html
<section id="cow" hidden>
  <h2>Cost of waiting</h2>
  <table><thead><tr><th>Pos</th><th>Best now</th><th>Expected next</th><th>Cost</th></tr></thead>
  <tbody></tbody></table>
</section>
```

In `board.js`, add to `render()` before the main table render:

```js
  const cowEl = document.getElementById("cow");
  if (d.format === "snake" && d.cost_of_waiting) {
    cowEl.hidden = false;
    const entries = Object.entries(d.cost_of_waiting)
      .sort((a, b) => b[1].cost - a[1].cost);
    cowEl.querySelector("tbody").innerHTML = entries.map(([pos, c]) =>
      `<tr><td>${pos}</td><td>${c.best_now}</td><td>${c.expected_next}</td>
       <td><b>${c.cost}</b></td></tr>`).join("");
  } else {
    cowEl.hidden = true;
  }
```

Also guard the auction-only strip fields, which are absent on a snake board:

```js
  document.getElementById("inflation").textContent =
    d.inflation !== undefined ? d.inflation.toFixed(2) : "—";
  document.getElementById("spent").textContent =
    d.budget ? `$${d.budget.spent}/${d.budget.total}` : "—";
```

And extend the row template to show survival on snake boards:

```js
    const money = p.baseline !== undefined
      ? `<td>$${p.baseline}</td><td><b>$${p.adjusted}</b></td>`
      : `<td colspan="2">${Math.round((p.survival ?? 0) * 100)}% survives</td>`;
```

Replace the two `<td>$...</td>` cells in the template with `${money}`.

In `board.css`, add:

```css
#cow { padding: .75rem 0; border-bottom: 2px solid var(--edge); }
#cow h2 { font-size: .8rem; text-transform: uppercase; opacity: .6; margin: 0 0 .4rem; }
#cow table { width: auto; min-width: 24rem; }
```

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest -v`
Expected: PASS, all tasks' tests.

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/ tests/
git commit -m "feat: snake board with cost-of-waiting panel"
```

---

### Task 13: Age and durability adjustments (default-off)

Both estimators ship with weight `0.0`. Task 14 decides whether either earns a
non-zero weight. Shipping them on by default would put an unvalidated number in
front of a live draft, which the spec forbids.

**Files:**
- Create: `src/ffdo/engine/adjustments.py`
- Test: `tests/engine/test_adjustments.py`

**Interfaces:**
- Consumes: `SeasonStatLine`, `PlayerProfile`, `SEASON_LENGTH`.
- Produces:
  - `adjustments.AGE_WEIGHT: float = 0.0`, `adjustments.DURABILITY_WEIGHT: float = 0.0`
  - `adjustments.expected_games_missed(history, position, *, prior_rate=None) -> float`
  - `adjustments.fit_age_curve(history_by_player, profiles) -> dict[str, dict[int, float]]`
  - `adjustments.build(profiles, history, points, replacement_ppg, *, age_weight=AGE_WEIGHT, durability_weight=DURABILITY_WEIGHT) -> dict[str, dict[str, float]]`
    shaped for `vor.compute(adjustments=...)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_adjustments.py
from ffdo.domain.models import PlayerProfile, SeasonStatLine
from ffdo.engine import adjustments


def _line(season, gp, **stats):
    from ffdo.domain.constants import SEASON_LENGTH
    return SeasonStatLine(player_id="p", season=season, games_played=gp,
                          season_length=SEASON_LENGTH[season], stats=stats)


def _profile(pid="p", pos="RB", age=26):
    return PlayerProfile(player_id=pid, first_name="A", last_name="B",
                         position=pos, team="X", age=age, years_exp=4,
                         injury_status=None, active=True)


def test_weights_default_to_zero():
    """Unvalidated adjustments must not reach a live board."""
    assert adjustments.AGE_WEIGHT == 0.0
    assert adjustments.DURABILITY_WEIGHT == 0.0


def test_durable_player_has_lower_expected_games_missed():
    durable = [_line(2023, 17), _line(2024, 18), _line(2025, 18)]
    fragile = [_line(2023, 9), _line(2024, 11), _line(2025, 10)]
    assert (adjustments.expected_games_missed(durable, "RB")
            < adjustments.expected_games_missed(fragile, "RB"))


def test_estimate_shrinks_toward_prior_with_thin_history():
    """One bad season must not brand a player fragile forever."""
    one_bad = [_line(2025, 4)]
    many_bad = [_line(2023, 4), _line(2024, 5), _line(2025, 4)]
    assert (adjustments.expected_games_missed(one_bad, "RB")
            < adjustments.expected_games_missed(many_bad, "RB"))


def test_no_history_returns_the_positional_prior():
    got = adjustments.expected_games_missed([], "RB", prior_rate=0.15)
    assert got == 0.15 * 18


def test_availability_cost_uses_the_gap_to_replacement_not_raw_points():
    """A missed game costs the gap to a streamer, not the player's full output."""
    profiles = {"p": _profile()}
    history = {"p": [_line(2023, 9), _line(2024, 9), _line(2025, 9)]}
    built = adjustments.build(profiles, history, points={"p": 180.0},
                              replacement_ppg={"RB": 8.0},
                              durability_weight=1.0)
    cost = built["p"]["durability"]
    assert cost < 0
    # Player is 10 ppg; replacement is 8. Cost per missed game is 2, not 10.
    assert abs(cost) < 180.0 * 0.5


def test_build_returns_empty_adjustments_when_weights_are_zero():
    profiles = {"p": _profile()}
    history = {"p": [_line(2025, 4)]}
    built = adjustments.build(profiles, history, points={"p": 180.0},
                              replacement_ppg={"RB": 8.0})
    assert built["p"] == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/engine/test_adjustments.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/ffdo/engine/adjustments.py`**

```python
"""Age and durability adjustments. Both default OFF until backtested.

Sleeper projects every player at a full healthy season, so availability is the
input its board structurally ignores. Age is priced by the market already, so
it only pays if the market UNDER-discounts it -- which Task 14 decides.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ffdo.domain.constants import SEASON_LENGTH
from ffdo.domain.models import PlayerProfile, SeasonStatLine

# Promoted above zero only on out-of-sample improvement (Task 14).
AGE_WEIGHT: float = 0.0
DURABILITY_WEIGHT: float = 0.0

# Beta-Binomial prior strength, in pseudo-seasons. A player with one season of
# history stays close to the positional prior; five seasons dominate it.
_PRIOR_STRENGTH = 2.0

_DEFAULT_MISS_RATE: dict[str, float] = {
    "QB": 0.10, "RB": 0.18, "WR": 0.14, "TE": 0.15,
}


def expected_games_missed(
    history: Sequence[SeasonStatLine],
    position: str,
    *,
    prior_rate: float | None = None,
    current_season: int = 2026,
) -> float:
    """Expected games missed next season, shrunk toward a positional prior.

    Recent seasons carry more weight than old ones.
    """
    rate = prior_rate if prior_rate is not None else _DEFAULT_MISS_RATE.get(position, 0.15)
    length = SEASON_LENGTH[current_season]
    if not history:
        return rate * length

    missed = played = 0.0
    for line in history:
        # Halve the weight for each season further back.
        recency = 0.5 ** (current_season - line.season - 1)
        missed += recency * line.games_missed
        played += recency * line.games_played

    observed = missed + played
    prior_games = _PRIOR_STRENGTH * length
    blended = (missed + rate * prior_games) / (observed + prior_games)
    return blended * length


def fit_age_curve(
    history_by_player: Mapping[str, Sequence[SeasonStatLine]],
    profiles: Mapping[str, PlayerProfile],
    *,
    points_key: str = "pts_half_ppr",
) -> dict[str, dict[int, float]]:
    """Delta-method aging curves: mean change in points-per-game from age a to a+1.

    Cross-sectional averages are badly survivorship-biased -- declining players
    leave the league -- so consecutive-season deltas are used instead.
    """
    deltas: dict[str, dict[int, list[float]]] = {}
    for player_id, lines in history_by_player.items():
        prof = profiles.get(player_id)
        if prof is None or prof.age is None:
            continue
        ordered = sorted(lines, key=lambda s: s.season)
        for prev, curr in zip(ordered, ordered[1:], strict=False):
            if prev.games_played < 4 or curr.games_played < 4:
                continue
            prev_ppg = prev.stats.get(points_key, 0.0) / prev.games_played
            curr_ppg = curr.stats.get(points_key, 0.0) / curr.games_played
            age_then = prof.age - (2026 - prev.season)
            deltas.setdefault(prof.position, {}).setdefault(age_then, []).append(
                curr_ppg - prev_ppg)

    return {
        position: {age: sum(vals) / len(vals) for age, vals in by_age.items() if vals}
        for position, by_age in deltas.items()
    }


def build(
    profiles: Mapping[str, PlayerProfile],
    history: Mapping[str, Sequence[SeasonStatLine]],
    points: Mapping[str, float],
    replacement_ppg: Mapping[str, float],
    *,
    age_weight: float = AGE_WEIGHT,
    durability_weight: float = DURABILITY_WEIGHT,
    age_curve: Mapping[str, Mapping[int, float]] | None = None,
    current_season: int = 2026,
) -> dict[str, dict[str, float]]:
    """Per-player point deltas, keyed by adjustment name, for the audit trail."""
    length = SEASON_LENGTH[current_season]
    out: dict[str, dict[str, float]] = {}

    for player_id, prof in profiles.items():
        entry: dict[str, float] = {}
        projected = points.get(player_id)

        if durability_weight and projected:
            missed = expected_games_missed(
                history.get(player_id, ()), prof.position,
                current_season=current_season)
            player_ppg = projected / length
            gap = player_ppg - replacement_ppg.get(prof.position, 0.0)
            # Waivers exist: a missed game costs the gap to a streamer.
            entry["durability"] = -durability_weight * max(0.0, gap) * missed

        if age_weight and age_curve and prof.age is not None:
            delta_ppg = (age_curve.get(prof.position) or {}).get(prof.age)
            if delta_ppg:
                entry["age"] = age_weight * delta_ppg * length

        out[player_id] = entry
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/test_adjustments.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/adjustments.py tests/engine/test_adjustments.py
git commit -m "feat: age and durability estimators, default-off"
```

---

### Task 14: Backtest harness

The gate that decides whether Task 13's weights ship non-zero. The baseline is
**historical ADP**, never historical projections.

**Files:**
- Create: `src/ffdo/backtest/__init__.py`, `src/ffdo/backtest/harness.py`
- Test: `tests/backtest/test_harness.py`

**Interfaces:**
- Consumes: snapshot fixtures, `adjustments`, `scoring`.
- Produces:
  - `harness.spearman(a: Sequence[float], b: Sequence[float]) -> float`
  - `harness.evaluate_season(season: int, *, age_weight: float, durability_weight: float) -> dict`
    returning `{"n", "baseline_rho", "model_rho", "improvement"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/backtest/test_harness.py
import pytest

from ffdo.backtest import harness


def test_spearman_detects_perfect_and_inverse_rank_agreement():
    assert harness.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert harness.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


@pytest.mark.parametrize("season", [2023, 2024, 2025])
def test_adp_baseline_reproduces_the_known_correlation(season):
    """ADP is the clean preseason signal; it scores rho ~0.65 every year.

    A materially different number means the pipeline changed, not the NFL.
    """
    result = harness.evaluate_season(season, age_weight=0.0, durability_weight=0.0)
    assert result["n"] > 150
    assert 0.55 <= result["baseline_rho"] <= 0.75


def test_zero_weights_leave_the_baseline_untouched():
    result = harness.evaluate_season(2025, age_weight=0.0, durability_weight=0.0)
    assert result["improvement"] == pytest.approx(0.0, abs=1e-9)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/backtest/test_harness.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.backtest'`

- [ ] **Step 3: Write `src/ffdo/backtest/harness.py`**

```python
"""Out-of-sample validation against the ADP baseline.

Historical projections are contaminated (see ingest/projections.py), so ADP is
the only clean preseason signal available -- and it is what the room actually
drafts on, which makes beating it the operational definition of edge.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ffdo.domain.constants import SEASON_LENGTH
from ffdo.engine import adjustments as adj
from ffdo.ingest import players as players_mod
from ffdo.ingest import projections as proj_mod
from ffdo.ingest import snapshot
from ffdo.ingest import stats as stats_mod

OFFENSE = {"QB", "RB", "WR", "TE"}
_ADP_KEY = "half_ppr"


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    ra = np.argsort(np.argsort(np.asarray(a, dtype=float)))
    rb = np.argsort(np.argsort(np.asarray(b, dtype=float)))
    return float(np.corrcoef(ra, rb)[0, 1])


def evaluate_season(
    season: int,
    *,
    age_weight: float,
    durability_weight: float,
) -> dict:
    """Score ADP alone against ADP plus adjustments, using only prior data."""
    profiles = players_mod.parse(snapshot.load("players_nfl"))
    actual = stats_mod.parse(snapshot.load(f"stats_{season}"), season)

    # ADP survives contamination, so reading this file is deliberate and safe.
    _proj, adp = proj_mod.parse(
        snapshot.load(f"projections_{season}_CONTAMINATED"),
        season, allow_contaminated=True)

    history: dict[str, list] = {}
    for past in range(2021, season):
        for pid, line in stats_mod.parse(
                snapshot.load(f"stats_{past}"), past).items():
            history.setdefault(pid, []).append(line)

    ids, adp_values, truth = [], [], []
    for pid, market in adp.items():
        value = market.adp.get(_ADP_KEY, 999.0)
        prof = profiles.get(pid)
        if value >= 999 or prof is None or prof.position not in OFFENSE:
            continue
        line = actual.get(pid)
        if line is None:
            continue
        ids.append(pid)
        adp_values.append(value)
        truth.append(line.stats.get("pts_half_ppr", 0.0))

    # Lower ADP means better, so negate to align direction with points.
    baseline = [-v for v in adp_values]
    baseline_rho = spearman(baseline, truth)

    if not age_weight and not durability_weight:
        return {"n": len(ids), "baseline_rho": round(baseline_rho, 4),
                "model_rho": round(baseline_rho, 4), "improvement": 0.0}

    length = SEASON_LENGTH[season]
    # Map ADP rank onto a points-like scale so adjustments are commensurate.
    order = np.argsort(adp_values)
    pseudo = np.empty(len(ids), dtype=float)
    pseudo[order] = np.linspace(300.0, 20.0, len(ids))
    points = dict(zip(ids, pseudo, strict=True))

    subset = {pid: profiles[pid] for pid in ids}
    curve = adj.fit_age_curve(
        {pid: history.get(pid, []) for pid in ids}, subset) if age_weight else None

    built = adj.build(
        subset, {pid: history.get(pid, []) for pid in ids}, points,
        replacement_ppg={"QB": 14.0, "RB": 8.0, "WR": 8.0, "TE": 6.0},
        age_weight=age_weight, durability_weight=durability_weight,
        age_curve=curve, current_season=season,
    )
    model = [points[pid] + sum(built.get(pid, {}).values()) for pid in ids]
    model_rho = spearman(model, truth)

    return {
        "n": len(ids),
        "baseline_rho": round(baseline_rho, 4),
        "model_rho": round(model_rho, 4),
        "improvement": round(model_rho - baseline_rho, 4),
        "season_length": length,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/backtest/test_harness.py -v`
Expected: PASS. `baseline_rho` should land near 0.65 for each season.

- [ ] **Step 5: Run the gate and record the verdict**

```bash
uv run python -c "
from ffdo.backtest import harness
for aw, dw in [(0,0), (0,1.0), (1.0,0), (1.0,1.0)]:
    rows = [harness.evaluate_season(s, age_weight=aw, durability_weight=dw)
            for s in (2023, 2024, 2025)]
    mean = sum(r['improvement'] for r in rows) / len(rows)
    print(f'age={aw} dur={dw}  mean improvement={mean:+.4f}  '
          f'{[r[\"improvement\"] for r in rows]}')
"
```

- [ ] **Step 6: Set the weights and document the outcome**

A weight is promoted **only if mean improvement is positive across all three
seasons**. Update `AGE_WEIGHT` / `DURABILITY_WEIGHT` in
`src/ffdo/engine/adjustments.py` accordingly, then append the measured numbers
to the spec's §8 — including a plain statement if an adjustment failed. A
failed adjustment stays at `0.0` and that fact gets written down.

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/ docs/ tests/
git commit -m "feat: backtest harness and validated adjustment weights"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §3 data sources | 2, 3, 4, 8 |
| §3.1 season length varies | 1 (`SEASON_LENGTH`), 3 |
| §3.2 contamination | 4 (guard), 14 (ADP baseline) |
| §3.3 snapshot policy | 2 (loader); September capture is post-draft, outside this plan |
| §4 layering | File Structure; enforced by adapters returning only domain types |
| §5 domain model | 1 |
| §6.1 rescore | 5 |
| §6.2 availability | 13 |
| §6.3 age | 13 |
| §6.4 replacement level | 6 |
| §6.5 VOR | 7 |
| §7.1 survival simulation | 11 |
| §7.2 room calibration | **Not implemented — see gap below** |
| §7.3 cost of waiting | 11 |
| §7.4 tiers, run detection | 7 (tiers); **run detection deferred — see gap below** |
| §8 validation | 13 (default-off), 14 (gate) |
| §9 auction | 9 |
| §10 board | 10, 12 |
| §11 testing | Every task |

**Known gaps, deliberately deferred past the Aug 25 deadline:**

1. **§7.2 room calibration** — fitting ADP spread from this league's own draft
   history. The simulation currently uses a fixed `tau=8.0`. This is a
   refinement to an already-working model, and getting it wrong silently is
   worse than not having it. Add after the draft.
2. **§7.4 run detection** — the binomial test on recent picks. Cheap to add, but
   it is a supporting signal and the cost-of-waiting table already surfaces the
   same information with more rigour.
3. **§4.2 SQLite cache** — Task 10 Step 5 notes that `/api/board` refetches 14 MB
   of player data per poll. **A TTL cache must be added before draft day**; a
   time-bucketed `lru_cache` is enough. This is the one gap that would actually
   hurt on Aug 25.

**Placeholder scan:** No TBDs, no "add error handling", no "similar to Task N".
Every code step contains runnable content.

**Type consistency:** `PlayerProfile`, `SeasonStatLine`, `SeasonProjection`,
`MarketADP`, `LeagueProfile`, `DraftPick`, `DraftState`, `ValuedPlayer` are
defined once in Task 1 and used with identical field names throughout.
`score_stats`, `replacement_levels`, `compute`, `assign_tiers`,
`baseline_prices`, `inflation_factor`, `max_bid`, `simulate_survival`,
`cost_of_waiting`, `build_auction_board`, `build_snake_board`, `build`,
`evaluate_season` each appear with one signature across all tasks.
