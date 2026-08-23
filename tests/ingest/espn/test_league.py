from ffdo.ingest import snapshot
from ffdo.ingest.espn import league

ESPN_SNAPSHOT_DIR = snapshot.DEFAULT_SNAPSHOT_DIR.parent / "2026-08-23-espn-league"


def _msettings():
    return snapshot.load("mSettings", snapshot_dir=ESPN_SNAPSHOT_DIR)


def test_parse_reads_identity_and_size_from_the_real_league():
    lg = league.parse(_msettings())
    assert lg.league_id == "1882997948"
    assert lg.season == 2026
    assert lg.num_teams == 10
    assert lg.name == "Pigskin Pricing Experts"


def test_parse_builds_roster_positions_matching_the_real_lineup_slot_counts():
    """Verified live 2026-08-23: {"0":1,"2":2,"4":2,"6":1,"16":1,"17":1,
    "20":6,"21":1,"23":1}, every other slot at 0."""
    lg = league.parse(_msettings())
    from collections import Counter
    counts = Counter(lg.roster_positions)
    assert counts == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "DEF": 1,
                      "K": 1, "BN": 6, "IR": 1, "FLEX": 1}
    assert lg.roster_size == 16


def test_parse_builds_scoring_settings_matching_the_real_scoring_items():
    lg = league.parse(_msettings())
    assert lg.scoring_settings == {
        "pass_yd": 0.04, "pass_td": 4.0, "pass_int": -2.0,
        "rush_yd": 0.1, "rush_td": 6.0,
        "rec_yd": 0.1, "rec_td": 6.0, "rec": 1.0,
        "fum_lost": -2.0,
    }


def test_parse_reads_auction_budget_even_for_a_snake_league():
    """ESPN always includes draftSettings.auctionBudget regardless of draft
    type; this league's real value is 200. Harmless to carry through --
    the snake board path never reads LeagueProfile.budget for pricing."""
    lg = league.parse(_msettings())
    assert lg.budget == 200


def test_draft_type_reads_snake_from_the_real_league():
    assert league.draft_type(_msettings()) == "snake"


def test_roster_positions_raises_on_an_unmapped_nonzero_slot():
    """A silently-dropped roster slot would under-count roster size and
    corrupt replacement-level math -- must fail loudly instead."""
    import pytest
    raw = _msettings()
    modified = {
        **raw,
        "settings": {
            **raw["settings"],
            "rosterSettings": {
                **raw["settings"]["rosterSettings"],
                "lineupSlotCounts": {
                    **raw["settings"]["rosterSettings"]["lineupSlotCounts"],
                    "7": 1,  # a real ESPN slot id (OP/superflex-like) this table doesn't map
                },
            },
        },
    }
    with pytest.raises(ValueError, match="lineup slot"):
        league.parse(modified)
