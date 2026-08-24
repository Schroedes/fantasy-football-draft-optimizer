"""ESPN's fantasy API client. Cookie-authenticated -- there is no OAuth or
public API key flow for a private league. Everything above ingest/espn/
must never see espn_s2/SWID or any raw ESPN JSON key; adapters translate
at this boundary, same rule ffdo.ingest.client applies to Sleeper.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ffdo.ingest.http import get_json_with_retry

# Verified live 2026-08-23 against a real league: "fantasy.espn.com" (the
# host used throughout most public writeups) 302-redirects reads to
# https://www.espn.com/fantasy/ -- it no longer serves this API directly.
# This host is the one that actually returns data.
BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"

# A default/bare HTTP client User-Agent gets the same redirect treatment
# even on the correct host -- confirmed live; CloudFront in front of ESPN's
# API appears to filter on it.
_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


# Verified live: a request to the player-pool endpoint with no filter header
# returns only a default top-50 page, not the full pool -- silently
# breaking the crosswalk for almost every real draft pick. This filter
# (verified live: 11,613 entries returned, covering all six fantasy-
# relevant positions) is required on every kona_player_info request.
PLAYER_POOL_FILTER_HEADER = {
    "X-Fantasy-Filter": '{"players":{"filterSlotIds":{"value":[0,2,4,6,16,17]},"limit":3000}}',
}


class EspnClient:
    def __init__(
        self,
        espn_s2: str,
        swid: str,
        base_delay: float = 0.0,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._headers = {
            "Cookie": f"espn_s2={espn_s2}; SWID={swid}",
            "User-Agent": _USER_AGENT,
        }
        self._base_delay = base_delay
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def get_json(
        self, url: str, extra_headers: dict[str, str] | None = None, max_attempts: int = 4,
    ) -> Any:
        headers = {**self._headers, **extra_headers} if extra_headers else self._headers
        return get_json_with_retry(
            self._client, url, headers=headers,
            base_delay=self._base_delay, max_attempts=max_attempts,
            sleep=time.sleep)

    def close(self) -> None:
        self._client.close()
