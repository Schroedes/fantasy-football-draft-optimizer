"""FastAPI app. Serves board state and the static board."""

from __future__ import annotations

import logging
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

from ffdo.api.draft_pick_ledger import DraftPickLedger
from ffdo.api.lineup_ledger import LineupLedger
from ffdo.api.store import LeagueStore
from ffdo.api.trade_ledger import TradeLedger
from ffdo.api.waiver_ledger import WaiverLedger
from ffdo.domain.models import NON_STARTING_SLOTS, DiscoveredLeague, TrackedLeague

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
_LINEUP_LEDGER = LineupLedger(Path("data") / "ffdo.db")
_TRADE_LEDGER = TradeLedger(Path("data") / "ffdo.db")
_WAIVER_LEDGER = WaiverLedger(Path("data") / "ffdo.db")
_DRAFT_PICK_LEDGER = DraftPickLedger(Path("data") / "ffdo.db")
# In-memory only, per draft_id -- the last known live simulate_survival()
# estimate for each player while they were still available. A player who
# gets drafted between two polls drops out of the NEXT poll's `survival`
# dict, so this must be updated with dict.update (merge), never replaced
# wholesale, or the last known value for a just-drafted player would be
# lost the moment he's no longer "available". Process-lifetime only, same
# as every other in-memory cache in this module -- resets on restart,
# which is fine: it only ever backfills a value that would otherwise be
# None.
_DRAFT_SURVIVAL_CACHE: dict[str, dict[str, float]] = {}

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


