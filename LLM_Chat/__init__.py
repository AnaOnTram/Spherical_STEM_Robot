"""LLM chat helpers for local and cloud voice conversations."""

try:
    from LLM_Chat.service import LLMChatResult
except Exception:
    LLMChatResult = None

from LLM_Chat.service import (
    local_chat_with_audio,
    cloud_chat_with_audio,
    reset_session,
)

__all__ = [
    "LLMChatResult",
    "local_chat_with_audio",
    "cloud_chat_with_audio",
    "reset_session",
]
