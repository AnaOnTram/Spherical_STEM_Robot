"""Audio recording module with dual-mic noise cancellation using ALSA."""
import logging
import queue
import struct
import threading
import wave
from pathlib import Path
from typing import Callable, Optional

import numpy as np

try:
    import alsaaudio
except ImportError:
    alsaaudio = None

try:
    from scipy import signal as scipy_signal
    from scipy.ndimage import uniform_filter1d
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

from config import (
    AUDIO_RECORD_DEVICE,
    AUDIO_SAMPLE_RATE,
    AUDIO_CHANNELS,
    AUDIO_CHUNK_SIZE,
)

# Try to import optional config
try:
    from config import (
        AUDIO_RECORD_DEVICE_2,
        AUDIO_NOISE_REDUCTION,
        AUDIO_DUAL_MIC_ENABLED,
        AUDIO_OUTPUT_CHANNELS,
    )
except ImportError:
    AUDIO_RECORD_DEVICE_2 = None
    AUDIO_NOISE_REDUCTION = True
    AUDIO_DUAL_MIC_ENABLED = False
    AUDIO_OUTPUT_CHANNELS = 1

logger = logging.getLogger(__name__)


class NoiseReducer:
    """Real-time noise reduction using spectral subtraction and adaptive filtering."""

    def __init__(self, sample_rate: int = 48000, frame_size: int = 512):
        self.sample_rate = sample_rate
        self.frame_size = frame_size

        # Spectral subtraction parameters
        self.noise_floor = None
        self.noise_floor_alpha = 0.98  # Smoothing factor for noise floor update
        self.subtraction_factor = 2.0  # How aggressively to subtract noise
        self.spectral_floor = 0.01     # Minimum spectral value to prevent musical noise

        # Adaptive filter for dual-mic (LMS algorithm)
        self.filter_order = 128
        self.filter_weights = np.zeros(self.filter_order)
        self.mu = 0.01  # Learning rate for LMS

        # Buffer for overlap-add processing
        self.overlap = frame_size // 2
        self.prev_frame = np.zeros(self.overlap)

        # Voice activity detection
        self.vad_threshold = 0.02
        self.noise_estimate_frames = 0
        self.noise_estimate_max_frames = 50  # ~0.5s at 48kHz/512

    def estimate_noise_floor(self, spectrum: np.ndarray) -> None:
        """Update noise floor estimate during silence."""
        if self.noise_floor is None:
            self.noise_floor = spectrum.copy()
        else:
            # Exponential moving average
            self.noise_floor = (
                self.noise_floor_alpha * self.noise_floor +
                (1 - self.noise_floor_alpha) * spectrum
            )

    def spectral_subtraction(self, audio: np.ndarray) -> np.ndarray:
        """Apply spectral subtraction noise reduction."""
        if not SCIPY_AVAILABLE:
            return audio

        # Apply window
        window = np.hanning(len(audio))
        windowed = audio.astype(np.float32) * window

        # FFT
        spectrum = np.fft.rfft(windowed)
        magnitude = np.abs(spectrum)
        phase = np.angle(spectrum)

        # Check for voice activity (simple energy-based VAD)
        energy = np.mean(audio.astype(np.float32) ** 2)
        is_speech = energy > self.vad_threshold

        if not is_speech and self.noise_estimate_frames < self.noise_estimate_max_frames:
            # Update noise floor during silence
            self.estimate_noise_floor(magnitude)
            self.noise_estimate_frames += 1
        elif self.noise_floor is None:
            # No noise estimate yet, pass through
            return audio

        # Spectral subtraction
        if self.noise_floor is not None:
            # Subtract noise floor
            cleaned_magnitude = magnitude - self.subtraction_factor * self.noise_floor
            # Apply spectral floor to prevent musical noise
            cleaned_magnitude = np.maximum(cleaned_magnitude, self.spectral_floor * magnitude)

            # Reconstruct signal
            cleaned_spectrum = cleaned_magnitude * np.exp(1j * phase)
            cleaned = np.fft.irfft(cleaned_spectrum)

            # Remove window effect with overlap-add
            cleaned = cleaned[:len(audio)]

            return cleaned.astype(np.int16)

        return audio

    def adaptive_filter_lms(
        self,
        primary: np.ndarray,
        reference: np.ndarray,
    ) -> np.ndarray:
        """Apply LMS adaptive filter for dual-mic noise cancellation.

        Args:
            primary: Primary microphone signal (desired + noise)
            reference: Reference microphone signal (mostly noise)

        Returns:
            Cleaned audio signal
        """
        output = np.zeros(len(primary), dtype=np.float32)
        primary_f = primary.astype(np.float32) / 32768.0
        reference_f = reference.astype(np.float32) / 32768.0

        # Pad reference for filter
        ref_padded = np.concatenate([
            np.zeros(self.filter_order - 1),
            reference_f
        ])

        for i in range(len(primary)):
            # Get reference samples for filter
            ref_segment = ref_padded[i:i + self.filter_order][::-1]

            # Filter output (estimated noise)
            noise_estimate = np.dot(self.filter_weights, ref_segment)

            # Error signal (cleaned audio)
            error = primary_f[i] - noise_estimate
            output[i] = error

            # Update filter weights (LMS)
            self.filter_weights += 2 * self.mu * error * ref_segment

        # Convert back to int16
        output = np.clip(output * 32768, -32768, 32767).astype(np.int16)
        return output

    def process(
        self,
        audio: np.ndarray,
        reference: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """Process audio with noise reduction.

        Args:
            audio: Primary audio signal (int16)
            reference: Optional reference mic signal for dual-mic cancellation

        Returns:
            Processed audio signal (int16)
        """
        if reference is not None and len(reference) == len(audio):
            # Dual-mic adaptive noise cancellation
            audio = self.adaptive_filter_lms(audio, reference)

        # Apply spectral subtraction for remaining noise
        audio = self.spectral_subtraction(audio)

        return audio


class AudioRecorder:
    """Audio recorder with stereo input support, noise cancellation, and echo cancellation using ALSA."""

    def __init__(
        self,
        device: str = AUDIO_RECORD_DEVICE,
        device_2: Optional[str] = AUDIO_RECORD_DEVICE_2,
        sample_rate: int = AUDIO_SAMPLE_RATE,
        channels: int = AUDIO_CHANNELS,
        chunk_size: int = AUDIO_CHUNK_SIZE,
        noise_reduction: bool = AUDIO_NOISE_REDUCTION,
        dual_mic: bool = AUDIO_DUAL_MIC_ENABLED,
        echo_cancellation: bool = True,
    ):
        self.device = device
        self.device_2 = device_2
        self.sample_rate = sample_rate
        self.channels = channels  # Input channels (2 for stereo USB mic)
        self.chunk_size = chunk_size
        self._noise_reduction = noise_reduction
        self._dual_mic = dual_mic and device_2 is not None
        self._echo_cancellation = echo_cancellation

        self._pcm: Optional["alsaaudio.PCM"] = None
        self._pcm_2: Optional["alsaaudio.PCM"] = None
        self._recording = False
        self._thread: Optional[threading.Thread] = None
        self._audio_queue: queue.Queue = queue.Queue(maxsize=100)
        self._callbacks: list[Callable[[np.ndarray], None]] = []

        # Noise reduction
        self._noise_reducer: Optional[NoiseReducer] = None
        if noise_reduction:
            self._noise_reducer = NoiseReducer(sample_rate, chunk_size)
        
        # Echo cancellation state - uses global playback buffer from audio module
        self._echo_filter_length = int(0.1 * sample_rate)  # 100ms echo tail
        self._echo_filter = np.zeros(self._echo_filter_length)
        self._echo_mu = 0.01  # LMS learning rate
        self._echo_reference = np.zeros(self._echo_filter_length)

    def _check_alsa(self) -> None:
        """Check if ALSA is available."""
        if alsaaudio is None:
            raise RuntimeError(
                "pyalsaaudio not installed. Install with: pip install pyalsaaudio"
            )

    def _init_pcm(self, device: str) -> Optional["alsaaudio.PCM"]:
        """Initialize PCM device."""
        try:
            pcm = alsaaudio.PCM(
                type=alsaaudio.PCM_CAPTURE,
                mode=alsaaudio.PCM_NORMAL,
                device=device,
            )
            pcm.setchannels(self.channels)
            pcm.setrate(self.sample_rate)
            pcm.setformat(alsaaudio.PCM_FORMAT_S16_LE)
            pcm.setperiodsize(self.chunk_size)
            return pcm
        except alsaaudio.ALSAAudioError as e:
            logger.warning(f"Failed to initialize audio device {device}: {e}")
            return None

    def start(self) -> None:
        """Start recording audio."""
        self._check_alsa()
        if self._recording:
            return

        # Initialize primary microphone
        self._pcm = self._init_pcm(self.device)
        if not self._pcm:
            raise RuntimeError(f"Failed to open primary audio device: {self.device}")

        # Initialize secondary microphone for noise cancellation
        if self._dual_mic and self.device_2:
            self._pcm_2 = self._init_pcm(self.device_2)
            if self._pcm_2:
                logger.info(f"Dual-mic noise cancellation enabled: {self.device} + {self.device_2}")
            else:
                logger.warning(f"Secondary mic {self.device_2} not available, using single mic")
                self._dual_mic = False

        self._recording = True
        self._thread = threading.Thread(target=self._record_loop, daemon=True)
        self._thread.start()

        logger.info(
            f"Started recording: {self.sample_rate}Hz, {self.chunk_size} samples/chunk "
            f"(~{self.chunk_size / self.sample_rate * 1000:.1f}ms latency), "
            f"noise_reduction={self._noise_reduction}, dual_mic={self._dual_mic}"
        )

    def stop(self) -> None:
        """Stop recording audio."""
        self._recording = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._pcm:
            self._pcm.close()
            self._pcm = None
        if self._pcm_2:
            self._pcm_2.close()
            self._pcm_2 = None
        logger.info("Stopped recording")

    def _stereo_to_mono(self, stereo_data: np.ndarray) -> np.ndarray:
        """Convert stereo audio to mono by averaging channels.

        For USB camera mic with dual mics, this also provides basic noise
        cancellation since noise is often uncorrelated between mics.
        """
        if len(stereo_data) % 2 != 0:
            # Handle odd length by trimming last sample
            stereo_data = stereo_data[:-1]

        # Reshape to (n_samples, 2) and average
        stereo_2d = stereo_data.reshape(-1, 2)
        return stereo_2d.mean(axis=1).astype(np.int16)

    def _get_playback_reference(self) -> Optional[np.ndarray]:
        """Get playback audio from global buffer for echo cancellation.
        
        Returns:
            Playback audio chunk or None if no reference available
        """
        if not self._echo_cancellation:
            return None
        
        try:
            from audio import get_playback_buffer
            playback_buffer = get_playback_buffer()
            if playback_buffer is not None and not playback_buffer.empty():
                return playback_buffer.get_nowait()
        except Exception:
            pass
        return None

    def _apply_echo_cancellation(self, recorded: np.ndarray) -> np.ndarray:
        """Apply acoustic echo cancellation using LMS adaptive filter.
        
        Args:
            recorded: Recorded audio from microphone
            
        Returns:
            Audio with echo removed
        """
        # Get playback reference (what was being played)
        playback = self._get_playback_reference()
        if playback is None:
            return recorded
        
        # Match lengths
        min_len = min(len(recorded), len(playback))
        recorded = recorded[:min_len]
        playback = playback[:min_len]
        
        # Normalize to float
        recorded_f = recorded.astype(np.float32) / 32768.0
        playback_f = playback.astype(np.float32) / 32768.0
        
        output = np.zeros(min_len, dtype=np.float32)
        
        # LMS adaptive filter for echo cancellation
        for i in range(min_len):
            # Update reference buffer
            self._echo_reference = np.roll(self._echo_reference, 1)
            self._echo_reference[0] = playback_f[i]
            
            # Predict echo
            echo_estimate = np.dot(self._echo_filter, self._echo_reference)
            
            # Error (cleaned signal)
            error = recorded_f[i] - echo_estimate
            output[i] = error
            
            # Update filter weights
            self._echo_filter += self._echo_mu * error * self._echo_reference
        
        # Convert back to int16
        output = np.clip(output * 32768, -32768, 32767).astype(np.int16)
        return output

    def _record_loop(self) -> None:
        """Recording thread loop with stereo-to-mono conversion."""
        while self._recording:
            try:
                # Read from microphone
                length, data = self._pcm.read()
                if length <= 0:
                    continue

                # Convert bytes to numpy array
                audio_data = np.frombuffer(data, dtype=np.int16)

                # Handle stereo input - convert to mono
                if self.channels == 2:
                    # Stereo input: convert to mono
                    audio_data = self._stereo_to_mono(audio_data)

                # Apply echo cancellation
                if self._echo_cancellation:
                    audio_data = self._apply_echo_cancellation(audio_data)

                # Apply noise reduction (on mono signal)
                if self._noise_reduction and self._noise_reducer:
                    audio_data = self._noise_reducer.process(audio_data)

                # Queue processed audio
                try:
                    self._audio_queue.put_nowait(audio_data)
                except queue.Full:
                    # Drop oldest frame to prevent latency buildup
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

            except alsaaudio.ALSAAudioError as e:
                logger.error(f"Recording error: {e}")
                break

    def get_audio(self, timeout: float = 0.1) -> Optional[np.ndarray]:
        """Get recorded audio chunk from queue.

        Args:
            timeout: Timeout in seconds

        Returns:
            Audio data as numpy array or None if timeout
        """
        try:
            return self._audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_audio_buffer(self, duration: float) -> np.ndarray:
        """Get audio buffer for specified duration.

        Args:
            duration: Duration in seconds

        Returns:
            Concatenated audio data
        """
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
        """Add callback for real-time audio processing."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[np.ndarray], None]) -> None:
        """Remove callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def record_to_file(self, filepath: str, duration: float) -> str:
        """Record audio to WAV file.

        Args:
            filepath: Output file path
            duration: Recording duration in seconds

        Returns:
            Path to recorded file
        """
        audio_data = self.get_audio_buffer(duration)
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)  # Always mono output
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_data.tobytes())

        logger.info(f"Recorded {duration}s audio to {filepath}")
        return str(path)

    @property
    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self._recording

    @property
    def noise_reduction_enabled(self) -> bool:
        """Check if noise reduction is enabled."""
        return self._noise_reduction

    @property
    def dual_mic_enabled(self) -> bool:
        """Check if dual-mic mode is enabled and active."""
        return self._dual_mic and self._pcm_2 is not None

    def iter_audio_chunks(self):
        """Iterate over audio chunks for streaming.

        Yields:
            Raw audio bytes (16-bit PCM)
        """
        while self._recording:
            chunk = self.get_audio(timeout=0.5)
            if chunk is not None:
                yield chunk.tobytes()


