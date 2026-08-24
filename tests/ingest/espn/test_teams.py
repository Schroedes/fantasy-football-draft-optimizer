from ffdo.ingest import snapshot
from ffdo.ingest.espn import teams

ESPN_SNAPSHOT_DIR = snapshot.DEFAULT_SNAPSHOT_DIR.parent / "2026-08-23-espn-league"


def _mteam():
    return snapshot.load("mTeam", snapshot_dir=ESPN_SNAPSHOT_DIR)


def test_parse_builds_a_team_profile_per_real_team():
    out = teams.parse(_mteam())
    assert len(out) == 10
    assert all(isinstance(rid, int) for rid in out)


def test_parse_still_names_a_team_with_no_owner():
    """The real league's team id 4 has an empty owners list (an unclaimed
    slot) -- must still produce a named team, not be skipped or crash."""
    out = teams.parse(_mteam())
    assert out[4].roster_id == 4
    assert out[4].display_name


def test_find_roster_id_resolves_the_swid_that_owns_a_team():
    """Verified against the real league: the sanitized fixture's synthetic
    stand-in for the real connecting user's SWID resolves to teamId 7."""
    roster_id = teams.find_roster_id(
        _mteam(), "{00000004-0000-0000-0000-000000000000}")
    assert roster_id == 7


def test_find_roster_id_returns_none_for_an_unknown_swid():
    assert teams.find_roster_id(
        _mteam(), "{ffffffff-0000-0000-0000-000000000000}") is None


def test_find_roster_id_matches_case_insensitively():
    lowercase_swid = "{00000004-0000-0000-0000-000000000000}".lower()
    roster_id = teams.find_roster_id(_mteam(), lowercase_swid)
    assert roster_id == 7
