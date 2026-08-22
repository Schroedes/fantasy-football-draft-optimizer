# tests/domain/test_models.py
import dataclasses
import pytest

from ffdo.domain.constants import SEASON_LENGTH, is_offense_scoring_key
from ffdo.domain.models import (
    DraftPick, DraftState, LeagueProfile, MarketADP,
    PlayerProfile, SeasonProjection, SeasonStatLine, TeamProfile,
)


def _player(**kw):
    base = dict(player_id="9221", first_name="Jahmyr", last_name="Gibbs",
                position="RB", team="DET", age=24, years_exp=3,
                injury_status=None, active=True)
    return PlayerProfile(**{**base, **kw})


def test_player_profile_is_frozen():
    p = _player()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.age = 25


def test_player_full_name():
    assert _player().full_name == "Jahmyr Gibbs"


def test_season_length_covers_history_and_current():
    assert SEASON_LENGTH[2023] == 17
    assert SEASON_LENGTH[2024] == 18
    assert SEASON_LENGTH[2026] == 18


@pytest.mark.parametrize("key,expected", [
    ("rec", True), ("rec_yd", True), ("rush_td", True), ("pass_yd", True),
    ("fum", True), ("fum_lost", True), ("st_td", True), ("bonus_rec_te", True),
    ("fum_rec", False), ("fum_rec_td", False),
    ("pts_allow_0", False), ("fgm_40_49", False), ("sack", False),
])
def test_offense_scoring_key_classification(key, expected):
    assert is_offense_scoring_key(key) is expected


def test_league_profile_derives_starting_slots_and_roster_size():
    lg = LeagueProfile(
        league_id="1315881559957458944", season=2026, num_teams=12,
        roster_positions=("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                          "BN", "BN", "BN", "BN", "BN"),
        scoring_settings={"rec": 0.5}, budget=200,
    )
    assert lg.starting_slots == ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX")
    assert lg.roster_size == 13


def test_draft_state_reports_spend_per_roster():
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=4, roster_id=9,
                  picked_by="437507358097141760", player_id="11566", amount=42),
        DraftPick(pick_no=2, round=1, draft_slot=5, roster_id=3,
                  picked_by="x", player_id="4034", amount=58),
        DraftPick(pick_no=3, round=1, draft_slot=6, roster_id=9,
                  picked_by="437507358097141760", player_id="6786", amount=10),
    )
    st = DraftState(draft_id="d", draft_type="auction", status="drafting",
                    num_teams=12, rounds=13, budget=200, picks=picks)
    assert st.spent_by_roster()[9] == 52
    assert st.drafted_player_ids() == frozenset({"11566", "4034", "6786"})


def test_stat_line_and_projection_and_adp_construct():
    s = SeasonStatLine(player_id="9221", season=2025, games_played=17,
                       season_length=18, stats={"rush_yd": 1200.0})
    assert s.games_missed == 1
    SeasonProjection(player_id="9221", season=2026,
                     stats={"rush_yd": 1100.0}, last_modified=None)
    MarketADP(player_id="9221", season=2026, adp={"half_ppr": 1.8})


def test_team_profile_is_frozen_and_holds_roster_id_and_name():
    tp = TeamProfile(roster_id=3, display_name="The Foobars")
    assert tp.roster_id == 3
    assert tp.display_name == "The Foobars"
    with pytest.raises(dataclasses.FrozenInstanceError):
        tp.display_name = "renamed"
