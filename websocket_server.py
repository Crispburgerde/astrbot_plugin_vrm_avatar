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
import email.utils
import http
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import final
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, JsonValue
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.http11 import Request, Response

from astrbot.api import logger

# Callback type aliases -----------------------------------------------------
AsyncCallback = Callable[[], Awaitable[None]]
MessageCallback = Callable[[dict[str, JsonValue]], Awaitable[None]]

# MIME types for the plugin data files served over plain HTTP.
_CONTENT_TYPES = {
    ".vrm": "model/gltf-binary",
    ".vrma": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


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
        file_root: Plugin data directory whose files are served over plain
            HTTP under ``/files/`` (see :meth:`_handle_http`). ``None``
            disables file serving.
        http_port: Port for the HTTP file server. ``None`` means automatic
            (WebSocket port + 1).
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
        file_root: Path | None = None,
        http_port: int | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_message = on_message
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._http_port = http_port

        self._server: Server | None = None
        self._connections: set[ServerConnection] = set()
        self._http_server: asyncio.Server | None = None
        self._file_root = file_root.resolve(strict=False) if file_root else None

    # -- Properties ---------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the server is currently listening."""
        return self._server is not None

    @property
    def client_count(self) -> int:
        """Number of currently connected clients."""
        return len(self._connections)

    @property
    def http_port(self) -> int:
        """Port of the plain-HTTP file server (WebSocket port + 1 by default)."""
        if self._http_port is not None:
            return self._http_port
        return self.port + 1

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
            process_request=self._process_request,
        )
        logger.info(f"[WebSocket] server listening on ws://{self.host}:{self.port}")

        # The file server runs on its own port: websockets' ``process_request``
        # hook aborts the TCP connection right after replying to a
        # non-WebSocket request, which would truncate large file downloads.
        if self._file_root is not None:
            self._http_server = await asyncio.start_server(
                self._handle_http, self.host, self.http_port
            )
            logger.info(
                f"[FileServer] file server listening on "
                f"http://{self.host}:{self.http_port}/files/"
            )

    def _process_request(
        self,
        connection: ServerConnection,
        request: Request,
    ) -> Response | None:
        """Intercept the opening handshake to answer plain HTTP requests.

        Browsers, health checks, or port probes send regular HTTP (e.g.
        ``Connection: keep-alive``) which would otherwise make the library
        raise ``InvalidUpgrade`` and log a full traceback per request. File
        downloads are NOT served here — see :meth:`_handle_http` — because
        the websockets library aborts the connection immediately after a
        handshake rejection response.

        Args:
            connection: The incoming server connection.
            request: The HTTP handshake request.

        Returns:
            An HTTP 426 response for non-WebSocket requests, or ``None``
            to continue the normal WebSocket handshake.
        """
        if request.headers.get("Upgrade", "").lower() != "websocket":
            logger.info(
                f"[WebSocket] rejected non-WebSocket request "
                f"{request.path!r} from {connection.remote_address}"
            )
            return connection.respond(
                http.HTTPStatus.UPGRADE_REQUIRED, "WebSocket endpoint only\n"
            )
        return None

    # -- HTTP file server ----------------------------------------------------

    async def _handle_http(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Serve one ``GET /files/<relative-path>`` request from ``file_root``.

        Large assets (VRM model, background, VRMA animations) are fetched by
        the browser over plain HTTP with CORS, ETag revalidation, and
        streaming reads, instead of being pushed as base64 inside WebSocket
        JSON messages. Responses use ``Connection: close``; a 304 is returned
        when the client's ``If-None-Match`` matches, so page refreshes never
        re-download an unchanged model.
        """
        peer = writer.get_extra_info("peername")
        try:
            request_line = await reader.readline()
            if not request_line:
                return
            parts = request_line.decode("latin-1").split()
            if len(parts) != 3:
                self._write_http(writer, http.HTTPStatus.BAD_REQUEST, "Bad request\n")
                return
            method, raw_path, _ = parts
            req_headers: dict[str, str] = {}
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                name, _, value = line.decode("latin-1").partition(":")
                req_headers[name.strip().lower()] = value.strip()

            path = urlsplit(raw_path).path
            if method != "GET" or not path.startswith("/files/"):
                self._write_http(writer, http.HTTPStatus.NOT_FOUND, "Not found\n")
                return
            rel = unquote(path[len("/files/") :])
            assert self._file_root is not None  # guarded by start()
            target = (self._file_root / rel).resolve(strict=False)
            if not target.is_relative_to(self._file_root):
                self._write_http(writer, http.HTTPStatus.FORBIDDEN, "Forbidden\n")
                return
            if not target.is_file():
                self._write_http(writer, http.HTTPStatus.NOT_FOUND, "Not found\n")
                return

            stat = target.stat()
            etag = f'"{stat.st_size}-{stat.st_mtime_ns}"'
            last_modified = email.utils.formatdate(stat.st_mtime, usegmt=True)
            if req_headers.get("if-none-match") == etag:
                self._write_http(
                    writer,
                    http.HTTPStatus.NOT_MODIFIED,
                    "",
                    etag=etag,
                    last_modified=last_modified,
                )
                logger.info(f"[FileServer] file cache hit (304) {rel!r} from {peer}")
                return

            self._write_http(
                writer,
                http.HTTPStatus.OK,
                "",
                etag=etag,
                last_modified=last_modified,
                content_type=_CONTENT_TYPES.get(
                    target.suffix.lower(), "application/octet-stream"
                ),
                content_length=stat.st_size,
            )
            with open(target, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    writer.write(chunk)
                    await writer.drain()
            logger.info(
                f"[FileServer] served file {rel!r} ({stat.st_size} bytes) to {peer}"
            )
        except ConnectionError:
            pass  # client aborted the download
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[FileServer] file request from {peer} failed: {exc}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    @staticmethod
    def _write_http(
        writer: asyncio.StreamWriter,
        status: http.HTTPStatus,
        body: str,
        etag: str | None = None,
        last_modified: str | None = None,
        content_type: str | None = None,
        content_length: int | None = None,
    ) -> None:
        """Write a minimal ``Connection: close`` HTTP response header (and body)."""
        headers = [
            f"HTTP/1.1 {status.value} {status.phrase}",
            "Access-Control-Allow-Origin: *",
            # ETag/Last-Modified are not CORS-safelisted; without this the
            # cross-origin frontend cannot read them for cache validation.
            "Access-Control-Expose-Headers: ETag, Last-Modified",
            # Always revalidate: the user can swap files under the same name,
            # and a 304 round-trip is nearly free.
            "Cache-Control: no-cache",
            "Connection: close",
        ]
        if etag is not None:
            headers.append(f"ETag: {etag}")
        if last_modified is not None:
            headers.append(f"Last-Modified: {last_modified}")
        if content_type is not None:
            headers.append(f"Content-Type: {content_type}")
        headers.append(
            f"Content-Length: {content_length if content_length is not None else len(body.encode())}"
        )
        writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("latin-1"))
        if body:
            writer.write(body.encode())

    async def stop(self) -> None:
        """Stop the server and close all active client connections."""
        if self._server is None:
            return

        # Close all client connections first so handlers exit promptly.
        for ws in list[ServerConnection](self._connections):
            await ws.close()
        self._connections.clear()

        if self._http_server is not None:
            self._http_server.close()
            await self._http_server.wait_closed()
            self._http_server = None

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
