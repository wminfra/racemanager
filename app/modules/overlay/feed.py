import asyncio
import json
import logging

from websockets.asyncio.client import connect

logger = logging.getLogger(__name__)


async def receive_kiosk_states(url: str, emit, stopping, connect_factory=connect,
                               retry_seconds: float = 2.0):
    """Reconnect to the sanitized, read-only Kiosk WebSocket on the local machine."""
    while not stopping():
        try:
            async with connect_factory(url, open_timeout=4, close_timeout=1, max_size=16_384) as socket:
                async for message in socket:
                    if stopping():
                        return
                    try:
                        state = json.loads(message)
                    except (TypeError, json.JSONDecodeError):
                        logger.warning("Ignoring invalid message from Kiosk WebSocket")
                        continue
                    if isinstance(state, dict):
                        emit(state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Overlay Kiosk WebSocket disconnected: %s", exc)
        if not stopping():
            await asyncio.sleep(retry_seconds)
