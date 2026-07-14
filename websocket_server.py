"""Async WebSocket server for real-time communication with VRM avatar clients.

Features
--------
- Async lifecycle management (``start`` / ``stop``).
- Multi-client connection tracking with thread-safe add/remove.
- JSON-based message protocol (send and receive).
- Broadcast and targeted message delivery.
- Configurable ``on_connect`` / ``on_disconnect`` / ``on_message`` callbacks.
- Automatic heartbeat (ping/pong) keepalive.
- Graceful shutdown that closes every active connection.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import final

from pydantic import BaseModel, JsonValue
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed, WebSocketException

from astrbot.api import logger

# Callback type aliases -------------------------------------------------------
AsyncCallback = Callable[[], Awaitable[None]]
MessageCallback = Callable[[dict[str, JsonValue]], Awaitable[None]]


@final
class WebSocketServer:
    """Async WebSocket server with multi-client connection management.

    Args:
        host: Bind address (e.g. ``"localhost"`` or ``"0.0.0.0"``).
        port: TCP port to listen on.
        on_connect: Awaited after every successful client handshake.
        on_disconnect: Awaited after a client disconnects.
        on_message: Awaited for every valid JSON message received.
        ping_interval: Seconds between automatic ping frames. ``0`` disables.
        ping_timeout: Seconds to wait for a pong before dropping the connection.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8765,
        on_connect: AsyncCallback | None = None,
        on_disconnect: AsyncCallback | None = None,
        on_message: MessageCallback | None = None,
        ping_interval: float = 20.0,
        ping_timeout: float = 20.0,
    ) -> None:
        self.host = host
        self.port = port
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_message = on_message
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout

        self._server: Server | None = None
        self._connections: set[ServerConnection] = set()

    # -- Properties ---------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the server is currently listening."""
        return self._server is not None

    @property
    def client_count(self) -> int:
        """Number of currently connected clients."""
        return len(self._connections)

    # -- Lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Start listening for WebSocket connections (idempotent)."""
        if self._server is not None:
            logger.warning("[WebSocket] server is already running")
            return
        self._server = await serve(
            self._handle_connection,
            self.host,
            self.port,
            ping_interval=self._ping_interval,
            ping_timeout=self._ping_timeout,
        )
        logger.info(f"[WebSocket] server listening on ws://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop the server and close all active client connections."""
        if self._server is None:
            return

        # Close all client connections first so handlers exit promptly.
        for ws in list[ServerConnection](self._connections):
            await ws.close()
        self._connections.clear()

        self._server.close()
        await self._server.wait_closed()
        self._server = None
        logger.info("[WebSocket] server stopped")

    # -- Connection handling ------------------------------------------------

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        """Per-connection coroutine: register, dispatch messages, unregister."""
        peer = websocket.remote_address
        self._connections.add(websocket)
        logger.info(
            f"[WebSocket] client connected: {peer} (total: {self.client_count})"
        )
        try:
            if self._on_connect is not None:
                await self._on_connect()
            await self._receive_loop(websocket)
        except ConnectionClosed:
            pass
        except WebSocketException as exc:
            logger.warning(f"[WebSocket] protocol error from {peer}: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[WebSocket] unexpected error from {peer}: {exc}")
        finally:
            self._connections.discard(websocket)
            logger.info(
                f"[WebSocket] client disconnected: {peer} (remaining: {self.client_count})"
            )
            if self._on_disconnect is not None:
                try:
                    await self._on_disconnect()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[WebSocket] on_disconnect callback error: {exc}")

    async def _receive_loop(self, websocket: ServerConnection) -> None:
        """Read and dispatch incoming JSON messages until the socket closes."""
        async for raw in websocket:
            try:
                payload: JsonValue = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    f"[WebSocket] non-JSON payload from {websocket.remote_address}: {raw!r}"
                )
                continue
            if not isinstance(payload, dict):
                logger.warning(
                    f"[WebSocket] non-object JSON from {websocket.remote_address}: {payload!r}"
                )
                continue
            if self._on_message is not None:
                try:
                    await self._on_message(payload)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[WebSocket] on_message callback error: {exc}")

    # -- Sending ------------------------------------------------------------

    async def send_message(
        self,
        message: BaseModel,
        websocket: ServerConnection | None = None,
    ) -> None:
        """Send a JSON message.

        Args:
            message: Pydantic model that will be serialised to JSON. ``None``
                valued fields are omitted so optional payloads stay absent.
            websocket: When provided, send only to this client. When ``None``,
                the message is broadcast to every connected client.

        Raises:
            ConnectionClosed: When sending to a specific client that has closed.
        """
        data = message.model_dump_json(exclude_none=True, ensure_ascii=False)

        if websocket is not None:
            await websocket.send(data)
            return

        # Broadcast — copy the set because it may mutate during iteration.
        targets = list(self._connections)
        if not targets:
            logger.debug("[WebSocket] broadcast skipped — no connected clients")
            return

        results = await asyncio.gather(
            *(ws.send(data) for ws in targets), return_exceptions=True
        )
        for ws, result in zip(targets, results):
            if isinstance(result, Exception):
                logger.warning(
                    f"[WebSocket] broadcast failed to {ws.remote_address}: {result}"
                )
