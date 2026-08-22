"""FastAPI app. Serves board state and the static board."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

LEAGUE_ID = "1315881559957458944"
DRAFT_ID = "1315881559965835264"


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

    def get(self, loader: Callable[[], Any]) -> Any:
        now = time.monotonic()
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

    players_cache = _TTLCache(ttl_seconds=24 * 3600)
    projections_cache = _TTLCache(ttl_seconds=3600)

    @app.get("/api/board")
    def get_board() -> dict:
        sleeper = client_mod.SleeperClient()
        try:
            lg = league_mod.parse(
                sleeper.get_json(f"{client_mod.V1}/league/{LEAGUE_ID}"))
            profiles = players_cache.get(
                lambda: players_mod.parse(
                    sleeper.get_json(f"{client_mod.V1}/players/nfl")))
            proj, _adp = projections_cache.get(
                lambda: proj_mod.parse(
                    sleeper.get_json(
                        f"{client_mod.PROJECTIONS}/{lg.season}"
                        "?season_type=regular&position[]=QB&position[]=RB"
                        "&position[]=WR&position[]=TE"),
                    lg.season))
            state = draft_mod.parse(
                sleeper.get_json(f"{client_mod.V1}/draft/{DRAFT_ID}"),
                sleeper.get_json(f"{client_mod.V1}/draft/{DRAFT_ID}/picks"))
        finally:
            sleeper.close()

        # Sleeper's /league/<id> settings carry no auction budget field for
        # this league -- the budget lives on the draft object instead (see
        # ffdo.ingest.draft.parse). Fall back to the draft's budget so the
        # engine's league.num_teams * league.budget math never hits a
        # league.budget of None.
        if lg.budget is None:
            lg = replace(lg, budget=state.budget)

        points = {pid: scoring.score_stats(p.stats, lg.scoring_settings)
                  for pid, p in proj.items() if pid in profiles}
        valued = vor.assign_tiers(vor.compute(points, profiles, lg))
        baseline = auction.baseline_prices(valued, lg)
        return board_mod.build_auction_board(lg, state, valued, baseline)

    # Static mount MUST be registered last: StaticFiles("/") matches any
    # path under it, so routes declared after this point would be shadowed.
    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
