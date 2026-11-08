import re
from pathlib import Path
from typing import override

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import Plain
from astrbot.core.provider.provider import Provider
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .messages import (
    SetIdleMessage,
    build_performance_message,
    build_update_animations_message,
    build_update_background_message,
    build_update_character_message,
)
from .performance import Performance, match_allowed_name, parse_performance
from .prompts import (
    build_extractor_prompt,
    build_extractor_user_prompt,
    build_idle_prompt,
)
from .websocket_server import WebSocketServer

PLUGIN_NAME = "astrbot_plugin_vrm_avatar"

# A dialogue fragment not ending in terminal punctuation may have been split
# mid-sentence by the extractor model; such fragments are merge candidates.
# Ellipses are excluded: they are treated as caesuras, not sentence ends.
_FRAGMENT_TAIL_RE = re.compile(r"[。！？!?\.\"”』」)]\s*$")
# Ellipses/dashes are often mere caesuras, so even a punctuated beat this
# short counts as a fragment and is merged into the next line.
_CAESURA_MAX_CHARS = 8


def _merge_fragmented(performances: list[Performance]) -> list[Performance]:
    """Merge only accidental over-splits, keeping deliberate emotion beats.

    Small extractor models sometimes split at commas, ellipsis caesuras, or
    mid-clause. A beat is merged into the following line only when ALL of
    these hold: its text lacks terminal punctuation OR is at most
    ``_CAESURA_MAX_CHARS`` long (an accidental split), and the next line
    carries the same emotion. A changed emotion marks a deliberate
    performance beat, so the split is preserved even mid-sentence.

    Args:
        performances: The parsed performances, in playback order.

    Returns:
        The merged performance list (never empty for non-empty input).
    """
    merged: list[Performance] = []
    for item in performances:
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and (
                not _FRAGMENT_TAIL_RE.search(prev.text)
                or len(prev.text) <= _CAESURA_MAX_CHARS
            )
            and item.emotion == prev.emotion
        ):
            merged[-1] = Performance(
                text=prev.text + item.text,
                emotion=prev.emotion,
                intensity=max(prev.intensity, item.intensity),
            )
        else:
            merged.append(item)
    return merged


def _derive_idle_names(animations: list[object]) -> list[str]:
    """Collect the configured idle-layer animation names.

    Idle names feed the ``set_idle`` tool whitelist. Non-idle entries are
    no longer triggered during performances (the ``action`` field was
    removed) but their config is kept for future use. Names are collected
    in config order and de-duplicated, since a name is the animation's
    unique identifier on the client.
    """
    idle_names: list[str] = []
    seen: set[str] = set()
    for raw in animations or []:
        if not isinstance(raw, dict) or raw.get("layer") != "idle":
            continue
        name = raw.get("name")
        if not isinstance(name, str):
            continue
        name = name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        idle_names.append(name)
    return idle_names


