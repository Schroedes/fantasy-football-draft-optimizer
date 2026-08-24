"""Generic GET-with-retry loop shared by every provider's HTTP client.

Extracted from ffdo.ingest.client.SleeperClient once ffdo.ingest.espn.client
needed the identical retry/backoff behavior -- same discipline as the
rank_by_position/greedy_fill_slots extraction in engine/replacement.py: one
algorithm, two callers, verified behavior-preserving via SleeperClient's
existing tests passing unchanged before and after.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx


def get_json_with_retry(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    base_delay: float = 0.0,
    max_attempts: int = 4,
    sleep: Callable[[float], None],
) -> Any:
    last: Exception | None = None
    for attempt in range(max_attempts):
        try:
            resp = client.get(url, headers=headers)
        except httpx.TransportError as exc:
            last = exc
            if attempt == max_attempts - 1:
                break
            sleep(base_delay + 2**attempt)
            continue

        if resp.status_code == 429 or resp.status_code >= 500:
            last = httpx.HTTPStatusError(
                f"retryable {resp.status_code}",
                request=resp.request, response=resp,
            )
            if attempt == max_attempts - 1:
                break
            sleep(base_delay + 2**attempt)
            continue

        # Any other error status (404, 400, 401, 403, ...) is permanent:
        # fail fast rather than burning retry attempts and backoff time.
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"GET {url} failed after {max_attempts} attempts") from last
