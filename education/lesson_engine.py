"""Interactive lesson engine for STEM education."""
import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

from .content_manager import ContentManager, Lesson, LessonStep

logger = logging.getLogger(__name__)


class LessonState(Enum):
    """Lesson playback state."""
    IDLE = "idle"
    LOADING = "loading"
    PLAYING = "playing"
    PAUSED = "paused"
    WAITING_GESTURE = "waiting_gesture"
    COMPLETED = "completed"


@dataclass
class LessonProgress:
    """Lesson progress data."""
    lesson_id: str
    current_step: int
    total_steps: int
    state: LessonState
    step_title: str = ""
    step_content: str = ""
    expected_gesture: Optional[str] = None
    elapsed_time: float = 0.0


class LessonEngine:
    """Engine for running interactive lessons."""

    def __init__(
        self,
        content_manager: ContentManager,
        serial_manager=None,
        audio_player=None,
        image_processor=None,
    ):
        self.content_manager = content_manager
        self.serial_manager = serial_manager
        self.audio_player = audio_player
        self.image_processor = image_processor

        self._current_lesson: Optional[Lesson] = None
        self._current_step_index: int = 0
        self._state = LessonState.IDLE
        self._running = False
        self._paused = False

        self._callbacks: list[Callable[[LessonProgress], None]] = []
        self._step_start_time: Optional[datetime] = None
        self._auto_advance_task: Optional[asyncio.Task] = None

    @property
    def state(self) -> LessonState:
        """Get current state."""
        return self._state

    @property
    def current_lesson(self) -> Optional[Lesson]:
        """Get current lesson."""
        return self._current_lesson

    @property
    def current_step(self) -> Optional[LessonStep]:
        """Get current step."""
        if self._current_lesson and 0 <= self._current_step_index < len(self._current_lesson.steps):
            return self._current_lesson.steps[self._current_step_index]
        return None

    def get_progress(self) -> LessonProgress:
        """Get current lesson progress."""
        step = self.current_step
        elapsed = 0.0
        if self._step_start_time:
            elapsed = (datetime.now() - self._step_start_time).total_seconds()

        return LessonProgress(
            lesson_id=self._current_lesson.lesson_id if self._current_lesson else "",
            current_step=self._current_step_index,
            total_steps=len(self._current_lesson.steps) if self._current_lesson else 0,
            state=self._state,
            step_title=step.title if step else "",
            step_content=step.content if step else "",
            expected_gesture=step.gesture_trigger if step else None,
            elapsed_time=elapsed,
        )

    async def load_lesson(self, lesson_id: str) -> bool:
        """Load a lesson.

        Args:
            lesson_id: Lesson ID to load

        Returns:
            True if loaded successfully
        """
        self._state = LessonState.LOADING

        lesson = self.content_manager.get_lesson(lesson_id)
        if not lesson:
            logger.error(f"Lesson not found: {lesson_id}")
            self._state = LessonState.IDLE
            return False

        if not lesson.steps:
            logger.error(f"Lesson has no steps: {lesson_id}")
            self._state = LessonState.IDLE
            return False

        self._current_lesson = lesson
        self._current_step_index = 0
        self._state = LessonState.IDLE

        logger.info(f"Loaded lesson: {lesson.title}")
        self._notify_progress()

        return True

    async def start(self) -> bool:
        """Start playing the loaded lesson.

        Returns:
            True if started successfully
        """
        if not self._current_lesson:
            logger.error("No lesson loaded")
            return False

        self._running = True
        self._paused = False
        self._current_step_index = 0

        await self._play_current_step()
        return True

    async def _play_current_step(self) -> None:
        """Play the current step."""
        step = self.current_step
        if not step:
            await self._complete_lesson()
            return

        logger.info(f"Playing step: {step.title}")
        self._step_start_time = datetime.now()

        # Update display if available
        if self.image_processor and self.serial_manager:
            await self._update_display(step)

        # Play audio if available
        if self.audio_player and step.audio_file:
            try:
                self.audio_player.play_file(step.audio_file)
            except Exception as e:
                logger.error(f"Failed to play audio: {e}")

        # Set state based on step configuration
        if step.gesture_trigger:
            self._state = LessonState.WAITING_GESTURE
        else:
            self._state = LessonState.PLAYING

        # Set up auto-advance timer if duration is specified
        if step.duration > 0 and not step.gesture_trigger:
            self._auto_advance_task = asyncio.create_task(
                self._auto_advance(step.duration)
            )

        self._notify_progress()

    async def _update_display(self, step: LessonStep) -> None:
        """Update E-Ink display for step."""
        try:
            if step.display_image:
                # Use provided image
                image_data = self.image_processor.process(step.display_image)
            else:
                # Generate text display
                display_text = f"{step.title}\n\n{step.content}"
                image_data = self.image_processor.process_text(display_text)

            # Send to ESP32
            from esp_serial.commands import CommandBuilder
            cmd = CommandBuilder.display_image(image_data)
            await self.serial_manager.send_command_async(cmd)

        except Exception as e:
            logger.error(f"Failed to update display: {e}")

    async def _auto_advance(self, duration: float) -> None:
        """Auto-advance after duration."""
        await asyncio.sleep(duration)
        if self._running and not self._paused:
            await self.next_step()

    async def next_step(self) -> bool:
        """Advance to next step.

        Returns:
            True if advanced, False if lesson complete
        """
        if not self._current_lesson:
            return False

        # Cancel auto-advance timer
        if self._auto_advance_task:
            self._auto_advance_task.cancel()
            self._auto_advance_task = None

        self._current_step_index += 1

        if self._current_step_index >= len(self._current_lesson.steps):
            await self._complete_lesson()
            return False

        await self._play_current_step()
        return True

    async def previous_step(self) -> bool:
        """Go to previous step.

        Returns:
            True if moved back
        """
        if not self._current_lesson or self._current_step_index <= 0:
            return False

        # Cancel auto-advance timer
        if self._auto_advance_task:
            self._auto_advance_task.cancel()
            self._auto_advance_task = None

        self._current_step_index -= 1
        await self._play_current_step()
        return True

    async def go_to_step(self, step_index: int) -> bool:
        """Go to specific step.

        Args:
            step_index: Step index (0-based)

        Returns:
            True if moved to step
        """
        if not self._current_lesson:
            return False

        if step_index < 0 or step_index >= len(self._current_lesson.steps):
            return False

        # Cancel auto-advance timer
        if self._auto_advance_task:
            self._auto_advance_task.cancel()
            self._auto_advance_task = None

        self._current_step_index = step_index
        await self._play_current_step()
        return True

    def pause(self) -> None:
        """Pause lesson playback."""
        if self._state == LessonState.PLAYING:
            self._paused = True
            self._state = LessonState.PAUSED
            self._notify_progress()

    def resume(self) -> None:
        """Resume lesson playback."""
        if self._state == LessonState.PAUSED:
            self._paused = False
            step = self.current_step
            if step and step.gesture_trigger:
                self._state = LessonState.WAITING_GESTURE
            else:
                self._state = LessonState.PLAYING
            self._notify_progress()

    async def stop(self) -> None:
        """Stop lesson playback."""
        self._running = False
        self._paused = False

        if self._auto_advance_task:
            self._auto_advance_task.cancel()
            self._auto_advance_task = None

        if self.audio_player:
            self.audio_player.stop()

        self._state = LessonState.IDLE
        self._current_lesson = None
        self._current_step_index = 0
        self._notify_progress()

    async def _complete_lesson(self) -> None:
        """Handle lesson completion."""
        logger.info(f"Lesson completed: {self._current_lesson.title}")
        self._state = LessonState.COMPLETED
        self._running = False
        self._notify_progress()

    def handle_gesture(self, gesture: str) -> bool:
        """Handle detected gesture.

        Args:
            gesture: Detected gesture name

        Returns:
            True if gesture was handled
        """
        if self._state != LessonState.WAITING_GESTURE:
            return False

        step = self.current_step
        if not step or not step.gesture_trigger:
            return False

        if gesture == step.gesture_trigger:
            logger.info(f"Gesture matched: {gesture}")
            # Schedule next step
            asyncio.create_task(self.next_step())
            return True

        return False

    def add_callback(self, callback: Callable[[LessonProgress], None]) -> None:
        """Add progress callback."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[LessonProgress], None]) -> None:
        """Remove progress callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify_progress(self) -> None:
        """Notify callbacks of progress update."""
        progress = self.get_progress()
        for callback in self._callbacks:
            try:
                callback(progress)
            except Exception as e:
                logger.error(f"Progress callback error: {e}")

    @property
    def is_running(self) -> bool:
        """Check if a lesson is running."""
        return self._running

    @property
    def is_paused(self) -> bool:
        """Check if lesson is paused."""
        return self._paused
