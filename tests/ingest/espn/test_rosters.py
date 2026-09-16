import httpx

from ffdo.ingest.espn import rosters
from ffdo.ingest.espn.client import EspnClient


class _CW:
    """Minimal crosswalk stand-in matching the real Crosswalk dataclass
    shape: a plain `.espn_to_sleeper` dict, looked up with `.get`."""
    def __init__(self, m): self.espn_to_sleeper = m


_LEAGUE_RAW = {
    "settings": {
        "scheduleSettings": {"matchupPeriodCount": 14},
    },
    "status": {"currentMatchupPeriod": 10},
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
    assert e.reserve_ids == ()                          # the one IR entry (9999) is a crosswalk miss
    assert e.taxi_ids == ()                             # ESPN has no taxi-squad concept
    assert e.wins == 7 and e.points_for == 1352.8
    assert week.week == 10 and week.complete is False


def test_fetch_tags_a_crosswalked_ir_player_as_reserve():
    """Regression: droppable_player_ids relies on reserve_ids to exclude
    IR players from waiver-drop candidates (the same bug #44 fixed for
    Sleeper) -- ESPN's IR slot (21) must feed it too."""
    raw = {**_LEAGUE_RAW, "teams": [
        {**_LEAGUE_RAW["teams"][0], "roster": {"entries": [
            {"playerId": 1001, "lineupSlotId": 0},
            {"playerId": 1002, "lineupSlotId": 21},   # IR, and IS in the crosswalk
        ]}},
    ]}

    def handler(request):
        return httpx.Response(200, json=raw)

    entries, _week, _raw = rosters.fetch(_client(handler), "L1", 2026, _CROSSWALK)
    e = entries[0]
    assert e.player_ids == ("s1", "s2")
    assert e.starter_ids == ("s1",)
    assert e.reserve_ids == ("s2",)


def test_week_past_matchup_period_count_is_complete():
    raw = {**_LEAGUE_RAW, "status": {"currentMatchupPeriod": 15}}

    def handler(request):
        return httpx.Response(200, json=raw)

    _e, week, _r = rosters.fetch(_client(handler), "L1", 2026, _CROSSWALK)
    assert week.complete is True


def test_status_is_read_from_the_payload_top_level_not_under_settings():
    """Regression: `status` is a sibling of `settings`, not nested under
    it. A payload that (wrongly) only carries a nested copy must not be
    mistaken for the real thing -- it should read as week 0, same as a
    payload with no status at all."""
    raw = {**_LEAGUE_RAW, "status": {},
           "settings": {**_LEAGUE_RAW["settings"],
                        "status": {"currentMatchupPeriod": 10}}}

    def handler(request):
        return httpx.Response(200, json=raw)

    _e, week, _r = rosters.fetch(_client(handler), "L1", 2026, _CROSSWALK)
    assert week.week == 0


# -- slot_aligned_starters ---------------------------------------------------

_MROSTER_TWO_RB = {
    "teams": [
        {"id": 1, "roster": {"entries": [
            {"playerId": 1, "lineupSlotId": 0},    # QB
            {"playerId": 2, "lineupSlotId": 2},    # RB
            {"playerId": 3, "lineupSlotId": 2},    # RB
            {"playerId": 4, "lineupSlotId": 23},   # FLEX
            {"playerId": 5, "lineupSlotId": 20},   # BN -- must not fill a starting slot
            {"playerId": 6, "lineupSlotId": 21},   # IR -- must not fill a starting slot
            {"playerId": 99, "lineupSlotId": 4},   # WR, but not in the crosswalk
        ]}},
    ],
}
_CW_TWO_RB = _CW({"1": "sQB", "2": "sRB1", "3": "sRB2", "4": "sFLEX",
                  "5": "sBN", "6": "sIR"})


def test_slot_aligned_starters_matches_positions_in_order():
    out = rosters.slot_aligned_starters(
        _MROSTER_TWO_RB, _CW_TWO_RB, roster_id=1,
        starting_slots=("QB", "RB", "RB", "WR", "FLEX"))
    assert out[0] == "sQB"
    assert set(out[1:3]) == {"sRB1", "sRB2"}           # order between them is arbitrary
    assert out[3] is None                              # WR slot: crosswalk miss -> empty
    assert out[4] == "sFLEX"


def test_slot_aligned_starters_bench_and_ir_never_fill_a_starting_slot():
    out = rosters.slot_aligned_starters(
        _MROSTER_TWO_RB, _CW_TWO_RB, roster_id=1,
        starting_slots=("QB",))
    assert out == ("sQB",)   # sBN/sIR never surface even if a slot needed filling


def test_slot_aligned_starters_unknown_roster_id_returns_all_empty():
    out = rosters.slot_aligned_starters(
        _MROSTER_TWO_RB, _CW_TWO_RB, roster_id=999,
        starting_slots=("QB", "RB"))
    assert out == (None, None)
