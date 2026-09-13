from ffdo.engine import scorecard


def test_lineup_metric_counts_followed_status():
    outcomes = [
        scorecard.LineupOutcome(season=2026, week=1, recommended={}, actual=(), followed="full"),
        scorecard.LineupOutcome(season=2026, week=2, recommended={}, actual=(), followed="partial"),
        scorecard.LineupOutcome(season=2026, week=3, recommended={}, actual=(), followed="none"),
    ]
    result = scorecard.lineup_metric(outcomes, {}, {})
    assert result["weeks_resolved"] == 3
    assert result["weeks_full"] == 1
    assert result["weeks_partial"] == 1
    assert result["weeks_none"] == 1


def test_lineup_metric_sums_points_left_on_the_bench_when_not_followed():
    outcomes = [
        scorecard.LineupOutcome(
            season=2026, week=1, recommended={0: "recommended_p"},
            actual=("actual_p",), followed="none"),
    ]
    weekly_points = {
        (2026, 1): {
            "recommended_p": {"rec_yd": 100.0},  # 100 yards -> 10.0 pts at 0.1/yd
            "actual_p": {"rec_yd": 20.0},         # 20 yards -> 2.0 pts
        },
    }
    scoring_settings = {"rec_yd": 0.1}
    result = scorecard.lineup_metric(outcomes, weekly_points, scoring_settings)
    assert result["points_left_on_bench"] == 8.0


def test_lineup_metric_fully_followed_weeks_contribute_no_points_left():
    outcomes = [
        scorecard.LineupOutcome(
            season=2026, week=1, recommended={0: "p1"}, actual=("p1",), followed="full"),
    ]
    result = scorecard.lineup_metric(outcomes, {}, {})
    assert result["points_left_on_bench"] == 0.0


def test_lineup_metric_missing_weekly_stats_defaults_to_zero_points():
    outcomes = [
        scorecard.LineupOutcome(
            season=2026, week=1, recommended={0: "p1"}, actual=("p2",), followed="none"),
    ]
    result = scorecard.lineup_metric(outcomes, {}, {})  # no weekly_points entry for (2026, 1)
    assert result["points_left_on_bench"] == 0.0


def test_lineup_metric_empty_outcomes():
    result = scorecard.lineup_metric([], {}, {})
    assert result == {
        "weeks_resolved": 0, "weeks_full": 0, "weeks_partial": 0,
        "weeks_none": 0, "points_left_on_bench": 0.0,
    }


def test_trade_metric_scopes_to_your_own_trades():
    outcomes = [
        scorecard.TradeOutcome(
            transaction_id="t1", roster_a_id=1, roster_b_id=2,
            side_a_value_at_trade=10.0, side_b_value_at_trade=10.0,
            side_a_current_value=15.0, side_b_current_value=10.0),
        scorecard.TradeOutcome(
            transaction_id="t2", roster_a_id=3, roster_b_id=4,  # not your trade
            side_a_value_at_trade=10.0, side_b_value_at_trade=10.0,
            side_a_current_value=5.0, side_b_current_value=10.0),
    ]
    result = scorecard.trade_metric(outcomes, your_roster_id=1)
    assert result["total_trades_in_league"] == 2
    assert result["your_trades"] == 1


def test_trade_metric_classifies_gained_lost_unchanged():
    outcomes = [
        scorecard.TradeOutcome(
            transaction_id="gained", roster_a_id=1, roster_b_id=2,
            side_a_value_at_trade=10.0, side_b_value_at_trade=10.0,
            side_a_current_value=20.0, side_b_current_value=10.0),
        scorecard.TradeOutcome(
            transaction_id="lost", roster_a_id=1, roster_b_id=2,
            side_a_value_at_trade=10.0, side_b_value_at_trade=10.0,
            side_a_current_value=2.0, side_b_current_value=10.0),
        scorecard.TradeOutcome(
            transaction_id="unchanged", roster_a_id=1, roster_b_id=2,
            side_a_value_at_trade=10.0, side_b_value_at_trade=10.0,
            side_a_current_value=10.2, side_b_current_value=10.0),
    ]
    result = scorecard.trade_metric(outcomes, your_roster_id=1)
    assert result["gained_value"] == 1
    assert result["lost_value"] == 1
    assert result["unchanged"] == 1


def test_trade_metric_reads_the_correct_side_when_you_are_roster_b():
    outcomes = [
        scorecard.TradeOutcome(
            transaction_id="t1", roster_a_id=1, roster_b_id=2,
            side_a_value_at_trade=10.0, side_b_value_at_trade=10.0,
            side_a_current_value=10.0, side_b_current_value=25.0),
    ]
    result = scorecard.trade_metric(outcomes, your_roster_id=2)
    assert result["gained_value"] == 1


def test_trade_metric_empty_outcomes():
    result = scorecard.trade_metric([], your_roster_id=1)
    assert result == {
        "total_trades_in_league": 0, "your_trades": 0,
        "gained_value": 0, "lost_value": 0, "unchanged": 0,
    }
