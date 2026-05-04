"""Sleeper API client with on-disk response caching, retry, and throttling.

The Sleeper API is public and unauthenticated. Their published rate limit is
1000 requests/minute; we throttle conservatively below that. Completed seasons
never change, so their cached responses can be reused indefinitely; pass
``force=True`` to refetch in-progress data.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

SLEEPER_BASE_URL = "https://api.sleeper.app/v1"
DEFAULT_TIMEOUT = httpx.Timeout(30.0)
MIN_REQUEST_INTERVAL_S = 0.05  # ~20 req/sec, well under Sleeper's 1000/min cap


class SleeperClient:
    def __init__(
        self,
        cache_dir: Path,
        *,
        http: httpx.Client | None = None,
        base_url: str = SLEEPER_BASE_URL,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url
        self._http = http if http is not None else httpx.Client(
            timeout=DEFAULT_TIMEOUT, base_url=base_url
        )
        self._owns_http = http is None
        self._last_request_time = 0.0

    def __enter__(self) -> SleeperClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _get(self, path: str) -> Any:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL_S:
            time.sleep(MIN_REQUEST_INTERVAL_S - elapsed)
        resp = self._http.get(path)
        self._last_request_time = time.monotonic()
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        if not resp.content:
            return None
        return resp.json()

    def _cached_get(self, path: str, cache_key: str, *, force: bool = False) -> Any:
        cache_path = self.cache_dir / f"{cache_key}.json"
        if cache_path.exists() and not force:
            return json.loads(cache_path.read_text())
        data = self._get(path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data))
        return data

    # --- Endpoint helpers -------------------------------------------------

    def get_league(self, league_id: str, *, force: bool = False) -> dict[str, Any] | None:
        return self._cached_get(
            f"/league/{league_id}", f"{league_id}/league", force=force
        )

    def get_users(self, league_id: str, *, force: bool = False) -> list[dict[str, Any]]:
        return self._cached_get(
            f"/league/{league_id}/users", f"{league_id}/users", force=force
        ) or []

    def get_rosters(self, league_id: str, *, force: bool = False) -> list[dict[str, Any]]:
        return self._cached_get(
            f"/league/{league_id}/rosters", f"{league_id}/rosters", force=force
        ) or []

    def get_matchups(
        self, league_id: str, week: int, *, force: bool = False
    ) -> list[dict[str, Any]]:
        return self._cached_get(
            f"/league/{league_id}/matchups/{week}",
            f"{league_id}/matchups_w{week:02d}",
            force=force,
        ) or []

    def get_transactions(
        self, league_id: str, week: int, *, force: bool = False
    ) -> list[dict[str, Any]]:
        return self._cached_get(
            f"/league/{league_id}/transactions/{week}",
            f"{league_id}/transactions_w{week:02d}",
            force=force,
        ) or []

    def get_winners_bracket(
        self, league_id: str, *, force: bool = False
    ) -> list[dict[str, Any]]:
        return self._cached_get(
            f"/league/{league_id}/winners_bracket",
            f"{league_id}/winners_bracket",
            force=force,
        ) or []

    def get_losers_bracket(
        self, league_id: str, *, force: bool = False
    ) -> list[dict[str, Any]]:
        return self._cached_get(
            f"/league/{league_id}/losers_bracket",
            f"{league_id}/losers_bracket",
            force=force,
        ) or []

    def get_traded_picks(
        self, league_id: str, *, force: bool = False
    ) -> list[dict[str, Any]]:
        return self._cached_get(
            f"/league/{league_id}/traded_picks",
            f"{league_id}/traded_picks",
            force=force,
        ) or []

    def get_drafts(self, league_id: str, *, force: bool = False) -> list[dict[str, Any]]:
        return self._cached_get(
            f"/league/{league_id}/drafts", f"{league_id}/drafts", force=force
        ) or []

    def get_draft_picks(
        self, draft_id: str, *, force: bool = False
    ) -> list[dict[str, Any]]:
        return self._cached_get(
            f"/draft/{draft_id}/picks", f"drafts/{draft_id}/picks", force=force
        ) or []

    def get_draft_traded_picks(
        self, draft_id: str, *, force: bool = False
    ) -> list[dict[str, Any]]:
        return self._cached_get(
            f"/draft/{draft_id}/traded_picks",
            f"drafts/{draft_id}/traded_picks",
            force=force,
        ) or []

    def get_players_nfl(self, *, force: bool = False) -> dict[str, Any]:
        # Single global ~5MB endpoint; cached at the cache-dir root.
        cache_path = self.cache_dir / "players_nfl.json"
        if cache_path.exists() and not force:
            return json.loads(cache_path.read_text())
        data = self._get("/players/nfl")
        cache_path.write_text(json.dumps(data))
        return data
