"""Decision ledger for real, completed trades -- every trade in the
league, not only ones the tracked user is party to (spec §6.1).

Records a trade's value snapshot once, on first detection
(`record_if_absent`) -- never overwritten, the same write-once posture as
`lineup_ledger.py`. Does NOT compute "current value" itself: that needs
fresh valuation/pick data this module has no access to. The API layer
(api/app.py) recomputes it at read time and merges it onto what
`list_for_league` returns (spec §6.3) -- this module only ever persists
and returns the frozen trade-time snapshot plus enough raw data
(banked-points-at-trade) for that recomputation to happen elsewhere.

Same connection pattern as lineup_ledger.py -- one file, stdlib sqlite3,
no ORM, corrupt-DB tolerance (treat as empty)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ffdo.domain.models import TradeTransaction


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class TradeLedgerEntry:
    league_key: str
    transaction_id: str
    season: int
    week: int
    roster_a_id: int
    roster_b_id: int
    roster_a_gets: list[str]
    roster_b_gets: list[str]
    picks_to_a: list[dict]   # DraftPickAsset fields, serialized -- see _row_to_entry
    picks_to_b: list[dict]
    traded_at_ms: int
    side_a_value_at_trade: float
    side_b_value_at_trade: float
    banked_a_at_trade: dict[str, float]
    banked_b_at_trade: dict[str, float]
    recorded_at: str


class TradeLedger:
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
                CREATE TABLE IF NOT EXISTS trade_record (
                    league_key TEXT NOT NULL,
                    transaction_id TEXT NOT NULL,
                    season INTEGER NOT NULL,
                    week INTEGER NOT NULL,
                    roster_a_id INTEGER NOT NULL,
                    roster_b_id INTEGER NOT NULL,
                    roster_a_gets_json TEXT NOT NULL,
                    roster_b_gets_json TEXT NOT NULL,
                    picks_to_a_json TEXT NOT NULL,
                    picks_to_b_json TEXT NOT NULL,
                    traded_at_ms INTEGER NOT NULL,
                    side_a_value_at_trade REAL NOT NULL,
                    side_b_value_at_trade REAL NOT NULL,
                    banked_a_json TEXT NOT NULL,
                    banked_b_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (league_key, transaction_id)
                );
                """
            )
            conn.commit()
        except sqlite3.DatabaseError:
            pass

    def get(self, league_key: str, transaction_id: str) -> TradeLedgerEntry | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM trade_record WHERE league_key = ? AND transaction_id = ?",
                    (league_key, transaction_id),
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        return self._row_to_entry(row) if row is not None else None

    def record_if_absent(
        self,
        league_key: str,
        trade: TradeTransaction,
        *,
        side_a_value: float,
        side_b_value: float,
        banked_a: dict[str, float],
        banked_b: dict[str, float],
    ) -> TradeLedgerEntry:
        existing = self.get(league_key, trade.transaction_id)
        if existing is not None:
            return existing
        recorded_at = _now()
        picks_to_a_json = json.dumps([
            {"season": p.season, "round": p.round, "projected_slot": p.projected_slot,
             "current_owner_roster_id": p.current_owner_roster_id,
             "original_roster_id": p.original_roster_id} for p in trade.picks_to_a])
        picks_to_b_json = json.dumps([
            {"season": p.season, "round": p.round, "projected_slot": p.projected_slot,
             "current_owner_roster_id": p.current_owner_roster_id,
             "original_roster_id": p.original_roster_id} for p in trade.picks_to_b])
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO trade_record "
                    "(league_key, transaction_id, season, week, roster_a_id, roster_b_id, "
                    " roster_a_gets_json, roster_b_gets_json, picks_to_a_json, picks_to_b_json, "
                    " traded_at_ms, side_a_value_at_trade, side_b_value_at_trade, "
                    " banked_a_json, banked_b_json, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (league_key, trade.transaction_id, trade.season, trade.week,
                     trade.roster_a_id, trade.roster_b_id,
                     json.dumps(trade.roster_a_gets), json.dumps(trade.roster_b_gets),
                     picks_to_a_json, picks_to_b_json, trade.traded_at_ms,
                     side_a_value, side_b_value,
                     json.dumps(banked_a), json.dumps(banked_b), recorded_at),
                )
                conn.commit()
        except sqlite3.DatabaseError:
            pass
        result = self.get(league_key, trade.transaction_id)
        return result if result is not None else TradeLedgerEntry(
            league_key=league_key, transaction_id=trade.transaction_id,
            season=trade.season, week=trade.week,
            roster_a_id=trade.roster_a_id, roster_b_id=trade.roster_b_id,
            roster_a_gets=trade.roster_a_gets, roster_b_gets=trade.roster_b_gets,
            picks_to_a=[], picks_to_b=[], traded_at_ms=trade.traded_at_ms,
            side_a_value_at_trade=side_a_value, side_b_value_at_trade=side_b_value,
            banked_a_at_trade=banked_a, banked_b_at_trade=banked_b,
            recorded_at=recorded_at)

    def list_for_league(self, league_key: str) -> list[TradeLedgerEntry]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM trade_record WHERE league_key = ? ORDER BY traded_at_ms ASC",
                    (league_key,),
                ).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [self._row_to_entry(row) for row in rows]

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> TradeLedgerEntry:
        return TradeLedgerEntry(
            league_key=row["league_key"], transaction_id=row["transaction_id"],
            season=row["season"], week=row["week"],
            roster_a_id=row["roster_a_id"], roster_b_id=row["roster_b_id"],
            roster_a_gets=json.loads(row["roster_a_gets_json"]),
            roster_b_gets=json.loads(row["roster_b_gets_json"]),
            picks_to_a=json.loads(row["picks_to_a_json"]),
            picks_to_b=json.loads(row["picks_to_b_json"]),
            traded_at_ms=row["traded_at_ms"],
            side_a_value_at_trade=row["side_a_value_at_trade"],
            side_b_value_at_trade=row["side_b_value_at_trade"],
            banked_a_at_trade=json.loads(row["banked_a_json"]),
            banked_b_at_trade=json.loads(row["banked_b_json"]),
            recorded_at=row["recorded_at"],
        )
