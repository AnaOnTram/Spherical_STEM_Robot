"""Human/person tracking using MediaPipe or OpenCV."""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

# MediaPipe is not available on ARM64 (Raspberry Pi)
try:
    import mediapipe as mp
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    mp = None
    MEDIAPIPE_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class BoundingBox:
    """Bounding box for detected person."""
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        """Get center point."""
        return (self.x + self.width // 2, self.y + self.height // 2)

    @property
    def area(self) -> int:
        """Get area."""
        return self.width * self.height


@dataclass
class TrackedPerson:
    """Tracked person data."""
    id: int
    bbox: BoundingBox
    confidence: float
    timestamp: datetime
    velocity: tuple[float, float] = (0.0, 0.0)  # pixels per second
    pose_landmarks: Optional[list] = None


class HumanTracker:
    """Human detection and tracking."""

    def __init__(
        self,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
        use_pose: bool = False,  # Use pose detection for more detailed tracking
    ):
        self.min_detection_confidence = min_detection_confidence
        self.min_tracking_confidence = min_tracking_confidence
        self.use_pose = use_pose

        self._detector = None
        self._pose = None
        self._initialized = False
        self._callbacks: list[Callable[[list[TrackedPerson]], None]] = []

        # Tracking state
        self._tracked_persons: dict[int, TrackedPerson] = {}
        self._next_id = 0
        self._last_timestamp: Optional[datetime] = None

    def _check_deps(self) -> None:
        """Check dependencies."""
        if cv2 is None:
            raise RuntimeError("OpenCV not installed. Install with: pip install opencv-python")

    def initialize(self) -> bool:
        """Initialize detector."""
        self._check_deps()

        try:
            if self.use_pose and MEDIAPIPE_AVAILABLE:
                try:
                    self._pose = mp.solutions.pose.Pose(
                        static_image_mode=False,
                        model_complexity=1,
                        min_detection_confidence=self.min_detection_confidence,
                        min_tracking_confidence=self.min_tracking_confidence,
                    )
                    logger.info("Human tracker initialized with MediaPipe Pose")
                    self._initialized = True
                    return True
                except Exception as e:
                    logger.warning(f"MediaPipe Pose init failed: {e}, falling back to HOG")

            # Fall back to HOG person detector (works on all platforms)
            self._detector = cv2.HOGDescriptor()
            self._detector.setSVMDetector(
                cv2.HOGDescriptor_getDefaultPeopleDetector()
            )
            logger.info("Human tracker initialized with HOG detector")

            self._initialized = True
            return True

        except Exception as e:
            logger.error(f"Failed to initialize human tracker: {e}")
            return False

    def detect(self, frame: np.ndarray) -> list[TrackedPerson]:
        """Detect and track humans in frame.

        Args:
            frame: BGR image frame

        Returns:
            List of tracked persons
        """
        if not self._initialized:
            if not self.initialize():
                return []

        current_time = datetime.now()
        detections = []

        if self._pose is not None:
            detections = self._detect_with_pose(frame)
        else:
            detections = self._detect_with_hog(frame)

        # Update tracking
        tracked = self._update_tracking(detections, current_time)

        # Notify callbacks
        if tracked:
            for callback in self._callbacks:
                try:
                    callback(tracked)
                except Exception as e:
                    logger.error(f"Tracker callback error: {e}")

        self._last_timestamp = current_time
        return tracked

    def _detect_with_hog(self, frame: np.ndarray) -> list[dict]:
        """Detect using HOG descriptor."""
        # Resize for faster detection
        scale = 0.5
        small_frame = cv2.resize(frame, None, fx=scale, fy=scale)

        # Detect people
        boxes, weights = self._detector.detectMultiScale(
            small_frame,
            winStride=(8, 8),
            padding=(4, 4),
            scale=1.05,
        )

        detections = []
        for (x, y, w, h), weight in zip(boxes, weights):
            if weight >= self.min_detection_confidence:
                # Scale back to original size
                detections.append({
                    "bbox": BoundingBox(
                        int(x / scale), int(y / scale),
                        int(w / scale), int(h / scale)
                    ),
                    "confidence": float(weight),
                    "landmarks": None,
                })

        return detections

    def _detect_with_pose(self, frame: np.ndarray) -> list[dict]:
        """Detect using MediaPipe Pose."""
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self._pose.process(rgb_frame)

        detections = []
        if results.pose_landmarks:
            # Get bounding box from landmarks
            h, w = frame.shape[:2]
            landmarks = results.pose_landmarks.landmark

            # Get min/max coordinates
            x_coords = [lm.x * w for lm in landmarks if lm.visibility > 0.5]
            y_coords = [lm.y * h for lm in landmarks if lm.visibility > 0.5]

            if x_coords and y_coords:
                x_min, x_max = int(min(x_coords)), int(max(x_coords))
                y_min, y_max = int(min(y_coords)), int(max(y_coords))

                # Add padding
                padding = 20
                x_min = max(0, x_min - padding)
                y_min = max(0, y_min - padding)
                x_max = min(w, x_max + padding)
                y_max = min(h, y_max + padding)

                detections.append({
                    "bbox": BoundingBox(
                        x_min, y_min,
                        x_max - x_min, y_max - y_min
                    ),
                    "confidence": 0.9,
                    "landmarks": landmarks,
                })

        return detections

    def _update_tracking(
        self,
        detections: list[dict],
        current_time: datetime,
    ) -> list[TrackedPerson]:
        """Update tracking with new detections using simple IoU matching."""
        tracked = []

        # Calculate time delta for velocity
        dt = 1.0
        if self._last_timestamp:
            dt = (current_time - self._last_timestamp).total_seconds()
            dt = max(dt, 0.001)  # Avoid division by zero

        # Match detections to existing tracks
        matched_tracks = set()
        matched_detections = set()

        for det_idx, detection in enumerate(detections):
            best_iou = 0.3  # Minimum IoU threshold
            best_track_id = None

            for track_id, person in self._tracked_persons.items():
                if track_id in matched_tracks:
                    continue

                iou = self._calculate_iou(detection["bbox"], person.bbox)
                if iou > best_iou:
                    best_iou = iou
                    best_track_id = track_id

            if best_track_id is not None:
                # Update existing track
                old_person = self._tracked_persons[best_track_id]
                old_center = old_person.bbox.center
                new_center = detection["bbox"].center

                velocity = (
                    (new_center[0] - old_center[0]) / dt,
                    (new_center[1] - old_center[1]) / dt,
                )

                person = TrackedPerson(
                    id=best_track_id,
                    bbox=detection["bbox"],
                    confidence=detection["confidence"],
                    timestamp=current_time,
                    velocity=velocity,
                    pose_landmarks=detection.get("landmarks"),
                )
                self._tracked_persons[best_track_id] = person
                tracked.append(person)
                matched_tracks.add(best_track_id)
                matched_detections.add(det_idx)
            else:
                # New detection, create new track
                person = TrackedPerson(
                    id=self._next_id,
                    bbox=detection["bbox"],
                    confidence=detection["confidence"],
                    timestamp=current_time,
                    pose_landmarks=detection.get("landmarks"),
                )
                self._tracked_persons[self._next_id] = person
                tracked.append(person)
                self._next_id += 1
                matched_detections.add(det_idx)

        # Remove stale tracks (not matched for too long)
        stale_threshold = 1.0  # seconds
        stale_tracks = []
        for track_id, person in self._tracked_persons.items():
            if track_id not in matched_tracks:
                age = (current_time - person.timestamp).total_seconds()
                if age > stale_threshold:
                    stale_tracks.append(track_id)

        for track_id in stale_tracks:
            del self._tracked_persons[track_id]

        return tracked

    def _calculate_iou(self, box1: BoundingBox, box2: BoundingBox) -> float:
        """Calculate Intersection over Union."""
        x1 = max(box1.x, box2.x)
        y1 = max(box1.y, box2.y)
        x2 = min(box1.x + box1.width, box2.x + box2.width)
        y2 = min(box1.y + box1.height, box2.y + box2.height)

        if x2 <= x1 or y2 <= y1:
            return 0.0

        intersection = (x2 - x1) * (y2 - y1)
        union = box1.area + box2.area - intersection

        return intersection / union if union > 0 else 0.0

    def draw_tracks(
        self,
        frame: np.ndarray,
        persons: list[TrackedPerson],
    ) -> np.ndarray:
        """Draw tracking visualization on frame."""
        annotated = frame.copy()

        for person in persons:
            bbox = person.bbox

            # Draw bounding box
            cv2.rectangle(
                annotated,
                (bbox.x, bbox.y),
                (bbox.x + bbox.width, bbox.y + bbox.height),
                (0, 255, 0), 2
            )

            # Draw ID and confidence
            label = f"ID:{person.id} ({person.confidence:.2f})"
            cv2.putText(
                annotated, label,
                (bbox.x, bbox.y - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2
            )

            # Draw velocity vector
            if person.velocity != (0.0, 0.0):
                center = bbox.center
                end_x = int(center[0] + person.velocity[0] * 0.1)
                end_y = int(center[1] + person.velocity[1] * 0.1)
                cv2.arrowedLine(
                    annotated, center, (end_x, end_y),
                    (255, 0, 0), 2
                )

        return annotated

    def add_callback(self, callback: Callable[[list[TrackedPerson]], None]) -> None:
        """Add callback for tracking updates."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[list[TrackedPerson]], None]) -> None:
        """Remove callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def close(self) -> None:
        """Release resources."""
        if self._pose:
            self._pose.close()
            self._pose = None
        self._detector = None
        self._initialized = False
        self._tracked_persons.clear()

    @property
    def is_initialized(self) -> bool:
        """Check if tracker is initialized."""
        return self._initialized

    @property
    def active_tracks(self) -> int:
        """Get number of active tracks."""
        return len(self._tracked_persons)