def generate_wav_stream(recorder: AudioRecorder):
    """Generate WAV audio stream for HTTP streaming.

    Sends complete small WAV chunks that browsers can buffer and play.
    Output is always mono (1 channel) since we convert stereo input to mono.

    Args:
        recorder: AudioRecorder instance

    Yields:
        Complete WAV audio chunks
    """
    sample_rate = recorder.sample_rate
    channels = 1  # Output is always mono
    bits_per_sample = 16
    bytes_per_sample = bits_per_sample // 8

    def make_wav_chunk(audio_bytes: bytes) -> bytes:
        """Create a complete WAV file from audio bytes."""
        data_size = len(audio_bytes)
        file_size = 36 + data_size

        # Build WAV header
        header = b'RIFF'
        header += struct.pack('<I', file_size)
        header += b'WAVE'

        # fmt chunk
        header += b'fmt '
        header += struct.pack('<I', 16)  # Chunk size
        header += struct.pack('<H', 1)   # Audio format (1 = PCM)
        header += struct.pack('<H', channels)
        header += struct.pack('<I', sample_rate)
        header += struct.pack('<I', sample_rate * channels * bytes_per_sample)  # Byte rate
        header += struct.pack('<H', channels * bytes_per_sample)  # Block align
        header += struct.pack('<H', bits_per_sample)

        # data chunk
        header += b'data'
        header += struct.pack('<I', data_size)

        return header + audio_bytes

    # Collect audio and send as complete WAV chunks
    # Each chunk is ~100ms for lower latency
    chunk_duration = 0.1  # seconds
    samples_per_chunk = int(sample_rate * chunk_duration)
    bytes_per_chunk = samples_per_chunk * channels * bytes_per_sample

    buffer = b''

    for audio_chunk in recorder.iter_audio_chunks():
        buffer += audio_chunk

        while len(buffer) >= bytes_per_chunk:
            # Extract chunk and create WAV
            chunk_data = buffer[:bytes_per_chunk]
            buffer = buffer[bytes_per_chunk:]
            yield make_wav_chunk(chunk_data)
