"""Tier selection parsing for WowsGuess start commands."""

from __future__ import annotations

import re


MIN_TIER = 5
MAX_TIER = 11

_ROMAN_TO_TIER = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "Ⅰ": 1,
    "Ⅱ": 2,
    "Ⅲ": 3,
    "Ⅳ": 4,
    "Ⅴ": 5,
    "Ⅵ": 6,
    "Ⅶ": 7,
    "Ⅷ": 8,
    "Ⅸ": 9,
    "Ⅹ": 10,
    "Ⅺ": 11,
}
_TIER_TO_ROMAN = ("", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI")
_RANGE_PATTERN = re.compile(r"^\s*([^\s\-–—]+)\s*(?:[-–—]\s*([^\s\-–—]+))?\s*$", re.IGNORECASE)


def _parse_tier_token(value: str) -> int:
    normalized = value.strip().upper()
    if normalized.isdecimal():
        tier = int(normalized)
    else:
        tier = _ROMAN_TO_TIER.get(normalized, 0)
    if not MIN_TIER <= tier <= MAX_TIER:
        raise ValueError(f"等级必须在 {MIN_TIER}-{MAX_TIER}（V-XI）之间。")
    return tier


def parse_tier_range(value: str) -> tuple[int, int]:
    """Parse one selected tier or inclusive tier range using Arabic/Roman input."""
    match = _RANGE_PATTERN.fullmatch(value)
    if not match:
        raise ValueError("等级格式应为 V、VIII-X 或 8-10。")
    lower = _parse_tier_token(match.group(1))
    upper = _parse_tier_token(match.group(2)) if match.group(2) else lower
    return min(lower, upper), max(lower, upper)


def parse_start_options(value: str) -> tuple[str, tuple[int, int] | None]:
    """Parse optional difficulty plus one optional single/range tier selector."""
    tokens = value.strip().split()
    if len(tokens) > 2:
        raise ValueError("用法：/猜船 开始 [普通|困难] [等级或等级范围]。")
    difficulty = "standard"
    if tokens and tokens[0].casefold() in {"普通", "标准", "standard", "normal", "困难", "hard"}:
        difficulty = (
            "hard" if tokens.pop(0).casefold() in {"困难", "hard"} else "standard"
        )
    if not tokens:
        return difficulty, None
    if len(tokens) != 1:
        raise ValueError("用法：/猜船 开始 [普通|困难] [等级或等级范围]。")
    return difficulty, parse_tier_range(tokens[0])


def format_tier_range(tier_range: tuple[int, int]) -> str:
    """Format an inclusive tier range using standard ASCII Roman numerals."""
    lower, upper = tier_range
    if not (MIN_TIER <= lower <= upper <= MAX_TIER):
        raise ValueError("等级范围无效。")
    return f"{_TIER_TO_ROMAN[lower]}级" if lower == upper else f"{_TIER_TO_ROMAN[lower]}-{_TIER_TO_ROMAN[upper]}级"
