"""Reads the committed preseason snapshot. Fixture source for all tests."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SNAPSHOT_DIR = _REPO_ROOT / "data" / "snapshots" / "2026-08-22-draft-day"


def load(name: str, snapshot_dir: Path | None = None) -> Any:
    path = (snapshot_dir or DEFAULT_SNAPSHOT_DIR) / f"{name}.json.gz"
    if not path.exists():
        raise FileNotFoundError(f"no snapshot {name!r} at {path}")
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)
