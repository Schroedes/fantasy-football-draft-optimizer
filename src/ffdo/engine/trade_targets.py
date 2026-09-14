"""Ranks specific players on another team worth trading for, paired with a
value-matched offer package from your own roster -- the piece Phase 1
(engine/roster_needs.py) deliberately deferred: naming players, not just
positions. See docs/superpowers/specs/2026-09-14-trade-targets-design.md.

`already_selected_yours`/`already_selected_theirs` (player ids already
checked in an in-progress Trade Machine session) are folded into each
team's roster BEFORE target/offer positions are chosen -- your_hypo and
their_hypo below -- the same substitution pattern
roster_needs.position_needs already uses for its before/after preview.
Critically, each side's needs are still scored against every OTHER team's
REAL, frozen state (position_needs only ever substitutes the one entry
being scored into `all_rosters`): computing your needs does not see
their_hypo, and computing their needs does not see your_hypo. This matches
Phase 1's established invariant -- see roster_needs.position_needs's
docstring -- so a hypothetical change on one side never leaks into the
other side's own rank via a side channel.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from ffdo.domain.models import DraftPickAsset, RosterEntry, ValuedPlayer
from ffdo.engine import power_ranking, roster_needs
from ffdo.engine import pick_value
from ffdo.engine.trade_value import evaluate_trade

_POSITIONS = ("QB", "RB", "WR", "TE")
_NEEDY = ("Moderate", "Severe")
_FAIR_MAGNITUDE = 20.0  # same "Fair trade"/"Slight edge" bound as the Trade Machine scoreboard (season.js's tbScoreboardHTML)
_MAX_OFFER_PLAYERS = 2


@dataclass(frozen=True, slots=True)
class TradeSuggestion:
    partner_roster_id: int
    partner_team_name: str
    target_player_ids: tuple[str, ...]
    offer_player_ids: tuple[str, ...]
    offer_picks: tuple[DraftPickAsset, ...]  # empty for redraft/keeper leagues
    target_value: float
    offer_value: float
    net_value_gain: float  # your total roster value: after - before
    differential: float    # target_value - offer_value, for display
    target_position: str
    offer_position: str


def _players_at_position(entry: RosterEntry, valued: Mapping[str, ValuedPlayer], position: str) -> list[str]:
    return [pid for pid in entry.player_ids if pid in valued and valued[pid].profile.position == position]


def _best_at_position(entry: RosterEntry, valued: Mapping[str, ValuedPlayer], position: str) -> str | None:
    candidates = _players_at_position(entry, valued, position)
    if not candidates:
        return None
    return max(candidates, key=lambda pid: valued[pid].vor)


def _build_offer_package(
    candidates: list[str],
    target_value: float,
    valued: Mapping[str, ValuedPlayer],
    your_picks: Sequence[DraftPickAsset],
    pick_curve: Mapping[int, Mapping],
    current_season: int,
    round_size: int,
) -> tuple[tuple[str, ...], tuple[DraftPickAsset, ...], float] | None:
    """Grows an offer package -- single best player, then a bridging pick
    (dynasty/keeper only), then a second player -- stopping as soon as the
    raw value gap against `target_value` is inside the fair band. Returns
    None if nothing within the 2-player cap closes the gap (spec Step 3)."""
    if not candidates:
        return None
    ranked = sorted(candidates, key=lambda pid: -valued[pid].vor)
    offer_players = [ranked[0]]
    offer_picks: list[DraftPickAsset] = []

    def _value_and_gap() -> tuple[float, float]:
        value = sum(valued[pid].vor for pid in offer_players) + sum(
            pick_value.slot_value(p, pick_curve, current_season=current_season,
                                  round_size=round_size) for p in offer_picks)
        return value, abs(target_value - value)

    value, gap = _value_and_gap()
    if gap < _FAIR_MAGNITUDE:
        return tuple(offer_players), tuple(offer_picks), value

    if your_picks:
        best_pick = max(your_picks, key=lambda p: pick_value.slot_value(
            p, pick_curve, current_season=current_season, round_size=round_size))
        offer_picks.append(best_pick)
        value, gap = _value_and_gap()
        if gap < _FAIR_MAGNITUDE:
            return tuple(offer_players), tuple(offer_picks), value

    if len(ranked) >= _MAX_OFFER_PLAYERS:
        offer_players.append(ranked[1])
        value, gap = _value_and_gap()
        if gap < _FAIR_MAGNITUDE:
            return tuple(offer_players), tuple(offer_picks), value

    return None


def _net_value_gain(
    your_roster: RosterEntry,
    target_ids: tuple[str, ...],
    offer_ids: tuple[str, ...],
    offer_picks: Sequence[DraftPickAsset],
    free_agent_ids: Iterable[str],
    valued: Mapping[str, ValuedPlayer],
    league,
    *,
    pick_curve: Mapping[int, Mapping],
    current_season: int,
    round_size: int,
) -> float:
    """Your total roster value after the trade minus before, backfilling a
    freed roster spot (offering more players than you receive) with the
    single best available free agent by raw VOR (spec Step 4) -- one FA
    regardless of how many spots actually opened, per the spec's own
    simplification."""
    before_value, _ = power_ranking.team_value(your_roster, valued, league, position="OVR", scope="full")
    after_ids = (set(your_roster.player_ids) - set(offer_ids)) | set(target_ids)
    if len(offer_ids) > len(target_ids):
        valued_fas = [pid for pid in free_agent_ids if pid in valued]
        if valued_fas:
            best_fa = max(valued_fas, key=lambda pid: valued[pid].vor)
            after_ids = after_ids | {best_fa}
    after_roster = replace(your_roster, player_ids=tuple(after_ids))
    after_value, _ = power_ranking.team_value(after_roster, valued, league, position="OVR", scope="full")
    pick_cost = sum(pick_value.slot_value(p, pick_curve, current_season=current_season,
                                          round_size=round_size) for p in offer_picks)
    return (after_value - before_value) - pick_cost


def suggest_for_team(
    your_roster: RosterEntry,
    their_roster: RosterEntry,
    all_rosters: list[RosterEntry],
    valued: Mapping[str, ValuedPlayer],
    league,
    free_agent_ids: Iterable[str],
    your_picks: Sequence[DraftPickAsset],
    *,
    pick_curve: Mapping[int, Mapping],
    current_season: int,
    round_size: int,
    already_selected_yours: frozenset[str] = frozenset(),
    already_selected_theirs: frozenset[str] = frozenset(),
) -> list[TradeSuggestion]:
    your_hypo = replace(your_roster, player_ids=tuple(
        (set(your_roster.player_ids) - already_selected_yours) | already_selected_theirs))
    their_hypo = replace(their_roster, player_ids=tuple(
        (set(their_roster.player_ids) - already_selected_theirs) | already_selected_yours))

    your_needs = roster_needs.position_needs(your_hypo, all_rosters, valued, league)
    their_needs = roster_needs.position_needs(their_hypo, all_rosters, valued, league)

    target_positions = [
        pos for pos in _POSITIONS
        if your_needs[pos].severity in _NEEDY and their_needs[pos].severity == "Fine"
        and len(_players_at_position(their_hypo, valued, pos)) >= 2
    ]
    offer_positions = [
        pos for pos in _POSITIONS
        if your_needs[pos].severity == "Fine" and their_needs[pos].severity in _NEEDY
        and len(_players_at_position(your_hypo, valued, pos)) >= 2
    ]
    if not target_positions or not offer_positions:
        return []

    free_agent_id_list = list(free_agent_ids)
    suggestions: list[TradeSuggestion] = []
    for target_position in target_positions:
        target_id = _best_at_position(their_hypo, valued, target_position)
        if target_id is None:
            continue
        target_ids = (target_id,)
        target_value = valued[target_id].vor

        for offer_position in offer_positions:
            candidates = _players_at_position(your_hypo, valued, offer_position)
            built = _build_offer_package(
                candidates, target_value, valued, your_picks, pick_curve,
                current_season, round_size)
            if built is None:
                continue
            offer_ids, offer_picks, offer_value = built

            evaluation = evaluate_trade(
                {"player_ids": list(target_ids), "picks": []},
                {"player_ids": list(offer_ids), "picks": list(offer_picks)},
                valued_players=valued, pick_curve=pick_curve,
                current_season=current_season, round_size=round_size)
            if abs(evaluation.differential) >= _FAIR_MAGNITUDE:
                continue

            net_gain = _net_value_gain(
                your_hypo, target_ids, offer_ids, offer_picks, free_agent_id_list, valued, league,
                pick_curve=pick_curve, current_season=current_season, round_size=round_size)
            if net_gain <= 0:
                continue

            suggestions.append(TradeSuggestion(
                partner_roster_id=their_roster.roster_id,
                partner_team_name=their_roster.team_name,
                target_player_ids=target_ids,
                offer_player_ids=offer_ids,
                offer_picks=tuple(offer_picks),
                target_value=round(target_value, 1),
                offer_value=round(offer_value, 1),
                net_value_gain=round(net_gain, 1),
                differential=round(target_value - offer_value, 1),
                target_position=target_position,
                offer_position=offer_position,
            ))
            break  # one suggestion per target -- first offer position that clears both filters

    suggestions.sort(key=lambda s: -s.net_value_gain)
    return suggestions
