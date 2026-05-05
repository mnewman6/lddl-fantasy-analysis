"""KeepTradeCut public-rankings scraper.

KTC has no public API. The dynasty-rankings page server-renders a single JS
variable ``var playersArray = [...];`` containing the full ranked list.
Each record holds both ``oneQBValues`` and ``superflexValues`` so one fetch
covers both formats. Picks are flagged with ``position == 'RDP'``.

This client is rate-limited and identifies as a real browser. Cache-by-date
mirrors the FantasyCalc client so repeat-runs in a day are no-ops.
"""

from __future__ import annotations

import json
import re
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

KTC_BASE_URL = "https://keeptradecut.com"
DYNASTY_PATH = "/dynasty-rankings"
DEFAULT_TIMEOUT = httpx.Timeout(45.0)
MIN_REQUEST_INTERVAL_S = 1.5  # be polite — we hit the public site

# Match `var playersArray = [...];` (also tolerate `let`/`const`).
_PLAYERS_RE = re.compile(
    r"(?:var|let|const)\s+playersArray\s*=\s*(\[.*?\])\s*;",
    re.DOTALL,
)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Safari/605.1.15"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class KTCParseError(RuntimeError):
    """Raised when we can't find/parse the playersArray in the HTML."""


class KTCClient:
    def __init__(
        self,
        cache_dir: Path,
        *,
        http: httpx.Client | None = None,
        base_url: str = KTC_BASE_URL,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url
        self._http = http if http is not None else httpx.Client(
            timeout=DEFAULT_TIMEOUT,
            base_url=base_url,
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
        )
        self._owns_http = http is None
        self._last_request_time = 0.0

    def __enter__(self) -> KTCClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    @retry(
        retry=retry_if_exception_type(
            (httpx.HTTPStatusError, httpx.TransportError)
        ),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _fetch_html(self, path: str) -> str:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL_S:
            time.sleep(MIN_REQUEST_INTERVAL_S - elapsed)
        resp = self._http.get(path)
        self._last_request_time = time.monotonic()
        resp.raise_for_status()
        return resp.text

    def get_dynasty_rankings(
        self,
        *,
        snapshot_date: date | None = None,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        """Return the parsed dynasty playersArray.

        Each record contains both ``oneQBValues`` and ``superflexValues``
        sub-objects. Picks are records with ``position == 'RDP'``.
        """
        snapshot_date = snapshot_date or date.today()
        cache_path = self.cache_dir / f"{snapshot_date.isoformat()}_dynasty.json"
        if cache_path.exists() and not force:
            return json.loads(cache_path.read_text())

        html = self._fetch_html(DYNASTY_PATH)
        m = _PLAYERS_RE.search(html)
        if not m:
            raise KTCParseError(
                "Could not find `playersArray` in KTC dynasty-rankings HTML. "
                "The page may have changed structure or returned an "
                "anti-bot interstitial."
            )
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError as e:
            raise KTCParseError(
                f"playersArray was found but not valid JSON: {e}"
            ) from e

        if not isinstance(data, list) or not data:
            raise KTCParseError(
                f"playersArray parsed but not a non-empty list (got {type(data).__name__}, len={len(data) if hasattr(data,'__len__') else '?'})"
            )

        cache_path.write_text(json.dumps(data))
        return data
