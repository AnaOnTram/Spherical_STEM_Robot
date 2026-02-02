"""Cross-platform audio recording with macOS/Linux support."""
import logging
import queue
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from config import (
    AUDIO_RECORD_DEVICE,
    AUDIO_SAMPLE_RATE,
    AUDIO_CHANNELS,
    AUDIO_CHUNK_SIZE,
)

logger = logging.getLogger(__name__)


class AudioBackend(ABC):
    """Abstract base class for audio recording backends."""
    
    @abstractmethod
    def start(self) -> None:
        """Start recording."""
        pass
    
    @abstractmethod
    def stop(self) -> None:
        """Stop recording."""
        pass
    
    @abstractmethod
    def read(self) -> tuple[int, bytes]:
        """Read audio data.
        
        Returns:
            Tuple of (length, data)
        """
        pass
    
    @property
    @abstractmethod
    def is_recording(self) -> bool:
        """Check if currently recording."""
        pass


class ALSABackend(AudioBackend):
    """ALSA backend for Linux."""
    
    def __init__(self, device: str, sample_rate: int, channels: int, chunk_size: int):
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self._pcm = None
        
        try:
            import alsaaudio
            self._alsaaudio = alsaaudio
        except ImportError:
            raise RuntimeError("pyalsaaudio not installed")
    
    def start(self) -> None:
        """Start ALSA recording."""
        self._pcm = self._alsaaudio.PCM(
            type=self._alsaaudio.PCM_CAPTURE,
            mode=self._alsaaudio.PCM_NORMAL,
            device=self.device,
        )
        self._pcm.setchannels(self.channels)
        self._pcm.setrate(self.sample_rate)
        self._pcm.setformat(self._alsaaudio.PCM_FORMAT_S16_LE)
        self._pcm.setperiodsize(self.chunk_size)
        logger.info(f"ALSA recording started on {self.device}")
    
    def stop(self) -> None:
        """Stop ALSA recording."""
        if self._pcm:
            self._pcm.close()
            self._pcm = None
            logger.info("ALSA recording stopped")
    
    def read(self) -> tuple[int, bytes]:
        """Read audio data from ALSA."""
        if not self._pcm:
            return 0, b""
        try:
            return self._pcm.read()
        except Exception as e:
            logger.error(f"ALSA read error: {e}")
            return 0, b""
    
    @property
    def is_recording(self) -> bool:
        """Check if recording."""
        return self._pcm is not None


class SoundDeviceBackend(AudioBackend):
    """SoundDevice backend for macOS and other platforms."""
    
    def __init__(self, device: str, sample_rate: int, channels: int, chunk_size: int):
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self._stream = None
        self._buffer = queue.Queue(maxsize=100)
        self._recording = False
        self._thread: Optional[threading.Thread] = None
        
        try:
            import sounddevice as sd
            self._sd = sd
        except ImportError:
            raise RuntimeError("sounddevice not installed. Install with: pip install sounddevice")
    
    def _audio_callback(self, indata, frames, time_info, status):
        """Callback for sounddevice stream."""
        if status:
            logger.warning(f"Audio callback status: {status}")
        
        # Convert float32 to int16
        audio_int16 = (indata * 32767).astype(np.int16)
        
        # Flatten if stereo
        if self.channels > 1:
            audio_int16 = audio_int16.reshape(-1)
        else:
            audio_int16 = audio_int16.flatten()
        
        try:
            self._buffer.put_nowait(audio_int16.tobytes())
        except queue.Full:
            # Drop oldest frame
            try:
                self._buffer.get_nowait()
                self._buffer.put_nowait(audio_int16.tobytes())
            except queue.Empty:
                pass
    
    def start(self) -> None:
        """Start sounddevice recording."""
        self._recording = True
        
        # Find device if "auto" specified
        device_id = None
        if self.device == "auto":
            try:
                devices = self._sd.query_devices()
                for i, dev in enumerate(devices):
                    if dev['max_input_channels'] > 0:
                        device_id = i
                        logger.info(f"Auto-selected input device: {dev['name']}")
                        break
            except Exception as e:
                logger.warning(f"Could not auto-detect device: {e}")
        else:
            # Try to parse device string (e.g., "0" or device name)
            try:
                device_id = int(self.device)
            except ValueError:
                # Try to find by name
                try:
                    devices = self._sd.query_devices()
                    for i, dev in enumerate(devices):
                        if self.device.lower() in dev['name'].lower():
                            device_id = i
                            break
                except Exception:
                    pass
        
        self._stream = self._sd.InputStream(
            device=device_id,
            channels=self.channels,
            samplerate=self.sample_rate,
            blocksize=self.chunk_size,
            dtype=np.float32,
            callback=self._audio_callback,
        )
        self._stream.start()
        logger.info(f"SoundDevice recording started (device={device_id})")
    
    def stop(self) -> None:
        """Stop sounddevice recording."""
        self._recording = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        # Clear buffer
        while not self._buffer.empty():
            try:
                self._buffer.get_nowait()
            except queue.Empty:
                break
        logger.info("SoundDevice recording stopped")
    
    def read(self) -> tuple[int, bytes]:
        """Read audio data from buffer."""
        try:
            data = self._buffer.get(timeout=0.1)
            return len(data) // 2, data  # 2 bytes per int16 sample
        except queue.Empty:
            return 0, b""
    
    @property
    def is_recording(self) -> bool:
        """Check if recording."""
        return self._stream is not None and self._stream.active


