from ffdo.domain.models import LeagueProfile, PlayerProfile, RosterEntry, ValuedPlayer
from ffdo.engine import power_ranking


def _league():
    return LeagueProfile(league_id="x", season=2026, num_teams=3,
                         roster_positions=("QB", "RB", "WR", "FLEX", "BN", "BN"),
                         scoring_settings={}, budget=200)


def _vp(pid, pos, v):
    prof = PlayerProfile(player_id=pid, first_name=pos, last_name=pid, position=pos,
                         team="X", age=26, years_exp=4, injury_status=None, active=True)
    return ValuedPlayer(profile=prof, projected_points=v, adjusted_points=v,
                        vor=v, tier=1, adjustments={})


def _entry(rid, players, starters=()):
    return RosterEntry(roster_id=rid, team_name=f"T{rid}", player_ids=tuple(players),
                       starter_ids=tuple(starters), wins=0, losses=0, ties=0,
                       points_for=0.0, points_against=0.0)


# team 1: strong starters, empty bench.  team 2: weaker starters, deep bench.
VALUED = {
    "q1": _vp("q1", "QB", 30), "r1": _vp("r1", "RB", 40), "w1": _vp("w1", "WR", 35), "f1": _vp("f1", "RB", 20),
    "q2": _vp("q2", "QB", 25), "r2": _vp("r2", "RB", 22), "w2": _vp("w2", "WR", 20), "f2": _vp("f2", "WR", 18),
    "b2a": _vp("b2a", "RB", 15), "b2b": _vp("b2b", "WR", 14),
    "q3": _vp("q3", "QB", 10), "r3": _vp("r3", "RB", 12), "w3": _vp("w3", "WR", 11), "f3": _vp("f3", "TE", 5),
}
ROSTERS = [
    _entry(1, ["q1", "r1", "w1", "f1"]),
    _entry(2, ["q2", "r2", "w2", "f2", "b2a", "b2b"]),
    _entry(3, ["q3", "r3", "w3", "f3"]),
]
STANDINGS = {1: 2, 2: 1, 3: 3}   # team 2 leads the standings


def test_overall_starters_ranks_by_starting_lineup_value():
    rows = power_ranking.rank(ROSTERS, VALUED, _league(), STANDINGS, 1,
                              position="OVR", scope="starters")
    assert [r.roster_id for r in rows] == [1, 2, 3]
    assert rows[0].power_rank == 1 and rows[0].is_you is True
    assert rows[0].delta == STANDINGS[1] - 1        # 2 - 1 = +1


def test_full_scope_can_reorder_vs_starters():
    starters = power_ranking.rank(ROSTERS, VALUED, _league(), STANDINGS, None,
                                  position="OVR", scope="starters")
    full = power_ranking.rank(ROSTERS, VALUED, _league(), STANDINGS, None,
                              position="OVR", scope="full")
    # team 2's deep bench lifts its full value; the gap to team 1 shrinks
    s_gap = starters[0].value - next(r for r in starters if r.roster_id == 2).value
    f_gap = full[0].value - next(r for r in full if r.roster_id == 2).value
    assert f_gap < s_gap
    assert any(r.bench_value > 0 for r in full)
    assert all(r.bench_value == 0 for r in starters)


def test_position_ranking_respects_scope():
    # RB, starters: only the RB actually in a starting slot (dedicated or FLEX) counts
    rb_start = power_ranking.rank(ROSTERS, VALUED, _league(), STANDINGS, None,
                                  position="RB", scope="starters")
    rb_full = power_ranking.rank(ROSTERS, VALUED, _league(), STANDINGS, None,
                                 position="RB", scope="full")
    t2_start = next(r for r in rb_start if r.roster_id == 2).value
    t2_full = next(r for r in rb_full if r.roster_id == 2).value
    assert t2_full > t2_start          # b2a (benched RB) only counts under "full"


def test_delta_sign_positive_when_roster_beats_record():
    rows = power_ranking.rank(ROSTERS, VALUED, _league(), {1: 3, 2: 1, 3: 2}, None,
                              position="OVR", scope="starters")
    you = next(r for r in rows if r.roster_id == 1)
    assert you.power_rank == 1 and you.standings_rank == 3
    assert you.delta == 2             # ranks 2 spots better than the standings
