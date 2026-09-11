"""Decision ledger for the weekly lineup recommendation.

One row per (league_key, season, week). Written once, on the first
`/lineup` view of that week (`record_if_absent`) -- never overwritten by a
later view the same week, even as projections refine, because the
ledger's job is "what did we tell you, and what did you have set, the
FIRST time you looked." Resolved once every game that week has started
(`resolve`, called from `ffdo.api.app.get_lineup` once
`ingest.sleeper.schedule.week_locked` is true): compares the final actual
starters against the stored recommendation, slot by slot.

Same connection pattern as `ffdo.api.store.LeagueStore` -- one file,
stdlib `sqlite3`, no ORM. This app is still a single local process for one
user, so there is no concurrency model beyond "open a connection per
call."
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class LineupRecord:
    league_key: str
    season: int
    week: int
    recommended: dict[int, str | None]
    actual: tuple[str | None, ...]
    recorded_at: str
    followed: str | None
    resolved_at: str | None


class LineupLedger:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        if not self._ready:
            self._init_schema(conn)
            self._ready = True
        return conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS lineup_recommendation (
                    league_key TEXT NOT NULL,
                    season INTEGER NOT NULL,
                    week INTEGER NOT NULL,
                    recommended_json TEXT NOT NULL,
                    actual_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    followed TEXT,
                    resolved_at TEXT,
                    PRIMARY KEY (league_key, season, week)
                );
                """
            )
            conn.commit()
        except sqlite3.DatabaseError:
            # A corrupt/foreign file at this path: treat the store as empty
            # rather than crashing the app on startup (mirrors LeagueStore).
            pass

    def get(self, league_key: str, season: int, week: int) -> LineupRecord | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM lineup_recommendation "
                    "WHERE league_key = ? AND season = ? AND week = ?",
                    (league_key, season, week),
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        return self._row_to_record(row) if row is not None else None

    def record_if_absent(
        self,
        league_key: str,
        season: int,
        week: int,
        recommended: dict[int, str | None],
        actual: tuple[str | None, ...],
    ) -> LineupRecord:
        existing = self.get(league_key, season, week)
        if existing is not None:
            return existing
        recorded_at = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO lineup_recommendation "
                "(league_key, season, week, recommended_json, actual_json, "
                " recorded_at, followed, resolved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (league_key, season, week,
                 json.dumps({str(k): v for k, v in recommended.items()}),
                 json.dumps(list(actual)),
                 recorded_at, None, None),
            )
            conn.commit()
        # Re-read rather than trust the just-written values: a concurrent
        # writer (impossible in this single-process app today, but cheap
        # to get right) could have won the INSERT OR IGNORE race.
        return self.get(league_key, season, week)  # type: ignore[return-value]

    def resolve(
        self,
        league_key: str,
        season: int,
        week: int,
        final_actual: tuple[str | None, ...],
    ) -> str:
        record = self.get(league_key, season, week)
        if record is None:
            raise ValueError(
                f"no lineup_recommendation row for {league_key} season {season} week {week}")
        total = len(record.recommended)
        matches = sum(
            1 for i, want in record.recommended.items()
            if i < len(final_actual) and final_actual[i] == want
        )
        followed = "full" if matches == total else ("partial" if matches > 0 else "none")
        with self._connect() as conn:
            conn.execute(
                "UPDATE lineup_recommendation SET followed = ?, resolved_at = ? "
                "WHERE league_key = ? AND season = ? AND week = ?",
                (followed, _now(), league_key, season, week),
            )
            conn.commit()
        return followed

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> LineupRecord:
        recommended_raw = json.loads(row["recommended_json"])
        return LineupRecord(
            league_key=row["league_key"], season=row["season"], week=row["week"],
            recommended={int(k): v for k, v in recommended_raw.items()},
            actual=tuple(json.loads(row["actual_json"])),
            recorded_at=row["recorded_at"], followed=row["followed"],
            resolved_at=row["resolved_at"],
        )
