import pytest

from ffdo.domain.models import LeagueProfile, ValuedPlayer
from ffdo.engine import roster
from ffdo.ingest import draft, players, snapshot


def test_real_2025_auction_produces_sane_per_team_lineups():
    """Replay the real, completed 2025 auction (12 teams, ~800 picks across
    all five historical drafts, this one alone is ~150 picks). Every
    roster's lineup fill must respect the league's actual slot counts, and
    starters/bench must partition each roster's VOR with nothing lost or
    double-counted.
    """
    hist = snapshot.load("league_history")
    profiles = players.parse(snapshot.load("players_nfl"))
    meta = hist["drafts"]["2025"]["meta"]
    picks = hist["drafts"]["2025"]["picks"]
    state = draft.parse(meta, picks)

    league = LeagueProfile(
        league_id="x", season=2025, num_teams=12,
        roster_positions=("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                          "BN", "BN", "BN", "BN", "BN"),
        scoring_settings={}, budget=200)

    # Synthetic VOR ramp by pick order -- same convention as the auction
    # replay test in tests/engine/test_auction.py. Real per-player VOR needs
    # the full scoring/projection pipeline, which this fixture test isn't
    # exercising; a descending-by-pick-order proxy is enough to sanity-check
    # that the lineup fill behaves across a real draft's real positions.
    valued: dict[str, ValuedPlayer] = {}
    for i, p in enumerate(state.picks):
        prof = profiles.get(p.player_id)
        if prof is None or prof.position not in ("QB", "RB", "WR", "TE"):
            continue
        vor = 150.0 - i * 0.5
        valued[p.player_id] = ValuedPlayer(
            profile=prof, projected_points=vor, adjusted_points=vor,
            vor=vor, tier=1, adjustments={})

    picks_by_roster: dict[int, list[str]] = {}
    for p in state.picks:
        if p.roster_id is not None:
            picks_by_roster.setdefault(p.roster_id, []).append(p.player_id)

    assert len(picks_by_roster) == 12

    for roster_id, player_ids in picks_by_roster.items():
        team_players = {pid: valued[pid] for pid in player_ids if pid in valued}
        lineup = roster.team_lineup(team_players, league)

        assert sum(lineup.by_position.values()) == pytest.approx(lineup.starting_vor)
        assert len(lineup.starters) <= len(league.starting_slots)

        total_vor = sum(vp.vor for vp in team_players.values())
        assert lineup.starting_vor + lineup.bench_vor == pytest.approx(total_vor)
