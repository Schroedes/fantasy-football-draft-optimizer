"""Translates /v1/user/<username> into a (user_id, display_name) pair."""

from __future__ import annotations

from typing import Any


def parse(raw: dict[str, Any]) -> tuple[str, str]:
    user_id = raw["user_id"]
    display_name = raw.get("display_name") or raw.get("username") or ""
    return user_id, display_name
