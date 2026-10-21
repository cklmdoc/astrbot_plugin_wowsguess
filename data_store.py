"""Download and cache the public static WowsGuess data files."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import aiohttp

from .catalog import Catalogue


DATA_ORIGIN = "https://www.woyiba.com/data"
REQUIRED_FILES = (
    "ships.json",
    "attributes.json",
    "difficulties.json",
    "pools/standard.json",
    "pools/hard.json",
)


class DataUnavailableError(RuntimeError):
    """Raised when the public game catalogue cannot be obtained or validated."""


class WowsGuessDataStore:
    """Use a local cache and refresh it atomically from the public data origin."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.cache_dir = data_dir / "woyiba_data"
        self._catalogue: Catalogue | None = None
        self._lock = asyncio.Lock()

    async def get_catalogue(self, refresh: bool = False) -> Catalogue:
        async with self._lock:
            if self._catalogue is not None and not refresh:
                return self._catalogue
            if refresh or not self._has_complete_cache():
                try:
                    await self._refresh_cache()
                except (aiohttp.ClientError, asyncio.TimeoutError, DataUnavailableError) as exc:
                    if not self._has_complete_cache():
                        raise DataUnavailableError(
                            "无法下载窝一把公开题库，且本地没有可用缓存。请检查网络后重试。"
                        ) from exc
            self._catalogue = self._load_cache()
            return self._catalogue

    def _has_complete_cache(self) -> bool:
        return all((self.cache_dir / filename).is_file() for filename in REQUIRED_FILES)

    async def _refresh_cache(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        staging_dir = self.data_dir / "woyiba_data.staging"
        if staging_dir.exists():
            for path in sorted(staging_dir.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            staging_dir.rmdir()
        staging_dir.mkdir(parents=True)

        timeout = aiohttp.ClientTimeout(total=35)
        headers = {"User-Agent": "AstrBot-WowsGuess/1.0 (+https://www.woyiba.com/)"}
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                for filename in REQUIRED_FILES:
                    target = staging_dir / filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    async with session.get(f"{DATA_ORIGIN}/{filename}") as response:
                        response.raise_for_status()
                        target.write_bytes(await response.read())
            self._validate_directory(staging_dir)
            if self.cache_dir.exists():
                backup_dir = self.data_dir / "woyiba_data.backup"
                if backup_dir.exists():
                    for path in sorted(backup_dir.rglob("*"), reverse=True):
                        if path.is_file():
                            path.unlink()
                        elif path.is_dir():
                            path.rmdir()
                    backup_dir.rmdir()
                os.replace(self.cache_dir, backup_dir)
                try:
                    os.replace(staging_dir, self.cache_dir)
                except OSError:
                    os.replace(backup_dir, self.cache_dir)
                    raise
                for path in sorted(backup_dir.rglob("*"), reverse=True):
                    if path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        path.rmdir()
                backup_dir.rmdir()
            else:
                os.replace(staging_dir, self.cache_dir)
        finally:
            if staging_dir.exists():
                for path in sorted(staging_dir.rglob("*"), reverse=True):
                    if path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        path.rmdir()
                staging_dir.rmdir()

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DataUnavailableError(f"题库文件损坏：{path.name}") from exc
        if not isinstance(data, dict):
            raise DataUnavailableError(f"题库文件格式无效：{path.name}")
        return data

    def _validate_directory(self, directory: Path) -> None:
        ships = self._read_json(directory / "ships.json").get("ships")
        attributes = self._read_json(directory / "attributes.json").get("attributes")
        if not isinstance(ships, list) or not ships:
            raise DataUnavailableError("舰船题库为空或格式无效")
        if not isinstance(attributes, list) or not attributes:
            raise DataUnavailableError("属性定义为空或格式无效")
        for difficulty in ("standard", "hard"):
            pool = self._read_json(directory / f"pools/{difficulty}.json")
            if not isinstance(pool.get("answers"), list) or not isinstance(
                pool.get("attributeKeys"), list
            ):
                raise DataUnavailableError(f"{difficulty} 题库格式无效")

    def _load_cache(self) -> Catalogue:
        self._validate_directory(self.cache_dir)
        ships = self._read_json(self.cache_dir / "ships.json")["ships"]
        attributes = self._read_json(self.cache_dir / "attributes.json")
        pools = {
            difficulty: self._read_json(self.cache_dir / f"pools/{difficulty}.json")
            for difficulty in ("standard", "hard")
        }
        return Catalogue(ships=ships, attributes=attributes, pools=pools)
