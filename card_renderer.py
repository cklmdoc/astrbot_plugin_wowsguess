"""HTML card rendering for WowsGuess feedback, status and help images.

The visual language is borrowed from the sibling Minecraft guess plugin
(dark board, accent strip, header with chances, guess banner, attribute tiles,
history table and legend footer) while the palette and iconography stay naval.
"""

from __future__ import annotations

from html import escape
from typing import Any

CARD_WIDTH = 1060
_STATE_LABELS = {"exact": "完全相同", "close": "接近", "far": "不匹配"}
_DIRECTION_LABELS = {"up": "更大", "down": "更小"}
_OUTCOME_LABELS = {"playing": "进行中", "won": "猜中了", "lost": "已结束", "gaveup": "已放弃"}
_DIFFICULTY_LABELS = {"standard": "普通", "hard": "困难", "random": "随机"}


def _text(value: Any) -> str:
    return escape(str(value), quote=True)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _state(value: Any) -> str:
    state = str(value)
    return state if state in _STATE_LABELS else "far"


def _direction(value: Any) -> str:
    direction = str(value or "")
    return direction if direction in _DIRECTION_LABELS else ""


# ---------------------------------------------------------------------------
# inline SVG glyphs (pixel-ish, colour driven by currentColor)
# ---------------------------------------------------------------------------

_GLYPHS = {
    "exact": (
        '<path d="M3 8.6 6.3 12 13 4.4" fill="none" stroke="currentColor" '
        'stroke-width="2.6" stroke-linecap="square" stroke-linejoin="miter"/>'
    ),
    "close": (
        '<path d="M2.4 9.6c2.1-4.8 3.9 4.8 6 0s3.9 4.8 6 0" fill="none" '
        'stroke="currentColor" stroke-width="2.4" stroke-linecap="square"/>'
    ),
    "miss": (
        '<path d="M4.2 4.2l7.6 7.6M11.8 4.2l-7.6 7.6" fill="none" '
        'stroke="currentColor" stroke-width="2.4" stroke-linecap="square"/>'
    ),
    "up": (
        '<path d="M8 13V4M4.5 7.6 8 4l3.5 3.6" fill="none" stroke="currentColor" '
        'stroke-width="2.2" stroke-linecap="square" stroke-linejoin="miter"/>'
    ),
    "down": (
        '<path d="M8 3v9M4.5 8.4 8 12l3.5-3.6" fill="none" stroke="currentColor" '
        'stroke-width="2.2" stroke-linecap="square" stroke-linejoin="miter"/>'
    ),
    "target": (
        '<circle cx="8" cy="8" r="4.8" fill="none" stroke="currentColor" stroke-width="1.8"/>'
        '<circle cx="8" cy="8" r="1.4" fill="currentColor"/>'
    ),
}


def _glyph(name: str, size: int = 14) -> str:
    body = _GLYPHS.get(name, _GLYPHS["target"])
    return f'<svg class="gl" width="{size}" height="{size}" viewBox="0 0 16 16" aria-hidden="true">{body}</svg>'


_SHIP_ICON = """<svg class="px" width="{size}" height="{size}" viewBox="0 0 16 16" shape-rendering="crispEdges" aria-hidden="true">
<rect x="8" y="0" width="1" height="3" fill="#C9DEEF"/>
<rect x="7" y="3" width="3" height="2" fill="#C9DEEF"/>
<rect x="6" y="5" width="5" height="2" fill="#8FB9D8"/>
<rect x="6" y="7" width="1" height="2" fill="#C9DEEF"/>
<rect x="10" y="7" width="1" height="2" fill="#C9DEEF"/>
<rect x="1" y="9" width="13" height="2" fill="#6E9FC6"/>
<rect x="13" y="8" width="2" height="1" fill="#6E9FC6"/>
<rect x="1" y="9" width="13" height="1" fill="#8FB9D8"/>
<rect x="3" y="8" width="2" height="1" fill="#C9DEEF"/>
<rect x="9" y="8" width="3" height="1" fill="#C9DEEF"/>
<rect x="2" y="11" width="12" height="1" fill="#2C455C"/>
<rect x="1" y="12" width="13" height="1" fill="#1B2836"/>
<rect x="3" y="13" width="10" height="1" fill="#2C455C"/>
</svg>"""


def _ship_icon(size: int = 44) -> str:
    return _SHIP_ICON.format(size=size)


