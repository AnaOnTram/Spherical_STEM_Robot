"""WebSocket manager for real-time streaming and events."""
import asyncio
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class EventType(Enum):
    """WebSocket event types."""
    # Connection events
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"

    # Robot events
    MOVEMENT_UPDATE = "movement_update"
    DISPLAY_UPDATE = "display_update"

    # Detection events
    GESTURE_DETECTED = "gesture_detected"
    PERSON_DETECTED = "person_detected"
    SOUND_DETECTED = "sound_detected"
    ALARM_TRIGGERED = "alarm_triggered"

    # Stream events
    VIDEO_FRAME = "video_frame"
    AUDIO_CHUNK = "audio_chunk"


@dataclass
class WebSocketEvent:
    """WebSocket event data."""
    event_type: EventType
    data: dict
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps({
            "type": self.event_type.value,
            "data": self.data,
            "timestamp": self.timestamp,
        })


class WebSocketManager:
    """Manages WebSocket connections and event broadcasting."""

    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._subscriptions: dict[WebSocket, Set[EventType]] = {}
        self._event_handlers: dict[EventType, list[Callable]] = {}

    @property
    def connection_count(self) -> int:
        """Get number of active connections."""
        return len(self._connections)

    async def connect(
        self,
        websocket: WebSocket,
        subscribe_to: Optional[list[EventType]] = None,
    ) -> None:
        """Accept new WebSocket connection.

        Args:
            websocket: WebSocket connection
            subscribe_to: Event types to subscribe to (None = all)
        """
        await websocket.accept()
        self._connections.add(websocket)

        # Set up subscriptions
        if subscribe_to:
            self._subscriptions[websocket] = set(subscribe_to)
        else:
            self._subscriptions[websocket] = set(EventType)

        logger.info(f"WebSocket connected, total: {self.connection_count}")

        # Send connected event
        await self._send_to_socket(
            websocket,
            WebSocketEvent(
                EventType.CONNECTED,
                {"message": "Connected to Spherical Robot"},
            ),
        )

    async def disconnect(self, websocket: WebSocket) -> None:
        """Handle WebSocket disconnection."""
        self._connections.discard(websocket)
        self._subscriptions.pop(websocket, None)
        logger.info(f"WebSocket disconnected, remaining: {self.connection_count}")

    async def _send_to_socket(
        self,
        websocket: WebSocket,
        event: WebSocketEvent,
    ) -> bool:
        """Send event to specific WebSocket.

        Returns:
            True if sent successfully
        """
        try:
            await websocket.send_text(event.to_json())
            return True
        except Exception as e:
            logger.error(f"Failed to send to WebSocket: {e}")
            return False

    async def broadcast(
        self,
        event: WebSocketEvent,
        exclude: Optional[Set[WebSocket]] = None,
    ) -> int:
        """Broadcast event to all subscribed connections.

        Args:
            event: Event to broadcast
            exclude: Connections to exclude

        Returns:
            Number of connections sent to
        """
        exclude = exclude or set()
        sent_count = 0

        for websocket in list(self._connections):
            if websocket in exclude:
                continue

            # Check if subscribed to this event type
            subscriptions = self._subscriptions.get(websocket, set())
            if event.event_type not in subscriptions:
                continue

            if await self._send_to_socket(websocket, event):
                sent_count += 1
            else:
                # Remove failed connection
                await self.disconnect(websocket)

        return sent_count

    async def send_to(
        self,
        websocket: WebSocket,
        event: WebSocketEvent,
    ) -> bool:
        """Send event to specific connection."""
        if websocket not in self._connections:
            return False
        return await self._send_to_socket(websocket, event)

    def subscribe(
        self,
        websocket: WebSocket,
        event_types: list[EventType],
    ) -> None:
        """Subscribe connection to event types."""
        if websocket in self._subscriptions:
            self._subscriptions[websocket].update(event_types)

    def unsubscribe(
        self,
        websocket: WebSocket,
        event_types: list[EventType],
    ) -> None:
        """Unsubscribe connection from event types."""
        if websocket in self._subscriptions:
            self._subscriptions[websocket] -= set(event_types)

    # Event broadcasting helpers
    async def broadcast_gesture(
        self,
        gesture: str,
        confidence: float,
        handedness: str = "unknown",
    ) -> None:
        """Broadcast gesture detection event."""
        await self.broadcast(WebSocketEvent(
            EventType.GESTURE_DETECTED,
            {
                "gesture": gesture,
                "confidence": confidence,
                "handedness": handedness,
            },
        ))

    async def broadcast_person(
        self,
        person_id: int,
        bbox: dict,
        confidence: float,
    ) -> None:
        """Broadcast person detection event."""
        await self.broadcast(WebSocketEvent(
            EventType.PERSON_DETECTED,
            {
                "id": person_id,
                "bbox": bbox,
                "confidence": confidence,
            },
        ))

    async def broadcast_sound(
        self,
        category: str,
        confidence: float,
        class_name: str,
    ) -> None:
        """Broadcast sound detection event."""
        await self.broadcast(WebSocketEvent(
            EventType.SOUND_DETECTED,
            {
                "category": category,
                "confidence": confidence,
                "class_name": class_name,
            },
        ))

    async def broadcast_alarm(
        self,
        state: str,
        duration: float,
        audio_file: Optional[str] = None,
    ) -> None:
        """Broadcast alarm event."""
        await self.broadcast(WebSocketEvent(
            EventType.ALARM_TRIGGERED,
            {
                "state": state,
                "duration": duration,
                "audio_file": audio_file,
            },
        ))

    async def broadcast_movement(
        self,
        left_speed: int,
        right_speed: int,
        status: str,
    ) -> None:
        """Broadcast movement update."""
        await self.broadcast(WebSocketEvent(
            EventType.MOVEMENT_UPDATE,
            {
                "left_speed": left_speed,
                "right_speed": right_speed,
                "status": status,
            },
        ))

    async def handle_connection(self, websocket: WebSocket) -> None:
        """Handle WebSocket connection lifecycle.

        Use this in FastAPI route:
            @app.websocket("/ws")
            async def websocket_endpoint(websocket: WebSocket):
                await ws_manager.handle_connection(websocket)
        """
        await self.connect(websocket)

        try:
            while True:
                # Receive and process messages
                data = await websocket.receive_text()

                try:
                    message = json.loads(data)
                    await self._handle_message(websocket, message)
                except json.JSONDecodeError:
                    await self.send_to(websocket, WebSocketEvent(
                        EventType.ERROR,
                        {"message": "Invalid JSON"},
                    ))

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            await self.disconnect(websocket)

    async def _handle_message(
        self,
        websocket: WebSocket,
        message: dict,
    ) -> None:
        """Handle incoming WebSocket message."""
        msg_type = message.get("type")

        if msg_type == "subscribe":
            # Subscribe to event types
            events = message.get("events", [])
            event_types = []
            for event_name in events:
                try:
                    event_types.append(EventType(event_name))
                except ValueError:
                    pass
            self.subscribe(websocket, event_types)

        elif msg_type == "unsubscribe":
            # Unsubscribe from event types
            events = message.get("events", [])
            event_types = []
            for event_name in events:
                try:
                    event_types.append(EventType(event_name))
                except ValueError:
                    pass
            self.unsubscribe(websocket, event_types)

        elif msg_type == "ping":
            # Respond to ping
            await self.send_to(websocket, WebSocketEvent(
                EventType.CONNECTED,
                {"message": "pong"},
            ))


# Global WebSocket manager instance
ws_manager = WebSocketManager()
