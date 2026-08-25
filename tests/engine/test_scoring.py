from ffdo.domain.constants import STANDARD_HALF_PPR
from ffdo.ingest import league, players, snapshot, stats
from ffdo.engine import scoring

OFFENSE = {"QB", "RB", "WR", "TE"}


def test_score_stats_applies_weights():
    assert scoring.score_stats({"rec": 4, "rec_yd": 50}, {"rec": 0.5, "rec_yd": 0.1}) == 7.0


def test_score_stats_ignores_defensive_keys_for_offense():
    """A WR must not be credited a defensive fumble-recovery touchdown."""
    got = scoring.score_stats({"rec_td": 1, "fum_rec_td": 1},
                              {"rec_td": 6, "fum_rec_td": 6})
    assert got == 6.0


def test_golden_reproduces_sleeper_half_ppr_2025():
    """Our rescore must reproduce Sleeper's own 2025 pts_half_ppr.

    Verified: >=98% of players scoring 50+ points match within 0.01.
    Do NOT extend this to earlier seasons -- Sleeper changed the preset
    definition between 2021 and 2023 (2021 counted `fum` at -1, 2023+ does not).
    """
    profiles = players.parse(snapshot.load("players_nfl"))
    lines = stats.parse(snapshot.load("stats_2025"), 2025)

    total = matched = 0
    for player_id, line in lines.items():
        prof = profiles.get(player_id)
        if prof is None or prof.position not in OFFENSE:
            continue
        sleeper_pts = line.stats.get("pts_half_ppr", 0.0)
        if sleeper_pts < 50:
            continue
        total += 1
        if abs(scoring.score_stats(line.stats, STANDARD_HALF_PPR) - sleeper_pts) < 0.01:
            matched += 1

    assert total > 200
    assert matched / total >= 0.98, f"only {matched}/{total} reproduced"


def test_league_scoring_diverges_from_preset_on_fumbles():
    """This league penalises ALL fumbles (`fum: -1`) on top of `fum_lost: -2`.

    Sleeper's own board shows the preset, so fumble-prone QBs are systematically
    overvalued there. This is the concrete edge the rescore layer buys.
    """
    lg = league.parse(snapshot.load("league_history")["leagues"]["2026"])
    profiles = players.parse(snapshot.load("players_nfl"))
    lines = stats.parse(snapshot.load("stats_2025"), 2025)

    deltas = []
    for player_id, line in lines.items():
        prof = profiles.get(player_id)
        if prof is None or prof.position != "QB":
            continue
        if line.stats.get("pts_half_ppr", 0.0) < 200:
            continue
        preset = scoring.score_stats(line.stats, STANDARD_HALF_PPR)
        actual = scoring.score_stats(line.stats, lg.scoring_settings)
        deltas.append(actual - preset)

    assert deltas
    assert min(deltas) <= -8.0, "expected fumble-prone QBs to lose 8+ points"
    assert max(deltas) <= 0.0, "league scoring can only reduce QB value here"


def test_score_stats_credits_defensive_categories():
    """A DEF player's turnover/sack production must now score, unlike
    today where score_stats returns 0.0 for every non-offense key."""
    got = scoring.score_stats(
        {"sack": 3, "int": 2, "fum_rec": 1},
        {"sack": 1.0, "int": 2.0, "fum_rec": 2.0},
    )
    assert got == 3 * 1.0 + 2 * 2.0 + 1 * 2.0


def test_score_stats_credits_kicking_categories():
    got = scoring.score_stats(
        {"fgm_40_49": 2, "fgm_50p": 1, "xpm": 3, "fgmiss": 1},
        {"fgm_40_49": 4.0, "fgm_50p": 5.0, "xpm": 1.0, "fgmiss": -1.0},
    )
    assert got == 2 * 4.0 + 1 * 5.0 + 3 * 1.0 + 1 * -1.0


def test_score_stats_still_excludes_points_allowed():
    """Points-allowed weights are configured but must have no effect --
    see design doc §3.2 and the `_DEFENSE_BARE` comment in constants.py."""
    got = scoring.score_stats(
        {"sack": 1, "pts_allow_0": 1},
        {"sack": 1.0, "pts_allow_0": 5.0},
    )
    assert got == 1.0
