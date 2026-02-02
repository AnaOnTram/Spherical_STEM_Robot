"""Multi-channel notification manager for crying detection alerts."""
import asyncio
import json
import logging
import platform
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, List

import numpy as np

logger = logging.getLogger(__name__)


class NotificationChannel(Enum):
    """Available notification channels."""
    WEBSOCKET = "websocket"
    WEBHOOK = "webhook"
    LOCAL_SOUND = "local_sound"
    FILE_LOG = "file_log"


@dataclass
class DetectionEvent:
    """Crying detection event."""
    timestamp: datetime
    event_type: str  # "crying_detected", "crying_confirmed", "alarm_triggered"
    confidence: float
    duration: float = 0.0
    audio_file: Optional[str] = None
    metadata: dict = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type,
            "confidence": self.confidence,
            "duration": self.duration,
            "audio_file": self.audio_file,
            "metadata": self.metadata,
        }


class NotificationManager:
    """Manages multi-channel notifications for crying detection."""
    
    def __init__(
        self,
        webhook_url: Optional[str] = None,
        local_sound_enabled: bool = True,
        log_file: Optional[str] = None,
        max_history: int = 100,
    ):
        self.webhook_url = webhook_url
        self.local_sound_enabled = local_sound_enabled
        self.log_file = log_file
        self.max_history = max_history
        
        # Event history (circular buffer)
        self._history: deque = deque(maxlen=max_history)
        self._history_lock = threading.Lock()
        
        # Callbacks for WebSocket notifications
        self._websocket_callbacks: List[Callable[[DetectionEvent], None]] = []
        
        # Audio player for local sound
        self._audio_player = None
        
        # Setup log file
        if self.log_file:
            self._setup_log_file()
    
    def _setup_log_file(self) -> None:
        """Setup log file directory."""
        if self.log_file:
            log_path = Path(self.log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
    
    def add_websocket_callback(self, callback: Callable[[DetectionEvent], None]) -> None:
        """Add callback for WebSocket notifications."""
        self._websocket_callbacks.append(callback)
    
    def remove_websocket_callback(self, callback: Callable[[DetectionEvent], None]) -> None:
        """Remove WebSocket callback."""
        if callback in self._websocket_callbacks:
            self._websocket_callbacks.remove(callback)
    
    def notify(self, event: DetectionEvent, channels: Optional[List[NotificationChannel]] = None) -> None:
        """Send notification through specified channels.
        
        Args:
            event: Detection event to notify about
            channels: List of channels to use (None = all enabled)
        """
        # Add to history
        with self._history_lock:
            self._history.append(event)
        
        if channels is None:
            channels = list(NotificationChannel)
        
        # Send through each channel
        for channel in channels:
            try:
                if channel == NotificationChannel.WEBSOCKET:
                    self._notify_websocket(event)
                elif channel == NotificationChannel.WEBHOOK:
                    self._notify_webhook(event)
                elif channel == NotificationChannel.LOCAL_SOUND:
                    self._notify_local_sound(event)
                elif channel == NotificationChannel.FILE_LOG:
                    self._notify_file_log(event)
            except Exception as e:
                logger.error(f"Notification error on {channel.value}: {e}")
    
    def _notify_websocket(self, event: DetectionEvent) -> None:
        """Send WebSocket notification."""
        for callback in self._websocket_callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"WebSocket callback error: {e}")
    
    def _notify_webhook(self, event: DetectionEvent) -> None:
        """Send HTTP webhook notification."""
        if not self.webhook_url:
            return
        
        # Run in thread to avoid blocking
        def send_webhook():
            try:
                import urllib.request
                import urllib.error
                
                data = json.dumps(event.to_dict()).encode('utf-8')
                
                req = urllib.request.Request(
                    self.webhook_url,
                    data=data,
                    headers={
                        'Content-Type': 'application/json',
                        'User-Agent': 'SphericalBot/1.0',
                    },
                    method='POST'
                )
                
                with urllib.request.urlopen(req, timeout=10) as response:
                    logger.debug(f"Webhook sent: {response.status}")
                    
            except Exception as e:
                logger.warning(f"Webhook failed: {e}")
        
        thread = threading.Thread(target=send_webhook, daemon=True)
        thread.start()
    
    def _notify_local_sound(self, event: DetectionEvent) -> None:
        """Play local alarm sound."""
        if not self.local_sound_enabled:
            return
        
        # Only play sound for confirmed alarms
        if event.event_type != "alarm_triggered":
            return
        
        # Run in thread to avoid blocking
        def play_sound():
            try:
                system = platform.system()
                
                if system == "Darwin":  # macOS
                    # Use afplay for alarm sound
                    # Try to play a system sound or generate one
                    try:
                        # Try system alert sound
                        subprocess.run(
                            ["afplay", "/System/Library/Sounds/Glass.aiff"],
                            check=False,
                            timeout=5
                        )
                    except Exception:
                        # Fallback to say command
                        subprocess.run(
                            ["say", "Baby crying detected"],
                            check=False,
                            timeout=5
                        )
                        
                elif system == "Linux":
                    # Try multiple methods for Linux
                    methods = [
                        ["aplay", "-q"],  # Will need actual file
                        ["paplay"],  # PulseAudio
                        ["ogg123"],  # Ogg Vorbis
                    ]
                    
                    # Generate a simple beep using speaker-test or console beep
                    try:
                        subprocess.run(
                            ["speaker-test", "-t", "sine", "-f", "800", "-l", "1"],
                            check=False,
                            timeout=3,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                    except Exception:
                        pass
                
                # Try to use the audio player if available
                if self._audio_player:
                    try:
                        self._audio_player.play_tone(frequency=800, duration=2.0)
                    except Exception as e:
                        logger.debug(f"Audio player sound failed: {e}")
                        
            except Exception as e:
                logger.warning(f"Local sound notification failed: {e}")
        
        thread = threading.Thread(target=play_sound, daemon=True)
        thread.start()
    
    def _notify_file_log(self, event: DetectionEvent) -> None:
        """Write to log file."""
        if not self.log_file:
            return
        
        try:
            log_entry = json.dumps({
                **event.to_dict(),
                "logged_at": datetime.now().isoformat()
            })
            
            with open(self.log_file, 'a') as f:
                f.write(log_entry + '\n')
                
        except Exception as e:
            logger.warning(f"File logging failed: {e}")
    
    def get_history(self, limit: int = 100, event_type: Optional[str] = None) -> List[DetectionEvent]:
        """Get detection history.
        
        Args:
            limit: Maximum number of events to return
            event_type: Filter by event type (None = all)
            
        Returns:
            List of detection events
        """
        with self._history_lock:
            events = list(self._history)
        
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        
        return events[-limit:]
    
    def clear_history(self) -> None:
        """Clear detection history."""
        with self._history_lock:
            self._history.clear()
    
    def set_audio_player(self, player) -> None:
        """Set audio player for local sound notifications."""
        self._audio_player = player


class AlarmNotifier:
    """High-level interface for alarm notifications."""
    
    def __init__(self, notification_manager: NotificationManager):
        self._manager = notification_manager
        self._last_notification_time = 0
        self._notification_cooldown = 5.0  # seconds between notifications
    
    def notify_crying_detected(self, confidence: float, **kwargs) -> None:
        """Notify that crying was detected."""
        event = DetectionEvent(
            timestamp=datetime.now(),
            event_type="crying_detected",
            confidence=confidence,
            metadata=kwargs
        )
        self._manager.notify(event, [NotificationChannel.WEBSOCKET, NotificationChannel.FILE_LOG])
    
    def notify_crying_confirmed(self, confidence: float, duration: float, **kwargs) -> None:
        """Notify that crying was confirmed (sustained)."""
        event = DetectionEvent(
            timestamp=datetime.now(),
            event_type="crying_confirmed",
            confidence=confidence,
            duration=duration,
            metadata=kwargs
        )
        self._manager.notify(event, [NotificationChannel.WEBSOCKET, NotificationChannel.FILE_LOG])
    
    def notify_alarm_triggered(
        self,
        confidence: float,
        duration: float,
        audio_file: Optional[str] = None,
        **kwargs
    ) -> None:
        """Notify that alarm was triggered."""
        # Check cooldown
        current_time = time.time()
        if current_time - self._last_notification_time < self._notification_cooldown:
            logger.debug("Skipping notification due to cooldown")
            return
        
        self._last_notification_time = current_time
        
        event = DetectionEvent(
            timestamp=datetime.now(),
            event_type="alarm_triggered",
            confidence=confidence,
            duration=duration,
            audio_file=audio_file,
            metadata=kwargs
        )
        
        # Send through all channels for alarms
        self._manager.notify(event)
    
    def notify_alarm_acknowledged(self, **kwargs) -> None:
        """Notify that alarm was acknowledged."""
        event = DetectionEvent(
            timestamp=datetime.now(),
            event_type="alarm_acknowledged",
            confidence=1.0,
            metadata=kwargs
        )
        self._manager.notify(event, [NotificationChannel.WEBSOCKET, NotificationChannel.FILE_LOG])
