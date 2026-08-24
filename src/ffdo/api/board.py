"""Shapes engine output into the JSON the board renders."""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.models import DraftState, TeamProfile, ValuedPlayer
from ffdo.engine import auction
from ffdo.engine import grading
from ffdo.engine import roster as roster_engine


def _build_rosters_payload(
    league,
    state: DraftState,
    valued: Mapping[str, ValuedPlayer],
    teams: Mapping[int, TeamProfile] | None,
    your_roster_id: int | None,
) -> list[dict]:
    teams = teams or {}
    picks_by_roster: dict[int, list[str]] = {}
    for p in state.picks:
        if p.roster_id is None:
            continue
        picks_by_roster.setdefault(p.roster_id, []).append(p.player_id)

    roster_ids = set(teams) | set(picks_by_roster)
    rows = []
    for rid in roster_ids:
        team_players = {pid: valued[pid] for pid in picks_by_roster.get(rid, [])
                        if pid in valued}
        lineup = roster_engine.team_lineup(team_players, league)
        team = teams.get(rid)
        players = sorted(
            (
                {
                    "player_id": pid,
                    "name": vp.profile.full_name,
                    "position": vp.profile.position,
                    "vor": round(vp.vor, 1),
                    "starter": pid in lineup.starters,
                }
                for pid, vp in team_players.items()
            ),
            key=lambda r: (r["starter"], r["vor"]),
            reverse=True,
        )
        rows.append({
            "roster_id": rid,
            "team_name": team.display_name if team else f"Team {rid}",
            "is_you": rid == your_roster_id,
            "starting_vor": round(lineup.starting_vor, 1),
            "bench_vor": round(lineup.bench_vor, 1),
            "by_position": {k: round(v, 1) for k, v in lineup.by_position.items()},
            "players": players,
        })
    rows.sort(key=lambda r: (r["starting_vor"], r["bench_vor"]), reverse=True)
    return rows


def _history_row(pick, vp, teams, grade, amount) -> dict:
    team = teams.get(pick.roster_id) if pick.roster_id is not None else None
    if team is not None:
        team_name = team.display_name
    elif pick.roster_id is not None:
        team_name = f"Team {pick.roster_id}"
    else:
        team_name = "—"
    return {
        "pick_no": pick.pick_no,
        "round": pick.round,
        "roster_id": pick.roster_id,
        "team_name": team_name,
        "player_id": pick.player_id,
        "name": vp.profile.full_name if vp else pick.player_id,
        "position": vp.profile.position if vp else None,
        "vor": round(vp.vor, 1) if vp else None,
        "amount": amount,
        "grade": grade,
    }


def _build_auction_history(
    state: DraftState,
    valued: Mapping[str, ValuedPlayer],
    baseline: Mapping[str, float],
    teams: Mapping[int, TeamProfile],
) -> list[dict]:
    """Newest pick first. Ungraded (no badge) when the pick has no recorded
    price -- e.g. a keeper slotted in without a bid -- since there is
    nothing to compare against."""
    rows = []
    for pick in sorted(state.picks, key=lambda p: p.pick_no):
        vp = valued.get(pick.player_id)
        grade = None
        if pick.amount is not None and vp is not None:
            base = baseline.get(pick.player_id, 1.0)
            grade = grading.grade_auction_pick(base, pick.amount)
        rows.append(_history_row(pick, vp, teams, grade, pick.amount))
    rows.reverse()
    return rows


def _build_snake_history(
    state: DraftState,
    valued: Mapping[str, ValuedPlayer],
    teams: Mapping[int, TeamProfile],
) -> list[dict]:
    """Newest pick first. Each pick is graded against the VOR of every other
    still-fantasy-relevant (VOR > 0) player who was undrafted immediately
    before it -- reconstructed by replaying picks in order, not against the
    full player pool, so a 10th-round pick isn't graded against 1st-round
    talent that was already gone."""
    drafted_so_far: set[str] = set()
    rows = []
    for pick in sorted(state.picks, key=lambda p: p.pick_no):
        vp = valued.get(pick.player_id)
        grade = None
        if vp is not None:
            alternatives = [
                other.vor for pid, other in valued.items()
                if other.vor > 0 and pid != pick.player_id and pid not in drafted_so_far
            ]
            grade = grading.grade_snake_pick(vp.vor, alternatives)
        rows.append(_history_row(pick, vp, teams, grade, None))
        drafted_so_far.add(pick.player_id)
    rows.reverse()
    return rows