class CrossPlatformRecorder:
    """Cross-platform audio recorder that selects appropriate backend."""
    
    def __init__(
        self,
        device: str = AUDIO_RECORD_DEVICE,
        sample_rate: int = AUDIO_SAMPLE_RATE,
        channels: int = AUDIO_CHANNELS,
        chunk_size: int = AUDIO_CHUNK_SIZE,
        noise_reduction: bool = True,
    ):
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self._noise_reduction = noise_reduction
        
        self._backend: Optional[AudioBackend] = None
        self._audio_queue: queue.Queue = queue.Queue(maxsize=100)
        self._callbacks: list[Callable[[np.ndarray], None]] = []
        self._recording = False
        self._thread: Optional[threading.Thread] = None
        
        # Try to select best backend
        self._select_backend()
    
    def _select_backend(self) -> None:
        """Select the best available backend."""
        # Try ALSA first (Linux/Pi)
        try:
            self._backend = ALSABackend(
                self.device, self.sample_rate, self.channels, self.chunk_size
            )
            logger.info("Using ALSA backend")
            return
        except (RuntimeError, ImportError) as e:
            logger.debug(f"ALSA not available: {e}")
        
        # Fallback to sounddevice (macOS, Windows, Linux)
        try:
            self._backend = SoundDeviceBackend(
                self.device, self.sample_rate, self.channels, self.chunk_size
            )
            logger.info("Using SoundDevice backend")
            return
        except (RuntimeError, ImportError) as e:
            logger.debug(f"SoundDevice not available: {e}")
        
        raise RuntimeError(
            "No audio backend available. "
            "Install pyalsaaudio for Linux or sounddevice for macOS/Windows."
        )
    
    def start(self) -> None:
        """Start recording."""
        if self._recording:
            return
        
        if not self._backend:
            raise RuntimeError("No audio backend available")
        
        self._backend.start()
        self._recording = True
        self._thread = threading.Thread(target=self._record_loop, daemon=True)
        self._thread.start()
        
        logger.info(
            f"Recording started: {self.sample_rate}Hz, {self.channels}ch, "
            f"backend={type(self._backend).__name__}"
        )
    
    def stop(self) -> None:
        """Stop recording."""
        self._recording = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._backend:
            self._backend.stop()
        logger.info("Recording stopped")
    
    def _record_loop(self) -> None:
        """Recording thread loop."""
        while self._recording:
            try:
                length, data = self._backend.read()
                if length <= 0:
                    time.sleep(0.001)
                    continue
                
                # Convert to numpy array
                audio_data = np.frombuffer(data, dtype=np.int16)
                
                # Handle stereo to mono conversion if needed
                if self.channels == 2 and len(audio_data) % 2 == 0:
                    audio_data = audio_data.reshape(-1, 2).mean(axis=1).astype(np.int16)
                
                # Queue audio
                try:
                    self._audio_queue.put_nowait(audio_data)
                except queue.Full:
                    try:
                        self._audio_queue.get_nowait()
                        self._audio_queue.put_nowait(audio_data)
                    except queue.Empty:
                        pass
                
                # Notify callbacks
                for callback in self._callbacks:
                    try:
                        callback(audio_data)
                    except Exception as e:
                        logger.error(f"Callback error: {e}")
                
            except Exception as e:
                logger.error(f"Record loop error: {e}")
                time.sleep(0.1)
    
    def get_audio(self, timeout: float = 0.1) -> Optional[np.ndarray]:
        """Get audio chunk from queue."""
        try:
            return self._audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def get_audio_buffer(self, duration: float) -> np.ndarray:
        """Get audio buffer for specified duration."""
        samples_needed = int(duration * self.sample_rate)
        buffer = []
        collected = 0
        
        while collected < samples_needed:
            chunk = self.get_audio(timeout=1.0)
            if chunk is None:
                break
            buffer.append(chunk)
            collected += len(chunk)
        
        if not buffer:
            return np.array([], dtype=np.int16)
        
        return np.concatenate(buffer)[:samples_needed]
    
    def add_callback(self, callback: Callable[[np.ndarray], None]) -> None:
        """Add callback for audio data."""
        self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable[[np.ndarray], None]) -> None:
        """Remove callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    @property
    def is_recording(self) -> bool:
        """Check if recording."""
        return self._recording and self._backend and self._backend.is_recording


def create_recorder(**kwargs) -> CrossPlatformRecorder:
    """Factory function to create appropriate recorder."""
    return CrossPlatformRecorder(**kwargs)
