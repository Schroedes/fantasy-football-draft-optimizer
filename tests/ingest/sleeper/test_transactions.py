from ffdo.domain.models import DraftPickAsset
from ffdo.ingest.sleeper import transactions


class _FakeClient:
    def __init__(self, by_week):
        self._by_week = by_week

    def get_json(self, url):
        for week, payload in self._by_week.items():
            if f"/transactions/{week}" in url:
                return payload
        raise AssertionError(f"unexpected URL: {url}")


def test_fetch_trades_filters_to_completed_trades_only():
    client = _FakeClient({
        1: [
            {"type": "trade", "status": "complete", "transaction_id": "t1",
             "roster_ids": [2, 3], "adds": {"p1": 2}, "drops": {"p1": 3},
             "draft_picks": [], "created": 1000},
            {"type": "waiver", "status": "complete", "transaction_id": "w1",
             "roster_ids": [2], "adds": {"p9": 2}, "drops": {}, "created": 900},
            {"type": "trade", "status": "pending", "transaction_id": "t2",
             "roster_ids": [4, 5], "adds": {}, "drops": {}, "created": 1100},
        ],
    })
    result = transactions.fetch_trades(client, "L1", season=2026, through_week=1)
    assert len(result) == 1
    assert result[0].transaction_id == "t1"


def test_fetch_trades_splits_adds_by_roster_into_both_directions():
    client = _FakeClient({
        1: [{"type": "trade", "status": "complete", "transaction_id": "t1",
             "roster_ids": [2, 3],
             "adds": {"p1": 2, "p2": 3}, "drops": {"p1": 3, "p2": 2},
             "draft_picks": [], "created": 1000}],
    })
    result = transactions.fetch_trades(client, "L1", season=2026, through_week=1)
    trade = result[0]
    assert trade.roster_a_id == 2
    assert trade.roster_b_id == 3
    assert trade.roster_a_gets == ["p1"]
    assert trade.roster_b_gets == ["p2"]


def test_fetch_trades_parses_draft_picks_by_new_owner():
    client = _FakeClient({
        1: [{"type": "trade", "status": "complete", "transaction_id": "t1",
             "roster_ids": [2, 3], "adds": {}, "drops": {},
             "draft_picks": [{"round": 1, "season": "2027", "roster_id": 2,
                             "owner_id": 2, "previous_owner_id": 3}],
             "created": 1000}],
    })
    result = transactions.fetch_trades(client, "L1", season=2026, through_week=1)
    trade = result[0]
    assert trade.picks_to_b == []
    assert len(trade.picks_to_b) == 0
    # owner_id 2 == roster_a_id (first of sorted roster_ids) -- picks_to_a
    assert trade.picks_to_a == [
        DraftPickAsset(season=2027, round=1, projected_slot=None,
                       current_owner_roster_id=2, original_roster_id=3,
                       via_team_name=None)]


def test_fetch_trades_scans_every_week_through_the_given_week():
    client = _FakeClient({
        1: [{"type": "trade", "status": "complete", "transaction_id": "t1",
             "roster_ids": [2, 3], "adds": {}, "drops": {}, "draft_picks": [],
             "created": 1000}],
        2: [{"type": "trade", "status": "complete", "transaction_id": "t2",
             "roster_ids": [2, 3], "adds": {}, "drops": {}, "draft_picks": [],
             "created": 2000}],
    })
    result = transactions.fetch_trades(client, "L1", season=2026, through_week=2)
    assert {t.transaction_id for t in result} == {"t1", "t2"}
