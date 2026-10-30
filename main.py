"""AstrBot chat plugin for the WowsGuess-style ship guessing game."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.message_components import Image
from astrbot.api.provider import ProviderRequest
from astrbot.core.provider.register import llm_tools  # global registry needed by _tool_schema

try:  # AstrBot content parts: inject temp hints without touching the cached system prompt
    from astrbot.core.agent.message import TextPart as _TextPart
except ImportError:  # pragma: no cover - older AstrBot builds
    _TextPart = None

from . import llm_tool
from .catalog import Catalogue
from .card_renderer import (
    build_guess_card_html,
    build_help_card_html,
    build_status_card_html,
    make_guess_card_view,
    make_status_card_view,
)
from .core import evaluate_guess
from .data_store import DataUnavailableError, WowsGuessDataStore
from .game_service import GameService
from .render_service import BACKEND_AUTO, CardRenderService, RenderedCard, RenderSettings
from .renderer import LocalCardRenderer, LocalRenderSettings
from .tier import format_tier_range, parse_start_options


PLUGIN_NAME = "astrbot_plugin_wowsguess"
MAX_ATTEMPTS = 15
FOLLOW_UP_SECONDS = 600
FREE_TEXT_MAX_LENGTH = 24
GUESS_TOPIC = re.compile(
    r"猜|船名|舰船|战舰世界|窝一把|开始|状态|还剩|剩几|次数|放弃|帮助|提示|答案|难度|等级"
)
FREE_TEXT_PREFIXES = ("帮我猜一下", "帮我猜", "我猜", "猜猜看", "猜一下", "猜猜", "猜")
FREE_TEXT_TRAILING = ("号", "舰")
# 群聊未 @ 时只认“明确在猜”的短句：整句舰名，或“我猜X”这类前缀；普通闲聊不接管。
QUIET_GUESS_PREFIXES = ("我猜是", "我猜", "猜是", "猜一下", "猜")
COMMAND_ACTIONS = {
    "": "help",
    "帮助": "help",
    "help": "help",
    "开始": "start",
    "状态": "status",
    "放弃": "giveup",
    "刷新数据": "refresh",
    "刷新": "refresh",
    "猜": "guess",
}
PLAYING_SYSTEM_PROMPT = (
    "\n\n【猜船游戏】当前会话有一局猜船游戏正在进行。用户只发一个舰船名、"
    "或让你猜某个舰名时，调用 wows_guess 工具（action=\"猜\", ship=用户说的舰名），"
    "不要自己编造猜测结果或属性反馈。用户问进度用 action=\"状态\"，想结束用 action=\"放弃\"。"
)


@dataclass
class ActionResult:
    """One user-facing reply: text always, plus an optional rendered card image."""

    text: str
    image: bytes | None = None
    image_url: str | None = None
    error: bool = False

    def has_image(self) -> bool:
        return bool(self.image or self.image_url)


def _mark_doc(text: str):
    """llm_tool reads the tool description from the docstring at decoration time."""

    def decorator(func):
        func.__doc__ = text
        return func

    return decorator


def _patch_tool_parameters(name: str, parameters: dict[str, Any]) -> bool:
    """Best-effort patch of the registered tool schema.

    ``llm_tool`` builds parameters from the docstring, which cannot express enums or
    required fields; the registry layout is private to AstrBot, so keep the patch
    defensive and let ``_verify_llm_tools`` report a mismatch instead of crashing.
    """

    try:
        for tool in reversed(llm_tools.func_list):
            if tool.name == name:
                tool.parameters = parameters
                return True
    except Exception:  # noqa: BLE001 - private registry shape may change between versions
        return False
    return False


def _tool_schema(name: str, parameters: dict[str, Any]):
    """Docstrings only express plain types; swap in the full JSON schema (enums, required)."""

    def decorator(func):
        _patch_tool_parameters(name, parameters)
        return func

    return decorator


def _temp_text_part(text: str) -> Any | None:
    """Wrap ``text`` in a provider-only content part that is never saved to history."""
    if _TextPart is None:  # pragma: no cover - old AstrBot without content parts
        return None
    part = _TextPart(text=text)
    mark_as_temp = getattr(part, "mark_as_temp", None)
    if callable(mark_as_temp):
        part = mark_as_temp()
    return part


class WowsGuessPlugin(Star):
    """Play an endless WowsGuess-style round in every chat session."""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.store = WowsGuessDataStore(self.data_dir)
        self.games = GameService(self.data_dir, max_attempts=MAX_ATTEMPTS)
        self._catalogue: Catalogue | None = None
        self._ready_lock = asyncio.Lock()
        self._last_tool_use: dict[str, float] = {}
        self._refresh_task: asyncio.Task[Any] | None = None
        self.rendering = CardRenderService(
            self._build_render_settings(),
            local=self._build_local_renderer(),
            remote=self._remote_render,
            logger=logger,
        )

    async def initialize(self) -> None:
        """Warm the local cache without preventing plugin load if the site is down."""
        self._verify_llm_tools()
        logger.info("WowsGuess 卡片渲染：%s", self.rendering.describe())
        if not self.config.get("refresh_on_start", True):
            return
        if self.store.has_usable_cache():
            # Serve the existing cache immediately and refresh it in the background:
            # the download must never block plugin startup or other sessions.
            self._refresh_task = asyncio.create_task(self._refresh_catalogue_in_background())
            logger.info("WowsGuess 题库将在后台刷新，本次启动继续使用本地缓存。")
            return
        try:
            await self._catalogue_or_raise(refresh=True)
            logger.info("WowsGuess public ship catalogue refreshed.")
        except DataUnavailableError as exc:
            logger.warning("WowsGuess catalogue was not refreshed: %s", exc)

    async def _refresh_catalogue_in_background(self) -> None:
        """Refresh the cache after startup without raising on network failures."""
        try:
            catalogue = await self._catalogue_or_raise(refresh=True)
        except DataUnavailableError as exc:
            logger.warning("WowsGuess 题库后台刷新失败，继续使用本地缓存：%s", exc)
            return
        if self.store.last_refresh_ok:
            logger.info("WowsGuess 题库已刷新：%s 艘舰船。", len(catalogue.ships))
        else:
            logger.warning("WowsGuess 题库刷新失败，继续使用本地缓存。")

    async def terminate(self) -> None:
        """Release the local Chromium and stop a pending background refresh."""
        task, self._refresh_task = self._refresh_task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - teardown must not raise
                pass
        await self.rendering.close()

    def _verify_llm_tools(self) -> None:
        """Verify the tool was registered with the full schema; repair it when possible."""
        try:
            tool = llm_tools.get_func(llm_tool.NAME)
        except Exception:  # noqa: BLE001 - private registry shape may change between versions
            tool = None
        if tool is None:
            logger.error(
                "WowsGuess LLM 工具 %s 未注册，请检查 AstrBot 版本兼容性。",
                llm_tool.NAME,
            )
            return
        if tool.parameters == llm_tool.SCHEMA:
            return
        if _patch_tool_parameters(llm_tool.NAME, llm_tool.SCHEMA):
            logger.warning(
                "WowsGuess LLM 工具 %s 的完整 schema 已在启动时重新应用。", llm_tool.NAME
            )
            return
        logger.error(
            "WowsGuess LLM 工具 %s 无法应用完整 schema，请检查 AstrBot 版本兼容性。",
            llm_tool.NAME,
        )

    async def _catalogue_or_raise(self, refresh: bool = False) -> Catalogue:
        async with self._ready_lock:
            if self._catalogue is None or refresh:
                self._catalogue = await self.store.get_catalogue(refresh=refresh)
            return self._catalogue

    @staticmethod
    def _session_id(event: AstrMessageEvent) -> str:
        return str(event.message_obj.session_id or event.unified_msg_origin)

    @staticmethod
    def _is_group_chat(event: AstrMessageEvent) -> bool:
        return bool(getattr(event.message_obj, "group_id", ""))

    @staticmethod
    def _is_admin(event: AstrMessageEvent) -> bool:
        checker = getattr(event, "is_admin", None)
        if callable(checker):
            return bool(checker())
        return getattr(event, "role", "") == "admin"

    @staticmethod
    def _name(ship: dict[str, Any]) -> str:
        return str(ship.get("nameZh") or ship.get("name") or ship["index"])

    @staticmethod
    def _event_field(value: Any, name: str) -> Any:
        """Read a field from adapter payloads that may be mappings or objects."""
        if isinstance(value, dict):
            return value.get(name)
        getter = getattr(value, "get", None)
        if callable(getter):
            try:
                result = getter(name)
            except (KeyError, TypeError):
                result = None
            if result is not None:
                return result
        return getattr(value, name, None)

    @staticmethod
    def _sender(event: AstrMessageEvent) -> str:
        message_obj = getattr(event, "message_obj", None)
        if getattr(message_obj, "group_id", ""):
            raw_message = getattr(message_obj, "raw_message", None)
            raw_sender = WowsGuessPlugin._event_field(raw_message, "sender")
            card = WowsGuessPlugin._event_field(raw_sender, "card")
            if card is None:
                card = WowsGuessPlugin._event_field(
                    getattr(message_obj, "sender", None), "card"
                )
            if card is not None and str(card).strip():
                return str(card).strip()
        getter = getattr(event, "get_sender_name", None)
        return str(getter()).strip() if callable(getter) else ""

    @staticmethod
    def _format_value(definition: dict[str, Any], value: Any) -> str:
        if definition.get("key") == "nation":
            return str(definition.get("labels", {}).get(str(value), value))
        if definition.get("key") == "species":
            return str(definition.get("labels", {}).get(str(value), value))
        if isinstance(value, float):
            decimals = definition.get("decimals")
            if isinstance(decimals, int):
                text = f"{value:.{decimals}f}"
            else:
                text = f"{value:g}"
        else:
            text = str(value)
        unit = str(definition.get("unit") or "")
        return f"{text}{unit}"

    def _format_feedback(self, catalogue: Catalogue, row: dict[str, Any]) -> str:
        symbols = {"exact": "🟩", "close": "🟨", "far": "⬜"}
        fields = []
        for result in row["feedback"]:
            definition = catalogue.definitions[result["key"]]
            label = definition.get("label") or result["key"]
            direction = {"up": "⬆", "down": "⬇", None: ""}[result["direction"]]
            fields.append(
                f"{symbols[result['state']]} {label}={self._format_value(definition, result['guessValue'])}{direction}"
            )
        return "\n".join(fields)

    # ---- card rendering --------------------------------------------------
    # 默认本地 Playwright 渲染（热态约 0.3s），远端 html_render 只作兜底。

    def _text_config(self, key: str, default: str) -> str:
        value = self.config.get(key, default)
        return str(value) if value is not None else default

    def _bool_config(self, key: str, default: bool) -> bool:
        value = self.config.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() not in ("false", "0", "off", "no")
        return bool(value)

    def _int_config(self, key: str, default: int) -> int:
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return default

    def _float_config(self, key: str, default: float) -> float:
        try:
            return float(self.config.get(key, default))
        except (TypeError, ValueError):
            return default

    def _build_render_settings(self) -> RenderSettings:
        return RenderSettings(
            backend=self._text_config("render_backend", BACKEND_AUTO),
            remote_fallback=self._bool_config("remote_render_fallback", True),
            local_timeout=self._float_config("local_render_timeout_seconds", 20.0),
            remote_timeout=self._float_config("render_timeout_seconds", 15.0),
        )

    def _build_local_renderer(self) -> LocalCardRenderer | None:
        if not LocalCardRenderer.is_supported():
            logger.warning("未安装 playwright，WowsGuess 卡片将使用远端 html_render。")
            return None
        image_format = self._text_config("card_image_format", "png")
        renderer = LocalCardRenderer(
            LocalRenderSettings(
                image_format=image_format,
                quality=self._int_config("card_image_quality", 80),
                scale=self._float_config("render_scale", 1.5),
                browser_path=self._text_config("browser_path", ""),
            ),
            logger=logger,
        )
        logger.info("WowsGuess 本地 Playwright 卡片渲染已就绪（%s）。", image_format)
        return renderer

    async def _remote_render(self, html: str) -> str:
        image_format = self._text_config("card_image_format", "png")
        options: dict[str, Any] = {"type": image_format, "full_page": True, "animations": "disabled"}
        if image_format == "jpeg":
            options["quality"] = self._int_config("card_image_quality", 80)
        return await self.html_render(html, {}, options=options)

    async def _render_card(self, html: str) -> RenderedCard | None:
        if not self.config.get("render_cards", True):
            return None
        return await self.rendering.render(html)

    @staticmethod
    def _with_card(text: str, card: RenderedCard | None) -> ActionResult:
        if card is None:
            return ActionResult(text=text)
        return ActionResult(text=text, image=card.image, image_url=card.image_url)

    @staticmethod
    def _image_chain(event: AstrMessageEvent, result: ActionResult):
        if result.image:
            return event.chain_result([Image.fromBytes(result.image)])
        return event.image_result(result.image_url or "")

    def _history_rows(
        self,
        catalogue: Catalogue,
        state: dict[str, Any],
        *,
        limit: int = 5,
    ) -> tuple[list[dict[str, Any]], int]:
        """Rebuild the guess history shown under the current guess."""
        guesses = list(state.get("guesses") or [])
        answer = catalogue.ship(str(state.get("answerIndex")))
        difficulty = str(state.get("difficulty"))
        pool_difficulty = "standard" if difficulty == "random" else difficulty
        pool = catalogue.pool(pool_difficulty) or {}
        if not pool.get("attributeKeys"):
            pool = {"attributeKeys": list(catalogue.definitions)}
        feedback_history = list(state.get("feedbackKeys") or [])
        players = list(state.get("players") or [""] * len(guesses))
        rows = []
        if answer is not None:
            for position, index in enumerate(guesses, 1):
                ship = catalogue.ship(str(index))
                if ship is None:
                    continue
                try:
                    guess_pool = pool
                    if difficulty == "random" and position <= len(feedback_history):
                        keys = [
                            key
                            for key in feedback_history[position - 1]
                            if isinstance(key, str) and key in catalogue.definitions
                        ]
                        if keys:
                            guess_pool = {**pool, "attributeKeys": keys}
                    evaluation = evaluate_guess(ship, answer, guess_pool, catalogue.definitions)
                except (KeyError, TypeError, ValueError):
                    continue
                states = [item["state"] for item in evaluation["feedback"]]
                attributes = (
                    [
                        {
                            "label": str(
                                catalogue.definitions.get(item["key"], {}).get("label")
                                or item["key"]
                            ),
                            "state": item["state"],
                        }
                        for item in evaluation["feedback"]
                    ]
                    if difficulty == "random"
                    else []
                )
                rows.append(
                    {
                        "n": position,
                        "name": self._name(ship),
                        "player": str(players[position - 1] if position <= len(players) else ""),
                        "states": states,
                        "attributes": attributes,
                        "hit": sum(1 for value in states if value == "exact"),
                        "win": bool(evaluation["correct"]),
                    }
                )
        return rows[-limit:], len(guesses)

    async def _render_feedback_card(
        self,
        catalogue: Catalogue,
        state: dict[str, Any],
        row: dict[str, Any],
        ship: dict[str, Any],
        status_text: str,
        player: str = "",
    ) -> RenderedCard | None:
        """Render the current guess as a WowsGuess-style multi-card board."""
        answer = catalogue.ship(str(state.get("answerIndex")))
        history, total = self._history_rows(catalogue, state)
        outcome = "won" if row["correct"] else "lost" if state["status"] == "lost" else "playing"
        view = make_guess_card_view(
            attempt=int(row["attempt"]),
            max_attempts=MAX_ATTEMPTS,
            remaining=max(0, MAX_ATTEMPTS - len(state["guesses"])),
            ship_name=self._name(ship),
            ship_index=str(ship["index"]),
            difficulty=str(state["difficulty"]),
            status_text=status_text,
            feedback=row["feedback"],
            definitions=catalogue.definitions,
            outcome=outcome,
            player=player,
            answer_name=self._name(answer) if answer is not None else "",
            answer_index=str(answer["index"]) if answer is not None else "",
            history=history,
            history_total=total,
        )
        return await self._render_card(build_guess_card_html(view))

    async def _render_status_card(
        self,
        catalogue: Catalogue,
        state: dict[str, Any],
        *,
        status_text: str = "",
    ) -> RenderedCard | None:
        """Render progress, full history and the answer as an image."""
        answer = catalogue.ship(str(state.get("answerIndex")))
        guesses = list(state.get("guesses") or [])
        history, total = self._history_rows(catalogue, state, limit=10)
        view = make_status_card_view(
            attempt=len(guesses),
            max_attempts=MAX_ATTEMPTS,
            remaining=max(0, MAX_ATTEMPTS - len(guesses)),
            difficulty=str(state.get("difficulty") or "standard"),
            status_text=status_text,
            outcome=str(state.get("status") or "playing"),
            answer_name=self._name(answer) if answer is not None else "",
            answer_index=str(answer["index"]) if answer is not None else "",
            history=history,
            history_total=total,
        )
        return await self._render_card(build_status_card_html(view))

    async def _render_help_card(self) -> RenderedCard | None:
        """Render the command reference with one visual card per command."""
        return await self._render_card(build_help_card_html(self._help_commands(), MAX_ATTEMPTS))

    @staticmethod
    def _help_commands() -> list[dict[str, Any]]:
        return [
            {
                "category": "开始游戏",
                "command": "/猜船 开始 [普通|困难|随机] [等级/范围]",
                "description": "新开一局。例：`开始 普通 V`、`开始 困难 8-10`；随机难度每次猜测会重新随机抽取一组有效属性，不固定数量。",
            },
            {
                "category": "提交猜测",
                "command": "/猜船 猜 <舰名>",
                "description": f"猜一艘船；支持中文名、英文名、舰船代码和别名。每局最多猜 {MAX_ATTEMPTS} 次。",
            },
            {
                "category": "快捷输入",
                "command": "直接发舰名 / 自然语言",
                "description": "对局中可直接发舰名；群聊未 @ 时只认整句舰名或「我猜X」（可在配置里关闭），@ 或私聊时也可说「帮我猜胡德」「还剩几次」「放弃」。",
            },
            {
                "category": "查看进度",
                "command": "/猜船 状态",
                "description": "查看当前难度、等级范围、已猜舰船和剩余次数。",
            },
            {
                "category": "结束本局",
                "command": "/猜船 放弃",
                "description": "结束当前游戏并公布答案；结束后即可开始下一局。",
            },
            {
                "category": "帮助",
                "command": "/猜船 帮助",
                "description": "显示这张指令卡片；也可发送 `/猜船` 查看。",
            },
            {
                "category": "题库管理",
                "command": "/猜船 刷新数据",
                "description": "从窝一把刷新公开舰船与题库数据。",
                "admin_only": True,
            },
        ]

    def _format_state(self, catalogue: Catalogue, state: dict[str, Any]) -> str:
        guesses = list(state.get("guesses") or [])
        labels = [self._name(catalogue.ship(index) or {"index": index}) for index in guesses]
        difficulty = str(state.get("difficulty") or "standard")
        difficulty_label = {"standard": "普通", "hard": "困难", "random": "随机"}.get(
            difficulty, difficulty
        )
        tier_range = state.get("tierRange")
        tier_suffix = ""
        if isinstance(tier_range, list) and len(tier_range) == 2:
            try:
                tier_suffix = f"（答案限定 {format_tier_range((int(tier_range[0]), int(tier_range[1])))}）"
            except ValueError:
                pass
        remaining = max(0, MAX_ATTEMPTS - len(guesses))
        guessed = "、".join(labels) if labels else "暂无"
        return f"难度：{difficulty_label}{tier_suffix}\n进度：{len(guesses)}/{MAX_ATTEMPTS}，剩余 {remaining} 次\n已猜：{guessed}"

    # ---- command parsing / dispatch -------------------------------------

    @staticmethod
    def _parse_command(text: str) -> tuple[str, str]:
        tokens = (text or "").strip().split(maxsplit=2)
        if tokens and tokens[0].lstrip("/") == "猜船":
            tokens = tokens[1:]
        if not tokens:
            return "", ""
        action = tokens[0].strip()
        argument = " ".join(tokens[1:]).strip() if len(tokens) > 1 else ""
        return action, argument

    @staticmethod
    def _emit(event: AstrMessageEvent, result: ActionResult):
        if result.image:
            return event.chain_result([Image.fromBytes(result.image)])
        if result.image_url:
            return event.image_result(result.image_url)
        return event.plain_result(result.text)

    @filter.command("猜船")
    async def wows_guess(self, event: AstrMessageEvent):
        """猜船 开始 [普通|困难|随机] / 猜 <舰名> / 状态 / 放弃 / 帮助 / 刷新数据。"""
        action_word, argument = self._parse_command(event.message_str)
        action = COMMAND_ACTIONS.get(action_word)
        if action is None:
            result = ActionResult("未知子命令。\n" + self._help_text(), error=True)
        else:
            result = await self._dispatch(event, action, argument)
        yield self._emit(event, result)

    async def _dispatch(self, event: AstrMessageEvent, action: str, argument: str) -> ActionResult:
        if action == "help":
            return await self._action_help()
        if action == "start":
            return await self._action_start(event, argument)
        if action == "status":
            return await self._action_status(event)
        if action == "giveup":
            return await self._action_give_up(event)
        if action == "refresh":
            return await self._action_refresh(event)
        if action == "guess":
            return await self._action_guess(event, argument)
        return ActionResult("未知子命令。\n" + self._help_text(), error=True)

    async def _action_help(self) -> ActionResult:
        if self.config.get("render_cards", True):
            try:
                card = await self._render_help_card()
            except Exception as exc:
                logger.warning("Failed to render WowsGuess help card: %s", exc)
            else:
                if card is not None:
                    return self._with_card(self._help_text(), card)
        return ActionResult(text=self._help_text())

    async def _action_start(self, event: AstrMessageEvent, argument: str) -> ActionResult:
        try:
            difficulty, tier_range = parse_start_options(argument)
        except ValueError as exc:
            return ActionResult(str(exc), error=True)
        try:
            catalogue = await self._catalogue_or_raise()
            await self.games.start(
                self._session_id(event),
                difficulty,
                catalogue,
                require_finish=self._is_group_chat(event),
                tier_range=tier_range,
            )
        except (DataUnavailableError, ValueError) as exc:
            return ActionResult(str(exc), error=True)
        label = {"standard": "普通", "hard": "困难", "random": "随机"}[difficulty]
        tier_label = f"（{format_tier_range(tier_range)}）" if tier_range else ""
        hint = (
            "\n随机难度会在每次猜测后重新抽取要展示的属性。"
            if difficulty == "random"
            else ""
        ) + (
            "\n对局中也可以直接发舰名（无需输入 /猜船 猜）；群聊未 @ 时只认整句舰名。"
            if self.config.get("free_text_guess", True)
            else ""
        )
        return ActionResult(
            f"已开始{label}猜船{tier_label}。本局最多 {MAX_ATTEMPTS} 次。\n"
            "发送 `/猜船 猜 <舰名>` 提交；可用中文、英文、代码或别名。"
            f"{hint}"
        )

    async def _action_status(self, event: AstrMessageEvent) -> ActionResult:
        try:
            catalogue = await self._catalogue_or_raise()
            state = await self.games.current(self._session_id(event))
        except DataUnavailableError as exc:
            return ActionResult(str(exc), error=True)
        if not state:
            return ActionResult("本会话还没有游戏，请先发送 `/猜船 开始`。", error=True)
        text = self._format_state(catalogue, state)
        if self.config.get("render_cards", True):
            try:
                card = await self._render_status_card(catalogue, state)
            except Exception as exc:  # noqa: BLE001 - text fallback must win
                logger.warning("Failed to render WowsGuess status card: %s", exc)
            else:
                if card is not None:
                    return self._with_card(text, card)
        return ActionResult(text)

    async def _action_give_up(self, event: AstrMessageEvent) -> ActionResult:
        try:
            catalogue = await self._catalogue_or_raise()
            state, answer = await self.games.give_up(self._session_id(event), catalogue)
        except (DataUnavailableError, ValueError) as exc:
            return ActionResult(str(exc), error=True)
        answer_text = f"本局结束。答案是：{self._name(answer)}（{answer['index']}）"
        text = answer_text + "\n" + self._format_state(catalogue, state)
        if self.config.get("render_cards", True):
            try:
                card = await self._render_status_card(catalogue, state, status_text=answer_text)
            except Exception as exc:  # noqa: BLE001 - text fallback must win
                logger.warning("Failed to render WowsGuess status card: %s", exc)
            else:
                if card is not None:
                    return self._with_card(text, card)
        return ActionResult(text)

    async def _action_refresh(self, event: AstrMessageEvent) -> ActionResult:
        if not self._is_admin(event):
            return ActionResult("只有 AstrBot 管理员可以刷新题库。", error=True)
        try:
            catalogue = await self._catalogue_or_raise(refresh=True)
        except DataUnavailableError as exc:
            return ActionResult(str(exc), error=True)
        if not self.store.last_refresh_ok:
            return ActionResult(
                f"题库刷新失败，继续使用本地缓存（{len(catalogue.ships)} 艘舰船）。"
            )
        return ActionResult(f"题库已刷新：{len(catalogue.ships)} 艘舰船。")

    async def _action_guess(self, event: AstrMessageEvent, argument: str) -> ActionResult:
        if not argument:
            return ActionResult("请提供舰名。例如：`/猜船 猜 天津`", error=True)
        try:
            catalogue = await self._catalogue_or_raise()
        except DataUnavailableError as exc:
            return ActionResult(str(exc), error=True)
        ship, candidates = catalogue.resolve(argument)
        if ship is None:
            if candidates:
                options = "、".join(f"{self._name(item)}（{item['index']}）" for item in candidates)
                return ActionResult(f"匹配到多个舰船，请改用更具体的名称或舰船代码：{options}", error=True)
            return ActionResult("没有找到这艘船。可使用中文、英文、舰船代码或别名。", error=True)
        try:
            state, row, answer = await self.games.guess(
                self._session_id(event), ship, catalogue, player=self._sender(event)
            )
        except ValueError as exc:
            return ActionResult(str(exc), error=True)
        return await self._feedback_reply(catalogue, state, row, ship, answer, player=self._sender(event))

    async def _feedback_reply(
        self,
        catalogue: Catalogue,
        state: dict[str, Any],
        row: dict[str, Any],
        ship: dict[str, Any],
        answer: dict[str, Any],
        player: str = "",
    ) -> ActionResult:
        header = f"第 {row['attempt']}/{MAX_ATTEMPTS} 次：{self._name(ship)}（{ship['index']}）"
        if row["correct"]:
            status_text = f"🎉 猜中了！答案是：{self._name(answer)}（{answer['index']}）"
        elif state["status"] == "lost":
            status_text = f"💥 次数用尽。答案是：{self._name(answer)}（{answer['index']}）"
        else:
            status_text = f"继续猜，还剩 {MAX_ATTEMPTS - len(state['guesses'])} 次"
        plain_text = header + "\n" + self._format_feedback(catalogue, row) + "\n" + status_text
        if self.config.get("render_cards", True):
            try:
                card = await self._render_feedback_card(
                    catalogue, state, row, ship, status_text, player=player
                )
            except Exception as exc:
                logger.warning("Failed to render WowsGuess feedback card: %s", exc)
            else:
                if card is not None:
                    return self._with_card(plain_text, card)
        return ActionResult(text=plain_text)

    # ---- free-text shortcut (no LLM) ------------------------------------

    @filter.event_message_type(EventMessageType.ALL)
    async def free_text_guess(self, event: AstrMessageEvent):
        """对局中直接发送舰名即视为一次猜测，省掉 /猜船 猜 的输入。"""
        if not self.config.get("free_text_guess", True):
            return
        text = (event.message_str or "").strip()
        if not text or text.startswith(("/", "猜船")):
            return  # 命令仍由 /猜船 handler 处理
        if len(text) > FREE_TEXT_MAX_LENGTH:
            return  # 长句多半不是在猜船，交给 LLM 正常回复

        # 群聊未 @ 的消息也会进入插件（AstrBot 的 handler filter 命中即视为唤醒），
        # 这时只接受“整句舰名 / 我猜X”这类明确猜测，普通闲聊不接管。
        woken = bool(getattr(event, "is_at_or_wake_command", False))
        quiet = self._is_group_chat(event) and not woken
        if quiet and not self.config.get("group_guess_without_at", True):
            return  # 配置里关闭了群聊免 @ 猜测

        session_id = self._session_id(event)
        try:
            catalogue = await self._catalogue_or_raise()
            state = await self.games.current(session_id)
        except DataUnavailableError:
            return
        if not state or state.get("status") != "playing":
            return
        ship = self._match_quiet_text(catalogue, text) if quiet else self._match_free_text(catalogue, text)
        if ship is None:
            return  # 不是舰名：不接管，交回 LLM
        try:
            updated, row, answer = await self.games.guess(
                session_id, ship, catalogue, player=self._sender(event)
            )
        except ValueError as exc:
            if not quiet:  # 未 @ 时重复/已结束多半是闲聊，静默忽略
                yield event.plain_result(str(exc))
            return
        feedback = await self._feedback_reply(
            catalogue, updated, row, ship, answer, player=self._sender(event)
        )
        stop = getattr(event, "stop_event", None)
        if callable(stop):
            stop()  # 这次猜测已有卡片/文本答复，不再交给其它插件或默认 LLM
        yield self._emit(event, feedback)

    @classmethod
    def _free_text_candidates(cls, text: str) -> list[str]:
        """Convert free chat text into exact-match candidates (prefixes/punctuation/trailing 号)."""
        cleaned = text.strip().strip("，。！？!?、~～….,;:：；“”‘’\"'《》 ")
        for prefix in FREE_TEXT_PREFIXES:
            if cleaned.startswith(prefix) and len(cleaned) > len(prefix):
                cleaned = cleaned[len(prefix):].strip()
                break
        candidates = [cleaned]
        if cleaned.endswith(FREE_TEXT_TRAILING) and len(cleaned) > 2:
            candidates.append(cleaned[:-1])
        return [candidate for candidate in candidates if len(candidate) >= 2]

    @classmethod
    def _match_free_text(cls, catalogue: Catalogue, text: str) -> dict[str, Any] | None:
        for candidate in cls._free_text_candidates(text):
            ship = catalogue.match_exact(candidate)
            if ship is not None:
                return ship
        return None

    @classmethod
    def _match_quiet_text(cls, catalogue: Catalogue, text: str) -> dict[str, Any] | None:
        """群聊未 @ 时的保守匹配：整句舰名，或“我猜X”这类明确前缀（不认后缀/标点花活）。"""
        cleaned = text.strip().strip("，。！？!?、~～….,;:：；“”‘’\"'《》 ")
        if not cleaned:
            return None
        ship = catalogue.match_exact(cleaned)
        if ship is not None:
            return ship
        for prefix in QUIET_GUESS_PREFIXES:
            if cleaned.startswith(prefix) and len(cleaned) > len(prefix):
                ship = catalogue.match_exact(cleaned[len(prefix) :].strip())
                if ship is not None:
                    return ship
        return None

    # ---- LLM tool --------------------------------------------------------

    @_tool_schema(llm_tool.NAME, llm_tool.SCHEMA)
    @filter.llm_tool(name=llm_tool.NAME)
    @_mark_doc(llm_tool.DESCRIPTION)
    async def wows_guess_tool(self, event: AstrMessageEvent, **args):
        self._last_tool_use[self._session_id(event)] = time.monotonic()
        try:
            request = llm_tool.parse_args(args)
        except llm_tool.InvalidArgs as exc:
            return llm_tool.invalid(str(exc))

        result = await self._dispatch(event, request.action, request.argument)
        if result.has_image():
            await event.send(self._image_chain(event, result))
            if self._bool_config("llm_tool_cards_only", True):
                return None  # 卡片就是回答：结束本轮，不再多一次 LLM 请求
        if result.error:
            return llm_tool.error(result.text)
        return llm_tool.result(result.text, card_sent=result.has_image())

    @filter.on_llm_request()
    async def _offer_guess_tool(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """Only offer the tool when the message looks relevant (or right after use)."""
        if not self.config.get("llm_tool_enabled", True):
            if req.func_tool is not None:
                req.func_tool.remove_tool(llm_tool.NAME)
            return
        if req.func_tool is None:
            return
        offers, playing = await self._tool_visibility(event)
        if not offers:
            req.func_tool.remove_tool(llm_tool.NAME)
            return
        if playing:
            self._append_playing_hint(req)

    @staticmethod
    def _append_playing_hint(req: ProviderRequest) -> None:
        """Inject the playing hint as a temp content part: never touch the cached system prompt."""
        part = _temp_text_part(PLAYING_SYSTEM_PROMPT)
        if part is None:  # pragma: no cover - old AstrBot without content parts
            return
        parts = getattr(req, "extra_user_content_parts", None)
        if parts is None:
            parts = []
            req.extra_user_content_parts = parts
        if not any(getattr(item, "text", None) == PLAYING_SYSTEM_PROMPT for item in parts):
            parts.append(part)

    async def _tool_visibility(self, event: AstrMessageEvent) -> tuple[bool, bool]:
        session_id = self._session_id(event)
        text = (event.message_str or "").strip()
        try:
            state = await self.games.current(session_id)
        except Exception:  # noqa: BLE001 - visibility must never break LLM requests
            state = None
        playing = bool(state and state.get("status") == "playing")
        last_used = self._last_tool_use.get(session_id)
        if last_used is not None and time.monotonic() - last_used < FOLLOW_UP_SECONDS:
            return True, playing
        if text and GUESS_TOPIC.search(text):
            return True, playing
        if playing and 0 < len(text) <= FREE_TEXT_MAX_LENGTH:
            return True, playing
        return False, playing

    @staticmethod
    def _help_text() -> str:
        return (
            "《窝一把·猜战舰》指令帮助（图片不可用时的文本版）\n"
            "`/猜船 开始 [普通|困难|随机] [等级/范围]` — 新开一局；例：`/猜船 开始 普通 V`、`/猜船 开始 困难 8-10`、`/猜船 开始 随机`\n"
            "随机难度每次猜测都会重新随机抽取 1 项或多项属性；同一次不重复，下一次重新抽。\n"
            "`/猜船 猜 <舰名>` — 提交中文名、英文名、代码或别名\n"
            "对局中也可以直接发舰名（如「胡德」）；群聊未 @ 时只认整句舰名或「我猜X」，可在配置里关闭；@ 或私聊时也可说「帮我猜胡德」「还剩几次」\n"
            "`/猜船 状态` — 查看难度、等级限制、已猜列表与剩余次数\n"
            "`/猜船 放弃` — 结束本局并公布答案\n"
            "`/猜船 帮助` — 显示指令卡片\n"
            "`/猜船 刷新数据` — 刷新公开题库（仅管理员）\n"
            f"等级支持阿拉伯数字和罗马数字（V-XI），范围如 8-10；每局最多 {MAX_ATTEMPTS} 次。\n"
            "🟩 精确；🟨 接近；⬆/⬇ 表示答案数值更高/更低；⬜ 不匹配。"
        )
