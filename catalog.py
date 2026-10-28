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
        self._exact_names: dict[str, list[dict[str, Any]]] = {}
        for ship in self.ships:
            for name in self._ship_names(ship):
                self._exact_names.setdefault(name, []).append(ship)

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

        exact = list(self._exact_names.get(needle, ()))
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

    def match_exact(self, query: str) -> dict[str, Any] | None:
        """Return the only ship whose index/name/nameZh/aliasZh exactly equals query.

        Unlike :meth:`resolve`, this never falls back to partial matching, so it is
        safe for free-text chat input: a sentence that merely contains a ship name
        does not turn into a guess.
        """
        ships = self._exact_names.get(self._normalise(query))
        return ships[0] if ships and len(ships) == 1 else None

    def ship(self, index: str) -> dict[str, Any] | None:
        return self.by_index.get(index)

    def pool(self, difficulty: str) -> dict[str, Any] | None:
        return self.pools.get(difficulty)
