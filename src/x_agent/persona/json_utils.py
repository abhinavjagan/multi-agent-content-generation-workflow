"""Defensive JSON extraction from LLM output.

LLMs frequently wrap JSON in markdown fences or add a sentence of preamble.
We accept any of: a clean JSON object, a fenced ```json``` block, or a
"first balanced { ... } substring".
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL | re.IGNORECASE)


class JsonParseError(ValueError):
    pass


def _find_balanced(text: str, opener: str, closer: str) -> str | None:
    start = text.find(opener)
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json(text: str) -> Any:
    """Best-effort JSON extraction. Raises ``JsonParseError`` on total failure."""
    if not text or not text.strip():
        raise JsonParseError("empty text")

    candidates: list[str] = []

    # 1. Fenced block.
    m = _FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1))

    # 2. Balanced object / array.
    for opener, closer in (("{", "}"), ("[", "]")):
        balanced = _find_balanced(text, opener, closer)
        if balanced:
            candidates.append(balanced)

    # 3. The whole thing, in case it's already pure JSON.
    candidates.append(text.strip())

    last_err: Exception | None = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_err = exc
            continue
    raise JsonParseError(f"no parseable JSON in text: {last_err}")
