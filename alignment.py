"""Word-level audio/text alignment and segment time matching.

The whole reply is synthesized as ONE TTS clip (no per-segment audio).
Word timings come from an external forced-alignment (FA) HTTP service and
are matched back onto the parsed performance segments, so the frontend
knows the start/end time of each segment inside the shared audio. When
alignment is unavailable or incomplete, the timings are instead estimated
proportionally from the audio duration (backend-side fallback).
"""

import math
import re
from pathlib import Path
from typing import Any

import aiohttp
from pydantic import BaseModel

from astrbot.api import logger
from astrbot.core.utils.media_utils import ensure_wav, get_media_duration

from .performance import Performance


class WordAlignment(BaseModel):
    """One aligned unit (a Chinese character or an English word) with its
    time window in seconds inside the audio.
    """

    word: str
    start: float
    end: float


def _extract_word_alignments(data: dict[str, Any]) -> list[WordAlignment]:
    """Pull the ordered word timings out of an FA service response.

    Tolerates the field-name variations seen across FA server
    implementations (``timestamps``/``alignment``/``words`` lists whose
    items use ``text``/``word`` and ``start_time``/``start``).

    Args:
        data: The decoded JSON response body.

    Returns:
        The parsed word alignments; empty when the payload has no usable
        timing list.
    """
    items = data.get("timestamps") or data.get("alignment") or data.get("words") or []
    if not isinstance(items, list):
        return []
    words: list[WordAlignment] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        word = item.get("text") or item.get("word")
        start = item.get("start_time", item.get("start"))
        end = item.get("end_time", item.get("end"))
        if not isinstance(word, str) or not word.strip():
            continue
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        words.append(WordAlignment(word=word, start=float(start), end=float(end)))
    return words


async def align_audio_text(
    audio_path: str, text: str, alignment_config: dict[str, Any]
) -> list[WordAlignment] | None:
    """Align the synthesized audio with its text via the FA service.

    The audio is first converted to WAV (via ffmpeg) as the service
    requires, then POSTed as multipart form data together with the model
    name, text, and language from ``alignment_config``.

    Args:
        audio_path: Path to the synthesized TTS audio file (any format
            ffmpeg can decode).
        text: The full dialogue text (tags stripped) the audio speaks.
        alignment_config: The plugin's ``forced_alignment`` config section
            (``enabled``, ``api_url``, ``model``, ``language``).

    Returns:
        The ordered list of word alignments, or ``None`` when the service
        is disabled, unavailable, or fails.
    """
    if not alignment_config.get("enabled", False):
        logger.info("[Alignment] FA disabled, skipping")
        return None
    api_url = str(alignment_config.get("api_url", "")).rstrip("/")
    if not api_url:
        logger.info("[Alignment] no FA api_url configured, skipping")
        return None
    model = str(alignment_config.get("model", ""))
    language = str(alignment_config.get("language", "zh"))

    try:
        wav_path = await ensure_wav(audio_path)
    except Exception as e:
        logger.warning(f"[Alignment] WAV conversion failed: {e}")
        return None

    form = aiohttp.FormData()
    form.add_field("model", model)
    form.add_field("text", text)
    form.add_field("language", language)
    try:
        with open(wav_path, "rb") as f:
            form.add_field(
                "file",
                f.read(),
                filename=Path(wav_path).name,
                content_type="audio/wav",
            )
    except Exception as e:
        logger.warning(f"[Alignment] failed to read audio file: {e}")
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                api_url,
                data=form,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(
                        f"[Alignment] FA service returned {resp.status}: {body[:200]}"
                    )
                    return None
                words = _extract_word_alignments(await resp.json())
    except Exception as e:
        logger.warning(f"[Alignment] FA request failed: {e}")
        return None
    finally:
        # Remove the temporary WAV only when ensure_wav actually converted.
        if wav_path != audio_path:
            try:
                Path(wav_path).unlink(missing_ok=True)
            except OSError:
                pass

    if not words:
        logger.warning("[Alignment] FA service returned no word timings")
        return None
    return words


# Everything that is not a letter, digit, or CJK character is dropped
# before matching, so punctuation and whitespace never block alignment.
_STRIP_RE = re.compile(r"[\W_]+", re.UNICODE)


def _normalize(s: str) -> str:
    return _STRIP_RE.sub("", s).casefold()


