"""Current rosters + standings for a Sleeper league, from
/league/<id>/rosters + /users. Distinct from the draft board, which reads
rosters off draft picks -- post-draft, adds/drops/trades have moved
players, so the live roster feed is authoritative."""

from __future__ import annotations

from ffdo.domain.models import RosterEntry
from ffdo.ingest.client import V1, SleeperClient
from ffdo.ingest.teams import _display_names


def _points(settings: dict, whole_key: str, decimal_key: str) -> float:
    whole = float(settings.get(whole_key) or 0)
    return whole + float(settings.get(decimal_key) or 0) / 100.0


def fetch(sleeper: SleeperClient, league_id: str) -> list[RosterEntry]:
    rosters_raw = sleeper.get_json(f"{V1}/league/{league_id}/rosters")
    users_raw = sleeper.get_json(f"{V1}/league/{league_id}/users")
    names = _display_names(users_raw)

    out: list[RosterEntry] = []
    for r in rosters_raw:
        raw_id = r.get("roster_id")
        if raw_id is None:
            continue
        roster_id = int(raw_id)
        settings = r.get("settings") or {}
        owner_id = r.get("owner_id")
        players = tuple(str(p) for p in (r.get("players") or []))
        starters = tuple(str(p) for p in (r.get("starters") or [])
                         if p not in ("0", 0, None))
        out.append(RosterEntry(
            roster_id=roster_id,
            team_name=(names.get(str(owner_id)) if owner_id is not None else None)
                      or f"Team {roster_id}",
            player_ids=players,
            starter_ids=starters,
            wins=int(settings.get("wins") or 0),
            losses=int(settings.get("losses") or 0),
            ties=int(settings.get("ties") or 0),
            points_for=_points(settings, "fpts", "fpts_decimal"),
            points_against=_points(settings, "fpts_against", "fpts_against_decimal"),
        ))
    out.sort(key=lambda e: e.roster_id)
    return out


def raw_starters(
    sleeper: SleeperClient, league_id: str, roster_id: int,
) -> tuple[str | None, ...]:
    """The tracked user's OWN starters array, exactly as Sleeper returns
    it -- positionally aligned to the league's starting slots (in order,
    length always equal to the starting-slot count), with `"0"` (Sleeper's
    empty-slot placeholder) mapped to `None`.

    Deliberately NOT `RosterEntry.starter_ids` (see `fetch` above): that
    field is a compacted SET with alignment already discarded, correct for
    #2's "is this player starting at all" question but wrong for a
    slot-by-slot diff, which needs the alignment back. This fetches the
    same endpoint `fetch` does and is safe to call alongside it -- no
    caching here, same as `fetch`, since a roster's starters can change at
    any moment right up to kickoff."""
    rosters_raw = sleeper.get_json(f"{V1}/league/{league_id}/rosters")
    for r in rosters_raw:
        if r.get("roster_id") == roster_id:
            return tuple(
                None if p in ("0", 0, None) else str(p)
                for p in (r.get("starters") or [])
            )
    return ()
