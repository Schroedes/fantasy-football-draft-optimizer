# Proactive Trade Targets (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Identify specific players on other teams worth trading for, paired
with a value-matched offer package, and surface them two ways: always-visible
"Suggested additions" inside the Trade Machine modal while building a real
trade (C), and a new league-wide "Targets" season-screen tab with nothing
pre-selected (D).

**Architecture:** One new engine module, `engine/trade_targets.py`, holds all
new logic (`suggest_for_team`) and is the single source of truth both new
endpoints compose. It reuses Phase 1's `roster_needs.position_needs` for
weakness/surplus scoring, `power_ranking.team_value` for total-roster-value
comparisons, and `trade_value.evaluate_trade` for the fairness check —
zero new valuation math. Two thin FastAPI endpoints adapt it to (C)'s
in-progress-trade context and (D)'s cold-start browse. The frontend adds a
panel to the existing Trade Machine modal (C) and a new tab (D), both in
`season.js`/`season.css`, following the exact conventions Trade Machine and
Roster Needs Phase 1 already established there.

**Tech Stack:** Python (FastAPI backend, pytest), vanilla JS/CSS frontend
(no framework, matches `season.js`'s existing house style).

**Spec:** [docs/superpowers/specs/2026-09-14-trade-targets-design.md](../specs/2026-09-14-trade-targets-design.md)

## Global Constraints

- Fairness band: a pairing's raw value differential (target vs. offer, via
  `trade_value.evaluate_trade`) must have magnitude < 20 to surface — the
  same threshold the Trade Machine scoreboard already uses for "Fair
  trade"/"Slight edge" (`season.js`'s `tbScoreboardHTML`). "Lopsided"
  (magnitude ≥ 20) is always rejected.
- Offer packages cap at 2 players. A draft pick may be added as a bridging
  asset (dynasty/keeper only) but does not count against that cap.
- Net value filter: your total roster value after the trade (with the
  single best available free agent backfilled into any freed roster spot)
  must strictly exceed your total roster value before it. Ties (gain ≤ 0)
  are rejected, not just losses.
- `PICK_VALUE_CURVE` (sub-project #5) is reused as-is — no new pick
  valuation model in this phase.
- Both endpoints must compose the same `trade_targets.suggest_for_team` —
  no duplicated target/offer-selection logic between (C) and (D).
- Frontend changes are verified live against a real tracked Sleeper league
  (`ffdo-api` dev server, port 8150) — this codebase's established pattern
  of catching the most serious bugs via manual smoke tests, not fixtures
  alone.

## Design decisions beyond the spec's illustrative signatures

The spec's Data Model section sketches `suggest_for_team`'s signature and
`TradeSuggestion`'s fields illustratively, not literally. This plan locks in
the following concretizations — implementers should treat these as settled,
not as contradictions to flag against the spec:

- `suggest_for_team` additionally takes `all_rosters` (needed to call
  `roster_needs.position_needs`, exactly as Phase 1 required it) and
  `pick_curve`/`current_season`/`round_size` (needed by `evaluate_trade`
  and `pick_value.slot_value`). The spec's `all_picks_by_roster` becomes
  `your_picks: Sequence[DraftPickAsset]` — only your own team's picks are
  ever offered (spec §3), so there's no need to pass every team's picks in.
- `TradeSuggestion.offer_pick_labels: tuple[str, ...]` becomes
  `offer_picks: tuple[DraftPickAsset, ...]` — the full asset, not just its
  label. The frontend's one-click "Add to trade" (spec's UI §(C)) needs a
  pick's `season`/`round`/`original_roster_id` to reconstruct the exact
  selection key `season.js` already uses elsewhere (`_tbSelectionToPayload`
  and `tbRowHTML`'s `pick:<season>:<round>:<original_roster_id>` format,
  see `src/ffdo/web/season/season.js:733-736`) — a label string alone
  can't do that, and two different picks can share a label (e.g. two
  different teams' "R1" picks).
- `TradeSuggestion` gains `target_position: str` and `offer_position: str`
  — structural facts already computed inside `suggest_for_team`, needed by
  the API layer to compose the "why" line the spec's UI §(D) shows (e.g.
  "Fills your TE need...").

## Task 1: `engine/trade_targets.py` — the suggestion algorithm

**Files:**
- Create: `src/ffdo/engine/trade_targets.py`
- Test: `tests/engine/test_trade_targets.py`

**Interfaces:**
- Consumes: `ffdo.domain.models.{DraftPickAsset, RosterEntry, ValuedPlayer}`;
  `ffdo.engine.roster_needs.position_needs(entry, all_rosters, valued, league) -> dict[str, NeedScore]`
  where `NeedScore` has `.rank: int` and `.severity: "Fine"|"Moderate"|"Severe"`;
  `ffdo.engine.power_ranking.team_value(entry, valued, league, *, position, scope) -> tuple[float, float]`
  (returns `(value, bench_value)` — for `position="OVR", scope="full"`,
  `value` is already the team's full starting+bench VOR total, confirmed
  by reading `src/ffdo/engine/power_ranking.py:19-34` and
  `src/ffdo/engine/roster.py:25-49`: `starting_vor + bench_vor` always
  equals the raw sum of every rostered valued player's `.vor`, regardless
  of `league.starting_slots`);
  `ffdo.engine.trade_value.evaluate_trade(side_a, side_b, *, valued_players, pick_curve, current_season, round_size) -> TradeEvaluation`
  (`side_a`/`side_b` are `{"player_ids": [...], "picks": [...]}`;
  `TradeEvaluation.differential = side_a_value - side_b_value`);
  `ffdo.engine.pick_value.slot_value(pick, curve, *, current_season, round_size) -> float`.
- Produces: `TradeSuggestion` dataclass and
  `suggest_for_team(your_roster, their_roster, all_rosters, valued, league, free_agent_ids, your_picks, *, pick_curve, current_season, round_size, already_selected_yours=frozenset(), already_selected_theirs=frozenset()) -> list[TradeSuggestion]`
  — consumed by Task 2's endpoints.

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/test_trade_targets.py`:

```python
from dataclasses import replace

import pytest

from ffdo.domain.models import DraftPickAsset, LeagueProfile, PlayerProfile, RosterEntry, ValuedPlayer
from ffdo.engine import trade_targets


def _league(roster_positions, num_teams=2):
    return LeagueProfile(league_id="x", season=2026, num_teams=num_teams,
                         roster_positions=roster_positions, scoring_settings={}, budget=200)


