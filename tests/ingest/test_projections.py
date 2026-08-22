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


def test_adp_is_preserved_even_for_contaminated_seasons():
    """ADP is fixed at draft time and is the ONLY clean historical signal."""
    _, adp = projections.parse(
        snapshot.load("projections_2023_CONTAMINATED"), 2023,
        allow_contaminated=True)
    ranked = sorted((v.adp["half_ppr"], k) for k, v in adp.items()
                    if v.adp.get("half_ppr", 999) < 999)
    assert len(ranked) > 100
    assert ranked[0][0] < 5
