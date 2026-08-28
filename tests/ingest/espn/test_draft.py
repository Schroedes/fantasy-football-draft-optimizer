from ffdo.ingest import snapshot
from ffdo.ingest.espn import draft
from ffdo.ingest.espn.crosswalk import Crosswalk

ESPN_SNAPSHOT_DIR = snapshot.DEFAULT_SNAPSHOT_DIR.parent / "2026-08-23-espn-league"


def _combined_league_response():
    """The real API returns one merged object when multiple `view=` params
    are requested together; mSettings/mDraftDetail were captured as two
    separate single-view requests during design validation, so recombine
    them here to match draft.parse()'s actual production input shape (see
    ffdo.ingest.espn.connect.resolve, which fetches exactly this combined
    view in one request).

    A plain shallow `{**settings_raw, **draft_raw}` spread does not
    reproduce that shape: both single-view fixtures carry the same
    top-level skeleton (`settings`, `draftDetail`, ...) but each view only
    populates its own slice -- mSettings' `settings` has the full object
    (including `size`) while its `draftDetail` has no `picks`; mDraftDetail
    is the exact opposite (`settings` has only `draftSettings`, but
    `draftDetail` has the full 150-pick array). A shallow spread lets
    whichever fixture is merged in last clobber the other's populated
    key, so this explicitly takes the full `settings` from mSettings and
    the full `draftDetail` from mDraftDetail -- what a real single
    multi-view request actually returns."""
    settings_raw = snapshot.load("mSettings", snapshot_dir=ESPN_SNAPSHOT_DIR)
    draft_raw = snapshot.load("mDraftDetail", snapshot_dir=ESPN_SNAPSHOT_DIR)
    return {**settings_raw, "draftDetail": draft_raw["draftDetail"]}


def test_a_pre_draft_league_produces_correct_metadata_and_zero_picks():
    """Verified live 2026-08-23: this real league's draft hasn't started --
    ESPN pre-populates the *entire* 150-slot picks array with playerId: -1
    placeholders. None of them are real picks."""
    state = draft.parse(_combined_league_response(), Crosswalk(espn_to_sleeper={}, unmatched=()))
    assert state.picks == ()
    assert state.num_teams == 10
    assert state.rounds == 15
    assert state.draft_type == "snake"
    assert state.status == "pre_draft"
    assert state.draft_id == "1882997948"


def test_draft_order_is_populated_from_round_one_even_before_any_picks_are_made():
    """The whole point of `draft_order`: ESPN's round-1 slots already carry
    a real teamId even when every pick in the league is still an unplayed
    placeholder (this is the SAME real pre-draft snapshot as the test
    above, where `state.picks == ()`) -- so a roster's seat should be
    knowable immediately, not only after that roster's first real pick."""
    state = draft.parse(_combined_league_response(), Crosswalk(espn_to_sleeper={}, unmatched=()))
    assert state.picks == ()
    assert state.draft_order is not None
    assert len(state.draft_order) == state.num_teams == 10
    # Every roster gets a distinct slot 1..num_teams (a real snake order).
    assert set(state.draft_order.values()) == set(range(1, 11))


def test_draft_order_covers_a_team_whose_own_pick_is_still_unplayed():
    """Direct proof this doesn't just fall back to reading picks: team 1's
    round-1 slot is still a placeholder (playerId: -1, excluded from
    `state.picks` per test_filters_out_unplayed_placeholder_picks above),
    but it must still appear in `draft_order` -- that's the entire reason
    this field exists instead of just inferring from `state.picks`."""
    raw = _synthetic_raw([
        {"playerId": -1, "teamId": 1, "roundId": 1, "roundPickNumber": 1,
         "overallPickNumber": 1, "bidAmount": 0},
        {"playerId": 4984, "teamId": 2, "roundId": 1, "roundPickNumber": 2,
         "overallPickNumber": 2, "bidAmount": 0},
    ])
    cw = Crosswalk(espn_to_sleeper={"4984": "sleeper-4984"}, unmatched=())

    state = draft.parse(raw, cw)

    assert len(state.picks) == 1  # team 1's slot is still unplayed
    assert state.draft_order == {1: 1, 2: 2}


def _synthetic_raw(picks):
    return {
        "id": 1882997948,
        "settings": {"size": 2, "draftSettings": {"type": "SNAKE", "auctionBudget": 200}},
        "draftDetail": {"drafted": False, "inProgress": True, "picks": picks},
    }


def test_filters_out_unplayed_placeholder_picks():
    raw = _synthetic_raw([
        {"playerId": -1, "teamId": 1, "roundId": 1, "roundPickNumber": 1,
         "overallPickNumber": 1, "bidAmount": 0},
        {"playerId": 4984, "teamId": 2, "roundId": 1, "roundPickNumber": 2,
         "overallPickNumber": 2, "bidAmount": 0},
    ])
    cw = Crosswalk(espn_to_sleeper={"4984": "sleeper-4984"}, unmatched=())

    state = draft.parse(raw, cw)

    assert len(state.picks) == 1
    pick = state.picks[0]
    assert pick.player_id == "sleeper-4984"
    assert pick.pick_no == 2
    assert pick.round == 1
    assert pick.draft_slot == 2
    assert pick.roster_id == 2
    assert pick.amount is None  # bidAmount 0 -> None, matching snake semantics
    assert pick.picked_by is None


def test_excludes_a_pick_whose_player_the_crosswalk_could_not_match():
    raw = _synthetic_raw([
        {"playerId": 99999, "teamId": 1, "roundId": 1, "roundPickNumber": 1,
         "overallPickNumber": 1, "bidAmount": 0},
    ])
    cw = Crosswalk(espn_to_sleeper={}, unmatched=("99999",))

    state = draft.parse(raw, cw)
    assert state.picks == ()


def test_carries_a_real_auction_bid_amount_through_when_present():
    """The parser is generic even though this project only connects ESPN
    snake leagues in this pass -- exercising the auction field costs
    nothing extra and proves it isn't silently dropped."""
    raw = _synthetic_raw([
        {"playerId": 4984, "teamId": 1, "roundId": 1, "roundPickNumber": 1,
         "overallPickNumber": 1, "bidAmount": 55},
    ])
    cw = Crosswalk(espn_to_sleeper={"4984": "sleeper-4984"}, unmatched=())

    state = draft.parse(raw, cw)
    assert state.picks[0].amount == 55