# ---------------------------------------------------------------------------
# view builders
# ---------------------------------------------------------------------------


def _format_tiles(
    feedback: list[dict[str, Any]], definitions: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    tiles = []
    for result in feedback:
        definition = definitions.get(result["key"], {})
        value = result["guessValue"]
        key = str(result["key"])
        if key in {"nation", "species"}:
            value_text = str(definition.get("labels", {}).get(str(value), value))
        elif isinstance(value, float):
            decimals = definition.get("decimals")
            value_text = f"{value:.{decimals}f}" if isinstance(decimals, int) else f"{value:g}"
        else:
            value_text = str(value)
        value_text += str(definition.get("unit") or "")
        tiles.append(
            {
                "label": str(definition.get("label") or key),
                "value": value_text,
                "state": _state(result["state"]),
                "direction": _direction(result.get("direction")),
            }
        )
    return tiles


def _history_view(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows = []
    for item in history or []:
        states = [_state(state) for state in (item.get("states") or [])]
        attributes = [
            {"label": str(attribute.get("label") or ""), "state": _state(attribute.get("state"))}
            for attribute in item.get("attributes", [])
            if isinstance(attribute, dict)
        ]
        rows.append(
            {
                "n": _int(item.get("n")),
                "name": str(item.get("name") or ""),
                "player": str(item.get("player") or ""),
                "states": states,
                "attributes": attributes,
                "hit": _int(item.get("hit")),
                "win": bool(item.get("win")),
            }
        )
    return rows


def make_guess_card_view(
    *,
    attempt: int,
    max_attempts: int,
    remaining: int,
    ship_name: str,
    ship_index: str,
    difficulty: str,
    feedback: list[dict[str, Any]],
    definitions: dict[str, dict[str, Any]],
    status_text: str,
    outcome: str = "playing",
    player: str = "",
    answer_name: str = "",
    answer_index: str = "",
    history: list[dict[str, Any]] | None = None,
    history_total: int | None = None,
) -> dict[str, Any]:
    """Convert one guess into template-ready localized card data."""
    tiles = _format_tiles(feedback, definitions)
    return {
        "kind": "guess",
        "attempt": _int(attempt),
        "max_attempts": _int(max_attempts),
        "remaining": max(0, _int(remaining)),
        "ship_name": str(ship_name),
        "ship_index": str(ship_index),
        "difficulty_label": _DIFFICULTY_LABELS.get(str(difficulty), str(difficulty)),
        "difficulty_key": str(difficulty),
        "status_text": str(status_text),
        "outcome": outcome if outcome in _OUTCOME_LABELS else "playing",
        "player": str(player),
        "answer_name": str(answer_name),
        "answer_index": str(answer_index),
        "tiles": tiles,
        "hit": sum(1 for tile in tiles if tile["state"] == "exact"),
        "total": len(tiles),
        "history": _history_view(history),
        "history_total": len(history or []) if history_total is None else _int(history_total),
    }


def make_status_card_view(
    *,
    attempt: int,
    max_attempts: int,
    remaining: int,
    difficulty: str,
    status_text: str,
    outcome: str = "playing",
    answer_name: str = "",
    answer_index: str = "",
    history: list[dict[str, Any]] | None = None,
    history_total: int | None = None,
) -> dict[str, Any]:
    """Convert a saved round into the status-card view."""
    rows = _history_view(history)
    return {
        "kind": "status",
        "attempt": _int(attempt),
        "max_attempts": _int(max_attempts),
        "remaining": max(0, _int(remaining)),
        "difficulty_label": _DIFFICULTY_LABELS.get(str(difficulty), str(difficulty)),
        "difficulty_key": str(difficulty),
        "status_text": str(status_text),
        "outcome": outcome if outcome in _OUTCOME_LABELS else "playing",
        "answer_name": str(answer_name),
        "answer_index": str(answer_index),
        "history": rows,
        "history_total": len(rows) if history_total is None else _int(history_total),
    }


# ---------------------------------------------------------------------------
# CSS / page shell
# ---------------------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: #0B1016; }
body {
  width: %(width)dpx;
  color: #E8EEF6;
  font-family: "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC", "PingFang SC",
    "Microsoft YaHei", "WenQuanYi Micro Hei", sans-serif;
  font-size: 15px;
  line-height: 1.45;
  -webkit-font-smoothing: antialiased;
}
.board { width: 100%%; background: #141B24; overflow: hidden; }
.strip { height: 6px; background: linear-gradient(90deg, #3E7CA6, #4FB9C9); }
.wrap { padding: 24px 28px 22px; }
.px { display: block; image-rendering: pixelated; }
.gl { display: block; flex: none; }

/* header */
.head { display: flex; align-items: center; gap: 14px; }
.icon { flex: none; padding: 6px; border-radius: 8px; background: #1B2531; border: 1px solid rgba(255,255,255,.08); }
.title { flex: 1; min-width: 0; }
.eyebrow { font-size: 13px; color: rgba(232,238,246,.55); letter-spacing: .12em; }
.title h1 { font-size: 26px; font-weight: 800; line-height: 1.25; letter-spacing: .02em; }
.meta { display: flex; gap: 8px; margin-top: 4px; }
.pill { font-size: 13px; line-height: 20px; padding: 0 9px; border-radius: 999px; color: rgba(232,238,246,.82);
  background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.10); }
.pill.standard { color: #9CCBE8; border-color: rgba(79,185,201,.45); }
.pill.hard { color: #F2A196; border-color: rgba(233,115,102,.45); }
.pill.random { color: #D7BBF8; border-color: rgba(166,129,220,.50); }
.chances { flex: none; width: 210px; text-align: right; }
.chances .num { font-size: 34px; font-weight: 800; line-height: 1; font-variant-numeric: tabular-nums; }
.chances .num small { font-size: 15px; font-weight: 500; color: rgba(232,238,246,.6); margin-left: 4px; }
.chances .num.low { color: #E3B341; } .chances .num.crit { color: #E97366; }
.chances .cap { font-size: 13px; color: rgba(232,238,246,.6); margin-top: 4px; }
.seg { display: flex; gap: 2px; height: 8px; margin-top: 8px; }
.seg i { flex: 1; border-radius: 1px; background: rgba(255,255,255,.10); }
.seg i.on { background: #4FB9C9; }
.seg.low i.on { background: #E3B341; } .seg.crit i.on { background: #E97366; }

/* current guess / outcome banner */
.guess { margin-top: 20px; display: flex; align-items: center; gap: 16px; padding: 14px 18px;
  border-radius: 10px; background: #1A2430; border: 1px solid rgba(255,255,255,.08); }
.guess .badge { flex: none; width: 44px; height: 44px; border-radius: 10px; display: flex; align-items: center;
  justify-content: center; background: #26384A; color: #9CCBE8; }
.guess .who { font-size: 14px; color: rgba(232,238,246,.62); }
.guess .who b { color: #E8EEF6; font-weight: 700; }
.guess .main { flex: 1; min-width: 0; }
.guess .name { font-size: 28px; font-weight: 800; line-height: 1.25; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.guess .name span { font-size: 16px; font-weight: 500; color: rgba(232,238,246,.55); margin-left: 10px; }
.guess .score { flex: none; text-align: right; }
.guess .score .v { font-size: 26px; font-weight: 800; line-height: 1.1; font-variant-numeric: tabular-nums; }
.guess .score .v small { font-size: 15px; font-weight: 500; color: rgba(232,238,246,.55); }
.guess .score .c { font-size: 13px; color: rgba(232,238,246,.6); }
.guess.won { background: rgba(95,184,120,.14); border-color: rgba(95,184,120,.5); }
.guess.won .badge { background: #5FB878; color: #0F2416; }
.guess.lost { background: rgba(233,115,102,.12); border-color: rgba(233,115,102,.45); }
.guess.lost .badge { background: #E97366; color: #2A0E0B; }

/* attribute tiles */
.grid { margin-top: 14px; display: grid; grid-template-columns: repeat(var(--cols, 4), minmax(0, 1fr)); gap: 10px; }
.t { display: flex; flex-direction: column; min-height: 108px; padding: 11px 13px 12px; border-radius: 8px;
  background: #1B2531; border: 1px solid rgba(255,255,255,.09); }
.t.exact { background: rgba(95,184,120,.15); border-color: rgba(95,184,120,.55); }
.t.close { background: rgba(227,179,65,.14); border-color: rgba(227,179,65,.60); }
.t .top { display: flex; align-items: center; justify-content: space-between; gap: 6px; font-size: 14px;
  color: rgba(232,238,246,.68); }
.t .top span:first-child { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.st { flex: none; width: 22px; height: 22px; border-radius: 6px; display: flex; align-items: center;
  justify-content: center; background: rgba(255,255,255,.08); color: rgba(232,238,246,.6); }
.exact .st { background: #5FB878; color: #0F2416; }
.close .st { background: #E3B341; color: #2B2006; }
.t .val { margin-top: 5px; display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px;
  font-size: 22px; font-weight: 800; line-height: 1.25; }
.t .val .txt { min-width: 0; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 2;
  -webkit-box-orient: vertical; overflow: hidden; }
.t.far .val { color: rgba(232,238,246,.80); }
.dir { display: inline-flex; align-items: center; gap: 2px; font-size: 13px; font-weight: 700; line-height: 20px;
  padding: 0 7px 0 5px; border-radius: 5px; background: rgba(79,185,201,.16); color: #9CCBE8; white-space: nowrap; }
.t.far .dir { background: rgba(222,146,85,.18); color: #F0A76B; }
.empty { grid-column: 1 / -1; padding: 36px 20px; text-align: center; border-radius: 8px;
  border: 1px dashed rgba(255,255,255,.18); color: rgba(232,238,246,.6); }

/* history */
.sec { margin-top: 20px; display: flex; justify-content: space-between; align-items: baseline; }
.sec h2 { font-size: 16px; font-weight: 700; }
.sec span { font-size: 13px; color: rgba(232,238,246,.55); }
.hist { margin-top: 8px; border-radius: 10px; border: 1px solid rgba(255,255,255,.08); overflow: hidden; }
.row { display: flex; align-items: center; gap: 12px; padding: 8px 14px; font-size: 15px; }
.row + .row { border-top: 1px solid rgba(255,255,255,.06); }
.row .n { width: 30px; color: rgba(232,238,246,.5); font-variant-numeric: tabular-nums; }
.row .nm { width: 170px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row .pl { flex: 1; min-width: 0; color: rgba(232,238,246,.55); white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; }
.sq { display: flex; gap: 3px; flex-wrap: wrap; max-width: 360px; }
.sq i { width: 18px; height: 18px; border-radius: 4px; display: flex; align-items: center;
  justify-content: center; background: rgba(255,255,255,.09); color: rgba(232,238,246,.45); }
.sq i.exact { background: #5FB878; color: #0F2416; }
.sq i.close { background: #E3B341; color: #2B2006; }
.hattr-item { display: inline-flex; align-items: center; gap: 3px; padding: 1px 4px; border-radius: 4px; background: rgba(255,255,255,.05); }
.hattr-item small { color: rgba(232,238,246,.68); font-size: 10px; white-space: nowrap; }
.hattr { width: 17px; height: 17px; border-radius: 4px; display: flex; align-items: center;
  justify-content: center; background: rgba(255,255,255,.09); color: rgba(232,238,246,.45); }
.hattr.exact { background: #5FB878; color: #0F2416; }
.hattr.close { background: #E3B341; color: #2B2006; }
.row .h { width: 44px; text-align: right; color: rgba(232,238,246,.55); font-variant-numeric: tabular-nums; }
.row.win .nm { color: #8FD6A4; }

/* footer */
.foot { margin-top: 18px; padding-top: 14px; border-top: 1px solid rgba(255,255,255,.07);
  display: flex; justify-content: space-between; align-items: center; gap: 16px; font-size: 13px;
  color: rgba(232,238,246,.6); }
.legend { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.legend span { display: inline-flex; align-items: center; gap: 5px; }
.legend .st { width: 18px; height: 18px; border-radius: 5px; }
.legend .exact { background: #5FB878; color: #0F2416; }
.legend .close { background: #E3B341; color: #2B2006; }
.tip { white-space: nowrap; color: rgba(232,238,246,.72); }
.tip code { font-family: inherit; font-weight: 700; color: #9CCBE8; }

/* help page */
.how { margin-top: 20px; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.how div { padding: 12px 14px; border-radius: 8px; font-size: 14px; color: rgba(232,238,246,.72);
  background: #1B2531; border: 1px solid rgba(255,255,255,.09); }
.how .st { width: 20px; height: 20px; border-radius: 5px; }
.how .exact { background: #5FB878; color: #0F2416; } .how .close { background: #E3B341; color: #2B2006; }
.how b { display: flex; align-items: center; gap: 6px; font-size: 15px; color: #E8EEF6; margin-bottom: 4px; }
.cmds { margin-top: 12px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.cmd { padding: 14px 16px; border-radius: 8px; background: #1B2531; border: 1px solid rgba(255,255,255,.09); }
.cmd .cat { display: flex; align-items: center; justify-content: space-between; font-size: 12px;
  letter-spacing: .08em; color: rgba(232,238,246,.45); }
.cmd code { display: block; margin-top: 5px; font-family: inherit; font-size: 17px; font-weight: 800;
  color: #E8EEF6; overflow-wrap: anywhere; }
.cmd p { margin-top: 6px; font-size: 14px; color: rgba(232,238,246,.65); overflow-wrap: anywhere; }
.cmd.admin { grid-column: 1 / -1; }
.badge { padding: 3px 8px; border-radius: 999px; font-size: 12px; color: #F8D98A; background: #594617;
  border: 1px solid #8C7026; letter-spacing: 0; }
"""

_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
{body}
</body>
</html>"""


def _page(title: str, body: str) -> str:
    return _PAGE.format(title=_text(title), css=_CSS % {"width": CARD_WIDTH}, body=body)


# ---------------------------------------------------------------------------
# fragments
# ---------------------------------------------------------------------------


def _legend() -> str:
    return (
        '<div class="legend">'
        f'<span><i class="st exact">{_glyph("exact", 12)}</i>完全相同</span>'
        f'<span><i class="st close">{_glyph("close", 12)}</i>接近</span>'
        f'<span><i class="st">{_glyph("miss", 12)}</i>不匹配</span>'
        f'<span><i class="st">{_glyph("up", 12)}</i>{_glyph("down", 12)} 答案更大 / 更小</span>'
        "</div>"
    )


def _chances(view: dict[str, Any]) -> str:
    maximum = max(0, _int(view.get("max_attempts")))
    remaining = max(0, min(maximum, _int(view.get("remaining")))) if maximum else 0
    outcome = str(view.get("outcome") or "playing")
    ended = outcome in {"won", "lost", "gaveup"}
    if maximum:
        low = maximum * 0.34
        crit = maximum * 0.14
        level = "crit" if remaining <= crit else "low" if remaining <= low else ""
        filled = max(0, min(maximum, _int(view.get("attempt")))) if ended else remaining
        segments = "".join(
            f'<i class="{"on" if index < filled else ""}"></i>' for index in range(min(maximum, 50))
        )
        if ended:
            level = ""
            number = (
                f'<div class="num">{filled}<small>/ {maximum}</small></div>'
                '<div class="cap">本局共猜</div>'
            )
        else:
            number = (
                f'<div class="num {level}">{remaining}<small>/ {maximum}</small></div>'
                '<div class="cap">剩余机会</div>'
            )
    else:
        level = ""
        segments = ""
        number = f'<div class="num">{_int(view.get("attempt"))}<small>次</small></div><div class="cap">本局共猜</div>'
    return f'<div class="chances">{number}<div class="seg {level}">{segments}</div></div>'


def _header(
    view: dict[str, Any],
    *,
    title: str,
    eyebrow: str,
    pills: list[str],
    show_chances: bool = True,
) -> str:
    difficulty = str(view.get("difficulty_label") or "普通")
    difficulty_key = str(view.get("difficulty_key") or "")
    if difficulty_key not in _DIFFICULTY_LABELS:
        difficulty_key = next(
            (key for key, label in _DIFFICULTY_LABELS.items() if label == difficulty),
            "standard",
        )
    pill_html = f'<span class="pill {difficulty_key}">{_text(difficulty)}</span>'
    for pill in pills:
        pill_html += f'<span class="pill">{_text(pill)}</span>'
    chances = _chances(view) if show_chances else ""
    return f"""
<div class="head">
  <div class="icon">{_ship_icon(44)}</div>
  <div class="title">
    <div class="eyebrow">{_text(eyebrow)}</div>
    <h1>{_text(title)}</h1>
    <div class="meta">{pill_html}</div>
  </div>
  {chances}
</div>"""


def _board(body: str) -> str:
    return f'<section class="board"><div class="strip"></div><div class="wrap">{body}</div></section>'


def _history_rows_html(rows: list[dict[str, Any]], total: int, caption: str) -> str:
    if not rows:
        return (
            '<div class="sec"><h2>猜测记录</h2><span>暂无</span></div>'
            '<div class="hist"><div class="empty" style="border:0">还没有可展示的猜测记录</div></div>'
        )
    body = []
    for row in reversed(rows):
        if row.get("attributes"):
            cells = "".join(
                f'<span class="hattr-item"><small>{_text(attribute["label"])}</small>'
                f'<i class="hattr {attribute["state"]}">{_glyph(attribute["state"] if attribute["state"] in {"exact", "close"} else "miss", 10)}</i></span>'
                for attribute in row["attributes"]
            )
        else:
            cells = "".join(
                f'<i class="{state}">{_glyph(state if state in {"exact", "close"} else "miss", 12)}</i>'
                for state in row["states"]
            )
        classes = "row" + (" win" if row.get("win") else "")
        body.append(
            f'<div class="{classes}"><span class="n">#{row["n"]}</span>'
            f'<span class="nm">{_text(row["name"])}</span>'
            f'<span class="pl">{_text(row["player"])}</span>'
            f'<span class="sq">{cells}</span>'
            f'<span class="h">{row["hit"]}/{len(row["states"])}</span></div>'
        )
    return (
        f'<div class="sec"><h2>猜测记录</h2><span>{_text(caption)} · 共 {total} 次</span></div>'
        f'<div class="hist">{"".join(body)}</div>'
    )


def _banner_guess(view: dict[str, Any]) -> str:
    outcome = str(view.get("outcome") or "playing")
    classes = "guess" + (" won" if outcome == "won" else " lost" if outcome in {"lost", "gaveup"} else "")
    badge = {"won": "exact", "lost": "miss", "gaveup": "miss"}.get(outcome, "target")
    player = str(view.get("player") or "")
    who = f"第 {_int(view.get('attempt'))} 次猜测" + (f" · {_text(player)}" if player else "")
    answer_name = str(view.get("answer_name") or "")
    answer_index = str(view.get("answer_index") or "")
    if outcome in {"won", "lost", "gaveup"} and answer_name:
        who = {"won": "猜中了", "lost": "次数用尽", "gaveup": "已放弃"}[outcome] + " · 答案是"
        name_html = f'{_text(answer_name)} <span>{_text(answer_index)}</span>'
        score_html = '<div class="score"><div class="v">—</div><div class="c">本局结束</div></div>'
    else:
        name_html = f'{_text(view.get("ship_name"))} <span>{_text(view.get("ship_index"))}</span>'
        score_html = (
            f'<div class="score"><div class="v">{_int(view.get("hit"))}<small>/{_int(view.get("total"))}</small></div>'
            '<div class="c">属性命中</div></div>'
        )
    return (
        f'<div class="{classes}"><div class="badge">{_glyph(badge, 24)}</div>'
        f'<div class="main"><div class="who">{who}</div><div class="name">{name_html}</div></div>'
        f"{score_html}</div>"
    )


def _tiles_html(view: dict[str, Any]) -> str:
    tiles = view.get("tiles") or []
    if not tiles:
        return '<div class="grid"><div class="empty">还没有可展示的属性，先猜一个名字吧。</div></div>'
    items = []
    for tile in tiles:
        state = _state(tile.get("state"))
        direction = _direction(tile.get("direction"))
        dir_html = ""
        if direction:
            dir_html = (
                f'<span class="dir">{_glyph(direction, 12)}{_DIRECTION_LABELS[direction]}</span>'
            )
        items.append(
            f'<div class="t {state}"><div class="top"><span>{_text(tile.get("label"))}</span>'
            f'<span class="st">{_glyph(state, 13)}</span></div>'
            f'<div class="val"><span class="txt">{_text(tile.get("value"))}</span>{dir_html}</div></div>'
        )
    return f'<div class="grid" style="--cols: 4">{"".join(items)}</div>'


def _foot(left: str, tip: str) -> str:
    return f'<div class="foot">{left}<div class="tip">{tip}</div></div>'


def _clip(text: Any, limit: int) -> str:
    raw = str(text or "")
    return raw if len(raw) <= limit else raw[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# public builders
# ---------------------------------------------------------------------------


def build_guess_card_html(view: dict[str, Any]) -> str:
    """Build the self-contained feedback board for one guess."""
    body = _header(
        view,
        title="猜战舰",
        eyebrow="WOWSGUESS · 窝一把猜战舰",
        pills=[_OUTCOME_LABELS.get(str(view.get("outcome") or "playing"), "进行中")],
    )
    body += _banner_guess(view)
    body += _tiles_html(view)
    if view.get("history"):
        body += _history_rows_html(
            view.get("history") or [],
            _int(view.get("history_total")),
            "最近 5 次",
        )
    tip = (
        '<code>/猜船 开始</code> 再来一局'
        if str(view.get("outcome")) in {"won", "lost", "gaveup"}
        else "对局中直接发舰名即可猜"
    )
    body += _foot(_legend(), tip)
    return _page("WowsGuess 猜测结果", _board(body))


def build_status_card_html(view: dict[str, Any]) -> str:
    """Build the status board for the saved round."""
    body = _header(
        view,
        title="本局状态",
        eyebrow="WOWSGUESS · 窝一把猜战舰",
        pills=[_OUTCOME_LABELS.get(str(view.get("outcome") or "playing"), "进行中")],
    )
    outcome = str(view.get("outcome") or "playing")
    classes = "guess" + (" won" if outcome == "won" else " lost" if outcome in {"lost", "gaveup"} else "")
    if outcome in {"won", "lost", "gaveup"}:
        badge = {"won": "exact", "lost": "miss", "gaveup": "miss"}.get(outcome, "miss")
        name_html = (
            f'{_text(view.get("answer_name"))} <span>{_text(view.get("answer_index"))}</span>'
        )
        who = "答案是"
    else:
        badge = "target"
        name_html = f'已猜 {_int(view.get("attempt"))} 次 · 剩余 {_int(view.get("remaining"))} 次'
        who = "本局进行中"
    body += (
        f'<div class="{classes}"><div class="badge">{_glyph(badge, 24)}</div>'
        f'<div class="main"><div class="who">{who}</div><div class="name">{name_html}</div></div></div>'
    )
    history = view.get("history") or []
    total = _int(view.get("history_total"))
    body += _history_rows_html(
        history,
        total,
        "全部记录" if total <= len(history) else f"最近 {len(history)} 次",
    )
    tip = (
        "发送 <code>/猜船 开始</code> 再来一局"
        if str(view.get("outcome") or "playing") in {"won", "lost", "gaveup"}
        else "发送舰名继续猜，或 <code>/猜船 放弃</code> 结束本局"
    )
    body += _foot(_legend(), tip)
    return _page("WowsGuess 本局状态", _board(body))


def build_help_card_html(commands: list[dict[str, Any]], max_attempts: int) -> str:
    """Build an injection-safe command reference card for the help action."""
    how = f"""
<div class="how">
  <div><b><i class="st exact">{_glyph("exact", 13)}</i>完全相同</b>该属性与答案一致</div>
  <div><b><i class="st close">{_glyph("close", 13)}</i>接近</b>数值接近，或多值属性有交集</div>
  <div><b>{_glyph("up", 14)}{_glyph("down", 14)} 方向</b>答案的数值更大或更小</div>
</div>"""
    cards = []
    for item in commands:
        admin_badge = (
            '<span class="badge">管理员</span>' if item.get("admin_only") else ""
        )
        cards.append(
            f'<div class="cmd{" admin" if item.get("admin_only") else ""}"><div class="cat"><span>{_text(item.get("category", "命令"))}</span>{admin_badge}</div>'
            f'<code>{_text(item.get("command", ""))}</code>'
            f'<p>{_text(item.get("description", ""))}</p></div>'
        )
    grid = "".join(cards) or '<div class="empty">暂无可用命令</div>'
    body = _header(
        {"difficulty_label": "普通", "difficulty_key": "standard", "max_attempts": 0, "remaining": 0},
        title="玩法与指令",
        eyebrow="WOWSGUESS · 窝一把猜战舰",
        pills=[f"每局最多 {max_attempts} 次"],
        show_chances=False,
    )
    body += how
    body += f'<div class="cmds">{grid}</div>'
    body += _foot(
        _legend(),
        f"等级支持 V-XI（阿拉伯数字或罗马数字），范围如 <b>8-10</b>；每局最多 {max_attempts} 次。",
    )
    return _page("WowsGuess 指令帮助", _board(body))
