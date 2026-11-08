"""Shared wire-protocol types used across the plugin."""

from pydantic import BaseModel


class FilePayload(BaseModel):
    """A file reference on the wire.

    Large assets (VRM model, background, VRMA animations) are referenced by
    ``url`` — a path served by the plugin's own HTTP file server on its
    configurable port (``http_port``, default 8766) — so the client fetches
    and caches them natively instead of pushing megabytes of base64 through
    a JSON WebSocket message. Small
    payloads (TTS audio) still use the ``data`` base64 field. Exactly one
    of ``url`` / ``data`` is set; ``exclude_none`` on serialisation keeps
    the absent one out of the message.
    """

    filename: str
    url: str | None = None
    data: str | None = None
