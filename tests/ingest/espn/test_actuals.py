from ffdo.ingest.espn import actuals


class _CW:
    """Minimal crosswalk stand-in matching the real Crosswalk dataclass
    shape: a plain `.espn_to_sleeper` dict, looked up with `.get`."""
    def __init__(self, m): self.espn_to_sleeper = m


_MROSTER = {
    "teams": [
        {"roster": {"entries": [
            {"playerId": 1001, "playerPoolEntry": {"player": {"stats": [
                {"statSourceId": 0, "scoringPeriodId": 1, "appliedTotal": 18.4},
                {"statSourceId": 0, "scoringPeriodId": 2, "appliedTotal": 12.1},
                {"statSourceId": 1, "scoringPeriodId": 3, "appliedTotal": 99.0},  # projection, ignore
                {"statSourceId": 0, "scoringPeriodId": 11, "appliedTotal": 20.0}, # past cutoff
            ]}}},
        ]}},
    ],
}


def test_sums_actual_period_totals_up_to_cutoff():
    out = actuals.points_so_far(_MROSTER, _CW({"1001": "s1"}), through_week=9)
    assert out["s1"] == 30.5     # 18.4 + 12.1; period 11 and the projection excluded


def test_crosswalk_miss_is_skipped():
    out = actuals.points_so_far(_MROSTER, _CW({}), through_week=9)
    assert out == {}
