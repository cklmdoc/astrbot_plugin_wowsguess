"""Persistent, session-isolated endless WowsGuess rounds."""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Any

from .catalog import Catalogue
from .core import submit_guess
from .tier import format_tier_range


class GameService:
    """Keep one independent saved round per AstrBot session id."""

    def __init__(self, data_dir: Path, max_attempts: int = 15) -> None:
        self.games_dir = data_dir / "games"
        self.games_dir.mkdir(parents=True, exist_ok=True)
        self.max_attempts = max_attempts
        self._lock = asyncio.Lock()

    @staticmethod
    def _safe_session(session_id: str) -> str:
        import hashlib

        return hashlib.sha256(session_id.encode("utf-8")).hexdigest()

    def _path(self, session_id: str) -> Path:
        return self.games_dir / f"{self._safe_session(session_id)}.json"

    def _read(self, session_id: str) -> dict[str, Any] | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return state if isinstance(state, dict) else None

    def _write(self, session_id: str, state: dict[str, Any]) -> None:
        path = self._path(session_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    async def start(
        self,
        session_id: str,
        difficulty: str,
        catalogue: Catalogue,
        *,
        require_finish: bool = False,
        tier_range: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        if difficulty not in {"standard", "hard"}:
            raise ValueError("难度只能是「普通」或「困难」。")
        pool = catalogue.pool(difficulty)
        if not pool or not pool.get("answers"):
            raise ValueError(f"{difficulty} 难度暂时没有可用题库。")
        answer_indices = list(pool["answers"])
        if tier_range is not None:
            minimum, maximum = tier_range
            answer_indices = [
                ship_index
                for ship_index in answer_indices
                if (ship := catalogue.ship(str(ship_index)))
                and minimum <= ship.get("attrs", {}).get("level", 0) <= maximum
            ]
            if not answer_indices:
                raise ValueError(
                    f"{format_tier_range(tier_range)} 在当前难度题库中没有可用答案。"
                )
        async with self._lock:
            current = self._read(session_id)
            if require_finish and current and current.get("status") == "playing":
                raise ValueError("当前游戏尚未结束，请先猜中、用完次数或发送 `/猜船 放弃`。")
            answer_index = random.choice(answer_indices)
            state = {
                "difficulty": difficulty,
                "tierRange": list(tier_range) if tier_range is not None else None,
                "answerIndex": answer_index,
                "guesses": [],
                "players": [],
                "status": "playing",
            }
            self._write(session_id, state)
            return state

    async def current(self, session_id: str) -> dict[str, Any] | None:
        async with self._lock:
            return self._read(session_id)

    async def guess(
        self,
        session_id: str,
        guess_ship: dict[str, Any],
        catalogue: Catalogue,
        *,
        player: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        async with self._lock:
            state = self._read(session_id)
            if not state:
                raise ValueError("本会话还没有游戏，请先发送 `/猜船 开始`。")
            pool = catalogue.pool(str(state.get("difficulty")))
            answer = catalogue.ship(str(state.get("answerIndex")))
            if not pool or not answer:
                raise ValueError("本局使用的题库已失效，请发送 `/猜船 开始` 新开一局。")
            updated, row = submit_guess(
                state,
                guess_ship,
                answer,
                pool,
                catalogue.definitions,
                max_attempts=self.max_attempts,
            )
            previous = list(state.get("players") or [""] * len(state.get("guesses") or []))
            updated = {**updated, "players": previous + [str(player or "")]}
            self._write(session_id, updated)
            return updated, row, answer

    async def give_up(self, session_id: str, catalogue: Catalogue) -> tuple[dict[str, Any], dict[str, Any]]:
        async with self._lock:
            state = self._read(session_id)
            if not state:
                raise ValueError("本会话还没有进行中的游戏。")
            if state.get("status") == "playing":
                state = {**state, "status": "lost"}
                self._write(session_id, state)
            answer = catalogue.ship(str(state.get("answerIndex")))
            if not answer:
                raise ValueError("无法读取本局答案，请开始新游戏。")
            return state, answer
