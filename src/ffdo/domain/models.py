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
    name: str = ""
    status: str = ""

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
