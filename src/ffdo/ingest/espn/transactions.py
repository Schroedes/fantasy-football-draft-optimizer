"""Real completed FAAB waiver claims, from ESPN's mTransactions2 view --
the sibling of ingest.sleeper.waivers, producing the same WaiverClaim
domain model so api.app's /waivers endpoint can treat both providers
identically.

UNVERIFIED against a real resolved claim or a full season's volume: the
real league available while building this uses waiver-PRIORITY, not
FAAB (`acquisitionSettings.isUsingAcquisitionBudget == False`, every
real `bidAmount` observed was 0), so there is no FAAB claim to confirm
"won" against, and no live evidence this holds up at a full season's
transaction volume.

A single request with `scoringPeriodId=1` is used rather than looping
week by week the way Sleeper's own per-week endpoint requires -- live
probing (2026-09-16) found `scoringPeriodId` values other than 1 (0, 2,
3, or omitted entirely) all returned an *identical* byte-for-byte
response (apparently a "recent activity" view), while `scoringPeriodId=1`
alone returned a much larger response spanning multiple scoringPeriodIds
(draft, free-agency, and the current week's pending claims together).
That one call appears to be ESPN's real "full history" mode for this
unofficial endpoint, not a filter to period 1 specifically -- but this
is inferred from one league's behavior at week 2, not confirmed against
a full season. Revisit this if a real FAAB league surfaces a case where
claims from an earlier week are missing.
"""

from __future__ import annotations

from ffdo.domain.models import WaiverClaim
from ffdo.ingest.espn.client import BASE, EspnClient
from ffdo.ingest.espn.crosswalk import Crosswalk

_FULL_HISTORY_SCORING_PERIOD = 1


def _fetch_raw(espn: EspnClient, league_id: str, season: int) -> dict:
    return espn.get_json(
        f"{BASE}/seasons/{season}/segments/0/leagues/{league_id}"
        f"?view=mTransactions2&scoringPeriodId={_FULL_HISTORY_SCORING_PERIOD}")


def _add_item(t: dict) -> dict | None:
    return next((it for it in t.get("items") or [] if it.get("type") == "ADD"), None)


def _claim(t: dict, add: dict, crosswalk: Crosswalk, season: int, *, won: bool) -> WaiverClaim:
    espn_pid = str(add.get("playerId"))
    # A claim whose added player fails to crosswalk still spent real FAAB
    # budget -- `remaining_budget` sums every claim's `bid_amount`
    # regardless of whether the player itself is identifiable, so this
    # must never be dropped just because the crosswalk missed. The
    # synthetic id can never collide with a real Sleeper id and safely
    # falls through every downstream `profiles.get(...)` as unknown,
    # rather than silently understating how much budget was actually
    # spent.
    sleeper_id = crosswalk.espn_to_sleeper.get(espn_pid) or f"espn-uncrosswalked:{espn_pid}"
    return WaiverClaim(
        transaction_id=str(t["id"]), season=season,
        week=int(t.get("scoringPeriodId") or 0),
        roster_id=int(t["teamId"]), player_id=sleeper_id,
        bid_amount=float(t.get("bidAmount") or 0.0),
        created_ms=int(t.get("proposedDate") or 0), won=won)


def fetch_waivers(
    espn: EspnClient, league_id: str, season: int, crosswalk: Crosswalk,
    *, through_week: int,
) -> list[WaiverClaim]:
    """Only WON claims (status == "EXECUTED") -- a claim that never
    executed never spent budget, mirroring Sleeper's own fetch_waivers
    contract exactly."""
    out: list[WaiverClaim] = []
    raw = _fetch_raw(espn, league_id, season)
    for t in raw.get("transactions") or []:
        if t.get("type") != "WAIVER" or t.get("status") != "EXECUTED":
            continue
        if int(t.get("scoringPeriodId") or 0) > through_week:
            continue
        add = _add_item(t)
        if add is None:
            continue
        out.append(_claim(t, add, crosswalk, season, won=True))
    return out


def fetch_all_claims(
    espn: EspnClient, league_id: str, season: int, crosswalk: Crosswalk,
    *, through_week: int,
) -> list[WaiverClaim]:
    """Every FAAB waiver-type transaction, win or loss -- for a future
    ESPN waiver ledger, mirroring Sleeper's fetch_all_claims."""
    out: list[WaiverClaim] = []
    raw = _fetch_raw(espn, league_id, season)
    for t in raw.get("transactions") or []:
        if t.get("type") != "WAIVER":
            continue
        if int(t.get("scoringPeriodId") or 0) > through_week:
            continue
        add = _add_item(t)
        if add is None:
            continue
        out.append(_claim(t, add, crosswalk, season, won=t.get("status") == "EXECUTED"))
    return out
