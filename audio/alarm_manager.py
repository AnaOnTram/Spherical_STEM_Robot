"""Alarm manager for crying detection and notification."""
import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from config import CRYING_DETECTION_DURATION
from .recorder import AudioRecorder
from .player import AudioPlayer
from .yamnet_classifier import YAMNetClassifier, SoundEvent, SoundCategory

logger = logging.getLogger(__name__)


class AlarmState(Enum):
    """Alarm state machine states."""
    IDLE = "idle"
    DETECTING = "detecting"
    CONFIRMED = "confirmed"
    ALARMING = "alarming"
    COOLDOWN = "cooldown"


@dataclass
class AlarmEvent:
    """Alarm event data."""
    timestamp: datetime
    state: AlarmState
    duration: float = 0.0
    audio_file: Optional[str] = None
    confidence: float = 0.0


@dataclass
class AlarmConfig:
    """Alarm configuration."""
    detection_duration: float = CRYING_DETECTION_DURATION
    cooldown_duration: float = 30.0  # seconds between alarms
    alarm_sound_path: Optional[str] = None
    recording_duration: float = 10.0  # seconds to record on alarm
    recordings_dir: str = "/tmp/spherical_bot/recordings"


class AlarmManager:
    """Manages crying detection and alarm triggering."""

    def __init__(
        self,
        recorder: AudioRecorder,
        player: AudioPlayer,
        classifier: YAMNetClassifier,
        config: Optional[AlarmConfig] = None,
    ):
        self.recorder = recorder
        self.player = player
        self.classifier = classifier
        self.config = config or AlarmConfig()

        self._state = AlarmState.IDLE
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._callbacks: list[Callable[[AlarmEvent], None]] = []

        # Detection tracking
        self._crying_start: Optional[datetime] = None
        self._last_alarm: Optional[datetime] = None
        self._detection_count = 0

        # Ensure recordings directory exists
        Path(self.config.recordings_dir).mkdir(parents=True, exist_ok=True)

    @property
    def state(self) -> AlarmState:
        """Get current alarm state."""
        return self._state

    def start(self) -> None:
        """Start alarm monitoring."""
        if self._running:
            return

        if not self.classifier.is_loaded:
            if not self.classifier.load_model():
                logger.error("Failed to load classifier, cannot start alarm manager")
                return

        self._running = True
        self._state = AlarmState.IDLE
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("Alarm manager started")

    def stop(self) -> None:
        """Stop alarm monitoring."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._state = AlarmState.IDLE
        logger.info("Alarm manager stopped")

    def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                # Get audio buffer (1 second)
                audio = self.recorder.get_audio_buffer(1.0)
                if len(audio) == 0:
                    time.sleep(0.1)
                    continue

                # Classify audio
                event = self.classifier.classify(audio)
                self._process_sound_event(event)

                # Small delay to prevent CPU overload
                time.sleep(0.1)

            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
                time.sleep(1.0)

    def _process_sound_event(self, event: SoundEvent) -> None:
        """Process classified sound event through state machine."""
        is_crying = event.category == SoundCategory.CRYING

        if self._state == AlarmState.IDLE:
            if is_crying:
                self._crying_start = datetime.now()
                self._state = AlarmState.DETECTING
                self._detection_count = 1
                logger.info(f"Crying detected, confidence: {event.confidence:.2f}")

        elif self._state == AlarmState.DETECTING:
            if is_crying:
                self._detection_count += 1
                elapsed = (datetime.now() - self._crying_start).total_seconds()

                if elapsed >= self.config.detection_duration:
                    self._state = AlarmState.CONFIRMED
                    logger.info(
                        f"Crying confirmed after {elapsed:.1f}s, "
                        f"triggering alarm"
                    )
                    self._trigger_alarm(event.confidence)
            else:
                # Reset if crying stops
                self._state = AlarmState.IDLE
                self._crying_start = None
                self._detection_count = 0
                logger.debug("Crying stopped, resetting detection")

        elif self._state == AlarmState.COOLDOWN:
            if self._last_alarm:
                elapsed = (datetime.now() - self._last_alarm).total_seconds()
                if elapsed >= self.config.cooldown_duration:
                    self._state = AlarmState.IDLE
                    logger.info("Cooldown complete, resuming monitoring")

    def _trigger_alarm(self, confidence: float) -> None:
        """Trigger alarm and notify callbacks."""
        self._state = AlarmState.ALARMING

        # Record audio clip
        audio_file = None
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            audio_file = f"{self.config.recordings_dir}/crying_{timestamp}.wav"
            self.recorder.record_to_file(audio_file, self.config.recording_duration)
        except Exception as e:
            logger.error(f"Failed to record audio: {e}")

        # Play alarm sound if configured
        if self.config.alarm_sound_path and Path(self.config.alarm_sound_path).exists():
            try:
                self.player.play_file(self.config.alarm_sound_path)
            except Exception as e:
                logger.error(f"Failed to play alarm: {e}")
        else:
            # Play default tone
            try:
                self.player.play_tone(frequency=800, duration=2.0)
            except Exception as e:
                logger.error(f"Failed to play tone: {e}")

        # Create alarm event
        elapsed = (datetime.now() - self._crying_start).total_seconds()
        alarm_event = AlarmEvent(
            timestamp=datetime.now(),
            state=AlarmState.ALARMING,
            duration=elapsed,
            audio_file=audio_file,
            confidence=confidence,
        )

        # Notify callbacks
        for callback in self._callbacks:
            try:
                callback(alarm_event)
            except Exception as e:
                logger.error(f"Callback error: {e}")

        # Enter cooldown
        self._last_alarm = datetime.now()
        self._state = AlarmState.COOLDOWN
        logger.info("Alarm triggered, entering cooldown")

    def add_callback(self, callback: Callable[[AlarmEvent], None]) -> None:
        """Add callback for alarm events."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[AlarmEvent], None]) -> None:
        """Remove callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def acknowledge(self) -> None:
        """Acknowledge alarm (skip cooldown)."""
        if self._state == AlarmState.COOLDOWN:
            self._state = AlarmState.IDLE
            logger.info("Alarm acknowledged, resuming monitoring")

    def test_alarm(self) -> None:
        """Trigger test alarm."""
        logger.info("Triggering test alarm")
        self._crying_start = datetime.now()
        self._trigger_alarm(confidence=1.0)

    @property
    def is_running(self) -> bool:
        """Check if monitoring is active."""
        return self._running
