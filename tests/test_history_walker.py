"""Unit tests for the previous_league_id walker."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from lddl.clients.sleeper import SleeperClient
from lddl.ingest.league_history import walk_history


def _mock_transport(leagues: dict[str, dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        # request.url.path looks like "/v1/league/<id>"
        league_id = request.url.path.rsplit("/", 1)[-1]
        if league_id not in leagues:
            return httpx.Response(404)
        return httpx.Response(200, json=leagues[league_id])

    return httpx.MockTransport(handler)


def _client(tmp_path: Path, leagues: dict[str, dict]) -> SleeperClient:
    transport = _mock_transport(leagues)
    http = httpx.Client(transport=transport, base_url="https://api.sleeper.app/v1")
    return SleeperClient(cache_dir=tmp_path, http=http)


def test_walks_chain_oldest_first(tmp_path: Path) -> None:
    leagues = {
        "C": {"league_id": "C", "season": "2026", "previous_league_id": "B"},
        "B": {"league_id": "B", "season": "2025", "previous_league_id": "A"},
        "A": {"league_id": "A", "season": "2024", "previous_league_id": None},
    }
    with _client(tmp_path, leagues) as client:
        chain = walk_history(client, "C", force_head=True)
    assert [c["season"] for c in chain] == ["2024", "2025", "2026"]


def test_single_season(tmp_path: Path) -> None:
    leagues = {
        "X": {"league_id": "X", "season": "2024", "previous_league_id": None},
    }
    with _client(tmp_path, leagues) as client:
        chain = walk_history(client, "X")
    assert len(chain) == 1
    assert chain[0]["league_id"] == "X"


def test_cycle_detected(tmp_path: Path) -> None:
    leagues = {
        "A": {"league_id": "A", "season": "2024", "previous_league_id": "B"},
        "B": {"league_id": "B", "season": "2023", "previous_league_id": "A"},
    }
    with _client(tmp_path, leagues) as client:
        with pytest.raises(RuntimeError, match="cycle"):
            walk_history(client, "A")


def test_missing_league_raises(tmp_path: Path) -> None:
    with _client(tmp_path, {}) as client:
        with pytest.raises(RuntimeError, match="not found"):
            walk_history(client, "missing")


def test_non_head_uses_cache(tmp_path: Path) -> None:
    """Walking a chain twice should only re-hit the head; older seasons cached."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        league_id = request.url.path.rsplit("/", 1)[-1]
        leagues = {
            "C": {"league_id": "C", "season": "2026", "previous_league_id": "B"},
            "B": {"league_id": "B", "season": "2025", "previous_league_id": None},
        }
        return httpx.Response(200, json=leagues[league_id])

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://api.sleeper.app/v1")
    with SleeperClient(cache_dir=tmp_path, http=http) as client:
        walk_history(client, "C", force_head=True)
        first = call_count["n"]
        walk_history(client, "C", force_head=True)
        second = call_count["n"]
    # First walk: 2 calls (C, B). Second walk: head re-fetched but B cached → +1.
    assert first == 2
    assert second == 3
