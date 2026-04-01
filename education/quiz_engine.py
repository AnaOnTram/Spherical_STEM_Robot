"""Quiz engine for interactive MCQ sessions with MediaPipe finger-count input.

Answer mapping (fingers held up in front of the camera):
  1 finger  → option A (index 0)
  2 fingers → option B (index 1)
  3 fingers → option C (index 2)
  4 fingers → option D (index 3)

This is computed by GestureDetector.count_extended_fingers() which uses the
four non-thumb MediaPipe hand landmarks (TIP y < PIP y ⇒ finger is up).
The detection loop in main.py calls handle_finger_count() on the active
engine instance whenever a valid count (1–4) is seen.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Coroutine, List, Optional, Union

logger = logging.getLogger(__name__)

OPTION_LABELS = ["A", "B", "C", "D"]

# Minimum consecutive frames showing the same count before it is accepted.
# This avoids accepting transient flickers.
_DEBOUNCE_FRAMES = 3


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class QuizQuestion:
    """A single MCQ question."""
    question: str
    options: List[str]          # exactly 4 strings
    correct_index: int          # 0-based index of the correct option
    title: str = "WonderBall STEM"

    def __post_init__(self):
        if len(self.options) != 4:
            raise ValueError("Each QuizQuestion must have exactly 4 options")
        if not (0 <= self.correct_index <= 3):
            raise ValueError("correct_index must be 0–3")


class QuizState(Enum):
    IDLE = "idle"
    READING_QUESTION = "reading_question"   # TTS is playing
    WAITING_ANSWER = "waiting_answer"       # listening for finger count
    SHOWING_RESULT = "showing_result"       # brief feedback delay
    COMPLETED = "completed"


@dataclass
class QuizProgress:
    """Snapshot of the current quiz state, sent to callbacks."""
    state: QuizState
    question_index: int
    total_questions: int
    current_question: Optional[str]
    options: List[str]
    last_finger_count: Optional[int]
    last_answer_index: Optional[int]
    last_correct: Optional[bool]
    score: int
    correct_answer_index: Optional[int]


# Type alias for both sync and async callables accepted by the engine
_AnyCallable = Union[Callable[..., None], Callable[..., Coroutine]]


# ---------------------------------------------------------------------------
# Quiz engine
# ---------------------------------------------------------------------------
class QuizEngine:
    """Runs an interactive MCQ quiz session driven by finger-count input.

    Typical lifecycle
    -----------------
    1.  engine = QuizEngine(questions, tts_fn, display_fn)
    2.  await engine.start()               – reads Q1 aloud, renders on display
    3.  engine.handle_finger_count(n)      – called from gesture-detector loop
    4.  repeat until state == COMPLETED
    """

    def __init__(
        self,
        questions: List[QuizQuestion],
        tts_fn: Optional[_AnyCallable] = None,
        display_fn: Optional[_AnyCallable] = None,
        result_delay: float = 2.5,
        shuffle: bool = False,
        debounce_frames: int = _DEBOUNCE_FRAMES,
    ):
        """
        Args:
            questions:       List of QuizQuestion objects.
            tts_fn:          Async or sync callable(text) that speaks via TTS.
            display_fn:      Async or sync callable(question, options, title)
                             that renders the MCQ card on the E-ink display.
            result_delay:    Seconds to pause after each answer before the
                             next question begins.
            shuffle:         Randomise question order.
            debounce_frames: How many consecutive camera frames must show the
                             same finger count before it is accepted as an
                             answer. Prevents accidental single-frame triggers.
        """
        self._questions = list(questions)
        if shuffle:
            random.shuffle(self._questions)

        self._tts_fn = tts_fn
        self._display_fn = display_fn
        self._result_delay = result_delay
        self._debounce_frames = debounce_frames

        self._index = 0
        self._score = 0
        self._state = QuizState.IDLE
        self._last_finger_count: Optional[int] = None
        self._last_answer_index: Optional[int] = None
        self._last_correct: Optional[bool] = None
        self._callbacks: list[Callable[[QuizProgress], None]] = []
        self._accepting_input = False

        # Debounce state
        self._debounce_count: int = 0
        self._debounce_candidate: Optional[int] = None

        # Session lifecycle gate: start() blocks until stop() or completion.
        self._done_event: asyncio.Event = asyncio.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> QuizState:
        return self._state

    @property
    def score(self) -> int:
        return self._score

    @property
    def total(self) -> int:
        return len(self._questions)

    def add_callback(self, cb: Callable[[QuizProgress], None]) -> None:
        self._callbacks.append(cb)

    def remove_callback(self, cb: Callable[[QuizProgress], None]) -> None:
        try:
            self._callbacks.remove(cb)
        except ValueError:
            pass

    async def start(self) -> None:
        """Start the quiz from the first question."""
        if not self._questions:
            logger.error("No questions loaded into QuizEngine")
            self._done_event.set()
            return
        self._done_event.clear()
        self._index = 0
        self._score = 0
        self._last_finger_count = None
        self._last_answer_index = None
        self._last_correct = None
        self._debounce_count = 0
        self._debounce_candidate = None
        await self._present_question()
        await self._done_event.wait()

    async def stop(self) -> None:
        """Abort the quiz and reset to IDLE."""
        self._accepting_input = False
        self._state = QuizState.IDLE
        self._notify()
        self._done_event.set()

    def handle_finger_count(self, finger_count: int) -> bool:
        """Accept a finger count from the camera detection loop.

        Implements simple debouncing: the same count must be seen for
        ``debounce_frames`` consecutive calls before it is treated as an answer.

        Args:
            finger_count: Number of extended fingers (1–4).

        Returns:
            True if the count was debounced and accepted as a final answer.
        """
        if not self._accepting_input:
            return False
        if self._state != QuizState.WAITING_ANSWER:
            return False
        if not (1 <= finger_count <= 4):
            return False

        # Debounce
        if finger_count == self._debounce_candidate:
            self._debounce_count += 1
        else:
            self._debounce_candidate = finger_count
            self._debounce_count = 1

        if self._debounce_count < self._debounce_frames:
            return False

        # Accepted — reset debounce and process answer
        self._debounce_count = 0
        self._debounce_candidate = None
        self._accepting_input = False

        option_index = finger_count - 1   # 1 finger → index 0 (A), etc.
        self._last_finger_count = finger_count
        self._last_answer_index = option_index
        q = self._questions[self._index]
        self._last_correct = (option_index == q.correct_index)
        if self._last_correct:
            self._score += 1

        logger.info(
            f"Quiz answer accepted: {finger_count} finger(s) → "
            f"option {OPTION_LABELS[option_index]} "
            f"correct={self._last_correct} score={self._score}/{self.total}"
        )

        asyncio.create_task(self._show_result_and_advance())
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _present_question(self) -> None:
        """Render + read the current question, then enter WAITING_ANSWER."""
        q = self._questions[self._index]
        self._state = QuizState.READING_QUESTION
        self._accepting_input = False
        self._debounce_count = 0
        self._debounce_candidate = None
        self._notify()

        # Update E-ink display
        if self._display_fn:
            try:
                result = self._display_fn(q.question, q.options, q.title)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.error(f"Quiz display error: {exc}")

        # Speak the question and options
        tts_text = self._build_tts_text(q)
        if self._tts_fn:
            try:
                result = self._tts_fn(tts_text)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.error(f"Quiz TTS error: {exc}")

        # Now accept finger-count answers from the camera
        self._state = QuizState.WAITING_ANSWER
        self._accepting_input = True
        self._notify()

    def _build_tts_text(self, q: QuizQuestion) -> str:
        """Compose the spoken text for a question."""
        parts = [f"Question {self._index + 1}. {q.question}"]
        for label, option in zip(OPTION_LABELS, q.options):
            parts.append(f"Option {label}: {option}")
        parts.append(
            "Hold up 1 finger for A, 2 fingers for B, "
            "3 fingers for C, or 4 fingers for D."
        )
        return ". ".join(parts)

    async def _show_result_and_advance(self) -> None:
        """Read out feedback, pause, then move to the next question."""
        self._state = QuizState.SHOWING_RESULT
        self._notify()

        if self._tts_fn and self._last_correct is not None:
            q = self._questions[self._index]
            correct_label = OPTION_LABELS[q.correct_index]
            correct_text = q.options[q.correct_index]
            if self._last_correct:
                feedback = "Correct! Well done!"
            else:
                feedback = (
                    f"Not quite. The correct answer is "
                    f"option {correct_label}: {correct_text}."
                )
            try:
                result = self._tts_fn(feedback)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.error(f"Quiz TTS feedback error: {exc}")

        await asyncio.sleep(self._result_delay)

        self._index += 1
        if self._index >= len(self._questions):
            await self._complete()
        else:
            self._last_finger_count = None
            self._last_answer_index = None
            self._last_correct = None
            await self._present_question()

    async def _complete(self) -> None:
        """Finalise the quiz."""
        self._state = QuizState.COMPLETED
        self._accepting_input = False
        logger.info(f"Quiz complete: score {self._score}/{self.total}")

        if self._tts_fn:
            summary = (
                f"Quiz finished! You scored {self._score} out of {self.total}. "
                + ("Amazing!" if self._score == self.total else "Keep practising!")
            )
            try:
                result = self._tts_fn(summary)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.error(f"Quiz TTS summary error: {exc}")

        self._notify()
        self._done_event.set()

    def _notify(self) -> None:
        """Push current progress to all registered callbacks."""
        q = self._questions[self._index] if self._index < len(self._questions) else None
        progress = QuizProgress(
            state=self._state,
            question_index=self._index,
            total_questions=self.total,
            current_question=q.question if q else None,
            options=q.options if q else [],
            last_finger_count=self._last_finger_count,
            last_answer_index=self._last_answer_index,
            last_correct=self._last_correct,
            score=self._score,
            correct_answer_index=(
                self._questions[self._index - 1].correct_index
                if self._index > 0 else None
            ),
        )
        for cb in self._callbacks:
            try:
                cb(progress)
            except Exception as exc:
                logger.error(f"Quiz callback error: {exc}")


# ---------------------------------------------------------------------------
# Built-in question bank
# ---------------------------------------------------------------------------

DEFAULT_QUESTIONS: List[QuizQuestion] = [
    QuizQuestion(
        question="What is 1 plus 2?",
        options=["2", "3", "4", "5"],
        correct_index=1,
        title="WonderBall Maths",
    ),
    QuizQuestion(
        question="How many sides does a triangle have?",
        options=["2", "4", "3", "5"],
        correct_index=2,
        title="WonderBall Maths",
    ),
    QuizQuestion(
        question="What colour is the sky on a sunny day?",
        options=["Green", "Red", "Yellow", "Blue"],
        correct_index=3,
        title="WonderBall Science",
    ),
    QuizQuestion(
        question="Which animal says moo?",
        options=["Dog", "Cow", "Cat", "Duck"],
        correct_index=1,
        title="WonderBall Animals",
    ),
    QuizQuestion(
        question="How many legs does a spider have?",
        options=["6", "4", "8", "10"],
        correct_index=2,
        title="WonderBall Science",
    ),
    QuizQuestion(
        question="What is 2 plus 2?",
        options=["3", "5", "6", "4"],
        correct_index=3,
        title="WonderBall Maths",
    ),
    QuizQuestion(
        question="Which shape has four equal sides?",
        options=["Circle", "Triangle", "Square", "Rectangle"],
        correct_index=2,
        title="WonderBall Shapes",
    ),
    QuizQuestion(
        question="What do caterpillars turn into?",
        options=["Birds", "Butterflies", "Frogs", "Spiders"],
        correct_index=1,
        title="WonderBall Science",
    ),
]
