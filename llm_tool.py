"""Structured LLM tool for the guess game: validated JSON arguments in, JSON result out.

The model never composes command strings: every argument has its own field (with
enums where possible) and is validated here. Bad arguments come back as status
"invalid" with what to fix instead of a random guess.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .tier import parse_tier_range

NAME = "wows_guess"

ACTIONS = {
    "开始": "start",
    "猜": "guess",
    "状态": "status",
    "放弃": "giveup",
    "帮助": "help",
}

DIFFICULTIES = {"普通": "standard", "标准": "standard", "困难": "hard"}

DESCRIPTION = (
    "玩《窝一把·猜战舰》（战舰世界猜船游戏）：开始一局、提交一次舰名猜测、查看进度、放弃、帮助。"
    "用户聊猜船、只发一个战舰名、说“猜XX / 帮我猜XX”、问还剩几次、要求放弃或者问怎么玩时调用。"
    "返回 JSON：status、text（可直接转述的要点）、next（下一步建议）；"
    "不要编造猜测结果或属性反馈。"
)

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(ACTIONS),
            "description": "开始=新开一局；猜=提交一次猜测；状态=查看本局进度；放弃=公布答案；帮助=指令说明",
        },
        "ship": {
            "type": "string",
            "description": "action=猜 时用户说的舰名（中文名/英文名/舰船代码/别名），只填一个舰名",
        },
        "difficulty": {
            "type": "string",
            "enum": ["普通", "困难"],
            "description": "action=开始 时的难度，默认普通",
        },
        "tier": {
            "type": "string",
            "description": "action=开始 时的等级或等级范围，如 V、8-10；不填则不限制",
        },
    },
    "required": ["action"],
}

NEXT = "继续玩直接说下一个舰名；看进度说“状态”；结束说“放弃”；新开一局说“开始”。"


class InvalidArgs(ValueError):
    """The tool arguments do not match the expected shape."""


@dataclass
class Request:
    """One validated tool call mapped to a game action."""

    action: str
    argument: str = ""


def _text(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    return "" if value is None else str(value).strip()


def parse_args(args: dict[str, Any]) -> Request:
    """Validate model-provided arguments and map them onto a game action."""
    word = _text(args, "action")
    if word not in ACTIONS:
        raise InvalidArgs("action 只能是：" + " / ".join(ACTIONS))
    action = ACTIONS[word]

    if action == "guess":
        ship = _text(args, "ship")
        if not ship:
            raise InvalidArgs("action=猜 时必须填 ship（用户说的舰名）")
        if len(ship) > 32:
            raise InvalidArgs("ship 只填一个舰名，不要把整句话塞进来")
        return Request(action, ship)

    if action == "start":
        difficulty = _text(args, "difficulty")
        tier = _text(args, "tier")
        parts: list[str] = []
        if difficulty:
            if difficulty not in DIFFICULTIES:
                raise InvalidArgs("difficulty 只能是 普通 或 困难")
            parts.append("困难" if DIFFICULTIES[difficulty] == "hard" else "普通")
        if tier:
            try:
                parse_tier_range(tier)
            except ValueError as exc:
                raise InvalidArgs(str(exc)) from exc
            parts.append(tier)
        return Request(action, " ".join(parts))

    return Request(action)


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def result(text: str, *, card_sent: bool = False) -> str:
    """Successful tool result; ``text`` can be relayed to the user."""
    return _dump({"status": "ok", "text": text, "card_sent_to_user": card_sent, "next": NEXT})


def error(message: str) -> str:
    """A game-level failure the model should explain to the user."""
    return _dump({"status": "error", "message": message, "next": NEXT})


def invalid(message: str) -> str:
    """Bad tool arguments; the model should fix them or ask the user."""
    return _dump({"status": "invalid", "message": message, "next": NEXT})
