"""Catalogue name resolution: exact vs partial, and the free-text-safe exact matcher."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_wowsguess.catalog import Catalogue  # noqa: E402

SHIPS = [
    {
        "index": "PZSC719",
        "name": "Tianjin",
        "nameZh": "天津",
        "aliasZh": "津海",
        "attrs": {"level": 10, "nation": "Pan_Asia"},
    },
    {
        "index": "PBSB609",
        "name": "Stalingrad",
        "nameZh": "斯大林格勒",
        "attrs": {"level": 10, "nation": "Russia"},
    },
    {"index": "PASB001", "name": "Alabama", "nameZh": "阿拉巴马", "attrs": {"level": 8, "nation": "USA"}},
    {"index": "PBSB002", "name": "Alabama", "nameZh": "阿拉巴马二号", "attrs": {"level": 8, "nation": "USA"}},
]
ATTRIBUTES = {
    "attributes": [
        {"key": "level", "label": "等级", "kind": "ordered", "close": {"abs": 1}, "unit": "级", "decimals": 0},
        {
            "key": "nation",
            "label": "国家",
            "kind": "discrete",
            "labels": {"Pan_Asia": "泛亚", "Russia": "苏联", "USA": "美国"},
        },
    ]
}


def make_catalogue() -> Catalogue:
    return Catalogue(SHIPS, ATTRIBUTES, {})


def test_resolve_exact_names():
    catalogue = make_catalogue()
    for query in ("天津", "tianjin", "Tianjin", "PZSC719", "pzsc719", "津海", " 天津 "):
        ship, candidates = catalogue.resolve(query)
        assert ship is not None and ship["index"] == "PZSC719", query
        assert candidates == []


def test_resolve_partial_and_ambiguous():
    catalogue = make_catalogue()
    # unique partial still resolves for command input
    ship, candidates = catalogue.resolve("斯大林")
    assert ship is not None and ship["index"] == "PBSB609" and candidates == []
    # duplicate English name cannot be resolved to one ship
    ship, candidates = catalogue.resolve("Alabama")
    assert ship is None and {item["index"] for item in candidates} == {"PASB001", "PBSB002"}
    ship, candidates = catalogue.resolve("不存在的船")
    assert ship is None and candidates == []


def test_match_exact_is_strict():
    catalogue = make_catalogue()
    assert catalogue.match_exact("天津")["index"] == "PZSC719"
    assert catalogue.match_exact("PZSC719")["index"] == "PZSC719"
    # partial names are not accepted for free text
    assert catalogue.match_exact("天津号") is None
    assert catalogue.match_exact("斯大林") is None
    assert catalogue.match_exact("胡德好强啊") is None
    # ambiguous exact names are rejected on purpose
    assert catalogue.match_exact("Alabama") is None
    assert catalogue.match_exact("") is None
