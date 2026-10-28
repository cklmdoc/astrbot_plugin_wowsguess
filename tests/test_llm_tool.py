"""The structured LLM tool: argument validation and JSON result payloads."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_wowsguess import llm_tool  # noqa: E402


def test_guess_action_requires_ship():
    request = llm_tool.parse_args({"action": "猜", "ship": " 天津 "})
    assert request.action == "guess"
    assert request.argument == "天津"


def test_start_action_builds_command_argument():
    request = llm_tool.parse_args({"action": "开始", "difficulty": "困难", "tier": "8-10"})
    assert request.action == "start"
    assert request.argument == "困难 8-10"
    assert llm_tool.parse_args({"action": "开始"}).argument == ""
    assert llm_tool.parse_args({"action": "开始", "difficulty": "标准"}).argument == "普通"


def test_actions_without_arguments():
    for word, action in (("状态", "status"), ("放弃", "giveup"), ("帮助", "help")):
        request = llm_tool.parse_args({"action": word})
        assert (request.action, request.argument) == (action, "")


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"action": "开船"},
        {"action": "猜"},
        {"action": "猜", "ship": "x" * 33},
        {"action": "开始", "difficulty": "噩梦"},
        {"action": "开始", "tier": "12"},
        {"action": "开始", "tier": "V-XII"},
    ],
)
def test_invalid_arguments(args):
    with pytest.raises(llm_tool.InvalidArgs):
        llm_tool.parse_args(args)


def test_json_payloads():
    ok = json.loads(llm_tool.result("第 1/15 次：天津", card_sent=True))
    assert ok["status"] == "ok"
    assert ok["text"] == "第 1/15 次：天津"
    assert ok["card_sent_to_user"] is True
    assert ok["next"]

    error = json.loads(llm_tool.error("本会话还没有游戏"))
    assert error["status"] == "error" and "还没有游戏" in error["message"]

    invalid = json.loads(llm_tool.invalid("action 只能是：开始 / 猜"))
    assert invalid["status"] == "invalid"


def test_schema_shape():
    assert llm_tool.SCHEMA["required"] == ["action"]
    assert set(llm_tool.SCHEMA["properties"]["action"]["enum"]) == set(llm_tool.ACTIONS)
    assert llm_tool.DESCRIPTION
