"""Builders for messages sent to the VRM client."""

import base64
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .performance import parse_performances
from .tts import get_tts_audio

# --- Shared payload models -------------------------------------------------


class FilePayload(BaseModel):
    """A file embedded as a base64 data payload."""

    filename: str
    data: str


# --- Message models --------------------------------------------------------


class PerformanceSegment(BaseModel):
    """A single dialogue line with optional expression/action/audio."""

    dialogue: str
    expression: str
    action: str | None = None
    audio: FilePayload | None = None


class AnimationEntry(BaseModel):
    """A named VRMA animation file with its loop flag."""

    name: str
    file: FilePayload
    loop: bool = False


class PerformanceMessage(BaseModel):
    type: Literal["performance"] = "performance"
    segments: list[PerformanceSegment]


class UpdateCharacterMessage(BaseModel):
    type: Literal["update_character"] = "update_character"
    vrm: FilePayload


class UpdateBackgroundMessage(BaseModel):
    type: Literal["update_background"] = "update_background"
    background: FilePayload


class UpdateAnimationsMessage(BaseModel):
    type: Literal["update_animations"] = "update_animations"
    animations: list[AnimationEntry]


# --- Builders --------------------------------------------------------------


def _encode_file_base64(target: Path) -> str:
    """Read ``target`` and return its contents as a base64 string."""
    with open(target, "rb") as f:
        return base64.b64encode(f.read()).decode()


async def build_performance(
    context: Context,
    tts_provider_id: str,
    text: str,
) -> PerformanceMessage | None:
    """Parse the LLM output text and build a complete performance message.

    The performances are extracted from ``text`` via :func:`parse_performances`,
    enriched with TTS audio and wrapped into the message payload expected by the
    client. Returns ``None`` when no performances could be parsed.
    """
    performances = parse_performances(text)
    if not performances:
        return None

    segments: list[PerformanceSegment] = []
    for perf in performances:
        logger.info(
            f"[Performance] dialogue: {perf.dialogue}, expression: {perf.expression}, action: {perf.action}"
        )
        segment = PerformanceSegment(
            dialogue=perf.dialogue,
            expression=perf.expression,
            action=perf.action or None,
        )
        audio_result = await get_tts_audio(context, tts_provider_id, perf.dialogue)
        if audio_result:
            filename, audio_base64 = audio_result
            segment.audio = FilePayload(filename=filename, data=audio_base64)
        segments.append(segment)
    return PerformanceMessage(segments=segments)


async def build_update_character_message(
    vrm_paths: list[str], plugin_name: str
) -> UpdateCharacterMessage | None:
    """Read the configured VRM file and build an update_character message.

    The model is embedded as a base64 payload so the client receives it
    directly over the WebSocket without an extra download round-trip.
    """
    vrm_file = _resolve_plugin_file(vrm_paths, plugin_name, "VRM")
    if vrm_file is None:
        return None
    logger.info(f"[编码 VRM 文件]: {vrm_file.name}")
    return UpdateCharacterMessage(
        vrm=FilePayload(filename=vrm_file.name, data=_encode_file_base64(vrm_file))
    )


async def build_update_background_message(
    bg_paths: list[str], plugin_name: str
) -> UpdateBackgroundMessage | None:
    """Read the configured background image and build an update_background message."""
    bg_file = _resolve_plugin_file(bg_paths, plugin_name, "背景")
    if bg_file is None:
        return None
    logger.info(f"[编码背景文件]: {bg_file.name}")
    return UpdateBackgroundMessage(
        background=FilePayload(filename=bg_file.name, data=_encode_file_base64(bg_file))
    )


async def build_update_animations_message(
    animations: list[dict[str, object]], plugin_name: str
) -> UpdateAnimationsMessage | None:
    """Read configured VRMA animations and build an update_animations message.

    Each animation maps a name to a single VRMA file, embedded as a base64
    payload. The animation named ``idle`` is used by the client as the
    standby animation.
    """
    entries: list[AnimationEntry] = []
    for animation in animations or []:
        name = animation.get("name", "")
        if not isinstance(name, str) or not name.strip():
            logger.warning("[跳过未命名的动画]")
            continue
        name = name.strip()
        file_paths = animation.get("file", [])
        if not isinstance(file_paths, Sequence):
            continue
        vrma_file = _resolve_plugin_file(file_paths, plugin_name, "VRMA")
        if vrma_file is None:
            continue
        logger.info(f"[编码 VRMA 文件]: {vrma_file.name}")
        entries.append(
            AnimationEntry(
                name=name,
                file=FilePayload(
                    filename=vrma_file.name, data=_encode_file_base64(vrma_file)
                ),
                loop=bool(animation.get("loop", False)),
            )
        )
    if not entries:
        return None
    return UpdateAnimationsMessage(animations=entries)


def _resolve_plugin_file(
    rel_paths: Sequence[object], plugin_name: str, label: str
) -> Path | None:
    """Validate and resolve a configured plugin data file path.

    ``rel_paths`` follows the AstrBot config convention (a list whose first
    element is the relative path). Returns the absolute ``Path`` when valid, or
    ``None`` (with a warning logged) otherwise.
    """
    if not rel_paths:
        logger.warning(f"[未配置{label}文件，跳过更新]")
        return None
    rel_path = rel_paths[0]
    if not isinstance(rel_path, str) or not rel_path:
        logger.warning(f"[{label}文件配置无效]: {rel_path}")
        return None

    plugin_data_dir = (Path(get_astrbot_plugin_data_path()) / plugin_name).resolve(
        strict=False
    )
    target = (plugin_data_dir / rel_path).resolve(strict=False)
    try:
        _ = target.relative_to(plugin_data_dir)
    except ValueError:
        logger.warning(f"[{label}文件路径非法]: {rel_path}")
        return None
    if not target.is_file():
        logger.warning(f"[{label}文件不存在]: {target}")
        return None
    return target
