"""Hand gesture detection using MediaPipe Tasks GestureRecognizer."""
import logging
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

# MediaPipe Tasks API (mediapipe >= 0.10)
try:
    from mediapipe.tasks import python as mp_tasks
    from mediapipe.tasks.python import vision as mp_vision
    import mediapipe as mp
    MEDIAPIPE_AVAILABLE = True
except ImportError as _mp_err:
    mp_tasks = None
    mp_vision = None
    mp = None
    MEDIAPIPE_AVAILABLE = False
    logging.getLogger(__name__).warning(f"MediaPipe not available: {_mp_err}")

logger = logging.getLogger(__name__)

# Default model URL (MediaPipe GestureRecognizer float16 task bundle)
_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
    "gesture_recognizer/float16/latest/gesture_recognizer.task"
)
# Local cache path so we only download once
_MODEL_CACHE = Path(__file__).parent / "models" / "gesture_recognizer.task"

# Map MediaPipe category names → our Gesture enum values
_MP_GESTURE_MAP = {
    "None":        "none",
    "Closed_Fist": "closed_fist",
    "Open_Palm":   "open_palm",
    "Pointing_Up": "pointing_up",
    "Thumb_Up":    "thumbs_up",
    "Thumb_Down":  "thumbs_down",
    "Victory":     "peace",
    "ILoveYou":    "ok",
}


class Gesture(Enum):
    """Recognized hand gestures."""
    NONE = "none"
    OPEN_PALM = "open_palm"
    CLOSED_FIST = "closed_fist"
    POINTING_UP = "pointing_up"
    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"
    PEACE = "peace"   # V / Victory sign
    OK = "ok"         # ILoveYou / OK sign
    WAVE = "wave"


@dataclass
class GestureEvent:
    """Detected gesture event."""
    gesture: Gesture
    confidence: float
    timestamp: datetime
    hand_landmarks: Optional[list] = None   # list of NormalizedLandmark
    handedness: str = "unknown"             # "Left" or "Right"


