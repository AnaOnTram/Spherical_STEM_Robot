"""Regression tests for QuizEngine lifecycle blocking behavior."""

from __future__ import annotations

import asyncio

from education.quiz_engine import QuizEngine, QuizQuestion, QuizState


def _single_question() -> list[QuizQuestion]:
    return [
        QuizQuestion(
            question="What is 1 + 1?",
            options=["2", "1", "3", "4"],
            correct_index=0,
            title="Test Quiz",
        )
    ]


def test_start_waits_for_answer_or_stop():
    engine = QuizEngine(
        questions=_single_question(),
        tts_fn=None,
        display_fn=None,
        result_delay=0.01,
        debounce_frames=2,
    )

    async def _run():
        task = asyncio.create_task(engine.start())
        await asyncio.sleep(0.05)

        # start() should still be blocked waiting for an answer.
        assert task.done() is False
        assert engine.state == QuizState.WAITING_ANSWER

        await engine.stop()
        await asyncio.wait_for(task, timeout=0.2)
        assert engine.state == QuizState.IDLE

    asyncio.run(_run())


def test_start_completes_after_debounced_answer():
    engine = QuizEngine(
        questions=_single_question(),
        tts_fn=None,
        display_fn=None,
        result_delay=0.01,
        debounce_frames=2,
    )

    async def _run():
        task = asyncio.create_task(engine.start())
        await asyncio.sleep(0.05)
        assert engine.state == QuizState.WAITING_ANSWER

        # Debounced answer: 1 finger twice -> option A.
        assert engine.handle_finger_count(1) is False
        assert engine.handle_finger_count(1) is True

        await asyncio.wait_for(task, timeout=0.5)
        assert engine.state == QuizState.COMPLETED
        assert engine.score == 1

    asyncio.run(_run())
