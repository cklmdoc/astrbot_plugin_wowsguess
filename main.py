"""AstrBot chat plugin for the WowsGuess-style ship guessing game."""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from .catalog import Catalogue
from .card_renderer import build_guess_card_html, build_help_card_html, make_guess_card_view
from .data_store import DataUnavailableError, WowsGuessDataStore
from .game_service import GameService
from .tier import format_tier_range, parse_start_options


PLUGIN_NAME = "astrbot_plugin_wowsguess"
MAX_ATTEMPTS = 8


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

    async def initialize(self) -> None:
        """Warm the local cache without preventing plugin load if the site is down."""
        if not self.config.get("refresh_on_start", True):
            return
        try:
            await self._catalogue_or_raise(refresh=True)
            logger.info("WowsGuess public ship catalogue refreshed.")
        except DataUnavailableError as exc:
            logger.warning("WowsGuess catalogue was not refreshed: %s", exc)

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
    def _name(ship: dict[str, Any]) -> str:
        return str(ship.get("nameZh") or ship.get("name") or ship["index"])

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

    async def _render_feedback_card(
        self,
        catalogue: Catalogue,
        state: dict[str, Any],
        row: dict[str, Any],
        ship: dict[str, Any],
        status_text: str,
    ) -> str:
        """Render the current guess as a WowsGuess-style multi-card board."""
        view = make_guess_card_view(
            attempt=int(row["attempt"]),
            max_attempts=MAX_ATTEMPTS,
            ship_name=self._name(ship),
            ship_index=str(ship["index"]),
            difficulty=str(state["difficulty"]),
            remaining=max(0, MAX_ATTEMPTS - len(state["guesses"])),
            status_text=status_text,
            feedback=row["feedback"],
            definitions=catalogue.definitions,
        )
        return await self.html_render(
            build_guess_card_html(view),
            {},
            options={"type": "png", "full_page": True, "animations": "disabled"},
        )

    async def _render_help_card(self) -> str:
        """Render the command reference with one visual card per command."""
        return await self.html_render(
            build_help_card_html(self._help_commands()),
            {},
            options={"type": "png", "full_page": True, "animations": "disabled"},
        )

    @staticmethod
    def _help_commands() -> list[dict[str, Any]]:
        return [
            {
                "category": "开始游戏",
                "command": "/猜船 开始 [普通|困难] [等级/范围]",
                "description": "新开一局。例：`开始 普通 V`、`开始 困难 8-10`；等级支持阿拉伯数字与罗马数字。",
            },
            {
                "category": "提交猜测",
                "command": "/猜船 猜 <舰名>",
                "description": "猜一艘船；支持中文名、英文名、舰船代码和别名。每局最多猜 8 次。",
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
        difficulty = "普通" if state.get("difficulty") == "standard" else "困难"
        tier_range = state.get("tierRange")
        tier_suffix = ""
        if isinstance(tier_range, list) and len(tier_range) == 2:
            try:
                tier_suffix = f"（答案限定 {format_tier_range((int(tier_range[0]), int(tier_range[1])))}）"
            except ValueError:
                pass
        remaining = max(0, MAX_ATTEMPTS - len(guesses))
        guessed = "、".join(labels) if labels else "暂无"
        return f"难度：{difficulty}{tier_suffix}\n进度：{len(guesses)}/{MAX_ATTEMPTS}，剩余 {remaining} 次\n已猜：{guessed}"

    @filter.command("猜船")
    async def wows_guess(self, event: AstrMessageEvent):
        """猜船 开始 [普通|困难] / 猜 <舰名> / 状态 / 放弃 / 帮助 / 刷新数据。"""
        tokens = event.message_str.strip().split(maxsplit=2)
        if tokens and tokens[0].lstrip("/") == "猜船":
            tokens = tokens[1:]
        action = tokens[0] if tokens else ""
        argument = " ".join(tokens[1:]) if len(tokens) > 1 else ""
        action = action.strip()
        argument = argument.strip()
        if not action or action in {"帮助", "help"}:
            if self.config.get("render_cards", True):
                try:
                    help_url = await self._render_help_card()
                except Exception as exc:
                    logger.warning("Failed to render WowsGuess help card: %s", exc)
                else:
                    yield event.image_result(help_url)
                    return
            yield event.plain_result(self._help_text())
            return
        if action == "开始":
            try:
                difficulty, tier_range = parse_start_options(argument)
            except ValueError as exc:
                yield event.plain_result(str(exc))
                return
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
                yield event.plain_result(str(exc))
                return
            label = "普通" if difficulty == "standard" else "困难"
            tier_label = f"（{format_tier_range(tier_range)}）" if tier_range else ""
            yield event.plain_result(
                f"已开始{label}猜船{tier_label}。本局最多 {MAX_ATTEMPTS} 次。\n"
                "发送 `/猜船 猜 <舰名>` 提交；可用中文、英文、代码或别名。"
            )
            return
        if action == "状态":
            try:
                catalogue = await self._catalogue_or_raise()
                state = await self.games.current(self._session_id(event))
            except DataUnavailableError as exc:
                yield event.plain_result(str(exc))
                return
            if not state:
                yield event.plain_result("本会话还没有游戏，请先发送 `/猜船 开始`。")
                return
            yield event.plain_result(self._format_state(catalogue, state))
            return
        if action == "放弃":
            try:
                catalogue = await self._catalogue_or_raise()
                state, answer = await self.games.give_up(self._session_id(event), catalogue)
            except (DataUnavailableError, ValueError) as exc:
                yield event.plain_result(str(exc))
                return
            yield event.plain_result(
                f"本局结束。答案是：{self._name(answer)}（{answer['index']}）\n"
                + self._format_state(catalogue, state)
            )
            return
        if action in {"刷新数据", "刷新"}:
            if getattr(event, "role", "") != "admin":
                yield event.plain_result("只有 AstrBot 管理员可以刷新题库。")
                return
            try:
                catalogue = await self._catalogue_or_raise(refresh=True)
            except DataUnavailableError as exc:
                yield event.plain_result(str(exc))
                return
            yield event.plain_result(f"题库已刷新：{len(catalogue.ships)} 艘舰船。")
            return
        if action != "猜":
            yield event.plain_result("未知子命令。\n" + self._help_text())
            return
        if not argument:
            yield event.plain_result("请提供舰名。例如：`/猜船 猜 天津`")
            return
        try:
            catalogue = await self._catalogue_or_raise()
        except DataUnavailableError as exc:
            yield event.plain_result(str(exc))
            return
        ship, candidates = catalogue.resolve(argument)
        if ship is None:
            if candidates:
                options = "、".join(f"{self._name(item)}（{item['index']}）" for item in candidates)
                yield event.plain_result(f"匹配到多个舰船，请改用更具体的名称或舰船代码：{options}")
            else:
                yield event.plain_result("没有找到这艘船。可使用中文、英文、舰船代码或别名。")
            return
        try:
            state, row, answer = await self.games.guess(
                self._session_id(event), ship, catalogue
            )
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return
        header = f"第 {row['attempt']}/{MAX_ATTEMPTS} 次：{self._name(ship)}（{ship['index']}）"
        if row["correct"]:
            status_text = f"🎉 猜中了！答案是：{self._name(answer)}（{answer['index']}）"
        elif state["status"] == "lost":
            status_text = f"💥 次数用尽。答案是：{self._name(answer)}（{answer['index']}）"
        else:
            status_text = f"继续猜，还剩 {MAX_ATTEMPTS - len(state['guesses'])} 次"
        if self.config.get("render_cards", True):
            try:
                card_url = await self._render_feedback_card(
                    catalogue, state, row, ship, status_text
                )
            except Exception as exc:
                logger.warning("Failed to render WowsGuess feedback card: %s", exc)
            else:
                yield event.image_result(card_url)
                return
        yield event.plain_result(
            header + "\n" + self._format_feedback(catalogue, row) + "\n" + status_text
        )

    @staticmethod
    def _help_text() -> str:
        return (
            "《窝一把·猜战舰》指令帮助（图片不可用时的文本版）\n"
            "`/猜船 开始 [普通|困难] [等级/范围]` — 新开一局；例：`/猜船 开始 普通 V`、`/猜船 开始 困难 8-10`\n"
            "`/猜船 猜 <舰名>` — 提交中文名、英文名、代码或别名\n"
            "`/猜船 状态` — 查看难度、等级限制、已猜列表与剩余次数\n"
            "`/猜船 放弃` — 结束本局并公布答案\n"
            "`/猜船 帮助` — 显示指令卡片\n"
            "`/猜船 刷新数据` — 刷新公开题库（仅管理员）\n"
            "等级支持阿拉伯数字和罗马数字（V-XI），范围如 8-10；每局最多 8 次。\n"
            "🟩 精确；🟨 接近；⬆/⬇ 表示答案数值更高/更低；⬜ 不匹配。"
        )
