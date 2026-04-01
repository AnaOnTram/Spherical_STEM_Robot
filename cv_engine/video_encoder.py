"""Video capture and H.264 encoding for streaming.

Supports three capture backends selected by CAMERA_BACKEND in config:
  - "picamera2"   libcamera-based Python API (Pi CSI cameras, requires Python 3.13)
  - "libcamera"   rpicam-vid subprocess → YUV420 pipe (CSI cameras, any Python version)
  - "v4l2"        OpenCV V4L2 (USB UVC cameras)
  - "auto"        try picamera2, then libcamera, then v4l2
"""

import asyncio
import io
import logging
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Iterator, Optional

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from picamera2 import Picamera2
    _PICAMERA2_AVAILABLE = True
except Exception:
    Picamera2 = None
    _PICAMERA2_AVAILABLE = False

try:
    from PIL import Image as _PILImage
    _PIL_AVAILABLE = True
except ImportError:
    _PILImage = None
    _PIL_AVAILABLE = False

from config import (
    CAMERA_BACKEND,
    CAMERA_DEVICE,
    CAMERA_WIDTH,
    CAMERA_HEIGHT,
    CAMERA_FPS,
    CAMERA_ROTATION_DEGREES,
)

logger = logging.getLogger(__name__)

# Prefer rpicam-vid (newer name), fall back to libcamera-vid
_RPICAM_VID = shutil.which("rpicam-vid") or shutil.which("libcamera-vid")


@dataclass
class VideoStream:
    """Video stream configuration."""

    backend: str = CAMERA_BACKEND
    device: str = CAMERA_DEVICE
    width: int = CAMERA_WIDTH
    height: int = CAMERA_HEIGHT
    fps: int = CAMERA_FPS
    rotation_degrees: int = CAMERA_ROTATION_DEGREES
    codec: str = "h264"


