"""Simulate the rest of a snake draft forward to estimate your best
achievable team, accounting for who's likely to survive to each of your
future picks."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

import numpy as np

from ffdo.domain.constants import OFFENSE_POSITIONS
from ffdo.domain.models import DraftState, ValuedPlayer
from ffdo.engine import roster as roster_engine
from ffdo.engine.market import gone_this_stretch
from ffdo.engine.replacement import FLEX_ELIGIBILITY


def _your_draft_slot(state: DraftState, roster_id: int | None) -> int | None:
    """Your seat. Prefers `state.draft_order` (a provider-supplied full
    pick order, known before anyone has picked -- ESPN populates this;
    see ffdo.ingest.espn.draft.parse), falling back to reading it off any
    pick you've already made. None if neither signal has your seat yet
    (or roster_id is unset)."""
    if roster_id is None:
        return None
    if state.draft_order is not None:
        return state.draft_order.get(roster_id)
    return next((p.draft_slot for p in state.picks if p.roster_id == roster_id), None)


def _pick_no_for(round_no: int, draft_slot: int, num_teams: int) -> int:
    pick_in_round = draft_slot if round_no % 2 == 1 else num_teams - draft_slot + 1
    return (round_no - 1) * num_teams + pick_in_round


def _slot_for_pick(pick_no: int, num_teams: int) -> int:
    round_no = (pick_no - 1) // num_teams + 1
    pick_in_round = (pick_no - 1) % num_teams + 1
    return pick_in_round if round_no % 2 == 1 else num_teams - pick_in_round + 1


def _need_weights(sim_roster: Mapping[str, ValuedPlayer], league) -> dict[str, float]:
    """Cheap stand-in for "do I still need this position": full weight
    while a dedicated starting slot is open, reduced weight once only
    FLEX-eligible room remains, low (bench-only) weight otherwise. Covers
    every position this league actually rosters -- not hardcoded to
    OFFENSE_POSITIONS -- so DEF/K (real dedicated slots, never
    flex-eligible) get weighted the same way a real offense position
    does, rather than being silently unpickable (they score `vor * 0.0`
    forever if absent from this dict, since the caller looks them up via
    `weights.get(position, 0.0)`). Not a replacement for
    roster.marginal_lineup_values -- that still scores each trial's FINAL
    roster (see Task 3's simulate_snake_plan); this only steers the
    in-simulation pick, where the exact version is too expensive to run
    at every pick of every trial.
    """
    pos_counts: dict[str, int] = {}
    for vp in sim_roster.values():
        pos_counts[vp.profile.position] = pos_counts.get(vp.profile.position, 0) + 1

    # Every literal position this league has a dedicated slot for.
    # OFFENSE_POSITIONS is always included even at zero dedicated slots
    # (e.g. no dedicated QB slot, QB only via superflex), so those
    # positions still get a real bench-tier weight instead of being
    # absent from the dict entirely.
    dedicated_positions = frozenset(
        slot for slot in league.roster_positions
        if slot not in FLEX_ELIGIBILITY and slot != "BN"
    ) | OFFENSE_POSITIONS
    dedicated_counts = {pos: league.roster_positions.count(pos) for pos in dedicated_positions}

    flex_positions = frozenset(
        pos for slot in league.roster_positions if slot in FLEX_ELIGIBILITY
        for pos in FLEX_ELIGIBILITY[slot]
    )
    flex_total = sum(1 for slot in league.roster_positions if slot in FLEX_ELIGIBILITY)
    flex_used = sum(max(0, pos_counts.get(pos, 0) - dedicated_counts.get(pos, 0))
                    for pos in flex_positions)
    flex_open = flex_total - flex_used

    weights: dict[str, float] = {}
    for pos in dedicated_positions:
        dedicated_open = dedicated_counts[pos] - min(pos_counts.get(pos, 0), dedicated_counts[pos])
        if dedicated_open > 0:
            weights[pos] = 1.0
        elif pos in flex_positions and flex_open > 0:
            weights[pos] = 0.85
        else:
            weights[pos] = 0.15
    return weights


def _current_starting_vor(
    state: DraftState, valued: Mapping[str, ValuedPlayer], league, roster_id: int | None,
) -> float:
    your_roster = {p.player_id: valued[p.player_id] for p in state.picks
                   if p.roster_id == roster_id and p.player_id in valued}
    return roster_engine.team_lineup(your_roster, league).starting_vor


def simulate_snake_plan(
    valued: Mapping[str, ValuedPlayer],
    adp: Mapping[str, float],
    state: DraftState,
    league,
    roster_id: int | None,
    *,
    sims: int = 200,
    tau: float = 8.0,
    rng: np.random.Generator | None = None,
) -> dict | None:
    """Roll the rest of the draft forward `sims` times. At each of YOUR
    future picks, take the cheap need-weighted-VOR choice (_need_weights);
    each stretch of opponent picks in between is removed in one batched
    Gumbel-max draw (market.gone_this_stretch), the same mechanism
    simulate_survival already uses. Returns None if your draft slot can't
    be determined yet -- no result before your first real pick.
    """
    your_draft_slot = _your_draft_slot(state, roster_id)
    if your_draft_slot is None:
        return None

    rng = rng or np.random.default_rng()
    your_picks_made = sum(1 for p in state.picks if p.roster_id == roster_id)
    your_future_pick_nos = [
        _pick_no_for(r, your_draft_slot, league.num_teams)
        for r in range(your_picks_made + 1, league.roster_size + 1)
    ]
    if not your_future_pick_nos:
        return {
            "picks": [],
            "expected_starting_vor": round(_current_starting_vor(state, valued, league, roster_id), 1),
            "sims_run": 0,
        }

    drafted = state.drafted_player_ids()
    available_ids = [pid for pid in valued if pid not in drafted]
    your_current_roster = {p.player_id: valued[p.player_id] for p in state.picks
                           if p.roster_id == roster_id and p.player_id in valued}
    next_pick_no = max((p.pick_no for p in state.picks), default=0) + 1

    position_tallies = [Counter() for _ in your_future_pick_nos]
    player_tallies = [Counter() for _ in your_future_pick_nos]
    final_vors: list[float] = []

    for _ in range(sims):
        sim_available = set(available_ids)
        sim_roster = dict(your_current_roster)
        cursor = next_pick_no

        for i, your_pick_no in enumerate(your_future_pick_nos):
            gap = your_pick_no - cursor
            if gap > 0:
                gone = gone_this_stretch(list(sim_available), adp, gap, tau, rng)
                sim_available -= gone
            if not sim_available:
                break

            weights = _need_weights(sim_roster, league)
            best_id = max(
                sim_available,
                key=lambda pid: valued[pid].vor * weights.get(valued[pid].profile.position, 0.0),
            )
            sim_roster[best_id] = valued[best_id]
            sim_available.discard(best_id)
            position_tallies[i][valued[best_id].profile.position] += 1
            player_tallies[i][best_id] += 1
            cursor = your_pick_no + 1

        final_vors.append(roster_engine.team_lineup(sim_roster, league).starting_vor)

    picks = []
    for i, pick_no in enumerate(your_future_pick_nos):
        if not position_tallies[i]:
            continue
        top_pos, pos_n = position_tallies[i].most_common(1)[0]
        top_player, player_n = player_tallies[i].most_common(1)[0]
        picks.append({
            "pick_no": pick_no,
            "picks_from_now": i + 1,
            "most_likely_position": top_pos,
            "position_hit_rate": round(pos_n / sims, 3),
            "most_likely_player_id": top_player,
            "most_likely_player_name": valued[top_player].profile.full_name,
            "player_hit_rate": round(player_n / sims, 3),
        })

    return {
        "picks": picks,
        "expected_starting_vor": round(sum(final_vors) / len(final_vors), 1) if final_vors else 0.0,
        "sims_run": sims,
    }
