# tests/ingest/test_projections.py
import pytest

from ffdo.ingest import projections, snapshot


def test_parses_current_season_projections_and_adp():
    proj, adp = projections.parse(snapshot.load("projections_2026"), 2026)
    assert len(proj) > 500
    gibbs = proj["9221"]
    assert gibbs.season == 2026
    assert gibbs.stats["rush_yd"] > 0
    assert adp["9221"].adp["half_ppr"] < 10


def test_drops_rows_that_carry_no_projection_only_adp():
    """Sleeper's projections feed returns a row for every player who has an
    ADP, including hundreds with no statistical projection at all (~120 of
    its 153 "kickers" are pure ADP rows). Those score 0 and clutter the
    board; a row with nothing but `gp` after the adp_ keys are stripped
    carries no projection and must not produce a SeasonProjection -- its
    ADP is still preserved (ADP is an independent signal)."""
    raw = [
        {"player_id": "real", "stats": {"gp": 17.0, "rush_yd": 900.0,
                                        "adp_half_ppr": 12.0},
         "last_modified": 1_600_000_000_000},
        {"player_id": "adp_only", "stats": {"gp": 18.0, "adp_half_ppr": 300.0},
         "last_modified": 1_600_000_000_000},
    ]
    proj, adp = projections.parse(raw, 2026)
    assert "real" in proj
    assert "adp_only" not in proj
    assert adp["adp_only"].adp["half_ppr"] == 300.0


def test_keeps_a_row_whose_only_projection_signal_is_a_precomputed_total():
    """A precomputed `pts_*` total (no exploded component stats) is still a
    real projection -- the boundary is "no projection data at all," not
    "no component stats." """
    raw = [{"player_id": "p", "stats": {"gp": 17.0, "pts_half_ppr": 40.0,
                                        "adp_half_ppr": 90.0},
            "last_modified": 1_600_000_000_000}]
    proj, _ = projections.parse(raw, 2026)
    assert "p" in proj


def test_rejects_contaminated_historical_projections():
    raw = snapshot.load("projections_2023_CONTAMINATED")
    with pytest.raises(projections.ContaminatedProjectionError):
        projections.parse(raw, 2023)


def test_contaminated_projections_readable_when_explicitly_allowed():
    """A player drafted 10th overall (Nick Chubb, ADP 10.6) has no stored
    projection at all in the 2023 snapshot -- the `pts_half_ppr` key is
    entirely absent, not zeroed -- because he suffered a week-2
    season-ending injury and Sleeper's projections endpoint reports back
    the season's actual (nonexistent) production, not the preseason
    estimate. His ADP, in contrast, is untouched: it was fixed at draft
    time and the season that followed cannot rewrite it. This is why ADP
    is the only historical signal this project trusts.
    """
    raw = snapshot.load("projections_2023_CONTAMINATED")
    proj, adp = projections.parse(raw, 2023, allow_contaminated=True)
    # Find the top-11-ADP player whose projection was wiped (Chubb), rather
    # than assuming iteration order -- most top-11-ADP players finished the
    # season healthy and DO still carry a real pts_half_ppr.
    wiped_pid, wiped_adp = next(
        (pid, a.adp["half_ppr"]) for pid, a in adp.items()
        if a.adp.get("half_ppr", 999) < 11
        and "pts_half_ppr" not in proj[pid].stats)
    assert wiped_adp < 11
    assert "pts_half_ppr" not in proj[wiped_pid].stats


def test_rejects_an_unknown_season_rather_than_silently_skipping_the_guard():
    """`SEASON_START.get(season)` used to return None for an unmapped season,
    which short-circuited the guard condition (`kickoff and ...`) to False --
    silently passing untrusted data through instead of refusing. The guard's
    job is to refuse when it cannot prove the data is clean, not to trust by
    default on an unexpected input."""
    raw = [{"player_id": "1", "stats": {"pts_half_ppr": 10.0},
            "last_modified": 1_700_000_000_000}]
    with pytest.raises(projections.ContaminatedProjectionError):
        projections.parse(raw, 2099)


def test_unknown_season_is_readable_when_explicitly_allowed():
    raw = [{"player_id": "1", "stats": {"pts_half_ppr": 10.0},
            "last_modified": 1_700_000_000_000}]
    proj, _ = projections.parse(raw, 2099, allow_contaminated=True)
    assert "1" in proj


def test_rejects_data_with_no_last_modified_timestamp_on_any_row():
    """If no row carries a last_modified timestamp, `_last_modified` returns
    None, which also short-circuited the old guard condition to False. There
    is no way to verify the data predates kickoff, so the safe default is to
    refuse, not to assume it's clean."""
    raw = [{"player_id": "1", "stats": {"pts_half_ppr": 10.0}}]
    with pytest.raises(projections.ContaminatedProjectionError):
        projections.parse(raw, 2026)


def test_missing_last_modified_is_readable_when_explicitly_allowed():
    raw = [{"player_id": "1", "stats": {"pts_half_ppr": 10.0}}]
    proj, _ = projections.parse(raw, 2026, allow_contaminated=True)
    assert "1" in proj


def test_empty_input_does_not_raise_for_a_known_season():
    """No rows at all is not the same failure mode as 'rows exist but none
    carry a timestamp' -- there is nothing to contaminate."""
    proj, adp = projections.parse([], 2026)
    assert proj == {}
    assert adp == {}


def test_adp_is_preserved_even_for_contaminated_seasons():
    """ADP is fixed at draft time and is the ONLY clean historical signal."""
    _, adp = projections.parse(
        snapshot.load("projections_2023_CONTAMINATED"), 2023,
        allow_contaminated=True)
    ranked = sorted((v.adp["half_ppr"], k) for k, v in adp.items()
                    if v.adp.get("half_ppr", 999) < 999)
    assert len(ranked) > 100
    assert ranked[0][0] < 5
