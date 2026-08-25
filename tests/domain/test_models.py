# tests/domain/test_models.py
import dataclasses
import pytest

from ffdo.domain.constants import (
    SEASON_LENGTH, is_defense_scoring_key, is_kicking_scoring_key,
    is_offense_scoring_key,
)
from ffdo.domain.models import (
    DraftPick, DraftState, LeagueProfile, MarketADP,
    PlayerProfile, SeasonProjection, SeasonStatLine, Session, TeamProfile,
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


@pytest.mark.parametrize("key,expected", [
    ("sack", True), ("int", True), ("fum_rec", True), ("blk_kick", True),
    ("safe", True), ("ff", True), ("def_td", True), ("def_st_td", True),
    ("def_kr_td", True), ("def_pr_td", True), ("def_fum_td", True),
    ("rec_td", False), ("pts_allow_0", False), ("pts_allow_35p", False),
    ("yds_allow_0_100", False), ("fgm_40_49", False),
    # Deliberately excluded even though it's a real defense-adjacent key --
    # ESPN's equivalent category (statId 63) has no D/ST-slot override,
    # meaning it doesn't apply to team defense; keeping it excluded from
    # offense too (already true via `_DEFENSIVE_ONLY`) avoids crediting
    # anyone for it. See Task 3 and constants.py's `_DEFENSIVE_ONLY` comment.
    ("fum_rec_td", False),
])
def test_defense_scoring_key_classification(key, expected):
    assert is_defense_scoring_key(key) is expected


@pytest.mark.parametrize("key,expected", [
    ("fgm_20_29", True), ("fgm_40_49", True), ("fgm_50p", True),
    ("fgm_60p", True), ("fgmiss_50p", True), ("fgm", True), ("fga", True),
    ("fgmiss", True), ("xpm", True), ("xpa", True), ("xpmiss", True),
    ("sack", False), ("rush_yd", False), ("pts_allow_0", False),
])
def test_kicking_scoring_key_classification(key, expected):
    assert is_kicking_scoring_key(key) is expected


def test_league_profile_derives_starting_slots_and_roster_size():
    lg = LeagueProfile(
        league_id="1315881559957458944", season=2026, num_teams=12,
        roster_positions=("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                          "BN", "BN", "BN", "BN", "BN"),
        scoring_settings={"rec": 0.5}, budget=200,
    )
    assert lg.starting_slots == ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX")
    assert lg.roster_size == 13


def test_league_profile_name_and_status_default_to_empty_string():
    lg = LeagueProfile(
        league_id="x", season=2026, num_teams=12,
        roster_positions=("QB", "BN"), scoring_settings={}, budget=200,
    )
    assert lg.name == ""
    assert lg.status == ""


def test_league_profile_accepts_name_and_status():
    lg = LeagueProfile(
        league_id="x", season=2026, num_teams=12,
        roster_positions=("QB", "BN"), scoring_settings={}, budget=200,
        name="P-Vegas Ballers", status="pre_draft",
    )
    assert lg.name == "P-Vegas Ballers"
    assert lg.status == "pre_draft"


def test_session_is_frozen_and_holds_the_connected_leagues_identity():
    session = Session(
        username="tester", user_id="U1", league_id="L1", draft_id="D1",
        roster_id=3, league_name="Test League", season=2026, num_teams=12,
        budget=200,
        roster_positions=("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                          "BN", "BN", "BN", "BN", "BN"),
        scoring_settings={"rec": 0.5}, draft_type="auction",
        draft_status="pre_draft", rounds=13, is_mock=False,
        connected_at="2026-08-22T00:00:00+00:00",
    )
    assert session.roster_id == 3
    assert session.scoring_settings == {"rec": 0.5}
    with pytest.raises(dataclasses.FrozenInstanceError):
        session.roster_id = 4


def test_session_defaults_provider_to_sleeper_with_no_espn_credentials():
    session = Session(
        username="tester", user_id="U1", league_id="L1", draft_id="D1",
        roster_id=3, league_name="Test League", season=2026, num_teams=12,
        budget=200,
        roster_positions=("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                          "BN", "BN", "BN", "BN", "BN"),
        scoring_settings={"rec": 0.5}, draft_type="auction",
        draft_status="pre_draft", rounds=13, is_mock=False,
        connected_at="2026-08-22T00:00:00+00:00",
    )
    assert session.provider == "sleeper"
    assert session.espn_s2 is None
    assert session.swid is None


def test_session_accepts_espn_provider_and_cookies():
    session = Session(
        username="", user_id="{00000004-0000-0000-0000-000000000000}",
        league_id="1882997948", draft_id="1882997948", roster_id=7,
        league_name="Pigskin Pricing Experts", season=2026, num_teams=10,
        budget=200,
        roster_positions=("QB", "RB", "RB", "WR", "WR", "TE", "FLEX",
                          "DEF", "K", "BN", "BN", "BN", "BN", "BN", "BN", "IR"),
        scoring_settings={"pass_yd": 0.04}, draft_type="snake",
        draft_status="pre_draft", rounds=15, is_mock=False,
        connected_at="2026-08-23T00:00:00+00:00",
        provider="espn", espn_s2="s2-value",
        swid="{00000004-0000-0000-0000-000000000000}",
    )
    assert session.provider == "espn"
    assert session.espn_s2 == "s2-value"
    assert session.swid == "{00000004-0000-0000-0000-000000000000}"


def test_session_is_mock_defaults_to_nothing_it_must_be_explicit():
    """is_mock has no default -- every Session construction site must say
    explicitly whether it's a real league or a mock draft, the same way
    `rounds` was made required rather than guessable."""
    with pytest.raises(TypeError):
        Session(
            username="tester", user_id="U1", league_id="L1", draft_id="D1",
            roster_id=3, league_name="Test League", season=2026, num_teams=12,
            budget=200,
            roster_positions=("QB", "BN"),
            scoring_settings={"rec": 0.5}, draft_type="auction",
            draft_status="pre_draft", rounds=13,
            connected_at="2026-08-22T00:00:00+00:00",
        )


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
