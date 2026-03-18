"""LLM chat helpers for local voice conversations."""

try:
    from LLM_Chat.service import LLMChatResult
except Exception:
    LLMChatResult = None

from LLM_Chat.service import (
    oral_chat_with_llm,
    synthesize_speech,
    reset_session,
)

__all__ = [
    "LLMChatResult",
    "oral_chat_with_llm",
    "synthesize_speech",
    "reset_session",
]