def _vp(pid, pos, v):
    prof = PlayerProfile(player_id=pid, first_name=pos, last_name=pid, position=pos,
                         team="X", age=26, years_exp=4, injury_status=None, active=True)
    return ValuedPlayer(profile=prof, projected_points=v, adjusted_points=v,
                        vor=v, tier=1, adjustments={})


def _entry(rid, players):
    return RosterEntry(roster_id=rid, team_name=f"T{rid}", player_ids=tuple(players),
                       starter_ids=(), wins=0, losses=0, ties=0,
                       points_for=0.0, points_against=0.0)


def _pick(season, rnd, roster_id):
    return DraftPickAsset(season=season, round=rnd, projected_slot=None,
                          current_owner_roster_id=roster_id, original_roster_id=roster_id,
                          via_team_name=None)


# Flat, hand-verifiable curve: any round-1 pick with projected_slot=None and
# season == current_season (years_out == 0, discount factor 1.0) values at
# exactly 15.0 -- avoids needing PICK_UNCERTAINTY_DISCOUNT_RATE's real value.
_CURVE = {1: {"round_avg": 15.0}}


# ---- _build_offer_package: the value-matching escalation (spec Step 3) ----

def test_build_offer_package_single_player_passes_when_already_fair():
    valued = {"p1": _vp("p1", "RB", 30)}
    built = trade_targets._build_offer_package(
        ["p1"], target_value=25.0, valued=valued, your_picks=[],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    assert built == (("p1",), (), 30.0)


def test_build_offer_package_adds_a_pick_to_bridge_the_gap():
    valued = {"p1": _vp("p1", "RB", 30)}
    pick = _pick(2026, 1, 1)  # slot_value == 15.0 under _CURVE
    built = trade_targets._build_offer_package(
        ["p1"], target_value=50.0, valued=valued, your_picks=[pick],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    # single player alone: |50-30|=20, not < 20 -- fails.
    # +pick: |50-45|=5 < 20 -- passes.
    assert built == (("p1",), (pick,), 45.0)


def test_build_offer_package_adds_a_second_player_when_no_picks_available():
    # Redraft leagues always pass your_picks=[] (the dynasty-vs-redraft
    # branch) -- the pick-bridging step must simply be skipped, not error.
    valued = {"p1": _vp("p1", "RB", 30), "p2": _vp("p2", "RB", 25)}
    built = trade_targets._build_offer_package(
        ["p1", "p2"], target_value=70.0, valued=valued, your_picks=[],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    # single (p1=30): |70-30|=40, fails. No picks -- skip. +p2: 55, |70-55|=15<20 -- passes.
    assert built == (("p1", "p2"), (), 55.0)


def test_build_offer_package_drops_the_pairing_when_nothing_closes_the_gap():
    valued = {"p1": _vp("p1", "RB", 30), "p2": _vp("p2", "RB", 25)}
    pick = _pick(2026, 1, 1)
    built = trade_targets._build_offer_package(
        ["p1", "p2"], target_value=200.0, valued=valued, your_picks=[pick],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    assert built is None


def test_build_offer_package_returns_none_with_no_candidates():
    assert trade_targets._build_offer_package(
        [], target_value=25.0, valued={}, your_picks=[],
        pick_curve=_CURVE, current_season=2026, round_size=10) is None


# ---- _net_value_gain: total roster value before vs. after, with waiver backfill (spec Step 4) ----

_NV_LEAGUE = _league(("QB", "RB", "WR", "BN"))


def test_net_value_gain_positive_with_no_backfill_needed():
    valued = {"t1": _vp("t1", "RB", 40), "o1": _vp("o1", "RB", 20)}
    roster = _entry(1, ["o1"])
    gain = trade_targets._net_value_gain(
        roster, target_ids=("t1",), offer_ids=("o1",),
        free_agent_ids=[], valued=valued, league=_NV_LEAGUE)
    # before: 20 (o1). after: 40 (t1, o1 removed, no spot freed). gain = 20.
    assert gain == pytest.approx(20.0)


def test_net_value_gain_backfills_a_freed_spot_with_the_best_free_agent():
    # 2 players offered for 1 targeted -- one spot opens, backfilled with
    # the single best free agent by raw VOR (spec: "the single best
    # available free agent", not one per spot freed).
    valued = {
        "t1": _vp("t1", "RB", 30),
        "o1": _vp("o1", "RB", 20), "o2": _vp("o2", "WR", 15),
        "fa1": _vp("fa1", "WR", 5), "fa2": _vp("fa2", "RB", 8),
    }
    roster = _entry(1, ["o1", "o2"])
    gain = trade_targets._net_value_gain(
        roster, target_ids=("t1",), offer_ids=("o1", "o2"),
        free_agent_ids=["fa1", "fa2"], valued=valued, league=_NV_LEAGUE)
    # before: 20+15=35. after: 30 (t1) + best FA (fa2=8) = 38. gain = 3.
    assert gain == pytest.approx(3.0)


def test_net_value_gain_negative_when_offer_outvalues_target():
    valued = {"t1": _vp("t1", "RB", 10), "o1": _vp("o1", "RB", 30)}
    roster = _entry(1, ["o1"])
    gain = trade_targets._net_value_gain(
        roster, target_ids=("t1",), offer_ids=("o1",),
        free_agent_ids=[], valued=valued, league=_NV_LEAGUE)
    assert gain == pytest.approx(-20.0)


# ---- suggest_for_team: the full two-way-fit + dual-filter pipeline ----
#
# 2-team league, no FLEX (QB/RB/RB/WR/WR/TE dedicated + bench). Both teams
# roster exactly 1 QB and 1 TE -- _thinness's capped-position formula gives
# count=1 a floor of 1.0, so QB/TE never read as "Fine" for either team
# (score >= 0.5*1.0 = 0.5 regardless of rank) and never qualify as a target
# or offer position. RB is your surplus / their need; WR is your need /
# their surplus -- the two-way fit the algorithm should find.
_POS_LEAGUE = _league(("QB", "RB", "RB", "WR", "WR", "TE", "BN", "BN", "BN", "BN", "BN"))
_POS_VALUED = {
    "q1": _vp("q1", "QB", 20), "q2": _vp("q2", "QB", 15),
    "r1a": _vp("r1a", "RB", 30), "r1b": _vp("r1b", "RB", 25), "r1c": _vp("r1c", "RB", 5),
    "r2a": _vp("r2a", "RB", 10), "r2b": _vp("r2b", "RB", 8),
    "w1a": _vp("w1a", "WR", 8), "w1b": _vp("w1b", "WR", 6),
    "w2a": _vp("w2a", "WR", 32), "w2b": _vp("w2b", "WR", 20), "w2c": _vp("w2c", "WR", 5),
    "t1": _vp("t1", "TE", 8), "t2": _vp("t2", "TE", 6),
}
_YOUR = _entry(1, ["q1", "r1a", "r1b", "r1c", "w1a", "w1b", "t1"])
_THEIRS = _entry(2, ["q2", "r2a", "r2b", "w2a", "w2b", "w2c", "t2"])
_ALL_ROSTERS = [_YOUR, _THEIRS]


def test_suggest_for_team_picks_the_two_way_fit_and_passes_both_filters():
    suggestions = trade_targets.suggest_for_team(
        _YOUR, _THEIRS, _ALL_ROSTERS, _POS_VALUED, _POS_LEAGUE,
        free_agent_ids=[], your_picks=[],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s.target_position == "WR"
    assert s.offer_position == "RB"
    assert s.target_player_ids == ("w2a",)   # their best WR: your worst-need position, their surplus
    assert s.offer_player_ids == ("r1a",)    # your best RB: your surplus, their need
    assert s.offer_picks == ()
    assert s.target_value == pytest.approx(32.0)
    assert s.offer_value == pytest.approx(30.0)
    assert s.net_value_gain == pytest.approx(2.0)
    assert s.differential == pytest.approx(2.0)
    assert s.partner_roster_id == 2
    assert s.partner_team_name == "T2"


def test_suggest_for_team_rejects_a_fair_but_value_negative_pairing():
    # |25-30|=5 is well within the fair band, but giving away your
    # more-valuable RB for a less-valuable WR makes your team worse --
    # net_value_gain must reject it regardless of fairness.
    valued = dict(_POS_VALUED)
    valued["w2a"] = _vp("w2a", "WR", 25)
    suggestions = trade_targets.suggest_for_team(
        _YOUR, _THEIRS, _ALL_ROSTERS, valued, _POS_LEAGUE,
        free_agent_ids=[], your_picks=[],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    assert suggestions == []


def test_suggest_for_team_rejects_a_value_positive_but_lopsided_pairing():
    # Their target WR is worth far more than anything at RB you could
    # offer (single best + a second player still isn't close) -- net value
    # would be positive, but no fair-band package can be built, so no
    # pick-bridge exists here (redraft-shaped: your_picks=[]) and the
    # pairing must be dropped.
    valued = dict(_POS_VALUED)
    valued["w2a"] = _vp("w2a", "WR", 90)
    suggestions = trade_targets.suggest_for_team(
        _YOUR, _THEIRS, _ALL_ROSTERS, valued, _POS_LEAGUE,
        free_agent_ids=[], your_picks=[],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    assert suggestions == []


def test_suggest_for_team_includes_a_bridging_pick_for_dynasty_leagues():
    # A single RB alone (30) isn't fair against a 52-value target
    # (gap=22 >= 20); adding the pick (15) closes it (gap=7 < 20). This is
    # the dynasty-vs-redraft branch actually reachable end-to-end: with
    # your_picks=[] (redraft) this same target would fall through to
    # test_suggest_for_team_rejects_a_value_positive_but_lopsided_pairing's
    # style rejection instead, since the escalation has no pick to reach for.
    valued = dict(_POS_VALUED)
    valued["w2a"] = _vp("w2a", "WR", 52)
    pick = _pick(2026, 1, 1)
    suggestions = trade_targets.suggest_for_team(
        _YOUR, _THEIRS, _ALL_ROSTERS, valued, _POS_LEAGUE,
        free_agent_ids=[], your_picks=[pick],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s.offer_player_ids == ("r1a",)
    assert s.offer_picks == (pick,)
    assert s.offer_value == pytest.approx(45.0)
    assert s.target_value == pytest.approx(52.0)
    assert s.net_value_gain == pytest.approx(7.0)


def test_suggest_for_team_folds_already_selected_into_the_hypothetical_roster():
    # w2a (their best WR, the position's usual target) is already checked
    # in the in-progress trade. Once excluded from their candidate pool,
    # their remaining WR depth (w2b=20, w2c=5) sits exactly at
    # starting_reach with no cushion left -- thinness=1.0 pushes their WR
    # need to Moderate, so WR stops qualifying as a target position
    # (target requires the partner to read as Fine/surplus there).
    # already_selected must be folded in before target/offer positions are
    # chosen, not applied as a post-hoc filter on the result.
    suggestions = trade_targets.suggest_for_team(
        _YOUR, _THEIRS, _ALL_ROSTERS, _POS_VALUED, _POS_LEAGUE,
        free_agent_ids=[], your_picks=[],
        pick_curve=_CURVE, current_season=2026, round_size=10,
        already_selected_theirs=frozenset({"w2a"}))
    assert suggestions == []


def test_suggest_for_team_returns_empty_with_no_two_way_fit():
    # Neither team has any surplus position (everyone rosters exactly
    # their starters, nothing more) -- the >=2-rostered gate excludes
    # every position from both target_positions and offer_positions.
    league = _league(("QB", "RB", "WR", "TE", "BN"))
    valued = {
        "q1": _vp("q1", "QB", 20), "r1": _vp("r1", "RB", 15),
        "w1": _vp("w1", "WR", 10), "t1": _vp("t1", "TE", 5),
        "q2": _vp("q2", "QB", 12), "r2": _vp("r2", "RB", 8),
        "w2": _vp("w2", "WR", 6), "t2": _vp("t2", "TE", 3),
    }
    your_roster = _entry(1, ["q1", "r1", "w1", "t1"])
    their_roster = _entry(2, ["q2", "r2", "w2", "t2"])
    suggestions = trade_targets.suggest_for_team(
        your_roster, their_roster, [your_roster, their_roster], valued, league,
        free_agent_ids=[], your_picks=[],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    assert suggestions == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_trade_targets.py -v`
Expected: FAIL with `ModuleNotFoundError` / `AttributeError` (module doesn't exist yet).

- [ ] **Step 3: Write the implementation**

Create `src/ffdo/engine/trade_targets.py`:

```python
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
    free_agent_ids: Iterable[str],
    valued: Mapping[str, ValuedPlayer],
    league,
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
    return after_value - before_value


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
                your_hypo, target_ids, offer_ids, free_agent_id_list, valued, league)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_trade_targets.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/engine/trade_targets.py tests/engine/test_trade_targets.py
git commit -m "feat: add engine/trade_targets.py -- ranked player-level trade suggestions

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

## Task 2: API endpoints

**Files:**
- Modify: `src/ffdo/api/app.py:211` (module imports), and insert two new
  endpoints between `get_trade_builder` (ends `src/ffdo/api/app.py:1700`
  with `return {"teams": teams}`) and `get_trades`
  (`src/ffdo/api/app.py:1702`, `@app.get("/api/leagues/{league_key}/trades")`)
- Test: `tests/api/test_trade_endpoints.py`

**Interfaces:**
- Consumes: Task 1's `trade_targets.suggest_for_team` and `TradeSuggestion`
  (`.target_player_ids`, `.offer_player_ids`, `.offer_picks`,
  `.target_value`, `.offer_value`, `.net_value_gain`, `.differential`,
  `.target_position`, `.offer_position`, `.partner_roster_id`,
  `.partner_team_name`); the existing `get_trade_builder` endpoint's fetch
  pattern (`src/ffdo/api/app.py:1605-1700`) as a model to adapt, not share
  (this codebase's existing endpoints each independently fetch what they
  need -- `evaluate_trade_endpoint` and `get_trade_builder` already don't
  share a helper despite near-identical fetch logic; this task follows
  that same convention rather than introducing a new shared abstraction);
  `waiver_value_mod.free_agents(all_player_ids: Iterable[str], rosters: Sequence) -> set[str]`
  (already imported in `create_app` as `waiver_value_mod`).
- Produces: `POST /api/leagues/{league_key}/trade/suggestions` and
  `GET /api/leagues/{league_key}/trade-targets`, consumed by Task 3 and
  Task 4's frontend fetches respectively.

- [ ] **Step 1: Add the `trade_targets` import**

In `src/ffdo/api/app.py`, immediately after line 211
(`from ffdo.engine import trade_value as trade_value_mod`), add:

```python
    from ffdo.engine import trade_targets as trade_targets_mod
```

- [ ] **Step 2: Write the failing tests**

In `tests/api/test_trade_endpoints.py`, add after
`test_trade_builder_is_sleeper_only` (currently ending at line 136):

```python
def test_trade_suggestions_is_sleeper_only():
    from ffdo.domain.models import TrackedLeague

    app_mod._STORE.upsert(_tracked(
        league_key="espn:E2:2026", provider="espn", provider_league_id="E2"))
    body = {"partner_roster_id": 2, "side_a": {"player_ids": [], "picks": []},
            "side_b": {"player_ids": [], "picks": []}}
    res = TestClient(create_app()).post("/api/leagues/espn:E2:2026/trade/suggestions", json=body)
    assert res.status_code == 400


def test_trade_targets_is_sleeper_only():
    from ffdo.domain.models import TrackedLeague

    app_mod._STORE.upsert(_tracked(
        league_key="espn:E3:2026", provider="espn", provider_league_id="E3"))
    res = TestClient(create_app()).get("/api/leagues/espn:E3:2026/trade-targets")
    assert res.status_code == 400


def test_trade_suggestions_returns_empty_list_when_no_team_has_surplus_depth(monkeypatch, tmp_path):
    # The shared _ROSTERS fixture rosters exactly 1 player per position per
    # team (see test_season_endpoint.py) -- no position anywhere clears the
    # >=2-rostered surplus gate, so suggest_for_team deterministically
    # returns []. This is a real, meaningful assertion (confirms the
    # >=2-rostered gate works end-to-end through the real API and
    # valuation pipeline), not a placeholder.
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
    }

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            for key, val in resp.items():
                if key in url:
                    return val
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeClient)
    client = TestClient(create_app())
    body = {"partner_roster_id": 2,
            "side_a": {"player_ids": [], "picks": []},
            "side_b": {"player_ids": [], "picks": []}}
    res = client.post("/api/leagues/sleeper:L1:2026/trade/suggestions", json=body)
    assert res.status_code == 200
    assert res.json() == {"suggestions": []}

    res2 = client.get("/api/leagues/sleeper:L1:2026/trade-targets")
    assert res2.status_code == 200
    assert res2.json() == {"suggestions": []}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_trade_endpoints.py -v`
Expected: FAIL (404s -- the endpoints don't exist yet).

- [ ] **Step 4: Write the implementation**

In `src/ffdo/api/app.py`, insert immediately after line 1700
(`return {"teams": teams}`, the end of `get_trade_builder`) and before line
1702 (`@app.get("/api/leagues/{league_key}/trades")`):

```python
    def _load_trade_targets_context(lg):
        """Rosters, valuations, player profiles, free-agent ids, and your
        own team's future picks -- everything both new endpoints below
        need. Mirrors get_trade_builder's fetch pattern; kept separate
        from it (not extracted into a shared helper) since no existing
        endpoint in this file shares fetch logic across endpoints either
        -- each fetches independently. Returns None on a Sleeper outage."""
        sleeper = client_mod.SleeperClient()
        try:
            nfl = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))
            profiles, _espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            proj_anchor = _season_proj_anchor_for(lg.season).get(
                lambda: _load_projection_anchor(sleeper, lg.season))
            rosters = rosters_mod.fetch(sleeper, lg.provider_league_id)
            through_week = _through_week(nfl)
            actuals = actuals_mod.points_so_far(sleeper, lg.provider_league_id, through_week)

            your_picks: list = []
            if lg.resolved_format in ("dynasty", "keeper"):
                worst_to_best = sorted(rosters, key=lambda r: (r.wins, r.points_for))
                try:
                    capital = traded_picks_mod.capital(
                        sleeper, lg.provider_league_id,
                        num_teams=lg.num_teams,
                        rounds=int((lg.raw_settings or {}).get("draft_rounds") or 4),
                        standings_order=[r.roster_id for r in worst_to_best],
                        draft_years=(lg.season + 1, lg.season + 2),
                        team_names={r.roster_id: r.team_name for r in rosters})
                    your_picks = [a for a in capital if a.current_owner_roster_id == lg.roster_id]
                except (httpx.HTTPError, RuntimeError):
                    logging.getLogger("ffdo.api").warning(
                        "trade-targets: traded-picks fetch failed for %s, picks omitted",
                        lg.league_key)
        except (httpx.HTTPError, RuntimeError):
            return None
        finally:
            sleeper.close()

        all_pids = {pid for r in rosters for pid in r.player_ids}
        valued = ros_value_mod.roster_value(
            all_pids, lg, resolved_format=lg.resolved_format, season_proj=proj_anchor,
            profiles=profiles, actuals=actuals, weeks_played=through_week,
            season_weeks=_season_weeks(lg.season))
        free_agent_ids = waiver_value_mod.free_agents(set(profiles.keys()), rosters)

        return rosters, valued, profiles, free_agent_ids, your_picks

    def _suggestion_json(s, valued, profiles) -> dict:
        def _player_row(pid: str) -> dict:
            prof = profiles.get(pid)
            vp = valued.get(pid)
            return {
                "player_id": pid,
                "name": prof.full_name if prof else pid,
                "position": prof.position if prof else "",
                "value": round(vp.vor, 1) if vp is not None else 0.0,
            }

        def _pick_row(pick) -> dict:
            return {"label": pick.label, "season": pick.season, "round": pick.round,
                    "original_roster_id": pick.original_roster_id}

        return {
            "partner_roster_id": s.partner_roster_id,
            "partner_team_name": s.partner_team_name,
            "target_players": [_player_row(pid) for pid in s.target_player_ids],
            "offer_players": [_player_row(pid) for pid in s.offer_player_ids],
            "offer_picks": [_pick_row(p) for p in s.offer_picks],
            "target_value": s.target_value,
            "offer_value": s.offer_value,
            "net_value_gain": s.net_value_gain,
            "differential": s.differential,
            "why": (f"Fills your {s.target_position} need -- they're deep at "
                    f"{s.target_position} and thin at {s.offer_position}, "
                    f"where you have surplus."),
        }

    @app.post("/api/leagues/{league_key}/trade/suggestions")
    def get_trade_suggestions(league_key: str, payload: dict) -> dict:
        """Counter-offer suggestions for the trade partner already selected
        in the live Trade Machine session (spec (C)) -- same request shape
        as POST /trade/evaluate, so whatever's already checked there is
        folded into the hypothetical roster suggest_for_team scores
        against."""
        lg = _load_league(league_key)
        if lg.provider != "sleeper":
            raise HTTPException(status_code=400, detail="Trade suggestions are Sleeper-only for now")

        ctx = _load_trade_targets_context(lg)
        if ctx is None:
            raise HTTPException(status_code=502, detail="Couldn't reach Sleeper, try again")
        rosters, valued, profiles, free_agent_ids, your_picks = ctx

        you_roster = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        partner_roster = next(
            (r for r in rosters if r.roster_id == payload.get("partner_roster_id")), None)
        if you_roster is None or partner_roster is None:
            return {"suggestions": []}

        side_a = payload.get("side_a") or {}
        side_b = payload.get("side_b") or {}
        already_yours = frozenset(side_a.get("player_ids", []))
        already_theirs = frozenset(side_b.get("player_ids", []))

        suggestions = trade_targets_mod.suggest_for_team(
            you_roster, partner_roster, rosters, valued, lg, free_agent_ids, your_picks,
            pick_curve=PICK_VALUE_CURVE, current_season=lg.season, round_size=lg.num_teams,
            already_selected_yours=already_yours, already_selected_theirs=already_theirs)

        return {"suggestions": [_suggestion_json(s, valued, profiles) for s in suggestions]}

    @app.get("/api/leagues/{league_key}/trade-targets")
    def get_trade_targets(league_key: str) -> dict:
        """League-wide "who should I target" browse (spec (D)) -- your real
        current roster against every other team, nothing pre-selected.
        Merges every team's suggest_for_team() output, top 10 by net value
        gain."""
        lg = _load_league(league_key)
        if lg.provider != "sleeper":
            raise HTTPException(status_code=400, detail="Trade targets are Sleeper-only for now")

        ctx = _load_trade_targets_context(lg)
        if ctx is None:
            raise HTTPException(status_code=502, detail="Couldn't reach Sleeper, try again")
        rosters, valued, profiles, free_agent_ids, your_picks = ctx

        you_roster = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        if you_roster is None:
            return {"suggestions": []}

        all_suggestions = []
        for partner_roster in rosters:
            if partner_roster.roster_id == you_roster.roster_id:
                continue
            all_suggestions.extend(trade_targets_mod.suggest_for_team(
                you_roster, partner_roster, rosters, valued, lg, free_agent_ids, your_picks,
                pick_curve=PICK_VALUE_CURVE, current_season=lg.season, round_size=lg.num_teams))

        all_suggestions.sort(key=lambda s: -s.net_value_gain)
        top = all_suggestions[:10]
        return {"suggestions": [_suggestion_json(s, valued, profiles) for s in top]}

```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_trade_endpoints.py -v`
Expected: PASS (all tests, including the pre-existing ones in this file).

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `uv run pytest`
Expected: PASS (no regressions).

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/api/app.py tests/api/test_trade_endpoints.py
git commit -m "feat: add POST /trade/suggestions and GET /trade-targets endpoints

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

## Task 3: Trade Machine "Suggested additions" panel (spec's C)

**Files:**
- Modify: `src/ffdo/web/season/season.js`
- Modify: `src/ffdo/web/season/season.css`

**Interfaces:**
- Consumes: Task 2's `POST /api/leagues/{key}/trade/suggestions` (request
  body identical to the existing `POST /trade/evaluate` call already built
  by `_tbSelectionToPayload`); the existing Trade Machine modal state and
  functions in `season.js` (`_tradeBuilderData`, `_tradeBuilderPartnerId`,
  `_tradeBuilderYourSel`, `_tradeBuilderPartnerSel`, `_tbTeam`,
  `_tbSelectionToPayload`, `scheduleTradeBuilderEvaluate`,
  `renderTradeBuilderModal`, `openTradeBuilder`, `loadTradeBuilder`); the
  existing selection-key format `player:<id>` /
  `pick:<season>:<round>:<original_roster_id>` (`season.js:725-746`).
- Produces: a "Suggested additions" panel always visible once a partner is
  selected, with a per-row "Add to trade" action. Task 4 reuses the same
  selection-key format when it seeds the modal from a Targets-tab click.

- [ ] **Step 1: Add suggestions state**

In `src/ffdo/web/season/season.js`, immediately after line 30
(`let _tradeBuilderEvalTimer = null;`), add:

```js
let _tbSuggestions = null;         // last POST /trade/suggestions result, {error:true}, or null (not loaded yet)
let _tbSuggestionsPending = false;
```

- [ ] **Step 2: Reset the new state on mount**

In `mountSeason()`, immediately after line 90
(`clearTimeout(_tradeBuilderEvalTimer);`), add:

```js
  _tbSuggestions = null;
  _tbSuggestionsPending = false;
```

- [ ] **Step 3: Fetch suggestions alongside the trade evaluation**

Replace the body of `scheduleTradeBuilderEvaluate()` (currently):

```js
function scheduleTradeBuilderEvaluate() {
  _tradeBuilderEvalPending = true;
  clearTimeout(_tradeBuilderEvalTimer);
  _tradeBuilderEvalTimer = setTimeout(evaluateTradeBuilder, 400);
}
```

with:

```js
function scheduleTradeBuilderEvaluate() {
  _tradeBuilderEvalPending = true;
  _tbSuggestionsPending = true;
  clearTimeout(_tradeBuilderEvalTimer);
  _tradeBuilderEvalTimer = setTimeout(() => {
    evaluateTradeBuilder();
    fetchTradeSuggestions();
  }, 400);
}
```

- [ ] **Step 4: Add `fetchTradeSuggestions`**

Immediately after `evaluateTradeBuilder()` (the function ending at line 778
with `renderTradeBuilderModal();\n}`), add:

```js
async function fetchTradeSuggestions() {
  const yourTeam = _tradeBuilderData.teams.find(t => t.is_you);
  const partnerTeam = _tbTeam(_tradeBuilderPartnerId);
  if (!yourTeam || !partnerTeam) {
    _tbSuggestions = null;
    _tbSuggestionsPending = false;
    renderTradeBuilderModal();
    return;
  }
  _tbSuggestionsPending = true;
  const myKey = _key;
  const body = {
    partner_roster_id: partnerTeam.roster_id,
    side_a: _tbSelectionToPayload(_tradeBuilderYourSel, yourTeam),
    side_b: _tbSelectionToPayload(_tradeBuilderPartnerSel, partnerTeam),
  };
  try {
    const res = await fetch(`/api/leagues/${encodeURIComponent(myKey)}/trade/suggestions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (_key !== myKey) return;
    _tbSuggestions = res.ok ? await res.json() : { error: true };
  } catch (e) {
    if (_key !== myKey) return;
    _tbSuggestions = { error: true };
  }
  _tbSuggestionsPending = false;
  renderTradeBuilderModal();
}

function onAddSuggestionToTrade(index) {
  const s = _tbSuggestions && _tbSuggestions.suggestions && _tbSuggestions.suggestions[index];
  if (!s) return;
  s.target_players.forEach(p => _tradeBuilderPartnerSel.add(`player:${p.player_id}`));
  s.offer_players.forEach(p => _tradeBuilderYourSel.add(`player:${p.player_id}`));
  s.offer_picks.forEach(p => _tradeBuilderYourSel.add(`pick:${p.season}:${p.round}:${p.original_roster_id}`));
  renderTradeBuilderModal();
  scheduleTradeBuilderEvaluate();
}
```

- [ ] **Step 5: Fetch suggestions as soon as a partner is known, not just on toggle**

In `openTradeBuilder()`, replace:

```js
function openTradeBuilder() {
  _tradeBuilderOpen = true;
  _tradeBuilderYourSel = new Set();
  _tradeBuilderPartnerSel = new Set();
  _tradeBuilderEval = null;
  if (_tradeBuilderData === null) {
    loadTradeBuilder();
  } else if (!_tradeBuilderData.error && _tradeBuilderPartnerId === null) {
    const firstOther = _tradeBuilderData.teams.find(t => !t.is_you);
    _tradeBuilderPartnerId = firstOther ? firstOther.roster_id : null;
  }
  renderTradeBuilderModal();
}
```

with:

```js
function openTradeBuilder() {
  _tradeBuilderOpen = true;
  _tradeBuilderYourSel = new Set();
  _tradeBuilderPartnerSel = new Set();
  _tradeBuilderEval = null;
  _tbSuggestions = null;
  if (_tradeBuilderData === null) {
    loadTradeBuilder();
  } else if (!_tradeBuilderData.error && _tradeBuilderPartnerId === null) {
    const firstOther = _tradeBuilderData.teams.find(t => !t.is_you);
    _tradeBuilderPartnerId = firstOther ? firstOther.roster_id : null;
  }
  renderTradeBuilderModal();
  if (_tradeBuilderData !== null && !_tradeBuilderData.error && _tradeBuilderPartnerId !== null) {
    fetchTradeSuggestions();
  }
}
```

In `loadTradeBuilder()`, replace the tail:

```js
  _tradeBuilderData = result;
  if (!result.error) {
    const firstOther = result.teams.find(t => !t.is_you);
    _tradeBuilderPartnerId = firstOther ? firstOther.roster_id : null;
  }
  renderTradeBuilderModal();
}
```

with:

```js
  _tradeBuilderData = result;
  if (!result.error && _tradeBuilderPartnerId === null) {
    const firstOther = result.teams.find(t => !t.is_you);
    _tradeBuilderPartnerId = firstOther ? firstOther.roster_id : null;
  }
  renderTradeBuilderModal();
  if (!result.error && _tradeBuilderPartnerId !== null) {
    fetchTradeSuggestions();
  }
}
```

(The `_tradeBuilderPartnerId === null` guard on the first replacement is
required for Task 4: a click-through from the Targets tab sets
`_tradeBuilderPartnerId` before `loadTradeBuilder()`'s response arrives,
and this must not be clobbered back to `firstOther`.)

- [ ] **Step 6: Wire the "Add to trade" click into the existing delegated listener**

In `mountSeason()`, replace:

```js
  container.querySelector("#trade-builder-root").addEventListener("click", (e) => {
    if (e.target.closest("[data-tb-close]")) { closeTradeBuilder(); }
  });
```

with:

```js
  container.querySelector("#trade-builder-root").addEventListener("click", (e) => {
    if (e.target.closest("[data-tb-close]")) { closeTradeBuilder(); return; }
    const addBtn = e.target.closest("[data-tb-add-suggestion]");
    if (addBtn) { onAddSuggestionToTrade(Number(addBtn.dataset.tbAddSuggestion)); }
  });
```

- [ ] **Step 7: Render the panel**

Immediately after `ordinalSuffix()` (the function ending at line 899 with
its closing `}`), add:

```js
function tbSuggestionsHTML() {
  if (!_tbSuggestions || _tbSuggestions.error) return "";
  const items = _tbSuggestions.suggestions || [];
  const updating = _tbSuggestionsPending ? "updating" : "";
  if (items.length === 0) {
    return `
      <div class="tb-suggestions ${updating}">
        <p class="tb-section-label">Suggested additions</p>
        <p class="tb-empty">No additional players clear both the fairness and value-gain bar right now.</p>
      </div>`;
  }
  const rows = items.map((s, i) => {
    const targets = s.target_players.map(p => escapeHtml(p.name)).join(", ");
    const offers = [...s.offer_players.map(p => escapeHtml(p.name)),
                    ...s.offer_picks.map(p => escapeHtml(p.label))].join(", ") || "(nothing else)";
    return `<div class="tb-suggestion-row">
      <div class="tb-suggestion-main">
        <span class="tb-suggestion-ask">Ask for ${targets}</span>
        <span class="tb-suggestion-arrow">&harr;</span>
        <span class="tb-suggestion-give">offer ${offers}</span>
        <span class="tb-suggestion-gain">+${s.net_value_gain.toFixed(1)} value</span>
      </div>
      <button class="tb-add-btn" type="button" data-tb-add-suggestion="${i}">Add to trade</button>
    </div>`;
  }).join("");
  return `
    <div class="tb-suggestions ${updating}">
      <p class="tb-section-label">Suggested additions</p>
      <div class="tb-suggestions-list">${rows}</div>
    </div>`;
}
```

In `renderTradeBuilderModal()`, replace:

```js
      ${tbScoreboardHTML()}
      ${tbNeedsHTML()}
      <div class="tb-foot">
```

with:

```js
      ${tbScoreboardHTML()}
      ${tbNeedsHTML()}
      ${tbSuggestionsHTML()}
      <div class="tb-foot">
```

- [ ] **Step 8: Style the panel**

In `src/ffdo/web/season/season.css`, immediately after the `.tb-need-delta`
rules and their `@media` block (after line 601, `}` closing
`@media (max-width: 640px)`), add:

```css
.tb-suggestions { padding: 16px 28px 4px; transition: opacity 0.15s ease; }
.tb-suggestions.updating { opacity: 0.5; }
.tb-suggestions-list { display: flex; flex-direction: column; gap: 1px; margin-top: 8px; }
.tb-suggestion-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}
.tb-suggestion-row:last-child { border-bottom: none; }
.tb-suggestion-main {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12.5px;
  min-width: 0;
}
.tb-suggestion-ask { color: var(--text); font-weight: 600; }
.tb-suggestion-arrow { color: var(--faint); }
.tb-suggestion-give { color: var(--muted); }
.tb-suggestion-gain {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--green);
  font-weight: 600;
}
.tb-add-btn {
  font: 600 11.5px var(--font-sans);
  color: var(--text);
  background: var(--surface-2);
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  padding: 6px 12px;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
}
.tb-add-btn:hover { border-color: var(--accent); color: var(--accent); }
```

- [ ] **Step 9: Manually verify against a real league**

Run: `uv run uvicorn ffdo.api.app:app --port 8150` (or use this
worktree's `.claude/launch.json` `ffdo-api` config), open a tracked
Sleeper league's season screen in a browser, go to the Trades tab, click
"Propose a trade", and confirm:
- With a partner selected and nothing else checked, a "Suggested
  additions" panel appears below the roster-needs panel (or its empty
  state, if no pairing clears both filters for this league/partner).
- Checking/unchecking players or picks refreshes the panel on the same
  ~400ms debounce as the scoreboard.
- Switching the trade partner clears and reloads the panel for the new
  partner.
- Clicking "Add to trade" on a suggestion checks the corresponding boxes
  in the existing player/pick pickers and the scoreboard updates to
  reflect them.

- [ ] **Step 10: Commit**

```bash
git add src/ffdo/web/season/season.js src/ffdo/web/season/season.css
git commit -m "feat: Trade Machine -- always-visible Suggested additions panel

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

## Task 4: League-wide "Targets" tab (spec's D)

**Files:**
- Modify: `src/ffdo/web/season/season.js`
- Modify: `src/ffdo/web/season/season.css`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 2's `GET /api/leagues/{key}/trade-targets`; the existing
  `_panel` tab-switching pattern (`season.js:16, 98-104, 155-195`) each
  other tab (`lineup`, `trades`, `waivers`, `scorecard`) already follows;
  Task 3's `openTradeBuilder`/`loadTradeBuilder` (now partner-id-aware
  per Task 3 Step 5) and the `player:<id>` / `pick:<season>:<round>:<original_roster_id>`
  selection-key format.
- Produces: a new "Targets" tab, wired end-to-end into the Trade Machine
  modal via click-through.

- [ ] **Step 1: Add targets state**

In `src/ffdo/web/season/season.js`, immediately after line 20
(`let _scorecardData = null; ...`), add:

```js
let _targetsData = null;  // null until the Targets tab has been opened at least once
```

- [ ] **Step 2: Reset on mount**

In `mountSeason()`, immediately after line 82 (`_scorecardData = null;`),
add:

```js
  _targetsData = null;
```

- [ ] **Step 3: Add the tab button**

In `renderRightPanel()`, replace:

```js
      <button data-panel-tab="scorecard" class="${_panel === "scorecard" ? "on" : ""}">Scorecard</button>
    </div>`;
```

with:

```js
      <button data-panel-tab="scorecard" class="${_panel === "scorecard" ? "on" : ""}">Scorecard</button>
      <button data-panel-tab="targets" class="${_panel === "targets" ? "on" : ""}">Targets</button>
    </div>`;
```

- [ ] **Step 4: Wire the tab's load/render branch**

In `renderRightPanel()`, immediately after the `scorecard` branch
(the block ending with `return tabBar + renderScorecard();\n  }`), add:

```js
  if (_panel === "targets") {
    if (_targetsData === null) {
      loadTargets();
      return tabBar + `<div class="lineup-loading">Loading trade targets&hellip;</div>`;
    }
    return tabBar + renderTargets();
  }
```

- [ ] **Step 5: Add `loadTargets`, `renderTargets`, and the click-through**

Immediately after `renderScorecard()` (find it by its call site above;
insert right after its closing `}`), add:

```js
async function loadTargets() {
  // Same stale-response guard as loadLineup()/loadTrades()/loadWaivers()/
  // loadScorecard() above.
  const myKey = _key;
  let result;
  try {
    const res = await fetch(`/api/leagues/${encodeURIComponent(myKey)}/trade-targets`);
    if (_key !== myKey) return;
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      if (_key !== myKey) return;
      result = { error: body.detail || "Couldn't load trade targets" };
    } else {
      const data = await res.json();
      if (_key !== myKey) return;
      result = data;
    }
  } catch (e) {
    if (_key !== myKey) return;
    result = { error: "Couldn't load trade targets" };
  }
  _targetsData = result;
  render();
}

function renderTargets() {
  if (_targetsData.error) {
    return `<div class="lineup-error">${escapeHtml(_targetsData.error)}</div>`;
  }
  const items = _targetsData.suggestions || [];
  if (items.length === 0) {
    return `<div class="lineup-empty">No trade targets clear both the fairness and value-gain bar right now.</div>`;
  }
  const rows = items.map((s, i) => {
    const targets = s.target_players.map(p => escapeHtml(p.name)).join(", ");
    const offers = [...s.offer_players.map(p => escapeHtml(p.name)),
                    ...s.offer_picks.map(p => escapeHtml(p.label))].join(", ") || "(nothing else)";
    return `<div class="lineup-row target-row" data-target-row="${i}">
      <div class="target-row-main">
        <span class="target-partner">${escapeHtml(s.partner_team_name)}</span>
        <span class="target-ask">Ask for ${targets}</span>
        <span class="target-arrow">&harr;</span>
        <span class="target-give">offer ${offers}</span>
      </div>
      <div class="target-row-meta">
        <span class="target-why">${escapeHtml(s.why)}</span>
        <span class="target-gain">+${s.net_value_gain.toFixed(1)} value</span>
      </div>
    </div>`;
  }).join("");
  return `<div class="lineup-list">${rows}</div>`;
}

function onTargetRowClick(index) {
  const s = _targetsData && _targetsData.suggestions && _targetsData.suggestions[index];
  if (!s) return;
  openTradeBuilderWithSuggestion(s);
}

// Mirrors openTradeBuilder() but seeds the partner and both selections
// from a suggestion instead of defaulting to the first other team --
// used by the Targets tab's click-through. Kept separate from
// openTradeBuilder() rather than adding an optional argument there, since
// the two callers' defaulting behavior genuinely differs (first-other-team
// vs. this-specific-suggestion).
function openTradeBuilderWithSuggestion(s) {
  _tradeBuilderOpen = true;
  _tradeBuilderPartnerId = s.partner_roster_id;
  _tradeBuilderPartnerSel = new Set(s.target_players.map(p => `player:${p.player_id}`));
  _tradeBuilderYourSel = new Set([
    ...s.offer_players.map(p => `player:${p.player_id}`),
    ...s.offer_picks.map(p => `pick:${p.season}:${p.round}:${p.original_roster_id}`),
  ]);
  _tradeBuilderEval = null;
  _tbSuggestions = null;
  if (_tradeBuilderData === null) {
    loadTradeBuilder();
  }
  renderTradeBuilderModal();
  if (_tradeBuilderData !== null && !_tradeBuilderData.error) {
    scheduleTradeBuilderEvaluate();
  }
}
```

- [ ] **Step 6: Wire the row click into the existing delegated listener**

In `mountSeason()`, replace:

```js
    const proposeBtn = e.target.closest("[data-propose-trade]");
    if (proposeBtn) { openTradeBuilder(); }
  });
```

with:

```js
    const proposeBtn = e.target.closest("[data-propose-trade]");
    if (proposeBtn) { openTradeBuilder(); return; }
    const targetRow = e.target.closest("[data-target-row]");
    if (targetRow) { onTargetRowClick(Number(targetRow.dataset.targetRow)); }
  });
```

- [ ] **Step 7: Style the tab**

In `src/ffdo/web/season/season.css`, immediately after the
`.trades-toolbar { margin-bottom: 10px; }` rule (line 380), add:

```css
.lineup-row.target-row { display: block; cursor: pointer; }
.lineup-row.target-row:hover { border-color: var(--border-strong); }
.target-row-main {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
}
.target-partner {
  font-size: 10.5px;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--faint);
  font-weight: 600;
}
.target-ask { color: var(--text); font-weight: 600; }
.target-arrow { color: var(--faint); }
.target-give { color: var(--muted); }
.target-row-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 4px;
  font-size: 12px;
}
.target-why { color: var(--faint); }
.target-gain {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--green);
  font-weight: 600;
  flex-shrink: 0;
}
```

- [ ] **Step 8: Update the README**

In `README.md`, replace the paragraph describing the Trades tab (lines
18-29, from `The **Trades** tab shows every real trade...` through
`...or hand your trade partner a rank jump you didn't mean to give
them.`) by adding one new paragraph immediately after it:

```markdown
The **Targets** tab ranks specific players on other teams worth trading
for league-wide -- your weak positions matched against real surplus
elsewhere, each paired with a value-matched offer from your own roster
that only ever surfaces when it's fair to your trade partner *and*
actually raises your own team's total value. The same suggestions also
appear inside the Trade Machine itself once you've picked a partner, so
you're never stuck guessing what else to ask for.
```

- [ ] **Step 9: Manually verify against a real league**

With `ffdo-api` running, open a tracked Sleeper league's season screen,
click the new "Targets" tab, and confirm:
- The tab loads and shows either a ranked list of suggestions or its
  empty state, with no console errors.
- Each row shows the partner team, the ask, the offer, the "why" line,
  and the net value gain.
- Clicking a row opens the Trade Machine modal with that partner already
  selected and both sides of the suggestion already checked, and the
  scoreboard evaluates it.

- [ ] **Step 10: Run the full suite one more time**

Run: `uv run pytest`
Expected: PASS (no regressions from the README/frontend-only changes,
confirms nothing else broke across the whole branch).

- [ ] **Step 11: Commit**

```bash
git add src/ffdo/web/season/season.js src/ffdo/web/season/season.css README.md
git commit -m "feat: add league-wide Targets tab, click-through into Trade Machine

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
