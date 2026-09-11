from ffdo.api.lineup_ledger import LineupLedger


def test_record_if_absent_creates_a_row(tmp_path):
    ledger = LineupLedger(tmp_path / "ffdo.db")
    record = ledger.record_if_absent(
        "sleeper:L1:2026", 2026, 10,
        recommended={0: "p1", 1: "p2"}, actual=("p3", "p2"))

    assert record.recommended == {0: "p1", 1: "p2"}
    assert record.actual == ("p3", "p2")
    assert record.followed is None
    assert record.resolved_at is None

    fetched = ledger.get("sleeper:L1:2026", 2026, 10)
    assert fetched == record


def test_record_if_absent_is_a_no_op_on_a_second_call_same_week(tmp_path):
    ledger = LineupLedger(tmp_path / "ffdo.db")
    first = ledger.record_if_absent(
        "sleeper:L1:2026", 2026, 10,
        recommended={0: "p1"}, actual=("p_old",))
    second = ledger.record_if_absent(
        "sleeper:L1:2026", 2026, 10,
        recommended={0: "DIFFERENT"}, actual=("p_new",))

    assert second.recommended == {0: "p1"}   # unchanged from the first call
    assert second.actual == ("p_old",)
    assert first.recorded_at == second.recorded_at


def test_get_returns_none_for_a_week_never_recorded(tmp_path):
    ledger = LineupLedger(tmp_path / "ffdo.db")
    assert ledger.get("sleeper:L1:2026", 2026, 10) is None


def test_resolve_full_when_final_matches_recommendation_everywhere(tmp_path):
    ledger = LineupLedger(tmp_path / "ffdo.db")
    ledger.record_if_absent("sleeper:L1:2026", 2026, 10,
                            recommended={0: "p1", 1: "p2"}, actual=("p_old", "p2"))

    followed = ledger.resolve("sleeper:L1:2026", 2026, 10, final_actual=("p1", "p2"))
    assert followed == "full"
    assert ledger.get("sleeper:L1:2026", 2026, 10).followed == "full"
    assert ledger.get("sleeper:L1:2026", 2026, 10).resolved_at is not None


def test_resolve_partial_when_final_matches_some_but_not_all(tmp_path):
    ledger = LineupLedger(tmp_path / "ffdo.db")
    ledger.record_if_absent("sleeper:L1:2026", 2026, 10,
                            recommended={0: "p1", 1: "p2"}, actual=("p_old", "p_old2"))

    followed = ledger.resolve("sleeper:L1:2026", 2026, 10, final_actual=("p1", "p_old2"))
    assert followed == "partial"


def test_resolve_none_when_final_matches_nothing_beyond_the_original(tmp_path):
    ledger = LineupLedger(tmp_path / "ffdo.db")
    ledger.record_if_absent("sleeper:L1:2026", 2026, 10,
                            recommended={0: "p1", 1: "p2"}, actual=("p_old", "p_old2"))

    followed = ledger.resolve("sleeper:L1:2026", 2026, 10,
                              final_actual=("p_old", "p_old2"))
    assert followed == "none"


def test_two_different_weeks_are_independent_rows(tmp_path):
    ledger = LineupLedger(tmp_path / "ffdo.db")
    ledger.record_if_absent("sleeper:L1:2026", 2026, 9, recommended={0: "wk9"}, actual=("a",))
    ledger.record_if_absent("sleeper:L1:2026", 2026, 10, recommended={0: "wk10"}, actual=("b",))

    assert ledger.get("sleeper:L1:2026", 2026, 9).recommended == {0: "wk9"}
    assert ledger.get("sleeper:L1:2026", 2026, 10).recommended == {0: "wk10"}
