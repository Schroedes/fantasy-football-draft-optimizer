"""SQLite-backed store of tracked leagues and per-provider credentials.

Replaces the single-league `session.json` / `SessionStore`. One file
(`data/ffdo.db`), stdlib `sqlite3`, no ORM. This app is still a single
local process for one user, so there is no concurrency model beyond
"open a connection per call."
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ffdo.domain.models import ProviderCredential, TrackedLeague

_LEAGUE_COLUMNS = (
    "league_key", "provider", "provider_league_id", "season", "name", "user_id",
    "roster_id", "draft_id", "draft_type", "draft_status", "num_teams", "budget",
    "rounds", "roster_positions", "scoring_settings", "format", "format_override",
    "raw_settings", "is_mock", "tracked_at", "last_refreshed_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LeagueStore:
    def __init__(self, path: Path, legacy_session_path: Path | None = None) -> None:
        self._path = path
        self._legacy_session_path = legacy_session_path
        self._ready = False

    # -- schema ------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        first_open = not self._ready
        if first_open:
            self._init_schema(conn)
            self._ready = True
            self._migrate_legacy_session(conn)
        return conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tracked_league (
                    league_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_league_id TEXT NOT NULL,
                    season INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    roster_id INTEGER,
                    draft_id TEXT NOT NULL,
                    draft_type TEXT NOT NULL,
                    draft_status TEXT NOT NULL,
                    num_teams INTEGER NOT NULL,
                    budget INTEGER,
                    rounds INTEGER NOT NULL,
                    roster_positions TEXT NOT NULL,
                    scoring_settings TEXT NOT NULL,
                    format TEXT NOT NULL,
                    format_override TEXT,
                    raw_settings TEXT NOT NULL,
                    is_mock INTEGER NOT NULL,
                    tracked_at TEXT NOT NULL,
                    last_refreshed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_credential (
                    provider TEXT PRIMARY KEY,
                    user_identifier TEXT NOT NULL,
                    espn_s2 TEXT,
                    swid TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.commit()
        except sqlite3.DatabaseError:
            # A corrupt/foreign file at this path: treat the store as empty
            # rather than crashing the app on startup.
            pass

    def _migrate_legacy_session(self, conn: sqlite3.Connection) -> None:
        """One-shot import of a pre-multi-league ``data/session.json``.

        Runs once, on the first ``_connect()``. Guarded by the empty-table
        check plus the rename to ``<name>.migrated``, so it is idempotent
        and never touches a store that already has leagues. A missing or
        malformed legacy file is a no-op, not an error.
        """
        path = self._legacy_session_path
        if path is None or not path.exists():
            return
        try:
            already = conn.execute("SELECT COUNT(*) FROM tracked_league").fetchone()[0]
        except sqlite3.DatabaseError:
            return
        if already:
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        provider = raw.get("provider", "sleeper")
        is_mock = bool(raw.get("is_mock"))
        provider_key = "sleeper-mock" if is_mock else provider
        league_id = raw.get("league_id") or raw.get("draft_id", "")
        provider_league_id = raw["draft_id"] if is_mock else league_id
        season = int(raw["season"])
        from ffdo.domain.models import make_league_key
        league_key = make_league_key(provider_key, provider_league_id, season)
        now = _now()
        tracked = TrackedLeague(
            league_key=league_key, provider=provider_key,
            provider_league_id=provider_league_id, season=season,
            name=raw.get("league_name", ""), user_id=raw.get("user_id", ""),
            roster_id=raw.get("roster_id"), draft_id=raw.get("draft_id", ""),
            draft_type=raw.get("draft_type", "snake"),
            draft_status=raw.get("draft_status", ""),
            num_teams=int(raw.get("num_teams", 0)), budget=raw.get("budget"),
            rounds=int(raw.get("rounds", 0)),
            roster_positions=tuple(raw.get("roster_positions", ())),
            scoring_settings={k: float(v) for k, v
                              in (raw.get("scoring_settings") or {}).items()},
            fmt="redraft", format_override=None, raw_settings={}, is_mock=is_mock,
            tracked_at=now, last_refreshed_at=now,
        )
        self._write_league_row(conn, tracked, tracked.format_override, tracked.tracked_at)

        espn_s2, swid = raw.get("espn_s2"), raw.get("swid")
        if espn_s2 or swid:
            conn.execute(
                "INSERT OR REPLACE INTO provider_credential "
                "(provider, user_identifier, espn_s2, swid, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("espn", swid or "", espn_s2, swid, now),
            )
        elif provider == "sleeper" and raw.get("username"):
            conn.execute(
                "INSERT OR REPLACE INTO provider_credential "
                "(provider, user_identifier, espn_s2, swid, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("sleeper", raw["username"], None, None, now),
            )
        conn.commit()
        path.rename(path.with_name(path.name + ".migrated"))

    # -- tracked leagues -------------------------------------------------

    # Read ops (list/get/get_credential) deliberately swallow a corrupt-file
    # DatabaseError and report "empty" so the app still starts; write ops let it
    # surface, since silently dropping a write would be the worse failure.
    def list(self) -> list[TrackedLeague]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM tracked_league ORDER BY tracked_at"
                ).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [self._row_to_league(r) for r in rows]

    def get(self, league_key: str) -> TrackedLeague | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM tracked_league WHERE league_key = ?", (league_key,)
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        return self._row_to_league(row) if row is not None else None

    @staticmethod
    def _write_league_row(
        conn: sqlite3.Connection,
        tracked: TrackedLeague,
        format_override: str | None,
        tracked_at: str,
    ) -> None:
        values = (
            tracked.league_key, tracked.provider, tracked.provider_league_id,
            tracked.season, tracked.name, tracked.user_id, tracked.roster_id,
            tracked.draft_id, tracked.draft_type, tracked.draft_status,
            tracked.num_teams, tracked.budget, tracked.rounds,
            json.dumps(list(tracked.roster_positions)),
            json.dumps(dict(tracked.scoring_settings)),
            tracked.fmt, format_override,
            json.dumps(dict(tracked.raw_settings)),
            int(tracked.is_mock), tracked_at, tracked.last_refreshed_at,
        )
        placeholders = ", ".join("?" for _ in _LEAGUE_COLUMNS)
        conn.execute(
            f"INSERT OR REPLACE INTO tracked_league "
            f"({', '.join(_LEAGUE_COLUMNS)}) VALUES ({placeholders})",
            values,
        )

    def upsert(self, tracked: TrackedLeague) -> None:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT format_override, tracked_at FROM tracked_league WHERE league_key = ?",
                (tracked.league_key,),
            ).fetchone()
            format_override = existing["format_override"] if existing else tracked.format_override
            tracked_at = existing["tracked_at"] if existing else tracked.tracked_at
            self._write_league_row(conn, tracked, format_override, tracked_at)
            conn.commit()

    def delete(self, league_key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM tracked_league WHERE league_key = ?", (league_key,))
            conn.commit()

    def set_format_override(self, league_key: str, value: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tracked_league SET format_override = ? WHERE league_key = ?",
                (value, league_key),
            )
            conn.commit()

    def touch_status(self, league_key: str, draft_status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tracked_league SET draft_status = ?, last_refreshed_at = ? "
                "WHERE league_key = ?",
                (draft_status, _now(), league_key),
            )
            conn.commit()

    # -- credentials ----------------------------------------------------

    def get_credential(self, provider: str) -> ProviderCredential | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM provider_credential WHERE provider = ?", (provider,)
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        if row is None:
            return None
        return ProviderCredential(
            provider=row["provider"], user_identifier=row["user_identifier"],
            espn_s2=row["espn_s2"], swid=row["swid"], updated_at=row["updated_at"],
        )

    def put_credential(self, cred: ProviderCredential) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO provider_credential "
                "(provider, user_identifier, espn_s2, swid, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (cred.provider, cred.user_identifier, cred.espn_s2, cred.swid,
                 cred.updated_at),
            )
            conn.commit()

    # -- row mapping ---------------------------------------------------

    @staticmethod
    def _row_to_league(row: sqlite3.Row) -> TrackedLeague:
        return TrackedLeague(
            league_key=row["league_key"], provider=row["provider"],
            provider_league_id=row["provider_league_id"], season=row["season"],
            name=row["name"], user_id=row["user_id"], roster_id=row["roster_id"],
            draft_id=row["draft_id"], draft_type=row["draft_type"],
            draft_status=row["draft_status"], num_teams=row["num_teams"],
            budget=row["budget"], rounds=row["rounds"],
            roster_positions=tuple(json.loads(row["roster_positions"])),
            scoring_settings={k: float(v) for k, v
                              in json.loads(row["scoring_settings"]).items()},
            fmt=row["format"], format_override=row["format_override"],
            raw_settings=json.loads(row["raw_settings"]),
            is_mock=bool(row["is_mock"]),
            tracked_at=row["tracked_at"], last_refreshed_at=row["last_refreshed_at"],
        )
