"""Performance model and LLM output parsing.

The LLM emits its reply as a JSON **array of performance objects**. Each
object is one beat of the performance and carries three fields:

- ``text``: the dialogue spoken by the avatar for this beat.
- ``emotion``: one of the fixed 24-word emotion vocabulary shared with the
  frontend engine; unknown values degrade to ``neutral``.
- ``intensity``: the emotion strength in ``[0, 1]``; missing or out-of-range
  values are clamped, missing defaults to ``0.85``.

Idle standby variants are **not** part of this array: they are persistent
state rather than timed performance, so they are switched out-of-band via
the ``set_idle`` LLM tool, which sends a dedicated wire message.
"""

import json
import re
from collections.abc import Sequence

from pydantic import BaseModel

from astrbot.api import logger

from .wire_types import FilePayload

# Fixed 24-word emotion vocabulary, kept in sync with the frontend engine.
EMOTION_WORDS: tuple[str, ...] = (
    "neutral",
    "happy",
    "excited",
    "amused",
    "proud",
    "confident",
    "calm",
    "relieved",
    "affectionate",
    "shy",
    "embarrassed",
    "curious",
    "concerned",
    "confused",
    "tired",
    "sad",
    "lonely",
    "disappointed",
    "anxiety",
    "fear",
    "anger",
    "frustrated",
    "disgust",
    "surprised",
)
_EMOTIONS_CASEFOLDED = {word.casefold(): word for word in EMOTION_WORDS}

# Default intensity applied when the LLM omits the field or emits a
# non-numeric value.
DEFAULT_INTENSITY = 0.85

# --- Performance model -----------------------------------------------------


class Performance(BaseModel):
    """One object of the LLM's performance array: a dialogue line with the
    emotion (and its intensity) to express while speaking it.

    ``audio`` is filled by the TTS step after parsing; it is ``None`` until
    then. Each performance maps to at most one TTS clip.
    """

    text: str
    emotion: str = "neutral"
    intensity: float = DEFAULT_INTENSITY
    audio: FilePayload | None = None


# --- Parser ----------------------------------------------------------------


def match_allowed_name(raw: str, allowed: Sequence[str] | None) -> str | None:
    """Resolve a field value against the allowed-name whitelist.

    Prompt-side constraints alone cannot stop the LLM from inventing names,
    so validity is enforced here at parse time. Matching is case-insensitive;
    anything that does not exactly match an allowed name is rejected so a
    fabricated value is dropped rather than silently remapped.

    Args:
        raw: The field value as emitted by the LLM.
        allowed: The allowed names, or ``None`` to accept anything.

    Returns:
        The canonical allowed name, or ``None`` when ``raw`` matches nothing.
    """
    if allowed is None:
        return raw
    lowered = {name.casefold(): name for name in allowed}
    return lowered.get(raw.casefold())


def _extract_json_array(stripped: str) -> list[object] | None:
    """Extract the performance array from the (fence-stripped) LLM output.

    Tries the whole text first, then the substring from the first ``[`` to
    the last ``]`` to tolerate stray prose around the array. A single
    top-level object whose first list-typed value holds the array (e.g.
    ``{"performances": [...]}``) is also accepted.

    Args:
        stripped: The LLM output with surrounding whitespace and an optional
            Markdown code fence already removed.

    Returns:
        The raw parsed list, or ``None`` when no parsable array is found.
    """
    candidates = [stripped]
    start, end = stripped.find("["), stripped.rfind("]")
    if start != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), None)
        if isinstance(data, list):
            return data
    return None


def parse_performance(text: str) -> list[Performance] | None:
    """Parse the LLM's JSON array output into a list of performances.

    Each array element must be an object with a string ``text`` field and
    optional ``emotion`` / ``intensity`` fields. ``emotion`` values outside
    the fixed 24-word vocabulary degrade to ``neutral``; ``intensity``
    values are clamped to ``[0, 1]`` and default to ``0.85`` when missing
    or non-numeric, so the dialogue text always survives with a usable
    emotion beat. When the output is not a parsable JSON array at all, the
    whole text is treated as one plain dialogue line so the avatar still
    speaks.

    Args:
        text: The raw LLM output text.

    Returns:
        The ordered list of performances (one per array element), or
        ``None`` when nothing could be parsed.
    """
    # Tolerate a wrapped code fence (``` ... ``` or ```json ... ```).
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```\s*$", "", stripped).strip()

    raw_items = _extract_json_array(stripped)
    if raw_items is None:
        if not stripped:
            logger.warning("[Performance] output is empty")
        else:
            logger.warning("[Performance] output is not a JSON array")
        return None

    performances: list[Performance] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            logger.warning("[Performance] item %d is not an object, skipped", index + 1)
            continue
        raw_text = raw.get("text")
        if not isinstance(raw_text, str):
            logger.warning(
                "[Performance] item %d lacks a valid text field, skipped", index + 1
            )
            continue
        dialogue = raw_text.strip()

        emotion = "neutral"
        raw_emotion = raw.get("emotion")
        if isinstance(raw_emotion, str) and raw_emotion.strip():
            emotion = _EMOTIONS_CASEFOLDED.get(
                raw_emotion.strip().casefold(), "neutral"
            )
            if emotion == "neutral" and raw_emotion.strip().casefold() != "neutral":
                logger.warning(
                    "[Performance] item %d emotion not in vocabulary, "
                    "degraded to neutral: %s",
                    index + 1,
                    raw_emotion,
                )

        intensity = DEFAULT_INTENSITY
        raw_intensity = raw.get("intensity")
        if isinstance(raw_intensity, (int, float)) and not isinstance(
            raw_intensity, bool
        ):
            intensity = min(max(float(raw_intensity), 0.0), 1.0)
        elif raw_intensity is not None:
            logger.warning(
                "[Performance] item %d intensity not numeric, using default: %s",
                index + 1,
                raw_intensity,
            )

        # Skip beats with no dialogue left to perform.
        if not dialogue:
            continue
        performances.append(
            Performance(text=dialogue, emotion=emotion, intensity=intensity)
        )

    if not performances:
        logger.warning("[Performance] no performable items parsed from output")
        return None
    return performances
