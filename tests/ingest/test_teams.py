from ffdo.ingest import teams


def test_parse_prefers_team_name_over_display_name():
    rosters = [{"roster_id": 1, "owner_id": "u1"}]
    users = [{"user_id": "u1", "display_name": "user1",
             "metadata": {"team_name": "The Foobars"}}]
    out = teams.parse(rosters, users)
    assert out[1].display_name == "The Foobars"


def test_parse_falls_back_to_display_name_without_team_name():
    rosters = [{"roster_id": 2, "owner_id": "u2"}]
    users = [{"user_id": "u2", "display_name": "CoolTeam", "metadata": {}}]
    out = teams.parse(rosters, users)
    assert out[2].display_name == "CoolTeam"


def test_parse_falls_back_to_roster_id_label_without_owner_or_user_match():
    rosters = [{"roster_id": 3, "owner_id": None}]
    users = []
    out = teams.parse(rosters, users)
    assert out[3].display_name == "Team 3"


def test_parse_falls_back_when_owner_id_matches_no_user_record():
    rosters = [{"roster_id": 4, "owner_id": "ghost"}]
    users = [{"user_id": "u1", "display_name": "someone else"}]
    out = teams.parse(rosters, users)
    assert out[4].display_name == "Team 4"


def test_parse_keys_output_by_roster_id_as_int():
    rosters = [{"roster_id": "5", "owner_id": "u5"}]
    users = [{"user_id": "u5", "display_name": "Five"}]
    out = teams.parse(rosters, users)
    assert out[5].roster_id == 5


def test_display_names_helper_is_reused_by_parse():
    from ffdo.ingest.teams import _display_names
    users = [{"user_id": "u1", "display_name": "u1", "metadata": {"team_name": "Alpha"}}]
    assert _display_names(users) == {"u1": "Alpha"}
    # parse still works exactly as before
    out = teams.parse([{"roster_id": 1, "owner_id": "u1"}], users)
    assert out[1].display_name == "Alpha"