class VideoEncoder:
    """Video capture and encoding for streaming."""

    def __init__(self, config: Optional[VideoStream] = None):
        self.config = config or VideoStream()

        self._capture: Optional["cv2.VideoCapture"] = None   # v4l2
        self._picam: Optional["Picamera2"] = None             # picamera2
        self._libcam_proc: Optional[subprocess.Popen] = None  # libcamera subprocess
        self._active_backend: str = ""

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._frame_queue: queue.Queue = queue.Queue(maxsize=2)
        self._callbacks: list[Callable[[np.ndarray], None]] = []

        self._encoded_queue: queue.Queue = queue.Queue(maxsize=5)

    # ------------------------------------------------------------------
    # Backend resolution
    # ------------------------------------------------------------------

    def _resolve_backend(self) -> str:
        backend = self.config.backend.lower()
        if backend != "auto":
            return backend
        if _PICAMERA2_AVAILABLE:
            return "picamera2"
        if _RPICAM_VID:
            return "libcamera"
        return "v4l2"

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Start video capture. Returns True if started successfully."""
        if self._running:
            return True

        backend = self._resolve_backend()
        tried: list[str] = []

        while backend:
            tried.append(backend)
            if backend == "picamera2":
                if self._start_picamera2():
                    return True
                logger.warning("picamera2 failed, trying libcamera")
                backend = "libcamera" if _RPICAM_VID else "v4l2"
            elif backend == "libcamera":
                if self._start_libcamera():
                    return True
                logger.warning("libcamera subprocess failed, trying v4l2")
                backend = "v4l2"
            elif backend == "v4l2":
                return self._start_v4l2()
            else:
                logger.error("Unknown camera backend: %s", backend)
                return False

        return False

    # -- picamera2 backend ---

    def _start_picamera2(self) -> bool:
        if not _PICAMERA2_AVAILABLE:
            logger.error(
                "picamera2 not available for this Python version. "
                "Install via: sudo apt install -y python3-picamera2"
            )
            return False
        try:
            self._picam = Picamera2()
            cam_cfg = self._picam.create_preview_configuration(
                main={"size": (self.config.width, self.config.height), "format": "BGR888"},
                controls={"FrameRate": float(self.config.fps)},
            )
            self._picam.configure(cam_cfg)
            self._picam.start()
            self._active_backend = "picamera2"
            logger.info(
                "Camera opened via picamera2: %dx%d @ %dfps",
                self.config.width, self.config.height, self.config.fps,
            )
            self._running = True
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()
            return True
        except Exception as e:
            logger.error("Failed to start picamera2: %s", e)
            self._close_picamera2()
            return False

    # -- libcamera subprocess backend ---

    def _start_libcamera(self) -> bool:
        if not _RPICAM_VID:
            logger.error("rpicam-vid / libcamera-vid not found in PATH")
            return False
        try:
            cmd = [
                _RPICAM_VID,
                "-n",                          # no preview
                "--width", str(self.config.width),
                "--height", str(self.config.height),
                "--framerate", str(self.config.fps),
                "--codec", "yuv420",
                "-t", "0",                     # run indefinitely
                "-o", "-",                     # stdout
            ]
            self._libcam_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            # Verify the process is alive after a short delay
            time.sleep(0.3)
            if self._libcam_proc.poll() is not None:
                logger.error(
                    "rpicam-vid exited immediately (code %s)", self._libcam_proc.returncode
                )
                self._libcam_proc = None
                return False

            self._active_backend = "libcamera"
            logger.info(
                "Camera opened via libcamera (rpicam-vid): %dx%d @ %dfps",
                self.config.width, self.config.height, self.config.fps,
            )
            self._running = True
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()
            return True
        except Exception as e:
            logger.error("Failed to start libcamera subprocess: %s", e)
            self._close_libcamera()
            return False

    # -- v4l2 backend ---

    def _start_v4l2(self) -> bool:
        if cv2 is None:
            logger.error(
                "OpenCV not installed. Install with: pip install opencv-python"
            )
            return False
        try:
            self._capture = cv2.VideoCapture(self.config.device, cv2.CAP_V4L2)
            if not self._capture.isOpened():
                self._capture = cv2.VideoCapture(self.config.device)
            if not self._capture.isOpened():
                logger.error("Failed to open camera: %s", self.config.device)
                return False

            self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
            self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
            self._capture.set(cv2.CAP_PROP_FPS, self.config.fps)

            actual_width = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = int(self._capture.get(cv2.CAP_PROP_FPS))
            logger.info(
                "Camera opened via v4l2: %dx%d @ %dfps",
                actual_width, actual_height, actual_fps,
            )
            self._active_backend = "v4l2"
            self._running = True
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()
            return True
        except Exception as e:
            logger.error("Failed to start video capture (v4l2): %s", e)
            return False

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._active_backend == "picamera2":
            self._close_picamera2()
        elif self._active_backend == "libcamera":
            self._close_libcamera()
        else:
            if self._capture:
                self._capture.release()
                self._capture = None
        logger.info("Video capture stopped")

    def _close_picamera2(self) -> None:
        if self._picam is not None:
            try:
                self._picam.stop()
                self._picam.close()
            except Exception:
                pass
            self._picam = None

    def _close_libcamera(self) -> None:
        if self._libcam_proc is not None:
            try:
                self._libcam_proc.terminate()
                self._libcam_proc.wait(timeout=2.0)
            except Exception:
                pass
            self._libcam_proc = None

    # ------------------------------------------------------------------
    # Reconnect
    # ------------------------------------------------------------------

    def _reconnect(self) -> bool:
        if self._active_backend == "picamera2":
            return self._reconnect_picamera2()
        if self._active_backend == "libcamera":
            return self._reconnect_libcamera()
        return self._reconnect_v4l2()

    def _reconnect_picamera2(self) -> bool:
        self._close_picamera2()
        time.sleep(1.0)
        try:
            self._picam = Picamera2()
            cam_cfg = self._picam.create_preview_configuration(
                main={"size": (self.config.width, self.config.height), "format": "BGR888"},
                controls={"FrameRate": float(self.config.fps)},
            )
            self._picam.configure(cam_cfg)
            self._picam.start()
            logger.info("Camera reconnected successfully (picamera2)")
            return True
        except Exception as e:
            logger.error("Reconnect failed (picamera2): %s", e)
            self._close_picamera2()
            return False

    def _reconnect_libcamera(self) -> bool:
        self._close_libcamera()
        time.sleep(1.0)
        cmd = [
            _RPICAM_VID, "-n",
            "--width", str(self.config.width),
            "--height", str(self.config.height),
            "--framerate", str(self.config.fps),
            "--codec", "yuv420", "-t", "0", "-o", "-",
        ]
        try:
            self._libcam_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0
            )
            time.sleep(0.3)
            if self._libcam_proc.poll() is not None:
                self._libcam_proc = None
                return False
            logger.info("Camera reconnected successfully (libcamera)")
            return True
        except Exception as e:
            logger.error("Reconnect failed (libcamera): %s", e)
            self._libcam_proc = None
            return False

    def _reconnect_v4l2(self) -> bool:
        if self._capture:
            self._capture.release()
            self._capture = None
        time.sleep(1.0)
        self._capture = cv2.VideoCapture(self.config.device, cv2.CAP_V4L2)
        if not self._capture.isOpened():
            self._capture = cv2.VideoCapture(self.config.device)
        if not self._capture.isOpened():
            logger.error("Reconnect failed: cannot open %s", self.config.device)
            return False
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
        self._capture.set(cv2.CAP_PROP_FPS, self.config.fps)
        logger.info("Camera reconnected successfully (v4l2)")
        return True

    # ------------------------------------------------------------------
    # Frame reading
    # ------------------------------------------------------------------

    def _read_frame(self) -> Optional[np.ndarray]:
        """Read one BGR frame from the active backend. Returns None on failure."""
        if self._active_backend == "picamera2":
            if self._picam is None:
                return None
            try:
                return self._picam.capture_array("main")  # BGR888
            except Exception as e:
                logger.debug("picamera2 capture error: %s", e)
                return None

        if self._active_backend == "libcamera":
            if self._libcam_proc is None or self._libcam_proc.poll() is not None:
                return None
            frame_bytes = self.config.width * self.config.height * 3 // 2
            try:
                # Pipe reads may return partial data; loop until we have a full frame.
                buf = bytearray(frame_bytes)
                view = memoryview(buf)
                pos = 0
                while pos < frame_bytes:
                    n = self._libcam_proc.stdout.readinto(view[pos:])
                    if not n:  # EOF / process exited
                        return None
                    pos += n
                raw = bytes(buf)
            except Exception:
                return None
            yuv = np.frombuffer(raw, dtype=np.uint8).reshape(
                (self.config.height * 3 // 2, self.config.width)
            )
            if cv2 is not None:
                return cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)
            # Fallback: manual YUV420→RGB via numpy (no cv2 dependency)
            return _yuv420_to_bgr_numpy(yuv, self.config.width, self.config.height)

        # v4l2
        if not self._capture or not self._capture.isOpened():
            return None
        ret, frame = self._capture.read()
        return frame if ret else None

    def _apply_rotation(self, frame: np.ndarray) -> np.ndarray:
        """Rotate frame by configured camera rotation angle."""
        rotation = int(self.config.rotation_degrees) % 360
        if rotation == 0:
            return frame
        if cv2 is not None:
            if rotation == 90:
                return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
            if rotation == 180:
                return cv2.rotate(frame, cv2.ROTATE_180)
            if rotation == 270:
                return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        else:
            # numpy fallback (k=1 is 90° CCW, so negate for CW)
            k = {90: 3, 180: 2, 270: 1}.get(rotation, 0)
            if k:
                return np.ascontiguousarray(np.rot90(frame, k=k))

        logger.warning(
            "Unsupported CAMERA_ROTATION_DEGREES=%s; expected 0/90/180/270. Using 0.",
            self.config.rotation_degrees,
        )
        return frame

    # ------------------------------------------------------------------
    # Capture loop
    # ------------------------------------------------------------------

    def _capture_loop(self) -> None:
        consecutive_failures = 0
        MAX_FAILURES_BEFORE_RECONNECT = 30

        while self._running:
            try:
                frame = self._read_frame()
                if frame is None:
                    consecutive_failures += 1
                    if (
                        consecutive_failures == 1
                        or consecutive_failures % MAX_FAILURES_BEFORE_RECONNECT == 0
                    ):
                        logger.warning(
                            "Failed to read frame (attempt %d); attempting camera reconnect",
                            consecutive_failures,
                        )
                        if not self._reconnect():
                            time.sleep(2.0)
                    else:
                        time.sleep(0.1)
                    continue

                consecutive_failures = 0
                frame = self._apply_rotation(frame)

                try:
                    self._frame_queue.put_nowait(frame)
                except queue.Full:
                    try:
                        self._frame_queue.get_nowait()
                        self._frame_queue.put_nowait(frame)
                    except queue.Empty:
                        pass

                for callback in self._callbacks:
                    try:
                        callback(frame)
                    except Exception as e:
                        logger.error("Frame callback error: %s", e)

                time.sleep(0.001)

            except Exception as e:
                logger.error("Capture loop error: %s", e)
                time.sleep(0.01)

    # ------------------------------------------------------------------
    # Public frame access
    # ------------------------------------------------------------------

    def get_frame(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        """Get latest frame from queue."""
        try:
            return self._frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def read(self) -> tuple[bool, Optional[np.ndarray]]:
        """Read frame directly from camera (blocking)."""
        frame = self._read_frame()
        if frame is None:
            return False, None
        return True, self._apply_rotation(frame)

    # ------------------------------------------------------------------
    # Encoding / streaming
    # ------------------------------------------------------------------

    def encode_frame(self, frame: np.ndarray, quality: int = 80) -> bytes:
        """Encode frame to JPEG bytes."""
        if cv2 is not None:
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
            _, encoded = cv2.imencode(".jpg", frame, encode_params)
            return encoded.tobytes()
        if _PIL_AVAILABLE:
            img = _PILImage.fromarray(frame[:, :, ::-1])  # BGR → RGB
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            return buf.getvalue()
        raise RuntimeError("Neither cv2 nor Pillow available for JPEG encoding")

    def iter_frames(self) -> Iterator[np.ndarray]:
        while self._running:
            frame = self.get_frame(timeout=1.0)
            if frame is not None:
                yield frame

    async def iter_frames_async(self) -> AsyncIterator[np.ndarray]:
        loop = asyncio.get_event_loop()
        while self._running:
            frame = await loop.run_in_executor(None, self.get_frame, 1.0)
            if frame is not None:
                yield frame

    def iter_jpeg(self, quality: int = 80) -> Iterator[bytes]:
        for frame in self.iter_frames():
            yield self.encode_frame(frame, quality)

    async def iter_jpeg_async(self, quality: int = 80) -> AsyncIterator[bytes]:
        async for frame in self.iter_frames_async():
            yield self.encode_frame(frame, quality)

    def generate_mjpeg_stream(self, quality: int = 80) -> Iterator[bytes]:
        boundary = b"--frame\r\n"
        header = b"Content-Type: image/jpeg\r\n\r\n"
        for jpeg in self.iter_jpeg(quality):
            yield boundary + header + jpeg + b"\r\n"

    def add_callback(self, callback: Callable[[np.ndarray], None]) -> None:
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[np.ndarray], None]) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def frame_size(self) -> tuple[int, int]:
        """Get frame size (width, height) after rotation."""
        rotation = int(self.config.rotation_degrees) % 360
        if self._active_backend == "picamera2" and self._picam is not None:
            try:
                width, height = self._picam.camera_configuration()["main"]["size"]
            except Exception:
                width, height = self.config.width, self.config.height
        elif self._active_backend == "libcamera":
            width, height = self.config.width, self.config.height
        elif self._capture:
            width = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        else:
            width, height = self.config.width, self.config.height
        if rotation in (90, 270):
            return (height, width)
        return (width, height)


# ------------------------------------------------------------------
# Numpy-only YUV420 → BGR conversion (fallback when cv2 is absent)
# ------------------------------------------------------------------

def _yuv420_to_bgr_numpy(yuv: np.ndarray, width: int, height: int) -> np.ndarray:
    """Convert a YUV420 (I420) plane array to BGR uint8."""
    y = yuv[:height, :].astype(np.float32)
    uv_height = height // 2
    u = yuv[height : height + uv_height : 2, ::2].astype(np.float32) - 128
    v = yuv[height : height + uv_height : 2, 1::2].astype(np.float32) - 128
    # Upsample U and V to full size
    u = np.repeat(np.repeat(u, 2, axis=0), 2, axis=1)
    v = np.repeat(np.repeat(v, 2, axis=0), 2, axis=1)
    r = np.clip(y + 1.402 * v, 0, 255).astype(np.uint8)
    g = np.clip(y - 0.344136 * u - 0.714136 * v, 0, 255).astype(np.uint8)
    b = np.clip(y + 1.772 * u, 0, 255).astype(np.uint8)
    return np.stack([b, g, r], axis=2)
