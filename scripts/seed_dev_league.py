"""Tracks the pinned 2026 auction league into a local data/ffdo.db, so a
fresh clone has something on the board without going through the connect
UI. Replaces the removed FFDO_LEAGUE_ID / FFDO_DRAFT_ID env-var defaults.

Usage: uv run python scripts/seed_dev_league.py [SLEEPER_USERNAME]
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from ffdo.api.store import LeagueStore
from ffdo.domain.models import ProviderCredential
from ffdo.ingest import connect
from ffdo.ingest.client import SleeperClient

PINNED_LEAGUE_ID = "1315881559957458944"


def main() -> None:
    username = sys.argv[1] if len(sys.argv) > 1 else "noahdschroeder"
    store = LeagueStore(Path("data") / "ffdo.db")
    sleeper = SleeperClient()
    try:
        lg = connect.track(sleeper, PINNED_LEAGUE_ID, username)
    finally:
        sleeper.close()
    store.put_credential(ProviderCredential(
        provider="sleeper", user_identifier=username, espn_s2=None, swid=None,
        updated_at=datetime.now(timezone.utc).isoformat()))
    store.upsert(lg)
    print(f"tracked {lg.name} ({lg.league_key})")


if __name__ == "__main__":
    main()
