"""Strict tokenization and structural parsing for Cantonese Jyutping.

This module validates syllable spelling and tone shape; it does not claim that
every structurally valid syllable is a dictionary word or valid in every name.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


MAX_INPUT_CHARS = 2048
MAX_SYLLABLES = 512

# Standard Jyutping initials, longest first so gw/kw/ng are not split early.
_ONSETS = (
    "gw", "kw", "ng",
    "b", "p", "m", "f", "d", "t", "n", "l",
    "g", "k", "h", "w", "z", "c", "s", "j",
)

# Jyutping finals, longest first. The inventory includes syllabic m/ng.
_RIMES = tuple(sorted({
    "a", "aa", "aai", "aau", "aam", "aan", "aang", "aap", "aat", "aak",
    "ai", "au", "am", "an", "ang", "ap", "at", "ak",
    "e", "ei", "eu", "em", "en", "eng", "ep", "et", "ek",
    "i", "iu", "im", "in", "ing", "ip", "it", "ik",
    "o", "oi", "ou", "on", "ong", "ot", "ok",
    "u", "ui", "un", "ung", "ut", "uk",
    "oe", "oeng", "oek", "eoi", "eon", "eot",
    "yu", "yun", "yut", "m", "ng",
}, key=lambda value: (-len(value), value)))

_TOKEN_SPLIT = re.compile(r"[\s,!.?;:，。！？；：]+")

class JyutpingError(ValueError):
    """Raised when text is not a bounded sequence of Jyutping syllables."""


@dataclass(frozen=True)
class JyutpingSyllable:
    text: str
    onset: str
    rime: str
    tone: int
    coda: str
    checked: bool


def _parse_token(token: str) -> JyutpingSyllable:
    if not re.fullmatch(r"[A-Za-z]+[1-6]", token):
        raise JyutpingError(f"invalid Jyutping syllable: {token!r}")

    normalized = token.lower()
    body, tone_text = normalized[:-1], normalized[-1]
    tone = int(tone_text)

    # Syllabic nasals are finals with no onset, not the ng initial.
    if body in {"m", "ng"}:
        onset, rime = "", body
    else:
        onset = ""
        remainder = body
        for candidate in _ONSETS:
            if body.startswith(candidate) and len(body) > len(candidate):
                onset = candidate
                remainder = body[len(candidate):]
                break
        if remainder not in _RIMES:
            raise JyutpingError(f"unknown Jyutping rime in syllable: {token!r}")
        rime = remainder

    if rime not in _RIMES:
        raise JyutpingError(f"unknown Jyutping rime in syllable: {token!r}")

    coda = rime[-1] if rime.endswith(("p", "t", "k")) else ""
    return JyutpingSyllable(
        text=normalized,
        onset=onset,
        rime=rime,
        tone=tone,
        coda=coda,
        checked=bool(coda),
    )


def parse_jyutping(text: str) -> tuple[JyutpingSyllable, ...]:
    """Parse bounded Jyutping tokens separated by whitespace or common punctuation.

    Unsupported characters are retained in a token and therefore rejected;
    the parser never silently strips arbitrary input.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if len(text) > MAX_INPUT_CHARS:
        raise JyutpingError(f"input exceeds {MAX_INPUT_CHARS} characters")

    tokens = [part for part in _TOKEN_SPLIT.split(text.strip()) if part]
    if not tokens:
        raise JyutpingError("input must contain at least one Jyutping syllable")
    if len(tokens) > MAX_SYLLABLES:
        raise JyutpingError(f"input exceeds {MAX_SYLLABLES} syllables")

    return tuple(_parse_token(token) for token in tokens)
