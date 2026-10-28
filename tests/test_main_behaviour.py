"""End-to-end behaviour of the new free-text shortcut and the LLM tool."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("astrbot")

from astrbot.api import ToolSet  # noqa: E402
from astrbot.api.message_components import Image  # noqa: E402
from astrbot.core.provider.register import llm_tools  # noqa: E402

from astrbot_plugin_wowsguess import core, llm_tool  # noqa: E402
from astrbot_plugin_wowsguess import main as plugin_main  # noqa: E402
from astrbot_plugin_wowsguess.catalog import Catalogue  # noqa: E402
from astrbot_plugin_wowsguess.render_service import RenderedCard  # noqa: E402


SHIPS = [
    {
        "index": "PZSC719",
        "name": "Tianjin",
        "nameZh": "天津",
        "aliasZh": "津海",
        "attrs": {"level": 10, "nation": "Pan_Asia"},
    },
    {
        "index": "PBSB609",
        "name": "Stalingrad",
        "nameZh": "斯大林格勒",
        "attrs": {"level": 10, "nation": "Russia"},
    },
    {"index": "PASB001", "name": "Alabama", "nameZh": "阿拉巴马", "attrs": {"level": 8, "nation": "USA"}},
]
ATTRIBUTES = {
    "attributes": [
        {"key": "level", "label": "等级", "kind": "ordered", "close": {"abs": 1}, "unit": "级", "decimals": 0},
        {
            "key": "nation",
            "label": "国家",
            "kind": "discrete",
            "labels": {"Pan_Asia": "泛亚", "Russia": "苏联", "USA": "美国"},
        },
    ]
}
POOL = {"attributeKeys": ["level", "nation"]}


def make_catalogue() -> Catalogue:
    return Catalogue(SHIPS, ATTRIBUTES, {})


class FakeGames:
    """In-memory stand-in for GameService; keeps the real core.evaluate_guess semantics."""

    def __init__(self, answer_index: str = "PBSB609", playing: bool = True) -> None:
        self.state = {
            "difficulty": "standard",
            "tierRange": [8, 10],
            "answerIndex": answer_index,
            "guesses": [],
            "players": [],
            "status": "playing" if playing else "won",
        }
        self.started: list[tuple[str, tuple[int, int] | None, bool]] = []

    async def current(self, session_id: str):
        return dict(self.state) if self.state else None

    async def guess(self, session_id: str, ship: dict, catalogue: Catalogue, *, player: str = ""):
        if self.state["status"] != "playing":
            raise ValueError("本局已经结束，请发送 `/猜船 开始` 新开一局。")
        if ship["index"] in self.state["guesses"]:
            raise ValueError("这艘船已经猜过了，换一艘试试。")
        answer = catalogue.ship(self.state["answerIndex"])
        row = core.evaluate_guess(ship, answer, POOL, catalogue.definitions)
        row["attempt"] = len(self.state["guesses"]) + 1
        self.state["guesses"].append(ship["index"])
        self.state["players"].append(player)
        if row["correct"]:
            self.state["status"] = "won"
        elif len(self.state["guesses"]) >= plugin_main.MAX_ATTEMPTS:
            self.state["status"] = "lost"
        return dict(self.state), row, answer

    async def start(self, session_id: str, difficulty: str, catalogue: Catalogue, **kwargs):
        self.started.append((difficulty, kwargs.get("tier_range"), kwargs.get("require_finish", False)))
        self.state = {
            "difficulty": difficulty,
            "tierRange": list(kwargs["tier_range"]) if kwargs.get("tier_range") else None,
            "answerIndex": "PBSB609",
            "guesses": [],
            "players": [],
            "status": "playing",
        }
        return dict(self.state)


class FakeEvent:
    """Only the AstrMessageEvent surface used by the plugin."""

    def __init__(
        self,
        text: str,
        *,
        umo: str = "aiocqhttp:group:1",
        session_id: str = "group:1",
        group_id: str = "1",
        admin: bool = False,
        at: bool = True,
    ) -> None:
        self.message_str = text
        self.unified_msg_origin = umo
        self.message_obj = SimpleNamespace(session_id=session_id, group_id=group_id)
        self.sent = []
        self._admin = admin
        self.is_at_or_wake_command = at
        self.stopped = False

    def stop_event(self) -> None:
        self.stopped = True

    def is_admin(self) -> bool:
        return self._admin

    def get_sender_name(self) -> str:
        return "测试玩家"

    def plain_result(self, text: str):
        return ("text", text)

    def image_result(self, url: str):
        return ("image", url)

    def chain_result(self, chain):
        return ("chain", chain)

    async def send(self, result) -> None:
        self.sent.append(result)


class FakeRendering:
    """In-memory stand-in for CardRenderService (no Chromium in unit tests)."""

    def __init__(self, card=None, error: Exception | None = None) -> None:
        self.card = card
        self.error = error
        self.calls: list[str] = []

    async def render(self, html: str):
        self.calls.append(html)
        if self.error:
            raise self.error
        return self.card


def make_plugin(*, config: dict | None = None, games: FakeGames | None = None):
    plugin = object.__new__(plugin_main.WowsGuessPlugin)
    plugin.config = {
        "render_cards": False,
        "free_text_guess": True,
        "group_guess_without_at": True,
        "llm_tool_enabled": True,
        "llm_tool_cards_only": True,
    }
    plugin.config.update(config or {})
    plugin.games = games or FakeGames()
    plugin.rendering = FakeRendering(RenderedCard(backend="local", image=b"CARD"))
    plugin._catalogue = make_catalogue()
    plugin._ready_lock = asyncio.Lock()
    plugin._last_tool_use = {}
    return plugin


async def collect(gen):
    return [item async for item in gen]


def run(coro):
    return asyncio.run(coro)


# ---- free-text shortcut ----------------------------------------------------


@pytest.mark.parametrize("text", ["天津", " 天津 ", "猜天津", "我猜天津", "天津号", "天津。"])
def test_free_text_guess_accepts_short_forms(text):
    async def scenario():
        plugin = make_plugin()
        event = FakeEvent(text)
        results = await collect(plugin.free_text_guess(event))
        assert len(results) == 1
        assert results[0][0] == "text" and f"第 1/{plugin_main.MAX_ATTEMPTS} 次：天津" in results[0][1]
        assert plugin.games.state["guesses"] == ["PZSC719"]
        assert event.stopped is True

    run(scenario())


@pytest.mark.parametrize("text", ["猜船 猜 天津", "/猜船 开始", "今天天气不错", "胡德好强啊", "斯大林"])
def test_free_text_guess_leaves_other_messages_alone(text):
    async def scenario():
        plugin = make_plugin()
        assert await collect(plugin.free_text_guess(FakeEvent(text))) == []
        assert plugin.games.state["guesses"] == []

    run(scenario())


def test_free_text_guess_requires_a_running_game():
    async def scenario():
        plugin = make_plugin(games=FakeGames(playing=False))
        assert await collect(plugin.free_text_guess(FakeEvent("天津"))) == []

    run(scenario())


def test_free_text_guess_reports_duplicates_in_chinese():
    async def scenario():
        plugin = make_plugin()
        assert await collect(plugin.free_text_guess(FakeEvent("天津")))
        duplicate = FakeEvent("天津")
        results = await collect(plugin.free_text_guess(duplicate))
        assert len(results) == 1 and "已经猜过" in results[0][1]
        assert duplicate.stopped is False

    run(scenario())


def test_at_free_text_guess_stops_event_after_success():
    async def scenario():
        plugin = make_plugin()
        event = FakeEvent("天津", at=True)
        results = await collect(plugin.free_text_guess(event))
        assert len(results) == 1
        assert event.stopped is True

    run(scenario())


def test_free_text_can_be_disabled_by_config():
    async def scenario():
        plugin = make_plugin(config={"free_text_guess": False})
        assert await collect(plugin.free_text_guess(FakeEvent("天津"))) == []

    run(scenario())


# ---- group guesses without @ (v1.7) ----------------------------------------


def test_group_guess_without_at_accepts_exact_and_explicit_names():
    async def scenario():
        plugin = make_plugin()
        # 普通聊天、状态/放弃等指令、带后缀的玩笑叫法都不接管
        for text in ("今天吃什么", "放弃", "天津号", "胡德好强啊"):
            assert await collect(plugin.free_text_guess(FakeEvent(text, at=False))) == []
        assert plugin.games.state["guesses"] == []

        quiet = FakeEvent("天津", at=False)
        results = await collect(plugin.free_text_guess(quiet))
        assert len(results) == 1 and f"第 1/{plugin_main.MAX_ATTEMPTS} 次：天津" in results[0][1]
        assert quiet.stopped is True
        assert plugin.games.state["guesses"] == ["PZSC719"]

        # 重复舰名在没有 @ 时静默忽略，不再回错误文本
        duplicate = FakeEvent("天津", at=False)
        assert await collect(plugin.free_text_guess(duplicate)) == []
        assert duplicate.stopped is False

        # “我猜X”是明确猜测，允许
        results = await collect(plugin.free_text_guess(FakeEvent("我猜斯大林格勒", at=False)))
        assert len(results) == 1 and "猜中了" in results[0][1]

    run(scenario())


def test_group_guess_without_at_can_be_disabled():
    async def scenario():
        plugin = make_plugin(config={"group_guess_without_at": False})
        assert await collect(plugin.free_text_guess(FakeEvent("天津", at=False))) == []
        assert plugin.games.state["guesses"] == []

        # @ 过之后仍可猜
        results = await collect(plugin.free_text_guess(FakeEvent("天津", at=True)))
        assert len(results) == 1

    run(scenario())


# ---- command handler still works through the same actions ------------------


def test_command_start_uses_group_rule():
    async def scenario():
        plugin = make_plugin()
        event = FakeEvent("猜船 开始 困难 8-10")
        results = await collect(plugin.wows_guess(event))
        assert "已开始困难猜船（VIII-X级）" in results[0][1]
        assert plugin.games.started == [("hard", (8, 10), True)]

    run(scenario())


def test_command_status_and_guess():
    async def scenario():
        plugin = make_plugin()
        status = await collect(plugin.wows_guess(FakeEvent("猜船 状态")))
        assert "难度：普通（答案限定 VIII-X级）" in status[0][1]
        assert "已猜：暂无" in status[0][1]
        guess = await collect(plugin.wows_guess(FakeEvent("猜船 猜 天津")))
        assert f"第 1/{plugin_main.MAX_ATTEMPTS} 次：天津" in guess[0][1]

    run(scenario())


def test_command_renders_local_card_when_enabled():
    async def scenario():
        plugin = make_plugin(config={"render_cards": True})
        plugin.rendering = FakeRendering(RenderedCard(backend="local", image=b"CARD"))

        results = await collect(plugin.wows_guess(FakeEvent("猜船 猜 天津")))
        assert results[0][0] == "chain"
        assert isinstance(results[0][1][0], Image)
        assert plugin.rendering.calls and "board" in plugin.rendering.calls[0]

    run(scenario())


def test_command_renders_remote_card_when_local_unavailable():
    async def scenario():
        plugin = make_plugin(config={"render_cards": True})
        plugin.rendering = FakeRendering(
            RenderedCard(backend="remote", image_url="https://example.test/card.png")
        )

        results = await collect(plugin.wows_guess(FakeEvent("猜船 猜 天津")))
        assert results == [("image", "https://example.test/card.png")]

    run(scenario())


def test_command_falls_back_to_text_when_every_backend_fails():
    async def scenario():
        plugin = make_plugin(config={"render_cards": True})
        plugin.rendering = FakeRendering(None)

        results = await collect(plugin.wows_guess(FakeEvent("猜船 猜 天津")))
        assert results[0][0] == "text" and "第 1/" in results[0][1]

    run(scenario())


def test_command_falls_back_to_text_when_rendering_raises():
    async def scenario():
        plugin = make_plugin(config={"render_cards": True})
        plugin.rendering = FakeRendering(error=RuntimeError("boom"))

        results = await collect(plugin.wows_guess(FakeEvent("猜船 猜 天津")))
        assert results[0][0] == "text" and "第 1/" in results[0][1]

    run(scenario())


def test_help_renders_local_card_when_enabled():
    async def scenario():
        plugin = make_plugin(config={"render_cards": True})
        plugin.rendering = FakeRendering(RenderedCard(backend="local", image=b"HELP"))

        results = await collect(plugin.wows_guess(FakeEvent("猜船")))
        assert results[0][0] == "chain" and isinstance(results[0][1][0], Image)

    run(scenario())


def test_status_renders_card_when_enabled():
    async def scenario():
        plugin = make_plugin(config={"render_cards": True})
        plugin.rendering = FakeRendering(RenderedCard(backend="local", image=b"STATUS"))

        results = await collect(plugin.wows_guess(FakeEvent("猜船 状态")))
        assert results[0][0] == "chain" and isinstance(results[0][1][0], Image)
        assert plugin.rendering.calls and "本局状态" in plugin.rendering.calls[0]

    run(scenario())


def test_feedback_card_includes_history_and_player():
    async def scenario():
        plugin = make_plugin(config={"render_cards": True})
        plugin.rendering = FakeRendering(RenderedCard(backend="local", image=b"CARD"))

        await collect(plugin.wows_guess(FakeEvent("猜船 猜 天津")))
        await collect(plugin.wows_guess(FakeEvent("猜船 猜 斯大林格勒")))
        html = plugin.rendering.calls[-1]
        assert "天津" in html and "斯大林格勒" in html
        assert "测试玩家" in html
        assert "#1" in html and "#2" in html

    run(scenario())


# ---- LLM tool --------------------------------------------------------------


def test_tool_registered_with_full_schema():
    tool = llm_tools.get_func(llm_tool.NAME)
    assert tool is not None
    assert tool.parameters == llm_tool.SCHEMA
    make_plugin()._verify_llm_tools()


def test_tool_returns_json_result():
    async def scenario():
        plugin = make_plugin()
        event = FakeEvent("帮我猜天津")
        raw = await plugin.wows_guess_tool(event, action="猜", ship="天津")
        payload = json.loads(raw)
        assert payload["status"] == "ok"
        assert f"第 1/{plugin_main.MAX_ATTEMPTS} 次：天津" in payload["text"]
        assert payload["card_sent_to_user"] is False
        assert event.sent == []

    run(scenario())


def test_tool_cards_only_ends_turn_with_local_card():
    async def scenario():
        plugin = make_plugin(config={"render_cards": True, "llm_tool_cards_only": True})
        plugin.rendering = FakeRendering(RenderedCard(backend="local", image=b"CARD"))

        event = FakeEvent("帮我猜天津")
        raw = await plugin.wows_guess_tool(event, action="猜", ship="天津")
        assert raw is None
        assert event.sent[0][0] == "chain"
        assert isinstance(event.sent[0][1][0], Image)

    run(scenario())


def test_tool_cards_only_disabled_returns_json_and_sends_card():
    async def scenario():
        plugin = make_plugin(config={"render_cards": True, "llm_tool_cards_only": False})
        plugin.rendering = FakeRendering(RenderedCard(backend="local", image=b"CARD"))

        event = FakeEvent("帮我猜天津")
        raw = await plugin.wows_guess_tool(event, action="猜", ship="天津")
        payload = json.loads(raw)
        assert payload["status"] == "ok" and payload["card_sent_to_user"] is True
        assert event.sent and event.sent[0][0] == "chain"

    run(scenario())


def test_tool_reports_invalid_arguments():
    async def scenario():
        raw = await make_plugin().wows_guess_tool(FakeEvent("猜一下"), action="猜")
        payload = json.loads(raw)
        assert payload["status"] == "invalid" and "ship" in payload["message"]

    run(scenario())


def test_tool_status_without_game_is_an_error():
    async def scenario():
        games = FakeGames(playing=False)
        games.state = None
        raw = await make_plugin(games=games).wows_guess_tool(FakeEvent("状态"), action="状态")
        payload = json.loads(raw)
        assert payload["status"] == "error" and "还没有游戏" in payload["message"]

    run(scenario())


def test_tool_offered_on_demand():
    async def scenario():
        tool = llm_tools.get_func(llm_tool.NAME)

        def request():
            return SimpleNamespace(
                func_tool=ToolSet([tool]),
                system_prompt="",
                extra_user_content_parts=[],
            )

        # no game and unrelated message -> tool removed
        idle = make_plugin(games=FakeGames(playing=False))
        unrelated = request()
        await idle._offer_guess_tool(FakeEvent("今天天气不错"), unrelated)
        assert unrelated.func_tool.get_tool(llm_tool.NAME) is None
        assert unrelated.system_prompt == ""

        # active game + message mentioning the game -> tool kept and prompt hint injected
        plugin = make_plugin()
        relevant = request()
        await plugin._offer_guess_tool(FakeEvent("帮我猜胡德"), relevant)
        assert relevant.func_tool.get_tool(llm_tool.NAME) is not None
        assert relevant.system_prompt == ""  # 不改 system_prompt，保护提供方缓存
        if plugin_main._TextPart is not None:
            assert any(
                getattr(part, "text", None) == plugin_main.PLAYING_SYSTEM_PROMPT
                for part in relevant.extra_user_content_parts
            )

        # active game but a long unrelated sentence -> tool removed again
        long_text = "今天群里聊点别的事情吧，比如晚饭吃什么或者周末去哪里玩比较好"
        assert len(long_text) > plugin_main.FREE_TEXT_MAX_LENGTH
        long_request = request()
        await plugin._offer_guess_tool(FakeEvent(long_text), long_request)
        assert long_request.func_tool.get_tool(llm_tool.NAME) is None

    run(scenario())


def test_playing_prompt_uses_temporary_content_part():
    if plugin_main._TextPart is None:
        pytest.skip("this AstrBot build has no extra user content parts")

    async def scenario():
        plugin = make_plugin()
        req = SimpleNamespace(
            func_tool=ToolSet([llm_tools.get_func(llm_tool.NAME)]),
            system_prompt="BASE",
            extra_user_content_parts=[],
        )
        await plugin._offer_guess_tool(FakeEvent("我猜一下"), req)

        # 系统提示词保持逐字节不变（提供方前缀缓存不被破坏）
        assert req.system_prompt == "BASE"
        assert len(req.extra_user_content_parts) == 1
        part = req.extra_user_content_parts[0]
        assert part.text == plugin_main.PLAYING_SYSTEM_PROMPT
        assert part.model_dump_for_context().get("_no_save") is True

        # 重复调用不叠加同一句提示
        await plugin._offer_guess_tool(FakeEvent("我猜一下"), req)
        assert len(req.extra_user_content_parts) == 1

    run(scenario())


def test_tool_disabled_by_config_is_removed():
    async def scenario():
        plugin = make_plugin(config={"llm_tool_enabled": False})
        req = SimpleNamespace(func_tool=ToolSet([llm_tools.get_func(llm_tool.NAME)]), system_prompt="")
        await plugin._offer_guess_tool(FakeEvent("帮我猜胡德"), req)
        assert req.func_tool.get_tool(llm_tool.NAME) is None

    run(scenario())


class FakeStore:
    """Stand-in for WowsGuessDataStore used by the refresh command tests."""

    def __init__(self, ok: bool) -> None:
        self.last_refresh_ok = ok
        self.calls = 0

    async def get_catalogue(self, refresh: bool = False):
        self.calls += 1
        return make_catalogue()


def test_refresh_command_reports_success():
    async def scenario():
        plugin = make_plugin()
        plugin.store = FakeStore(ok=True)
        plugin._catalogue = None
        results = await collect(plugin.wows_guess(FakeEvent("猜船 刷新数据", admin=True)))
        assert results[0][0] == "text" and "题库已刷新" in results[0][1]
        assert plugin.store.calls == 1

    run(scenario())


def test_refresh_command_reports_failure_and_keeps_cache():
    async def scenario():
        plugin = make_plugin()
        plugin.store = FakeStore(ok=False)
        results = await collect(plugin.wows_guess(FakeEvent("猜船 刷新数据", admin=True)))
        assert results[0][0] == "text"
        assert "刷新失败" in results[0][1] and "缓存" in results[0][1]

    run(scenario())
