"""The only Sleeper-specific HTTP client. Everything else reads cache or snapshot."""

from __future__ import annotations

import time
from typing import Any

import httpx

from ffdo.ingest.http import get_json_with_retry

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
        return get_json_with_retry(
            self._client, url, base_delay=self._base_delay,
            max_attempts=max_attempts, sleep=time.sleep)

    def close(self) -> None:
        self._client.close()