@register(
    "astrbot_plugin_vrm_avatar",
    "Dadaburger",
    "为AstrBot提供一个VRM虚拟形象的插件",
    "v1.0.0",
)
class VRMAvatarPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.ws_server = WebSocketServer(
            host=config.get("ws_host", "0.0.0.0"),
            port=config.get("ws_port", 8765),
            on_connect=self._on_client_connected,
            # Serve plugin data files (VRM / background / VRMA) over plain
            # HTTP on a configurable port so the client fetches them by URL
            # with browser caching, instead of pushing base64 over the socket.
            file_root=Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME,
            http_port=config.get("http_port", 8766),
        )

    @override
    async def initialize(self):
        """Start the WebSocket server on plugin initialization."""
        await self.ws_server.start()

    @override
    async def terminate(self):
        """Shut down the WebSocket server on plugin destruction."""
        await self.ws_server.stop()

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        # The main LLM now generates natural replies; only the standby-pose
        # tool usage is still taught via the system prompt. The system
        # prompt is rebuilt from the persona on every request, so appending
        # here will not duplicate across turns.
        idle_names = _derive_idle_names(self.config.get("vrma_animations", []))
        if idle_names:
            req.system_prompt += "\n\n" + build_idle_prompt(idle_names)

    @filter.llm_tool(name="set_idle")
    async def set_idle(self, event: AstrMessageEvent, idle_name: str):
        """切换虚拟形象的待机姿态（Base 层待机动画变体）。待机姿态是持续状态，只在角色的情绪或状态发生持久变化、希望形象静止时以不同姿态待机时调用；不要频繁调用，同一姿态会保持到下一次切换。

        Args:
            idle_name(string): 待机动画变体名，必须逐字使用当前可用的待机动画名
        """
        idle_names = _derive_idle_names(self.config.get("vrma_animations", []))
        if not idle_names:
            return "当前没有配置任何待机动画，无法切换。"
        name = match_allowed_name(idle_name.strip(), idle_names)
        if name is None:
            return f"无效的待机动画名：{idle_name}。可用值：{'、'.join(idle_names)}"
        if self.ws_server.client_count == 0:
            return f"待机动画 {name} 未下发：当前没有形象客户端在线。"
        try:
            await self.ws_server.send_message(SetIdleMessage(name=name))
        except Exception as e:
            logger.warning(f"[Failed to send idle switch]: {e}")
            return f"切换待机动画 {name} 失败。"
        logger.info(f"[Performance] idle: {name}")
        return f"已切换待机动画：{name}。"

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        # The raw reply is decoupled from performance control: the chat
        # result chain is left untouched, and only the avatar client
        # receives the extracted performances.
        result = event.get_result()
        if result is None or not result.chain:
            return
        text = ""
        for comp in result.chain:
            if isinstance(comp, Plain):
                text += comp.text
        if not text.strip():
            return
        if self.ws_server.client_count == 0:
            return

        performances = await self._extract_performances(event, text)
        if not performances:
            return

        message_data = await build_performance_message(
            self.context,
            self.config.get("tts_provider_id", ""),
            performances,
        )
        try:
            await self.ws_server.send_message(message_data)
        except Exception as e:
            logger.warning(f"[Failed to send performance data]: {e}")

    async def _extract_performances(
        self,
        event: AstrMessageEvent,
        text: str,
    ) -> list[Performance]:
        """Split a raw reply into performances via the configured extractor
        model, falling back to the whole reply as a single dialogue beat.

        The extractor model (configured by ``extractor_provider_id``)
        receives the triggering user message (for scene context) plus the
        raw reply and returns a JSON array of ``{text, emotion,
        intensity}`` lines; emotion is validated against the fixed 24-word
        vocabulary and intensity clamped by the parser. When no extractor
        is configured, or its call/parse fails, the reply is forwarded
        whole with the default neutral emotion, so the avatar still speaks
        the entire reply.

        Args:
            event: The message event, used to resolve the extractor
                provider and the triggering user message.
            text: The raw reply text collected from the result chain.

        Returns:
            The ordered list of performances, or an empty list when the
            reply contains no performable dialogue.
        """
        provider_id = self.config.get("extractor_provider_id", "")
        provider = self.context.get_provider_by_id(provider_id) if provider_id else None
        # Only chat providers expose text_chat; other provider types (TTS/STT,
        # etc.) are not usable as the extractor, so fall back to the whole text.
        if not isinstance(provider, Provider):
            logger.info(
                "[Performance] no valid extractor configured, using whole text as one beat"
            )
        else:
            try:
                resp = await provider.text_chat(
                    prompt=build_extractor_user_prompt(event.message_str or "", text),
                    system_prompt=build_extractor_prompt(),
                )
            except Exception as e:
                logger.warning(f"[Extractor request failed]: {e}")
            else:
                performances = parse_performance((resp.completion_text or "").strip())
                if performances:
                    return _merge_fragmented(performances)
                logger.warning(
                    "[Performance] extractor output unparsable, using whole text as one beat"
                )

        # Fallback: the whole reply becomes a single dialogue beat.
        return [Performance(text=text.strip())]

    async def _on_client_connected(self) -> None:
        """Push the VRM model, background, and animations on client connect."""
        character_message = await build_update_character_message(
            self.config.get("vrm_file", []), PLUGIN_NAME
        )
        if character_message is not None:
            try:
                await self.ws_server.send_message(character_message)
                logger.info(f"[Pushed VRM model]: {character_message.vrm.filename}")
            except Exception as e:
                logger.warning(f"[Failed to push VRM model]: {e}")

        background_message = await build_update_background_message(
            self.config.get("background_file", []), PLUGIN_NAME
        )
        if background_message is not None:
            try:
                await self.ws_server.send_message(background_message)
                logger.info(
                    f"[Pushed background]: {background_message.background.filename}"
                )
            except Exception as e:
                logger.warning(f"[Failed to push background]: {e}")

        animations_message = await build_update_animations_message(
            self.config.get("vrma_animations", []), PLUGIN_NAME
        )
        if animations_message is not None:
            try:
                await self.ws_server.send_message(animations_message)
                logger.info(
                    f"[Pushed VRMA animations]: {len(animations_message.animations)}"
                )
            except Exception as e:
                logger.warning(f"[Failed to push VRMA animations]: {e}")
