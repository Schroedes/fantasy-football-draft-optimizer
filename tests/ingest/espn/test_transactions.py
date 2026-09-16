import httpx

from ffdo.ingest.espn import transactions
from ffdo.ingest.espn.client import EspnClient


class _CW:
    """Minimal crosswalk stand-in matching the real Crosswalk dataclass
    shape: a plain `.espn_to_sleeper` dict, looked up with `.get`."""
    def __init__(self, m): self.espn_to_sleeper = m


# Shaped after a real mTransactions2 response (verified live 2026-09-16):
# WAIVER transactions carry ADD/DROP items and a top-level bidAmount;
# ROSTER transactions are lineup moves (item type LINEUP) and must never
# be mistaken for an acquisition.
_RAW = {
    "transactions": [
        {"id": "t-won", "type": "WAIVER", "status": "EXECUTED", "teamId": 7,
         "bidAmount": 15, "scoringPeriodId": 2, "proposedDate": 1000,
         "items": [{"type": "ADD", "playerId": 9001},
                   {"type": "DROP", "playerId": 9002}]},
        {"id": "t-lost", "type": "WAIVER", "status": "CANCELED", "teamId": 7,
         "bidAmount": 5, "scoringPeriodId": 2, "proposedDate": 2000,
         "items": [{"type": "ADD", "playerId": 9003}]},
        {"id": "t-pending", "type": "WAIVER", "status": "PENDING", "teamId": 7,
         "bidAmount": 3, "scoringPeriodId": 3, "proposedDate": 3000,
         "items": [{"type": "ADD", "playerId": 9004}]},
        {"id": "t-lineup", "type": "ROSTER", "status": "EXECUTED", "teamId": 7,
         "scoringPeriodId": 2, "proposedDate": 4000,
         "items": [{"type": "LINEUP", "playerId": 9001}]},
        {"id": "t-crosswalk-miss", "type": "WAIVER", "status": "EXECUTED", "teamId": 7,
         "bidAmount": 8, "scoringPeriodId": 2, "proposedDate": 5000,
         "items": [{"type": "ADD", "playerId": 9999}]},
        {"id": "t-future", "type": "WAIVER", "status": "EXECUTED", "teamId": 7,
         "bidAmount": 20, "scoringPeriodId": 5, "proposedDate": 6000,
         "items": [{"type": "ADD", "playerId": 9001}]},
    ],
}
_CROSSWALK = _CW({"9001": "s1", "9002": "s2", "9003": "s3", "9004": "s4"})


def _client(handler):
    return EspnClient("s2", "{SWID}", base_delay=0, transport=httpx.MockTransport(handler))


def test_fetch_waivers_returns_only_executed_waiver_adds_through_the_cutoff_week():
    def handler(request):
        assert "scoringPeriodId=1" in str(request.url)
        return httpx.Response(200, json=_RAW)

    out = transactions.fetch_waivers(
        _client(handler), "L1", 2026, _CROSSWALK, through_week=3)
    # "t-crosswalk-miss" is also EXECUTED and within cutoff -- it survives
    # too (see the crosswalk-miss test below), just with an unidentified
    # player id.
    assert {c.transaction_id for c in out} == {"t-won", "t-crosswalk-miss"}
    c = next(c for c in out if c.transaction_id == "t-won")
    assert c.roster_id == 7
    assert c.player_id == "s1"
    assert c.bid_amount == 15.0
    assert c.week == 2
    assert c.won is True


def test_fetch_waivers_excludes_transactions_past_the_cutoff_week():
    def handler(request):
        return httpx.Response(200, json=_RAW)

    out = transactions.fetch_waivers(
        _client(handler), "L1", 2026, _CROSSWALK, through_week=3)
    assert "t-future" not in [c.transaction_id for c in out]


def test_fetch_waivers_still_counts_budget_for_a_crosswalk_miss():
    # A claim whose added player fails to crosswalk still spent real FAAB
    # budget -- it must not be silently dropped (that would understate
    # how much budget the team actually has left), just left unidentified.
    def handler(request):
        return httpx.Response(200, json=_RAW)

    out = transactions.fetch_waivers(
        _client(handler), "L1", 2026, _CROSSWALK, through_week=3)
    claim = next(c for c in out if c.transaction_id == "t-crosswalk-miss")
    assert claim.bid_amount == 8.0
    assert claim.player_id == "espn-uncrosswalked:9999"


def test_fetch_all_claims_includes_losses_within_the_cutoff():
    # Mirrors Sleeper's own fetch_all_claims: a non-EXECUTED status within
    # the cutoff week reads as won=False, including a same-week PENDING
    # claim -- the same "current week might still resolve" limitation
    # Sleeper's own docstring already discloses, not a new ESPN gap.
    def handler(request):
        return httpx.Response(200, json=_RAW)

    out = transactions.fetch_all_claims(
        _client(handler), "L1", 2026, _CROSSWALK, through_week=3)
    by_id = {c.transaction_id: c for c in out}
    assert by_id["t-won"].won is True
    assert by_id["t-lost"].won is False
    assert by_id["t-pending"].won is False
    assert "t-future" not in by_id
