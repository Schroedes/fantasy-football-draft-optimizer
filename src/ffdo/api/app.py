"""FastAPI app. Serves board state and the static board."""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from ffdo.api.session import SessionStore

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Defaults pin this user's real 2026 auction league/draft, so the app works
# out of the box with zero config. `FFDO_LEAGUE_ID` / `FFDO_DRAFT_ID` let it
# point at a different league (e.g. a snake league) without a settings UI --
# read fresh on every request rather than frozen as module constants at
# import time, so an env var set after the process starts (or changed by a
# test via monkeypatch) actually takes effect. A connected `Session` (see
# `_SESSION_STORE` below) takes precedence over both when one exists -- these
# env vars are the zero-config fallback for when the main screen's connect
# flow has never been used.
_DEFAULT_LEAGUE_ID = "1315881559957458944"
_DEFAULT_DRAFT_ID = "1315881559965835264"

# Module-level, not created inside `create_app()`, because `_league_id()` /
# `_draft_id()` / `_roster_id()` are free functions with no app instance in
# hand -- called both from `get_board()` and directly from tests. Tests that
# need an isolated store monkeypatch this attribute rather than constructing
# their own `create_app()` wiring:
#   monkeypatch.setattr(app_mod, "_SESSION_STORE", SessionStore(tmp_path / "session.json"))
_SESSION_STORE = SessionStore(Path("data") / "session.json")


def _league_id() -> str:
    session = _SESSION_STORE.get()
    if session is not None:
        return session.league_id
    return os.environ.get("FFDO_LEAGUE_ID", _DEFAULT_LEAGUE_ID)


def _draft_id() -> str:
    session = _SESSION_STORE.get()
    if session is not None:
        return session.draft_id
    return os.environ.get("FFDO_DRAFT_ID", _DEFAULT_DRAFT_ID)


def _roster_id() -> int | None:
    session = _SESSION_STORE.get()
    if session is not None:
        return session.roster_id
    raw = os.environ.get("FFDO_ROSTER_ID")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _session_public_dict(session) -> dict:
    """Never echo ESPN cookie credentials back over HTTP -- nothing in the
    frontend reads them, and there's no reason to hand a browser-side
    script (or a curious devtools user) a live copy of a session cookie."""
    data = asdict(session)
    data.pop("espn_s2", None)
    data.pop("swid", None)
    return data


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

    def has_value(self) -> bool:
        """True if a value is already cached -- never triggers a fetch."""
        return self._value is not None


_TRAILING_DRAFT_ID_RE = re.compile(r"(\d+)/?$")


def _extract_draft_id(value: str) -> str:
    """Accepts either a bare draft ID or a pasted share URL like
    https://sleeper.app/draft/nfl/1397145756879605760 -- the trailing digit
    run is the ID either way."""
    match = _TRAILING_DRAFT_ID_RE.search(value)
    return match.group(1) if match else value


def _uncached(url: str) -> str:
    """Appends a cache-busting query param so Sleeper's CDN can't serve a
    stale snapshot. Confirmed via response headers that /draft/<id> is
    fronted by Cloudflare with `cache-control: s-maxage=30` -- its `Age`
    header climbs steadily (HIT every time) across normal polls, so without
    this every fetch below can silently be reading up to ~30s-old data
    regardless of how fast we poll. Used only on draft meta + picks, the
    two calls that actually carry live nomination/bid -- the players/
    projections/teams feeds are already covered by this app's own TTL
    caches and gain nothing from busting Sleeper's on top.

    Uses a uuid rather than a timestamp: `time.time_ns()` isn't actually
    unique call-to-call on every platform (observed colliding back-to-back
    on Windows), and a collision here means two different polls share a
    cache key -- the CDN would silently serve the first poll's stale
    response to the second, defeating the whole point.
    """
    return f"{url}{'&' if '?' in url else '?'}_={uuid.uuid4().hex}"


