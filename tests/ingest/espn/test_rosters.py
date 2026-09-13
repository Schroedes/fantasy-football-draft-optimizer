import httpx

from ffdo.ingest.espn import rosters
from ffdo.ingest.espn.client import EspnClient


class _CW:
    """Minimal crosswalk stand-in matching the real Crosswalk dataclass
    shape: a plain `.espn_to_sleeper` dict, looked up with `.get`."""
    def __init__(self, m): self.espn_to_sleeper = m


_LEAGUE_RAW = {
    "settings": {
        "status": {"currentMatchupPeriod": 10},
        "scheduleSettings": {"matchupPeriodCount": 14},
    },
    "teams": [
        {"id": 1, "name": "Alpha",
         "record": {"overall": {"wins": 7, "losses": 2, "ties": 0,
                                "pointsFor": 1352.8, "pointsAgainst": 1190.0}},
         "roster": {"entries": [
             {"playerId": 1001, "lineupSlotId": 0},
             {"playerId": 1002, "lineupSlotId": 2},
             {"playerId": 1003, "lineupSlotId": 20},   # BN
             {"playerId": 9999, "lineupSlotId": 21},   # IR, and not in crosswalk
         ]}},
    ],
}
_CROSSWALK = _CW({"1001": "s1", "1002": "s2", "1003": "s3"})


def _client(handler):
    return EspnClient("s2", "{SWID}", base_delay=0, transport=httpx.MockTransport(handler))


def test_fetch_parses_teams_starters_and_week():
    def handler(request):
        return httpx.Response(200, json=_LEAGUE_RAW)

    entries, week, _raw = rosters.fetch(_client(handler), "L1", 2026, _CROSSWALK)
    assert len(entries) == 1
    e = entries[0]
    assert e.team_name == "Alpha"
    assert e.player_ids == ("s1", "s2", "s3")          # 9999 dropped (crosswalk miss)
    assert e.starter_ids == ("s1", "s2")               # slots 20/21 excluded
    assert e.wins == 7 and e.points_for == 1352.8
    assert week.week == 10 and week.complete is False


def test_week_past_matchup_period_count_is_complete():
    raw = {**_LEAGUE_RAW}
    raw["settings"] = {**raw["settings"], "status": {"currentMatchupPeriod": 15}}

    def handler(request):
        return httpx.Response(200, json=raw)

    _e, week, _r = rosters.fetch(_client(handler), "L1", 2026, _CROSSWALK)
    assert week.complete is True
