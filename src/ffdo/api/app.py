"""FastAPI app. Serves board state and the static board."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from ffdo.api.store import LeagueStore
from ffdo.domain.models import DiscoveredLeague, TrackedLeague

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Module-level, not created inside `create_app()`, because `_load_league()` is
# a free function with no app instance in hand -- called from every
# league-scoped endpoint and directly from tests. Tests that need an isolated
# store monkeypatch this attribute rather than constructing their own
# `create_app()` wiring (see tests/api/conftest.py):
#   monkeypatch.setattr(app_mod, "_STORE", LeagueStore(tmp_path / "ffdo.db"))
#
# `legacy_session_path` is what makes an existing single-league install carry
# its connected league across this refactor: the first `_connect()` imports
# `data/session.json` and renames it aside. Deliberately absent from the test
# fixture -- production startup is the only place that migration belongs.
_STORE = LeagueStore(Path("data") / "ffdo.db",
                     legacy_session_path=Path("data") / "session.json")

_FORMATS = ("redraft", "keeper", "dynasty")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_league(league_key: str) -> TrackedLeague:
    """The single gate every league-scoped endpoint goes through. Handlers
    above `ffdo.ingest` never see raw provider JSON or credentials -- they
    get a `TrackedLeague` from here and a `ProviderCredential` from
    `_STORE.get_credential()`."""
    lg = _STORE.get(league_key)
    if lg is None:
        raise HTTPException(status_code=404, detail="League not tracked")
    return lg


def _league_public_dict(lg: TrackedLeague) -> dict:
    """`TrackedLeague.fmt` avoids shadowing the builtin in Python; `format`
    is the wire name the frontend reads. `resolved_format` is a property, so
    `asdict` misses it -- it is what the UI should actually display."""
    data = asdict(lg)
    data["format"] = data.pop("fmt")
    data["resolved_format"] = lg.resolved_format
    return data


def _discovered_public(d: DiscoveredLeague) -> dict:
    """Same `fmt` -> `format` wire rename as `_league_public_dict`. Carries
    no credentials by construction -- `DiscoveredLeague` has no field for
    them."""
    data = asdict(d)
    data["format"] = data.pop("fmt")
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
    from ffdo.domain import models as models_mod
    from ffdo.engine import auction, scoring, vor
    from ffdo.ingest import client as client_mod
    from ffdo.ingest import connect as connect_mod
    from ffdo.ingest import discover as discover_mod
    from ffdo.ingest import draft as draft_mod
    from ffdo.ingest import league as league_mod
    from ffdo.ingest import mock_draft as mock_draft_mod
    from ffdo.ingest import players as players_mod
    from ffdo.ingest import projections as proj_mod
    from ffdo.ingest import teams as teams_mod
    from ffdo.ingest.espn import connect as espn_connect_mod
    from ffdo.ingest.espn import client as espn_client_mod
    from ffdo.ingest.espn import discover as espn_discover_mod
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
                "&position[]=WR&position[]=TE&position[]=DEF"
                "&position[]=K"),
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
        TTL caches in the background after a successful
        `POST /api/leagues/track`, so the draft room's first load doesn't pay
        for these fetches synchronously.
        Branches on provider: Sleeper's team-name cache and ESPN's
        player-pool cache are different, non-overlapping resources, and
        warming the wrong one for a given provider is worse than useless --
        Sleeper's /league/<id>/rosters called with an ESPN league_id 404s or
        returns malformed data, raising inside this background task on every
        ESPN connect.

        The team-name warm is gated on `provider == "sleeper"` specifically,
        not merely `!= "espn"`: a `sleeper-mock` league has no league behind
        it at all (its `provider_league_id` IS a draft id), so
        /league/<draft_id>/rosters does not exist and would raise here on
        every mock track. Mocks legitimately have no team names -- board.py
        falls back to "Team {roster_id}" -- so there is nothing to warm."""
        sleeper = client_mod.SleeperClient()
        try:
            players_cache.get(lambda: _load_players(sleeper))  # warms the cache; return value unused here
            _projections_cache_for(season).get(lambda: _load_projections(sleeper, season))
            if provider == "sleeper":
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

    def _season_from(payload: dict) -> int:
        try:
            return int(payload["season"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Season must be a year") from exc

    def _tracked_keys() -> frozenset[str]:
        return frozenset(lg.league_key for lg in _STORE.list())

    def _sleeper_discover(username: str, season: int) -> list:
        """Sleeper discovery needs a username -> user_id hop first. Both calls
        share one client so the connection is opened and closed once."""
        sleeper = client_mod.SleeperClient()
        try:
            user_id = discover_mod.resolve_user_id(sleeper, username)
            return discover_mod.list_leagues(
                sleeper, user_id, season, tracked_keys=_tracked_keys())
        except connect_mod.ConnectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            # A provider outage is not the user's mistake and not this app's
            # bug: 502 says "the upstream failed", where a bare 500 would
            # point the user at their own input.
            raise HTTPException(
                status_code=502, detail="Sleeper is not responding right now") from exc
        finally:
            sleeper.close()

    def _espn_discover(espn_s2: str, swid: str, season: int) -> list:
        """No `httpx.HTTPError` -> 502 arm here, unlike `_sleeper_discover`:
        `espn/discover.py` already swallows every transport/status failure
        except 401/403 and returns `[]`, deliberately falling back to the
        manual add-by-league-ID path. Expired cookies are the only thing that
        reaches this handler, as a `ConnectError`."""
        try:
            return espn_discover_mod.list_leagues(
                espn_s2, swid, season, tracked_keys=_tracked_keys())
        except espn_connect_mod.ConnectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _espn_track(provider_league_id: str, season: int, cred) -> TrackedLeague:
        """Shared by `POST /api/leagues/track` and `.../refresh`. ESPN's
        connect needs the Sleeper player profiles + espn_id index to build its
        crosswalk, which `players_cache` already holds."""
        sleeper = client_mod.SleeperClient()
        try:
            profiles, espn_id_index = players_cache.get(lambda: _load_players(sleeper))
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail="Couldn't reach Sleeper, try again") from exc
        finally:
            sleeper.close()
        try:
            return espn_connect_mod.track(
                provider_league_id, season, cred.espn_s2, cred.swid,
                profiles, espn_id_index)
        except espn_connect_mod.ConnectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            # Same reasoning as `_sleeper_discover`'s 502 arm: an ESPN outage
            # is neither the user's mistake nor this app's bug, and a bare 500
            # would point them at their own input.
            raise HTTPException(
                status_code=502, detail="Couldn't reach ESPN, try again") from exc

    def _require_espn_credential(action: str = "using this league"):
        """Two genuinely different failures, two different instructions: no
        stored credential at all means the user never connected ESPN (mirrors
        the Sleeper branch's "Connect Sleeper before ..."), while a stored
        credential missing its cookies is a connection that has gone stale.
        Telling someone to "reconnect" a provider they never connected sends
        them looking for a broken thing that was never there."""
        cred = _STORE.get_credential("espn")
        if cred is None:
            raise HTTPException(
                status_code=400, detail=f"Connect ESPN before {action}")
        if cred.espn_s2 is None or cred.swid is None:
            raise HTTPException(
                status_code=400,
                detail="Your ESPN cookies look expired -- reconnect ESPN")
        return cred

    @app.post("/api/providers/connect")
    def providers_connect(payload: dict) -> dict:
        """Stores one provider's credentials and immediately answers with
        everything that credential can see for the season, so the onboarding
        screen is a single round trip: paste once, pick leagues from a list.

        The credentials are persisted but never echoed back -- nothing in the
        frontend reads them, and handing a browser-side script a live copy of
        a session cookie is a needless leak. `DiscoveredLeague` has no field
        for them, so the response body cannot carry them by construction."""
        provider = str(payload.get("provider") or "").strip().lower()
        season = _season_from(payload)

        if provider == "sleeper":
            username = str(payload.get("username", "")).strip()
            if not username:
                raise HTTPException(status_code=400, detail="Username is required")
            leagues = _sleeper_discover(username, season)
            _STORE.put_credential(models_mod.ProviderCredential(
                provider="sleeper", user_identifier=username,
                espn_s2=None, swid=None, updated_at=_now_iso()))
        elif provider == "espn":
            espn_s2 = str(payload.get("espn_s2", "")).strip()
            swid = str(payload.get("swid", "")).strip()
            if not espn_s2 or not swid:
                raise HTTPException(
                    status_code=400, detail="espn_s2 and SWID are required")
            leagues = _espn_discover(espn_s2, swid, season)
            # The SWID doubles as ESPN's user identifier -- it is what
            # `teams.find_roster_id` matches a league member against.
            _STORE.put_credential(models_mod.ProviderCredential(
                provider="espn", user_identifier=swid,
                espn_s2=espn_s2, swid=swid, updated_at=_now_iso()))
        else:
            raise HTTPException(status_code=400, detail="Unknown provider")

        return {"leagues": [_discovered_public(d) for d in leagues]}

    # Declared BEFORE `/api/leagues/{league_key}`: FastAPI matches routes in
    # declaration order, so a literal path registered after a parameterized
    # sibling is unreachable -- "discovered" would be captured as a
    # league_key and 404 as an untracked league.
    @app.get("/api/leagues/discovered")
    def leagues_discovered(provider: str, season: int) -> dict:
        """Re-runs discovery for an already-connected provider, e.g. to pick
        up a league joined after onboarding, or to browse a different season.
        Reads the stored credential rather than asking for it again."""
        cred = _STORE.get_credential(provider)
        if cred is None:
            raise HTTPException(
                status_code=400,
                detail=f"No stored {provider} credentials -- connect first")
        if provider == "sleeper":
            leagues = _sleeper_discover(cred.user_identifier, season)
        elif provider == "espn":
            if cred.espn_s2 is None or cred.swid is None:
                raise HTTPException(
                    status_code=400,
                    detail="Your ESPN cookies look expired -- reconnect ESPN")
            leagues = _espn_discover(cred.espn_s2, cred.swid, season)
        else:
            raise HTTPException(status_code=400, detail="Unknown provider")
        return {"leagues": [_discovered_public(d) for d in leagues]}

    @app.post("/api/leagues/track")
    def track_leagues(payload: dict, background_tasks: BackgroundTasks) -> dict:
        """Accepts either a single league object or `{"leagues": [...]}`, so
        the discovery screen can track a whole multi-select in one call.

        Credentials are never taken from the request body -- the username /
        cookies come from what `POST /api/providers/connect` already stored,
        which is what keeps request payloads credential-free.

        All-or-nothing: every item is resolved against its provider FIRST and
        only the fully-resolved batch is persisted. Upserting inside the
        resolve loop meant a batch that failed on item 2 left item 1 tracked
        while the caller got a 400 and no `leagues` body -- a half-written
        state the discovery screen has no way to see or reconcile.
        """
        items = payload.get("leagues") or [payload]
        resolved: list[TrackedLeague] = []
        cred = None
        lg: TrackedLeague | None = None

        for item in items:
            provider = str(item.get("provider") or "").strip().lower()
            pid = str(item.get("provider_league_id", "")).strip()
            season = _season_from(item)

            if provider in ("sleeper", "sleeper-mock"):
                cred = _STORE.get_credential("sleeper")
                if cred is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Connect Sleeper before tracking a league")
                sleeper = client_mod.SleeperClient()
                try:
                    if provider == "sleeper-mock":
                        # Preserves the removed /api/connect's affordance:
                        # a user pastes the whole share URL, not the bare ID.
                        lg = connect_mod.track_mock(
                            sleeper, _extract_draft_id(pid), cred.user_identifier)
                    else:
                        lg = connect_mod.track(sleeper, pid, cred.user_identifier)
                except connect_mod.ConnectError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                except httpx.HTTPError as exc:
                    # Same arm `_sleeper_discover` already has: a provider
                    # outage is not the user's mistake, and a bare 500 would
                    # point them at their own input.
                    raise HTTPException(
                        status_code=502,
                        detail="Couldn't reach Sleeper, try again") from exc
                finally:
                    sleeper.close()
            elif provider == "espn":
                cred = _require_espn_credential("tracking a league")
                lg = _espn_track(pid, season, cred)
            else:
                raise HTTPException(status_code=400, detail="Unknown provider")

            resolved.append(lg)

        for tracked in resolved:
            _STORE.upsert(tracked)

        # One warm per dispatch, for the last league tracked. `_warm_caches`
        # populates the players cache (shared) plus the season- and
        # league-scoped ones; warming every league in a batch would multiply
        # the fetches for a cache the draft room only needs for whichever
        # league the user opens first. `items` is never empty (an empty
        # "leagues" list falls back to `[payload]`), so `lg`/`cred` are set.
        background_tasks.add_task(
            _warm_caches, lg.season, lg.provider_league_id, lg.provider,
            cred.espn_s2, cred.swid)
        return {"leagues": [_league_public_dict(t) for t in resolved]}

    @app.get("/api/leagues")
    def list_leagues_endpoint() -> list[dict]:
        """The switcher's payload: one compact row per tracked league. The
        full record is a separate `GET /api/leagues/{league_key}`, so the
        switcher doesn't ship every league's scoring settings on page load."""
        return [
            {
                "league_key": lg.league_key, "name": lg.name,
                "provider": lg.provider, "season": lg.season,
                "format": lg.fmt, "resolved_format": lg.resolved_format,
                "draft_status": lg.draft_status, "is_mock": lg.is_mock,
                "needs_attention": False,
            }
            for lg in _STORE.list()
        ]

    @app.get("/api/leagues/{league_key}")
    def get_league(league_key: str) -> dict:
        return _league_public_dict(_load_league(league_key))

    @app.delete("/api/leagues/{league_key}", status_code=204)
    def untrack_league(league_key: str) -> None:
        # _load_league first so untracking something that was never tracked
        # is a 404, not a silently-successful no-op.
        _load_league(league_key)
        _STORE.delete(league_key)

    @app.patch("/api/leagues/{league_key}")
    def patch_league(league_key: str, payload: dict) -> dict:
        """The one league field a user can set by hand: keeper/dynasty
        detection is a heuristic over provider settings, so an explicit
        override has to be able to win. An explicit `null` clears it.

        The key must be PRESENT: `payload.get("format_override")` cannot tell
        `{}` from `{"format_override": null}`, so without this guard a PATCH
        with an empty or unrelated body would silently wipe an override the
        user set deliberately."""
        _load_league(league_key)
        if "format_override" not in payload:
            raise HTTPException(status_code=422, detail="format_override is required")
        value = payload["format_override"]
        if value is not None and value not in _FORMATS:
            raise HTTPException(status_code=422, detail="Invalid format_override")
        _STORE.set_format_override(league_key, value)
        return _league_public_dict(_load_league(league_key))

    @app.post("/api/leagues/{league_key}/refresh")
    def refresh_league(league_key: str) -> dict:
        """Re-resolves a tracked league against its provider -- draft status,
        roster slot, scoring changes. Returns the re-read row rather than the
        freshly-resolved one, because `LeagueStore.upsert` deliberately
        preserves an existing `format_override` that the provider knows
        nothing about."""
        lg = _load_league(league_key)
        if lg.provider in ("sleeper", "sleeper-mock"):
            cred = _STORE.get_credential("sleeper")
            if cred is None:
                raise HTTPException(
                    status_code=400,
                    detail="Connect Sleeper before refreshing a league")
            sleeper = client_mod.SleeperClient()
            try:
                if lg.provider == "sleeper-mock":
                    fresh = connect_mod.track_mock(
                        sleeper, lg.provider_league_id, cred.user_identifier)
                else:
                    fresh = connect_mod.track(
                        sleeper, lg.provider_league_id, cred.user_identifier)
            except connect_mod.ConnectError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=502,
                    detail="Couldn't reach Sleeper, try again") from exc
            finally:
                sleeper.close()
        else:
            cred = _require_espn_credential("refreshing a league")
            fresh = _espn_track(lg.provider_league_id, lg.season, cred)
        _STORE.upsert(fresh)
        return _league_public_dict(_load_league(league_key))

    @app.get("/api/leagues/{league_key}/readiness")
    def get_readiness(league_key: str) -> dict:
        lg = _load_league(league_key)
        # `league_draft` is unconditionally synced: the league IS tracked, or
        # _load_league would have 404'd above.
        return {
            "league_draft": "synced",
            "players": "synced" if players_cache.has_value() else "pending",
            "projections": ("synced" if _projections_cache_for(lg.season).has_value()
                            else "pending"),
        }

    @app.get("/api/leagues/{league_key}/board/live")
    def get_board_live(league_key: str) -> dict:
        """Just the nomination/bid, at the cost of the two Sleeper calls that
        actually carry them -- draft meta and picks -- skipping the league
        fetch and the full scoring/VOR/baseline/roster rebuild that
        `/api/leagues/{key}/board` does. Polled every second (see board.js) so
        an auction's split-second bidding tracks live without waiting on the
        heavier endpoint's multi-second valuation recompute.
        """
        session = _load_league(league_key)
        provider = session.provider

        if provider == "espn":
            cred = _require_espn_credential("opening this board")

            # Mirrors get_board()'s ESPN branch below, minus the mTeam view
            # (team names aren't needed here) and minus _load_projections
            # (irrelevant to nomination/pick-count). live_nomination always
            # comes back None for ESPN -- espn_draft_mod.parse() never sets
            # nominated_player_id/current_bid, those are Sleeper-only
            # concepts -- so this needs no ESPN-specific nomination logic,
            # just a state built from the right source.
            sleeper = client_mod.SleeperClient()
            try:
                profiles, espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            finally:
                sleeper.close()

            espn = espn_client_mod.EspnClient(cred.espn_s2, cred.swid)
            try:
                raw = espn.get_json(
                    f"{espn_client_mod.BASE}/seasons/{session.season}/segments/0/"
                    f"leagues/{session.provider_league_id}"
                    "?view=mSettings&view=mDraftDetail")
                player_pool_raw = _espn_player_pool_cache_for(session.season).get(
                    lambda: espn.get_json(
                        f"{espn_client_mod.BASE}/seasons/{session.season}/players"
                        "?view=kona_player_info",
                        extra_headers=espn_client_mod.PLAYER_POOL_FILTER_HEADER))
            finally:
                espn.close()

            cw = _espn_crosswalk_cache_for(session.season).get(
                lambda: espn_crosswalk_mod.build(
                    espn_id_index, profiles,
                    espn_crosswalk_mod.parse_player_pool(player_pool_raw)))
            state = espn_draft_mod.parse(raw, cw)
        else:
            draft_id = session.draft_id
            sleeper = client_mod.SleeperClient()
            try:
                draft_meta = sleeper.get_json(_uncached(f"{client_mod.V1}/draft/{draft_id}"))
                picks_raw = sleeper.get_json(_uncached(f"{client_mod.V1}/draft/{draft_id}/picks"))
            finally:
                sleeper.close()
            state = draft_mod.parse(draft_meta, picks_raw)

        # Every poll of a live draft is also the freshest signal this app has
        # about whether that draft is still running -- cheaper and more
        # accurate than asking the user to hit refresh for the switcher's
        # status badge to catch up.
        _STORE.touch_status(league_key, state.status)

        return {
            "live_nomination": board_mod.build_live_nomination(state),
            "picks_made": len(state.picks),
        }

    @app.get("/api/leagues/{league_key}/board")
    def get_board(league_key: str) -> dict:
        session = _load_league(league_key)
        provider = session.provider

        if provider == "espn":
            cred = _require_espn_credential("opening this board")

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

            espn = espn_client_mod.EspnClient(cred.espn_s2, cred.swid)
            try:
                raw = espn.get_json(
                    f"{espn_client_mod.BASE}/seasons/{session.season}/segments/0/"
                    f"leagues/{session.provider_league_id}"
                    "?view=mSettings&view=mTeam&view=mDraftDetail")
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
            # A Sleeper mock draft has no league behind it at all -- its
            # `provider_league_id` IS its draft id, and `is_mock` (set at
            # track time, from the provider) is what selects the
            # mock-specific league-profile/backfill path below.
            is_mock = session.is_mock
            league_id = session.provider_league_id
            draft_id = session.draft_id
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

        # Every poll of a live draft is also the freshest signal this app has
        # about whether that draft is still running -- keeps the switcher's
        # status badge current without a separate refresh.
        _STORE.touch_status(league_key, state.status)

        # Sleeper's /league/<id> settings carry no auction budget field for
        # this league -- the budget lives on the draft object instead (see
        # ffdo.ingest.draft.parse). Fall back to the draft's budget so the
        # engine's league.num_teams * league.budget math never hits a
        # league.budget of None.
        if lg.budget is None:
            lg = replace(lg, budget=state.budget)

        # Sleeper's projections endpoint mostly honors the position[] query
        # filter -- confirmed live: `position[]=DEF` alone returns exactly
        # the 32 team defenses, and `_load_projections`'s QB/RB/WR/TE/DEF/K
        # filter returns only those positions, plus a couple of unrequested
        # leaks (FB, one CB row) that Sleeper's server includes regardless
        # of what's asked for. `vor.compute` structurally excludes any
        # position without a replacement level derived from
        # `league.roster_positions` (see ffdo.engine.vor), so no position
        # allowlist is needed here; scoring the occasional leaked FB/CB row
        # that gets excluded downstream is cheap. What the filter does NOT
        # do is add positions on its own -- DEF/K must be requested
        # explicitly (`_load_projections` does) or they're silently absent
        # from `proj` and therefore invisible to every league that rosters
        # them, no matter what `league.roster_positions` says.
        points = {pid: scoring.score_stats(p.stats, lg.scoring_settings)
                  for pid, p in proj.items() if pid in profiles}
        points = _active_only(points, profiles)
        valued = vor.assign_tiers(vor.compute(points, profiles, lg))

        if is_mock:
            # draft_order (and therefore roster_id) can only appear AFTER
            # tracking, so it must be re-resolved live from the same
            # draft_meta fetched above every poll -- never trusted from the
            # tracked league's static roster_id.
            roster_id = mock_draft_mod.resolve_roster_id(draft_meta, session.user_id)
        else:
            roster_id = session.roster_id

        if state.draft_type == "auction":
            baseline = auction.baseline_prices(valued, lg)
            board = board_mod.build_auction_board(
                lg, state, valued, baseline, roster_id=roster_id, teams=teams)
        else:
            from ffdo.engine import market
            from ffdo.engine import snake_plan as snake_plan_mod
            available = {pid for pid in valued if pid not in state.drafted_player_ids()}
            adp_means = {pid: a.adp["half_ppr"] for pid, a in adp_data.items()
                        if a.adp.get("half_ppr", 999) < 999}
            picks_until = lg.num_teams  # conservative: one full round
            survival = market.simulate_survival(adp_means, available, picks_until)
            cow = market.cost_of_waiting(valued, survival, available)
            plan = snake_plan_mod.simulate_snake_plan(valued, adp_means, state, lg, roster_id)
            board = board_mod.build_snake_board(
                lg, state, valued, survival, cow, plan, roster_id=roster_id, teams=teams)

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
