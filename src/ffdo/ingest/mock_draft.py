"""Translates a Sleeper mock draft's raw /v1/draft/<id> shape into the same
domain types a real league produces.

A mock draft has no league_id, no /league/<id>/rosters, and a coarse
`metadata.scoring_type` preset instead of a real scoring_settings dict.
Every function here is pure -- called both when a mock draft is first
connected (ffdo.ingest.connect.resolve_mock) and on every subsequent board
poll (ffdo.api.app.get_board), so there is exactly one implementation of
each rule rather than two that can drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ffdo.domain.constants import STANDARD_HALF_PPR
from ffdo.domain.models import LeagueProfile


class MockDraftError(Exception):
    """A user-facing reason a mock draft could not be translated."""


_SLOT_POSITION_MAP: dict[str, str] = {
    "slots_qb": "QB",
    "slots_rb": "RB",
    "slots_wr": "WR",
    "slots_te": "TE",
    "slots_k": "K",
    "slots_def": "DEF",
    "slots_flex": "FLEX",
    "slots_super_flex": "SUPER_FLEX",
}
_KNOWN_SLOT_KEYS = frozenset(_SLOT_POSITION_MAP) | {"slots_bn"}

_SUPPORTED_SCORING_PRESETS = ("ppr", "half_ppr", "standard")


def is_mock_draft(draft_raw: dict[str, Any]) -> bool:
    return draft_raw.get("league_id") is None


def roster_positions_from_slots(settings: dict[str, Any]) -> tuple[str, ...]:
    unrecognized = sorted(
        k for k in settings
        if k.startswith("slots_") and k not in _KNOWN_SLOT_KEYS)
    if unrecognized:
        raise MockDraftError(
            f"Unsupported roster slot type for this mock draft: {unrecognized[0]}")

    starters: list[str] = []
    for key, position in _SLOT_POSITION_MAP.items():
        starters.extend([position] * int(settings.get(key, 0)))

    if "slots_bn" in settings:
        bench_count = int(settings["slots_bn"])
    else:
        bench_count = max(0, int(settings.get("rounds", 0)) - len(starters))

    return tuple(starters) + ("BN",) * bench_count


def scoring_settings_for_preset(scoring_type: str) -> Mapping[str, float]:
    if scoring_type == "half_ppr":
        return dict(STANDARD_HALF_PPR)
    if scoring_type == "ppr":
        return {**STANDARD_HALF_PPR, "rec": 1.0}
    if scoring_type == "standard":
        return {**STANDARD_HALF_PPR, "rec": 0.0}
    raise MockDraftError(
        f"Unsupported scoring type for this mock draft: {scoring_type} "
        f"(supported: {', '.join(_SUPPORTED_SCORING_PRESETS)})")


def build_league_profile(draft_raw: dict[str, Any]) -> LeagueProfile:
    settings = draft_raw.get("settings") or {}
    metadata = draft_raw.get("metadata") or {}
    return LeagueProfile(
        league_id="",
        season=int(draft_raw["season"]),
        num_teams=int(settings["teams"]),
        roster_positions=roster_positions_from_slots(settings),
        scoring_settings=scoring_settings_for_preset(metadata.get("scoring_type", "")),
        budget=settings.get("budget"),
        name=metadata.get("name") or "",
        status=draft_raw["status"],
    )


def resolve_roster_id(draft_raw: dict[str, Any], user_id: str) -> int | None:
    draft_order = draft_raw.get("draft_order") or {}
    slot = draft_order.get(user_id)
    if slot is None:
        return None
    slot_to_roster_id = draft_raw.get("slot_to_roster_id") or {}
    roster_id = slot_to_roster_id.get(str(slot))
    return int(roster_id) if roster_id is not None else None


def backfill_roster_ids(
    picks_raw: list[dict[str, Any]],
    draft_raw: dict[str, Any],
) -> list[dict[str, Any]]:
    """Sleeper never populates a mock-draft pick's roster_id -- confirmed
    even for the connecting human's own picks. draft_slot mapped through
    slot_to_roster_id is the only reliable signal. Returns new pick dicts;
    does not mutate the input."""
    slot_to_roster_id = draft_raw.get("slot_to_roster_id") or {}
    return [
        {**pick, "roster_id": slot_to_roster_id.get(str(pick.get("draft_slot")))}
        for pick in picks_raw
    ]
