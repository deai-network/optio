"""Engine-wide conversation archive listener: read a suspended task's record.

One aiohttp server per engine, reached through the optio-api proxy (which
injects the basic-auth credential), like the per-task conversation listeners:

  GET /conversation/{processId}  — processId is the process document's _id
      (an ObjectId hex string). 200 {"events": [[seq, event], ...],
      "capturedAt": <ISO or null>} for a suspended process whose task has a
      `read_conversation` hook; otherwise an error.

Errors are JSON {"reason": <code>, "message": <text>}:
  401 unauthorized             wrong or missing basic auth (optio:<password>)
  404 not-found                no process document with that _id
  409 not-suspended            not cancelled + supportsResume + hasSavedState
  404 no-conversation-support  no registered task, or no read_conversation hook
  404 no-conversation          the hook returned None (no record)
  500 read-failed              the hook raised (or returned something unusable)

Every response carries Cache-Control: no-store. Decryption happens inside the
task's hook, in this process; the plaintext exists only in memory and in
transit. Nothing here logs conversation content or the password.

The address is published as {_id: "conversationArchive", url, password,
updatedAt} in {prefix}_engine_endpoints and deleted on stop (a crashed engine
leaves a stale document; the proxy then answers 503).

Spec: docs/2026-10-12-suspended-conversation-view-design.md §1.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from aiohttp import web
from bson import ObjectId

if TYPE_CHECKING:
    from optio_core.context import ProcessContext
    from optio_core.models import TaskInstance

logger = logging.getLogger(__name__)

ENDPOINT_ID = "conversationArchive"
# Bound aiohttp's graceful-shutdown wait (the 60 s default would stall engine
# shutdown behind a slow read).
SHUTDOWN_TIMEOUT_S = 2.0
_OBJECTID_RE = re.compile(r"^[0-9a-fA-F]{24}$")
_NO_STORE = {"Cache-Control": "no-store"}


def is_suspended(doc: dict) -> bool:
    """A suspended process: cancelled, resumable, with a saved state."""
    status = doc.get("status") or {}
    return (
        status.get("state") == "cancelled"
        and bool(doc.get("supportsResume"))
        and bool(doc.get("hasSavedState"))
    )


def _iso(value: Any) -> Any:
    # A naive datetime (Mongo's default decoding) is UTC.
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return value


def _payload(result: Any) -> dict | None:
    """The 200 body for a hook result, or None for "no record"."""
    if result is None:
        return None
    if isinstance(result, dict):
        events = result.get("events")
        if events is None:
            return None
        captured_at = _iso(result.get("capturedAt"))
    elif isinstance(result, (list, tuple)):
        events, captured_at = result, None
    else:
        raise TypeError(f"read_conversation returned {type(result).__name__}")
    return {"events": list(events), "capturedAt": captured_at}


def _error(status: int, reason: str, message: str) -> web.Response:
    return web.json_response(
        {"reason": reason, "message": message}, status=status, headers=_NO_STORE,
    )


class ConversationArchiveListener:
    def __init__(
        self,
        *,
        db,
        prefix: str,
        resolve_task: Callable[[str], "TaskInstance | None"],
        make_context: Callable[[dict], "ProcessContext"],
    ) -> None:
        self._db = db
        self._prefix = prefix
        self._resolve_task = resolve_task
        self._make_context = make_context
        self._password = secrets.token_urlsafe(32)
        self._runner: web.AppRunner | None = None
        self._url: str | None = None

    @property
    def url(self) -> str | None:
        """The published address, `http://<host>:<port>/`; None until started."""
        return self._url

    @property
    def password(self) -> str:
        """The inner basic-auth password (user `optio`)."""
        return self._password

    @property
    def _endpoints(self):
        return self._db[f"{self._prefix}_engine_endpoints"]

    # -- HTTP --------------------------------------------------------------------

    def _authorized(self, request: web.Request) -> bool:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            userpass = base64.b64decode(auth[6:], validate=True)
        except (binascii.Error, ValueError):
            return False
        return secrets.compare_digest(userpass, f"optio:{self._password}".encode())

    async def _handle_conversation(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return _error(401, "unauthorized", "Missing or wrong credentials.")
        id_str = request.match_info["processId"]
        doc = None
        if _OBJECTID_RE.match(id_str):
            doc = await self._db[f"{self._prefix}_processes"].find_one(
                {"_id": ObjectId(id_str)},
            )
        if doc is None:
            return _error(404, "not-found", "No such process.")
        if not is_suspended(doc):
            return _error(409, "not-suspended", "The process is not suspended.")
        task = self._resolve_task(doc["processId"])
        hook = getattr(task, "read_conversation", None) if task is not None else None
        if hook is None:
            return _error(
                404, "no-conversation-support",
                "This task does not keep a readable conversation record.",
            )
        try:
            payload = _payload(await hook(self._make_context(doc)))
            body = None if payload is None else json.dumps(payload)
        except Exception as exc:  # noqa: BLE001
            # The exception type only: its message may carry conversation content.
            logger.error(
                "conversation archive: reading the record of process %s (%s) failed: %s",
                id_str, doc["processId"], type(exc).__name__,
            )
            return _error(500, "read-failed", "Reading the conversation record failed.")
        if body is None:
            return _error(404, "no-conversation", "No saved conversation record.")
        return web.Response(text=body, content_type="application/json", headers=_NO_STORE)

    # -- lifecycle -----------------------------------------------------------------

    async def start(self) -> None:
        """Bind (OPTIO_WIDGET_TUNNEL_BIND, ephemeral port) and publish the address."""
        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
        app = web.Application()
        app.router.add_get("/conversation/{processId}", self._handle_conversation)
        runner = web.AppRunner(app, shutdown_timeout=SHUTDOWN_TIMEOUT_S)
        await runner.setup()
        try:
            site = web.TCPSite(runner, bind_addr, 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            url = f"http://{upstream_host}:{port}/"
            await self._endpoints.replace_one(
                {"_id": ENDPOINT_ID},
                {"url": url, "password": self._password,
                 "updatedAt": datetime.now(timezone.utc)},
                upsert=True,
            )
        except BaseException:
            await runner.cleanup()
            raise
        self._runner, self._url = runner, url
        logger.info("conversation archive listening; published %s", url)

    async def stop(self) -> None:
        """Unpublish (only our own document) and close. Idempotent."""
        runner, self._runner = self._runner, None
        if runner is None:
            return
        try:
            # A newer engine may have replaced the document; leave it alone.
            await self._endpoints.delete_one(
                {"_id": ENDPOINT_ID, "password": self._password},
            )
        finally:
            await runner.cleanup()
