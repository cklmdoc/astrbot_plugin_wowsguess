"""Hardening tests for the catalogue download/cache layer (v1.7.2)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import aiohttp
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_wowsguess.data_store import (  # noqa: E402
    MAX_FILE_BYTES,
    DataUnavailableError,
    WowsGuessDataStore,
)

SHIP = {"index": "AAA", "name": "Alpha", "nameZh": "甲", "attrs": {"level": 10, "nation": "X"}}
ATTRIBUTES = {
    "attributes": [
        {"key": "level", "kind": "ordered", "close": {"abs": 1}},
        {"key": "nation", "kind": "discrete"},
    ]
}
POOL = {"answers": ["AAA"], "attributeKeys": ["level", "nation"], "groupOf": {}}


def write_cache(root: Path, *, answers: list | None = None, ships: list | None = None) -> Path:
    cache = root / "woyiba_data"
    (cache / "pools").mkdir(parents=True, exist_ok=True)
    (cache / "ships.json").write_text(json.dumps({"ships": ships or [SHIP]}), encoding="utf-8")
    (cache / "attributes.json").write_text(json.dumps(ATTRIBUTES), encoding="utf-8")
    (cache / "difficulties.json").write_text(
        json.dumps({"difficulties": [{"key": "standard"}]}), encoding="utf-8"
    )
    for difficulty in ("standard", "hard"):
        (cache / "pools" / f"{difficulty}.json").write_text(
            json.dumps({**POOL, "answers": answers or ["AAA"]}), encoding="utf-8"
        )
    return cache


def run(coro):
    return asyncio.run(coro)


# ---- cache usability -------------------------------------------------------
def test_has_usable_cache_accepts_complete_payload(tmp_path):
    write_cache(tmp_path)
    assert WowsGuessDataStore(tmp_path).has_usable_cache() is True


def test_missing_difficulties_file_invalidates_cache(tmp_path):
    write_cache(tmp_path)
    (tmp_path / "woyiba_data" / "difficulties.json").unlink()
    assert WowsGuessDataStore(tmp_path).has_usable_cache() is False


def test_answer_outside_ship_list_invalidates_cache(tmp_path):
    write_cache(tmp_path, answers=["NOPE"])
    assert WowsGuessDataStore(tmp_path).has_usable_cache() is False


def test_ship_without_index_invalidates_cache(tmp_path):
    write_cache(tmp_path, ships=[{"name": "No Index", "nameZh": "无编号", "attrs": {}}])
    assert WowsGuessDataStore(tmp_path).has_usable_cache() is False


# ---- refresh behaviour -----------------------------------------------------
def test_get_catalogue_loads_cache_without_network(tmp_path, monkeypatch):
    write_cache(tmp_path)
    store = WowsGuessDataStore(tmp_path)

    async def unexpected(*args, **kwargs):
        raise AssertionError("network refresh must not run when cache is usable")

    monkeypatch.setattr(store, "_refresh_cache", unexpected)
    catalogue = run(store.get_catalogue())
    assert [ship["index"] for ship in catalogue.ships] == ["AAA"]


def test_refresh_failure_falls_back_to_usable_cache(tmp_path, monkeypatch):
    write_cache(tmp_path)
    store = WowsGuessDataStore(tmp_path)

    async def failing() -> None:
        raise aiohttp.ClientError("offline")

    monkeypatch.setattr(store, "_refresh_cache", failing)
    catalogue = run(store.get_catalogue(refresh=True))
    assert [ship["index"] for ship in catalogue.ships] == ["AAA"]
    assert store.last_refresh_ok is False


def test_no_cache_and_refresh_failure_raises(tmp_path, monkeypatch):
    store = WowsGuessDataStore(tmp_path)

    async def failing() -> None:
        raise aiohttp.ClientError("offline")

    monkeypatch.setattr(store, "_refresh_cache", failing)
    with pytest.raises(DataUnavailableError):
        run(store.get_catalogue(refresh=True))


def test_oserror_during_refresh_becomes_data_error(tmp_path, monkeypatch):
    store = WowsGuessDataStore(tmp_path)

    async def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_download_all", broken)
    with pytest.raises(DataUnavailableError):
        run(store._refresh_cache())


# ---- download guards -------------------------------------------------------
class FakeContent:
    def __init__(self, chunks) -> None:
        self._chunks = list(chunks)

    async def iter_chunked(self, size):
        for chunk in self._chunks:
            yield chunk


class FakeResponse:
    def __init__(self, chunks=(), content_length=None) -> None:
        self.content = FakeContent(chunks)
        self.content_length = content_length


def test_read_limited_accepts_small_payload():
    payload = run(WowsGuessDataStore._read_limited(FakeResponse([b"{}"]), "ships.json"))
    assert payload == b"{}"


def test_read_limited_rejects_declared_oversize():
    with pytest.raises(DataUnavailableError):
        run(
            WowsGuessDataStore._read_limited(
                FakeResponse(content_length=MAX_FILE_BYTES + 1), "ships.json"
            )
        )


def test_read_limited_rejects_streamed_oversize():
    with pytest.raises(DataUnavailableError):
        run(
            WowsGuessDataStore._read_limited(
                FakeResponse([b"x" * (MAX_FILE_BYTES + 1)]), "ships.json"
            )
        )


def test_read_limited_rejects_empty_payload():
    with pytest.raises(DataUnavailableError):
        run(WowsGuessDataStore._read_limited(FakeResponse(), "ships.json"))


@pytest.mark.parametrize(
    "url",
    [
        "https://www.woyiba.com/data/ships.json",
        "https://woyiba.com/data/ships.json",
    ],
)
def test_download_url_allows_https_woyiba(url):
    WowsGuessDataStore._check_download_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.woyiba.com/data/ships.json",
        "https://evil.example/data/ships.json",
        "https://www.woyiba.com.evil.example/data/ships.json",
    ],
)
def test_download_url_rejects_untrusted_targets(url):
    with pytest.raises(DataUnavailableError):
        WowsGuessDataStore._check_download_url(url)
