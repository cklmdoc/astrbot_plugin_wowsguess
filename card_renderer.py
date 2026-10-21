"""HTML card rendering for WowsGuess feedback images."""

from __future__ import annotations

from html import escape
from typing import Any


_STATE_LABELS = {"exact": "完全相同", "close": "接近", "far": "不匹配"}


def _text(value: Any) -> str:
    return escape(str(value), quote=True)


def make_guess_card_view(
    *,
    attempt: int,
    max_attempts: int,
    ship_name: str,
    ship_index: str,
    difficulty: str,
    remaining: int,
    status_text: str,
    feedback: list[dict[str, Any]],
    definitions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Convert domain feedback to template-ready localized card data."""
    formatted = []
    for result in feedback:
        definition = definitions[result["key"]]
        value = result["guessValue"]
        key = result["key"]
        if key in {"nation", "species"}:
            value_text = str(definition.get("labels", {}).get(str(value), value))
        elif isinstance(value, float):
            decimals = definition.get("decimals")
            value_text = f"{value:.{decimals}f}" if isinstance(decimals, int) else f"{value:g}"
        else:
            value_text = str(value)
        value_text += str(definition.get("unit") or "")
        direction = {"up": "⬆", "down": "⬇"}.get(result.get("direction"), "")
        formatted.append(
            {
                "label": str(definition.get("label") or key),
                "value": value_text,
                "state": result["state"],
                "arrow": direction,
            }
        )
    return {
        "attempt": attempt,
        "max_attempts": max_attempts,
        "ship_name": ship_name,
        "ship_index": ship_index,
        "difficulty": "普通" if difficulty == "standard" else "困难",
        "remaining": remaining,
        "status_text": status_text,
        "feedback": formatted,
    }


def build_guess_card_html(view: dict[str, Any]) -> str:
    """Build a self-contained, injection-safe multi-card feedback board."""
    cards = []
    for item in view.get("feedback", []):
        state = str(item.get("state", "far"))
        if state not in _STATE_LABELS:
            state = "far"
        arrow = str(item.get("arrow", "")).strip()
        value = _text(item.get("value", ""))
        cards.append(
            f'''<article class="tile {state}">
  <div class="tile-head"><span>{_text(item.get("label", "属性"))}</span><small>{_STATE_LABELS[state]}</small></div>
  <div class="tile-value">{value}{(" <b>" + _text(arrow) + "</b>") if arrow else ""}</div>
</article>'''
        )

    grid = "\n".join(cards) or '<div class="empty">暂无可展示的属性反馈</div>'
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:28px; background:#0e1726; color:#edf4ff; font-family:"Noto Sans CJK SC","Noto Sans SC","Microsoft YaHei",Arial,sans-serif; }}
.board {{ width:1060px; margin:0 auto; border:1px solid #30415c; border-radius:22px; overflow:hidden; background:linear-gradient(145deg,#142238,#0e1726 62%); box-shadow:0 16px 48px rgba(0,0,0,.35); }}
.header {{ display:flex; align-items:center; justify-content:space-between; gap:24px; padding:25px 28px 22px; border-bottom:1px solid #30415c; }}
.eyebrow {{ margin:0 0 7px; color:#99acc7; font-size:17px; letter-spacing:.08em; }}
h1 {{ margin:0; font-size:35px; line-height:1.18; letter-spacing:.01em; }}
.code {{ color:#a9bad2; font-size:18px; font-weight:500; }}
.counter {{ min-width:150px; padding:13px 15px; border-radius:13px; text-align:center; background:#1c2d47; color:#dceaff; font-size:18px; line-height:1.5; }}
.counter strong {{ display:block; color:#fff; font-size:25px; }}
.grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; padding:22px 28px 26px; }}
.tile {{ min-height:116px; padding:15px 16px; border-radius:15px; border:1px solid transparent; background:#25364f; }}
.tile.exact {{ background:linear-gradient(145deg,#135f4d,#0d493d); border-color:#2c9d7c; }}
.tile.close {{ background:linear-gradient(145deg,#775b16,#56410f); border-color:#c9a333; }}
.tile.far {{ background:linear-gradient(145deg,#34445c,#26364d); border-color:#526680; }}
.tile-head {{ display:flex; align-items:center; justify-content:space-between; gap:8px; color:#d6e2f1; font-size:18px; }}
.tile-head small {{ flex:none; color:#bac9da; font-size:13px; }}
.tile-value {{ margin-top:17px; color:#fff; font-size:25px; font-weight:700; word-break:break-word; line-height:1.15; }}
.tile-value b {{ display:inline-block; margin-left:5px; color:#fff; font-size:23px; }}
.footer {{ display:flex; align-items:center; justify-content:space-between; gap:16px; padding:15px 28px; border-top:1px solid #30415c; color:#aebfd5; font-size:16px; }}
.footer strong {{ color:#eff7ff; }}
.empty {{ grid-column:1/-1; padding:25px; color:#aebfd5; text-align:center; }}
</style>
</head>
<body>
<section class="board">
  <header class="header">
    <div>
      <p class="eyebrow">WowsGuess · {_text(view.get("difficulty", ""))} · 第 {_text(view.get("attempt", ""))} 次猜测</p>
      <h1>{_text(view.get("ship_name", "未知舰船"))} <span class="code">{_text(view.get("ship_index", ""))}</span></h1>
    </div>
    <div class="counter"><strong>{_text(view.get("remaining", 0))} 次</strong>剩余机会</div>
  </header>
  <main class="grid">{grid}</main>
  <footer class="footer"><span>🟩 完全相同　🟨 接近　⬜ 不匹配　⬆⬇ 答案数值方向</span><strong>{_text(view.get("status_text", ""))}</strong></footer>
</section>
</body>
</html>'''
