"""The only module that performs HTTP. Everything else reads cache or snapshot."""

from __future__ import annotations

import time
from typing import Any

import httpx

V1 = "https://api.sleeper.app/v1"
PROJECTIONS = "https://api.sleeper.app/projections/nfl"


class SleeperClient:
    """Sleeper asks callers to stay under 1000 requests/minute."""

    def __init__(
        self,
        base_delay: float = 0.0,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_delay = base_delay
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def get_json(self, url: str, max_attempts: int = 4) -> Any:
        last: Exception | None = None
        for attempt in range(max_attempts):
            try:
                resp = self._client.get(url)
            except httpx.TransportError as exc:
                last = exc
                if attempt == max_attempts - 1:
                    break
                time.sleep(self._base_delay + 2**attempt)
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                last = httpx.HTTPStatusError(
                    f"retryable {resp.status_code}",
                    request=resp.request, response=resp,
                )
                if attempt == max_attempts - 1:
                    break
                time.sleep(self._base_delay + 2**attempt)
                continue

            # Any other error status (404, 400, 401, 403, ...) is permanent:
            # fail fast rather than burning retry attempts and backoff time.
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"GET {url} failed after {max_attempts} attempts") from last

    def close(self) -> None:
        self._client.close()
