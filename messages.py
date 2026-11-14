"""Builders for messages sent to the VRM client."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel

from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .alignment import align_performances
from .performance import Performance
from .tts import get_tts_audio
from .wire_types import FilePayload

# --- Message models --------------------------------------------------------


class AnimationEntry(BaseModel):
    """A named VRMA animation file, tagged with the client layer it feeds.

    ``layer`` routes the animation on the client's layered animation system:

    - ``"idle"``: Base-layer standby variants, switchable via the
      ``set_idle`` tool's out-of-band message
    - ``"gesture"`` / ``"action"`` (default): one-shot animations; no
      longer triggered during performances (the ``action`` field was
      removed), kept for future use
    """

    name: str
    layer: Literal["idle", "gesture", "action"] = "action"
    file: FilePayload


class PerformanceMessage(BaseModel):
    """Wire message wrapping parsed performances for the VRM client.

    The payload is a list of :class:`Performance` segments plus ONE shared
    TTS audio clip for the whole reply (``audio``, a sibling of the list,
    not per segment). Each performance carries its ``start``/``end`` time
    window inside that clip, filled by the word-alignment step; the client
    plays the audio once and switches emotions at the segment boundaries.
    ``audio`` is ``None`` when no TTS provider produced audio.
    """

    type: Literal["performance"] = "performance"
    performances: list[Performance]
    audio: FilePayload | None = None


class UpdateCharacterMessage(BaseModel):
    type: Literal["update_character"] = "update_character"
    vrm: FilePayload


class UpdateBackgroundMessage(BaseModel):
    type: Literal["update_background"] = "update_background"
    background: FilePayload


class UpdateAnimationsMessage(BaseModel):
    type: Literal["update_animations"] = "update_animations"
    animations: list[AnimationEntry]


class SetIdleMessage(BaseModel):
    """Switch the client's Base-layer standby animation variant.

    Requested out-of-band by the ``set_idle`` LLM tool rather than being
    part of a performance array, since idle variants are persistent
    state. The tool call is buffered per event and this message is sent
    alongside the reply's performance, so the pose change lands together
    with the speech instead of while the reply is still generating.
    """

    type: Literal["set_idle"] = "set_idle"
    name: str


# --- Builders --------------------------------------------------------------


def _file_url(rel_path: str) -> str:
    """Build the HTTP URL path served by the plugin's WebSocket server."""
    return "/files/" + quote(rel_path.replace("\\", "/"))


async def build_performance_message(
    context: Context,
    tts_provider_id: str,
    performances: list[Performance],
    alignment_config: dict[str, Any],
) -> PerformanceMessage:
    """Synthesize ONE shared TTS clip, align it, and wrap the performances
    for the wire.

    The full reply is synthesized as a single clip: the segment texts are
    joined with their emotion tags inline (``[emotion]``), which Fish Audio
    TTS interprets as prosody markers. The clip is then handed to
    :func:`alignment.align_performances`, which fills each performance's
    ``start``/``end`` window inside the clip (with a proportional-estimate
    fallback when the forced-alignment service is down or incomplete).
    The audio is attached to the message as a sibling of the performance
    list, never split per segment.

    Args:
        context: The AstrBot context, used for TTS resolution.
        tts_provider_id: The configured TTS provider id.
        performances: The parsed performances, in playback order.
        alignment_config: The plugin's ``forced_alignment`` config section.

    Returns:
        The wire-ready performance message.
    """
    dialogue = "\n".join(f"[{item.emotion}]{item.text}" for item in performances)
    audio_result = await get_tts_audio(context, tts_provider_id, dialogue)
    if audio_result:
        filename, audio_base64, audio_path = audio_result
        await align_performances(performances, audio_path, alignment_config)
        message = PerformanceMessage(
            performances=performances,
            audio=FilePayload(filename=filename, data=audio_base64),
        )
    else:
        message = PerformanceMessage(performances=performances)
    logger.info(f"[Performance] {performances}")
    return message


