"""Cached WowsGuess catalogue access and safe ship-name resolution."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


class Catalogue:
    """In-memory view of the public ships, attribute definitions, and pools."""

    def __init__(
        self,
        ships: Iterable[dict[str, Any]],
        attributes: dict[str, Any],
        pools: dict[str, dict[str, Any]],
    ) -> None:
        self.ships = list(ships)
        self.attributes = attributes
        self.pools = pools
        self.by_index = {str(ship["index"]): ship for ship in self.ships}
        self.definitions = {
            str(definition["key"]): definition
            for definition in attributes.get("attributes", [])
        }

    @staticmethod
    def _normalise(value: str) -> str:
        return " ".join(value.casefold().strip().split())

    @classmethod
    def _ship_names(cls, ship: dict[str, Any]) -> set[str]:
        return {
            cls._normalise(str(value))
            for value in (
                ship.get("index"),
                ship.get("name"),
                ship.get("nameZh"),
                ship.get("aliasZh"),
            )
            if value
        }

    def resolve(self, query: str, limit: int = 8) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Resolve an exact unique ship name or return candidates for disambiguation."""
        needle = self._normalise(query)
        if not needle:
            return None, []

        exact = [ship for ship in self.ships if needle in self._ship_names(ship)]
        if len(exact) == 1:
            return exact[0], []
        if exact:
            return None, exact[:limit]

        partial = [
            ship
            for ship in self.ships
            if any(needle in name for name in self._ship_names(ship))
        ]
        return (partial[0], []) if len(partial) == 1 else (None, partial[:limit])

    def ship(self, index: str) -> dict[str, Any] | None:
        return self.by_index.get(index)

    def pool(self, difficulty: str) -> dict[str, Any] | None:
        return self.pools.get(difficulty)
