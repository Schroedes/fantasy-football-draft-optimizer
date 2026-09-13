from ffdo.domain.models import WaiverClaim
from ffdo.ingest.sleeper import waivers


class _FakeClient:
    def __init__(self, by_week):
        self._by_week = by_week

    def get_json(self, url):
        for week, payload in self._by_week.items():
            if f"/transactions/{week}" in url:
                return payload
        raise AssertionError(f"unexpected URL: {url}")


def test_fetch_waivers_filters_to_completed_waivers_only():
    client = _FakeClient({
        1: [
            {"type": "waiver", "status": "complete", "transaction_id": "w1",
             "roster_ids": [3], "adds": {"p1": 3},
             "settings": {"waiver_bid": 12}, "created": 1000},
            {"type": "trade", "status": "complete", "transaction_id": "t1",
             "roster_ids": [2, 3], "adds": {"p9": 2}, "created": 900},
            {"type": "waiver", "status": "failed", "transaction_id": "w2",
             "roster_ids": [4], "adds": {"p2": 4},
             "settings": {"waiver_bid": 5}, "created": 1100},
            {"type": "free_agent", "status": "complete", "transaction_id": "f1",
             "roster_ids": [5], "adds": {"p3": 5}, "created": 1200},
        ],
    })
    result = waivers.fetch_waivers(client, "L1", season=2026, through_week=1)
    assert len(result) == 1
    assert result[0].transaction_id == "w1"


def test_fetch_waivers_parses_the_real_fields():
    client = _FakeClient({
        1: [{"type": "waiver", "status": "complete", "transaction_id": "w1",
             "roster_ids": [3], "adds": {"p1": 3},
             "settings": {"waiver_bid": 12}, "created": 1700000000000}],
    })
    result = waivers.fetch_waivers(client, "L1", season=2026, through_week=1)
    claim = result[0]
    assert claim.season == 2026
    assert claim.week == 1
    assert claim.roster_id == 3
    assert claim.player_id == "p1"
    assert claim.bid_amount == 12.0
    assert claim.created_ms == 1700000000000


def test_fetch_waivers_defaults_a_missing_bid_to_zero():
    """A `free_agent`-shaped waiver claim (uncontested, no bid) still has
    type=='waiver' on some leagues -- settings/waiver_bid can be absent or
    None rather than 0. Must not crash, must default to 0.0."""
    client = _FakeClient({
        1: [{"type": "waiver", "status": "complete", "transaction_id": "w1",
             "roster_ids": [3], "adds": {"p1": 3}, "settings": None,
             "created": 1000}],
    })
    result = waivers.fetch_waivers(client, "L1", season=2026, through_week=1)
    assert result[0].bid_amount == 0.0


def test_fetch_waivers_scans_every_week_through_the_given_week():
    client = _FakeClient({
        1: [{"type": "waiver", "status": "complete", "transaction_id": "w1",
             "roster_ids": [3], "adds": {"p1": 3},
             "settings": {"waiver_bid": 1}, "created": 1000}],
        2: [{"type": "waiver", "status": "complete", "transaction_id": "w2",
             "roster_ids": [3], "adds": {"p2": 3},
             "settings": {"waiver_bid": 2}, "created": 2000}],
    })
    result = waivers.fetch_waivers(client, "L1", season=2026, through_week=2)
    assert {c.transaction_id for c in result} == {"w1", "w2"}


def test_remaining_budget_sums_every_claim_for_a_roster_regardless_of_order():
    """remaining_budget computes a final cumulative sum per roster --
    addition is commutative, so this deliberately feeds claims in an
    arbitrary (not necessarily chronological) order and confirms the
    total is still correct. This function does NOT need chronological
    ordering to be correct (unlike scripts/fit_faab_curve.py's own walk,
    which needs each claim's INTERMEDIATE remaining-before state, not
    just a final total -- a genuinely different, order-sensitive need)."""
    claims = [
        WaiverClaim(transaction_id="b", season=2026, week=2, roster_id=5,
                   player_id="p2", bid_amount=30.0, created_ms=3000),
        WaiverClaim(transaction_id="a", season=2026, week=1, roster_id=5,
                   player_id="p1", bid_amount=10.0, created_ms=1000),
    ]
    result = waivers.remaining_budget(claims, waiver_budget=100.0)
    assert result[5] == 60.0


def test_remaining_budget_tracks_each_roster_independently():
    claims = [
        WaiverClaim(transaction_id="a", season=2026, week=1, roster_id=1,
                   player_id="p1", bid_amount=20.0, created_ms=1000),
        WaiverClaim(transaction_id="b", season=2026, week=1, roster_id=2,
                   player_id="p2", bid_amount=5.0, created_ms=1100),
    ]
    result = waivers.remaining_budget(claims, waiver_budget=100.0)
    assert result[1] == 80.0
    assert result[2] == 95.0


def test_remaining_budget_defaults_untouched_rosters_to_the_full_budget():
    result = waivers.remaining_budget([], waiver_budget=150.0)
    assert result == {}


def test_fetch_all_claims_includes_both_wins_and_losses():
    client = _FakeClient({
        1: [
            {"type": "waiver", "status": "complete", "transaction_id": "w1",
             "roster_ids": [3], "adds": {"p1": 3},
             "settings": {"waiver_bid": 12}, "created": 1000},
            {"type": "waiver", "status": "failed", "transaction_id": "w2",
             "roster_ids": [4], "adds": {"p2": 4},
             "settings": {"waiver_bid": 5}, "created": 1100},
            {"type": "trade", "status": "complete", "transaction_id": "t1",
             "roster_ids": [2, 3], "adds": {"p9": 2}, "created": 900},
        ],
    })
    result = waivers.fetch_all_claims(client, "L1", season=2026, through_week=1)
    assert {c.transaction_id for c in result} == {"w1", "w2"}
    won_by_id = {c.transaction_id: c.won for c in result}
    assert won_by_id["w1"] is True
    assert won_by_id["w2"] is False


def test_fetch_all_claims_scans_every_week_through_the_given_week():
    client = _FakeClient({
        1: [{"type": "waiver", "status": "complete", "transaction_id": "w1",
             "roster_ids": [3], "adds": {"p1": 3},
             "settings": {"waiver_bid": 1}, "created": 1000}],
        2: [{"type": "waiver", "status": "failed", "transaction_id": "w2",
             "roster_ids": [3], "adds": {"p2": 3},
             "settings": {"waiver_bid": 2}, "created": 2000}],
    })
    result = waivers.fetch_all_claims(client, "L1", season=2026, through_week=2)
    assert {c.transaction_id for c in result} == {"w1", "w2"}


def test_fetch_waivers_claims_default_won_to_true():
    client = _FakeClient({
        1: [{"type": "waiver", "status": "complete", "transaction_id": "w1",
             "roster_ids": [3], "adds": {"p1": 3},
             "settings": {"waiver_bid": 12}, "created": 1000}],
    })
    result = waivers.fetch_waivers(client, "L1", season=2026, through_week=1)
    assert result[0].won is True