def build_auction_board(
    league,
    state: DraftState,
    valued: Mapping[str, ValuedPlayer],
    baseline: Mapping[str, float],
    *,
    roster_id: int | None = None,
    teams: Mapping[int, TeamProfile] | None = None,
) -> dict:
    factor = auction.inflation_factor(baseline, state, league)
    drafted = state.drafted_player_ids()
    spent = state.spent_by_roster()

    # "Your" roster state, for the max-bid ceiling and budget strip. When
    # `roster_id` is unknown (FFDO_ROSTER_ID unset), fall back to a fresh
    # 0-spent/0-filled roster rather than guessing -- an honestly-labeled
    # "as if starting from scratch" number beats a silently wrong one.
    your_spent = spent.get(roster_id, 0) if roster_id is not None else 0
    your_slots_filled = (
        sum(1 for p in state.picks if p.roster_id == roster_id)
        if roster_id is not None else 0)
    your_max_bid = auction.max_bid(your_spent, your_slots_filled, league)
    your_slots_left = max(0, league.roster_size - your_slots_filled)
    your_dollars_left = league.budget - your_spent

    by_position = auction.positional_budget(
        valued, baseline, factor, state, league, roster_id, your_dollars_left)

    total_slots = league.num_teams * league.roster_size
    slots_remaining_room = max(1, total_slots - len(drafted))
    league_dollars_per_slot = (
        (league.num_teams * league.budget - sum(spent.values()))
        / slots_remaining_room)
    your_dollars_per_slot = (
        your_dollars_left / your_slots_left if your_slots_left > 0 else 0.0)

    rows = []
    for pid, vp in valued.items():
        base = baseline.get(pid, 1.0)
        # A $1-baseline player must never display a sub-$1 price -- $1 is
        # the legal minimum bid, so the model cannot recommend a number the
        # room can't act on, however low inflation drops.
        adjusted = max(auction.MIN_BID, base * factor)
        rows.append({
            "player_id": pid,
            "name": vp.profile.full_name,
            "position": vp.profile.position,
            "team": vp.profile.team,
            "age": vp.profile.age,
            "vor": round(vp.vor, 1),
            "tier": vp.tier,
            "baseline": round(base, 1),
            "adjusted": round(adjusted, 1),
            "max_bid": your_max_bid,
            "drafted": pid in drafted,
        })
    rows.sort(key=lambda r: r["vor"], reverse=True)

    # Sleeper keeps reporting the last nomination for a beat after the
    # player sells -- surface it only while he's still actually available,
    # so the board never shows a stale "on the block" player as live.
    live_nomination = None
    if state.nominated_player_id is not None and state.nominated_player_id not in drafted:
        live_nomination = {
            "player_id": state.nominated_player_id,
            "bid": state.current_bid,
        }

    return {
        "format": "auction",
        "live_nomination": live_nomination,
        "inflation": round(factor, 3),
        "budget": {
            "total": league.num_teams * league.budget,
            "spent": sum(spent.values()),
            "by_roster": spent,
            "your_roster_id": roster_id,
            "your_spent": your_spent,
            "your_slots_left": your_slots_left,
            "your_dollars_left": your_dollars_left,
            "your_dollars_per_slot": round(your_dollars_per_slot, 1),
            "league_dollars_per_slot": round(league_dollars_per_slot, 1),
            "by_position": by_position,
        },
        "picks_made": len(state.picks),
        "players": rows,
        "rosters": _build_rosters_payload(league, state, valued, teams, roster_id),
        "history": _build_auction_history(state, valued, baseline, teams or {}),
    }


def build_snake_board(
    league,
    state: DraftState,
    valued: Mapping[str, ValuedPlayer],
    survival: Mapping[str, float],
    cost_of_waiting: Mapping[str, Mapping[str, float]],
    *,
    roster_id: int | None = None,
    teams: Mapping[int, TeamProfile] | None = None,
) -> dict:
    drafted = state.drafted_player_ids()

    # `roster_id=None` (FFDO_ROSTER_ID unset) is treated as a fresh roster --
    # zero drafted -- the same fallback auction.positional_budget applies;
    # filtering picks by `p.roster_id == roster_id` without this guard would
    # otherwise match commissioner/keeper picks, which also carry
    # `roster_id=None`, as if they were "yours".
    your_players = (
        {p.player_id: valued[p.player_id] for p in state.picks
         if p.roster_id == roster_id and p.player_id in valued}
        if roster_id is not None else {}
    )
    lineup_value = roster_engine.marginal_lineup_values(your_players, valued, league)

    rows = [
        {
            "player_id": pid,
            "name": vp.profile.full_name,
            "position": vp.profile.position,
            "team": vp.profile.team,
            "age": vp.profile.age,
            "vor": round(vp.vor, 1),
            "tier": vp.tier,
            # `simulate_survival` only returns entries for players who carry
            # an ADP; a player absent from it has no ADP, not a 0% chance
            # of survival. Defaulting to 0.0 there previously rendered
            # "definitely gone" for players who are actually near-certain
            # to still be on the board -- backwards. Absence means no
            # signal either way, so default to certain survival (1.0).
            "survival": round(survival.get(pid, 1.0), 3),
            # How much this player would actually add to *your* starting
            # lineup right now, given who you've already drafted -- not
            # just how good they are league-wide. See
            # ffdo.engine.roster.marginal_lineup_values.
            "lineup_value": round(lineup_value.get(pid, 0.0), 1),
            "drafted": pid in drafted,
        }
        for pid, vp in valued.items()
    ]
    rows.sort(key=lambda r: r["vor"], reverse=True)
    return {
        "format": "snake",
        "cost_of_waiting": dict(cost_of_waiting),
        "picks_made": len(state.picks),
        "players": rows,
        "rosters": _build_rosters_payload(league, state, valued, teams, roster_id),
        "history": _build_snake_history(state, valued, teams or {}),
    }
