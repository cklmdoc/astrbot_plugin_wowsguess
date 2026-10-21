"""Pure gameplay primitives reconstructed from WowsGuess's client bundle."""

from __future__ import annotations

from typing import Any


FLOAT_EPSILON = 1e-9
RELATIVE_EPSILON = 1e-12


def _is_close(guess_value: float, answer_value: float, close: dict[str, Any] | None) -> bool:
    """Match the site's absolute/relative tolerance rule."""
    if not close:
        return False
    difference = abs(guess_value - answer_value)
    absolute = close.get("abs")
    if absolute is not None:
        return difference <= float(absolute) + FLOAT_EPSILON * max(
            1, abs(guess_value), abs(answer_value)
        )
    relative = close.get("rel")
    return (
        relative is not None
        and answer_value != 0
        and difference / abs(answer_value) <= float(relative) + RELATIVE_EPSILON
    )


def compare_attribute(
    guess_value: Any, answer_value: Any, definition: dict[str, Any]
) -> dict[str, Any]:
    """Return WowsGuess-compatible feedback for one ship attribute."""
    key = str(definition["key"])
    if definition.get("kind") == "discrete":
        return {
            "key": key,
            "state": "exact" if guess_value == answer_value else "far",
            "direction": None,
            "guessValue": guess_value,
        }

    guess_number = float(guess_value)
    answer_number = float(answer_value)
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
    group_of = pool.get("groupOf") or {}
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
    feedback = []
    for key in pool["attributeKeys"]:
        definition = definitions.get(key)
        if definition is None:
            raise ValueError(f"Unknown attribute {key!r}; refresh site data")
        feedback.append(
            compare_attribute(
                guess.get("attrs", {}).get(key),
                answer.get("attrs", {}).get(key),
                definition,
            )
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
    max_attempts: int = 8,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply one unique guess and return the persisted state plus result row."""
    if state.get("status") != "playing":
        raise ValueError("This game has already ended")
    guessed_indices = list(state.get("guesses") or [])
    if guess["index"] in guessed_indices:
        raise ValueError("That ship was already guessed")
    if len(guessed_indices) >= max_attempts:
        raise ValueError("No attempts remain")

    row = evaluate_guess(guess, answer, pool, definitions)
    row["attempt"] = len(guessed_indices) + 1
    guessed_indices.append(guess["index"])
    status = "won" if row["correct"] else "lost" if len(guessed_indices) >= max_attempts else "playing"
    updated = {**state, "guesses": guessed_indices, "status": status}
    return updated, row
