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
    # A filler RB is required (same thin-pool pattern as
    # `test_optimal_slots_flex_takes_the_best_remaining_eligible_player`
    # and the Fix 4(b) test above): with only `p_bench` in the RB pool and
    # `num_teams=2`, the dedicated slot's greedy fill fully consumes the
    # 1-player pool, pinning the replacement floor at `p_bench`'s own
    # value -- VOR 0.0 by construction. Once `diff()` compares VOR instead
    # of only identity (this branch's Fix 2), a VOR-0.0 "best" for an empty
    # slot (baseline value 0.0 too) would be wrongly read as "no
    # improvement" even though a real player obviously beats an empty
    # slot. The filler gives `p_bench` genuine positive headroom instead.
    profiles = {"p_bench": _profile("p_bench", "RB", team="BENCH_TEAM"),
               "p_filler": _profile("p_filler", "RB", team="FILLER_TEAM")}
    weekly_points = {"p_bench": _proj("p_bench", 10, rush_yd=150.0),
                     "p_filler": _proj("p_filler", 10, rush_yd=5.0)}
    league = _League(starting_slots=("RB",))
    valued = _valued_from(profiles, weekly_points, league)
    assert valued["p_bench"].vor > 0
    optimal = weekly_lineup.optimal_slots(valued, league)
    assert optimal[0] == "p_bench"

    rows = weekly_lineup.diff((None,), optimal, frozenset({"BENCH_TEAM"}),
                              valued, profiles, league)
    assert rows[0].status == "missed"
    assert rows[0].current_player_id is None
    assert rows[0].optimal_player_id == "p_bench"


