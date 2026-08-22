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
    raw = snapshot.load("projections_2023_CONTAMINATED")
    proj, adp = projections.parse(raw, 2023, allow_contaminated=True)
    # Nick Chubb: preseason ADP 10.6, stored "projection" 0.0 after a
    # week-2 season-ending injury. Proof the stored values are post-hoc.
    chubb = next(p for pid, p in proj.items() if adp[pid].adp.get("half_ppr", 999) < 11
                 and p.stats.get("pts_half_ppr") == 0.0)
    assert chubb.stats["pts_half_ppr"] == 0.0


def test_adp_is_preserved_even_for_contaminated_seasons():
    """ADP is fixed at draft time and is the ONLY clean historical signal."""
    _, adp = projections.parse(
        snapshot.load("projections_2023_CONTAMINATED"), 2023,
        allow_contaminated=True)
    ranked = sorted((v.adp["half_ppr"], k) for k, v in adp.items()
                    if v.adp.get("half_ppr", 999) < 999)
    assert len(ranked) > 100
    assert ranked[0][0] < 5
