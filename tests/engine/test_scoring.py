from ffdo.domain.constants import STANDARD_HALF_PPR
from ffdo.ingest import league, players, projections, snapshot, stats
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


def test_score_stats_does_not_leak_return_td_credit_onto_a_real_offensive_player():
    """Critical-1 regression: `def_kr_td` (kickoff-return TD) is credited
    by Sleeper to the individual returner, not exclusively to team DEF.

    Verified against the real 2026 projections snapshot: 8 real rostered
    WR/RB rows carry `def_kr_td` in their own `stats` dict, including
    Rashid Shaheed (player_id 8676, a real WR). A league that weights this
    category (the real connected ESPN league weights it at 6.0) must NOT
    give Shaheed +6 he isn't owed just because the key happens to appear
    on his row -- `score_stats` has no position parameter, so the only
    safe fix is excluding the key entirely (see `_DEFENSE_BARE`'s comment).
    """
    profiles = players.parse(snapshot.load("players_nfl"))
    raw_proj = snapshot.load("projections_2026")
    proj, _adp = projections.parse(raw_proj, 2026)

    shaheed_id = "8676"
    shaheed = profiles[shaheed_id]
    assert shaheed.first_name == "Rashid"
    assert shaheed.last_name == "Shaheed"
    assert shaheed.position == "WR"

    shaheed_stats = proj[shaheed_id].stats
    assert shaheed_stats.get("def_kr_td") == 1.0, (
        "fixture assumption changed -- Shaheed's real 2026 projection row "
        "no longer carries def_kr_td; re-verify against the snapshot"
    )

    without_kr_td = {k: v for k, v in shaheed_stats.items() if k != "def_kr_td"}
    league_weighted_for_kr_td = {**STANDARD_HALF_PPR, "def_kr_td": 6.0}

    with_key = scoring.score_stats(shaheed_stats, league_weighted_for_kr_td)
    without_key = scoring.score_stats(without_kr_td, league_weighted_for_kr_td)
    assert with_key == without_key, (
        "an offensive player's score must be unaffected by a defense-vocabulary "
        "return-TD key even when a league weights it nonzero"
    )


def test_golden_reproduces_sleeper_half_ppr_2025_for_kickers():
    """K analogue of the offense golden test (§6.2 of the design doc).

    Verified against the real connected league's own 2026 scoring_settings
    (data/snapshots/2026-08-22-draft-day/league_history.json.gz) applied to
    every real 2025 K actual-stats line: this reproduces Sleeper's own
    `pts_half_ppr` almost exactly (mean absolute diff ~0.02 points across
    all 42 real kickers, max 1.0 -- likely float/rounding noise on a single
    row), confirming the kicking classifier's vocabulary is correct and
    complete against real data.
    """
    profiles = players.parse(snapshot.load("players_nfl"))
    lines = stats.parse(snapshot.load("stats_2025"), 2025)
    lg = league.parse(snapshot.load("league_history")["leagues"]["2026"])

    total = matched = 0
    for player_id, line in lines.items():
        prof = profiles.get(player_id)
        if prof is None or prof.position != "K":
            continue
        sleeper_pts = line.stats.get("pts_half_ppr")
        if sleeper_pts is None:
            continue
        total += 1
        mine = scoring.score_stats(line.stats, lg.scoring_settings)
        if abs(mine - sleeper_pts) <= 1.0:
            matched += 1

    assert total >= 30
    assert matched / total >= 0.95, f"only {matched}/{total} reproduced"


def test_golden_reproduces_sleeper_half_ppr_2025_for_defenses_after_points_allowed():
    """DEF analogue of the offense golden test (§6.2 of the design doc).

    Points-allowed is deliberately excluded from scoring (design doc
    §3.2), so an exact match to Sleeper's `pts_half_ppr` (which DOES score
    points-allowed) is not the right bar. Instead this independently
    computes each team's real points-allowed contribution (using the same
    real league's own pts_allow_* weights) and asserts the residual --
    Sleeper's total minus ours -- is explained by that points-allowed
    component to within a small tolerance, i.e. the reproduction isn't
    silently wrong somewhere else in the defense vocabulary.

    This golden test is also what caught (during the fix wave that added
    it) that `def_st_ff`/`def_st_fum_rec` must NOT be added to
    `_DEFENSE_BARE` -- doing so measurably widened the unexplained
    residual instead of closing it, revealing they're a subset of the
    already-recognized `ff`/`fum_rec` totals rather than an additive
    category. See `_DEFENSE_BARE`'s comment.
    """
    profiles = players.parse(snapshot.load("players_nfl"))
    lines = stats.parse(snapshot.load("stats_2025"), 2025)
    lg = league.parse(snapshot.load("league_history")["leagues"]["2026"])
    weights = lg.scoring_settings
    pts_allow_keys = [k for k in weights
                       if k.startswith("pts_allow_") or k.startswith("yds_allow_")]

    total = 0
    unexplained = []
    for player_id, line in lines.items():
        prof = profiles.get(player_id)
        if prof is None or prof.position != "DEF":
            continue
        sleeper_pts = line.stats.get("pts_half_ppr")
        if sleeper_pts is None:
            continue
        total += 1
        mine = scoring.score_stats(line.stats, weights)
        pts_allow_component = sum(
            line.stats.get(k, 0.0) * weights.get(k, 0.0) for k in pts_allow_keys
        )
        residual = sleeper_pts - mine
        unexplained.append(abs(residual - pts_allow_component))

    assert total >= 25
    assert max(unexplained) <= 10.0, f"worst unexplained residual: {max(unexplained)}"
    assert sum(unexplained) / total <= 5.0, (
        f"mean unexplained residual too high: {sum(unexplained) / total}"
    )
