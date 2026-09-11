from ffdo.domain.models import PlayerProfile, SlotDiff, WeeklyProjection
from ffdo.engine import weekly_lineup

# Match the League fixture shape used in tests/engine/test_ros_value.py --
# a minimal object with `.scoring_settings` and `.starting_slots`.
# (Copy that exact helper/class here rather than importing across test
# files, matching this codebase's existing tests/engine/ convention of
# each test file owning its own fixtures.)


def _profile(pid, position, team="AAA", injury_status=None, active=True):
    return PlayerProfile(
        player_id=pid, first_name=pid, last_name="X", position=position,
        team=team, age=25, years_exp=3, injury_status=injury_status,
        active=active,
    )


def _proj(pid, week, **stats):
    return WeeklyProjection(player_id=pid, season=2026, week=week, stats=stats)


SCORING = {"pass_yd": 0.04, "pass_td": 4.0, "rush_yd": 0.1, "rush_td": 6.0,
          "rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0}


class _League:
    def __init__(self, starting_slots, scoring_settings=SCORING, num_teams=2):
        self.starting_slots = starting_slots
        self.scoring_settings = scoring_settings
        self.num_teams = num_teams


def test_weekly_value_excludes_a_bye_team_player_entirely():
    profiles = {"p1": _profile("p1", "RB", team="BYE")}
    weekly_points = {"p1": _proj("p1", 10, rush_yd=100.0, rush_td=1.0)}
    league = _League(starting_slots=("RB",))

    valued = weekly_lineup.weekly_value(
        ["p1"], league, weekly_points=weekly_points, profiles=profiles,
        bye_teams=frozenset({"BYE"}))
    assert "p1" not in valued


def test_weekly_value_excludes_a_hard_out_player_entirely():
    profiles = {"p1": _profile("p1", "RB", injury_status="Out")}
    weekly_points = {"p1": _proj("p1", 10, rush_yd=100.0, rush_td=1.0)}
    league = _League(starting_slots=("RB",))

    valued = weekly_lineup.weekly_value(
        ["p1"], league, weekly_points=weekly_points, profiles=profiles,
        bye_teams=frozenset())
    assert "p1" not in valued


def test_weekly_value_includes_a_merely_questionable_player():
    profiles = {"p1": _profile("p1", "RB", injury_status="Questionable")}
    weekly_points = {"p1": _proj("p1", 10, rush_yd=100.0, rush_td=1.0)}
    league = _League(starting_slots=("RB",))

    valued = weekly_lineup.weekly_value(
        ["p1"], league, weekly_points=weekly_points, profiles=profiles,
        bye_teams=frozenset())
    assert "p1" in valued


def test_optimal_slots_assigns_dedicated_slots_by_rank():
    profiles = {"p_hi": _profile("p_hi", "RB"), "p_lo": _profile("p_lo", "RB")}
    weekly_points = {"p_hi": _proj("p_hi", 10, rush_yd=150.0),
                     "p_lo": _proj("p_lo", 10, rush_yd=20.0)}
    league = _League(starting_slots=("RB",))

    valued = weekly_lineup.weekly_value(
        ["p_hi", "p_lo"], league, weekly_points=weekly_points,
        profiles=profiles, bye_teams=frozenset())
    slots = weekly_lineup.optimal_slots(valued, league)
    assert slots == {0: "p_hi"}


def test_optimal_slots_flex_takes_the_best_remaining_eligible_player():
    profiles = {"p_rb1": _profile("p_rb1", "RB"), "p_rb2": _profile("p_rb2", "RB"),
               "p_rb3": _profile("p_rb3", "RB"), "p_wr1": _profile("p_wr1", "WR")}
    weekly_points = {
        "p_rb1": _proj("p_rb1", 10, rush_yd=150.0),   # best RB -> dedicated RB slot
        "p_rb2": _proj("p_rb2", 10, rush_yd=60.0),    # 2nd RB -> should win FLEX over p_wr1
        "p_rb3": _proj("p_rb3", 10, rush_yd=5.0),     # weak filler -- absorbs the replacement-level floor
                                                        # so p_rb2 gets a genuine positive VOR instead of
                                                        # becoming its own replacement level (the bug: with
                                                        # only 2 RBs, p_rb2 was tied at VOR=0.0 with p_wr1,
                                                        # making the FLEX pick depend on hash-seed-randomized
                                                        # frozenset iteration order)
        "p_wr1": _proj("p_wr1", 10, rec=3.0, rec_yd=20.0),   # weaker than p_rb2 on VOR
    }
    league = _League(starting_slots=("RB", "FLEX"))

    valued = weekly_lineup.weekly_value(
        ["p_rb1", "p_rb2", "p_rb3", "p_wr1"], league, weekly_points=weekly_points,
        profiles=profiles, bye_teams=frozenset())
    slots = weekly_lineup.optimal_slots(valued, league)
    assert slots[0] == "p_rb1"
    assert slots[1] == "p_rb2"


def test_optimal_slots_processes_dedicated_slots_before_flex_regardless_of_roster_order():
    """The critical ordering case: FLEX appears BEFORE the dedicated RB slot
    in `starting_slots`. `engine.replacement.greedy_fill_slots` always fills
    ALL dedicated slots first, then FLEX -- `optimal_slots` must reproduce
    that two-phase order, not a naive left-to-right walk of
    `starting_slots`, or FLEX could steal the best RB before the dedicated
    RB slot gets a turn."""
    profiles = {"p_rb1": _profile("p_rb1", "RB"), "p_rb2": _profile("p_rb2", "RB")}
    weekly_points = {"p_rb1": _proj("p_rb1", 10, rush_yd=150.0),
                     "p_rb2": _proj("p_rb2", 10, rush_yd=60.0)}
    league = _League(starting_slots=("FLEX", "RB"))   # FLEX listed FIRST

    valued = weekly_lineup.weekly_value(
        ["p_rb1", "p_rb2"], league, weekly_points=weekly_points,
        profiles=profiles, bye_teams=frozenset())
    slots = weekly_lineup.optimal_slots(valued, league)
    # Dedicated RB slot (index 1) must get the BEST RB (p_rb1), even though
    # FLEX (index 0) is processed... wait, is listed first in the tuple.
    # A naive left-to-right walk would let FLEX (index 0) grab p_rb1 first.
    # The correct two-phase algorithm fills the dedicated RB slot (index 1)
    # with p_rb1 and leaves FLEX (index 0) with the remaining p_rb2.
    assert slots[1] == "p_rb1"
    assert slots[0] == "p_rb2"


def test_optimal_slots_none_when_fewer_eligible_players_than_slots():
    profiles = {"p1": _profile("p1", "RB")}
    weekly_points = {"p1": _proj("p1", 10, rush_yd=100.0)}
    league = _League(starting_slots=("RB", "RB"))

    valued = weekly_lineup.weekly_value(
        ["p1"], league, weekly_points=weekly_points, profiles=profiles,
        bye_teams=frozenset())
    slots = weekly_lineup.optimal_slots(valued, league)
    assert slots[0] == "p1"
    assert slots[1] is None


def _valued_from(profiles, weekly_points, league, bye_teams=frozenset()):
    return weekly_lineup.weekly_value(
        list(profiles), league, weekly_points=weekly_points, profiles=profiles,
        bye_teams=bye_teams)


def test_diff_match_when_current_equals_optimal():
    profiles = {"p1": _profile("p1", "RB")}
    weekly_points = {"p1": _proj("p1", 10, rush_yd=100.0)}
    league = _League(starting_slots=("RB",))
    valued = _valued_from(profiles, weekly_points, league)
    optimal = weekly_lineup.optimal_slots(valued, league)

    rows = weekly_lineup.diff(("p1",), optimal, frozenset(), valued, profiles, league)
    assert rows == [SlotDiff(slot_index=0, slot_label="RB", status="match",
                             current_player_id="p1", optimal_player_id=None, delta=0.0)]


def test_diff_suggested_swap_when_better_option_is_unlocked():
    profiles = {"p_bench": _profile("p_bench", "RB", team="BENCH_TEAM"),
               "p_started": _profile("p_started", "RB", team="STARTED_TEAM")}
    weekly_points = {"p_bench": _proj("p_bench", 10, rush_yd=150.0),
                     "p_started": _proj("p_started", 10, rush_yd=20.0)}
    league = _League(starting_slots=("RB",))
    valued = _valued_from(profiles, weekly_points, league)
    optimal = weekly_lineup.optimal_slots(valued, league)
    assert optimal[0] == "p_bench"

    rows = weekly_lineup.diff(("p_started",), optimal, frozenset(),
                              valued, profiles, league)
    row = rows[0]
    assert row.status == "suggested_swap"
    assert row.current_player_id == "p_started"
    assert row.optimal_player_id == "p_bench"
    assert row.delta > 0


def test_diff_missed_when_current_starters_team_already_locked():
    profiles = {"p_bench": _profile("p_bench", "RB", team="BENCH_TEAM"),
               "p_started": _profile("p_started", "RB", team="STARTED_TEAM")}
    weekly_points = {"p_bench": _proj("p_bench", 10, rush_yd=150.0),
                     "p_started": _proj("p_started", 10, rush_yd=20.0)}
    league = _League(starting_slots=("RB",))
    valued = _valued_from(profiles, weekly_points, league)
    optimal = weekly_lineup.optimal_slots(valued, league)

    rows = weekly_lineup.diff(("p_started",), optimal, frozenset({"STARTED_TEAM"}),
                              valued, profiles, league)
    assert rows[0].status == "missed"


def test_diff_missed_for_an_empty_slot_whose_only_fix_already_locked():
    profiles = {"p_bench": _profile("p_bench", "RB", team="BENCH_TEAM")}
    weekly_points = {"p_bench": _proj("p_bench", 10, rush_yd=150.0)}
    league = _League(starting_slots=("RB",))
    valued = _valued_from(profiles, weekly_points, league)
    optimal = weekly_lineup.optimal_slots(valued, league)
    assert optimal[0] == "p_bench"

    rows = weekly_lineup.diff((None,), optimal, frozenset({"BENCH_TEAM"}),
                              valued, profiles, league)
    assert rows[0].status == "missed"
    assert rows[0].current_player_id is None
    assert rows[0].optimal_player_id == "p_bench"


def test_diff_suggested_swap_for_an_empty_slot_whose_fix_is_still_unlocked():
    profiles = {"p_bench": _profile("p_bench", "RB", team="BENCH_TEAM")}
    weekly_points = {"p_bench": _proj("p_bench", 10, rush_yd=150.0)}
    league = _League(starting_slots=("RB",))
    valued = _valued_from(profiles, weekly_points, league)
    optimal = weekly_lineup.optimal_slots(valued, league)

    rows = weekly_lineup.diff((None,), optimal, frozenset(),
                              valued, profiles, league)
    assert rows[0].status == "suggested_swap"


def test_diff_match_when_both_current_and_optimal_are_empty():
    league = _League(starting_slots=("RB",))
    rows = weekly_lineup.diff((None,), {0: None}, frozenset(), {}, {}, league)
    assert rows[0].status == "match"
    assert rows[0].current_player_id is None
    assert rows[0].optimal_player_id is None


def test_diff_current_starter_excluded_from_valued_still_shows_a_zero_value():
    """A current starter who was excluded from `valued` (bye/hard-out) must
    not crash the diff, and reads as 0.0 rather than raising KeyError."""
    profiles = {"p_bye": _profile("p_bye", "RB", team="BYE_TEAM"),
               "p_bench": _profile("p_bench", "RB", team="BENCH_TEAM")}
    weekly_points = {"p_bye": _proj("p_bye", 10, rush_yd=150.0),
                     "p_bench": _proj("p_bench", 10, rush_yd=50.0)}
    league = _League(starting_slots=("RB",))
    valued = _valued_from(profiles, weekly_points, league, bye_teams=frozenset({"BYE_TEAM"}))
    assert "p_bye" not in valued
    optimal = weekly_lineup.optimal_slots(valued, league)

    rows = weekly_lineup.diff(("p_bye",), optimal, frozenset(), valued, profiles, league)
    assert rows[0].current_player_id == "p_bye"
    assert rows[0].status == "suggested_swap"
    assert rows[0].delta == round(valued["p_bench"].vor - 0.0, 1)
