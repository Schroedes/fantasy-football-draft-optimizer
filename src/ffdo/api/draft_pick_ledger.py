# src/ffdo/api/draft_pick_ledger.py
"""Decision ledger for real draft picks -- new in sub-project #7. Nothing
before this persisted a draft's picks once the draft completed: DraftState
is fetched live from Sleeper and discarded, and engine.grading's grades
were recomputed fresh on every board poll, never stored. Without this
ledger the outcome scorecard would have nothing to show for the Draft
metric after a draft ends.

Recorded incrementally as the live draft board is polled (see api/app.py's
get_board), one row per pick, the moment that pick is first seen --
record_if_absent means re-polling mid-draft never duplicates a row. A pick
seen for the first time via the "draft already complete" early-return path
(app.py) is recorded with grade/vor_at_pick/predicted_survival all None --
better than permanently losing that pick, even though it can never be
graded after the fact (grading needs the live valuation context that path
deliberately skips).

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
class DraftPickLedgerEntry:
    league_key: str
    draft_id: str
    pick_no: int
    round: int
    roster_id: int | None
    player_id: str
    position: str | None
    amount: int | None
    grade: str | None
    vor_at_pick: float | None
    predicted_survival: float | None
    recorded_at: str


class DraftPickLedger:
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
                CREATE TABLE IF NOT EXISTS draft_pick_record (
                    league_key TEXT NOT NULL,
                    draft_id TEXT NOT NULL,
                    pick_no INTEGER NOT NULL,
                    round INTEGER NOT NULL,
                    roster_id INTEGER,
                    player_id TEXT NOT NULL,
                    position TEXT,
                    amount INTEGER,
                    grade TEXT,
                    vor_at_pick REAL,
                    predicted_survival REAL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (league_key, draft_id, pick_no)
                );
                """
            )
            conn.commit()
        except sqlite3.DatabaseError:
            pass

    def get(self, league_key: str, draft_id: str, pick_no: int) -> DraftPickLedgerEntry | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM draft_pick_record "
                    "WHERE league_key = ? AND draft_id = ? AND pick_no = ?",
                    (league_key, draft_id, pick_no),
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        return self._row_to_entry(row) if row is not None else None

    def record_if_absent(
        self,
        league_key: str,
        *,
        draft_id: str,
        pick_no: int,
        round: int,
        roster_id: int | None,
        player_id: str,
        position: str | None,
        amount: int | None,
        grade: str | None,
        vor_at_pick: float | None,
        predicted_survival: float | None,
    ) -> DraftPickLedgerEntry:
        existing = self.get(league_key, draft_id, pick_no)
        if existing is not None:
            return existing
        recorded_at = _now()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO draft_pick_record "
                    "(league_key, draft_id, pick_no, round, roster_id, player_id, position, "
                    " amount, grade, vor_at_pick, predicted_survival, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (league_key, draft_id, pick_no, round, roster_id, player_id, position,
                     amount, grade, vor_at_pick, predicted_survival, recorded_at),
                )
                conn.commit()
        except sqlite3.DatabaseError:
            pass
        result = self.get(league_key, draft_id, pick_no)
        return result if result is not None else DraftPickLedgerEntry(
            league_key=league_key, draft_id=draft_id, pick_no=pick_no, round=round,
            roster_id=roster_id, player_id=player_id, position=position, amount=amount,
            grade=grade, vor_at_pick=vor_at_pick, predicted_survival=predicted_survival,
            recorded_at=recorded_at)

    def list_for_league(self, league_key: str) -> list[DraftPickLedgerEntry]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM draft_pick_record WHERE league_key = ? "
                    "ORDER BY draft_id ASC, pick_no ASC",
                    (league_key,),
                ).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [self._row_to_entry(row) for row in rows]

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> DraftPickLedgerEntry:
        return DraftPickLedgerEntry(
            league_key=row["league_key"], draft_id=row["draft_id"], pick_no=row["pick_no"],
            round=row["round"], roster_id=row["roster_id"], player_id=row["player_id"],
            position=row["position"], amount=row["amount"], grade=row["grade"],
            vor_at_pick=row["vor_at_pick"], predicted_survival=row["predicted_survival"],
            recorded_at=row["recorded_at"],
        )