def apply_word_alignments(
    performances: list[Performance], words: list[WordAlignment]
) -> None:
    """Match aligned words onto segments and fill in each segment's timing.

    Both the segment texts and the word list are normalized (punctuation
    and whitespace removed, case-folded) into one character stream that
    carries each character's segment index. Words are then matched against
    that stream strictly in order; a word that cannot be found ahead in the
    stream (ASR noise, TTS-hallucinated text) is skipped. A segment's
    ``start`` is the earliest matched word start covering its characters,
    and its ``end`` the latest matched word end.

    Args:
        performances: The parsed segments; ``start``/``end`` are filled in
            place.
        words: The word alignments from :func:`align_audio_text`.
    """
    chars: list[tuple[str, int]] = []  # (normalized char, segment index)
    for index, item in enumerate(performances):
        for ch in _normalize(item.text):
            chars.append((ch, index))

    starts: dict[int, float] = {}
    ends: dict[int, float] = {}
    pos = 0
    for wa in words:
        word = _normalize(wa.word)
        if not word:
            continue
        # Advance until the stream matches this word, tolerating extra
        # characters the words list does not account for.
        while pos + len(word) <= len(chars) and (
            "".join(c for c, _ in chars[pos : pos + len(word)]) != word
        ):
            pos += 1
        if pos + len(word) > len(chars):
            # Word not found in the remaining stream; drop it.
            continue
        for j in range(pos, pos + len(word)):
            seg = chars[j][1]
            if seg not in starts or wa.start < starts[seg]:
                starts[seg] = wa.start
            if seg not in ends or wa.end > ends[seg]:
                ends[seg] = wa.end
        pos += len(word)

    for index, item in enumerate(performances):
        item.start = starts.get(index)
        item.end = ends.get(index)
        if item.start is None or item.end is None:
            logger.warning(
                "[Alignment] segment %d matched no words, sent without timing",
                index + 1,
            )


def estimate_segment_timings(
    performances: list[Performance], duration_s: float | None
) -> None:
    """Fill in segment timings proportionally when alignment is incomplete.

    All-or-nothing: when every segment already carries a valid time window
    (finite numbers with ``end > start``), nothing is done. Otherwise the
    whole timeline is re-estimated — each segment gets a share of the audio
    duration (or of a 180ms-per-character estimate when the duration is
    unknown) proportional to its text length, overwriting whatever partial
    alignment data existed. This keeps the timing contract "when there is
    audio, every segment has a window" owned by the backend; the client's
    own estimation stays only as a last-resort defense.

    Args:
        performances: The parsed segments; ``start``/``end`` are filled in
            place.
        duration_s: The shared audio duration in seconds, or ``None`` when
            probing failed.
    """
    if all(
        item.start is not None
        and item.end is not None
        and math.isfinite(item.start)
        and math.isfinite(item.end)
        and item.end > item.start
        for item in performances
    ):
        return
    missing = sum(1 for item in performances if item.start is None or item.end is None)
    weights = [max(len(item.text), 1) for item in performances]
    total = sum(weights)
    span = (
        duration_s
        if duration_s is not None and math.isfinite(duration_s) and duration_s > 0
        else total * 0.18
    )
    cursor = 0.0
    for item, weight in zip(performances, weights):
        item.start = cursor
        cursor += weight / total * span
        item.end = cursor
    logger.info(
        "[Alignment] %d/%d segments missing timing, "
        "estimated proportional timeline (span=%.2fs)",
        missing,
        len(performances),
        span,
    )


async def align_performances(
    performances: list[Performance],
    audio_path: str,
    alignment_config: dict[str, Any],
) -> None:
    """Fill each performance's timing window inside the shared TTS clip.

    The clip is aligned against the concatenated segment text via the FA
    service and the word timings are matched onto the segments. Segments
    left without a valid window (service down or unmatched words) get the
    whole timeline re-estimated proportionally from the audio duration,
    so the degradation is an internal concern of this module and callers
    get an always-filled timeline whenever audio exists.

    Args:
        performances: The parsed segments, in playback order; ``start``/
            ``end`` are filled in place.
        audio_path: Path to the synthesized TTS audio file (any format
            ffmpeg can decode).
        alignment_config: The plugin's ``forced_alignment`` config section
            (``enabled``, ``api_url``, ``model``, ``language``).
    """
    text = "\n".join(item.text for item in performances)
    words = await align_audio_text(audio_path, text, alignment_config)
    if words:
        apply_word_alignments(performances, words)
    # Backend-side fallback: when alignment is unavailable or incomplete,
    # estimate every segment's window proportionally from the audio duration.
    duration_ms = await get_media_duration(audio_path)
    estimate_segment_timings(performances, duration_ms / 1000 if duration_ms else None)
