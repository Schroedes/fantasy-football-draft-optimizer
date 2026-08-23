"""Persists the connected league/user/draft so it survives a process restart.

A single JSON file plus an in-memory cache -- this app is a single local
process for one user's draft day, so there is no multi-session or
concurrency concern to design for.
"""

from __future__ import annotations

import json
from pathlib import Path

from ffdo.domain.models import Session

_UNSET = object()


class SessionStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._cached: Session | None | object = _UNSET

    def get(self) -> Session | None:
        if self._cached is _UNSET:
            self._cached = self.load()
        return self._cached

    def load(self) -> Session | None:
        if not self._path.exists():
            return None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        try:
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
        except (KeyError, TypeError):
            return None

    def save(self, session: Session) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
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
        self._path.write_text(json.dumps(payload), encoding="utf-8")
        self._cached = session

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()
        self._cached = None
