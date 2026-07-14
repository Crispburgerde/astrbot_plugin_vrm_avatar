"""Performance model and LLM output parsing."""

import json
import re

import json_repair
from pydantic import BaseModel

from astrbot.api import logger


class Performance(BaseModel):
    """A single dialogue segment with its expression and optional action."""

    dialogue: str
    expression: str
    # Optional VRMA action alias; ``None`` means keep the current (idle) state.
    action: str | None = None


def _safe_json_loads(text: str):
    """Try standard json.loads first, fall back to json_repair for malformed
    LLM output (e.g. unescaped double quotes inside string values).
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.debug("[Performance] json.loads 失败，尝试 json_repair 修复")
        return json_repair.loads(text)


def parse_performances(text: str) -> list[Performance]:
    """Parse the LLM output into a list of Performance segments.

    Supports both array format (preferred) and single-object format
    (backward compatible).
    """
    try:
        array_match = re.search(r"\[[\s\S]*\]", text)
        if array_match:
            data = _safe_json_loads(array_match.group())
            if isinstance(data, list):
                return [Performance(**item) for item in data]
        obj_match = re.search(r"\{[\s\S]*\}", text)
        if obj_match:
            data = _safe_json_loads(obj_match.group())
            if isinstance(data, dict):
                return [Performance(**data)]
    except Exception as e:
        logger.warning(f"[Performance] 解析失败: {e}")
    return []
