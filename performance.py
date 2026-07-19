"""Performance model and LLM output parsing."""

import re

from pydantic import BaseModel

from astrbot.api import logger


class Performance(BaseModel):
    """A single dialogue segment with its expression and optional action."""

    dialogue: str
    expression: str
    # Optional VRMA action alias; ``None`` means keep the current (idle) state.
    action: str | None = None


# Matches either an ``[expression]`` tag or an ``<action>`` tag. Tags may appear
# anywhere in the free-form text and are treated as inline markers.
_TAG_PATTERN = re.compile(r"\[(?P<expr>[^\[\]]+)\]|<(?P<action>[^<>]+)>")


def parse_performances(text: str) -> list[Performance]:
    """Parse free-form text with inline ``[expression]`` and ``<action>`` tags
    into a list of :class:`Performance` segments.

    Tag semantics:

    - ``[expression]`` is **sticky**: it sets the current expression, which
      stays in effect for all subsequent segments until the next
      ``[expression]`` tag appears.
    - ``<action>`` is **momentary**: it applies only to the next dialogue
      segment that follows it. Text accumulated before the tag closes the
      current segment without an action.

    Plain text between/around tags is accumulated and split into a new segment
    whenever a tag is encountered or the input ends. A surrounding Markdown
    code fence (```` ``` ````) is tolerated and stripped before parsing.

    Args:
        text: The raw LLM output text.

    Returns:
        A list of parsed :class:`Performance` segments. Empty when nothing
        could be parsed.
    """
    # Tolerate a wrapped code fence (``` ... ``` or ```text ... ```).
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```\s*$", "", stripped)

    performances: list[Performance] = []
    current_expression = "neutral"
    pending_action: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        """Close the current text buffer as a segment, if non-empty."""
        nonlocal buffer, pending_action
        dialogue = "".join(buffer).strip()
        buffer = []
        if not dialogue:
            return
        performances.append(
            Performance(
                dialogue=dialogue,
                expression=current_expression,
                action=pending_action,
            )
        )
        pending_action = None

    pos = 0
    for match in _TAG_PATTERN.finditer(stripped):
        if match.start() > pos:
            buffer.append(stripped[pos : match.start()])
        if match.group("expr") is not None:
            # Expression change: close out pending text first, then switch
            # the sticky expression state.
            flush()
            current_expression = match.group("expr").strip()
        else:
            # Action cue: close out pending text (with no action), then arm
            # the action for the next dialogue segment.
            flush()
            pending_action = match.group("action").strip()
        pos = match.end()

    buffer.append(stripped[pos:])
    flush()

    # A trailing action with no following dialogue would otherwise be lost;
    # attach it to the last emitted segment so the cue still plays.
    if pending_action and performances:
        last = performances[-1]
        if last.action is None:
            performances[-1] = last.model_copy(update={"action": pending_action})

    if not performances:
        logger.warning("[Performance] 未能从输出中解析出任何对话")
    return performances
