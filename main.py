from typing import cast, override

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import Plain

from .messages import (
    build_performance,
    build_update_animations_message,
    build_update_background_message,
    build_update_character_message,
)
from .prompts import build_performance_prompt
from .websocket_server import WebSocketServer

PLUGIN_NAME = "astrbot_plugin_vrm_avatar"


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
            host=config.get("ws_host", "localhost"),
            port=config.get("ws_port", 8765),
            on_connect=self._on_client_connected,
        )

    @override
    async def initialize(self):
        """插件初始化时启动 WebSocket 服务器"""
        await self.ws_server.start()

    @override
    async def terminate(self):
        """插件销毁时清理 WebSocket 服务器"""
        await self.ws_server.stop()

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        expressions = self.config.get("expressions", ["neutral"])
        # Derive action names from configured VRMA animations (excluding the
        # implicit ``idle`` standby), so the LLM can pick a fitting animation.
        actions = [
            a["name"].strip()
            for a in self.config.get("vrma_animations", [])
            if isinstance(a, dict)
            and isinstance(a.get("name"), str)
            and a["name"].strip()
            and a["name"].strip().lower() != "idle"
        ]
        req.system_prompt += build_performance_prompt(expressions, actions)

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        result = event.get_result()
        if result is None or not result.chain:
            return
        text = ""
        for comp in result.chain:
            if isinstance(comp, Plain):
                text += comp.text
        if not text:
            return
        message_data = await build_performance(
            cast(Context, self.context),
            self.config.get("tts_provider_id", ""),
            text,
        )
        if message_data is None:
            return
        try:
            await self.ws_server.send_message(message_data)
        except Exception as e:
            logger.warning(f"[发送表演数据失败]: {e}")

    async def _on_client_connected(self) -> None:
        """客户端连接成功后推送 VRM 模型与背景图。"""
        character_message = await build_update_character_message(
            self.config.get("vrm_file", []), PLUGIN_NAME
        )
        if character_message is not None:
            try:
                await self.ws_server.send_message(character_message)
                logger.info(f"[已推送 VRM 模型]: {character_message.vrm.filename}")
            except Exception as e:
                logger.warning(f"[推送 VRM 模型失败]: {e}")

        background_message = await build_update_background_message(
            self.config.get("background_file", []), PLUGIN_NAME
        )
        if background_message is not None:
            try:
                await self.ws_server.send_message(background_message)
                logger.info(f"[已推送背景图]: {background_message.background.filename}")
            except Exception as e:
                logger.warning(f"[推送背景图失败]: {e}")

        animations_message = await build_update_animations_message(
            self.config.get("vrma_animations", []), PLUGIN_NAME
        )
        if animations_message is not None:
            try:
                await self.ws_server.send_message(animations_message)
                logger.info(
                    f"[已推送 VRMA 动画]: {len(animations_message.animations)} 个"
                )
            except Exception as e:
                logger.warning(f"[推送 VRMA 动画失败]: {e}")