def create_app() -> FastAPI:
    app = FastAPI(title="ffdo")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    from ffdo.api import board as board_mod
    from ffdo.engine import auction, scoring, vor
    from ffdo.ingest import client as client_mod
    from ffdo.ingest import connect as connect_mod
    from ffdo.ingest import draft as draft_mod
    from ffdo.ingest import league as league_mod
    from ffdo.ingest import mock_draft as mock_draft_mod
    from ffdo.ingest import players as players_mod
    from ffdo.ingest import projections as proj_mod
    from ffdo.ingest import teams as teams_mod
    from ffdo.ingest.espn import connect as espn_connect_mod
    from ffdo.ingest.espn import client as espn_client_mod
    from ffdo.ingest.espn import crosswalk as espn_crosswalk_mod
    from ffdo.ingest.espn import draft as espn_draft_mod
    from ffdo.ingest.espn import league as espn_league_mod
    from ffdo.ingest.espn import teams as espn_teams_mod

    players_cache = _TTLCache(ttl_seconds=24 * 3600)
    # Keyed by season rather than a single shared cache: the projections feed
    # is season-specific (`_load_projections` fetches a season-scoped URL),
    # so a single cache would serve a stale season's payload after a league
    # switch within the TTL window (e.g. connect a 2025 league, then a 2026
    # league within the hour). Created lazily per season via
    # `_projections_cache_for()` below. `players_cache` above does NOT need
    # this treatment -- the players feed is not season-scoped.
    projections_caches: dict[int, _TTLCache] = {}
    # Same reasoning, keyed by league_id instead of season: team display
    # names are league-scoped (`/league/<id>/rosters` + `/users`), so a
    # single shared cache would keep serving one league's team names against
    # another league's roster_ids after a league switch within the TTL
    # window. Created lazily per league via `_teams_cache_for()` below.
    teams_caches: dict[str, _TTLCache] = {}
    # Same reasoning as projections_caches/teams_caches: ESPN's player pool
    # is season-scoped and expensive to re-fetch (thousands of entries), and
    # get_board() is polled every 3s during a live draft -- an unkeyed or
    # uncached fetch here would hammer ESPN's API for data that barely
    # changes within a draft session.
    espn_player_pool_caches: dict[int, _TTLCache] = {}
    espn_crosswalk_caches: dict[int, _TTLCache] = {}

    def _projections_cache_for(season: int) -> _TTLCache:
        return projections_caches.setdefault(season, _TTLCache(ttl_seconds=3600))

    def _teams_cache_for(league_id: str) -> _TTLCache:
        return teams_caches.setdefault(league_id, _TTLCache(ttl_seconds=24 * 3600))

    def _espn_player_pool_cache_for(season: int) -> _TTLCache:
        return espn_player_pool_caches.setdefault(season, _TTLCache(ttl_seconds=3600))

    def _espn_crosswalk_cache_for(season: int) -> _TTLCache:
        # Same TTL as the player-pool cache it's derived from -- caching the
        # built Crosswalk (not just its raw inputs) avoids re-running
        # build()'s O(pool size) matching AND re-emitting its "unmatched"
        # warning logs for the same ~20 players on every single 3-second
        # board poll during a live draft.
        return espn_crosswalk_caches.setdefault(season, _TTLCache(ttl_seconds=3600))

    def _load_players(sleeper: client_mod.SleeperClient) -> tuple[dict, dict]:
        """Returns (profiles, espn_id_index). Both are derived from the same
        raw fetch so ESPN connect's crosswalk doesn't need a second,
        separately-cached request for data players_cache already has."""
        raw = sleeper.get_json(f"{client_mod.V1}/players/nfl")
        return players_mod.parse(raw), players_mod.espn_id_index(raw)

    def _load_projections(sleeper: client_mod.SleeperClient, season: int):
        return proj_mod.parse(
            sleeper.get_json(
                f"{client_mod.PROJECTIONS}/{season}"
                "?season_type=regular&position[]=QB&position[]=RB"
                "&position[]=WR&position[]=TE"),
            season)

    def _load_teams(sleeper: client_mod.SleeperClient, league_id: str):
        return teams_mod.parse(
            sleeper.get_json(f"{client_mod.V1}/league/{league_id}/rosters"),
            sleeper.get_json(f"{client_mod.V1}/league/{league_id}/users"))

    def _warm_caches(
        season: int, league_id: str, provider: str,
        espn_s2: str | None, swid: str | None,
    ) -> None:
        """Pre-populates the players/projections/(teams or ESPN player pool)
        TTL caches in the background after a successful /api/connect, so the
        draft room's first load doesn't pay for these fetches synchronously.
        Branches on provider: Sleeper's team-name cache and ESPN's
        player-pool cache are different, non-overlapping resources, and
        warming the wrong one for a given provider is worse than useless --
        Sleeper's /league/<id>/rosters called with an ESPN league_id 404s or
        returns malformed data, raising inside this background task on every
        ESPN connect."""
        sleeper = client_mod.SleeperClient()
        try:
            players_cache.get(lambda: _load_players(sleeper))  # warms the cache; return value unused here
            _projections_cache_for(season).get(lambda: _load_projections(sleeper, season))
            if provider != "espn":
                _teams_cache_for(league_id).get(lambda: _load_teams(sleeper, league_id))
        finally:
            sleeper.close()

        if provider == "espn" and espn_s2 is not None and swid is not None:
            espn = espn_client_mod.EspnClient(espn_s2, swid)
            try:
                _espn_player_pool_cache_for(season).get(
                    lambda: espn.get_json(
                        f"{espn_client_mod.BASE}/seasons/{season}/players"
                        "?view=kona_player_info",
                        extra_headers=espn_client_mod.PLAYER_POOL_FILTER_HEADER))
            finally:
                espn.close()

    @app.post("/api/connect")
    def connect_league(payload: dict, background_tasks: BackgroundTasks) -> dict:
        provider = str(payload.get("provider") or "sleeper").strip().lower()

        if provider == "espn":
            league_id = str(payload.get("league_id", "")).strip()
            espn_s2 = str(payload.get("espn_s2", "")).strip()
            swid = str(payload.get("swid", "")).strip()
            if not league_id or not payload.get("season") or not espn_s2 or not swid:
                raise HTTPException(
                    status_code=400,
                    detail="League ID, season, espn_s2, and SWID are required")
            try:
                season = int(payload["season"])
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Season must be a year")

            sleeper = client_mod.SleeperClient()
            try:
                profiles, espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            finally:
                sleeper.close()

            try:
                session = espn_connect_mod.resolve(
                    league_id, season, espn_s2, swid, profiles, espn_id_index)
            except espn_connect_mod.ConnectError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            league_id = str(payload.get("league_id", "")).strip()
            draft_id_input = str(payload.get("draft_id", "")).strip()
            username = str(payload.get("username", "")).strip()

            if bool(league_id) == bool(draft_id_input):
                raise HTTPException(
                    status_code=400,
                    detail="Provide exactly one of league_id or draft_id")
            if not username:
                raise HTTPException(status_code=400, detail="Username is required")

            sleeper = client_mod.SleeperClient()
            try:
                if league_id:
                    session = connect_mod.resolve(sleeper, league_id, username)
                else:
                    session = connect_mod.resolve_mock(
                        sleeper, _extract_draft_id(draft_id_input), username)
            except connect_mod.ConnectError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                sleeper.close()

        _SESSION_STORE.save(session)
        background_tasks.add_task(
            _warm_caches, session.season, session.league_id, session.provider,
            session.espn_s2, session.swid)
        return _session_public_dict(session)

    @app.get("/api/session")
    def get_session() -> dict | None:
        session = _SESSION_STORE.get()
        return _session_public_dict(session) if session is not None else None

    @app.get("/api/readiness")
    def get_readiness() -> dict:
        session = _SESSION_STORE.get()
        projections_synced = (
            session is not None and _projections_cache_for(session.season).has_value())
        return {
            "league_draft": "synced" if session is not None else "pending",
            "players": "synced" if players_cache.has_value() else "pending",
            "projections": "synced" if projections_synced else "pending",
        }

    @app.get("/api/board/live")
    def get_board_live() -> dict:
        """Just the nomination/bid, at the cost of the two Sleeper calls that
        actually carry them -- draft meta and picks -- skipping the league
        fetch and the full scoring/VOR/baseline/roster rebuild that
        `/api/board` does. Polled every second (see board.js) so an auction's
        split-second bidding tracks live without waiting on the heavier
        endpoint's multi-second valuation recompute.
        """
        draft_id = _draft_id()
        sleeper = client_mod.SleeperClient()
        try:
            draft_meta = sleeper.get_json(_uncached(f"{client_mod.V1}/draft/{draft_id}"))
            picks_raw = sleeper.get_json(_uncached(f"{client_mod.V1}/draft/{draft_id}/picks"))
        finally:
            sleeper.close()
        state = draft_mod.parse(draft_meta, picks_raw)
        return {
            "live_nomination": board_mod.build_live_nomination(state),
            "picks_made": len(state.picks),
        }

    @app.get("/api/board")
    def get_board() -> dict:
        session = _SESSION_STORE.get()
        provider = session.provider if session is not None else "sleeper"

        if provider == "espn":
            if session is None or session.espn_s2 is None or session.swid is None:
                raise HTTPException(
                    status_code=400,
                    detail="No connected ESPN session -- connect from the main screen first")

            # ESPN's snake-only MVP has no mock-draft equivalent, so this
            # branch is never a mock draft.
            is_mock = False

            sleeper = client_mod.SleeperClient()
            try:
                profiles, espn_id_index = players_cache.get(lambda: _load_players(sleeper))
                proj, adp_data = _projections_cache_for(session.season).get(
                    lambda: _load_projections(sleeper, session.season))
            finally:
                sleeper.close()

            espn = espn_client_mod.EspnClient(session.espn_s2, session.swid)
            try:
                raw = espn.get_json(
                    f"{espn_client_mod.BASE}/seasons/{session.season}/segments/0/"
                    f"leagues/{session.league_id}?view=mSettings&view=mTeam&view=mDraftDetail")
                player_pool_raw = _espn_player_pool_cache_for(session.season).get(
                    lambda: espn.get_json(
                        f"{espn_client_mod.BASE}/seasons/{session.season}/players"
                        "?view=kona_player_info",
                        extra_headers=espn_client_mod.PLAYER_POOL_FILTER_HEADER))
            finally:
                espn.close()

            lg = espn_league_mod.parse(raw)
            cw = _espn_crosswalk_cache_for(session.season).get(
                lambda: espn_crosswalk_mod.build(
                    espn_id_index, profiles,
                    espn_crosswalk_mod.parse_player_pool(player_pool_raw)))
            state = espn_draft_mod.parse(raw, cw)
            teams = espn_teams_mod.parse(raw)
        else:
            league_id = _league_id()
            draft_id = _draft_id()
            is_mock = not league_id
            sleeper = client_mod.SleeperClient()
            try:
                if is_mock:
                    draft_meta = sleeper.get_json(_uncached(f"{client_mod.V1}/draft/{draft_id}"))
                    try:
                        lg = mock_draft_mod.build_league_profile(draft_meta)
                    except mock_draft_mod.MockDraftError as exc:
                        raise HTTPException(status_code=400, detail=str(exc)) from exc
                    picks_raw = mock_draft_mod.backfill_roster_ids(
                        sleeper.get_json(_uncached(f"{client_mod.V1}/draft/{draft_id}/picks")),
                        draft_meta)
                else:
                    lg = league_mod.parse(
                        sleeper.get_json(f"{client_mod.V1}/league/{league_id}"))
                    draft_meta = sleeper.get_json(_uncached(f"{client_mod.V1}/draft/{draft_id}"))
                    picks_raw = sleeper.get_json(_uncached(f"{client_mod.V1}/draft/{draft_id}/picks"))

                profiles, _espn_id_index = players_cache.get(lambda: _load_players(sleeper))
                proj, adp_data = _projections_cache_for(lg.season).get(
                    lambda: _load_projections(sleeper, lg.season))
                state = draft_mod.parse(draft_meta, picks_raw)
                # Mock drafts have no /league/<id>/rosters or /users to derive
                # team display names from -- board.py's rosters payload already
                # falls back to "Team {roster_id}" when `teams` is None, which
                # is exactly the right behavior here (no separate mock-specific
                # naming logic needed).
                teams = (None if is_mock else
                         _teams_cache_for(league_id).get(lambda: _load_teams(sleeper, league_id)))
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

        if is_mock:
            # draft_order (and therefore roster_id) can only appear AFTER
            # connecting, so it must be re-resolved live from the same
            # draft_meta fetched above every poll -- never trusted from the
            # persisted session's static roster_id.
            session = _SESSION_STORE.get()
            roster_id = (mock_draft_mod.resolve_roster_id(draft_meta, session.user_id)
                        if session is not None else None)
        else:
            roster_id = _roster_id()

        if state.draft_type == "auction":
            baseline = auction.baseline_prices(valued, lg)
            board = board_mod.build_auction_board(
                lg, state, valued, baseline, roster_id=roster_id, teams=teams)
        else:
            from ffdo.engine import market
            available = {pid for pid in valued if pid not in state.drafted_player_ids()}
            adp_means = {pid: a.adp["half_ppr"] for pid, a in adp_data.items()
                        if a.adp.get("half_ppr", 999) < 999}
            picks_until = lg.num_teams  # conservative: one full round
            survival = market.simulate_survival(adp_means, available, picks_until)
            cow = market.cost_of_waiting(valued, survival, available)
            board = board_mod.build_snake_board(
                lg, state, valued, survival, cow, roster_id=roster_id, teams=teams)

        board["is_mock"] = is_mock
        return board

    # Static mounts MUST be registered last: StaticFiles("/") matches any
    # path under it, so routes declared after this point would be shadowed.
    # `/board` is mounted before `/` so the board's own files aren't
    # shadowed by the root mount matching first.
    board_dir = WEB_DIR / "board"
    if board_dir.exists():
        app.mount("/board", StaticFiles(directory=board_dir, html=True), name="board")
    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
