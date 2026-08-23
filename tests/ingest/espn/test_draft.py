from ffdo.ingest import snapshot
from ffdo.ingest.espn import draft
from ffdo.ingest.espn.crosswalk import Crosswalk

ESPN_SNAPSHOT_DIR = snapshot.DEFAULT_SNAPSHOT_DIR.parent / "2026-08-23-espn-league"


def _mdraft_detail():
    return snapshot.load("mDraftDetail", snapshot_dir=ESPN_SNAPSHOT_DIR)


def test_a_pre_draft_league_produces_correct_metadata_and_zero_picks():
    """Verified live 2026-08-23: this real league's draft hasn't started --
    ESPN pre-populates the *entire* 150-slot picks array with playerId: -1
    placeholders. None of them are real picks."""
    state = draft.parse(_mdraft_detail(), Crosswalk(espn_to_sleeper={}, unmatched=()))
    assert state.picks == ()
    assert state.num_teams == 10
    assert state.rounds == 15
    assert state.draft_type == "snake"
    assert state.status == "pre_draft"
    assert state.draft_id == "1882997948"


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
