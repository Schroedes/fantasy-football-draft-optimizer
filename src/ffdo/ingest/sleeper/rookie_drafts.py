"""Real fantasy rookie-draft history for a Sleeper dynasty/keeper league,
walked back through `previous_league_id`. Used ONLY by
scripts/fit_pick_value.py to fit PICK_VALUE_CURVE from real slot-to-
player-to-production data -- never called from the live app path (the
live app only ever reads the already-fitted, checked-in constant).
"""

from __future__ import annotations

from dataclasses import dataclass

from ffdo.ingest import draft as draft_mod
from ffdo.ingest.client import V1, SleeperClient

# A rookie draft is `linear` (not `snake`, not `auction`) -- this
# distinguishes an annual rookie-only draft from the one-time startup
# draft. Confirmed live against real leagues during brainstorming: both of
# the user's tracked dynasty leagues (GDK, Room Temp IQ) run a one-time
# snake/auction startup draft followed by annual linear rookie drafts.
_ROOKIE_DRAFT_TYPE = "linear"


@dataclass(frozen=True, slots=True)
class RookiePick:
    season: int
    round: int
    # Sleeper's `draft_slot`. For a LINEAR draft this equals the pick's
    # position within its round directly -- unlike a snake draft, a linear
    # draft never reverses direction between rounds, so no snake-order
    # math is needed to turn draft_slot into "pick within round."
    pick_in_round: int
    player_id: str


def league_seasons(sleeper: SleeperClient, league_id: str, *, max_seasons: int = 8) -> list[str]:
    """Walks `previous_league_id` back from `league_id`. Returns every
    league_id in the chain, this season first, oldest last. Stops at
    `max_seasons` links or the first missing/absent `previous_league_id`,
    whichever comes first -- bounds the walk for a very long-running
    league."""
    chain = [league_id]
    current = league_id
    for _ in range(max_seasons - 1):
        raw = sleeper.get_json(f"{V1}/league/{current}")
        previous = raw.get("previous_league_id")
        if not previous:
            break
        chain.append(previous)
        current = previous
    return chain


def rookie_picks(sleeper: SleeperClient, league_id: str) -> list[RookiePick]:
    """Every real pick from every completed rookie (linear-type) draft in
    THIS ONE league (not its previous-season chain -- callers walk the
    chain themselves via `league_seasons` and call this once per link)."""
    drafts_raw = sleeper.get_json(f"{V1}/league/{league_id}/drafts")
    out: list[RookiePick] = []
    for entry in drafts_raw:
        if entry.get("type") != _ROOKIE_DRAFT_TYPE or entry.get("status") != "complete":
            continue
        draft_id = entry["draft_id"]
        season = int(entry["season"])
        picks_raw = sleeper.get_json(f"{V1}/draft/{draft_id}/picks")
        state = draft_mod.parse(entry, picks_raw)
        for pick in state.picks:
            out.append(RookiePick(
                season=season, round=pick.round,
                pick_in_round=pick.draft_slot, player_id=pick.player_id))
    return out
