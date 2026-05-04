"""Cache behavior tests for the Sleeper client."""

from __future__ import annotations

from pathlib import Path

import httpx

from lddl.clients.sleeper import SleeperClient


def _counted_client(tmp_path: Path, payload: dict, counter: dict) -> SleeperClient:
    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] = counter.get("n", 0) + 1
        return httpx.Response(200, json=payload)

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.sleeper.app/v1",
    )
    return SleeperClient(cache_dir=tmp_path, http=http)


def test_cache_hit_skips_http(tmp_path: Path) -> None:
    counter: dict = {}
    payload = {"league_id": "X", "season": "2024", "previous_league_id": None}
    with _counted_client(tmp_path, payload, counter) as client:
        a = client.get_league("X")
        b = client.get_league("X")
    assert a == b == payload
    assert counter["n"] == 1


def test_force_refetches(tmp_path: Path) -> None:
    counter: dict = {}
    payload = {"league_id": "X", "season": "2024", "previous_league_id": None}
    with _counted_client(tmp_path, payload, counter) as client:
        client.get_league("X")
        client.get_league("X", force=True)
    assert counter["n"] == 2


def test_404_returns_none(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.sleeper.app/v1",
    )
    with SleeperClient(cache_dir=tmp_path, http=http) as client:
        result = client.get_league("missing")
    assert result is None
