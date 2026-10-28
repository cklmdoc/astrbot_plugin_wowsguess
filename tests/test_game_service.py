"""GameService attempt limit: a round lasts 15 guesses, then it is lost."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_wowsguess.catalog import Catalogue  # noqa: E402
from astrbot_plugin_wowsguess.game_service import GameService  # noqa: E402

SHIPS = [{"index": "ANS", "name": "Answer", "nameZh": "答案舰", "attrs": {"level": 10, "nation": "Japan"}}]
SHIPS += [
    {
        "index": f"G{i:02d}",
        "name": f"Guess{i}",
        "nameZh": f"猜测{i}号",
        "attrs": {"level": 8, "nation": "USA"},
    }
    for i in range(1, 17)
]
ATTRIBUTES = {
    "attributes": [
        {"key": "level", "label": "等级", "kind": "ordered", "close": {"abs": 1}, "unit": "级", "decimals": 0},
        {"key": "nation", "label": "国家", "kind": "discrete", "labels": {"Japan": "日本", "USA": "美国"}},
    ]
}
POOL = {"answers": ["ANS"], "attributeKeys": ["level", "nation"], "groupOf": {}}


def make_catalogue() -> Catalogue:
    return Catalogue(SHIPS, ATTRIBUTES, {"standard": POOL, "hard": POOL})


def test_default_max_attempts_is_fifteen(tmp_path):
    assert GameService(tmp_path).max_attempts == 15


def test_plugin_constant_is_fifteen():
    pytest.importorskip("astrbot")
    from astrbot_plugin_wowsguess.main import MAX_ATTEMPTS

    assert MAX_ATTEMPTS == 15


def test_fifteen_guesses_then_loss(tmp_path):
    async def scenario():
        catalogue = make_catalogue()
        service = GameService(tmp_path, max_attempts=15)
        state = await service.start("session", "standard", catalogue)
        answer_index = state["answerIndex"]
        wrongs = [ship for ship in catalogue.ships if ship["index"] != answer_index]

        for index in range(15):
            updated, row, answer = await service.guess("session", wrongs[index], catalogue)
            assert row["attempt"] == index + 1
            assert answer["index"] == answer_index
            assert updated["status"] == ("lost" if index == 14 else "playing")

        # the 15th wrong guess ends the round; a 16th is refused
        with pytest.raises(ValueError):
            await service.guess("session", wrongs[15], catalogue)

    asyncio.run(scenario())


def test_guess_persists_player_names(tmp_path):
    async def scenario():
        catalogue = make_catalogue()
        service = GameService(tmp_path)
        state = await service.start("session", "standard", catalogue)
        wrong = next(ship for ship in catalogue.ships if ship["index"] != state["answerIndex"])

        updated, _, _ = await service.guess("session", wrong, catalogue, player="玩家A")
        assert updated["players"] == ["玩家A"]
        reloaded = await service.current("session")
        assert reloaded is not None and reloaded["players"] == ["玩家A"]

    asyncio.run(scenario())


def test_guess_realigns_short_player_lists(tmp_path):
    async def scenario():
        catalogue = make_catalogue()
        service = GameService(tmp_path)
        state = await service.start("session", "standard", catalogue)
        answer_index = state["answerIndex"]
        wrong = next(ship for ship in catalogue.ships if ship["index"] != answer_index)

        # Simulate a save from an older version with guesses but no player entries.
        path = service._path("session")
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["guesses"] = [wrong["index"]]
        stored["players"] = []
        path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")

        another = next(
            ship
            for ship in catalogue.ships
            if ship["index"] not in {answer_index, wrong["index"]}
        )
        updated, row, _ = await service.guess("session", another, catalogue, player="玩家B")
        assert row["attempt"] == 2
        assert len(updated["guesses"]) == 2
        assert updated["players"] == ["", "玩家B"]

    asyncio.run(scenario())