class GestureDetector:
    """Hand gesture detector using MediaPipe Tasks GestureRecognizer.

    Uses the official MediaPipe Tasks API (mediapipe >= 0.10) with the
    gesture_recognizer.task model bundle.  The model is downloaded once and
    cached locally at cv_engine/models/gesture_recognizer.task.

    Falls back to a basic skin-colour contour approach when MediaPipe is not
    installed so that the rest of the system keeps running.
    """

    def __init__(
        self,
        min_detection_confidence: float = 0.7,
        min_tracking_confidence: float = 0.5,
        max_num_hands: int = 2,
        model_path: Optional[str] = None,
    ):
        self.min_detection_confidence = min_detection_confidence
        self.min_tracking_confidence = min_tracking_confidence
        self.max_num_hands = max_num_hands
        # Allow caller to supply a pre-downloaded model path
        self.model_path = model_path

        self._recognizer: Optional[object] = None   # GestureRecognizer instance
        self._initialized = False
        self._use_mediapipe = MEDIAPIPE_AVAILABLE
        self._callbacks: list[Callable[[GestureEvent], None]] = []

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _check_deps(self) -> None:
        if cv2 is None:
            raise RuntimeError(
                "OpenCV not installed. Install with: pip install opencv-python"
            )

    def initialize(self) -> bool:
        """Initialize the gesture detector."""
        self._check_deps()

        if MEDIAPIPE_AVAILABLE:
            try:
                self._init_mediapipe_tasks()
                self._use_mediapipe = True
                self._initialized = True
                logger.info("GestureDetector initialised with MediaPipe Tasks GestureRecognizer")
                return True
            except Exception as exc:
                logger.warning(f"MediaPipe Tasks init failed: {exc} — falling back to basic detection")

        # Basic skin-colour fallback (no ML)
        self._use_mediapipe = False
        self._initialized = True
        logger.info("GestureDetector initialised with basic skin-colour detection (no ML)")
        return True

    def _resolve_model_path(self) -> Path:
        """Return a local path to the .task model, downloading if needed."""
        if self.model_path:
            p = Path(self.model_path)
            if not p.exists():
                raise FileNotFoundError(f"Supplied model path not found: {p}")
            return p

        if _MODEL_CACHE.exists():
            return _MODEL_CACHE

        # Download from Google Storage
        _MODEL_CACHE.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Downloading GestureRecognizer model from {_MODEL_URL} …")
        try:
            urllib.request.urlretrieve(_MODEL_URL, str(_MODEL_CACHE))
            logger.info(f"Model saved to {_MODEL_CACHE}")
        except Exception as exc:
            raise RuntimeError(f"Failed to download model: {exc}") from exc
        return _MODEL_CACHE

    def _init_mediapipe_tasks(self) -> None:
        """Initialise MediaPipe Tasks GestureRecognizer (IMAGE mode)."""
        model_path = self._resolve_model_path()

        base_opts = mp_tasks.BaseOptions(model_asset_path=str(model_path))
        options = mp_vision.GestureRecognizerOptions(
            base_options=base_opts,
            running_mode=mp_vision.RunningMode.IMAGE,
            num_hands=self.max_num_hands,
            min_hand_detection_confidence=self.min_detection_confidence,
            min_hand_presence_confidence=self.min_detection_confidence,
            min_tracking_confidence=self.min_tracking_confidence,
        )
        self._recognizer = mp_vision.GestureRecognizer.create_from_options(options)

    # ------------------------------------------------------------------
    # Detection entry point
    # ------------------------------------------------------------------

    def detect(self, frame: np.ndarray) -> list[GestureEvent]:
        """Detect hand gestures in a BGR camera frame.

        Args:
            frame: BGR image array from OpenCV.

        Returns:
            List of GestureEvent (one per detected hand).
        """
        if not self._initialized:
            if not self.initialize():
                return []

        if self._use_mediapipe and self._recognizer is not None:
            return self._detect_mediapipe(frame)
        return self._detect_basic(frame)

    # ------------------------------------------------------------------
    # MediaPipe Tasks detection
    # ------------------------------------------------------------------

    def _detect_mediapipe(self, frame: np.ndarray) -> list[GestureEvent]:
        """Run GestureRecognizer on a single BGR frame."""
        # Convert BGR → RGB and wrap in MediaPipe Image
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        try:
            result = self._recognizer.recognize(mp_image)
        except Exception as exc:
            logger.error(f"GestureRecognizer.recognize() failed: {exc}")
            return []

        events: list[GestureEvent] = []

        # result.gestures is a list-of-lists: one inner list per hand
        if not result.gestures:
            return events

        for hand_idx, hand_gestures in enumerate(result.gestures):
            if not hand_gestures:
                continue

            # Top gesture category for this hand
            top = hand_gestures[0]
            mp_name = top.category_name          # e.g. "Victory"
            confidence = float(top.score)

            # Map to our Gesture enum
            gesture_value = _MP_GESTURE_MAP.get(mp_name, "none")
            try:
                gesture = Gesture(gesture_value)
            except ValueError:
                gesture = Gesture.NONE

            # Handedness
            handedness = "unknown"
            if result.handedness and hand_idx < len(result.handedness):
                hand_cats = result.handedness[hand_idx]
                if hand_cats:
                    handedness = hand_cats[0].category_name  # "Left" or "Right"

            # Hand landmarks (list of NormalizedLandmark)
            landmarks = None
            if result.hand_landmarks and hand_idx < len(result.hand_landmarks):
                landmarks = result.hand_landmarks[hand_idx]

            event = GestureEvent(
                gesture=gesture,
                confidence=confidence,
                timestamp=datetime.now(),
                hand_landmarks=landmarks,
                handedness=handedness,
            )
            events.append(event)

            for cb in self._callbacks:
                try:
                    cb(event)
                except Exception as exc:
                    logger.error(f"Gesture callback error: {exc}")

        return events

    # ------------------------------------------------------------------
    # Basic skin-colour fallback (no ML)
    # ------------------------------------------------------------------

    def _detect_basic(self, frame: np.ndarray) -> list[GestureEvent]:
        """Simple skin-colour hand detection when MediaPipe is unavailable."""
        events: list[GestureEvent] = []

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        lower_skin = np.array([0, 20, 70], dtype=np.uint8)
        upper_skin = np.array([20, 255, 255], dtype=np.uint8)
        mask = cv2.inRange(hsv, lower_skin, upper_skin)

        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=2)
        mask = cv2.erode(mask, kernel, iterations=2)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 5000:
                continue

            x, y, w, h = cv2.boundingRect(contour)
            aspect_ratio = w / h if h > 0 else 0
            if not (0.3 < aspect_ratio < 2.0):
                continue

            hull = cv2.convexHull(contour, returnPoints=False)
            if len(hull) <= 3:
                continue

            defects = cv2.convexityDefects(contour, hull)
            if defects is None:
                continue

            finger_count = self._count_fingers(contour, defects)
            gesture = self._gesture_from_finger_count(finger_count)

            event = GestureEvent(
                gesture=gesture,
                confidence=0.5,
                timestamp=datetime.now(),
                handedness="unknown",
            )
            events.append(event)

            for cb in self._callbacks:
                try:
                    cb(event)
                except Exception as exc:
                    logger.error(f"Gesture callback error: {exc}")

        return events

    def _count_fingers(self, contour, defects) -> int:
        finger_count = 0
        for i in range(defects.shape[0]):
            s, e, f, d = defects[i, 0]
            start = tuple(contour[s][0])
            end   = tuple(contour[e][0])
            far   = tuple(contour[f][0])

            a = np.sqrt((end[0] - start[0])**2 + (end[1] - start[1])**2)
            b = np.sqrt((far[0] - start[0])**2 + (far[1] - start[1])**2)
            c = np.sqrt((end[0] - far[0])**2  + (end[1] - far[1])**2)

            if b * c > 0:
                angle = np.arccos(
                    np.clip((b**2 + c**2 - a**2) / (2 * b * c), -1.0, 1.0)
                )
                if angle <= np.pi / 2 and d > 20:
                    finger_count += 1

        return min(finger_count + 1, 5)

    def _gesture_from_finger_count(self, finger_count: int) -> Gesture:
        mapping = {
            0: Gesture.CLOSED_FIST,
            1: Gesture.POINTING_UP,
            2: Gesture.PEACE,
        }
        if finger_count >= 4:
            return Gesture.OPEN_PALM
        return mapping.get(finger_count, Gesture.NONE)

    # ------------------------------------------------------------------
    # Finger counting (used by quiz engine via main.py detection loop)
    # ------------------------------------------------------------------

    def _resolve_landmarks(self, hand_landmarks):
        """Return a plain list of NormalizedLandmark from either a list or wrapper."""
        if isinstance(hand_landmarks, list):
            return hand_landmarks
        return hand_landmarks.landmark

    def get_finger_states(self, hand_landmarks) -> list:
        """Return [thumb, index, middle, ring, pinky] extended booleans.

        Uses MCP joints as the reference (more robust than PIP for larger angles).
        Handles both upright and inverted hand orientations by checking whether
        the wrist (0) is below the middle-finger MCP (9).

        MediaPipe landmark indices used:
          Wrist: 0
          Thumb:  MCP=2, TIP=4
          Index:  MCP=5, TIP=8
          Middle: MCP=9, TIP=12
          Ring:   MCP=13, TIP=16
          Pinky:  MCP=17, TIP=20
        """
        lm = self._resolve_landmarks(hand_landmarks)

        # y increases downward in image coords (0=top, 1=bottom)
        # hand_up = wrist is below the middle MCP → fingers point upward
        hand_up = lm[0].y > lm[9].y

        states = []

        # Thumb: compare tip to its own MCP
        if hand_up:
            states.append(lm[4].y < lm[2].y)
        else:
            states.append(lm[4].y > lm[2].y)

        # Index, Middle, Ring, Pinky: tip vs MCP
        for tip_idx, mcp_idx in [(8, 5), (12, 9), (16, 13), (20, 17)]:
            if hand_up:
                states.append(lm[tip_idx].y < lm[mcp_idx].y)
            else:
                states.append(lm[tip_idx].y > lm[mcp_idx].y)

        return states  # [thumb, index, middle, ring, pinky]

    def count_extended_fingers(self, hand_landmarks) -> int:
        """Count extended non-thumb fingers (0–4).

        Uses MCP joints as reference and auto-detects hand orientation so it
        works whether the hand is held upright or inverted.
        """
        states = self.get_finger_states(hand_landmarks)
        # Skip thumb (index 0), count the 4 fingers
        return sum(states[1:])

    # ------------------------------------------------------------------
    # Annotation
    # ------------------------------------------------------------------

    def draw_landmarks(
        self,
        frame: np.ndarray,
        events: list[GestureEvent],
    ) -> np.ndarray:
        """Draw gesture labels (and optionally landmarks) on frame."""
        if not events:
            return frame

        annotated = frame.copy()
        h, w = annotated.shape[:2]

        for idx, event in enumerate(events):
            label = f"{event.gesture.value} ({event.confidence:.2f}) [{event.handedness}]"

            if event.hand_landmarks and self._use_mediapipe:
                # Draw landmark dots
                for lm in event.hand_landmarks:
                    cx, cy = int(lm.x * w), int(lm.y * h)
                    cv2.circle(annotated, (cx, cy), 4, (0, 255, 0), -1)

                # Wrist position for label anchor
                wrist = event.hand_landmarks[0]
                lx = int(wrist.x * w)
                ly = max(int(wrist.y * h) - 20, 20)
            else:
                lx, ly = 10, 30 + idx * 30

            cv2.putText(
                annotated, label, (lx, ly),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2,
            )

        return annotated

    # ------------------------------------------------------------------
    # Callback management
    # ------------------------------------------------------------------

    def add_callback(self, callback: Callable[[GestureEvent], None]) -> None:
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[GestureEvent], None]) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release resources."""
        if self._recognizer is not None:
            try:
                self._recognizer.close()
            except Exception:
                pass
            self._recognizer = None
        self._initialized = False

    @property
    def is_initialized(self) -> bool:
        return self._initialized
