# src/ffdo/api/waiver_ledger.py
"""Decision ledger for FAAB waiver claims -- deferred from sub-project #6,
built here alongside the outcome scorecard that reads it (spec
docs/superpowers/specs/2026-09-13-outcome-scorecard-design.md).

Unlike lineup_ledger.py (needs week-lock to resolve) and trade_ledger.py
(never resolves, continuously revalued), a waiver claim's outcome is known
synchronously -- Sleeper's transaction feed reports a final status (won or
lost) as soon as that week's waivers process -- so record_if_absent
captures the whole outcome in one write, no separate resolve() step.

Same connection pattern as the other ledgers: one file, stdlib sqlite3, no
ORM, corrupt-DB tolerance (treat as empty)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class WaiverLedgerEntry:
    league_key: str
    transaction_id: str
    season: int
    week: int
    roster_id: int
    add_player_id: str
    drop_player_id: str | None
    recommended_bid: float | None
    actual_bid: int
    predicted_vor_gain: float | None
    won: bool
    recorded_at: str


class WaiverLedger:
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
                CREATE TABLE IF NOT EXISTS waiver_record (
                    league_key TEXT NOT NULL,
                    transaction_id TEXT NOT NULL,
                    season INTEGER NOT NULL,
                    week INTEGER NOT NULL,
                    roster_id INTEGER NOT NULL,
                    add_player_id TEXT NOT NULL,
                    drop_player_id TEXT,
                    recommended_bid REAL,
                    actual_bid INTEGER NOT NULL,
                    predicted_vor_gain REAL,
                    won INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (league_key, transaction_id)
                );
                """
            )
            conn.commit()
        except sqlite3.DatabaseError:
            pass

    def get(self, league_key: str, transaction_id: str) -> WaiverLedgerEntry | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM waiver_record WHERE league_key = ? AND transaction_id = ?",
                    (league_key, transaction_id),
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        return self._row_to_entry(row) if row is not None else None

    def record_if_absent(
        self,
        league_key: str,
        *,
        transaction_id: str,
        season: int,
        week: int,
        roster_id: int,
        add_player_id: str,
        drop_player_id: str | None,
        recommended_bid: float | None,
        actual_bid: int,
        predicted_vor_gain: float | None,
        won: bool,
    ) -> WaiverLedgerEntry:
        existing = self.get(league_key, transaction_id)
        if existing is not None:
            return existing
        recorded_at = _now()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO waiver_record "
                    "(league_key, transaction_id, season, week, roster_id, add_player_id, "
                    " drop_player_id, recommended_bid, actual_bid, predicted_vor_gain, won, "
                    " recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (league_key, transaction_id, season, week, roster_id, add_player_id,
                     drop_player_id, recommended_bid, actual_bid, predicted_vor_gain,
                     int(won), recorded_at),
                )
                conn.commit()
        except sqlite3.DatabaseError:
            pass
        result = self.get(league_key, transaction_id)
        return result if result is not None else WaiverLedgerEntry(
            league_key=league_key, transaction_id=transaction_id, season=season, week=week,
            roster_id=roster_id, add_player_id=add_player_id, drop_player_id=drop_player_id,
            recommended_bid=recommended_bid, actual_bid=actual_bid,
            predicted_vor_gain=predicted_vor_gain, won=won, recorded_at=recorded_at)

    def list_for_league(self, league_key: str) -> list[WaiverLedgerEntry]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM waiver_record WHERE league_key = ? ORDER BY recorded_at ASC",
                    (league_key,),
                ).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [self._row_to_entry(row) for row in rows]

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> WaiverLedgerEntry:
        return WaiverLedgerEntry(
            league_key=row["league_key"], transaction_id=row["transaction_id"],
            season=row["season"], week=row["week"], roster_id=row["roster_id"],
            add_player_id=row["add_player_id"], drop_player_id=row["drop_player_id"],
            recommended_bid=row["recommended_bid"], actual_bid=row["actual_bid"],
            predicted_vor_gain=row["predicted_vor_gain"], won=bool(row["won"]),
            recorded_at=row["recorded_at"],
        )