def test_diff_suggested_swap_for_an_empty_slot_whose_fix_is_still_unlocked():
    # See the filler-player note in the previous test -- same thin-pool fix.
    profiles = {"p_bench": _profile("p_bench", "RB", team="BENCH_TEAM"),
               "p_filler": _profile("p_filler", "RB", team="FILLER_TEAM")}
    weekly_points = {"p_bench": _proj("p_bench", 10, rush_yd=150.0),
                     "p_filler": _proj("p_filler", 10, rush_yd=5.0)}
    league = _League(starting_slots=("RB",))
    valued = _valued_from(profiles, weekly_points, league)
    assert valued["p_bench"].vor > 0
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
    not crash the diff, and reads as 0.0 rather than raising KeyError.

    A third, weak filler RB is included so `p_bench` (the bench replacement)
    gets genuine positive VOR headroom instead of becoming its own
    replacement-level floor by construction -- with only ONE surviving RB,
    `p_bench`'s VOR would be exactly 0.0 and the final `delta == 0.0`
    assertion couldn't tell "correctly falls back to 0.0" apart from
    "genuinely computed a real value that happens to be 0.0" (same
    thin-pool pattern already fixed elsewhere in this file, e.g.
    `test_optimal_slots_flex_takes_the_best_remaining_eligible_player`).
    """
    profiles = {"p_bye": _profile("p_bye", "RB", team="BYE_TEAM"),
               "p_bench": _profile("p_bench", "RB", team="BENCH_TEAM"),
               "p_filler": _profile("p_filler", "RB", team="FILLER_TEAM")}
    weekly_points = {"p_bye": _proj("p_bye", 10, rush_yd=150.0),
                     "p_bench": _proj("p_bench", 10, rush_yd=50.0),
                     "p_filler": _proj("p_filler", 10, rush_yd=5.0)}
    league = _League(starting_slots=("RB",))
    valued = _valued_from(profiles, weekly_points, league, bye_teams=frozenset({"BYE_TEAM"}))
    assert "p_bye" not in valued
    optimal = weekly_lineup.optimal_slots(valued, league)
    assert optimal[0] == "p_bench"

    rows = weekly_lineup.diff(("p_bye",), optimal, frozenset(), valued, profiles, league)
    assert rows[0].current_player_id == "p_bye"
    assert rows[0].status == "suggested_swap"
    assert valued["p_bench"].vor > 0   # genuine headroom, not a 0.0/0.0 coincidence
    assert rows[0].delta == round(valued["p_bench"].vor - 0.0, 1)


def test_diff_match_for_permutation_equivalent_lineup_not_offsetting_swaps():
    """Two RBs with IDENTICAL weekly points (hence identical VOR) can be
    slotted either way between the dedicated RB slot and FLEX with no
    change to total value. `optimal_slots` picks one specific arrangement
    (by its internal tie-break); if the user's actual lineup happens to
    have them the other way around, the diff must NOT report two
    offsetting "suggested_swap" rows (one recommending a change that is
    strictly worse) -- both slots are already optimal in aggregate, so
    both must read "match".

    Hand-verified VOR math: both players score rush_yd=100.0 -> identical
    `score_stats` output (10.0) -> identical `adjusted_points` -> the
    league-wide replacement level for RB (from `replacement_levels`, with
    `num_teams=2` and a 2-player pool, exhausts the whole pool in the
    dedicated-slot pass) lands at the LAST player's value, which for this
    tied pool is also 10.0 -- so VOR == 10.0 - 10.0 == 0.0 for BOTH
    players. `value_of(best) <= value_of(current)` (0.0 <= 0.0) therefore
    holds in both directions.
    """
    profiles = {"p_rb_a": _profile("p_rb_a", "RB"), "p_rb_b": _profile("p_rb_b", "RB")}
    weekly_points = {"p_rb_a": _proj("p_rb_a", 10, rush_yd=100.0),
                     "p_rb_b": _proj("p_rb_b", 10, rush_yd=100.0)}
    league = _League(starting_slots=("RB", "FLEX"))
    valued = _valued_from(profiles, weekly_points, league)
    assert valued["p_rb_a"].vor == 0.0
    assert valued["p_rb_b"].vor == 0.0
    optimal = weekly_lineup.optimal_slots(valued, league)
    assert optimal == {0: "p_rb_b", 1: "p_rb_a"}

    # Current lineup has the pair in the REVERSE arrangement from `optimal`.
    rows = weekly_lineup.diff(("p_rb_a", "p_rb_b"), optimal, frozenset(),
                              valued, profiles, league)
    assert rows[0].status == "match"
    assert rows[1].status == "match"


def test_diff_never_suggests_bringing_in_a_player_already_starting_elsewhere():
    """`optimal_slots` solves for the single best overall arrangement, which
    can reassign an already-started player to a DIFFERENT slot than the one
    they currently occupy (e.g. the current RB-slot starter is actually the
    correct FLEX pick once a stronger bench RB claims the dedicated slot).
    `diff` must not report that reassignment as its own "suggested_swap" --
    the player isn't sitting on the bench, so there's nothing to "bring in"
    for that slot in isolation; only the one real bench-sourced upgrade
    (the dedicated RB slot below) should be actionable.

    Same player/value setup as
    `test_optimal_slots_flex_takes_the_best_remaining_eligible_player`:
    `optimal_slots` assigns RB -> p_rb_hi, FLEX -> p_rb_mid. The CURRENT
    lineup instead has p_rb_mid in the dedicated RB slot and a WR in FLEX,
    with p_rb_hi still on the bench.
    """
    profiles = {"p_rb_hi": _profile("p_rb_hi", "RB"), "p_rb_mid": _profile("p_rb_mid", "RB"),
               "p_rb_lo": _profile("p_rb_lo", "RB"), "p_wr": _profile("p_wr", "WR")}
    weekly_points = {
        "p_rb_hi": _proj("p_rb_hi", 10, rush_yd=150.0),
        "p_rb_mid": _proj("p_rb_mid", 10, rush_yd=60.0),
        "p_rb_lo": _proj("p_rb_lo", 10, rush_yd=5.0),
        "p_wr": _proj("p_wr", 10, rec=3.0, rec_yd=20.0),
    }
    league = _League(starting_slots=("RB", "FLEX"))
    valued = _valued_from(profiles, weekly_points, league)
    optimal = weekly_lineup.optimal_slots(valued, league)
    assert optimal == {0: "p_rb_hi", 1: "p_rb_mid"}

    # Current lineup: p_rb_mid started at RB (not yet swapped for p_rb_hi),
    # a WR in FLEX, p_rb_hi still on the bench.
    rows = weekly_lineup.diff(("p_rb_mid", "p_wr"), optimal, frozenset(),
                              valued, profiles, league)

    rb_row, flex_row = rows
    assert rb_row.status == "suggested_swap"
    assert rb_row.current_player_id == "p_rb_mid"
    assert rb_row.optimal_player_id == "p_rb_hi"

    # p_rb_mid is already starting (at RB) -- FLEX must not "suggest" him.
    assert flex_row.status == "match"
    assert flex_row.optimal_player_id is None


def test_diff_match_when_no_eligible_replacement_exists_at_all():
    """A bye-week starter with NO bench replacement league-wide at that
    position (`optimal.get(i)` is `None`) must read as "match", not a
    "suggested_swap" to a blank optimal player."""
    profiles = {"p_bye": _profile("p_bye", "RB", team="BYE_TEAM")}
    weekly_points = {"p_bye": _proj("p_bye", 10, rush_yd=150.0)}
    league = _League(starting_slots=("RB",))
    valued = _valued_from(profiles, weekly_points, league, bye_teams=frozenset({"BYE_TEAM"}))
    assert valued == {}
    optimal = weekly_lineup.optimal_slots(valued, league)
    assert optimal[0] is None

    rows = weekly_lineup.diff(("p_bye",), optimal, frozenset(), valued, profiles, league)
    assert rows[0].status == "match"
    assert rows[0].current_player_id == "p_bye"
    assert rows[0].optimal_player_id is None
