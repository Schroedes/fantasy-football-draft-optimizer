from ffdo.domain.models import PlayerProfile
from ffdo.ingest import snapshot
from ffdo.ingest.espn import crosswalk

ESPN_SNAPSHOT_DIR = snapshot.DEFAULT_SNAPSHOT_DIR.parent / "2026-08-23-espn-league"


def _profile(pid, first, last, position):
    return PlayerProfile(player_id=pid, first_name=first, last_name=last,
                         position=position, team="X", age=25, years_exp=3,
                         injury_status=None, active=True)


def test_normalize_name_strips_punctuation_and_suffixes():
    assert crosswalk.normalize_name("Ja'Marr Chase") == "jamarr chase"
    assert crosswalk.normalize_name("Odell Beckham Jr.") == "odell beckham"
    assert crosswalk.normalize_name("Michael Pittman III") == "michael pittman"


def test_build_resolves_via_espn_id_when_present():
    profiles = {"9999": _profile("9999", "Josh", "Allen", "QB")}
    espn_id_index = {"9999": "3918298"}
    espn_players = {"3918298": ("Josh Allen", "QB", 2)}

    cw = crosswalk.build(espn_id_index, profiles, espn_players)
    assert cw.espn_to_sleeper == {"3918298": "9999"}
    assert cw.unmatched == ()


def test_build_falls_back_to_normalized_name_and_position_match():
    profiles = {"1234": _profile("1234", "Ja'Marr", "Chase", "WR")}
    espn_players = {"555": ("Jamarr Chase", "WR", 4)}  # no espn_id hit

    cw = crosswalk.build({}, profiles, espn_players)
    assert cw.espn_to_sleeper == {"555": "1234"}


def test_build_excludes_an_ambiguous_fallback_match_rather_than_guessing():
    profiles = {
        "1": _profile("1", "John", "Smith", "WR"),
        "2": _profile("2", "John", "Smith", "WR"),
    }
    espn_players = {"999": ("John Smith", "WR", 4)}

    cw = crosswalk.build({}, profiles, espn_players)
    assert cw.espn_to_sleeper == {}
    assert cw.unmatched == ("999",)


def test_build_logs_a_warning_for_every_unmatched_player(caplog):
    """Explicit and logged, never silent -- the returned `unmatched` tuple
    is structured data a caller *can* act on, but this is what actually
    makes a real gap visible in the server's own logs without a caller
    having to remember to check it."""
    import logging
    with caplog.at_level(logging.WARNING, logger="ffdo.ingest.espn.crosswalk"):
        crosswalk.build({}, {}, {"999": ("Nobody Real", "WR", 4)})
    assert "999" in caplog.text


def test_build_excludes_a_player_with_no_match_at_all():
    cw = crosswalk.build({}, {}, {"999": ("Nobody Real", "WR", 4)})
    assert cw.unmatched == ("999",)


def test_build_resolves_a_team_defense_via_the_pro_team_id_table_not_name_matching():
    profiles = {}  # no individual-player match could ever apply to a defense
    espn_players = {"-16034": ("Texans D/ST", "DEF", 34)}

    cw = crosswalk.build({}, profiles, espn_players)
    assert cw.espn_to_sleeper == {"-16034": "HOU"}


def test_build_excludes_a_defense_whose_pro_team_id_has_no_table_entry():
    cw = crosswalk.build({}, {}, {"-99999": ("Made Up D/ST", "DEF", 99)})
    assert cw.unmatched == ("-99999",)


def test_parse_player_pool_against_the_real_dst_fixture():
    raw = snapshot.load("espnPlayersDst", snapshot_dir=ESPN_SNAPSHOT_DIR)
    pool = crosswalk.parse_player_pool(raw)
    assert len(pool) == 32
    assert pool["-16034"] == ("Texans D/ST", "DEF", 34)


def test_parse_player_pool_skips_unmapped_positions_in_a_broad_real_sample():
    """The real, unfiltered sample includes many non-fantasy-relevant IDP
    positions (LB, DE, CB, DT, ...) this project doesn't roster or value --
    they must be silently skipped, not misidentified as something else."""
    raw = snapshot.load("espnPlayersSample", snapshot_dir=ESPN_SNAPSHOT_DIR)
    pool = crosswalk.parse_player_pool(raw)
    assert 0 < len(pool) < len(raw)
    for full_name, position, _pro_team_id in pool.values():
        assert position in ("QB", "RB", "WR", "TE", "K", "DEF")


def test_espn_pro_team_id_table_matches_every_real_dst_entry_to_a_sleeper_abbreviation():
    """Full round-trip against real data: every one of the 32 real ESPN
    defenses must resolve to a Sleeper team abbreviation."""
    raw = snapshot.load("espnPlayersDst", snapshot_dir=ESPN_SNAPSHOT_DIR)
    pool = crosswalk.parse_player_pool(raw)
    cw = crosswalk.build({}, {}, pool)
    assert len(cw.espn_to_sleeper) == 32
    assert cw.unmatched == ()
