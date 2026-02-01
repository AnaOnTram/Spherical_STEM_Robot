"""Hand gesture detection using MediaPipe or TFLite fallback."""
import logging
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

# MediaPipe is not available on ARM64 (Raspberry Pi)
# Try to import, fall back to TFLite-based detection
try:
    import mediapipe as mp
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    mp = None
    MEDIAPIPE_AVAILABLE = False

logger = logging.getLogger(__name__)


class Gesture(Enum):
    """Recognized hand gestures."""
    NONE = "none"
    OPEN_PALM = "open_palm"
    CLOSED_FIST = "closed_fist"
    POINTING_UP = "pointing_up"
    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"
    PEACE = "peace"  # V sign
    OK = "ok"  # OK sign
    WAVE = "wave"


@dataclass
class GestureEvent:
    """Detected gesture event."""
    gesture: Gesture
    confidence: float
    timestamp: datetime
    hand_landmarks: Optional[list] = None
    handedness: str = "unknown"  # "Left" or "Right"


class GestureDetector:
    """Hand gesture detector using MediaPipe Hands or TFLite fallback."""

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
        self.model_path = model_path

        self._hands = None
        self._tflite_interpreter = None
        self._initialized = False
        self._use_mediapipe = MEDIAPIPE_AVAILABLE
        self._callbacks: list[Callable[[GestureEvent], None]] = []

    def _check_deps(self) -> None:
        """Check dependencies."""
        if cv2 is None:
            raise RuntimeError("OpenCV not installed. Install with: pip install opencv-python")

    def initialize(self) -> bool:
        """Initialize gesture detector (MediaPipe or TFLite fallback)."""
        self._check_deps()

        # Try MediaPipe first (x86/x64 systems)
        if MEDIAPIPE_AVAILABLE:
            try:
                self._hands = mp.solutions.hands.Hands(
                    static_image_mode=False,
                    max_num_hands=self.max_num_hands,
                    min_detection_confidence=self.min_detection_confidence,
                    min_tracking_confidence=self.min_tracking_confidence,
                )
                self._use_mediapipe = True
                self._initialized = True
                logger.info("Gesture detector initialized with MediaPipe")
                return True
            except Exception as e:
                logger.warning(f"MediaPipe init failed: {e}, trying TFLite fallback")

        # TFLite fallback for ARM64 (Raspberry Pi)
        try:
            self._init_tflite()
            self._use_mediapipe = False
            self._initialized = True
            logger.info("Gesture detector initialized with TFLite fallback")
            return True
        except Exception as e:
            logger.warning(f"TFLite fallback not available: {e}")

        # Final fallback: simple motion-based detection
        self._use_mediapipe = False
        self._initialized = True
        logger.info("Gesture detector initialized with basic detection (no ML)")
        return True

    def _init_tflite(self) -> None:
        """Initialize TFLite hand detection model."""
        tflite = None

        try:
            import tflite_runtime.interpreter as tflite
        except ImportError:
            try:
                import tensorflow.lite as tflite
            except ImportError:
                try:
                    import tensorflow as tf
                    tflite = tf.lite
                except ImportError:
                    pass

        if tflite is None:
            raise RuntimeError("TFLite runtime not available")

        # Look for hand detection model
        model_file = None
        if self.model_path:
            model_file = Path(self.model_path)
        else:
            # Check common locations
            search_paths = [
                Path(__file__).parent / "models" / "hand_landmark_lite.tflite",
                Path(__file__).parent / "models" / "palm_detection_lite.tflite",
                Path("/usr/share/tflite_models/hand_landmark.tflite"),
            ]
            for path in search_paths:
                if path.exists():
                    model_file = path
                    break

        if model_file and model_file.exists():
            self._tflite_interpreter = tflite.Interpreter(model_path=str(model_file))
            self._tflite_interpreter.allocate_tensors()
            logger.info(f"Loaded TFLite model: {model_file}")
        else:
            logger.warning(
                "No TFLite hand model found. Download from: "
                "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
            )
            raise FileNotFoundError("TFLite hand model not found")

    def detect(self, frame: np.ndarray) -> list[GestureEvent]:
        """Detect hand gestures in frame.

        Args:
            frame: BGR image frame from camera

        Returns:
            List of detected gesture events
        """
        if not self._initialized:
            if not self.initialize():
                return []

        if self._use_mediapipe and self._hands:
            return self._detect_mediapipe(frame)
        elif self._tflite_interpreter:
            return self._detect_tflite(frame)
        else:
            # Basic detection fallback (skin color based)
            return self._detect_basic(frame)

    def _detect_mediapipe(self, frame: np.ndarray) -> list[GestureEvent]:
        """Detect using MediaPipe."""
        # Convert BGR to RGB
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Process frame
        results = self._hands.process(rgb_frame)

        events = []
        if results.multi_hand_landmarks:
            for idx, hand_landmarks in enumerate(results.multi_hand_landmarks):
                # Get handedness
                handedness = "unknown"
                if results.multi_handedness and idx < len(results.multi_handedness):
                    handedness = results.multi_handedness[idx].classification[0].label

                # Classify gesture
                gesture, confidence = self._classify_gesture(hand_landmarks)

                event = GestureEvent(
                    gesture=gesture,
                    confidence=confidence,
                    timestamp=datetime.now(),
                    hand_landmarks=hand_landmarks.landmark,
                    handedness=handedness,
                )
                events.append(event)

                # Notify callbacks
                for callback in self._callbacks:
                    try:
                        callback(event)
                    except Exception as e:
                        logger.error(f"Gesture callback error: {e}")

        return events

    def _detect_tflite(self, frame: np.ndarray) -> list[GestureEvent]:
        """Detect using TFLite model (simplified)."""
        # This is a placeholder - full TFLite hand detection requires
        # palm detection + hand landmark models working together
        # For now, return empty and log that TFLite detection needs model setup
        logger.debug("TFLite detection requires proper model setup")
        return []

    def _detect_basic(self, frame: np.ndarray) -> list[GestureEvent]:
        """Basic skin-color based hand detection (no ML).

        This is a simple fallback when ML models aren't available.
        It can detect presence of hand-like regions but not specific gestures.
        """
        events = []

        # Convert to HSV for skin detection
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # Skin color range in HSV
        lower_skin = np.array([0, 20, 70], dtype=np.uint8)
        upper_skin = np.array([20, 255, 255], dtype=np.uint8)

        # Create mask for skin color
        mask = cv2.inRange(hsv, lower_skin, upper_skin)

        # Morphological operations to clean up
        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=2)
        mask = cv2.erode(mask, kernel, iterations=2)

        # Find contours
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            area = cv2.contourArea(contour)
            # Filter by area (hand-sized regions)
            if area > 5000:  # Minimum area threshold
                # Get bounding box
                x, y, w, h = cv2.boundingRect(contour)

                # Simple aspect ratio check for hand-like shape
                aspect_ratio = w / h if h > 0 else 0
                if 0.3 < aspect_ratio < 2.0:
                    # Count fingers using convex hull defects
                    hull = cv2.convexHull(contour, returnPoints=False)
                    if len(hull) > 3:
                        defects = cv2.convexityDefects(contour, hull)
                        if defects is not None:
                            finger_count = self._count_fingers(contour, defects)
                            gesture = self._gesture_from_finger_count(finger_count)

                            event = GestureEvent(
                                gesture=gesture,
                                confidence=0.5,  # Lower confidence for basic detection
                                timestamp=datetime.now(),
                                handedness="unknown",
                            )
                            events.append(event)

                            for callback in self._callbacks:
                                try:
                                    callback(event)
                                except Exception as e:
                                    logger.error(f"Gesture callback error: {e}")

        return events

    def _count_fingers(self, contour, defects) -> int:
        """Count extended fingers from convexity defects."""
        finger_count = 0

        for i in range(defects.shape[0]):
            s, e, f, d = defects[i, 0]
            start = tuple(contour[s][0])
            end = tuple(contour[e][0])
            far = tuple(contour[f][0])

            # Calculate distances
            a = np.sqrt((end[0] - start[0])**2 + (end[1] - start[1])**2)
            b = np.sqrt((far[0] - start[0])**2 + (far[1] - start[1])**2)
            c = np.sqrt((end[0] - far[0])**2 + (end[1] - far[1])**2)

            # Calculate angle using cosine rule
            if b * c > 0:
                angle = np.arccos((b**2 + c**2 - a**2) / (2 * b * c))

                # Finger detected if angle is less than 90 degrees
                if angle <= np.pi / 2 and d > 20:
                    finger_count += 1

        return min(finger_count + 1, 5)  # +1 for thumb, max 5

    def _gesture_from_finger_count(self, finger_count: int) -> Gesture:
        """Map finger count to gesture."""
        if finger_count == 0:
            return Gesture.CLOSED_FIST
        elif finger_count == 1:
            return Gesture.POINTING_UP
        elif finger_count == 2:
            return Gesture.PEACE
        elif finger_count >= 4:
            return Gesture.OPEN_PALM
        else:
            return Gesture.NONE

    def _classify_gesture(self, hand_landmarks) -> tuple[Gesture, float]:
        """Classify hand gesture from landmarks."""
        landmarks = hand_landmarks.landmark

        # Extract key points
        thumb_tip = landmarks[4]
        index_tip = landmarks[8]
        middle_tip = landmarks[12]
        ring_tip = landmarks[16]
        pinky_tip = landmarks[20]

        thumb_ip = landmarks[3]
        index_pip = landmarks[6]
        middle_pip = landmarks[10]
        ring_pip = landmarks[14]
        pinky_pip = landmarks[18]

        wrist = landmarks[0]

        # Calculate finger states (extended or not)
        def is_finger_extended(tip, pip, wrist) -> bool:
            # Finger is extended if tip is further from wrist than pip
            tip_dist = ((tip.x - wrist.x) ** 2 + (tip.y - wrist.y) ** 2) ** 0.5
            pip_dist = ((pip.x - wrist.x) ** 2 + (pip.y - wrist.y) ** 2) ** 0.5
            return tip_dist > pip_dist * 0.9

        def is_thumb_extended() -> bool:
            # Thumb extended if tip is far from palm
            return abs(thumb_tip.x - wrist.x) > 0.1

        index_extended = is_finger_extended(index_tip, index_pip, wrist)
        middle_extended = is_finger_extended(middle_tip, middle_pip, wrist)
        ring_extended = is_finger_extended(ring_tip, ring_pip, wrist)
        pinky_extended = is_finger_extended(pinky_tip, pinky_pip, wrist)
        thumb_extended = is_thumb_extended()

        extended_count = sum([
            index_extended, middle_extended, ring_extended, pinky_extended
        ])

        # Classify based on finger states
        if extended_count >= 4 and thumb_extended:
            return Gesture.OPEN_PALM, 0.9

        if extended_count == 0 and not thumb_extended:
            return Gesture.CLOSED_FIST, 0.9

        if index_extended and not middle_extended and not ring_extended and not pinky_extended:
            # Check if pointing up (index tip above pip)
            if index_tip.y < index_pip.y:
                return Gesture.POINTING_UP, 0.85

        if index_extended and middle_extended and not ring_extended and not pinky_extended:
            return Gesture.PEACE, 0.85

        if thumb_extended and not index_extended and not middle_extended:
            # Check thumb direction for thumbs up/down
            if thumb_tip.y < thumb_ip.y:
                return Gesture.THUMBS_UP, 0.8
            else:
                return Gesture.THUMBS_DOWN, 0.8

        # OK sign: thumb and index forming circle
        thumb_index_dist = (
            (thumb_tip.x - index_tip.x) ** 2 + (thumb_tip.y - index_tip.y) ** 2
        ) ** 0.5
        if thumb_index_dist < 0.05 and middle_extended:
            return Gesture.OK, 0.8

        return Gesture.NONE, 0.5

    def draw_landmarks(
        self,
        frame: np.ndarray,
        events: list[GestureEvent],
    ) -> np.ndarray:
        """Draw hand landmarks and gesture labels on frame.

        Args:
            frame: Original frame
            events: Detected gesture events

        Returns:
            Annotated frame
        """
        if not events:
            return frame

        annotated = frame.copy()

        for event in events:
            if event.hand_landmarks and self._use_mediapipe and mp is not None:
                # MediaPipe drawing
                mp_draw = mp.solutions.drawing_utils
                mp_hands = mp.solutions.hands

                # Create landmark proto for drawing
                hand_landmarks = mp.framework.formats.landmark_pb2.NormalizedLandmarkList()
                for lm in event.hand_landmarks:
                    landmark = hand_landmarks.landmark.add()
                    landmark.x = lm.x
                    landmark.y = lm.y
                    landmark.z = lm.z

                mp_draw.draw_landmarks(
                    annotated,
                    hand_landmarks,
                    mp_hands.HAND_CONNECTIONS,
                )

                # Draw gesture label
                h, w, _ = annotated.shape
                x = int(event.hand_landmarks[0].x * w)
                y = int(event.hand_landmarks[0].y * h) - 20

                label = f"{event.gesture.value} ({event.confidence:.2f})"
                cv2.putText(
                    annotated, label, (x, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2
                )
            else:
                # Basic text annotation for non-MediaPipe detection
                label = f"{event.gesture.value} ({event.confidence:.2f})"
                cv2.putText(
                    annotated, label, (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2
                )

        return annotated

    def add_callback(self, callback: Callable[[GestureEvent], None]) -> None:
        """Add callback for gesture events."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[GestureEvent], None]) -> None:
        """Remove callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def close(self) -> None:
        """Release resources."""
        if self._hands:
            self._hands.close()
            self._hands = None
        self._initialized = False

    @property
    def is_initialized(self) -> bool:
        """Check if detector is initialized."""
        return self._initialized
