#!/usr/bin/env python
"""Re-captures players_nfl/stats_{season}/projections_{season}_CONTAMINATED
from live Sleeper into a new dated snapshot directory, so backtest/harness.py
and scripts/fit_age_curve.py aren't stuck depending on a single frozen
2026-08-22 capture forever. Does NOT touch the existing
data/snapshots/2026-08-22-draft-day/ directory -- writes a new, separate
dated directory instead, so that capture stays available as a stable
historical reference.

Usage: uv run python scripts/refresh_snapshot.py [--seasons 2021-2026]
"""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import date
from pathlib import Path
from typing import Any

from ffdo.ingest.client import PROJECTIONS, V1, SleeperClient

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(data, fh)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seasons", default="2021-2026",
                        help="inclusive season range, e.g. 2021-2026")
    args = parser.parse_args()
    start_str, end_str = args.seasons.split("-")
    seasons = range(int(start_str), int(end_str) + 1)

    out_dir = _REPO_ROOT / "data" / "snapshots" / date.today().isoformat()
    sleeper = SleeperClient()
    try:
        print("Fetching players_nfl...")
        _write(out_dir / "players_nfl.json.gz", sleeper.get_json(f"{V1}/players/nfl"))

        for season in seasons:
            print(f"Fetching stats_{season}...")
            _write(out_dir / f"stats_{season}.json.gz",
                  sleeper.get_json(f"{V1}/stats/nfl/regular/{season}"))

        for season in seasons:
            # This script always fetches whatever Sleeper currently serves --
            # the _CONTAMINATED suffix documents that these files are meant
            # for allow_contaminated=True offline analysis use only, matching
            # the existing 2026-08-22-draft-day capture's own naming and
            # ingest/projections.py's contamination guard.
            print(f"Fetching projections_{season}...")
            _write(out_dir / f"projections_{season}_CONTAMINATED.json.gz",
                  sleeper.get_json(
                      f"{PROJECTIONS}/{season}"
                      "?season_type=regular&position[]=QB&position[]=RB"
                      "&position[]=WR&position[]=TE&position[]=DEF&position[]=K"))
    finally:
        sleeper.close()

    print(f"Snapshot written to {out_dir}")


if __name__ == "__main__":
    main()
