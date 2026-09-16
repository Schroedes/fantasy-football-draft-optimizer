from ffdo.ingest.espn import actuals


class _CW:
    """Minimal crosswalk stand-in matching the real Crosswalk dataclass
    shape: a plain `.espn_to_sleeper` dict, looked up with `.get`."""
    def __init__(self, m): self.espn_to_sleeper = m


_MROSTER = {
    "teams": [
        {"roster": {"entries": [
            {"playerId": 1001, "playerPoolEntry": {"player": {"stats": [
                {"statSourceId": 0, "seasonId": 2026, "scoringPeriodId": 1, "appliedTotal": 18.4},
                {"statSourceId": 0, "seasonId": 2026, "scoringPeriodId": 2, "appliedTotal": 12.1},
                {"statSourceId": 1, "seasonId": 2026, "scoringPeriodId": 3, "appliedTotal": 99.0},  # projection, ignore
                {"statSourceId": 0, "seasonId": 2026, "scoringPeriodId": 11, "appliedTotal": 20.0}, # past cutoff
            ]}}},
        ]}},
    ],
}


def test_sums_actual_period_totals_up_to_cutoff():
    out = actuals.points_so_far(_MROSTER, _CW({"1001": "s1"}), season=2026, through_week=9)
    assert out["s1"] == 30.5     # 18.4 + 12.1; period 11 and the projection excluded


def test_crosswalk_miss_is_skipped():
    out = actuals.points_so_far(_MROSTER, _CW({}), season=2026, through_week=9)
    assert out == {}


def test_ignores_the_season_aggregate_line_and_other_seasons():
    """Regression, from a real live payload: ESPN's `stats` array carries a
    season-aggregate line at `scoringPeriodId == 0` (a duplicate of the
    per-week totals, not an additional week) and prior seasons' totals
    under the same key, distinguished only by `seasonId`. Naively summing
    everything with `statSourceId == 0` double-counts the aggregate and
    bleeds in last year's points."""
    mroster = {
        "teams": [
            {"roster": {"entries": [
                {"playerId": 1001, "playerPoolEntry": {"player": {"stats": [
                    {"statSourceId": 0, "seasonId": 2025, "scoringPeriodId": 0, "appliedTotal": 366.9},
                    {"statSourceId": 0, "seasonId": 2026, "scoringPeriodId": 0, "appliedTotal": 33.6},
                    {"statSourceId": 0, "seasonId": 2026, "scoringPeriodId": 1, "appliedTotal": 33.6},
                ]}}},
            ]}},
        ],
    }
    out = actuals.points_so_far(mroster, _CW({"1001": "s1"}), season=2026, through_week=1)
    assert out["s1"] == 33.6
