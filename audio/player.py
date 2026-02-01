"""Audio playback module using ALSA with support for WAV, MP3, and real-time streaming."""
import io
import logging
import queue
import subprocess
import threading
import wave
from pathlib import Path
from typing import Optional, Callable

import numpy as np

try:
    import alsaaudio
except ImportError:
    alsaaudio = None

from config import AUDIO_PLAYBACK_DEVICE, AUDIO_SAMPLE_RATE, AUDIO_CHANNELS

logger = logging.getLogger(__name__)


class AudioPlayer:
    """Audio player using ALSA for USB speaker with support for multiple formats."""

    def __init__(
        self,
        device: str = AUDIO_PLAYBACK_DEVICE,
        sample_rate: int = AUDIO_SAMPLE_RATE,
        channels: int = AUDIO_CHANNELS,
    ):
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels

        self._pcm: Optional["alsaaudio.PCM"] = None
        self._playing = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Playback queue for streaming audio
        self._playback_queue: queue.Queue = queue.Queue(maxsize=100)
        self._playback_callbacks: list[Callable[[np.ndarray], None]] = []

    def _check_alsa(self) -> None:
        """Check if ALSA is available."""
        if alsaaudio is None:
            raise RuntimeError(
                "pyalsaaudio not installed. Install with: pip install pyalsaaudio"
            )

    def _init_pcm(self, sample_rate: int, channels: int) -> None:
        """Initialize PCM device for playback."""
        self._check_alsa()
        if self._pcm:
            self._pcm.close()

        self._pcm = alsaaudio.PCM(
            type=alsaaudio.PCM_PLAYBACK,
            mode=alsaaudio.PCM_NORMAL,
            device=self.device,
        )
        self._pcm.setchannels(channels)
        self._pcm.setrate(sample_rate)
        self._pcm.setformat(alsaaudio.PCM_FORMAT_S16_LE)
        self._pcm.setperiodsize(1024)

    def play_file(self, filepath: str, blocking: bool = False) -> None:
        """Play audio from WAV or MP3 file.

        Args:
            filepath: Path to audio file (WAV or MP3)
            blocking: If True, block until playback completes
        """
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {filepath}")

        if blocking:
            self._play_file_sync(filepath)
        else:
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._play_file_sync, args=(filepath,), daemon=True
            )
            self._thread.start()

    def _play_file_sync(self, filepath: str) -> None:
        """Play audio file synchronously with format detection."""
        path = Path(filepath)
        suffix = path.suffix.lower()
        
        try:
            if suffix == '.mp3':
                self._play_mp3_sync(filepath)
            elif suffix == '.wav':
                # Try WAV first, if it fails (bad format), use ffmpeg
                try:
                    self._play_wav_sync(filepath)
                except Exception as wav_err:
                    logger.warning(f"WAV playback failed, trying ffmpeg: {wav_err}")
                    self._play_with_ffmpeg_sync(filepath)
            elif suffix in ['.webm', '.ogg', '.m4a', '.aac', '.flac']:
                # For WebM and other formats, always use ffmpeg
                logger.info(f"Playing {suffix} file with ffmpeg: {filepath}")
                self._play_with_ffmpeg_sync(filepath)
            else:
                # For unknown formats, always use ffmpeg
                logger.info(f"Unknown format, trying ffmpeg: {filepath}")
                self._play_with_ffmpeg_sync(filepath)
        except Exception as e:
            logger.error(f"Playback error for {filepath}: {e}")
            self._playing = False

    def _play_wav_sync(self, filepath: str) -> None:
        """Play WAV file synchronously."""
        with wave.open(filepath, "rb") as wf:
            channels = wf.getnchannels()
            sample_rate = wf.getframerate()
            self._init_pcm(sample_rate, channels)

            self._playing = True
            logger.info(f"Playing WAV: {filepath}")

            chunk_size = 1024
            data = wf.readframes(chunk_size)

            while data and not self._stop_event.is_set():
                self._pcm.write(data)
                # Notify callbacks for echo cancellation
                chunk_array = np.frombuffer(data, dtype=np.int16)
                self._notify_playback(chunk_array)
                data = wf.readframes(chunk_size)

            self._playing = False
            logger.info(f"Finished playing: {filepath}")

    def _play_mp3_sync(self, filepath: str) -> None:
        """Play MP3 file by converting to WAV on the fly."""
        logger.info(f"Converting and playing MP3: {filepath}")
        
        try:
            # Use ffmpeg to convert MP3 to raw PCM
            cmd = [
                'ffmpeg',
                '-i', filepath,
                '-f', 's16le',
                '-acodec', 'pcm_s16le',
                '-ar', str(self.sample_rate),
                '-ac', '1',
                '-'
            ]
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=10*1024*1024
            )
            
            self._init_pcm(self.sample_rate, 1)
            self._playing = True
            
            chunk_size = 2048
            while not self._stop_event.is_set():
                data = process.stdout.read(chunk_size)
                if not data:
                    break
                self._pcm.write(data)
                # Notify callbacks for echo cancellation
                chunk_array = np.frombuffer(data, dtype=np.int16)
                self._notify_playback(chunk_array)
            
            process.terminate()
            self._playing = False
            logger.info(f"Finished playing MP3: {filepath}")
            
        except FileNotFoundError:
            logger.error("ffmpeg not found. Install with: sudo apt install ffmpeg")
            self._playing = False
        except Exception as e:
            logger.error(f"MP3 playback error: {e}")
            self._playing = False

    def _play_with_ffmpeg_sync(self, filepath: str) -> None:
        """Play any audio format using ffmpeg."""
        logger.info(f"Playing with ffmpeg: {filepath}")
        
        try:
            cmd = [
                'ffmpeg',
                '-i', filepath,
                '-f', 's16le',
                '-acodec', 'pcm_s16le',
                '-ar', str(self.sample_rate),
                '-ac', '1',
                '-'
            ]
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=10*1024*1024
            )
            
            self._init_pcm(self.sample_rate, 1)
            self._playing = True
            
            chunk_size = 2048
            while not self._stop_event.is_set():
                data = process.stdout.read(chunk_size)
                if not data:
                    break
                self._pcm.write(data)
                # Notify callbacks for echo cancellation
                chunk_array = np.frombuffer(data, dtype=np.int16)
                self._notify_playback(chunk_array)
            
            process.terminate()
            self._playing = False
            logger.info(f"Finished playing: {filepath}")
            
        except FileNotFoundError:
            logger.error("ffmpeg not found. Install with: sudo apt install ffmpeg")
            self._playing = False
        except Exception as e:
            logger.error(f"FFmpeg playback error: {e}")
            self._playing = False

    def play_audio_data(self, audio_data: bytes, sample_rate: int = 48000, channels: int = 1) -> None:
        """Play raw audio data (PCM bytes).
        
        Args:
            audio_data: Raw PCM audio data (int16)
            sample_rate: Sample rate of the audio
            channels: Number of channels
        """
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._play_audio_data_sync,
            args=(audio_data, sample_rate, channels),
            daemon=True
        )
        self._thread.start()

    def _play_audio_data_sync(self, audio_data: bytes, sample_rate: int, channels: int) -> None:
        """Play raw audio data synchronously."""
        try:
            self._init_pcm(sample_rate, channels)
            self._playing = True
            logger.info(f"Playing audio data: {len(audio_data)} bytes")

            # Write in chunks
            chunk_size = 2048
            for i in range(0, len(audio_data), chunk_size):
                if self._stop_event.is_set():
                    break
                chunk = audio_data[i:i + chunk_size]
                self._pcm.write(chunk)
                # Notify callbacks for echo cancellation
                chunk_array = np.frombuffer(chunk, dtype=np.int16)
                self._notify_playback(chunk_array)

            self._playing = False
            logger.info("Finished playing audio data")

        except Exception as e:
            logger.error(f"Audio data playback error: {e}")
            self._playing = False

    def play_data(
        self,
        audio_data: np.ndarray,
        sample_rate: Optional[int] = None,
        blocking: bool = False,
    ) -> None:
        """Play audio from numpy array.

        Args:
            audio_data: Audio samples as numpy array (int16)
            sample_rate: Sample rate (uses default if None)
            blocking: If True, block until playback completes
        """
        sample_rate = sample_rate or self.sample_rate

        if blocking:
            self._play_data_sync(audio_data, sample_rate)
        else:
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._play_data_sync,
                args=(audio_data, sample_rate),
                daemon=True,
            )
            self._thread.start()

    def _play_data_sync(self, audio_data: np.ndarray, sample_rate: int) -> None:
        """Play audio data synchronously."""
        try:
            self._init_pcm(sample_rate, self.channels)
            self._playing = True

            # Convert to bytes
            data = audio_data.astype(np.int16).tobytes()

            # Write in chunks
            chunk_size = 2048
            for i in range(0, len(data), chunk_size):
                if self._stop_event.is_set():
                    break
                chunk = data[i : i + chunk_size]
                self._pcm.write(chunk)
                
                # Feed playback data to recorder for echo cancellation
                chunk_array = np.frombuffer(chunk, dtype=np.int16)
                self._notify_playback(chunk_array)

            self._playing = False

        except Exception as e:
            logger.error(f"Playback error: {e}")
            self._playing = False

    def _notify_playback(self, audio_chunk: np.ndarray) -> None:
        """Notify recorder of playback for echo cancellation."""
        try:
            from audio import get_playback_buffer
            playback_buffer = get_playback_buffer()
            if playback_buffer is not None:
                try:
                    playback_buffer.put_nowait(audio_chunk.copy())
                except queue.Full:
                    # Drop oldest reference
                    try:
                        playback_buffer.get_nowait()
                        playback_buffer.put_nowait(audio_chunk.copy())
                    except queue.Empty:
                        pass
        except Exception:
            pass

    def stop(self) -> None:
        """Stop playback."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._pcm:
            self._pcm.close()
            self._pcm = None
        self._playing = False
        logger.info("Stopped playback")

    def play_tone(
        self,
        frequency: float = 440.0,
        duration: float = 1.0,
        volume: float = 0.5,
    ) -> None:
        """Play a simple sine wave tone.

        Args:
            frequency: Tone frequency in Hz
            duration: Duration in seconds
            volume: Volume (0.0 to 1.0)
        """
        t = np.linspace(0, duration, int(self.sample_rate * duration), dtype=np.float32)
        tone = np.sin(2 * np.pi * frequency * t) * volume * 32767
        self.play_data(tone.astype(np.int16))

    @property
    def is_playing(self) -> bool:
        """Check if currently playing."""
        return self._playing
