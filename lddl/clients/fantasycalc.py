"""FantasyCalc public API client.

FantasyCalc exposes ``GET /values/current`` with format params
(isDynasty, numQbs, numTeams, ppr). Responses include both human
players (with sleeperId set) and rookie picks (sleeperId like
``DP_<year_offset>_<slot>``, position='PICK'). Values are crowdsourced
approximations — every downstream report should caveat that.
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

FANTASYCALC_BASE_URL = "https://api.fantasycalc.com"
DEFAULT_TIMEOUT = httpx.Timeout(30.0)
MIN_REQUEST_INTERVAL_S = 0.1


class FantasyCalcClient:
    def __init__(
        self,
        cache_dir: Path,
        *,
        http: httpx.Client | None = None,
        base_url: str = FANTASYCALC_BASE_URL,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url
        self._http = http if http is not None else httpx.Client(
            timeout=DEFAULT_TIMEOUT, base_url=base_url
        )
        self._owns_http = http is None
        self._last_request_time = 0.0

    def __enter__(self) -> FantasyCalcClient:
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
    def _get(self, path: str, params: dict[str, Any]) -> Any:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL_S:
            time.sleep(MIN_REQUEST_INTERVAL_S - elapsed)
        resp = self._http.get(path, params=params)
        self._last_request_time = time.monotonic()
        resp.raise_for_status()
        return resp.json()

    def get_current_values(
        self,
        *,
        is_dynasty: bool = True,
        num_qbs: int = 1,
        num_teams: int = 12,
        ppr: float = 0.5,
        snapshot_date: date | None = None,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        snapshot_date = snapshot_date or date.today()
        cache_key = (
            f"{snapshot_date.isoformat()}_"
            f"{'dyn' if is_dynasty else 'rd'}_"
            f"qb{num_qbs}_t{num_teams}_ppr{ppr}"
        )
        cache_path = self.cache_dir / f"{cache_key}.json"
        if cache_path.exists() and not force:
            return json.loads(cache_path.read_text())
        params = {
            "isDynasty": "true" if is_dynasty else "false",
            "numQbs": num_qbs,
            "numTeams": num_teams,
            "ppr": ppr,
        }
        data = self._get("/values/current", params)
        cache_path.write_text(json.dumps(data))
        return data
