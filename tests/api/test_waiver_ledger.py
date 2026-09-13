# tests/api/test_waiver_ledger.py
from ffdo.api.waiver_ledger import WaiverLedger


def _kwargs(**overrides):
    base = dict(
        transaction_id="w1", season=2026, week=3, roster_id=5,
        add_player_id="p1", drop_player_id="p9", recommended_bid=15.0,
        actual_bid=15, predicted_vor_gain=8.0, won=True,
    )
    base.update(overrides)
    return base


def test_record_if_absent_persists_and_returns_the_entry(tmp_path):
    ledger = WaiverLedger(tmp_path / "test.db")
    entry = ledger.record_if_absent("league1", **_kwargs())
    assert entry.transaction_id == "w1"
    assert entry.roster_id == 5
    assert entry.add_player_id == "p1"
    assert entry.drop_player_id == "p9"
    assert entry.recommended_bid == 15.0
    assert entry.actual_bid == 15
    assert entry.predicted_vor_gain == 8.0
    assert entry.won is True
    assert entry.recorded_at


def test_record_if_absent_is_idempotent(tmp_path):
    ledger = WaiverLedger(tmp_path / "test.db")
    first = ledger.record_if_absent("league1", **_kwargs())
    second = ledger.record_if_absent("league1", **_kwargs(actual_bid=999, won=False))
    assert second.recorded_at == first.recorded_at
    assert second.actual_bid == 15  # the SECOND call's values are ignored -- write-once
    assert second.won is True


def test_get_returns_none_when_absent(tmp_path):
    ledger = WaiverLedger(tmp_path / "test.db")
    assert ledger.get("league1", "nonexistent") is None


def test_get_returns_the_recorded_entry(tmp_path):
    ledger = WaiverLedger(tmp_path / "test.db")
    ledger.record_if_absent("league1", **_kwargs())
    entry = ledger.get("league1", "w1")
    assert entry is not None
    assert entry.transaction_id == "w1"


def test_list_for_league_scopes_to_the_given_league(tmp_path):
    ledger = WaiverLedger(tmp_path / "test.db")
    ledger.record_if_absent("league1", **_kwargs(transaction_id="w1"))
    ledger.record_if_absent("league2", **_kwargs(transaction_id="w2"))
    result = ledger.list_for_league("league1")
    assert [e.transaction_id for e in result] == ["w1"]


def test_list_for_league_supports_a_lost_claim_with_null_recommendation_fields(tmp_path):
    ledger = WaiverLedger(tmp_path / "test.db")
    ledger.record_if_absent(
        "league1", transaction_id="w1", season=2026, week=1, roster_id=5,
        add_player_id="p2", drop_player_id=None, recommended_bid=None,
        actual_bid=3, predicted_vor_gain=None, won=False)
    entry = ledger.get("league1", "w1")
    assert entry.recommended_bid is None
    assert entry.drop_player_id is None
    assert entry.won is False


def test_corrupt_db_is_tolerated_as_empty(tmp_path):
    db_path = tmp_path / "test.db"
    db_path.write_bytes(b"not a real sqlite file")
    ledger = WaiverLedger(db_path)
    assert ledger.get("league1", "w1") is None
    assert ledger.list_for_league("league1") == []
