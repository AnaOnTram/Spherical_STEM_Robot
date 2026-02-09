"""LLM Chat service for local and cloud voice conversations."""

import base64
import io
import json
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
    import httpx
    start_time = time.time()
    
    # Use intermediate server auth key (NOT the OpenRouter API key)
    auth_key = getattr(config, 'INTERMEDIATE_SERVER_AUTH', 'admin')
    
    # Handle session
    if reset and session_id:
        reset_session(session_id)
    
    session_id = _get_or_create_session(session_id)
    messages = _sessions[session_id]
    
    # Add system prompt if first message or reset
    if not messages or reset:
        sys_prompt = system_prompt or "You are a helpful assistant for young children. Use very simple words that a 3-year-old can understand. Keep answers short and friendly. Speak in a warm, gentle voice."
        messages = [{"role": "system", "content": sys_prompt}]
    
    # Encode audio to base64 for audio-capable models
    encoded_audio = base64.b64encode(wav_data).decode("utf-8")
    user_message = "[Audio input sent]"
    
    # Create message with audio input (OpenAI audio format)
    messages.append({
        "role": "user",
        "content": [
            {
                "type": "input_audio",
                "input_audio": {
                    "data": encoded_audio,
                    "format": "wav"
                }
            }
        ]
    })
    
    # Trim session history if too long
    max_messages = config.LLM_CHAT_SESSION_MAX_MESSAGES
    if len(messages) > max_messages + 1:
        messages = [messages[0]] + messages[-(max_messages):]
        _sessions[session_id] = messages
    
    # Prepare request payload
    # Request both text and audio modalities for audio-capable models
    payload = {
        "model": config.OPENROUTER_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,  # Required for audio output
        "modalities": ["text", "audio"],
        "audio": {"voice": tts_voice or "alloy", "format": "pcm16"},  # pcm16 required for streaming
    }
    
    # Make request with httpx to properly send custom auth header
    headers = {
        "Content-Type": "application/json",
        "x-custom-auth-key": auth_key,
        "HTTP-Referer": config.OPENROUTER_SITE_URL or "",
        "X-Title": config.OPENROUTER_APP_NAME,
    }
    
    # DEBUG: Print what we're sending
    print(f"[DEBUG] URL: {config.OPENROUTER_BASE_URL}/chat/completions")
    print(f"[DEBUG] Auth key: {'***' if auth_key else 'NOT SET'}")
    
    try:
        # Handle streaming response for audio output
        print(f"[DEBUG] Sending streaming request with modalities: {payload['modalities']}")
        
        with httpx.stream(
            "POST",
            f"{config.OPENROUTER_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
            timeout=60.0,
        ) as response:
            print(f"[DEBUG] Response status: {response.status_code}")
            print(f"[DEBUG] Response headers: {dict(response.headers)}")
            
            if response.status_code != 200:
                error_text = response.read().decode()
                raise RuntimeError(f"API error: {response.status_code} - {error_text}")
            
            # Check if response is JSON or SSE
            content_type = response.headers.get("content-type", "")
            print(f"[DEBUG] Content-Type: {content_type}")
            
            text_parts = []
            audio_parts = []
            
            if "application/json" in content_type:
                # Complete JSON response (non-streaming)
                response_body = response.read()
                if isinstance(response_body, bytes):
                    response_body = response_body.decode('utf-8')
                print(f"[DEBUG] Response body type: {type(response_body)}")
                print(f"[DEBUG] Response body preview: {response_body[:200]}...")

                # Try to parse as JSON; if the body is actually SSE text
                # (e.g. intermediate server forwarded raw SSE with wrong content-type),
                # fall back to SSE parsing below.
                try:
                    data = json.loads(response_body)
                except json.JSONDecodeError:
                    data = None

                if isinstance(data, dict):
                    print(f"[DEBUG] Complete response received, data type: {type(data)}")
                    if data.get("choices"):
                        message = data["choices"][0].get("message", {})
                        content = message.get("content")

                        if isinstance(content, str):
                            text_parts.append(content)
                        elif isinstance(content, list):
                            for block in content:
                                if block.get("type") == "text":
                                    text_parts.append(block.get("text", ""))
                                elif block.get("type") == "audio":
                                    audio_chunk = block.get("audio", {}).get("data", "")
                                    if audio_chunk:
                                        audio_parts.append(audio_chunk)
                else:
                    # Response body is not valid JSON dict.
                    # If json.loads returned a string, the server JSON-encoded the SSE text;
                    # use the decoded string (which has real newlines). Otherwise use raw body.
                    sse_text = data if isinstance(data, str) else response_body
                    print(f"[DEBUG] Parsing as SSE (json-decoded={isinstance(data, str)}, length={len(sse_text)})")
                    debug_chunk_count = 0
                    debug_skipped = 0
                    for line in sse_text.splitlines():
                        line = line.strip()
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data: "):
                            line = line[6:]
                        if line == "[DONE]":
                            break
                        try:
                            chunk = json.loads(line)
                            if not isinstance(chunk, dict):
                                debug_skipped += 1
                                continue
                            choices = chunk.get("choices", [])
                            if not choices:
                                debug_skipped += 1
                                continue
                            choice = choices[0]
                            # Print first 3 chunks for debugging
                            if debug_chunk_count < 3:
                                print(f"[DEBUG] SSE chunk {debug_chunk_count} keys: {list(choice.keys())}")
                                delta = choice.get("delta", {})
                                print(f"[DEBUG]   delta keys: {list(delta.keys()) if isinstance(delta, dict) else type(delta)}")
                                if isinstance(delta, dict) and delta.get("audio"):
                                    print(f"[DEBUG]   audio keys: {list(delta['audio'].keys())}")
                            debug_chunk_count += 1
                            # Handle both streaming (delta) and non-streaming (message) formats
                            delta = choice.get("delta") or choice.get("message") or {}
                            if isinstance(delta, dict):
                                if delta.get("content"):
                                    text_parts.append(delta["content"])
                                if delta.get("audio"):
                                    audio_data_chunk = delta["audio"].get("data", "")
                                    if audio_data_chunk:
                                        audio_parts.append(audio_data_chunk)
                                    # Also capture transcript as text
                                    transcript = delta["audio"].get("transcript", "")
                                    if transcript:
                                        text_parts.append(transcript)
                        except (json.JSONDecodeError, IndexError, TypeError):
                            debug_skipped += 1
                            continue
                    print(f"[DEBUG] SSE parsing: {debug_chunk_count} chunks parsed, {debug_skipped} skipped")
            else:
                # SSE streaming response
                for line in response.iter_lines():
                    if not line:
                        continue
                    
                    line_str = line.decode('utf-8') if isinstance(line, bytes) else line
                    
                    # Skip "data: " prefix for SSE format
                    if line_str.startswith("data: "):
                        line_str = line_str[6:]
                    
                    if line_str == "[DONE]":
                        break
                    
                    try:
                        chunk = json.loads(line_str)
                        if not isinstance(chunk, dict):
                            continue
                        choices = chunk.get("choices", [])
                        if not choices:
                            continue
                        choice = choices[0]
                        delta = choice.get("delta") or choice.get("message") or {}

                        # Handle text delta
                        if isinstance(delta, dict):
                            if delta.get("content"):
                                text_parts.append(delta["content"])

                            # Handle audio delta
                            if delta.get("audio"):
                                audio_chunk = delta["audio"].get("data", "")
                                if audio_chunk:
                                    audio_parts.append(audio_chunk)
                                # Also capture transcript as text
                                transcript = delta["audio"].get("transcript", "")
                                if transcript:
                                    text_parts.append(transcript)

                    except (json.JSONDecodeError, IndexError, TypeError):
                        continue
            
            text = "".join(text_parts) if text_parts else "[No text response]"
            audio_data = "".join(audio_parts) if audio_parts else None
            
            print(f"[DEBUG] Response complete. Text length: {len(text)}, Audio chunks: {len(audio_parts)}")
            if audio_data:
                print(f"[DEBUG] Total audio data length: {len(audio_data)}")
        
        # For now, don't add assistant to session to avoid 400 errors on next turn
        # The intermediate server handles session state
        # messages.append({"role": "assistant", "content": text})
        
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"API error: {e.response.status_code} - {e.response.text}") from e
    except Exception as e:
        raise RuntimeError(f"Request failed: {e}")
    
    # Save audio from model response if available, otherwise generate TTS
    audio_path = None
    if audio_data:
        # Save audio data from model response (pcm16 format)
        import tempfile
        import wave
        import struct
        
        # Decode base64 pcm16 data
        pcm_data = base64.b64decode(audio_data)
        
        # Convert pcm16 to WAV
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            audio_path = f.name
            
        # Write WAV file with proper header
        with wave.open(audio_path, 'wb') as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 16-bit = 2 bytes
            wav_file.setframerate(24000)  # Standard rate for GPT audio
            wav_file.writeframes(pcm_data)
        
        print(f"[DEBUG] Audio saved from model response (pcm16->wav): {audio_path}")
    elif config.CLOUD_TTS_PROVIDER == "openai" and config.OPENAI_API_KEY:
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
