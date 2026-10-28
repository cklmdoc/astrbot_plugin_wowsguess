"""Pure gameplay primitives reconstructed from WowsGuess's client bundle."""

from __future__ import annotations

from typing import Any


FLOAT_EPSILON = 1e-9
RELATIVE_EPSILON = 1e-12


def _as_float(value: Any) -> float | None:
    """Best-effort numeric conversion; ``None`` marks a missing/invalid attribute."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_close(guess_value: float, answer_value: float, close: dict[str, Any] | None) -> bool:
    """Match the site's absolute/relative tolerance rule."""
    if not close:
        return False
    difference = abs(guess_value - answer_value)
    absolute = close.get("abs")
    if absolute is not None:
        try:
            absolute_value = float(absolute)
        except (TypeError, ValueError):
            return False
        return difference <= absolute_value + FLOAT_EPSILON * max(
            1, abs(guess_value), abs(answer_value)
        )
    relative = close.get("rel")
    if relative is None:
        return False
    try:
        relative_value = float(relative)
    except (TypeError, ValueError):
        return False
    return (
        answer_value != 0
        and difference / abs(answer_value) <= relative_value + RELATIVE_EPSILON
    )


def compare_attribute(
    guess_value: Any, answer_value: Any, definition: dict[str, Any]
) -> dict[str, Any]:
    """Return WowsGuess-compatible feedback for one ship attribute."""
    key = str(definition.get("key", "")) if isinstance(definition, dict) else ""
    if definition.get("kind") == "discrete":
        return {
            "key": key,
            "state": "exact" if guess_value is not None and guess_value == answer_value else "far",
            "direction": None,
            "guessValue": guess_value,
        }

    guess_number = _as_float(guess_value)
    answer_number = _as_float(answer_value)
    if guess_number is None or answer_number is None:
        # Malformed payload: treat it as unmatched instead of crashing the round.
        return {
            "key": key,
            "state": "far",
            "direction": None,
            "guessValue": guess_value,
        }
    if guess_number == answer_number:
        return {
            "key": key,
            "state": "exact",
            "direction": None,
            "guessValue": guess_value,
            "delta": 0,
        }
    return {
        "key": key,
        "state": "close"
        if _is_close(guess_number, answer_number, definition.get("close"))
        else "far",
        "direction": "up" if answer_number > guess_number else "down",
        "guessValue": guess_value,
        "delta": answer_number - guess_number,
    }


def ships_equivalent(guess_index: str, answer_index: str, pool: dict[str, Any]) -> bool:
    """Return whether the pool considers two ship variants the same answer."""
    if guess_index == answer_index:
        return True
    group_of = pool.get("groupOf")
    if not isinstance(group_of, dict):
        return False
    guess_group = group_of.get(guess_index)
    answer_group = group_of.get(answer_index)
    return guess_group is not None and answer_group is not None and guess_group == answer_group


def evaluate_guess(
    guess: dict[str, Any],
    answer: dict[str, Any],
    pool: dict[str, Any],
    definitions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build one guess row using pool order and equivalence rules."""
    attribute_keys = pool.get("attributeKeys")
    if not isinstance(attribute_keys, list) or not attribute_keys:
        raise ValueError("题库属性列表无效，请发送 `/猜船 刷新数据` 更新题库。")
    guess_attrs = guess.get("attrs") if isinstance(guess.get("attrs"), dict) else {}
    answer_attrs = answer.get("attrs") if isinstance(answer.get("attrs"), dict) else {}
    feedback = []
    for key in attribute_keys:
        definition = definitions.get(key)
        if not isinstance(definition, dict):
            # A definition that disappeared from the payload must not abort the round.
            continue
        feedback.append(
            compare_attribute(guess_attrs.get(key), answer_attrs.get(key), definition)
        )
    return {
        "feedback": feedback,
        "correct": ships_equivalent(guess["index"], answer["index"], pool),
    }


def submit_guess(
    state: dict[str, Any],
    guess: dict[str, Any],
    answer: dict[str, Any],
    pool: dict[str, Any],
    definitions: dict[str, dict[str, Any]],
    max_attempts: int = 15,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply one unique guess and return the persisted state plus result row."""
    if state.get("status") != "playing":
        raise ValueError("本局已经结束，请发送 `/猜船 开始` 新开一局。")
    guessed_indices = list(state.get("guesses") or [])
    if guess["index"] in guessed_indices:
        raise ValueError("这艘船已经猜过了，换一艘试试。")
    if len(guessed_indices) >= max_attempts:
        raise ValueError("本局次数已经用完，请发送 `/猜船 开始` 新开一局。")

    row = evaluate_guess(guess, answer, pool, definitions)
    row["attempt"] = len(guessed_indices) + 1
    guessed_indices.append(guess["index"])
    status = "won" if row["correct"] else "lost" if len(guessed_indices) >= max_attempts else "playing"
    updated = {**state, "guesses": guessed_indices, "status": status}
    return updated, row
