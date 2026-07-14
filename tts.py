"""TTS audio generation helper.

Lip-sync (viseme / phoneme timing) is intentionally NOT handled here: accurate
mouth animation requires analysing the audio that is actually playing, which is
done client-side via the Web Audio API (see the VRM frontend). The server only
synthesises and returns the audio bytes.
"""

import base64
from pathlib import Path

from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.core.provider.provider import TTSProvider


async def get_tts_audio(
    context: Context, tts_provider_id: str, text: str
) -> tuple[str, str] | None:
    """Generate TTS audio for the given text.

    Args:
        context: AstrBot context used to locate the TTS provider.
        tts_provider_id: The configured TTS provider id.
        text: The text to synthesize.

    Returns:
        A tuple of ``(filename, audio_base64)`` on success, otherwise
        ``None``.
    """
    if not tts_provider_id:
        logger.warning("[TTS] TTS provider 未配置")
        return None

    tts_provider = context.get_provider_by_id(tts_provider_id)
    if not isinstance(tts_provider, TTSProvider):
        logger.warning(
            f"[TTS] TTS provider 无效: id={tts_provider_id}, type={type(tts_provider)}"
        )
        return None

    try:
        audio_path = await tts_provider.get_audio(text)
        with open(audio_path, "rb") as f:
            audio_base64 = base64.b64encode(f.read()).decode()
        filename = Path(audio_path).name

        logger.info(f"[TTS] 音频生成成功: {audio_path}")
        return filename, audio_base64
    except Exception as e:
        logger.warning(f"[TTS] 音频生成异常: {e}")
        return None
