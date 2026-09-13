# tests/api/test_draft_pick_ledger.py
from ffdo.api.draft_pick_ledger import DraftPickLedger


def _kwargs(**overrides):
    base = dict(
        draft_id="d1", pick_no=5, round=1, roster_id=3, player_id="p1",
        position="RB", amount=None, grade="GOOD", vor_at_pick=42.5,
        predicted_survival=0.35,
    )
    base.update(overrides)
    return base


def test_record_if_absent_persists_and_returns_the_entry(tmp_path):
    ledger = DraftPickLedger(tmp_path / "test.db")
    entry = ledger.record_if_absent("league1", **_kwargs())
    assert entry.pick_no == 5
    assert entry.round == 1
    assert entry.roster_id == 3
    assert entry.player_id == "p1"
    assert entry.position == "RB"
    assert entry.grade == "GOOD"
    assert entry.vor_at_pick == 42.5
    assert entry.predicted_survival == 0.35
    assert entry.recorded_at


def test_record_if_absent_is_idempotent(tmp_path):
    ledger = DraftPickLedger(tmp_path / "test.db")
    first = ledger.record_if_absent("league1", **_kwargs())
    second = ledger.record_if_absent("league1", **_kwargs(grade="POOR"))
    assert second.recorded_at == first.recorded_at
    assert second.grade == "GOOD"  # second call's values ignored -- write-once


def test_different_picks_in_the_same_draft_are_both_recorded(tmp_path):
    ledger = DraftPickLedger(tmp_path / "test.db")
    ledger.record_if_absent("league1", **_kwargs(pick_no=1))
    ledger.record_if_absent("league1", **_kwargs(pick_no=2))
    result = ledger.list_for_league("league1")
    assert [e.pick_no for e in result] == [1, 2]


def test_get_returns_none_when_absent(tmp_path):
    ledger = DraftPickLedger(tmp_path / "test.db")
    assert ledger.get("league1", "d1", 1) is None


def test_list_for_league_scopes_to_the_given_league(tmp_path):
    ledger = DraftPickLedger(tmp_path / "test.db")
    ledger.record_if_absent("league1", **_kwargs())
    ledger.record_if_absent("league2", **_kwargs())
    result = ledger.list_for_league("league1")
    assert len(result) == 1
    assert result[0].league_key == "league1"


def test_ungraded_pick_supports_null_fields(tmp_path):
    """A pick recorded via the early-return (draft already complete on
    first poll) path has no grade/vor/survival available yet."""
    ledger = DraftPickLedger(tmp_path / "test.db")
    entry = ledger.record_if_absent(
        "league1", draft_id="d1", pick_no=1, round=1, roster_id=None,
        player_id="p1", position=None, amount=None, grade=None,
        vor_at_pick=None, predicted_survival=None)
    assert entry.grade is None
    assert entry.vor_at_pick is None
    assert entry.predicted_survival is None
    assert entry.roster_id is None


def test_corrupt_db_is_tolerated_as_empty(tmp_path):
    db_path = tmp_path / "test.db"
    db_path.write_bytes(b"not a real sqlite file")
    ledger = DraftPickLedger(db_path)
    assert ledger.get("league1", "d1", 1) is None
    assert ledger.list_for_league("league1") == []