async def build_update_character_message(
    vrm_paths: list[str], plugin_name: str
) -> UpdateCharacterMessage | None:
    """Read the configured VRM file and build an update_character message.

    The model is referenced by ``url`` (served by the plugin's own HTTP
    endpoint) instead of a base64 payload, so a large model streams through
    the browser's fetch/cache pipeline rather than a giant JSON WebSocket
    message.
    """
    resolved = _resolve_plugin_file(vrm_paths, plugin_name, "VRM")
    if resolved is None:
        return None
    vrm_file, rel_path = resolved
    logger.info(f"[Referencing VRM file]: {vrm_file.name}")
    return UpdateCharacterMessage(
        vrm=FilePayload(filename=vrm_file.name, url=_file_url(rel_path))
    )


async def build_update_background_message(
    bg_paths: list[str], plugin_name: str
) -> UpdateBackgroundMessage | None:
    """Read the configured background image and build an update_background message."""
    resolved = _resolve_plugin_file(bg_paths, plugin_name, "background")
    if resolved is None:
        return None
    bg_file, rel_path = resolved
    logger.info(f"[Referencing background file]: {bg_file.name}")
    return UpdateBackgroundMessage(
        background=FilePayload(filename=bg_file.name, url=_file_url(rel_path))
    )


async def build_update_animations_message(
    animations: list[dict[str, object]], plugin_name: str
) -> UpdateAnimationsMessage | None:
    """Read configured VRMA animations and build an update_animations message.

    Each animation maps a name to a single VRMA file, referenced by
    ``url``. ``layer`` routes the animation on the client's layered
    animation system (idle / gesture / action, default action); only idle
    entries are actively used (the ``set_idle`` tool whitelist). The
    animation named ``idle`` (layer=idle) is used by the client as the
    default standby.
    """
    entries: list[AnimationEntry] = []
    for animation in animations or []:
        name = animation.get("name")
        if not isinstance(name, str) or not name.strip():
            logger.warning("[Skipped unnamed animation]")
            continue
        name = name.strip()
        layer_raw = animation.get("layer", "action")
        layer = layer_raw if layer_raw in ("idle", "gesture", "action") else "action"
        if layer_raw not in ("idle", "gesture", "action"):
            logger.warning(
                f"[Invalid animation layer, treating as action]: {layer_raw}"
            )
        file_paths = animation.get("file", [])
        if not isinstance(file_paths, Sequence):
            continue
        resolved = _resolve_plugin_file(file_paths, plugin_name, "VRMA")
        if resolved is None:
            continue
        vrma_file, rel_path = resolved
        logger.info(f"[Referencing VRMA file]: {vrma_file.name} (layer={layer})")
        entries.append(
            AnimationEntry(
                name=name,
                layer=layer,
                file=FilePayload(filename=vrma_file.name, url=_file_url(rel_path)),
            )
        )
    if not entries:
        return None
    return UpdateAnimationsMessage(animations=entries)


def _resolve_plugin_file(
    rel_paths: Sequence[object], plugin_name: str, label: str
) -> tuple[Path, str] | None:
    """Validate and resolve a configured plugin data file path.

    ``rel_paths`` follows the AstrBot config convention (a list whose first
    element is the relative path). Returns the absolute ``Path`` and the
    relative path (used to build the served URL) when valid, or ``None``
    (with a warning logged) otherwise.
    """
    if not rel_paths:
        logger.warning(f"[{label} file not configured, skipping update]")
        return None
    rel_path = rel_paths[0]
    if not isinstance(rel_path, str) or not rel_path:
        logger.warning(f"[Invalid {label} file config]: {rel_path}")
        return None

    plugin_data_dir = (Path(get_astrbot_plugin_data_path()) / plugin_name).resolve(
        strict=False
    )
    target = (plugin_data_dir / rel_path).resolve(strict=False)
    try:
        _ = target.relative_to(plugin_data_dir)
    except ValueError:
        logger.warning(f"[Illegal {label} file path]: {rel_path}")
        return None
    if not target.is_file():
        logger.warning(f"[{label} file not found]: {target}")
        return None
    return target, str(rel_path)
