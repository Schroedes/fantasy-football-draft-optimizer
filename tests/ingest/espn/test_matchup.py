import httpx

from ffdo.ingest.espn import matchup
from ffdo.ingest.espn.client import EspnClient


class _CW:
    """Minimal crosswalk stand-in matching the real Crosswalk dataclass
    shape: a plain `.espn_to_sleeper` dict, looked up with `.get`."""
    def __init__(self, m): self.espn_to_sleeper = m


def _score_entry(slot_id, stats):
    return {"lineupSlotId": slot_id,
            "playerPoolEntry": {"player": {"stats": stats}}}


# Shaped after a real mRoster+mMatchupScore combined response (verified
# live 2026-09-16): `teams[].roster.entries` and
# `schedule[].{home,away}.rosterForCurrentScoringPeriod.entries` share the
# same per-team entry order (same lineupSlotId sequence), zipped
# positionally since neither array alone carries both playerId and points.
_RAW = {
    "status": {"currentMatchupPeriod": 2},
    "teams": [
        {"id": 1, "roster": {"entries": [
            {"playerId": 101, "lineupSlotId": 0},   # QB starter
            {"playerId": 102, "lineupSlotId": 20},  # BN -- excluded
        ]}},
        {"id": 2, "roster": {"entries": [
            {"playerId": 201, "lineupSlotId": 4},   # WR starter
        ]}},
        {"id": 3, "roster": {"entries": [
            {"playerId": 301, "lineupSlotId": 2},
        ]}},
    ],
    "schedule": [
        {"matchupPeriodId": 1,   # a stale prior-week entry -- must be ignored
         "home": {"teamId": 1, "rosterForCurrentScoringPeriod": {"entries": []}},
         "away": {"teamId": 2, "rosterForCurrentScoringPeriod": {"entries": []}}},
        {"matchupPeriodId": 2,
         "home": {"teamId": 1, "rosterForCurrentScoringPeriod": {"entries": [
             _score_entry(0, [{"statSourceId": 1, "appliedTotal": 18.4}]),
             _score_entry(20, [{"statSourceId": 1, "appliedTotal": 99.0}]),  # bench, excluded
         ]}},
         "away": {"teamId": 2, "rosterForCurrentScoringPeriod": {"entries": [
             # both a live/actual (0) and a projection (1) line -- actual wins
             _score_entry(4, [{"statSourceId": 1, "appliedTotal": 30.0},
                              {"statSourceId": 0, "appliedTotal": 12.7}]),
         ]}}},
        {"matchupPeriodId": 2,
         "home": {"teamId": 3, "rosterForCurrentScoringPeriod": {"entries": [
             _score_entry(2, [{"statSourceId": 1, "appliedTotal": 9.9}]),
         ]}},
         "away": {"teamId": 4, "rosterForCurrentScoringPeriod": {"entries": []}}},
    ],
}
_CROSSWALK = _CW({"101": "s101", "201": "s201", "301": "s301"})


def _client(handler):
    return EspnClient("s2", "{SWID}", base_delay=0, transport=httpx.MockTransport(handler))


def test_fetch_returns_the_current_periods_pairing_and_starter_points():
    def handler(request):
        return httpx.Response(200, json=_RAW)

    out = matchup.fetch(_client(handler), "L1", 2026, _CROSSWALK, roster_id=1)
    assert out is not None
    assert out.opponent_roster_id == 2
    assert out.your_starter_points == {"s101": 18.4}       # s102 bench-excluded (crosswalk miss too)
    assert out.opponent_starter_points == {"s201": 12.7}   # actual (0) preferred over projection (1)


def test_fetch_ignores_a_stale_prior_week_entry():
    """matchupPeriodId 1's home/away also include teamId 1/2 -- if the
    filter didn't check the period, it could grab the wrong week's
    (empty) entries instead of week 2's real ones."""
    def handler(request):
        return httpx.Response(200, json=_RAW)

    out = matchup.fetch(_client(handler), "L1", 2026, _CROSSWALK, roster_id=1)
    assert out.your_starter_points != {}


def test_fetch_returns_none_for_a_bye_week():
    def handler(request):
        return httpx.Response(200, json=_RAW)

    out = matchup.fetch(_client(handler), "L1", 2026, _CROSSWALK, roster_id=999)
    assert out is None


def test_fetch_works_from_the_away_side_too():
    def handler(request):
        return httpx.Response(200, json=_RAW)

    out = matchup.fetch(_client(handler), "L1", 2026, _CROSSWALK, roster_id=2)
    assert out.opponent_roster_id == 1
    assert out.your_starter_points == {"s201": 12.7}
    assert out.opponent_starter_points == {"s101": 18.4}
