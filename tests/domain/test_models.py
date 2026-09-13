# tests/domain/test_models.py
import dataclasses
import pytest

from ffdo.domain.constants import (
    SEASON_LENGTH, is_defense_scoring_key, is_kicking_scoring_key,
    is_offense_scoring_key,
)
from ffdo.domain.models import (
    DiscoveredLeague, DraftPick, DraftState, LeagueProfile, MarketADP,
    PlayerProfile, ProviderCredential, SeasonProjection, SeasonStatLine,
    TeamProfile, TrackedLeague, make_league_key,
)


def _tracked(**overrides):
    base = dict(
        league_key="sleeper:L1:2026", provider="sleeper", provider_league_id="L1",
        season=2026, name="Test League", user_id="U1", roster_id=3,
        draft_id="D1", draft_type="auction", draft_status="pre_draft",
        num_teams=12, budget=200, rounds=13,
        roster_positions=("QB", "RB", "RB", "WR", "BN"),
        scoring_settings={"rec": 0.5}, fmt="redraft", format_override=None,
        raw_settings={"type": 0}, is_mock=False,
        tracked_at="2026-09-02T00:00:00+00:00",
        last_refreshed_at="2026-09-02T00:00:00+00:00",
    )
    return TrackedLeague(**{**base, **overrides})


def test_make_league_key_joins_provider_id_and_season():
    assert make_league_key("espn", "1882997948", 2026) == "espn:1882997948:2026"


def test_resolved_format_prefers_the_override():
    assert _tracked(fmt="redraft", format_override="dynasty").resolved_format == "dynasty"
    assert _tracked(fmt="keeper", format_override=None).resolved_format == "keeper"


def test_tracked_league_slot_helpers():
    lg = _tracked(roster_positions=("QB", "RB", "FLEX", "BN", "BN"))
    assert lg.starting_slots == ("QB", "RB", "FLEX")
    assert lg.roster_size == 5


def test_provider_credential_and_discovered_league_construct():
    cred = ProviderCredential(provider="espn", user_identifier="{SWID}",
                              espn_s2="s2", swid="{SWID}", updated_at="t")
    assert cred.espn_s2 == "s2"
    disc = DiscoveredLeague(provider="sleeper", provider_league_id="L1", season=2026,
                            name="X", num_teams=12, draft_type="snake",
                            fmt="dynasty", draft_status="complete", already_tracked=True)
    assert disc.already_tracked is True


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
    # `pass_int_td` (interception-return TD, credited against the passer)
    # is excluded despite matching the `pass_` prefix: it also appears on
    # real DEF rows in the 2026 projections snapshot, the same
    # cross-position ambiguity as `def_kr_td`/`pr_td`. See
    # `_DEFENSIVE_ONLY` and `is_defense_scoring_key`'s docstring.
    ("pass_int_td", False),
])
def test_offense_scoring_key_classification(key, expected):
    assert is_offense_scoring_key(key) is expected


@pytest.mark.parametrize("key,expected", [
    ("sack", True), ("int", True), ("fum_rec", True), ("blk_kick", True),
    ("safe", True), ("ff", True), ("def_td", True), ("def_st_td", True),
    ("def_fum_td", True),
    ("rec_td", False), ("pts_allow_0", False), ("pts_allow_35p", False),
    ("yds_allow_0_100", False), ("fgm_40_49", False),
    # Deliberately excluded even though it's a real defense-adjacent key --
    # `fum_rec_td` never appears on a real DEF stat line at all (verified
    # against 2025 actuals); the two rows that DO carry it are a real RB
    # and a real WR. ESPN's model is "points applies to every slot unless
    # pointsOverrides narrows it" (not the reverse, as an earlier version
    # of this comment claimed), but statId 63 genuinely has no D/ST
    # override in the real fixture either way, consistent with it not
    # applying to team defense. Keeping it excluded from offense too
    # (already true via `_DEFENSIVE_ONLY`) avoids crediting anyone for it.
    # See Task 3, the fix-wave report, and constants.py's `_DEFENSIVE_ONLY`
    # comment.
    ("fum_rec_td", False),
    # `def_kr_td` / `def_pr_td` (kickoff/punt-return TDs) are deliberately
    # excluded despite being in the original design's starting key set --
    # verified against real 2026 projections: `def_kr_td` is credited to
    # the individual returner (8 real rostered WR/RB rows carry it, e.g.
    # Rashid Shaheed), not exclusively to team DEF, so recognizing it here
    # would leak scoring onto those offensive players. `def_pr_td` never
    # appears in real data at all -- Sleeper's real vocabulary for punt
    # returns is bare `pr_td`, which leaks the same way `def_kr_td` does.
    # See `domain.constants._DEFENSE_BARE`'s comment and the fix-wave
    # report (Critical 1 / Important 5).
    ("def_kr_td", False), ("def_pr_td", False), ("pr_td", False),
    # Verified real Sleeper keys, present at nonzero weight in a real
    # connected league and on real DEF stat lines -- deliberately NOT
    # added. `def_st_ff`/`def_st_fum_rec` are the special-teams-play SUBSET
    # of the already-recognized `ff`/`fum_rec` totals (verified: always
    # <= the bare value for the same team-week, zero exceptions across
    # 2025 actuals); adding them double-counts. `pass_int_td` appears on
    # both real QB rows (2025 actuals) and real DEF rows (2026
    # projections) -- the same cross-position ambiguity as `def_kr_td`.
    ("def_st_ff", False), ("def_st_fum_rec", False),
    ("st_ff", False), ("st_fum_rec", False), ("pass_int_td", False),
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
