"""Download and cache the public static WowsGuess data files."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import aiohttp

from .catalog import Catalogue


DATA_ORIGIN = "https://www.woyiba.com/data"
ALLOWED_HOSTS = frozenset({"www.woyiba.com", "woyiba.com"})
REQUIRED_FILES = (
    "ships.json",
    "attributes.json",
    "difficulties.json",
    "pools/standard.json",
    "pools/hard.json",
)
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 10.0
REFRESH_TIMEOUT_SECONDS = 30.0
REFRESH_GRACE_SECONDS = 3.0
CHUNK_BYTES = 64 * 1024


class DataUnavailableError(RuntimeError):
    """Raised when the public game catalogue cannot be obtained or validated."""


def _split_url(value: str) -> tuple[str, str]:
    """Return (scheme, hostname) for ``value``; malformed URLs yield empty parts."""
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(str(value))
    except (TypeError, ValueError):  # pragma: no cover - urlsplit rarely raises
        return "", ""
    return parts.scheme.lower(), (parts.hostname or "").lower().strip(".")


class WowsGuessDataStore:
    """Use a local cache and refresh it atomically from the public data origin."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.cache_dir = data_dir / "woyiba_data"
        self.last_refresh_ok = True
        self._catalogue: Catalogue | None = None
        self._load_lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._last_refresh_completed = 0.0

    # -- public API ------------------------------------------------------
    async def get_catalogue(self, refresh: bool = False) -> Catalogue:
        """Return a validated catalogue; downloads never run under the load lock."""
        if self._catalogue is not None and not refresh:
            return self._catalogue
        refresh_error: Exception | None = None
        if refresh or not self._cache_usable():
            try:
                await self._refresh_cache()
                self.last_refresh_ok = True
            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
                OSError,
                DataUnavailableError,
            ) as exc:
                refresh_error = exc
                self.last_refresh_ok = False
        if not self._cache_usable():
            raise DataUnavailableError(
                "无法下载窝一把公开题库，且本地没有可用缓存。请检查网络后重试。"
            ) from (refresh_error or DataUnavailableError("本地题库缓存无效。"))
        async with self._load_lock:
            if self._catalogue is None:
                self._catalogue = self._load_cache()
            return self._catalogue

    def has_usable_cache(self) -> bool:
        """Whether a complete, structurally valid cache is already on disk."""
        return self._cache_usable()

    # -- cache refresh ---------------------------------------------------
    def _cache_usable(self) -> bool:
        try:
            self._validate_directory(self.cache_dir)
        except DataUnavailableError:
            return False
        return True

    async def _refresh_cache(self) -> None:
        async with self._refresh_lock:
            if time.monotonic() - self._last_refresh_completed < REFRESH_GRACE_SECONDS:
                return  # a concurrent caller refreshed moments ago
            await self._refresh_cache_locked()
            self._last_refresh_completed = time.monotonic()

    async def _refresh_cache_locked(self) -> None:
        staging_dir = self.data_dir / "woyiba_data.staging"
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._remove_tree(staging_dir)
            staging_dir.mkdir(parents=True)
            timeout = aiohttp.ClientTimeout(
                total=None,
                connect=DOWNLOAD_TIMEOUT_SECONDS,
                sock_read=DOWNLOAD_TIMEOUT_SECONDS,
            )
            headers = {"User-Agent": "AstrBot-WowsGuess/1.0 (+https://www.woyiba.com/)"}
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                await asyncio.wait_for(
                    self._download_all(session, staging_dir),
                    timeout=REFRESH_TIMEOUT_SECONDS,
                )
            self._validate_directory(staging_dir)
            self._install(staging_dir)
        except OSError as exc:
            raise DataUnavailableError("题库缓存写入失败，请检查磁盘空间与目录权限。") from exc
        finally:
            try:
                self._remove_tree(staging_dir)
            except OSError:  # pragma: no cover - cleanup must not mask the real error
                pass

    async def _download_all(self, session: aiohttp.ClientSession, staging_dir: Path) -> None:
        payloads: dict[str, bytes] = {}
        total = 0

        async def fetch(filename: str) -> None:
            nonlocal total
            async with session.get(
                f"{DATA_ORIGIN}/{filename}",
                allow_redirects=True,
                max_redirects=3,
            ) as response:
                response.raise_for_status()
                self._check_download_url(str(response.url))
                payload = await self._read_limited(response, filename)
            total += len(payload)
            if total > MAX_TOTAL_BYTES:
                raise DataUnavailableError("题库下载超过总大小上限，已中止。")
            payloads[filename] = payload

        await asyncio.gather(*(fetch(filename) for filename in REQUIRED_FILES))
        for filename, payload in payloads.items():
            target = staging_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)

    @staticmethod
    def _check_download_url(url: str) -> None:
        scheme, host = _split_url(url)
        if scheme != "https" or host not in ALLOWED_HOSTS:
            raise DataUnavailableError(f"题库下载地址异常（{url}），已中止。")

    @staticmethod
    async def _read_limited(response: aiohttp.ClientResponse, filename: str) -> bytes:
        declared = getattr(response, "content_length", None)
        if declared is not None and declared > MAX_FILE_BYTES:
            raise DataUnavailableError(f"{filename} 超过单文件大小上限，已中止。")
        chunks = bytearray()
        async for chunk in response.content.iter_chunked(CHUNK_BYTES):
            chunks.extend(chunk)
            if len(chunks) > MAX_FILE_BYTES:
                raise DataUnavailableError(f"{filename} 超过单文件大小上限，已中止。")
        if not chunks:
            raise DataUnavailableError(f"{filename} 内容为空。")
        return bytes(chunks)

    def _install(self, staging_dir: Path) -> None:
        backup_dir = self.data_dir / "woyiba_data.backup"
        self._remove_tree(backup_dir)
        if self.cache_dir.exists():
            os.replace(self.cache_dir, backup_dir)
            try:
                os.replace(staging_dir, self.cache_dir)
            except OSError:
                os.replace(backup_dir, self.cache_dir)
                raise
            self._remove_tree(backup_dir)
        else:
            os.replace(staging_dir, self.cache_dir)

    @staticmethod
    def _remove_tree(directory: Path) -> None:
        if not directory.exists():
            return
        for child in sorted(directory.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        directory.rmdir()

    # -- validation / loading -------------------------------------------
    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataUnavailableError(f"题库文件损坏：{path.name}") from exc
        if not isinstance(data, dict):
            raise DataUnavailableError(f"题库文件格式无效：{path.name}")
        return data

    def _validate_directory(self, directory: Path) -> None:
        """Validate structure deeply enough that a malformed payload never reaches gameplay."""
        ships_payload = self._read_json(directory / "ships.json")
        ships = ships_payload.get("ships")
        if not isinstance(ships, list) or not ships:
            raise DataUnavailableError("舰船题库为空或格式无效")
        ship_indices: set[str] = set()
        for position, ship in enumerate(ships):
            if not isinstance(ship, dict):
                raise DataUnavailableError(f"舰船题库第 {position + 1} 项格式无效")
            index = ship.get("index")
            if not isinstance(index, str) or not index.strip():
                raise DataUnavailableError(f"舰船题库第 {position + 1} 项缺少编号")
            if index in ship_indices:
                raise DataUnavailableError(f"舰船题库编号重复：{index}")
            ship_indices.add(index)
            if not isinstance(ship.get("name"), str) or not isinstance(
                ship.get("nameZh"), str
            ):
                raise DataUnavailableError(f"舰船 {index} 缺少名称字段")
            attrs = ship.get("attrs")
            if not isinstance(attrs, dict):
                raise DataUnavailableError(f"舰船 {index} 属性格式无效")
            for attribute, value in attrs.items():
                if not isinstance(attribute, str):
                    raise DataUnavailableError(f"舰船 {index} 存在无效属性名")
                if value is not None and not isinstance(value, (str, int, float)):
                    raise DataUnavailableError(f"舰船 {index} 属性 {attribute} 类型无效")

        attribute_payload = self._read_json(directory / "attributes.json")
        definitions = attribute_payload.get("attributes")
        if not isinstance(definitions, list) or not definitions:
            raise DataUnavailableError("属性定义为空或格式无效")
        definition_keys: set[str] = set()
        for position, definition in enumerate(definitions):
            if not isinstance(definition, dict):
                raise DataUnavailableError(f"属性定义第 {position + 1} 项格式无效")
            key = definition.get("key")
            if not isinstance(key, str) or not key.strip():
                raise DataUnavailableError(f"属性定义第 {position + 1} 项缺少字段名")
            if key in definition_keys:
                raise DataUnavailableError(f"属性定义重复：{key}")
            definition_keys.add(key)
            kind = definition.get("kind")
            if kind is not None and not isinstance(kind, str):
                raise DataUnavailableError(f"属性 {key} 的类型定义无效")

        for difficulty in ("standard", "hard"):
            pool = self._read_json(directory / f"pools/{difficulty}.json")
            answers = pool.get("answers")
            if not isinstance(answers, list) or not answers:
                raise DataUnavailableError(f"{difficulty} 题库答案为空或格式无效")
            for answer in answers:
                if isinstance(answer, bool) or not isinstance(answer, (str, int)):
                    raise DataUnavailableError(f"{difficulty} 题库存在无效答案编号")
                if str(answer) not in ship_indices:
                    raise DataUnavailableError(
                        f"{difficulty} 题库答案 {answer} 不在舰船列表中"
                    )
            attribute_keys = pool.get("attributeKeys")
            if not isinstance(attribute_keys, list) or not attribute_keys:
                raise DataUnavailableError(f"{difficulty} 题库属性列表无效")
            for key in attribute_keys:
                if not isinstance(key, str) or key not in definition_keys:
                    raise DataUnavailableError(f"{difficulty} 题库引用了未知属性 {key!r}")
            group_of = pool.get("groupOf")
            if group_of is not None:
                if not isinstance(group_of, dict):
                    raise DataUnavailableError(f"{difficulty} 题库重复组格式无效")
                for key, value in group_of.items():
                    if not isinstance(key, str) or not isinstance(value, (int, str)):
                        raise DataUnavailableError(f"{difficulty} 题库重复组格式无效")

        difficulties = self._read_json(directory / "difficulties.json").get("difficulties")
        if not isinstance(difficulties, list) or not difficulties:
            raise DataUnavailableError("难度配置为空或格式无效")

    def _load_cache(self) -> Catalogue:
        self._validate_directory(self.cache_dir)
        ships = self._read_json(self.cache_dir / "ships.json")["ships"]
        attributes = self._read_json(self.cache_dir / "attributes.json")
        pools = {
            difficulty: self._read_json(self.cache_dir / f"pools/{difficulty}.json")
            for difficulty in ("standard", "hard")
        }
        return Catalogue(ships=ships, attributes=attributes, pools=pools)
