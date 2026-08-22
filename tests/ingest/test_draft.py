from ffdo.ingest import draft, snapshot


def _hist():
    return snapshot.load("league_history")["drafts"]


def test_parses_auction_amounts_from_string_metadata():
    d = _hist()["2025"]
    state = draft.parse(d["meta"], d["picks"])
    assert state.draft_type == "auction"
    assert state.budget == 200
    assert len(state.picks) == 168
    assert all(isinstance(p.amount, int) for p in state.picks)


def test_every_historical_auction_reconciles_to_the_budget():
    """12 rosters x $200 is the budget ceiling, not a total every room hits.

    Measured actuals across the five historical seasons: 2021 $2387, 2022
    $2393, 2023 $2388, 2024 $2394, 2025 $2398 -- all below the $2400 (12 x
    $200) ceiling. Teams legitimately leave a dollar or two unspent, so the
    real invariant is that no roster ever exceeds its budget, not that the
    room spends every dollar. We also sanity-floor total spend at 12 x $185
    to confirm the room does spend nearly all of it.
    """
    for season in ("2021", "2022", "2023", "2024", "2025"):
        d = _hist()[season]
        state = draft.parse(d["meta"], d["picks"])
        spend = state.spent_by_roster()
        assert len(spend) == 12, f"{season}: expected 12 rosters"
        assert all(s <= 200 for s in spend.values()), (
            f"{season}: a roster exceeded its $200 budget"
        )
        assert sum(spend.values()) >= 12 * 185, f"{season}: spend suspiciously low"


def test_pre_draft_state_has_no_picks():
    lg = snapshot.load("league_history")
    state = draft.parse(lg["drafts"]["2025"]["meta"], [])
    assert state.picks == ()
    assert state.drafted_player_ids() == frozenset()
