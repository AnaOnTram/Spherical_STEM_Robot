"""LLM Chat service for local and cloud voice conversations."""

import base64
import io
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import soundfile as sf
from openai import OpenAI

import config


@dataclass
class LLMChatResult:
    """Result from LLM chat operation."""
    session_id: str
    text: str
    transcript: Optional[str] = None
    audio_path: Optional[str] = None
    provider: str = "unknown"
    elapsed_ms: int = 0


# Session management for multi-turn conversations
_sessions: Dict[str, List[dict]] = {}

# Track if local server has been started
_local_server_process: Optional[subprocess.Popen] = None


def _is_server_running(host: str = "127.0.0.1", port: int = 8080, timeout: float = 1.0) -> bool:
    """Check if the LFM2.5 server is running."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def _start_local_server() -> bool:
    """Start the local LFM2.5 server if not already running.
    
    Returns:
        True if server is running (or was started successfully), False otherwise.
    """
    global _local_server_process
    
    # Check if already running
    if _is_server_running():
        return True
    
    # Check if auto-start is enabled
    if not getattr(config, 'LLM_CHAT_LOCAL_AUTO_START', True):
        return False
    
    # Check if llama.cpp server binary exists
    # Get repo root (parent of LLM_Chat directory)
    repo_root = Path(__file__).resolve().parent.parent
    
    llama_server_paths = [
        repo_root / "LLM_Chat" / "server" / "llama-liquid-audio-server",  # Project-local server (with libs)
        repo_root / "LLM_Chat" / "server" / "llama-server",  # Alternative name
        Path.home() / "llama.cpp" / "build" / "bin" / "llama-server",
        Path.home() / "llama.cpp" / "llama-server",
        Path("/usr/local/bin/llama-server"),
        Path("/usr/bin/llama-server"),
    ]
    
    llama_server = None
    for path in llama_server_paths:
        if path.exists():
            llama_server = path
            break
    
    if not llama_server:
        # Try to find in PATH
        import shutil
        llama_server_path = shutil.which("llama-server")
        if llama_server_path:
            llama_server = Path(llama_server_path)
    
    if not llama_server:
        print("[LLM Chat] llama-server not found. Searched:")
        for path in llama_server_paths:
            print(f"  - {path}")
        print("[LLM Chat] Please build llama.cpp or set LLM_CHAT_LOCAL_AUTO_START=False in config.py")
        return False
    
    # Verify model files exist
    model_path = Path(config.LFM_MODEL_PATH)
    mmproj_path = Path(config.LFM_MMPROJ_PATH)
    
    if not model_path.exists():
        print(f"[LLM Chat] Model file not found: {model_path}")
        return False
    
    if not mmproj_path.exists():
        print(f"[LLM Chat] MMProj file not found: {mmproj_path}")
        return False
    
    # Build command
    cmd = [
        str(llama_server),
        "-m", str(model_path),
        "-mm", str(mmproj_path),
        "--port", "8080",
        "-c", "4096",
        "-ngl", "99",  # GPU layers
        "--host", "127.0.0.1",
    ]
    
    # Optional: Add vocoder if available
    vocoder_path = Path(config.LFM_VOCODER_PATH)
    if vocoder_path.exists():
        cmd.extend(["-mv", str(vocoder_path)])
    
    # Add TTS speaker file (tokenizer) if available
    tokenizer_path = Path(config.LFM_TOKENIZER_PATH)
    if tokenizer_path.exists():
        cmd.extend(["--tts-speaker-file", str(tokenizer_path)])
    
    print(f"[LLM Chat] Starting local LFM2.5 server...")
    print(f"[LLM Chat] Command: {' '.join(cmd)}")
    
    # Create log files for debugging
    log_dir = Path(tempfile.gettempdir()) / "spherical_bot_llm"
    log_dir.mkdir(exist_ok=True)
    stdout_log = log_dir / "llama-server.stdout.log"
    stderr_log = log_dir / "llama-server.stderr.log"
    
    try:
        # Start server in background with logging
        stdout_file = open(stdout_log, 'w')
        stderr_file = open(stderr_log, 'w')
        
        # Set working directory to server binary location for shared libs
        cwd = llama_server.parent if llama_server else None
        
        _local_server_process = subprocess.Popen(
            cmd,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
            cwd=cwd,
        )
        
        # Wait for server to be ready
        print("[LLM Chat] Waiting for server to start...")
        for i in range(30):  # Wait up to 30 seconds
            time.sleep(1)
            if _is_server_running():
                print("[LLM Chat] Server is ready!")
                stdout_file.close()
                stderr_file.close()
                return True
            if _local_server_process.poll() is not None:
                exit_code = _local_server_process.poll()
                stdout_file.close()
                stderr_file.close()
                print(f"[LLM Chat] Server process exited with code {exit_code}")
                # Read and display error logs
                if stderr_log.exists():
                    stderr_content = stderr_log.read_text()
                    if stderr_content:
                        print(f"[LLM Chat] Server stderr:\n{stderr_content}")
                if stdout_log.exists():
                    stdout_content = stdout_log.read_text()
                    if stdout_content:
                        print(f"[LLM Chat] Server stdout:\n{stdout_content}")
                return False
        
        stdout_file.close()
        stderr_file.close()
        print("[LLM Chat] Server failed to start within 30 seconds")
        return False
        
    except Exception as e:
        print(f"[LLM Chat] Failed to start server: {e}")
        import traceback
        traceback.print_exc()
        return False


def _ensure_local_server():
    """Ensure local server is running before making requests."""
    if not _is_server_running():
        if not _start_local_server():
            raise RuntimeError(
                f"Local LFM2.5 server is not running at {config.LLM_CHAT_LOCAL_BASE_URL}. "
                f"Please start it manually or enable LLM_CHAT_LOCAL_AUTO_START in config.py"
            )


def _get_or_create_session(session_id: Optional[str]) -> str:
    """Get existing session or create new one."""
    if session_id is None:
        session_id = str(uuid.uuid4())
    if session_id not in _sessions:
        _sessions[session_id] = []
    return session_id


def reset_session(session_id: str) -> None:
    """Reset a chat session, clearing all context."""
    if session_id in _sessions:
        del _sessions[session_id]


def _process_audio_stream(stream, save_audio: bool = True) -> tuple:
    """Process streaming response from LLM.
    
    Returns:
        tuple: (text_content, audio_samples, audio_path)
    """
    text_chunks = []
    audio_samples = []
    audio_path = None
    
    for chunk in stream:
        if chunk.choices[0].finish_reason == "stop":
            break
            
        delta = chunk.choices[0].delta
        
        # Handle text
        if hasattr(delta, 'content') and delta.content:
            text_chunks.append(delta.content)
        
        # Handle audio
        if hasattr(delta, 'audio_chunk') and delta.audio_chunk:
            chunk_data = delta.audio_chunk.get("data")
            if chunk_data:
                pcm_bytes = base64.b64decode(chunk_data)
                samples = struct.unpack(f"<{len(pcm_bytes) // 4}f", pcm_bytes)
                audio_samples.extend(samples)
    
    # Save audio if we have samples
    if audio_samples and save_audio:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            audio_path = f.name
            samples_array = np.array(audio_samples, dtype=np.float32)
            sf.write(audio_path, samples_array, config.LLM_CHAT_LOCAL_SAMPLE_RATE)
    
    full_text = "".join(text_chunks)
    return full_text, audio_samples, audio_path


def local_chat_with_audio(
    wav_data: bytes,
    session_id: Optional[str] = None,
    reset: bool = False,
    max_tokens: int = 512,
    system_prompt: Optional[str] = None,
) -> LLMChatResult:
    """Chat with local LFM2.5-Audio model.
    
    Args:
        wav_data: WAV audio data bytes
        session_id: Session ID for multi-turn chat (None for new session)
        reset: Whether to reset session context
        max_tokens: Maximum tokens to generate
        system_prompt: Optional system prompt override
        
    Returns:
        LLMChatResult with response data
    """
    start_time = time.time()
    
    # Ensure local server is running
    _ensure_local_server()
    
    # Initialize client
    client = OpenAI(
        base_url=config.LLM_CHAT_LOCAL_BASE_URL,
        api_key="not-needed"  # Local model doesn't need API key
    )
    
    # Handle session
    if reset and session_id:
        reset_session(session_id)
    
    session_id = _get_or_create_session(session_id)
    messages = _sessions[session_id]
    
    # Add system prompt if first message or reset
    if not messages or reset:
        sys_prompt = system_prompt or config.LLM_CHAT_LOCAL_SYSTEM_PROMPT
        messages.append({"role": "system", "content": sys_prompt})
    
    # Encode audio
    encoded_audio = base64.b64encode(wav_data).decode("utf-8")
    
    # Add user message with audio
    messages.append({
        "role": "user",
        "content": [
            {
                "type": "input_audio",
                "input_audio": {"data": encoded_audio, "format": "wav"},
            }
        ],
    })
    
    # Trim session history if too long
    max_messages = config.LLM_CHAT_SESSION_MAX_MESSAGES
    if len(messages) > max_messages + 1:  # +1 for system message
        messages = [messages[0]] + messages[-(max_messages):]
        _sessions[session_id] = messages
    
    # Make streaming request
    try:
        stream = client.chat.completions.create(
            model=config.LLM_CHAT_LOCAL_MODEL or "",
            messages=messages,
            stream=True,
            max_tokens=max_tokens,
            extra_body={"reset_context": False},
        )
    except Exception as e:
        if "Connection refused" in str(e) or "ConnectError" in str(type(e)):
            raise RuntimeError(
                f"Cannot connect to local LFM2.5 server at {config.LLM_CHAT_LOCAL_BASE_URL}. "
                f"Please start the server or check the URL in config.py (LLM_CHAT_LOCAL_BASE_URL). "
                f"Original error: {e}"
            ) from e
        raise
    
    # Process response
    text, audio_samples, audio_path = _process_audio_stream(stream)
    
    # Note: LFM2.5 server only accepts "system" and "user" roles
    # Assistant responses are maintained internally by the server via reset_context
    # We don't add assistant messages to the session
    
    elapsed_ms = int((time.time() - start_time) * 1000)
    
    return LLMChatResult(
        session_id=session_id,
        text=text,
        transcript=None,  # ASR is implicit in the model
        audio_path=audio_path,
        provider="lfm2.5-audio-local",
        elapsed_ms=elapsed_ms,
    )


def cloud_chat_with_audio(
    wav_data: bytes,
    session_id: Optional[str] = None,
    reset: bool = False,
    temperature: float = 0.3,
    max_tokens: int = 512,
    system_prompt: Optional[str] = None,
    tts_voice: Optional[str] = None,
) -> LLMChatResult:
    """Chat with cloud LLM via OpenRouter (with optional TTS).
    
    Args:
        wav_data: WAV audio data bytes
        session_id: Session ID for multi-turn chat (None for new session)
        reset: Whether to reset session context
        temperature: Sampling temperature
        max_tokens: Maximum tokens to generate
        system_prompt: Optional system prompt override
        tts_voice: TTS voice to use for response (if TTS enabled)
        
    Returns:
        LLMChatResult with response data
    """
    start_time = time.time()
    
    if not config.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not configured")
    
    # Initialize client
    client = OpenAI(
        base_url=config.OPENROUTER_BASE_URL,
        api_key=config.OPENROUTER_API_KEY,
    )
    
    # Handle session
    if reset and session_id:
        reset_session(session_id)
    
    session_id = _get_or_create_session(session_id)
    messages = _sessions[session_id]
    
    # Add system prompt if first message or reset
    if not messages or reset:
        sys_prompt = system_prompt or "You are a helpful assistant."
        messages.append({"role": "system", "content": sys_prompt})
    
    # First, transcribe audio using OpenAI Whisper or similar via OpenRouter
    # For now, we'll use a simpler approach - treat as text input
    # In a full implementation, you'd use an ASR service here
    user_message = "[Audio input received]"
    
    messages.append({"role": "user", "content": user_message})
    
    # Trim session history if too long
    max_messages = config.LLM_CHAT_SESSION_MAX_MESSAGES
    if len(messages) > max_messages + 1:
        messages = [messages[0]] + messages[-(max_messages):]
        _sessions[session_id] = messages
    
    # Make request
    response = client.chat.completions.create(
        model=config.OPENROUTER_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        extra_headers={
            "HTTP-Referer": config.OPENROUTER_SITE_URL or "",
            "X-Title": config.OPENROUTER_APP_NAME,
        },
    )
    
    text = response.choices[0].message.content
    messages.append({"role": "assistant", "content": text})
    
    # Generate TTS if enabled
    audio_path = None
    if config.CLOUD_TTS_PROVIDER == "openai" and config.OPENAI_API_KEY:
        audio_path = _generate_tts(text, tts_voice or config.OPENAI_TTS_VOICE)
    
    elapsed_ms = int((time.time() - start_time) * 1000)
    
    return LLMChatResult(
        session_id=session_id,
        text=text,
        transcript=user_message,
        audio_path=audio_path,
        provider="openrouter",
        elapsed_ms=elapsed_ms,
    )


def _generate_tts(text: str, voice: str) -> Optional[str]:
    """Generate TTS audio using OpenAI.
    
    Args:
        text: Text to synthesize
        voice: Voice to use
        
    Returns:
        Path to generated audio file or None if failed
    """
    if not config.OPENAI_API_KEY:
        return None
    
    try:
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            audio_path = f.name
        
        response = client.audio.speech.create(
            model=config.OPENAI_TTS_MODEL,
            voice=voice,
            input=text,
            response_format=config.OPENAI_TTS_FORMAT,
        )
        
        response.stream_to_file(audio_path)
        return audio_path
        
    except Exception as e:
        print(f"TTS generation failed: {e}")
        return None
