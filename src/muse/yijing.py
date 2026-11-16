"""Deterministic, source-aware I Ching three-coin line calculations.

This module computes symbolic structure only. A seeded simulation is not a
physical coin toss, a prediction, or empirical evidence.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any, Sequence


TRIGRAM_BY_BITS = {
    "111": "乾",
    "110": "兑",
    "101": "离",
    "100": "震",
    "011": "巽",
    "010": "坎",
    "001": "艮",
    "000": "坤",
}

LINE_LABELS = {
    6: "老阴",
    7: "少阳",
    8: "少阴",
    9: "老阳",
}

METHOD_SOURCE = "https://github.com/ProfesseurHaipeng/iching-research-skill"
CALCULATION_VERSION = "muse-yijing-three-coin-v1"
KING_WEN_SEQUENCE = (
    "乾", "坤", "屯", "蒙", "需", "訟", "師", "比",
    "小畜", "履", "泰", "否", "同人", "大有", "謙", "豫",
    "隨", "蠱", "臨", "觀", "噬嗑", "賁", "剝", "復",
    "無妄", "大畜", "頤", "大過", "坎", "離", "咸", "恆",
    "遯", "大壯", "晉", "明夷", "家人", "睽", "蹇", "解",
    "損", "益", "夬", "姤", "萃", "升", "困", "井",
    "革", "鼎", "震", "艮", "漸", "歸妹", "豐", "旅",
    "巽", "兌", "渙", "節", "中孚", "小過", "既濟", "未濟",
)
KING_WEN_TRIGRAM_ORDER = ("111", "110", "101", "100", "011", "010", "001", "000")
KING_WEN_MATRIX = (
    (1, 43, 14, 34, 9, 5, 26, 11),
    (10, 58, 38, 54, 61, 60, 41, 19),
    (13, 49, 30, 55, 37, 63, 22, 36),
    (25, 17, 21, 51, 42, 3, 27, 24),
    (44, 28, 50, 32, 57, 48, 18, 46),
    (6, 47, 64, 40, 59, 29, 4, 7),
    (33, 31, 56, 62, 53, 39, 52, 15),
    (12, 45, 35, 16, 20, 8, 23, 2),
)
KING_WEN_CATALOG_SOURCES = (
    "https://bazi-atlas.com/hexagrams",
    "https://yiatlas.com/en/hexagrams",
    "https://toea.com/i-ching-hexagrams",
)


def _build_hexagram_catalog() -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for lower_index, lower_bits in enumerate(KING_WEN_TRIGRAM_ORDER):
        for upper_index, upper_bits in enumerate(KING_WEN_TRIGRAM_ORDER):
            number = KING_WEN_MATRIX[lower_index][upper_index]
            bits = lower_bits + upper_bits
            catalog[bits] = {
                "bits_bottom_to_top": bits,
                "king_wen_number": number,
                "king_wen_name": KING_WEN_SEQUENCE[number - 1],
                "unicode_symbol": chr(0x4DC0 + number - 1),
                "lower_trigram": {"bits_bottom_to_top": lower_bits, "name": TRIGRAM_BY_BITS[lower_bits]},
                "upper_trigram": {"bits_bottom_to_top": upper_bits, "name": TRIGRAM_BY_BITS[upper_bits]},
                "catalog_version": "received-king-wen-structural-lookup-v1",
                "catalog_sources": list(KING_WEN_CATALOG_SOURCES),
                "catalog_source_status": "secondary_structural_cross_reference",
            }
    if len(catalog) != 64 or {item["king_wen_number"] for item in catalog.values()} != set(range(1, 65)):
        raise RuntimeError("King Wen structural catalog must contain each of the 64 unique numbers exactly once")
    return catalog


HEXAGRAM_BY_BITS = _build_hexagram_catalog()


def lookup_hexagram(bits_bottom_to_top: str) -> dict[str, Any]:
    """Return the received King Wen identity for an exact six-line binary pattern."""
    if not isinstance(bits_bottom_to_top, str) or len(bits_bottom_to_top) != 6 or any(
        bit not in "01" for bit in bits_bottom_to_top
    ):
        raise ValueError("bits_bottom_to_top must be exactly six binary digits")
    item = HEXAGRAM_BY_BITS.get(bits_bottom_to_top)
    if item is None:
        raise ValueError("hexagram pattern is not present in the structural catalog")
    return {
        **item,
        "lower_trigram": dict(item["lower_trigram"]),
        "upper_trigram": dict(item["upper_trigram"]),
        "catalog_sources": list(item["catalog_sources"]),
    }





def _structural_relations(bits_bottom_to_top: str) -> dict[str, dict[str, Any]]:
    """Return common structural transforms; these are not interpretive verdicts."""
    nuclear_bits = bits_bottom_to_top[1:4] + bits_bottom_to_top[2:5]
    opposite_bits = "".join("0" if bit == "1" else "1" for bit in bits_bottom_to_top)
    inverse_bits = bits_bottom_to_top[::-1]
    return {
        "nuclear": lookup_hexagram(nuclear_bits),
        "opposite": lookup_hexagram(opposite_bits),
        "inverse": lookup_hexagram(inverse_bits),
    }


_HASH_DOMAIN = b"MUSE:YIJING:THREE_COIN:V1\x00"


def _trigram(bits: str) -> dict[str, str]:
    return {"bits_bottom_to_top": bits, "name": TRIGRAM_BY_BITS[bits]}


def _build_result(line_values: Sequence[int], *, input_origin: str) -> dict[str, Any]:
    primary_bits = "".join("1" if value in (7, 9) else "0" for value in line_values)
    relating_bits = "".join("1" if value in (7, 6) else "0" for value in line_values)
    lines: list[dict[str, Any]] = []
    moving_lines: list[int] = []
    for position, value in enumerate(line_values, start=1):
        yin_yang = "yin" if value in (6, 8) else "yang"
        changing = value in (6, 9)
        if changing:
            moving_lines.append(position)
        changed_yin_yang = (
            ("yang" if yin_yang == "yin" else "yin") if changing else yin_yang
        )
        lines.append({
            "position": position,
            "value": value,
            "label": LINE_LABELS[value],
            "yin_yang": yin_yang,
            "changing": changing,
            "relating_yin_yang": changed_yin_yang,
        })
    primary_hexagram = lookup_hexagram(primary_bits)
    relating_hexagram = lookup_hexagram(relating_bits)
    structural_relations = _structural_relations(primary_bits)
    return {
        "calculation_version": CALCULATION_VERSION,
        "calculation_method": (
            "three_coin_hash_seed_v1" if input_origin == "seeded_simulation"
            else "user_supplied_line_values"
        ),
        "input_origin": input_origin,
        "line_order": "bottom_to_top",
        "lines": lines,
        "line_values": list(line_values),
        "moving_lines": moving_lines,
        "primary": primary_hexagram,
        "relating": relating_hexagram,
        "structural_relations": structural_relations,
        "structural_relations_are_interpretations": False,
        "king_wen_number": primary_hexagram["king_wen_number"],
        "king_wen_name": primary_hexagram["king_wen_name"],
        "unicode_symbol": primary_hexagram["unicode_symbol"],
        "king_wen_catalog_status": "secondary_structural_reference_loaded",
        "king_wen_catalog_version": primary_hexagram["catalog_version"],
        "interpretation_status": "not_generated",
        "calculation_status": "calculated",
        "epistemic_status": "symbolic_structure_only",
        "is_physical_cast": False,
        "is_evidence": False,
        "is_prediction": False,
        "source_refs": [METHOD_SOURCE, *KING_WEN_CATALOG_SOURCES],
        "rule_source_status": "secondary_structural_reference_not_primary_textual_citation",
    }


def analyze_yijing_lines(line_values: Sequence[int]) -> dict[str, Any]:
    """Analyze six user-supplied traditional line values (6, 7, 8, 9)."""
    if isinstance(line_values, (str, bytes, bytearray)) or not isinstance(line_values, Sequence):
        raise ValueError("line_values must be a sequence of six integers")
    if len(line_values) != 6:
        raise ValueError("exactly six line values are required")
    if any(isinstance(value, bool) or not isinstance(value, int) or value not in LINE_LABELS
           for value in line_values):
        raise ValueError("each line value must be one of 6, 7, 8, or 9")
    return _build_result(tuple(line_values), input_origin="user_supplied_line_values")


def seeded_three_coin_cast(seed: str) -> dict[str, Any]:
    """Create a reproducible pseudo-random three-coin simulation from a visible seed.

    Coin value 2 represents yin and 3 represents yang. SHA-256 domain-separated
    counters make the result reproducible across Python runtimes without using
    Python's version-dependent random helper APIs.
    """
    if not isinstance(seed, str) or not seed.strip() or len(seed) > 256:
        raise ValueError("seed must contain 1-256 non-whitespace characters")
    seed_bytes = seed.encode("utf-8")
    coins_by_line: list[list[int]] = []
    line_values: list[int] = []
    for line_index in range(6):
        coins: list[int] = []
        for coin_index in range(3):
            counter = line_index * 3 + coin_index
            digest = sha256(
                _HASH_DOMAIN + seed_bytes + b"\x00" + counter.to_bytes(4, "big")
            ).digest()
            coins.append(3 if digest[0] & 1 else 2)
        coins_by_line.append(coins)
        line_values.append(sum(coins))
    result = _build_result(tuple(line_values), input_origin="seeded_simulation")
    result.update({
        "seed": seed,
        "seed_sha256": sha256(seed_bytes).hexdigest(),
        "coin_values_by_line": coins_by_line,
        "coin_value_mapping": {"2": "yin", "3": "yang"},
        "is_physical_cast": False,
    })
    return result
