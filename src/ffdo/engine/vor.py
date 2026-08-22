"""Value over replacement, plus tier detection by gap clustering."""

from __future__ import annotations

from collections.abc import Mapping
from statistics import median

from ffdo.domain.models import PlayerProfile, ValuedPlayer
from ffdo.engine.replacement import replacement_levels


def compute(
    points: Mapping[str, float],
    profiles: Mapping[str, PlayerProfile],
    league,
    *,
    adjustments: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, ValuedPlayer]:
    adjustments = adjustments or {}
    adjusted = {
        pid: pts + sum(adjustments.get(pid, {}).values())
        for pid, pts in points.items()
        if pid in profiles
    }
    positions = {pid: profiles[pid].position for pid in adjusted}
    levels = replacement_levels(adjusted, positions, league)

    out: dict[str, ValuedPlayer] = {}
    for pid, adj_pts in adjusted.items():
        pos = positions[pid]
        if pos not in levels:
            # No roster slot (dedicated or FLEX-reachable) exists for this
            # position in `league.roster_positions`, so there is no
            # replacement level to measure against. Silently defaulting to
            # 0.0 here would turn a player's raw point total into his VOR --
            # exactly backwards, since it makes an unrostered position look
            # like an extreme bargain. Exclude instead: a player at a
            # position the league doesn't start has no meaningful VOR.
            continue
        out[pid] = ValuedPlayer(
            profile=profiles[pid],
            projected_points=points[pid],
            adjusted_points=adj_pts,
            vor=adj_pts - levels[pos],
            tier=0,
            adjustments=dict(adjustments.get(pid, {})),
        )
    return out


def assign_tiers(
    valued: Mapping[str, ValuedPlayer],
    *,
    gap_multiple: float = 1.75,
) -> dict[str, ValuedPlayer]:
    """Tier breaks fall where a VOR gap exceeds `gap_multiple` x median gap."""
    by_position: dict[str, list[ValuedPlayer]] = {}
    for vp in valued.values():
        by_position.setdefault(vp.profile.position, []).append(vp)

    out: dict[str, ValuedPlayer] = {}
    for players in by_position.values():
        players.sort(key=lambda v: v.vor, reverse=True)
        gaps = [players[i].vor - players[i + 1].vor
                for i in range(len(players) - 1)]
        threshold = (median(g for g in gaps if g >= 0) * gap_multiple
                     if gaps else 0.0)
        tier = 1
        for i, vp in enumerate(players):
            if i > 0 and threshold > 0 and gaps[i - 1] > threshold:
                tier += 1
            out[vp.profile.player_id] = ValuedPlayer(
                profile=vp.profile,
                projected_points=vp.projected_points,
                adjusted_points=vp.adjusted_points,
                vor=vp.vor, tier=tier, adjustments=vp.adjustments,
            )
    return out
