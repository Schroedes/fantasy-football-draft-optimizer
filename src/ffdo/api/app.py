"""FastAPI app. Serves board state and the static board."""

from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Defaults pin this user's real 2026 auction league/draft, so the app works
# out of the box with zero config. `FFDO_LEAGUE_ID` / `FFDO_DRAFT_ID` let it
# point at a different league (e.g. a snake league) without a settings UI --
# read fresh on every request rather than frozen as module constants at
# import time, so an env var set after the process starts (or changed by a
# test via monkeypatch) actually takes effect.
_DEFAULT_LEAGUE_ID = "1315881559957458944"
_DEFAULT_DRAFT_ID = "1315881559965835264"


def _league_id() -> str:
    return os.environ.get("FFDO_LEAGUE_ID", _DEFAULT_LEAGUE_ID)


def _draft_id() -> str:
    return os.environ.get("FFDO_DRAFT_ID", _DEFAULT_DRAFT_ID)


def _roster_id() -> int | None:
    raw = os.environ.get("FFDO_ROSTER_ID")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _active_only(points: dict[str, float], profiles: dict) -> dict[str, float]:
    """Drop retired/inactive players from the valuation pool.

    `PlayerProfile.active` is parsed but was never used as a filter, so a
    retired player with a stale projection (e.g. Cam Newton) could still
    slip onto the board with a deeply negative VOR instead of not
    appearing at all.
    """
    return {pid: pts for pid, pts in points.items() if profiles[pid].active}


class _TTLCache:
    """Caches the result of `loader` in-process for `ttl_seconds`.

    The board endpoint is polled every 3s by the browser; the players feed
    alone is ~14MB and rarely changes, so re-fetching it on every poll is
    not viable. Projections change rarely during a draft window either.
    Draft state is intentionally NOT cached here -- it must reflect live
    picks on every poll.
    """

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._value: Any = None
        self._fetched_at: float = float("-inf")
        # Injectable so tests can fake elapsed time without real sleeps.
        self._now: Callable[[], float] = time.monotonic

    def get(self, loader: Callable[[], Any]) -> Any:
        now = self._now()
        if self._value is None or (now - self._fetched_at) > self._ttl:
            self._value = loader()
            self._fetched_at = now
        return self._value


def create_app() -> FastAPI:
    app = FastAPI(title="ffdo")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    from ffdo.api import board as board_mod
    from ffdo.engine import auction, scoring, vor
    from ffdo.ingest import client as client_mod
    from ffdo.ingest import draft as draft_mod
    from ffdo.ingest import league as league_mod
    from ffdo.ingest import players as players_mod
    from ffdo.ingest import projections as proj_mod
    from ffdo.ingest import teams as teams_mod

    players_cache = _TTLCache(ttl_seconds=24 * 3600)
    projections_cache = _TTLCache(ttl_seconds=3600)
    teams_cache = _TTLCache(ttl_seconds=24 * 3600)

    @app.get("/api/board")
    def get_board() -> dict:
        league_id = _league_id()
        draft_id = _draft_id()
        sleeper = client_mod.SleeperClient()
        try:
            lg = league_mod.parse(
                sleeper.get_json(f"{client_mod.V1}/league/{league_id}"))
            profiles = players_cache.get(
                lambda: players_mod.parse(
                    sleeper.get_json(f"{client_mod.V1}/players/nfl")))
            proj, adp_data = projections_cache.get(
                lambda: proj_mod.parse(
                    sleeper.get_json(
                        f"{client_mod.PROJECTIONS}/{lg.season}"
                        "?season_type=regular&position[]=QB&position[]=RB"
                        "&position[]=WR&position[]=TE"),
                    lg.season))
            state = draft_mod.parse(
                sleeper.get_json(f"{client_mod.V1}/draft/{draft_id}"),
                sleeper.get_json(f"{client_mod.V1}/draft/{draft_id}/picks"))
            teams = teams_cache.get(
                lambda: teams_mod.parse(
                    sleeper.get_json(f"{client_mod.V1}/league/{league_id}/rosters"),
                    sleeper.get_json(f"{client_mod.V1}/league/{league_id}/users")))
        finally:
            sleeper.close()

        # Sleeper's /league/<id> settings carry no auction budget field for
        # this league -- the budget lives on the draft object instead (see
        # ffdo.ingest.draft.parse). Fall back to the draft's budget so the
        # engine's league.num_teams * league.budget math never hits a
        # league.budget of None.
        if lg.budget is None:
            lg = replace(lg, budget=state.budget)

        # Sleeper's projections endpoint does not actually honor the
        # position[] query filter server-side (confirmed against the live
        # API) -- it returns every position it has projections for,
        # including FB/CB/K/DEF, none of which this league rosters.
        # `vor.compute` now structurally excludes any position without a
        # replacement level derived from `league.roster_positions` (see
        # ffdo.engine.vor), so no position allowlist is needed here; scoring
        # a few extra positions that get excluded downstream is cheap.
        points = {pid: scoring.score_stats(p.stats, lg.scoring_settings)
                  for pid, p in proj.items() if pid in profiles}
        points = _active_only(points, profiles)
        valued = vor.assign_tiers(vor.compute(points, profiles, lg))

        if state.draft_type == "auction":
            baseline = auction.baseline_prices(valued, lg)
            return board_mod.build_auction_board(
                lg, state, valued, baseline, roster_id=_roster_id(), teams=teams)

        from ffdo.engine import market
        available = {pid for pid in valued if pid not in state.drafted_player_ids()}
        adp_means = {pid: a.adp["half_ppr"] for pid, a in adp_data.items()
                     if a.adp.get("half_ppr", 999) < 999}
        picks_until = lg.num_teams  # conservative: one full round
        survival = market.simulate_survival(adp_means, available, picks_until)
        cow = market.cost_of_waiting(valued, survival, available)
        return board_mod.build_snake_board(
            lg, state, valued, survival, cow, roster_id=_roster_id(), teams=teams)

    # Static mount MUST be registered last: StaticFiles("/") matches any
    # path under it, so routes declared after this point would be shadowed.
    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
