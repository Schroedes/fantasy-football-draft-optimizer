#!/usr/bin/env python
"""Fits DYNASTY_AGE_CURVE from a historical snapshot directory -- prints the
result as a Python literal for a human to review and paste into
domain/constants.py by hand. Deliberately does NOT write the file itself: a
statistically-fit artifact silently overwriting a checked-in constant on
every run is a foot-gun a human should catch, not automate past.

Usage: uv run python scripts/fit_age_curve.py <snapshot-dir>
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from ffdo.domain.constants import STANDARD_HALF_PPR
from ffdo.domain.models import SeasonStatLine
from ffdo.engine import adjustments
from ffdo.ingest import players as players_mod
from ffdo.ingest import snapshot
from ffdo.ingest import stats as stats_mod

_STATS_FILE_RE = re.compile(r"^stats_(\d{4})\.json\.gz$")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot_dir", type=Path)
    args = parser.parse_args()

    profiles = players_mod.parse(snapshot.load("players_nfl", args.snapshot_dir))

    seasons = sorted(
        int(m.group(1)) for f in args.snapshot_dir.glob("stats_*.json.gz")
        if (m := _STATS_FILE_RE.match(f.name))
    )
    history_by_player: dict[str, list[SeasonStatLine]] = {}
    for season in seasons:
        for pid, line in stats_mod.parse(
                snapshot.load(f"stats_{season}", args.snapshot_dir), season).items():
            history_by_player.setdefault(pid, []).append(line)

    curve = adjustments.fit_age_curve(history_by_player, profiles, STANDARD_HALF_PPR)

    print(f"# Fit from {args.snapshot_dir.name}, seasons {seasons}")
    print("DYNASTY_AGE_CURVE: Final[dict[str, dict[int, float]]] = {")
    for position in sorted(curve):
        ages = curve[position]
        print(f'    "{position}": {{')
        for age in sorted(ages):
            print(f"        {age}: {round(ages[age], 4)},")
        print("    },")
    print("}")


if __name__ == "__main__":
    main()