def _standings_rank(rosters) -> dict[int, int]:
    """roster_id -> standings position (1 = best), by (wins, points_for)
    desc -- the default tiebreak both Sleeper and ESPN use.

    Deliberately derived here rather than read off the provider: Sleeper's
    /league/<id>/rosters carries no rank field at all, and ESPN's
    `playoffSeed` encodes division winners' auto-seeding, which is not the
    straight record order the power ranking's `delta` is meant to compare
    against. Computing both sides of that comparison the same way is what
    makes "3 spots better than your record" mean anything.
    """
    order = sorted(rosters, key=lambda r: (r.wins, r.points_for), reverse=True)
    return {r.roster_id: i + 1 for i, r in enumerate(order)}


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

    @property
    def value(self) -> Any:
        """Peek the cached value without triggering a fetch. Callers must
        check `has_value()` first -- this simply returns whatever `_value`
        currently holds (`None` if nothing has been cached yet)."""
        return self._value


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
    from ffdo.domain.constants import (
        DYNASTY_AGE_CURVE, FAAB_BID_CURVE, NFL_BYE_WEEKS, PICK_VALUE_CURVE, SEASON_LENGTH)
    from ffdo.domain.models import DraftPickAsset
    from ffdo.engine import auction, scoring, vor
    from ffdo.engine import pick_value as pick_value_mod
    from ffdo.engine import power_ranking as power_ranking_mod
    from ffdo.engine import ros_value as ros_value_mod
    from ffdo.engine import roster_needs as roster_needs_mod
    from ffdo.engine import scorecard as scorecard_mod
    from ffdo.engine import trade_value as trade_value_mod
    from ffdo.engine import trade_targets as trade_targets_mod
    from ffdo.engine import matchup_score as matchup_score_mod
    from ffdo.ingest.sleeper import matchups as matchups_mod
    from ffdo.ingest import actuals as actuals_mod
    from ffdo.ingest import client as client_mod
    from ffdo.ingest import connect as connect_mod
    from ffdo.ingest import discover as discover_mod
    from ffdo.ingest import draft as draft_mod
    from ffdo.ingest import league as league_mod
    from ffdo.ingest import mock_draft as mock_draft_mod
    from ffdo.ingest import nfl_state as nfl_state_mod
    from ffdo.ingest import players as players_mod
    from ffdo.ingest import projections as proj_mod
    from ffdo.ingest import rosters as rosters_mod
    from ffdo.ingest import teams as teams_mod
    from ffdo.ingest.espn import actuals as espn_actuals_mod
    from ffdo.ingest.espn import connect as espn_connect_mod
    from ffdo.ingest.espn import client as espn_client_mod
    from ffdo.ingest.espn import discover as espn_discover_mod
    from ffdo.ingest.espn import crosswalk as espn_crosswalk_mod
    from ffdo.ingest.espn import draft as espn_draft_mod
    from ffdo.ingest.espn import league as espn_league_mod
    from ffdo.ingest.espn import rosters as espn_rosters_mod
    from ffdo.ingest.espn import teams as espn_teams_mod
    from ffdo.ingest.espn import transactions as espn_transactions_mod
    from ffdo.ingest.sleeper import historical_weekly_stats as historical_weekly_stats_mod
    from ffdo.ingest.sleeper import player_history as player_history_mod
    from ffdo.ingest.sleeper import traded_picks as traded_picks_mod
    from ffdo.ingest.sleeper import transactions as transactions_mod
    from ffdo.ingest.sleeper import waivers as waivers_mod
    from ffdo.engine import waiver_value as waiver_value_mod
    from ffdo.ingest.sleeper import weekly_projections as weekly_projections_mod
    from ffdo.ingest.sleeper import schedule as schedule_mod
    from ffdo.engine import weekly_lineup as weekly_lineup_mod

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
    # /state/nfl is a single global document (not league- or season-scoped),
    # so unlike the caches above it needs no keying -- every league's season
    # view asks the same question and gets the same answer. An hour is well
    # inside the shortest interval that matters here (Sleeper advances
    # `display_week` once a week, on Tuesday).
    nfl_state_cache = _TTLCache(ttl_seconds=3600)
    # Separate from `projections_caches` despite reading the same feed: this
    # one holds ONLY the `SeasonProjection` half and is allowed to fall back
    # to `allow_contaminated=True` (see `_load_projection_anchor`), which the
    # draft board's cache must never do. A 24h TTL because the preseason
    # anchor is, by definition, not changing during the season.
    season_proj_anchor_caches: dict[int, _TTLCache] = {}
    # Keyed by league_key, warmed as a side effect of `_assemble_season` (the
    # season view) rather than fetched independently -- this is the
    # "needs_attention" signal for the `/api/leagues` switcher. It is
    # deliberately best-effort: a league whose season view has never been
    # opened has no entry here, and `list_leagues_endpoint` treats that as
    # `needs_attention: False` rather than fetching rosters itself just to
    # answer a switcher-row question. A 24h TTL matches `teams_caches` --
    # long enough that a page of switcher rows doesn't need every league's
    # season view re-opened every few minutes to stay accurate.
    roster_count_caches: dict[str, _TTLCache] = {}
    weekly_proj_caches: dict[tuple[int, int], _TTLCache] = {}
    # A short TTL, not the 900s the other weekly-scoped cache above uses:
    # `schedule_mod.week_games` fetches Sleeper's ENTIRE season schedule
    # (unofficial, undocumented) and filters to this week client-side, so
    # leaving it wholly uncached would hit that endpoint on every single
    # `/lineup` request/refresh. 60s still meaningfully throttles that,
    # while bounding worst-case lock-status staleness to about a minute --
    # short enough to matter at kickoff, the one moment this feature exists
    # to get right.
    schedule_caches: dict[tuple[int, int], _TTLCache] = {}
    player_history_caches: dict[int, _TTLCache] = {}
    actuals_caches: dict[tuple[str, int], _TTLCache] = {}
    waiver_claims_caches: dict[tuple[str, int], _TTLCache] = {}

    def _projections_cache_for(season: int) -> _TTLCache:
        return projections_caches.setdefault(season, _TTLCache(ttl_seconds=3600))

    def _teams_cache_for(league_id: str) -> _TTLCache:
        return teams_caches.setdefault(league_id, _TTLCache(ttl_seconds=24 * 3600))

    def _roster_count_cache_for(league_key: str) -> _TTLCache:
        return roster_count_caches.setdefault(league_key, _TTLCache(ttl_seconds=24 * 3600))

    def _weekly_proj_cache_for(season: int, week: int) -> _TTLCache:
        return weekly_proj_caches.setdefault((season, week), _TTLCache(ttl_seconds=900))

    def _schedule_cache_for(season: int, week: int) -> _TTLCache:
        return schedule_caches.setdefault((season, week), _TTLCache(ttl_seconds=60))

    def _actuals_cache_for(league_key: str, through_week: int) -> _TTLCache:
        return actuals_caches.setdefault((league_key, through_week), _TTLCache(ttl_seconds=3600))

    def _waiver_claims_cache_for(league_key: str, week: int) -> _TTLCache:
        return waiver_claims_caches.setdefault((league_key, week), _TTLCache(ttl_seconds=900))

    def _player_history_cache_for(season: int) -> _TTLCache:
        # A week, not the hour/day TTLs used elsewhere in this file --
        # finished-season stats never change, so this can cache far longer.
        return player_history_caches.setdefault(season, _TTLCache(ttl_seconds=7 * 24 * 3600))

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

    def _season_proj_anchor_for(season: int) -> _TTLCache:
        return season_proj_anchor_caches.setdefault(
            season, _TTLCache(ttl_seconds=24 * 3600))

    def _load_projection_anchor(sleeper: client_mod.SleeperClient, season: int):
        """The preseason projection anchor `ros_value.roster_value` blends
        season-to-date pace against.

        Sleeper overwrites (and for some players wipes) its projections feed
        after kickoff, which is exactly what `_load_projections`'
        `ContaminatedProjectionError` guard exists to refuse -- and the
        season view runs entirely post-kickoff, so that refusal fires on
        every single call once the season is underway. Refusing outright
        would mean no season view at all from week 1 onward, so this falls
        back to the post-kickoff feed with `allow_contaminated=True` and says
        so in the log. The fallback degrades gracefully rather than lying:
        `roster_value`'s pace blend shifts weight off the anchor as
        `weeks_played` grows (half of it is gone by week 4), so the further
        into the season the contaminated anchor is used, the less it counts.

        `ContaminatedProjectionError` must be caught BEFORE the caller's
        `RuntimeError -> 502` arm sees it -- it subclasses `RuntimeError`,
        and it is not a provider outage.
        """
        try:
            proj, _adp = _load_projections(sleeper, season)
            return proj
        except proj_mod.ContaminatedProjectionError:
            logging.getLogger("ffdo.api").warning(
                "season anchor: using post-kickoff projections for %s "
                "(no clean preseason snapshot)", season)
            raw = sleeper.get_json(
                f"{client_mod.PROJECTIONS}/{season}"
                "?season_type=regular&position[]=QB&position[]=RB"
                "&position[]=WR&position[]=TE&position[]=DEF&position[]=K")
            proj, _adp = proj_mod.parse(raw, season, allow_contaminated=True)
            return proj

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
            try:
                _projections_cache_for(season).get(lambda: _load_projections(sleeper, season))
            except proj_mod.ContaminatedProjectionError:
                # Expected once a season kicks off -- Sleeper's projections
                # endpoint stops being trustworthy the moment it's edited
                # post-kickoff (see projections.py's module docstring), and
                # every in-season league track hits this on every warm.
                # There's nothing to cache and nothing wrong to fix; the
                # request path re-derives the same refusal on demand and
                # falls back to ADP, so let this one signal go quiet instead
                # of logging an unhandled-exception traceback on every
                # background warm for the rest of the season.
                logging.getLogger("ffdo.api").info(
                    "season %s projections are post-kickoff; warm skipped, ADP will be used",
                    season)
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
        except (httpx.HTTPError, RuntimeError) as exc:
            # A provider outage is not the user's mistake and not this app's
            # bug: 502 says "the upstream failed", where a bare 500 would
            # point the user at their own input.
            #
            # `RuntimeError` is not defensive over-catching -- it is THE shape
            # a real outage takes. `ffdo.ingest.http.get_json_with_retry`
            # re-raises `RuntimeError(f"GET {url} failed after N attempts")`
            # once retries are exhausted, with the underlying httpx error only
            # as `__cause__`, so an httpx-only arm catches nothing on the exact
            # failure it was written for. Only a permanent status (404/401/...)
            # escapes as a real `httpx.HTTPStatusError` via `raise_for_status`,
            # which is why both are needed. Same pairing
            # `ffdo.ingest.espn.connect` already uses at its own call sites.
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
        except (httpx.HTTPError, RuntimeError) as exc:
            # See `_sleeper_discover` for why `RuntimeError` belongs here: an
            # exhausted retry loop in ffdo.ingest.http raises it, not an httpx
            # error.
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
        except (httpx.HTTPError, RuntimeError) as exc:
            # Same reasoning as `_sleeper_discover`'s 502 arm: an ESPN outage
            # is neither the user's mistake nor this app's bug, and a bare 500
            # would point them at their own input. `RuntimeError` for the same
            # exhausted-retry reason -- note espn_connect_mod.track already
            # maps RuntimeError to ConnectError for its *player-pool* fetch
            # only, so the league fetch's exhausted retries still land here.
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
                except (httpx.HTTPError, RuntimeError) as exc:
                    # Same arm `_sleeper_discover` already has, including the
                    # `RuntimeError` half: a provider outage is not the user's
                    # mistake, and a bare 500 would point them at their own
                    # input.
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
        switcher doesn't ship every league's scoring settings on page load.

        `needs_attention` is best-effort and cache-warmed: it reads whatever
        `_assemble_season` (the season view) last computed for this league's
        roster and defaults to `False` for a league whose season view has
        never been opened -- this endpoint never fetches rosters itself just
        to answer that question."""
        rows = []
        for lg in _STORE.list():
            cache = roster_count_caches.get(lg.league_key)
            attn = bool(cache.value["attn"]) if (cache and cache.has_value()) else False
            rows.append({
                "league_key": lg.league_key, "name": lg.name,
                "provider": lg.provider, "season": lg.season,
                "format": lg.fmt, "resolved_format": lg.resolved_format,
                "draft_status": lg.draft_status, "is_mock": lg.is_mock,
                "needs_attention": attn,
            })
        return rows

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
            except (httpx.HTTPError, RuntimeError) as exc:
                # `RuntimeError` for the same exhausted-retry reason as
                # `_sleeper_discover`'s arm.
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

                state = draft_mod.parse(draft_meta, picks_raw)
                if state.status == "complete":
                    # The season screen (src/ffdo/web/season/season.js) takes over
                    # once draft_status flips to "complete" -- board.js never
                    # renders this board data in that case, and Sleeper's
                    # projections feed is reliably contaminated post-kickoff
                    # (`_load_projections` raises on it), so loading it here just
                    # to throw the result away would 500 every real league whose
                    # draft happened before the app was reopened. See get_season
                    # for the actual season-view data.
                    _STORE.touch_status(league_key, state.status)
                    if not is_mock:
                        # This path never loads valuations, so any pick(s)
                        # first seen here (the final pick and a status flip
                        # to "complete" landing in the same poll) get
                        # recorded ungraded rather than lost entirely.
                        for pick in state.picks:
                            _DRAFT_PICK_LEDGER.record_if_absent(
                                league_key, draft_id=state.draft_id, pick_no=pick.pick_no,
                                round=pick.round, roster_id=pick.roster_id,
                                player_id=pick.player_id, position=None, amount=pick.amount,
                                grade=None, vor_at_pick=None, predicted_survival=None)
                    return {"draft_status": state.status, "is_mock": is_mock}

                profiles, _espn_id_index = players_cache.get(lambda: _load_players(sleeper))
                proj, adp_data = _projections_cache_for(lg.season).get(
                    lambda: _load_projections(sleeper, lg.season))
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
            _DRAFT_SURVIVAL_CACHE.setdefault(state.draft_id, {}).update(survival)
            cow = market.cost_of_waiting(valued, survival, available)
            plan = snake_plan_mod.simulate_snake_plan(valued, adp_means, state, lg, roster_id)
            board = board_mod.build_snake_board(
                lg, state, valued, survival, cow, plan, roster_id=roster_id, teams=teams)

        if not is_mock:
            from ffdo.engine import grading as grading_mod
            for pick in state.picks:
                if _DRAFT_PICK_LEDGER.get(league_key, state.draft_id, pick.pick_no) is not None:
                    continue
                vp = valued.get(pick.player_id)
                vor_at_pick = round(vp.vor, 2) if vp is not None else None
                position = profiles[pick.player_id].position if pick.player_id in profiles else None
                if state.draft_type == "auction":
                    grade = None
                    if pick.amount is not None and vp is not None:
                        base = baseline.get(pick.player_id, 1.0)
                        grade = grading_mod.grade_auction_pick(base, pick.amount)
                    predicted_survival = None
                else:
                    grade = None
                    if vp is not None:
                        drafted_before = {
                            p.player_id for p in state.picks if p.pick_no < pick.pick_no}
                        alternatives = [
                            other.vor for pid, other in valued.items()
                            if other.vor > 0 and pid != pick.player_id and pid not in drafted_before
                        ]
                        grade = grading_mod.grade_snake_pick(vp.vor, alternatives)
                    predicted_survival = _DRAFT_SURVIVAL_CACHE.get(state.draft_id, {}).get(
                        pick.player_id)
                _DRAFT_PICK_LEDGER.record_if_absent(
                    league_key, draft_id=state.draft_id, pick_no=pick.pick_no,
                    round=pick.round, roster_id=pick.roster_id, player_id=pick.player_id,
                    position=position, amount=pick.amount, grade=grade,
                    vor_at_pick=vor_at_pick, predicted_survival=predicted_survival)

        board["is_mock"] = is_mock
        return board

    def _season_weeks(season: int) -> int:
        """Regular-season length. The NFL moved 17 -> 18 games in 2024, so a
        pace projection must normalize against the right number or every
        season before 2024 reads ~6% high."""
        return SEASON_LENGTH.get(season, 18)

    def _through_week(nfl) -> int:
        """The last week whose actual points are final.

        `nfl.week` is the upcoming / in-progress week, so its points are
        partial at best -- counting them would make a pace projection read
        low mid-week and then jump on Tuesday. Preseason has no scored weeks
        at all, and once the regular season is over every week counts."""
        if nfl.complete:
            return _season_weeks(nfl.season)
        if nfl.season_type == "pre":
            return 0
        return max(0, nfl.week - 1)

    def _roster_callout(lg: TrackedLeague, you, valued, profiles) -> str | None:
        """One short, actionable line for the roster panel's header, or None.

        Order matters: an empty roster spot is a thing the user can act on
        today (there is a free agent to add), so it outranks "thin at RB",
        which is a structural observation about a roster that is already
        full. Only the first is returned -- a header with three warnings in
        it is a header nobody reads.
        """
        short = lg.roster_size - len(you.player_ids)
        if short > 0:
            return f"{short} empty roster spot{'s' if short != 1 else ''}"

        # "Startable" means positive VOR: a player valued at or below
        # replacement is, by definition, someone the waiver wire can match.
        startable: dict[str, int] = {}
        for pid in you.player_ids:
            prof = profiles.get(pid)
            if prof is None:
                continue
            vp = valued.get(pid)
            if vp is not None and vp.vor > 0:
                startable[prof.position] = startable.get(prof.position, 0) + 1

        starting_positions = set(lg.starting_slots)
        for pos in ("QB", "RB", "WR", "TE"):
            if pos in starting_positions and startable.get(pos, 0) == 1:
                return f"thin at {pos}"
        return None

    def _your_roster_payload(lg, you, valued, profiles, power_payload,
                             standings_rank) -> dict:
        byes = NFL_BYE_WEEKS.get(lg.season, {})
        # Slot labels come from the provider's own `starters` array rather
        # than being re-derived from the optimal-lineup fill: this panel
        # shows what the user actually has set, not what they should have
        # set. (The "should" view is sub-project #3's weekly lineup screen.)
        starters = set(you.starter_ids)
        players = []
        for pid in you.player_ids:
            prof = profiles.get(pid)
            if prof is None:
                # Same silent omission `ros_value.roster_value` already
                # applies: a rostered id with no Sleeper profile (a stale
                # crosswalk miss, an offseason-only id) has no name,
                # position or value to show, and inventing zeros for it
                # would put a phantom row on the user's roster.
                continue
            vp = valued.get(pid)
            players.append({
                "player_id": pid, "name": prof.full_name, "position": prof.position,
                "team": prof.team, "slot": prof.position if pid in starters else "BN",
                "starter": pid in starters,
                "value": round(vp.vor, 1) if vp else 0.0,
                "age": prof.age, "bye_week": byes.get(prof.team or ""),
                "injury_status": prof.injury_status,
            })
        players.sort(key=lambda p: (not p["starter"], -p["value"]))

        def _you_in(rows):
            return next((r for r in rows if r["roster_id"] == lg.roster_id), None)

        overall_you = _you_in(power_payload["overall"]["starters"])
        bench_full = _you_in(power_payload["overall"]["full"])
        pos_rank = {}
        for pos in ("QB", "RB", "WR", "TE"):
            hit = _you_in(power_payload["by_position"][pos]["starters"])
            pos_rank[pos] = hit["power_rank"] if hit else None

        return {
            "roster_id": you.roster_id, "team_name": you.team_name,
            "wins": you.wins, "losses": you.losses, "ties": you.ties,
            "points_for": round(you.points_for, 1),
            "points_against": round(you.points_against, 1),
            "power_rank": overall_you["power_rank"] if overall_you else None,
            "standings_rank": standings_rank.get(you.roster_id),
            "positional_rank": pos_rank,
            "bench_value": bench_full["bench_value"] if bench_full else 0.0,
            "callout": _roster_callout(lg, you, valued, profiles),
            "players": players,
        }

    def _draft_capital_payload(capital, rosters, your_roster_id):
        """Teams ranked by how much future draft capital they hold, each with
        its picks grouped by draft year for the UI's per-year chip rows.

        Ranked by pick COUNT first, then by the best (lowest) projected slot
        any of those picks carries, because a pick's only quality signal here
        is where it projects to land -- there is no pick-value model in this
        sub-project (that is #5's trade work). `None` in, `None` out: redraft
        leagues have no future picks to speak of and the panel is hidden
        entirely rather than shown empty.
        """
        if capital is None:
            return None
        names = {r.roster_id: r.team_name for r in rosters}
        by_owner: dict[int, list] = {}
        for asset in capital:
            by_owner.setdefault(asset.current_owner_roster_id, []).append(asset)
        ranked = sorted(
            by_owner.items(),
            key=lambda kv: (-len(kv[1]),
                            min((a.projected_slot or 99) for a in kv[1]),
                            kv[0]))
        out = []
        for i, (owner_id, assets) in enumerate(ranked):
            picks: dict[str, list] = {}
            for a in sorted(assets, key=lambda a: (a.season, a.round,
                                                   a.projected_slot or 99)):
                picks.setdefault(str(a.season), []).append({
                    "label": a.label, "round": a.round,
                    "projected_slot": a.projected_slot,
                    "via_team_name": a.via_team_name,
                })
            out.append({
                "power_rank": i + 1, "roster_id": owner_id,
                "team_name": names.get(owner_id, f"Team {owner_id}"),
                "is_you": owner_id == your_roster_id, "picks": picks,
            })
        return out

    def _assemble_season(lg, nfl, through_week, rosters, profiles, proj_anchor,
                         actuals, standings_rank, capital, history) -> dict:
        """The one payload builder both providers end in. Everything above
        this point is provider-specific fetching; everything from here down
        is the same code for Sleeper and ESPN, because both branches have
        already normalized to `RosterEntry` + Sleeper player ids."""
        all_pids = {pid for r in rosters for pid in r.player_ids}
        # THE FROZEN SEAM (see engine/ros_value.py): sub-project #4 swaps the
        # body of `roster_value` for a real multi-year model and keeps this
        # exact call shape. Nothing here may reach inside it.
        valued = ros_value_mod.roster_value(
            all_pids, lg,
            resolved_format=lg.resolved_format,
            season_proj=proj_anchor,
            profiles=profiles,
            actuals=actuals,
            weeks_played=through_week,
            season_weeks=_season_weeks(lg.season),
            history=history,
            age_curve=DYNASTY_AGE_CURVE if history is not None else None)

        # Warms the `needs_attention` signal the `/api/leagues` switcher
        # reads -- best-effort and side-effect-only: this endpoint's own
        # response is untouched by it. Only meaningful when the caller has a
        # roster of their own in this league (`you_entry` is None for a
        # league the user merely observes), matching `your_roster` below.
        you_entry = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        if you_entry is not None:
            unfilled = (sum(1 for s in lg.roster_positions if s not in NON_STARTING_SLOTS)
                       > len(you_entry.starter_ids))
            short = len(you_entry.player_ids) < lg.roster_size
            _roster_count_cache_for(lg.league_key).get(lambda: {"attn": bool(unfilled or short)})

        def _rank(position: str, scope: str) -> list[dict]:
            rows = power_ranking_mod.rank(
                rosters, valued, lg, standings_rank, lg.roster_id,
                position=position, scope=scope)
            return [{
                "roster_id": row.roster_id, "team_name": row.team_name,
                "is_you": row.is_you, "value": row.value,
                "bench_value": row.bench_value, "power_rank": row.power_rank,
                "standings_rank": row.standings_rank, "delta": row.delta,
            } for row in rows]

        power_ranking_payload = {
            "overall": {"starters": _rank("OVR", "starters"),
                        "full": _rank("OVR", "full")},
            "by_position": {
                pos: {"starters": _rank(pos, "starters"),
                      "full": _rank(pos, "full")}
                for pos in ("QB", "RB", "WR", "TE")
            },
        }

        # `roster_id` can legitimately be None (a league the user only
        # observes, or one whose roster slot never resolved), and a roster
        # that does not exist in the feed is not an error -- the panel is
        # simply absent, the way the draft board already tolerates having no
        # seat of its own.
        you = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        your_roster = (_your_roster_payload(lg, you, valued, profiles,
                                            power_ranking_payload, standings_rank)
                       if you else None)

        return {
            "nfl_week": {"season": nfl.season, "week": nfl.week,
                         "season_type": nfl.season_type, "complete": nfl.complete,
                         "values_through_week": through_week},
            "your_roster": your_roster,
            "power_ranking": power_ranking_payload,
            "standings": [
                {"roster_id": r.roster_id, "team_name": r.team_name,
                 "wins": r.wins, "losses": r.losses, "ties": r.ties,
                 "points_for": round(r.points_for, 1),
                 "points_against": round(r.points_against, 1)}
                for r in sorted(rosters, key=lambda r: standings_rank[r.roster_id])
            ],
            "draft_capital": _draft_capital_payload(capital, rosters, lg.roster_id),
        }

    def _season_espn(lg: TrackedLeague) -> dict:
        """ESPN's season view. The valuation inputs (player profiles, the
        projection anchor) come from Sleeper regardless of provider -- ESPN
        has no equivalent feed this project trusts -- so this branch talks to
        both APIs and crosswalks ESPN's roster ids onto Sleeper's before
        handing the result to the shared assembler."""
        cred = _require_espn_credential("the season view")

        sleeper = client_mod.SleeperClient()
        try:
            profiles, espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            proj_anchor = _season_proj_anchor_for(lg.season).get(
                lambda: _load_projection_anchor(sleeper, lg.season))
            # Fetched here, while `sleeper` is still open -- ESPN's own
            # rosters (and thus the player ids `history_for` needs to filter
            # to) aren't known until later in this function, but the raw
            # per-season stat lines are cacheable and player-agnostic, so
            # there's no reason to defer the fetch itself, only the filter.
            season_stats = None
            if lg.resolved_format == "dynasty":
                season_stats = {
                    season: _player_history_cache_for(season).get(
                        lambda season=season: player_history_mod.fetch_season(sleeper, season))
                    for season in range(2021, lg.season)
                }
        except (httpx.HTTPError, RuntimeError) as exc:
            # `RuntimeError` for the same exhausted-retry reason as
            # `_sleeper_discover`'s arm -- ffdo.ingest.http raises a plain
            # RuntimeError once retries run out, never an httpx error.
            raise HTTPException(
                status_code=502, detail="Couldn't reach Sleeper, try again") from exc
        finally:
            sleeper.close()

        espn = espn_client_mod.EspnClient(cred.espn_s2, cred.swid)
        try:
            player_pool_raw = _espn_player_pool_cache_for(lg.season).get(
                lambda: espn.get_json(
                    f"{espn_client_mod.BASE}/seasons/{lg.season}/players"
                    "?view=kona_player_info",
                    extra_headers=espn_client_mod.PLAYER_POOL_FILTER_HEADER))
            cw = _espn_crosswalk_cache_for(lg.season).get(
                lambda: espn_crosswalk_mod.build(
                    espn_id_index, profiles,
                    espn_crosswalk_mod.parse_player_pool(player_pool_raw)))
            rosters, nfl, mroster_raw = espn_rosters_mod.fetch(
                espn, lg.provider_league_id, lg.season, cw)
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(
                status_code=502, detail="Couldn't reach ESPN, try again") from exc
        finally:
            espn.close()

        through_week = _through_week(nfl)
        # Pure parse of the mRoster payload already in hand -- no second
        # round trip, unlike Sleeper's week-by-week matchups walk.
        actuals = espn_actuals_mod.points_so_far(mroster_raw, cw, lg.season, through_week)
        # ESPN exposes no traded-pick feed this project reads, so the draft
        # capital panel is absent for ESPN leagues of every format -- an
        # honest gap rather than an implicit-ownership table that would be
        # silently wrong the moment anyone traded a pick.
        # `history_for` is a pure reshaping step, so it's computed here, now
        # that ESPN's own roster player-ids are finally known, rather than
        # up where `season_stats` was fetched (`sleeper` is closed by now).
        history = None
        if season_stats is not None:
            all_pids_for_history = {pid for r in rosters for pid in r.player_ids}
            history = player_history_mod.history_for(all_pids_for_history, season_stats)
        return _assemble_season(lg, nfl, through_week, rosters, profiles,
                                proj_anchor, actuals, _standings_rank(rosters), None, history)

    @app.get("/api/leagues/{league_key}/season")
    def get_season(league_key: str) -> dict:
        """The post-draft season view: your roster, the league power ranking,
        standings, and (dynasty/keeper only) draft capital.

        Deliberately returns a full 200 payload even when
        `draft_status != "complete"`. The frontend decides board-vs-season
        routing from the live draft status it is already polling, and a
        league mid-draft still has real rosters and standings to show.
        """
        lg = _load_league(league_key)

        if lg.provider == "espn":
            return _season_espn(lg)

        sleeper = client_mod.SleeperClient()
        try:
            nfl = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))
            profiles, _espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            proj_anchor = _season_proj_anchor_for(lg.season).get(
                lambda: _load_projection_anchor(sleeper, lg.season))
            # The live roster feed, NOT the draft board's pick-derived
            # rosters: post-draft, adds/drops/trades have moved players.
            rosters = rosters_mod.fetch(sleeper, lg.provider_league_id)
            through_week = _through_week(nfl)
            actuals = actuals_mod.points_so_far(
                sleeper, lg.provider_league_id, through_week)

            capital = None
            if lg.resolved_format in ("dynasty", "keeper"):
                # Reverse standings order = draft order: worst record picks
                # first, which is what gives an untraded pick its projected
                # slot.
                worst_to_best = sorted(rosters, key=lambda r: (r.wins, r.points_for))
                try:
                    capital = traded_picks_mod.capital(
                        sleeper, lg.provider_league_id,
                        num_teams=lg.num_teams,
                        rounds=int((lg.raw_settings or {}).get("draft_rounds") or 4),
                        standings_order=[r.roster_id for r in worst_to_best],
                        draft_years=(lg.season + 1, lg.season + 2),
                        team_names={r.roster_id: r.team_name for r in rosters})
                except (httpx.HTTPError, RuntimeError):
                    # The traded-picks feed failing shouldn't take down the
                    # whole season view -- draft_capital just goes absent,
                    # exactly like a redraft league (_draft_capital_payload
                    # already treats `capital is None` as "omit the panel").
                    logging.getLogger("ffdo.api").warning(
                        "season: traded-picks fetch failed for %s, "
                        "draft_capital omitted", lg.league_key)

            history = None
            if lg.resolved_format == "dynasty":
                season_stats = {
                    season: _player_history_cache_for(season).get(
                        lambda season=season: player_history_mod.fetch_season(sleeper, season))
                    for season in range(2021, lg.season)
                }
                all_pids_for_history = {pid for r in rosters for pid in r.player_ids}
                history = player_history_mod.history_for(all_pids_for_history, season_stats)
        except (httpx.HTTPError, RuntimeError) as exc:
            # Both halves are load-bearing: `ffdo.ingest.http.
            # get_json_with_retry` raises a plain `RuntimeError` once retries
            # are exhausted (the common outage shape), while a permanent
            # status escapes as `httpx.HTTPStatusError`. See
            # `_sleeper_discover` for the full reasoning.
            raise HTTPException(
                status_code=502, detail="Couldn't reach Sleeper, try again") from exc
        finally:
            sleeper.close()

        return _assemble_season(lg, nfl, through_week, rosters, profiles,
                                proj_anchor, actuals, _standings_rank(rosters),
                                capital, history)

    def _lineup_espn(lg: TrackedLeague) -> dict:
        """ESPN's weekly-lineup view. Roster identity (who's on the team,
        who's currently starting) comes from ESPN's own feed, crosswalked
        to Sleeper ids -- but weekly projections and the schedule/lock
        feed are Sleeper-sourced regardless of provider, same posture as
        #2's valuation inputs (no ESPN equivalent exists). ESPN doesn't
        hand back a pre-ordered starters array the way Sleeper does, so
        `espn_rosters_mod.slot_aligned_starters` rebuilds that alignment
        from each roster entry's own `lineupSlotId`."""
        cred = _require_espn_credential("the weekly lineup")

        sleeper = client_mod.SleeperClient()
        try:
            nfl = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))
            profiles, espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            weekly_proj = _weekly_proj_cache_for(nfl.season, nfl.week).get(
                lambda: weekly_projections_mod.fetch(sleeper, nfl.season, nfl.week))
            try:
                games = _schedule_cache_for(nfl.season, nfl.week).get(
                    lambda: schedule_mod.week_games(sleeper, nfl.season, nfl.week))
            except (httpx.HTTPError, RuntimeError) as exc:
                logging.getLogger("ffdo.api").warning(
                    "lineup: schedule fetch failed for %s week %s (%s) -- "
                    "treating nothing as locked", lg.league_key, nfl.week, exc)
                games = []
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(
                status_code=502, detail="Couldn't reach Sleeper, try again") from exc
        finally:
            sleeper.close()

        if lg.roster_id is None:
            return {
                "nfl_week": {"season": nfl.season, "week": nfl.week},
                "week_locked": False, "swaps_suggested": 0, "diff": [],
                "ledger": None,
            }

        espn = espn_client_mod.EspnClient(cred.espn_s2, cred.swid)
        try:
            player_pool_raw = _espn_player_pool_cache_for(lg.season).get(
                lambda: espn.get_json(
                    f"{espn_client_mod.BASE}/seasons/{lg.season}/players"
                    "?view=kona_player_info",
                    extra_headers=espn_client_mod.PLAYER_POOL_FILTER_HEADER))
            cw = _espn_crosswalk_cache_for(lg.season).get(
                lambda: espn_crosswalk_mod.build(
                    espn_id_index, profiles,
                    espn_crosswalk_mod.parse_player_pool(player_pool_raw)))
            rosters, _espn_week, mroster_raw = espn_rosters_mod.fetch(
                espn, lg.provider_league_id, lg.season, cw)
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(
                status_code=502, detail="Couldn't reach ESPN, try again") from exc
        finally:
            espn.close()

        current_starters = espn_rosters_mod.slot_aligned_starters(
            mroster_raw, cw, lg.roster_id, lg.starting_slots)

        all_teams = frozenset(p.team for p in profiles.values() if p.team)
        bye_teams = schedule_mod.bye_teams(games, all_teams) if games else frozenset()
        locked_teams = schedule_mod.locked_teams(games)
        locked_now = schedule_mod.week_locked(games)

        all_pids = {pid for r in rosters for pid in r.player_ids}
        valued = weekly_lineup_mod.weekly_value(
            all_pids, lg, weekly_points=weekly_proj, profiles=profiles,
            bye_teams=bye_teams)
        you_roster = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        your_valued = ({pid: valued[pid] for pid in you_roster.player_ids if pid in valued}
                      if you_roster is not None else {})
        optimal = weekly_lineup_mod.optimal_slots(your_valued, lg)
        diff_rows = weekly_lineup_mod.diff(
            current_starters, optimal, locked_teams, valued, profiles, lg)

        record = _LINEUP_LEDGER.record_if_absent(
            lg.league_key, nfl.season, nfl.week, optimal, current_starters)
        if record.followed is None and locked_now:
            _LINEUP_LEDGER.resolve(lg.league_key, nfl.season, nfl.week, current_starters)
            record = _LINEUP_LEDGER.get(lg.league_key, nfl.season, nfl.week)

        def _player_json(pid: str | None) -> dict | None:
            if pid is None:
                return None
            prof = profiles.get(pid)
            vp = valued.get(pid)
            return {
                "player_id": pid,
                "name": prof.full_name if prof else pid,
                "team": prof.team if prof else None,
                "value": round(vp.vor, 1) if vp is not None else 0.0,
            }

        return {
            "nfl_week": {"season": nfl.season, "week": nfl.week},
            "week_locked": locked_now,
            "swaps_suggested": sum(1 for d in diff_rows if d.status == "suggested_swap"),
            "diff": [
                {"slot_index": d.slot_index, "slot_label": d.slot_label,
                 "status": d.status,
                 "current": _player_json(d.current_player_id),
                 "optimal": _player_json(d.optimal_player_id),
                 "delta": d.delta}
                for d in diff_rows
            ],
            "ledger": {"recorded_at": record.recorded_at, "followed": record.followed},
        }

    @app.get("/api/leagues/{league_key}/lineup")
    def get_lineup(league_key: str) -> dict:
        """This week's optimal-lineup recommendation vs. your actual
        current starters, lock-aware. Weekly projections and the
        schedule/lock feed are Sleeper-sourced regardless of provider (no
        ESPN equivalent exists) -- `_lineup_espn` still builds a real
        recommendation for ESPN leagues by crosswalking ESPN's own
        roster/starters onto those Sleeper-sourced values.
        """
        lg = _load_league(league_key)
        if lg.provider == "espn":
            return _lineup_espn(lg)

        sleeper = client_mod.SleeperClient()
        try:
            nfl = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))

            if lg.roster_id is None:
                # A league the user only observes -- nothing personal to
                # recommend, same posture as #2's `your_roster: null`.
                return {
                    "nfl_week": {"season": nfl.season, "week": nfl.week},
                    "week_locked": False, "swaps_suggested": 0, "diff": [],
                    "ledger": None,
                }

            profiles, _espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            rosters = rosters_mod.fetch(sleeper, lg.provider_league_id)
            current_starters = rosters_mod.raw_starters(
                sleeper, lg.provider_league_id, lg.roster_id)
            weekly_proj = _weekly_proj_cache_for(nfl.season, nfl.week).get(
                lambda: weekly_projections_mod.fetch(sleeper, nfl.season, nfl.week))

            try:
                games = _schedule_cache_for(nfl.season, nfl.week).get(
                    lambda: schedule_mod.week_games(sleeper, nfl.season, nfl.week))
            except (httpx.HTTPError, RuntimeError) as exc:
                # Unofficial, undocumented endpoint -- degrade to "nothing
                # is locked" rather than fail the whole request.
                logging.getLogger("ffdo.api").warning(
                    "lineup: schedule fetch failed for %s week %s (%s) -- "
                    "treating nothing as locked", lg.league_key, nfl.week, exc)
                games = []
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(
                status_code=502, detail="Couldn't reach Sleeper, try again") from exc
        finally:
            sleeper.close()

        all_teams = frozenset(p.team for p in profiles.values() if p.team)
        bye_teams = schedule_mod.bye_teams(games, all_teams) if games else frozenset()
        locked_teams = schedule_mod.locked_teams(games)
        locked_now = schedule_mod.week_locked(games)

        all_pids = {pid for r in rosters for pid in r.player_ids}
        valued = weekly_lineup_mod.weekly_value(
            all_pids, lg, weekly_points=weekly_proj, profiles=profiles,
            bye_teams=bye_teams)
        # `valued` spans the WHOLE league (needed for `weekly_value`'s
        # VOR/replacement-level baseline, same as power_ranking.py's
        # league-wide pool) -- but the lineup solve itself must only pick
        # from the tracked user's own roster, or it can recommend starting
        # another team's player. Same scoping `power_ranking.team_value`
        # already does for the season view's per-team lineup solve.
        you_roster = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        your_valued = ({pid: valued[pid] for pid in you_roster.player_ids if pid in valued}
                      if you_roster is not None else {})
        optimal = weekly_lineup_mod.optimal_slots(your_valued, lg)
        diff_rows = weekly_lineup_mod.diff(
            current_starters, optimal, locked_teams, valued, profiles, lg)

        record = _LINEUP_LEDGER.record_if_absent(
            lg.league_key, nfl.season, nfl.week, optimal, current_starters)
        if record.followed is None and locked_now:
            _LINEUP_LEDGER.resolve(lg.league_key, nfl.season, nfl.week, current_starters)
            record = _LINEUP_LEDGER.get(lg.league_key, nfl.season, nfl.week)

        def _player_json(pid: str | None) -> dict | None:
            if pid is None:
                return None
            prof = profiles.get(pid)
            vp = valued.get(pid)
            return {
                "player_id": pid,
                "name": prof.full_name if prof else pid,
                "team": prof.team if prof else None,
                "value": round(vp.vor, 1) if vp is not None else 0.0,
            }

        return {
            "nfl_week": {"season": nfl.season, "week": nfl.week},
            "week_locked": locked_now,
            "swaps_suggested": sum(1 for d in diff_rows if d.status == "suggested_swap"),
            "diff": [
                {"slot_index": d.slot_index, "slot_label": d.slot_label,
                 "status": d.status,
                 "current": _player_json(d.current_player_id),
                 "optimal": _player_json(d.optimal_player_id),
                 "delta": d.delta}
                for d in diff_rows
            ],
            "ledger": {"recorded_at": record.recorded_at, "followed": record.followed},
        }

    def _espn_roster_context(lg: TrackedLeague):
        """Rosters + valuation for an ESPN league, crosswalked to Sleeper
        player ids -- the same fetch `_lineup_espn`/`_waivers_espn` each
        already repeat independently, matching this file's established
        no-shared-fetch-across-endpoints convention. No dynasty age-curve
        history (matches `_load_trade_targets_context`'s precedent for
        this weight of computation)."""
        cred = _require_espn_credential("trades")

        sleeper = client_mod.SleeperClient()
        try:
            profiles, espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            proj_anchor = _season_proj_anchor_for(lg.season).get(
                lambda: _load_projection_anchor(sleeper, lg.season))
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(
                status_code=502, detail="Couldn't reach Sleeper, try again") from exc
        finally:
            sleeper.close()

        espn = espn_client_mod.EspnClient(cred.espn_s2, cred.swid)
        try:
            player_pool_raw = _espn_player_pool_cache_for(lg.season).get(
                lambda: espn.get_json(
                    f"{espn_client_mod.BASE}/seasons/{lg.season}/players"
                    "?view=kona_player_info",
                    extra_headers=espn_client_mod.PLAYER_POOL_FILTER_HEADER))
            cw = _espn_crosswalk_cache_for(lg.season).get(
                lambda: espn_crosswalk_mod.build(
                    espn_id_index, profiles,
                    espn_crosswalk_mod.parse_player_pool(player_pool_raw)))
            rosters, nfl, mroster_raw = espn_rosters_mod.fetch(
                espn, lg.provider_league_id, lg.season, cw)
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(
                status_code=502, detail="Couldn't reach ESPN, try again") from exc
        finally:
            espn.close()

        through_week = _through_week(nfl)
        actuals = espn_actuals_mod.points_so_far(mroster_raw, cw, lg.season, through_week)
        all_pids = {pid for r in rosters for pid in r.player_ids}
        valued = ros_value_mod.roster_value(
            all_pids, lg, resolved_format=lg.resolved_format, season_proj=proj_anchor,
            profiles=profiles, actuals=actuals, weeks_played=through_week,
            season_weeks=_season_weeks(lg.season))
        return rosters, valued, profiles

    def _evaluate_trade_espn(lg: TrackedLeague, payload: dict) -> dict:
        """ESPN's hypothetical trade evaluator: the comparison itself is
        pure current-roster VOR, no trade history needed, so it doesn't
        share the trade-ledger/trade-targets blockers (no real ESPN TRADE
        transaction shape has been seen -- see
        ingest.espn.transactions's module docstring). ESPN has no
        traded-picks feed, so `side_a`/`side_b` picks are simply not
        offered by the ESPN trade-builder UI in the first place -- a
        payload carrying pick ids anyway would just price them at 0,
        same as an unknown pick would for Sleeper."""
        rosters, valued, _profiles = _espn_roster_context(lg)

        side_a = payload.get("side_a") or {}
        side_b = payload.get("side_b") or {}

        def _picks_from_payload(raw_picks: list) -> list:
            return [DraftPickAsset(
                season=int(p["season"]), round=int(p["round"]),
                projected_slot=p.get("projected_slot"),
                current_owner_roster_id=int(p.get("current_owner_roster_id", 0)),
                original_roster_id=int(p.get("original_roster_id", 0)),
                via_team_name=None) for p in raw_picks]

        result = trade_value_mod.evaluate_trade(
            {"player_ids": side_a.get("player_ids", []),
             "picks": _picks_from_payload(side_a.get("picks", []))},
            {"player_ids": side_b.get("player_ids", []),
             "picks": _picks_from_payload(side_b.get("picks", []))},
            valued_players=valued, pick_curve=PICK_VALUE_CURVE,
            current_season=lg.season, round_size=lg.num_teams)

        response = {
            "side_a_value": round(result.side_a_value, 1),
            "side_b_value": round(result.side_b_value, 1),
            "differential": round(result.differential, 1),
            "differential_pct": (round(result.differential_pct, 3)
                                 if result.differential_pct is not None else None),
        }

        you_roster = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        partner_roster = next(
            (r for r in rosters if r.roster_id == payload.get("partner_roster_id")), None)
        if you_roster is not None and partner_roster is not None:
            side_a_ids = set(side_a.get("player_ids", []))
            side_b_ids = set(side_b.get("player_ids", []))
            you_after = replace(you_roster, player_ids=tuple(
                (set(you_roster.player_ids) - side_a_ids) | side_b_ids))
            partner_after = replace(partner_roster, player_ids=tuple(
                (set(partner_roster.player_ids) - side_b_ids) | side_a_ids))

            def _needs_json(entry) -> dict:
                return {pos: {"rank": n.rank, "severity": n.severity}
                       for pos, n in roster_needs_mod.position_needs(
                           entry, rosters, valued, lg).items()}

            response["needs_before"] = {
                "you": _needs_json(you_roster), "partner": _needs_json(partner_roster)}
            response["needs_after"] = {
                "you": _needs_json(you_after), "partner": _needs_json(partner_after)}

        return response

    @app.post("/api/leagues/{league_key}/trade/evaluate")
    def evaluate_trade_endpoint(league_key: str, payload: dict) -> dict:
        """Hypothetical trade evaluator. side_a is always the tracked
        user's own roster (spec §7) -- partner_roster_id names the other
        team side_b's players/picks are assumed to belong to."""
        lg = _load_league(league_key)
        if lg.provider == "espn":
            return _evaluate_trade_espn(lg, payload)

        sleeper = client_mod.SleeperClient()
        try:
            nfl = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))
            profiles, _espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            proj_anchor = _season_proj_anchor_for(lg.season).get(
                lambda: _load_projection_anchor(sleeper, lg.season))
            rosters = rosters_mod.fetch(sleeper, lg.provider_league_id)
            through_week = _through_week(nfl)
            actuals = actuals_mod.points_so_far(sleeper, lg.provider_league_id, through_week)
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail="Couldn't reach Sleeper, try again") from exc
        finally:
            sleeper.close()

        all_pids = {pid for r in rosters for pid in r.player_ids}
        valued = ros_value_mod.roster_value(
            all_pids, lg, resolved_format=lg.resolved_format, season_proj=proj_anchor,
            profiles=profiles, actuals=actuals, weeks_played=through_week,
            season_weeks=_season_weeks(lg.season))

        side_a = payload.get("side_a") or {}
        side_b = payload.get("side_b") or {}

        def _picks_from_payload(raw_picks: list) -> list:
            return [DraftPickAsset(
                season=int(p["season"]), round=int(p["round"]),
                projected_slot=p.get("projected_slot"),
                current_owner_roster_id=int(p.get("current_owner_roster_id", 0)),
                original_roster_id=int(p.get("original_roster_id", 0)),
                via_team_name=None) for p in raw_picks]

        result = trade_value_mod.evaluate_trade(
            {"player_ids": side_a.get("player_ids", []),
             "picks": _picks_from_payload(side_a.get("picks", []))},
            {"player_ids": side_b.get("player_ids", []),
             "picks": _picks_from_payload(side_b.get("picks", []))},
            valued_players=valued, pick_curve=PICK_VALUE_CURVE,
            current_season=lg.season, round_size=lg.num_teams)

        response = {
            "side_a_value": round(result.side_a_value, 1),
            "side_b_value": round(result.side_b_value, 1),
            "differential": round(result.differential, 1),
            "differential_pct": (round(result.differential_pct, 3)
                                 if result.differential_pct is not None else None),
        }

        # Roster-needs depth-impact preview: only computable when both a
        # real partner_roster_id and both rosters are known -- degrades to
        # simply omitting these fields rather than 400ing, since the value
        # comparison above is still useful on its own (e.g. mid-selection,
        # before a partner is fully resolved).
        you_roster = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        partner_roster = next(
            (r for r in rosters if r.roster_id == payload.get("partner_roster_id")), None)
        if you_roster is not None and partner_roster is not None:
            side_a_ids = set(side_a.get("player_ids", []))
            side_b_ids = set(side_b.get("player_ids", []))
            you_after = replace(you_roster, player_ids=tuple(
                (set(you_roster.player_ids) - side_a_ids) | side_b_ids))
            partner_after = replace(partner_roster, player_ids=tuple(
                (set(partner_roster.player_ids) - side_b_ids) | side_a_ids))

            def _needs_json(entry) -> dict:
                return {pos: {"rank": n.rank, "severity": n.severity}
                       for pos, n in roster_needs_mod.position_needs(
                           entry, rosters, valued, lg).items()}

            response["needs_before"] = {
                "you": _needs_json(you_roster), "partner": _needs_json(partner_roster)}
            response["needs_after"] = {
                "you": _needs_json(you_after), "partner": _needs_json(partner_after)}

        return response

    def _trade_builder_espn(lg: TrackedLeague) -> dict:
        """ESPN's trade-builder team list: players only, every team's
        `picks` is always empty -- ESPN has no traded-pick feed this
        project reads (same honest gap the season view's draft-capital
        panel already discloses), so a dynasty/keeper ESPN league simply
        can't trade future picks through this UI yet, rather than
        showing picks that might be silently wrong the moment anyone
        traded one."""
        rosters, valued, profiles = _espn_roster_context(lg)

        def _player_row(pid: str) -> dict | None:
            prof = profiles.get(pid)
            if prof is None:
                return None
            vp = valued.get(pid)
            return {"player_id": pid, "name": prof.full_name, "position": prof.position,
                    "value": round(vp.vor, 1) if vp is not None else 0.0}

        teams = []
        for r in rosters:
            players = []
            for pid in r.player_ids:
                row = _player_row(pid)
                if row is not None:
                    players.append(row)
            players.sort(key=lambda p: -p["value"])
            teams.append({
                "roster_id": r.roster_id, "team_name": r.team_name,
                "is_you": r.roster_id == lg.roster_id,
                "players": players, "picks": [],
            })
        teams.sort(key=lambda t: (not t["is_you"], t["team_name"]))

        return {"teams": teams}

    @app.get("/api/leagues/{league_key}/trade-builder")
    def get_trade_builder(league_key: str) -> dict:
        """Every team's roster (players + future picks, for the
        hypothetical trade-builder UI to pick from). Dynasty/keeper leagues
        get real future-pick data -- the same synthesis get_season's draft
        capital panel already uses (every roster implicitly owns its own
        pick in every round/year unless traded_picks says otherwise);
        redraft leagues get an empty `picks` list per team, since there's
        nothing future to trade away. ESPN leagues of every format get an
        empty `picks` list too, via `_trade_builder_espn` -- no ESPN
        traded-picks feed exists."""
        lg = _load_league(league_key)
        if lg.provider == "espn":
            return _trade_builder_espn(lg)

        capital = None
        sleeper = client_mod.SleeperClient()
        try:
            nfl = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))
            profiles, _espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            proj_anchor = _season_proj_anchor_for(lg.season).get(
                lambda: _load_projection_anchor(sleeper, lg.season))
            rosters = rosters_mod.fetch(sleeper, lg.provider_league_id)
            through_week = _through_week(nfl)
            actuals = actuals_mod.points_so_far(sleeper, lg.provider_league_id, through_week)

            if lg.resolved_format in ("dynasty", "keeper"):
                worst_to_best = sorted(rosters, key=lambda r: (r.wins, r.points_for))
                try:
                    capital = traded_picks_mod.capital(
                        sleeper, lg.provider_league_id,
                        num_teams=lg.num_teams,
                        rounds=int((lg.raw_settings or {}).get("draft_rounds") or 4),
                        standings_order=[r.roster_id for r in worst_to_best],
                        draft_years=(lg.season + 1, lg.season + 2),
                        team_names={r.roster_id: r.team_name for r in rosters})
                except (httpx.HTTPError, RuntimeError):
                    # Same posture as get_season: a traded-picks outage
                    # degrades to no picks rather than failing the whole
                    # request -- players are still tradeable either way.
                    logging.getLogger("ffdo.api").warning(
                        "trade-builder: traded-picks fetch failed for %s, picks omitted",
                        lg.league_key)
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail="Couldn't reach Sleeper, try again") from exc
        finally:
            sleeper.close()

        all_pids = {pid for r in rosters for pid in r.player_ids}
        valued = ros_value_mod.roster_value(
            all_pids, lg, resolved_format=lg.resolved_format, season_proj=proj_anchor,
            profiles=profiles, actuals=actuals, weeks_played=through_week,
            season_weeks=_season_weeks(lg.season))

        picks_by_owner: dict[int, list] = {}
        if capital:
            for asset in capital:
                picks_by_owner.setdefault(asset.current_owner_roster_id, []).append(asset)

        def _player_row(pid: str) -> dict | None:
            prof = profiles.get(pid)
            if prof is None:
                return None
            vp = valued.get(pid)
            return {"player_id": pid, "name": prof.full_name, "position": prof.position,
                    "value": round(vp.vor, 1) if vp is not None else 0.0}

        def _pick_row(asset) -> dict:
            return {
                "label": asset.label,
                "value": round(pick_value_mod.slot_value(
                    asset, PICK_VALUE_CURVE, current_season=lg.season,
                    round_size=lg.num_teams), 1),
                "season": asset.season, "round": asset.round,
                "projected_slot": asset.projected_slot,
                "current_owner_roster_id": asset.current_owner_roster_id,
                "original_roster_id": asset.original_roster_id,
            }

        teams = []
        for r in rosters:
            players = []
            for pid in r.player_ids:
                row = _player_row(pid)
                if row is not None:
                    players.append(row)
            players.sort(key=lambda p: -p["value"])
            picks = sorted(
                (_pick_row(a) for a in picks_by_owner.get(r.roster_id, [])),
                key=lambda p: (p["season"], p["round"]))
            teams.append({
                "roster_id": r.roster_id, "team_name": r.team_name,
                "is_you": r.roster_id == lg.roster_id,
                "players": players, "picks": picks,
            })
        teams.sort(key=lambda t: (not t["is_you"], t["team_name"]))

        return {"teams": teams}

    def _load_trade_targets_context(lg):
        """Rosters, valuations, player profiles, free-agent ids, and your
        own team's future picks -- everything both new endpoints below
        need. Mirrors get_trade_builder's fetch pattern; kept separate
        from it (not extracted into a shared helper) since no existing
        endpoint in this file shares fetch logic across endpoints either
        -- each fetches independently. Returns None on a Sleeper outage."""
        sleeper = client_mod.SleeperClient()
        try:
            nfl = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))
            profiles, _espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            proj_anchor = _season_proj_anchor_for(lg.season).get(
                lambda: _load_projection_anchor(sleeper, lg.season))
            rosters = rosters_mod.fetch(sleeper, lg.provider_league_id)
            through_week = _through_week(nfl)
            actuals = _actuals_cache_for(lg.league_key, through_week).get(
                lambda: actuals_mod.points_so_far(sleeper, lg.provider_league_id, through_week))

            your_picks: list = []
            if lg.resolved_format in ("dynasty", "keeper"):
                worst_to_best = sorted(rosters, key=lambda r: (r.wins, r.points_for))
                try:
                    capital = traded_picks_mod.capital(
                        sleeper, lg.provider_league_id,
                        num_teams=lg.num_teams,
                        rounds=int((lg.raw_settings or {}).get("draft_rounds") or 4),
                        standings_order=[r.roster_id for r in worst_to_best],
                        draft_years=(lg.season + 1, lg.season + 2),
                        team_names={r.roster_id: r.team_name for r in rosters})
                    your_picks = [a for a in capital if a.current_owner_roster_id == lg.roster_id]
                except (httpx.HTTPError, RuntimeError):
                    logging.getLogger("ffdo.api").warning(
                        "trade-targets: traded-picks fetch failed for %s, picks omitted",
                        lg.league_key)
        except (httpx.HTTPError, RuntimeError):
            return None
        finally:
            sleeper.close()

        all_pids = {pid for r in rosters for pid in r.player_ids}
        valued = ros_value_mod.roster_value(
            all_pids, lg, resolved_format=lg.resolved_format, season_proj=proj_anchor,
            profiles=profiles, actuals=actuals, weeks_played=through_week,
            season_weeks=_season_weeks(lg.season))
        free_agent_ids = waiver_value_mod.free_agents(set(profiles.keys()), rosters)

        return rosters, valued, profiles, free_agent_ids, your_picks

    def _suggestion_json(s, valued, profiles) -> dict:
        def _player_row(pid: str) -> dict:
            prof = profiles.get(pid)
            vp = valued.get(pid)
            return {
                "player_id": pid,
                "name": prof.full_name if prof else pid,
                "position": prof.position if prof else "",
                "value": round(vp.vor, 1) if vp is not None else 0.0,
            }

        def _pick_row(pick) -> dict:
            return {"label": pick.label, "season": pick.season, "round": pick.round,
                    "original_roster_id": pick.original_roster_id}

        return {
            "partner_roster_id": s.partner_roster_id,
            "partner_team_name": s.partner_team_name,
            "target_players": [_player_row(pid) for pid in s.target_player_ids],
            "offer_players": [_player_row(pid) for pid in s.offer_player_ids],
            "offer_picks": [_pick_row(p) for p in s.offer_picks],
            "target_value": s.target_value,
            "offer_value": s.offer_value,
            "net_value_gain": s.net_value_gain,
            "differential": s.differential,
            "why": (f"Fills your {s.target_position} need -- they're deep at "
                    f"{s.target_position} and thin at {s.offer_position}, "
                    f"where you have surplus."),
        }

    def _home_summary_espn(lg: TrackedLeague) -> dict:
        """ESPN's command-center card: record, power rank, and now the
        starting-lineup diff + swap-count flag, reusing `_lineup_espn`'s
        own crosswalk/slot-alignment approach. Waivers and trade targets
        still have no ESPN ingest for a league-wide "how many
        recommendations" flag count the way Sleeper's branch gets one
        cheaply from data it already fetched -- those flags stay absent
        rather than paying for a second full waiver/trade-targets
        computation just to produce a count. Matchup stays None (ESPN's
        matchup-pairing API is unresearched, same posture as #2's home
        summary from the start)."""
        cred = _require_espn_credential("the home summary")

        sleeper = client_mod.SleeperClient()
        try:
            nfl_now = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))
            profiles, espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            proj_anchor = _season_proj_anchor_for(lg.season).get(
                lambda: _load_projection_anchor(sleeper, lg.season))
            weekly_proj = _weekly_proj_cache_for(nfl_now.season, nfl_now.week).get(
                lambda: weekly_projections_mod.fetch(sleeper, nfl_now.season, nfl_now.week))
            try:
                games = _schedule_cache_for(nfl_now.season, nfl_now.week).get(
                    lambda: schedule_mod.week_games(sleeper, nfl_now.season, nfl_now.week))
            except (httpx.HTTPError, RuntimeError) as exc:
                logging.getLogger("ffdo.api").warning(
                    "home-summary: schedule fetch failed for %s week %s (%s) -- "
                    "treating nothing as locked", lg.league_key, nfl_now.week, exc)
                games = []
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(
                status_code=502, detail="Couldn't reach Sleeper, try again") from exc
        finally:
            sleeper.close()

        espn = espn_client_mod.EspnClient(cred.espn_s2, cred.swid)
        try:
            player_pool_raw = _espn_player_pool_cache_for(lg.season).get(
                lambda: espn.get_json(
                    f"{espn_client_mod.BASE}/seasons/{lg.season}/players"
                    "?view=kona_player_info",
                    extra_headers=espn_client_mod.PLAYER_POOL_FILTER_HEADER))
            cw = _espn_crosswalk_cache_for(lg.season).get(
                lambda: espn_crosswalk_mod.build(
                    espn_id_index, profiles,
                    espn_crosswalk_mod.parse_player_pool(player_pool_raw)))
            rosters, nfl, mroster_raw = espn_rosters_mod.fetch(
                espn, lg.provider_league_id, lg.season, cw)
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(
                status_code=502, detail="Couldn't reach ESPN, try again") from exc
        finally:
            espn.close()

        you = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        if you is None:
            return {
                "league_key": lg.league_key, "name": lg.name, "provider": lg.provider,
                "resolved_format": lg.resolved_format, "record": None,
                "power_rank": None, "matchup": None, "starters": [], "flags": {},
            }

        through_week = _through_week(nfl)
        actuals = espn_actuals_mod.points_so_far(mroster_raw, cw, lg.season, through_week)
        all_pids = {pid for r in rosters for pid in r.player_ids}
        valued = ros_value_mod.roster_value(
            all_pids, lg, resolved_format=lg.resolved_format, season_proj=proj_anchor,
            profiles=profiles, actuals=actuals, weeks_played=through_week,
            season_weeks=_season_weeks(lg.season))

        standings_rank = _standings_rank(rosters)
        power_rows = power_ranking_mod.rank(
            rosters, valued, lg, standings_rank, lg.roster_id, position="OVR", scope="starters")
        your_power_row = next((r for r in power_rows if r.roster_id == lg.roster_id), None)
        power_rank = None
        if your_power_row is not None:
            your_standings_rank = standings_rank.get(lg.roster_id)
            power_rank = {
                "value": your_power_row.power_rank,
                "of": len(rosters),
                "delta_vs_standings": (
                    your_standings_rank - your_power_row.power_rank
                    if your_standings_rank is not None else None),
            }

        all_teams = frozenset(p.team for p in profiles.values() if p.team)
        bye_teams = schedule_mod.bye_teams(games, all_teams) if games else frozenset()
        locked_teams = schedule_mod.locked_teams(games)

        current_starters = espn_rosters_mod.slot_aligned_starters(
            mroster_raw, cw, lg.roster_id, lg.starting_slots)
        weekly_valued = weekly_lineup_mod.weekly_value(
            all_pids, lg, weekly_points=weekly_proj, profiles=profiles, bye_teams=bye_teams)
        your_weekly_valued = {pid: weekly_valued[pid] for pid in you.player_ids if pid in weekly_valued}
        optimal = weekly_lineup_mod.optimal_slots(your_weekly_valued, lg)
        diff_rows = weekly_lineup_mod.diff(
            current_starters, optimal, locked_teams, weekly_valued, profiles, lg)

        def _starter_row(d) -> dict:
            prof = profiles.get(d.current_player_id) if d.current_player_id else None
            vp = weekly_valued.get(d.current_player_id) if d.current_player_id else None
            swap_to = None
            if d.status == "suggested_swap" and d.optimal_player_id:
                swap_prof = profiles.get(d.optimal_player_id)
                swap_to = swap_prof.full_name if swap_prof else d.optimal_player_id
            return {
                "slot_label": d.slot_label,
                "name": prof.full_name if prof else None,
                "value": round(vp.vor, 1) if vp else 0.0,
                "status": d.status,
                "swap_to": swap_to,
            }

        flags = {}
        swaps = sum(1 for d in diff_rows if d.status == "suggested_swap")
        if swaps:
            flags["lineup_swaps"] = swaps

        return {
            "league_key": lg.league_key,
            "name": you.team_name,
            "provider": lg.provider,
            "resolved_format": lg.resolved_format,
            "record": {"wins": you.wins, "losses": you.losses, "ties": you.ties},
            "power_rank": power_rank,
            "matchup": None,
            "starters": [_starter_row(d) for d in diff_rows],
            "flags": flags,
        }

    @app.get("/api/leagues/{league_key}/home-summary")
    def get_home_summary(league_key: str) -> dict:
        """One league's command-center card: power rank, starting-lineup
        diff, this week's matchup (Sleeper only), and attention-flag
        counts (lineup swap / waiver add / trade target). Composes every
        per-league computation this initiative already built -- no new
        scoring logic beyond Task 2's team_projected_score, which exists
        specifically because weekly_lineup's own per-player values are
        VOR, not the raw points a "projected score" needs to show.

        ESPN leagues get a smaller, honest card (record + power rank
        only) via `_home_summary_espn` -- weekly lineup, waivers and
        trade targets have no ESPN ingest yet."""
        lg = _load_league(league_key)
        if lg.provider == "espn":
            return _home_summary_espn(lg)

        ctx = _load_trade_targets_context(lg)
        if ctx is None:
            raise HTTPException(status_code=502, detail="Couldn't reach Sleeper, try again")
        rosters, valued, profiles, free_agent_ids, your_picks = ctx

        you = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        if you is None:
            # Observer-only tracking, same degrade /lineup and /waivers
            # already use -- nothing personal to show, not an error.
            return {
                "league_key": lg.league_key, "name": lg.name, "provider": lg.provider,
                "resolved_format": lg.resolved_format, "record": None,
                "power_rank": None, "matchup": None, "starters": [], "flags": {},
            }

        sleeper = client_mod.SleeperClient()
        try:
            nfl = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))
            current_starters = rosters_mod.raw_starters(
                sleeper, lg.provider_league_id, lg.roster_id)
            weekly_proj = _weekly_proj_cache_for(nfl.season, nfl.week).get(
                lambda: weekly_projections_mod.fetch(sleeper, nfl.season, nfl.week))
            try:
                games = _schedule_cache_for(nfl.season, nfl.week).get(
                    lambda: schedule_mod.week_games(sleeper, nfl.season, nfl.week))
            except (httpx.HTTPError, RuntimeError) as exc:
                logging.getLogger("ffdo.api").warning(
                    "home-summary: schedule fetch failed for %s week %s (%s) -- "
                    "treating nothing as locked", lg.league_key, nfl.week, exc)
                games = []
            current_week = matchups_mod.fetch(sleeper, lg.provider_league_id, nfl.week)

            waiver_recs_count = 0
            # The add/drop recommendation itself doesn't need FAAB -- only
            # the bid-amount suggestion does (see recommend_adds) -- so
            # this flag is computed for every waiver type now, with the
            # FAAB budget lookup skipped for a priority league. `raw_settings`
            # (captured at track/refresh time, already used this same way
            # for draft_rounds elsewhere in this file) avoids a live
            # settings re-fetch just to decide whether this league is
            # FAAB -- acceptable staleness for a flag COUNT, unlike
            # /waivers' own live re-fetch which needs an exact current
            # budget.
            try:
                is_faab = (lg.raw_settings or {}).get("waiver_type") == 2
                your_remaining = None
                if is_faab:
                    league_raw = sleeper.get_json(f"{client_mod.V1}/league/{lg.provider_league_id}")
                    waiver_budget = float((league_raw.get("settings") or {}).get("waiver_budget") or 0)
                    claims = _waiver_claims_cache_for(lg.league_key, nfl.week).get(
                        lambda: waivers_mod.fetch_waivers(
                            sleeper, lg.provider_league_id, season=lg.season, through_week=nfl.week))
                    budgets = waivers_mod.remaining_budget(claims, waiver_budget=waiver_budget)
                    your_remaining = budgets.get(lg.roster_id, waiver_budget)
                waiver_recs = waiver_value_mod.recommend_adds(
                    free_agent_ids, you.player_ids, valued, profiles, lg,
                    FAAB_BID_CURVE if is_faab else None, your_remaining)
                waiver_recs_count = len(waiver_recs)
            except (httpx.HTTPError, RuntimeError):
                logging.getLogger("ffdo.api").warning(
                    "home-summary: waiver fetch failed for %s, waiver flag omitted",
                    lg.league_key)
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail="Couldn't reach Sleeper, try again") from exc
        finally:
            sleeper.close()

        all_teams = frozenset(p.team for p in profiles.values() if p.team)
        bye_teams = schedule_mod.bye_teams(games, all_teams) if games else frozenset()
        locked_teams = schedule_mod.locked_teams(games)

        all_pids = {pid for r in rosters for pid in r.player_ids}
        weekly_valued = weekly_lineup_mod.weekly_value(
            all_pids, lg, weekly_points=weekly_proj, profiles=profiles, bye_teams=bye_teams)
        your_weekly_valued = {pid: weekly_valued[pid] for pid in you.player_ids if pid in weekly_valued}
        optimal = weekly_lineup_mod.optimal_slots(your_weekly_valued, lg)
        diff_rows = weekly_lineup_mod.diff(
            current_starters, optimal, locked_teams, weekly_valued, profiles, lg)

        def _starter_row(d) -> dict:
            prof = profiles.get(d.current_player_id) if d.current_player_id else None
            vp = weekly_valued.get(d.current_player_id) if d.current_player_id else None
            swap_to = None
            if d.status == "suggested_swap" and d.optimal_player_id:
                swap_prof = profiles.get(d.optimal_player_id)
                swap_to = swap_prof.full_name if swap_prof else d.optimal_player_id
            return {
                "slot_label": d.slot_label,
                "name": prof.full_name if prof else None,
                "value": round(vp.vor, 1) if vp else 0.0,
                "status": d.status,
                "swap_to": swap_to,
            }

        standings_rank = _standings_rank(rosters)
        power_rows = power_ranking_mod.rank(
            rosters, valued, lg, standings_rank, lg.roster_id, position="OVR", scope="starters")
        your_power_row = next((r for r in power_rows if r.roster_id == lg.roster_id), None)
        power_rank = None
        if your_power_row is not None:
            your_standings_rank = standings_rank.get(lg.roster_id)
            power_rank = {
                "value": your_power_row.power_rank,
                "of": len(rosters),
                "delta_vs_standings": (
                    your_standings_rank - your_power_row.power_rank
                    if your_standings_rank is not None else None),
            }

        matchup = None
        opponent_roster_id = current_week.pairing.get(lg.roster_id)
        if opponent_roster_id is not None:
            opponent = next((r for r in rosters if r.roster_id == opponent_roster_id), None)
            if opponent is not None:
                your_score = matchup_score_mod.team_projected_score(
                    you.starter_ids, weekly_points=weekly_proj, live_points=current_week.live_points,
                    profiles=profiles, locked_teams=locked_teams, scoring_settings=lg.scoring_settings)
                opp_score = matchup_score_mod.team_projected_score(
                    opponent.starter_ids, weekly_points=weekly_proj, live_points=current_week.live_points,
                    profiles=profiles, locked_teams=locked_teams, scoring_settings=lg.scoring_settings)
                matchup = {
                    "opponent_name": opponent.team_name,
                    "your_projected": round(your_score, 1),
                    "opponent_projected": round(opp_score, 1),
                }

        trade_suggestion_count = 0
        for partner_roster in rosters:
            if partner_roster.roster_id == you.roster_id:
                continue
            trade_suggestion_count += len(trade_targets_mod.suggest_for_team(
                you, partner_roster, rosters, valued, lg, free_agent_ids, your_picks,
                pick_curve=PICK_VALUE_CURVE, current_season=lg.season, round_size=lg.num_teams))

        flags = {}
        swaps = sum(1 for d in diff_rows if d.status == "suggested_swap")
        if swaps:
            flags["lineup_swaps"] = swaps
        if waiver_recs_count:
            flags["waiver_adds"] = waiver_recs_count
        if trade_suggestion_count:
            flags["trade_targets"] = trade_suggestion_count

        return {
            "league_key": lg.league_key,
            "name": you.team_name,
            "provider": lg.provider,
            "resolved_format": lg.resolved_format,
            "record": {"wins": you.wins, "losses": you.losses, "ties": you.ties},
            "power_rank": power_rank,
            "matchup": matchup,
            "starters": [_starter_row(d) for d in diff_rows],
            "flags": flags,
        }

    @app.post("/api/leagues/{league_key}/trade/suggestions")
    def get_trade_suggestions(league_key: str, payload: dict) -> dict:
        """Counter-offer suggestions for the trade partner already selected
        in the live Trade Machine session (spec (C)) -- same request shape
        as POST /trade/evaluate, so whatever's already checked there is
        folded into the hypothetical roster suggest_for_team scores
        against."""
        lg = _load_league(league_key)
        if lg.provider != "sleeper":
            raise HTTPException(status_code=400, detail="Trade suggestions are Sleeper-only for now")

        ctx = _load_trade_targets_context(lg)
        if ctx is None:
            raise HTTPException(status_code=502, detail="Couldn't reach Sleeper, try again")
        rosters, valued, profiles, free_agent_ids, your_picks = ctx

        you_roster = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        partner_roster = next(
            (r for r in rosters if r.roster_id == payload.get("partner_roster_id")), None)
        if you_roster is None or partner_roster is None:
            return {"suggestions": []}

        side_a = payload.get("side_a") or {}
        side_b = payload.get("side_b") or {}
        already_yours = frozenset(side_a.get("player_ids", []))
        already_theirs = frozenset(side_b.get("player_ids", []))

        suggestions = trade_targets_mod.suggest_for_team(
            you_roster, partner_roster, rosters, valued, lg, free_agent_ids, your_picks,
            pick_curve=PICK_VALUE_CURVE, current_season=lg.season, round_size=lg.num_teams,
            already_selected_yours=already_yours, already_selected_theirs=already_theirs)

        return {"suggestions": [_suggestion_json(s, valued, profiles) for s in suggestions]}

    @app.get("/api/leagues/{league_key}/trade-targets")
    def get_trade_targets(league_key: str) -> dict:
        """League-wide "who should I target" browse (spec (D)) -- your real
        current roster against every other team, nothing pre-selected.
        Merges every team's suggest_for_team() output, top 10 by net value
        gain."""
        lg = _load_league(league_key)
        if lg.provider != "sleeper":
            raise HTTPException(status_code=400, detail="Trade targets are Sleeper-only for now")

        ctx = _load_trade_targets_context(lg)
        if ctx is None:
            raise HTTPException(status_code=502, detail="Couldn't reach Sleeper, try again")
        rosters, valued, profiles, free_agent_ids, your_picks = ctx

        you_roster = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        if you_roster is None:
            return {"suggestions": []}

        all_suggestions = []
        for partner_roster in rosters:
            if partner_roster.roster_id == you_roster.roster_id:
                continue
            all_suggestions.extend(trade_targets_mod.suggest_for_team(
                you_roster, partner_roster, rosters, valued, lg, free_agent_ids, your_picks,
                pick_curve=PICK_VALUE_CURVE, current_season=lg.season, round_size=lg.num_teams))

        all_suggestions.sort(key=lambda s: -s.net_value_gain)
        top = all_suggestions[:10]
        return {"suggestions": [_suggestion_json(s, valued, profiles) for s in top]}

    @app.get("/api/leagues/{league_key}/trades")
    def get_trades(league_key: str) -> dict:
        """The real-trade decision ledger. Detects any new completed trade
        (every trade in the league, not only the tracked user's -- spec
        §6.1) as a side effect, then returns every recorded trade with a
        freshly recomputed current value (spec §6.3 -- never a one-time
        resolution)."""
        lg = _load_league(league_key)
        if lg.provider != "sleeper":
            raise HTTPException(status_code=400, detail="Trade ledger is Sleeper-only for now")

        sleeper = client_mod.SleeperClient()
        try:
            nfl = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))
            profiles, _espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            proj_anchor = _season_proj_anchor_for(lg.season).get(
                lambda: _load_projection_anchor(sleeper, lg.season))
            rosters = rosters_mod.fetch(sleeper, lg.provider_league_id)
            through_week = _through_week(nfl)
            actuals = actuals_mod.points_so_far(sleeper, lg.provider_league_id, through_week)
            real_trades = transactions_mod.fetch_trades(
                sleeper, lg.provider_league_id, season=lg.season, through_week=nfl.week)
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail="Couldn't reach Sleeper, try again") from exc
        finally:
            sleeper.close()

        all_pids = {pid for r in rosters for pid in r.player_ids}
        valued = ros_value_mod.roster_value(
            all_pids, lg, resolved_format=lg.resolved_format, season_proj=proj_anchor,
            profiles=profiles, actuals=actuals, weeks_played=through_week,
            season_weeks=_season_weeks(lg.season))

        # A trade can be years old -- a player long since dropped/traded
        # again has no live roster to be found on, but still has a Sleeper
        # profile (profiles is the full player pool, not just what's
        # currently rostered), so name resolution here is independent of
        # `rosters`/`valued` above.
        def _player_brief(pid: str) -> dict:
            prof = profiles.get(pid)
            return {"player_id": pid, "name": prof.full_name if prof else pid,
                    "position": prof.position if prof else None}

        def _pick_brief(p: dict) -> dict:
            asset = DraftPickAsset(
                season=p["season"], round=p["round"], projected_slot=p["projected_slot"],
                current_owner_roster_id=p["current_owner_roster_id"],
                original_roster_id=p["original_roster_id"], via_team_name=None)
            return {"label": asset.label,
                    "value": round(pick_value_mod.slot_value(
                        asset, PICK_VALUE_CURVE, current_season=lg.season,
                        round_size=lg.num_teams), 1)}

        names = {r.roster_id: r.team_name for r in rosters}

        for trade in real_trades:
            side_a = {"player_ids": trade.roster_a_gets, "picks": trade.picks_to_a}
            side_b = {"player_ids": trade.roster_b_gets, "picks": trade.picks_to_b}
            evaluation = trade_value_mod.evaluate_trade(
                side_a, side_b, valued_players=valued, pick_curve=PICK_VALUE_CURVE,
                current_season=lg.season, round_size=lg.num_teams)
            banked_a = {pid: actuals.get(pid, 0.0) for pid in trade.roster_a_gets}
            banked_b = {pid: actuals.get(pid, 0.0) for pid in trade.roster_b_gets}
            _TRADE_LEDGER.record_if_absent(
                lg.league_key, trade, side_a_value=evaluation.side_a_value,
                side_b_value=evaluation.side_b_value, banked_a=banked_a, banked_b=banked_b)

        entries = _TRADE_LEDGER.list_for_league(lg.league_key)
        out = []
        for entry in entries:
            current_a_delta = sum(
                actuals.get(pid, 0.0) - entry.banked_a_at_trade.get(pid, 0.0)
                for pid in entry.roster_a_gets)
            current_b_delta = sum(
                actuals.get(pid, 0.0) - entry.banked_b_at_trade.get(pid, 0.0)
                for pid in entry.roster_b_gets)
            picks_a = [_pick_brief(p) for p in entry.picks_to_a]
            picks_b = [_pick_brief(p) for p in entry.picks_to_b]
            out.append({
                "transaction_id": entry.transaction_id, "week": entry.week,
                "roster_a_id": entry.roster_a_id, "roster_b_id": entry.roster_b_id,
                "roster_a_team_name": names.get(entry.roster_a_id, f"Team {entry.roster_a_id}"),
                "roster_b_team_name": names.get(entry.roster_b_id, f"Team {entry.roster_b_id}"),
                "roster_a_gets": [_player_brief(pid) for pid in entry.roster_a_gets],
                "roster_b_gets": [_player_brief(pid) for pid in entry.roster_b_gets],
                "roster_a_picks": picks_a, "roster_b_picks": picks_b,
                "side_a_value_at_trade": round(entry.side_a_value_at_trade, 1),
                "side_b_value_at_trade": round(entry.side_b_value_at_trade, 1),
                "current_player_points_delta_a": round(current_a_delta, 1),
                "current_player_points_delta_b": round(current_b_delta, 1),
                "current_pick_value_a": round(sum(p["value"] for p in picks_a), 1),
                "current_pick_value_b": round(sum(p["value"] for p in picks_b), 1),
            })
        return {"trades": out}

    def _waivers_espn(lg: TrackedLeague) -> dict:
        """ESPN's waiver recommendations, add/drop always, a FAAB bid
        suggestion added on top only when
        raw_settings.acquisitionSettings.isUsingAcquisitionBudget is true
        (ESPN's equivalent of Sleeper's waiver_type == 2) -- same posture
        as get_waivers' own FAAB/priority split. UNVERIFIED end-to-end for
        the FAAB case: the real league this project validates ESPN
        against uses waiver-priority, not FAAB, and has no real waiver
        history to check a resolved claim against -- see
        ingest.espn.transactions's own module docstring for what's
        unverified in the underlying feed. The add/drop-only,
        no-bid-suggestion path (what that real league actually gets) IS
        live-verified."""
        acquisition = (lg.raw_settings or {}).get("acquisitionSettings") or {}
        is_faab = bool(acquisition.get("isUsingAcquisitionBudget"))
        waiver_budget = float(acquisition.get("acquisitionBudget") or 0) if is_faab else None
        cred = _require_espn_credential("waivers")

        sleeper = client_mod.SleeperClient()
        try:
            profiles, espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            proj_anchor = _season_proj_anchor_for(lg.season).get(
                lambda: _load_projection_anchor(sleeper, lg.season))
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(
                status_code=502, detail="Couldn't reach Sleeper, try again") from exc
        finally:
            sleeper.close()

        espn = espn_client_mod.EspnClient(cred.espn_s2, cred.swid)
        try:
            player_pool_raw = _espn_player_pool_cache_for(lg.season).get(
                lambda: espn.get_json(
                    f"{espn_client_mod.BASE}/seasons/{lg.season}/players"
                    "?view=kona_player_info",
                    extra_headers=espn_client_mod.PLAYER_POOL_FILTER_HEADER))
            cw = _espn_crosswalk_cache_for(lg.season).get(
                lambda: espn_crosswalk_mod.build(
                    espn_id_index, profiles,
                    espn_crosswalk_mod.parse_player_pool(player_pool_raw)))
            rosters, nfl, mroster_raw = espn_rosters_mod.fetch(
                espn, lg.provider_league_id, lg.season, cw)

            through_week = _through_week(nfl)
            actuals = espn_actuals_mod.points_so_far(mroster_raw, cw, lg.season, through_week)

            claims = espn_transactions_mod.fetch_waivers(
                espn, lg.provider_league_id, lg.season, cw, through_week=nfl.week)
            budgets = (waivers_mod.remaining_budget(claims, waiver_budget=waiver_budget)
                      if is_faab else {})

            your_claims = (
                [c for c in espn_transactions_mod.fetch_all_claims(
                    espn, lg.provider_league_id, lg.season, cw, through_week=nfl.week)
                 if c.roster_id == lg.roster_id]
                if lg.roster_id is not None else [])
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(
                status_code=502, detail="Couldn't reach ESPN, try again") from exc
        finally:
            espn.close()

        you = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        if you is None:
            return {"remaining_budget": waiver_budget, "recommendations": []}

        # The ESPN player pool (crosswalked), not the full Sleeper
        # universe `_load_trade_targets_context` uses for Sleeper --
        # ESPN's own free-agent pool is scoped to whoever ESPN's league
        # itself doesn't have owned, which the crosswalk's key set already
        # reflects.
        all_player_ids = set(cw.espn_to_sleeper.values())
        free_agent_ids = waiver_value_mod.free_agents(all_player_ids, rosters)
        your_remaining = budgets.get(lg.roster_id, waiver_budget) if is_faab else None

        valued = ros_value_mod.roster_value(
            set(you.player_ids) | free_agent_ids, lg,
            resolved_format=lg.resolved_format, season_proj=proj_anchor,
            profiles=profiles, actuals=actuals, weeks_played=through_week,
            season_weeks=_season_weeks(lg.season))

        recommendations = waiver_value_mod.recommend_adds(
            free_agent_ids, waiver_value_mod.droppable_player_ids(you), valued, profiles, lg,
            FAAB_BID_CURVE if is_faab else None, your_remaining)

        recommended_by_player = {r.free_agent_id: r for r in recommendations}
        for claim in your_claims:
            rec = recommended_by_player.get(claim.player_id)
            _WAIVER_LEDGER.record_if_absent(
                lg.league_key, transaction_id=claim.transaction_id, season=claim.season,
                week=claim.week, roster_id=claim.roster_id, add_player_id=claim.player_id,
                drop_player_id=rec.drop_player_id if rec else None,
                recommended_bid=rec.suggested_bid if rec else None,
                actual_bid=int(claim.bid_amount),
                predicted_vor_gain=rec.vor_gain if rec else None,
                won=claim.won)

        def _waiver_row(r) -> dict:
            add_prof = profiles.get(r.free_agent_id)
            drop_prof = profiles.get(r.drop_player_id) if r.drop_player_id else None
            return {
                "free_agent_id": r.free_agent_id,
                "free_agent_name": add_prof.full_name if add_prof else r.free_agent_id,
                "free_agent_position": add_prof.position if add_prof else "",
                "drop_player_id": r.drop_player_id,
                "drop_player_name": (
                    drop_prof.full_name if drop_prof else r.drop_player_id),
                "drop_player_position": drop_prof.position if drop_prof else "",
                "vor_gain": round(r.vor_gain, 1), "suggested_bid": r.suggested_bid,
            }

        return {
            "remaining_budget": round(your_remaining, 1) if your_remaining is not None else None,
            "recommendations": [_waiver_row(r) for r in recommendations],
        }

    @app.get("/api/leagues/{league_key}/waivers")
    def get_waivers(league_key: str) -> dict:
        """Free-agent add/drop recommendations, with a FAAB bid suggestion
        added on top for FAAB leagues (`waiver_type == 2`) -- a
        waiver-priority league still gets the same VOR-based add/drop
        call, just with `suggested_bid: null` and `remaining_budget: null`,
        since there's no budget concept to suggest a bid against.
        ESPN's FAAB detection lives in _waivers_espn."""
        lg = _load_league(league_key)
        if lg.provider == "espn":
            return _waivers_espn(lg)

        sleeper = client_mod.SleeperClient()
        try:
            league_raw = sleeper.get_json(f"{client_mod.V1}/league/{lg.provider_league_id}")
            settings = league_raw.get("settings") or {}
            is_faab = settings.get("waiver_type") == 2
            waiver_budget = float(settings.get("waiver_budget") or 0) if is_faab else None

            nfl = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))
            profiles, _espn_id_index = players_cache.get(lambda: _load_players(sleeper))
            proj_anchor = _season_proj_anchor_for(lg.season).get(
                lambda: _load_projection_anchor(sleeper, lg.season))
            rosters = rosters_mod.fetch(sleeper, lg.provider_league_id)
            through_week = _through_week(nfl)
            actuals = actuals_mod.points_so_far(sleeper, lg.provider_league_id, through_week)

            # nfl.week, NOT through_week -- a waiver claim can happen during
            # the current, still-in-progress week (see this task's design
            # note: reusing the stats-final bound here reproduces #5's
            # GET /trades bug).
            claims = waivers_mod.fetch_waivers(
                sleeper, lg.provider_league_id, season=lg.season, through_week=nfl.week)
            budgets = (waivers_mod.remaining_budget(claims, waiver_budget=waiver_budget)
                      if is_faab else {})

            # For the ledger (outcome scorecard) -- every claim for the
            # tracked roster, win or loss, not just the winning ones
            # `claims` above is scoped to.
            your_claims = (
                [c for c in waivers_mod.fetch_all_claims(
                    sleeper, lg.provider_league_id, season=lg.season, through_week=nfl.week)
                 if c.roster_id == lg.roster_id]
                if lg.roster_id is not None else [])

            all_player_ids = set(profiles)
        except HTTPException:
            raise
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail="Couldn't reach Sleeper, try again") from exc
        finally:
            sleeper.close()

        you = next((r for r in rosters if r.roster_id == lg.roster_id), None)
        if you is None:
            return {"remaining_budget": waiver_budget, "recommendations": []}

        free_agent_ids = waiver_value_mod.free_agents(all_player_ids, rosters)
        your_remaining = budgets.get(lg.roster_id, waiver_budget) if is_faab else None

        valued = ros_value_mod.roster_value(
            set(you.player_ids) | free_agent_ids, lg,
            resolved_format=lg.resolved_format, season_proj=proj_anchor,
            profiles=profiles, actuals=actuals, weeks_played=through_week,
            season_weeks=_season_weeks(lg.season))

        recommendations = waiver_value_mod.recommend_adds(
            free_agent_ids, waiver_value_mod.droppable_player_ids(you), valued, profiles, lg,
            FAAB_BID_CURVE if is_faab else None, your_remaining)

        # A claim only matches a recommendation if it happened THIS week
        # (recommend_adds only ever returns the current week's top-N) --
        # older claims are recorded with recommended_bid/predicted_vor_gain
        # as None, an accepted, disclosed limitation: past weeks'
        # recommendations were never persisted before this ledger existed,
        # so they cannot be reconstructed now.
        recommended_by_player = {r.free_agent_id: r for r in recommendations}
        for claim in your_claims:
            rec = recommended_by_player.get(claim.player_id)
            _WAIVER_LEDGER.record_if_absent(
                lg.league_key, transaction_id=claim.transaction_id, season=claim.season,
                week=claim.week, roster_id=claim.roster_id, add_player_id=claim.player_id,
                drop_player_id=rec.drop_player_id if rec else None,
                recommended_bid=rec.suggested_bid if rec else None,
                actual_bid=int(claim.bid_amount),
                predicted_vor_gain=rec.vor_gain if rec else None,
                won=claim.won)

        def _waiver_row(r) -> dict:
            add_prof = profiles.get(r.free_agent_id)
            drop_prof = profiles.get(r.drop_player_id) if r.drop_player_id else None
            return {
                "free_agent_id": r.free_agent_id,
                "free_agent_name": add_prof.full_name if add_prof else r.free_agent_id,
                "free_agent_position": add_prof.position if add_prof else "",
                "drop_player_id": r.drop_player_id,
                "drop_player_name": (
                    drop_prof.full_name if drop_prof else r.drop_player_id),
                "drop_player_position": drop_prof.position if drop_prof else "",
                "vor_gain": round(r.vor_gain, 1), "suggested_bid": r.suggested_bid,
            }

        return {
            "remaining_budget": round(your_remaining, 1) if your_remaining is not None else None,
            "recommendations": [_waiver_row(r) for r in recommendations],
        }

    @app.get("/api/leagues/{league_key}/scorecard")
    def get_scorecard(league_key: str) -> dict:
        """How good FFDO's recommendations have actually been, across all
        four recommendation types this app makes. Each metric degrades
        independently rather than failing the whole request -- a league
        with no waiver/trade/draft history yet still gets a 200 with
        whatever subset is computable."""
        lg = _load_league(league_key)
        if lg.provider != "sleeper":
            raise HTTPException(status_code=400, detail="Scorecard is Sleeper-only for now")

        lineup_records = _LINEUP_LEDGER.list_for_league(lg.league_key)
        resolved_lineup = [r for r in lineup_records if r.followed is not None]

        # Only fetch the specific (season, week) pairs the resolved ledger
        # rows actually need -- never the whole season's history.
        weekly_points: dict[tuple[int, int], dict[str, dict[str, float]]] = {}
        if resolved_lineup:
            sleeper = client_mod.SleeperClient()
            try:
                for season, week in {(r.season, r.week) for r in resolved_lineup}:
                    weekly_points[(season, week)] = historical_weekly_stats_mod.fetch(
                        sleeper, season, week)
            except (httpx.HTTPError, RuntimeError):
                # A stats-feed outage degrades this one metric's
                # points-left figure to 0.0 rather than failing the whole
                # scorecard -- the weeks-followed counts above are
                # unaffected, since they don't need this data.
                weekly_points = {}
            finally:
                sleeper.close()

        lineup_outcomes = [
            scorecard_mod.LineupOutcome(
                season=r.season, week=r.week, recommended=r.recommended,
                actual=r.actual, followed=r.followed)
            for r in resolved_lineup
        ]
        lineup_result = scorecard_mod.lineup_metric(
            lineup_outcomes, weekly_points, lg.scoring_settings)

        trade_entries = _TRADE_LEDGER.list_for_league(lg.league_key)
        trade_outcomes = []
        if trade_entries:
            sleeper = client_mod.SleeperClient()
            try:
                nfl = nfl_state_cache.get(lambda: nfl_state_mod.current_week(sleeper))
                through_week = _through_week(nfl)
                actuals = actuals_mod.points_so_far(sleeper, lg.provider_league_id, through_week)
            except (httpx.HTTPError, RuntimeError):
                actuals = {}
            finally:
                sleeper.close()
            for entry in trade_entries:
                current_a_delta = sum(
                    actuals.get(pid, 0.0) - entry.banked_a_at_trade.get(pid, 0.0)
                    for pid in entry.roster_a_gets)
                current_b_delta = sum(
                    actuals.get(pid, 0.0) - entry.banked_b_at_trade.get(pid, 0.0)
                    for pid in entry.roster_b_gets)
                trade_outcomes.append(scorecard_mod.TradeOutcome(
                    transaction_id=entry.transaction_id,
                    roster_a_id=entry.roster_a_id, roster_b_id=entry.roster_b_id,
                    side_a_value_at_trade=entry.side_a_value_at_trade,
                    side_b_value_at_trade=entry.side_b_value_at_trade,
                    side_a_current_value=entry.side_a_value_at_trade + current_a_delta,
                    side_b_current_value=entry.side_b_value_at_trade + current_b_delta))
        trade_result = scorecard_mod.trade_metric(
            trade_outcomes, your_roster_id=lg.roster_id if lg.roster_id is not None else -1)

        waiver_entries = _WAIVER_LEDGER.list_for_league(lg.league_key)
        waiver_outcomes = [
            scorecard_mod.WaiverOutcome(
                won=e.won, recommended_bid=e.recommended_bid, actual_bid=e.actual_bid)
            for e in waiver_entries
        ]
        waiver_result = scorecard_mod.waiver_metric(waiver_outcomes)

        draft_entries = _DRAFT_PICK_LEDGER.list_for_league(lg.league_key)
        your_draft_entries = (
            [e for e in draft_entries if e.roster_id == lg.roster_id]
            if lg.roster_id is not None else [])
        draft_outcomes = [scorecard_mod.DraftOutcome(grade=e.grade) for e in your_draft_entries]
        draft_result = scorecard_mod.draft_metric(draft_outcomes)

        return {
            "lineup": lineup_result, "trade": trade_result,
            "waiver": waiver_result, "draft": draft_result,
        }

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
