"""Redis Stream command consumer."""

import asyncio
import json
import logging
from typing import Callable, Awaitable
try:
    from redis.asyncio import Redis
except ImportError:
    Redis = None  # type: ignore[assignment,misc]

logger = logging.getLogger("feldwebel.consumer")


class CommandConsumer:
    """Consumes commands from a Redis Stream."""

    def __init__(
        self,
        redis: Redis,
        stream_name: str,
        group_name: str = "feldwebel",
        consumer_name: str = "worker-1",
    ):
        self._redis = redis
        self._stream = stream_name
        self._group = group_name
        self._consumer = consumer_name
        self._handlers: dict[str, Callable[..., Awaitable]] = {}
        self._running = False

    def on(self, command_type: str, handler: Callable[..., Awaitable]) -> None:
        """Register a handler for a command type."""
        self._handlers[command_type] = handler

    async def setup(self) -> None:
        """Create the consumer group (idempotent)."""
        try:
            await self._redis.xgroup_create(
                self._stream, self._group, id="0", mkstream=True,
            )
        except Exception:
            pass  # Group already exists

    async def run(self) -> None:
        """Start consuming commands. Blocks until stop() is called."""
        self._running = True
        logger.info(f"Consumer started on stream {self._stream}")

        while self._running:
            try:
                messages = await self._redis.xreadgroup(
                    self._group, self._consumer,
                    {self._stream: ">"},
                    count=1, block=1000,
                )

                if not messages:
                    continue

                for stream_name, entries in messages:
                    for msg_id, fields in entries:
                        await self._process_message(msg_id, fields)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error consuming command: {e}")
                await asyncio.sleep(1)

    async def _process_message(self, msg_id: bytes, fields: dict) -> None:
        """Process a single command message."""
        try:
            # Handle both bytes and string keys (depends on decode_responses)
            cmd_type = self._decode_field(fields, "type")
            payload_raw = self._decode_field(fields, "payload") or "{}"
            payload = json.loads(payload_raw)

            handler = self._handlers.get(cmd_type)
            if handler:
                logger.info(f"Processing command: {cmd_type} {payload}")
                await handler(payload)
            else:
                logger.warning(f"Unknown command type: {cmd_type}")

            await self._redis.xack(self._stream, self._group, msg_id)

        except Exception as e:
            logger.error(f"Error processing message {msg_id}: {e}")
            await self._redis.xack(self._stream, self._group, msg_id)

    @staticmethod
    def _decode_field(fields: dict, key: str) -> str:
        """Get a field value, handling both bytes and string keys."""
        val = fields.get(key) or fields.get(key.encode(encoding="utf-8"), b"")
        return val.decode() if isinstance(val, bytes) else str(val)

    def stop(self) -> None:
        """Signal the consumer to stop."""
        self._running = False
