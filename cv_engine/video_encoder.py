"""Video capture and H.264 encoding for streaming."""
import asyncio
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Iterator, Optional

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

from config import CAMERA_DEVICE, CAMERA_WIDTH, CAMERA_HEIGHT, CAMERA_FPS

logger = logging.getLogger(__name__)


@dataclass
class VideoStream:
    """Video stream configuration."""
    device: str = CAMERA_DEVICE
    width: int = CAMERA_WIDTH
    height: int = CAMERA_HEIGHT
    fps: int = CAMERA_FPS
    codec: str = "h264"


class VideoEncoder:
    """Video capture and encoding for streaming."""

    def __init__(self, config: Optional[VideoStream] = None):
        self.config = config or VideoStream()

        self._capture: Optional["cv2.VideoCapture"] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._frame_queue: queue.Queue = queue.Queue(maxsize=2)  # Small queue for low latency
        self._callbacks: list[Callable[[np.ndarray], None]] = []

        # Encoding
        self._encoder = None
        self._encoded_queue: queue.Queue = queue.Queue(maxsize=5)

    def _check_cv2(self) -> None:
        """Check if OpenCV is available."""
        if cv2 is None:
            raise RuntimeError("OpenCV not installed. Install with: pip install opencv-python")

    def start(self) -> bool:
        """Start video capture.

        Returns:
            True if started successfully
        """
        self._check_cv2()

        if self._running:
            return True

        try:
            # Try V4L2 backend first (better for Linux)
            self._capture = cv2.VideoCapture(self.config.device, cv2.CAP_V4L2)

            if not self._capture.isOpened():
                # Fall back to auto backend
                self._capture = cv2.VideoCapture(self.config.device)

            if not self._capture.isOpened():
                logger.error(f"Failed to open camera: {self.config.device}")
                return False

            # Set capture properties
            self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
            self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
            self._capture.set(cv2.CAP_PROP_FPS, self.config.fps)

            # Get actual properties
            actual_width = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = int(self._capture.get(cv2.CAP_PROP_FPS))

            logger.info(
                f"Camera opened: {actual_width}x{actual_height} @ {actual_fps}fps"
            )

            self._running = True
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()

            return True

        except Exception as e:
            logger.error(f"Failed to start video capture: {e}")
            return False

    def stop(self) -> None:
        """Stop video capture."""
        self._running = False

        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

        if self._capture:
            self._capture.release()
            self._capture = None

        logger.info("Video capture stopped")

    def _capture_loop(self) -> None:
        """Capture thread loop - optimized for low latency."""
        while self._running:
            try:
                ret, frame = self._capture.read()
                if not ret:
                    logger.warning("Failed to read frame")
                    time.sleep(0.001)
                    continue

                # Add to queue (drop old frames if full for low latency)
                try:
                    self._frame_queue.put_nowait(frame)
                except queue.Full:
                    # Drop oldest frame to minimize latency
                    try:
                        self._frame_queue.get_nowait()
                        self._frame_queue.put_nowait(frame)
                    except queue.Empty:
                        pass

                # Notify callbacks
                for callback in self._callbacks:
                    try:
                        callback(frame)
                    except Exception as e:
                        logger.error(f"Frame callback error: {e}")

                # Small yield to prevent CPU spinning
                time.sleep(0.001)

            except Exception as e:
                logger.error(f"Capture loop error: {e}")
                time.sleep(0.01)

    def get_frame(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        """Get latest frame from queue.

        Args:
            timeout: Timeout in seconds

        Returns:
            BGR frame or None if timeout
        """
        try:
            return self._frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def read(self) -> tuple[bool, Optional[np.ndarray]]:
        """Read frame directly from camera (blocking).

        Returns:
            Tuple of (success, frame)
        """
        if not self._capture or not self._capture.isOpened():
            return False, None

        return self._capture.read()

    def encode_frame(self, frame: np.ndarray, quality: int = 80) -> bytes:
        """Encode frame to JPEG.

        Args:
            frame: BGR frame
            quality: JPEG quality (1-100)

        Returns:
            JPEG encoded bytes
        """
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
        _, encoded = cv2.imencode(".jpg", frame, encode_params)
        return encoded.tobytes()

    def iter_frames(self) -> Iterator[np.ndarray]:
        """Iterate over frames.

        Yields:
            BGR frames
        """
        while self._running:
            frame = self.get_frame(timeout=1.0)
            if frame is not None:
                yield frame

    async def iter_frames_async(self) -> AsyncIterator[np.ndarray]:
        """Iterate over frames asynchronously.

        Yields:
            BGR frames
        """
        loop = asyncio.get_event_loop()
        while self._running:
            frame = await loop.run_in_executor(None, self.get_frame, 1.0)
            if frame is not None:
                yield frame

    def iter_jpeg(self, quality: int = 80) -> Iterator[bytes]:
        """Iterate over JPEG encoded frames.

        Args:
            quality: JPEG quality

        Yields:
            JPEG bytes
        """
        for frame in self.iter_frames():
            yield self.encode_frame(frame, quality)

    async def iter_jpeg_async(self, quality: int = 80) -> AsyncIterator[bytes]:
        """Iterate over JPEG encoded frames asynchronously.

        Args:
            quality: JPEG quality

        Yields:
            JPEG bytes
        """
        async for frame in self.iter_frames_async():
            yield self.encode_frame(frame, quality)

    def generate_mjpeg_stream(self, quality: int = 80) -> Iterator[bytes]:
        """Generate MJPEG stream (for HTTP streaming).

        Args:
            quality: JPEG quality

        Yields:
            MJPEG frame with boundary
        """
        boundary = b"--frame\r\n"
        header = b"Content-Type: image/jpeg\r\n\r\n"

        for jpeg in self.iter_jpeg(quality):
            yield boundary + header + jpeg + b"\r\n"

    def add_callback(self, callback: Callable[[np.ndarray], None]) -> None:
        """Add callback for new frames."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[np.ndarray], None]) -> None:
        """Remove callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    @property
    def is_running(self) -> bool:
        """Check if capture is running."""
        return self._running

    @property
    def frame_size(self) -> tuple[int, int]:
        """Get frame size (width, height)."""
        if self._capture:
            return (
                int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            )
        return (self.config.width, self.config.height)
