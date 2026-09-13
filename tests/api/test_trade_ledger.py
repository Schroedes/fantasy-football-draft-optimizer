from dataclasses import replace

from ffdo.api.trade_ledger import TradeLedger
from ffdo.domain.models import TradeTransaction


def _trade(tid="t1", roster_a=2, roster_b=3):
    return TradeTransaction(
        transaction_id=tid, season=2026, week=3,
        roster_a_id=roster_a, roster_b_id=roster_b,
        roster_a_gets=["p1"], roster_b_gets=["p2"],
        picks_to_a=[], picks_to_b=[], traded_at_ms=1000)


def test_record_if_absent_writes_once(tmp_path):
    ledger = TradeLedger(tmp_path / "ffdo.db")
    first = ledger.record_if_absent(
        "sleeper:L1:2026", _trade(), side_a_value=40.0, side_b_value=30.0,
        banked_a={"p1": 12.0}, banked_b={"p2": 8.0})
    again = ledger.record_if_absent(
        "sleeper:L1:2026", _trade(), side_a_value=999.0, side_b_value=999.0,
        banked_a={}, banked_b={})
    assert again.side_a_value_at_trade == first.side_a_value_at_trade == 40.0


def test_list_for_league_returns_only_that_leagues_trades(tmp_path):
    ledger = TradeLedger(tmp_path / "ffdo.db")
    ledger.record_if_absent("sleeper:L1:2026", _trade("t1"), side_a_value=1.0,
                            side_b_value=1.0, banked_a={}, banked_b={})
    ledger.record_if_absent("sleeper:L2:2026", _trade("t2"), side_a_value=2.0,
                            side_b_value=2.0, banked_a={}, banked_b={})
    result = ledger.list_for_league("sleeper:L1:2026")
    assert [r.transaction_id for r in result] == ["t1"]


def test_list_for_league_is_chronological(tmp_path):
    ledger = TradeLedger(tmp_path / "ffdo.db")
    # TradeTransaction is frozen, so dataclasses.replace makes a new
    # instance rather than mutating -- recorded out of order to confirm
    # list_for_league sorts by traded_at_ms, not by insertion order.
    later = replace(_trade("t-later", roster_a=2, roster_b=3), traded_at_ms=2000)
    earlier = replace(_trade("t-earlier", roster_a=4, roster_b=5), traded_at_ms=1000)
    ledger.record_if_absent("sleeper:L1:2026", later, side_a_value=1.0,
                            side_b_value=1.0, banked_a={}, banked_b={})
    ledger.record_if_absent("sleeper:L1:2026", earlier, side_a_value=1.0,
                            side_b_value=1.0, banked_a={}, banked_b={})
    result = ledger.list_for_league("sleeper:L1:2026")
    assert [r.transaction_id for r in result] == ["t-earlier", "t-later"]


def test_corrupt_db_treated_as_empty(tmp_path):
    path = tmp_path / "ffdo.db"
    path.write_bytes(b"not a real sqlite file")
    ledger = TradeLedger(path)
    assert ledger.list_for_league("sleeper:L1:2026") == []
