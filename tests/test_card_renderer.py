"""Card builders: help page, guess board and status board."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_wowsguess.card_renderer import (  # noqa: E402
    build_guess_card_html,
    build_help_card_html,
    build_status_card_html,
    make_guess_card_view,
    make_status_card_view,
)

DEFINITIONS = {
    "level": {"key": "level", "label": "等级", "kind": "ordered", "unit": "级", "decimals": 0},
    "nation": {
        "key": "nation",
        "label": "国家",
        "kind": "discrete",
        "labels": {"Pan_Asia": "泛亚"},
    },
}
FEEDBACK = [
    {"key": "level", "state": "close", "direction": "up", "guessValue": 8},
    {"key": "nation", "state": "exact", "direction": None, "guessValue": "Pan_Asia"},
]


def guess_view(**overrides):
    data = {
        "attempt": 2,
        "max_attempts": 15,
        "remaining": 13,
        "ship_name": "天津",
        "ship_index": "PZSC719",
        "difficulty": "hard",
        "feedback": FEEDBACK,
        "definitions": DEFINITIONS,
        "status_text": "继续猜，还剩 13 次",
        "outcome": "playing",
        "player": "玩家A",
        "answer_name": "大和",
        "answer_index": "PJSB001",
        "history": [
            {"n": 1, "name": "阿拉巴马", "player": "玩家B", "states": ["exact", "far"], "hit": 1},
            {"n": 2, "name": "天津", "player": "玩家A", "states": ["close", "exact"], "hit": 1},
        ],
        "history_total": 2,
    }
    data.update(overrides)
    return make_guess_card_view(**data)


def test_guess_card_renders_tiles_and_history():
    html = build_guess_card_html(guess_view())
    assert "天津" in html and "PZSC719" in html
    assert "等级" in html and "泛亚" in html and "更大" in html
    assert "玩家A" in html and "阿拉巴马" in html
    assert "猜测记录" in html and "#1" in html and "#2" in html
    assert "board" in html


def test_guess_card_masks_answer_while_playing():
    html = build_guess_card_html(guess_view())
    assert "大和" not in html


def test_guess_card_reveals_answer_after_win_or_loss():
    assert "大和" in build_guess_card_html(guess_view(outcome="won"))
    assert "大和" in build_guess_card_html(guess_view(outcome="lost", status_text="💥 次数用尽"))


def test_guess_card_escapes_ship_names():
    html = build_guess_card_html(guess_view(ship_name="<script>alert(1)</script>"))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_help_card_mentions_fifteen_attempts():
    html = build_help_card_html([], 15)
    assert "每局最多 15 次" in html


def test_help_card_escapes_command_text():
    html = build_help_card_html(
        [{"category": "<b>", "command": "<script>alert(1)</script>", "description": "<img>"}],
        15,
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_status_card_shows_progress_and_answer():
    playing = make_status_card_view(
        attempt=3,
        max_attempts=15,
        remaining=12,
        difficulty="standard",
        status_text="",
        outcome="playing",
        history=[{"n": 1, "name": "天津", "player": "A", "states": ["close", "exact"], "hit": 1}],
        history_total=1,
    )
    html = build_status_card_html(playing)
    assert "本局状态" in html and "已猜 3 次" in html and "天津" in html

    finished = make_status_card_view(
        attempt=15,
        max_attempts=15,
        remaining=0,
        difficulty="standard",
        status_text="",
        outcome="lost",
        answer_name="大和",
        answer_index="PJSB001",
        history=[],
        history_total=15,
    )
    assert "大和" in build_status_card_html(finished)
