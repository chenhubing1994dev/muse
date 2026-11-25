"""Fault-tolerant structured output handling for model responses (stdlib only).

Model output is untrusted text. This module turns it into validated Python data
through layered recovery, and never executes anything it parses:

1. ``extract_json``   strict parse -> fenced ``json`` block -> first balanced
                       object/array -> conservative syntax repair (smart quotes,
                       trailing commas, Python literals).
2. ``validate``       small JSON-schema subset (type, required, properties,
                       enum, min/max length, min/max items, minimum/maximum,
                       additionalProperties=false).
3. ``request_structured`` asks the model, parses, validates and, on failure,
                       sends a bounded repair prompt quoting the exact error.

It also provides ``CircuitBreaker`` and ``call_with_retry`` so every model or IO
call site can share one bounded timeout/backoff/fail-fast policy.
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping

MAX_TEXT_CHARS = 200_000


class StructuredOutputError(ValueError):
    """Model output could not be turned into data matching the schema."""

    def __init__(self, message: str, *, attempts: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts or []


class CircuitOpenError(RuntimeError):
    """The protected dependency failed repeatedly; calls are short-circuited."""


# --------------------------------------------------------------------------- parsing
_FENCE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.S)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_SMART_QUOTES = {"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'", "\uff02": '"'}


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _loads(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_reject_duplicate_keys)


def _balanced_candidates(text: str) -> Iterable[str]:
    """Yield balanced {...} / [...] substrings, string-aware, in order of appearance."""
    openers = {"{": "}", "[": "]"}
    for start, char in enumerate(text):
        if char not in openers:
            continue
        stack = [openers[char]]
        in_string = False
        escaped = False
        for index in range(start + 1, len(text)):
            current = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current in openers:
                stack.append(openers[current])
            elif stack and current == stack[-1]:
                stack.pop()
                if not stack:
                    yield text[start:index + 1]
                    break
            elif current in "}]":
                break  # mismatched closer: not a balanced candidate from this start


def _repair(text: str) -> str:
    repaired = text
    for bad, good in _SMART_QUOTES.items():
        repaired = repaired.replace(bad, good)
    repaired = _TRAILING_COMMA.sub(r"\1", repaired)
    repaired = re.sub(r"(?<![\w\"])True(?![\w\"])", "true", repaired)
    repaired = re.sub(r"(?<![\w\"])False(?![\w\"])", "false", repaired)
    repaired = re.sub(r"(?<![\w\"])None(?![\w\"])", "null", repaired)
    return repaired


def extract_json(text: Any, *, expect: type | tuple[type, ...] | None = None) -> Any:
    """Return the first JSON value recoverable from ``text``.

    ``expect`` (e.g. ``dict``) skips candidates of other types. Raises
    ``StructuredOutputError`` with the last parse error when nothing parses.
    """
    if not isinstance(text, str):
        raise StructuredOutputError("model output must be text")
    if len(text) > MAX_TEXT_CHARS:
        raise StructuredOutputError(f"model output exceeds {MAX_TEXT_CHARS} characters")
    stripped = text.strip().lstrip("\ufeff")
    if not stripped:
        raise StructuredOutputError("model output is empty")
    candidates: list[str] = [stripped]
    candidates.extend(match.group(1).strip() for match in _FENCE.finditer(stripped))
    candidates.extend(_balanced_candidates(stripped))
    last_error = "no JSON value found"
    seen: set[str] = set()
    for candidate in candidates:
        for variant in (candidate, _repair(candidate)):
            if not variant or variant in seen:
                continue
            seen.add(variant)
            try:
                value = _loads(variant)
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = str(exc)
                continue
            if expect is not None and not isinstance(value, expect):
                last_error = f"expected {expect}, found {type(value).__name__}"
                continue
            return value
    raise StructuredOutputError(f"no valid JSON in model output: {last_error}")


# --------------------------------------------------------------------------- schema
_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,), "array": (list,), "string": (str,), "boolean": (bool,),
    "integer": (int,), "number": (int, float), "null": (type(None),),
}


def validate(value: Any, schema: Mapping[str, Any], path: str = "$") -> list[str]:
    """Return a list of human-readable violations (empty when valid)."""
    errors: list[str] = []
    expected = schema.get("type")
    if expected is not None:
        names = expected if isinstance(expected, (list, tuple)) else [expected]
        ok = False
        for name in names:
            types = _TYPES.get(name)
            if types is None:
                continue
            if name in {"integer", "number"} and isinstance(value, bool):
                continue
            if isinstance(value, types):
                ok = True
                break
        if not ok:
            return [f"{path}: expected {expected}, found {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {list(schema['enum'])}")
    if isinstance(value, str):
        if "minLength" in schema and len(value.strip()) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above {schema['maximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for index, item in enumerate(value):
                errors.extend(validate(item, schema["items"], f"{path}[{index}]"))
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", ()):
            if key not in value:
                errors.append(f"{path}: missing required key {key!r}")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}: unexpected key {key!r}")
        for key, sub in properties.items():
            if key in value:
                errors.extend(validate(value[key], sub, f"{path}.{key}"))
    return errors


def parse_structured(text: Any, schema: Mapping[str, Any]) -> Any:
    expect = dict if schema.get("type") == "object" else (list if schema.get("type") == "array" else None)
    value = extract_json(text, expect=expect)
    errors = validate(value, schema)
    if errors:
        raise StructuredOutputError("schema violation: " + "; ".join(errors[:8]))
    return value


def request_structured(
    chat: Callable[[list[dict[str, str]]], str],
    messages: list[dict[str, str]],
    schema: Mapping[str, Any],
    *,
    max_repairs: int = 2,
) -> Any:
    """Call ``chat`` and return schema-valid data, repairing at most ``max_repairs`` times."""
    if isinstance(max_repairs, bool) or not isinstance(max_repairs, int) or not 0 <= max_repairs <= 5:
        raise ValueError("max_repairs must be between 0 and 5")
    conversation = list(messages)
    attempts: list[dict[str, Any]] = []
    for attempt in range(max_repairs + 1):
        raw = chat(conversation)
        try:
            value = parse_structured(raw, schema)
            attempts.append({"attempt": attempt, "status": "ok"})
            return value
        except StructuredOutputError as exc:
            attempts.append({"attempt": attempt, "status": "invalid", "error": str(exc)[:300]})
            if attempt == max_repairs:
                break
            conversation = conversation + [
                {"role": "assistant", "content": str(raw)[:4000]},
                {"role": "user", "content": (
                    "FORMAT_REPAIR_REQUIRED. Your previous reply was rejected: "
                    f"{str(exc)[:300]}. Reply with ONE JSON value only, no prose, no code fence, "
                    "matching this JSON schema exactly: " + json.dumps(schema, ensure_ascii=False)[:3000]
                )},
            ]
    raise StructuredOutputError("model output stayed invalid after repair attempts", attempts=attempts)


# --------------------------------------------------------------------------- resilience
class CircuitBreaker:
    """Thread-safe closed -> open -> half-open breaker for a flaky dependency."""

    def __init__(self, *, failure_threshold: int = 3, reset_seconds: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if isinstance(failure_threshold, bool) or not isinstance(failure_threshold, int) or failure_threshold < 1:
            raise ValueError("failure_threshold must be a positive integer")
        if not isinstance(reset_seconds, (int, float)) or reset_seconds <= 0:
            raise ValueError("reset_seconds must be positive")
        self.failure_threshold = failure_threshold
        self.reset_seconds = float(reset_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._last_error: str | None = None

    @property
    def state(self) -> str:
        with self._lock:
            return self._state_locked()

    def _state_locked(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.reset_seconds:
            return "half_open"
        return "open"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"state": self._state_locked(), "failures": self._failures, "last_error": self._last_error}

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            if self._state_locked() == "open":
                raise CircuitOpenError(f"circuit open after {self._failures} failure(s): {self._last_error}")
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            with self._lock:
                self._failures += 1
                self._last_error = type(exc).__name__
                if self._failures >= self.failure_threshold or self._opened_at is not None:
                    self._opened_at = self._clock()
            raise
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._last_error = None
        return result


def call_with_retry(
    func: Callable[[], Any], *, retries: int = 2, base_delay: float = 0.5, max_delay: float = 8.0,
    transient: Callable[[BaseException], bool] = lambda exc: isinstance(exc, (TimeoutError, ConnectionError, OSError)),
    sleep: Callable[[float], None] = time.sleep, jitter: bool = True,
) -> Any:
    """Run ``func`` with bounded exponential backoff for transient failures only."""
    if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 8:
        raise ValueError("retries must be between 0 and 8")
    attempt = 0
    while True:
        try:
            return func()
        except Exception as exc:
            if attempt >= retries or not transient(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** attempt))
            if jitter:
                delay *= 0.5 + random.random() / 2
            sleep(delay)
            attempt += 1
