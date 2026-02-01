"""Audio processing module."""
import queue
from typing import Optional

from .recorder import AudioRecorder
from .player import AudioPlayer
from .yamnet_classifier import YAMNetClassifier, SoundEvent
from .alarm_manager import AlarmManager

# Global playback reference buffer for echo cancellation
# This is shared between AudioPlayer and AudioRecorder
_playback_reference_buffer: Optional[queue.Queue] = None


def get_playback_buffer() -> Optional[queue.Queue]:
    """Get the global playback reference buffer."""
    global _playback_reference_buffer
    if _playback_reference_buffer is None:
        _playback_reference_buffer = queue.Queue(maxsize=100)
    return _playback_reference_buffer


def clear_playback_buffer() -> None:
    """Clear the playback reference buffer."""
    global _playback_reference_buffer
    if _playback_reference_buffer is not None:
        while not _playback_reference_buffer.empty():
            try:
                _playback_reference_buffer.get_nowait()
            except queue.Empty:
                break


__all__ = [
    "AudioRecorder",
    "AudioPlayer",
    "YAMNetClassifier",
    "SoundEvent",
    "AlarmManager",
    "get_playback_buffer",
    "clear_playback_buffer",
]
