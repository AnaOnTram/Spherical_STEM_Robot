"""YAMNet-based sound classification for crying detection."""
import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from config import AUDIO_SAMPLE_RATE, YAMNET_THRESHOLD

logger = logging.getLogger(__name__)

# YAMNet class indices for baby/child crying
CRYING_CLASS_INDICES = [
    20,   # Baby cry, infant cry
    21,   # Crying, sobbing
    22,   # Whimper
]

# Additional sound classes of interest
SPEECH_CLASS_INDICES = [0, 1, 2, 3]  # Speech, conversation
ALARM_CLASS_INDICES = [393, 394, 395, 396, 397]  # Various alarms


class SoundCategory(Enum):
    """Categories of detected sounds."""
    UNKNOWN = "unknown"
    CRYING = "crying"
    SPEECH = "speech"
    ALARM = "alarm"
    SILENCE = "silence"


@dataclass
class SoundEvent:
    """Detected sound event."""
    category: SoundCategory
    confidence: float
    timestamp: datetime
    class_name: str
    raw_scores: Optional[np.ndarray] = None


class YAMNetClassifier:
    """Sound classifier using TensorFlow Lite YAMNet model."""

    def __init__(
        self,
        model_path: Optional[str] = None,
        threshold: float = YAMNET_THRESHOLD,
    ):
        self.threshold = threshold
        self._interpreter = None
        self._class_names: list[str] = []
        self._model_loaded = False
        self._model_path = model_path
        self._callbacks: list[Callable[[SoundEvent], None]] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def load_model(self) -> bool:
        """Load YAMNet TensorFlow Lite model.

        Returns:
            True if model loaded successfully
        """
        tflite = None

        # Try different TFLite import methods
        try:
            import tflite_runtime.interpreter as tflite
            logger.debug("Using tflite_runtime")
        except ImportError:
            try:
                import tensorflow.lite as tflite
                logger.debug("Using tensorflow.lite")
            except ImportError:
                try:
                    # TensorFlow 2.x alternative import
                    import tensorflow as tf
                    tflite = tf.lite
                    logger.debug("Using tf.lite")
                except ImportError:
                    pass

        if tflite is None:
            logger.warning(
                "TFLite runtime not available. Sound classification disabled. "
                "Install with: pip install tensorflow (or use Python 3.11 with tflite-runtime)"
            )
            return False

        try:
            # Use provided model path or default
            if self._model_path:
                model_file = Path(self._model_path)
            else:
                # Default path for YAMNet lite model
                model_file = Path(__file__).parent / "models" / "yamnet.tflite"

            if not model_file.exists():
                logger.error(
                    f"YAMNet model not found at {model_file}. "
                    "Download from: https://tfhub.dev/google/lite-model/yamnet/tflite/1"
                )
                return False

            self._interpreter = tflite.Interpreter(model_path=str(model_file))
            self._interpreter.allocate_tensors()

            # Load class names
            class_file = model_file.parent / "yamnet_class_map.csv"
            if class_file.exists():
                self._load_class_names(class_file)
            else:
                logger.warning("Class names file not found, using indices")
                self._class_names = [f"class_{i}" for i in range(521)]

            self._model_loaded = True
            logger.info("YAMNet model loaded successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to load YAMNet model: {e}")
            return False

    def _load_class_names(self, csv_path: Path) -> None:
        """Load class names from CSV file."""
        self._class_names = []
        with open(csv_path, "r") as f:
            # Skip header
            next(f, None)
            for line in f:
                parts = line.strip().split(",")
                if len(parts) >= 3:
                    self._class_names.append(parts[2].strip('"'))

    def classify(self, audio_data: np.ndarray) -> SoundEvent:
        """Classify audio data.

        Args:
            audio_data: Audio samples (int16 or float32)

        Returns:
            SoundEvent with classification result
        """
        if not self._model_loaded:
            return SoundEvent(
                category=SoundCategory.UNKNOWN,
                confidence=0.0,
                timestamp=datetime.now(),
                class_name="model_not_loaded",
            )

        try:
            # Prepare audio for YAMNet (expects float32, normalized)
            if audio_data.dtype == np.int16:
                audio_float = audio_data.astype(np.float32) / 32768.0
            else:
                audio_float = audio_data.astype(np.float32)

            # Ensure correct length (YAMNet expects ~1 second at 16kHz)
            expected_samples = AUDIO_SAMPLE_RATE
            if len(audio_float) < expected_samples:
                audio_float = np.pad(
                    audio_float, (0, expected_samples - len(audio_float))
                )
            elif len(audio_float) > expected_samples:
                audio_float = audio_float[:expected_samples]

            # Run inference
            input_details = self._interpreter.get_input_details()
            output_details = self._interpreter.get_output_details()

            self._interpreter.set_tensor(input_details[0]["index"], audio_float)
            self._interpreter.invoke()

            # Get scores (shape: [1, num_classes])
            scores = self._interpreter.get_tensor(output_details[0]["index"])[0]

            # Determine category
            return self._categorize_scores(scores)

        except Exception as e:
            logger.error(f"Classification error: {e}")
            return SoundEvent(
                category=SoundCategory.UNKNOWN,
                confidence=0.0,
                timestamp=datetime.now(),
                class_name="error",
            )

    def _categorize_scores(self, scores: np.ndarray) -> SoundEvent:
        """Categorize scores into sound categories."""
        timestamp = datetime.now()

        # Check for crying
        crying_scores = [scores[i] for i in CRYING_CLASS_INDICES if i < len(scores)]
        max_crying = max(crying_scores) if crying_scores else 0

        # Check for speech
        speech_scores = [scores[i] for i in SPEECH_CLASS_INDICES if i < len(scores)]
        max_speech = max(speech_scores) if speech_scores else 0

        # Check for alarm
        alarm_scores = [scores[i] for i in ALARM_CLASS_INDICES if i < len(scores)]
        max_alarm = max(alarm_scores) if alarm_scores else 0

        # Find top class
        top_class_idx = np.argmax(scores)
        top_confidence = scores[top_class_idx]
        top_class_name = (
            self._class_names[top_class_idx]
            if top_class_idx < len(self._class_names)
            else f"class_{top_class_idx}"
        )

        # Determine category based on highest score above threshold
        if max_crying >= self.threshold and max_crying >= max(max_speech, max_alarm):
            return SoundEvent(
                category=SoundCategory.CRYING,
                confidence=float(max_crying),
                timestamp=timestamp,
                class_name=top_class_name,
                raw_scores=scores,
            )
        elif max_speech >= self.threshold and max_speech >= max_alarm:
            return SoundEvent(
                category=SoundCategory.SPEECH,
                confidence=float(max_speech),
                timestamp=timestamp,
                class_name=top_class_name,
                raw_scores=scores,
            )
        elif max_alarm >= self.threshold:
            return SoundEvent(
                category=SoundCategory.ALARM,
                confidence=float(max_alarm),
                timestamp=timestamp,
                class_name=top_class_name,
                raw_scores=scores,
            )
        elif top_confidence < 0.1:
            return SoundEvent(
                category=SoundCategory.SILENCE,
                confidence=1.0 - top_confidence,
                timestamp=timestamp,
                class_name="silence",
            )
        else:
            return SoundEvent(
                category=SoundCategory.UNKNOWN,
                confidence=float(top_confidence),
                timestamp=timestamp,
                class_name=top_class_name,
                raw_scores=scores,
            )

    def add_callback(self, callback: Callable[[SoundEvent], None]) -> None:
        """Add callback for sound events."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[SoundEvent], None]) -> None:
        """Remove callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def is_crying(self, audio_data: np.ndarray) -> tuple[bool, float]:
        """Quick check if audio contains crying.

        Returns:
            Tuple of (is_crying, confidence)
        """
        event = self.classify(audio_data)
        return (event.category == SoundCategory.CRYING, event.confidence)

    @property
    def is_loaded(self) -> bool:
        """Check if model is loaded."""
        return self._model_loaded
