"""LLM Chat service for local and cloud voice conversations."""

import atexit
import base64
import io
import json
import os
import signal
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

import httpx
import numpy as np
import soundfile as sf
import openai
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
_asr_process: Optional[subprocess.Popen] = None
_tts_process: Optional[subprocess.Popen] = None

# Cached path to the pre-generated "Processing. Please wait." audio file
_PROCESSING_AUDIO_PATH = Path(tempfile.gettempdir()) / "spherical_bot_processing.wav"


def get_processing_audio_path() -> Optional[str]:
    """Return the path to the pre-generated processing notification audio.

    Returns None if the file has not been generated yet.
    """
    if _PROCESSING_AUDIO_PATH.exists():
        return str(_PROCESSING_AUDIO_PATH)
    return None


def generate_processing_audio() -> bool:
    """Pre-generate 'Processing. Please wait.' WAV via Piper TTS and cache it.

    Called once during startup (after TTS service is confirmed running) so
    the file is ready instantly when a request comes in — no synthesis delay
    at interaction time.

    Returns True if the file was created successfully, False otherwise.
    """
    out_path = _PROCESSING_AUDIO_PATH
    try:
        response = httpx.post(
            config.LOCAL_TTS_URL,
            json={"text": "Processing. Please wait."},
            timeout=15.0,
        )
        response.raise_for_status()
        out_path.write_bytes(response.content)
        print(f"[Preload] Processing audio cached: {out_path}")
        return True
    except Exception as e:
        print(f"[Preload] Could not pre-generate processing audio: {e}")
        return False


def shutdown_all_services():
    """Shutdown all local services (LLM, ASR, TTS). Call this on program exit."""
    global _local_server_process, _asr_process, _tts_process
    
    print("[LLM Chat] Shutting down all services...")
    
    # Shutdown LLM server
    if _local_server_process is not None:
        print("[LLM Chat] Stopping LLM server...")
        try:
            _local_server_process.terminate()
            _local_server_process.wait(timeout=5)
        except:
            try:
                _local_server_process.kill()
            except:
                pass
        _local_server_process = None
    
    # Shutdown ASR service
    if _asr_process is not None:
        print("[ASR] Stopping ASR service...")
        try:
            _asr_process.terminate()
            _asr_process.wait(timeout=5)
        except:
            try:
                _asr_process.kill()
            except:
                pass
        _asr_process = None
    
    # Shutdown TTS service
    if _tts_process is not None:
        print("[TTS] Stopping TTS service...")
        try:
            _tts_process.terminate()
            _tts_process.wait(timeout=5)
        except:
            try:
                _tts_process.kill()
            except:
                pass
        _tts_process = None
    
    print("[LLM Chat] All services stopped.")


# Register shutdown handler to stop services on exit.
# Signal handling is managed by main.py; bot.stop() calls shutdown_all_services()
# directly, so we only need atexit as a last-resort safety net.
atexit.register(shutdown_all_services)


def _is_server_running(host: str = "127.0.0.1", port: int = 8080, timeout: float = 2.0) -> bool:
    """Check if the LFM2.5 server is running and the model is fully loaded."""
    try:
        response = httpx.get(f"http://{host}:{port}/health", timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


def _start_local_server(use_text_only: bool = False) -> bool:
    """Start the local LFM2.5 server if not already running.
    
    Args:
        use_text_only: If True, use text-only model without audio components.
        
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
    
    if use_text_only:
        # For text-only mode, use standard llama-server (not liquid-audio)
        llama_server_paths = [
            Path.home() / "llama.cpp" / "build" / "bin" / "llama-server",
            Path.home() / "llama.cpp" / "llama-server",
            Path("/usr/local/bin/llama-server"),
            Path("/usr/bin/llama-server"),
            repo_root / "LLM_Chat" / "server" / "llama-server",  # Fallback to project-local
        ]
    else:
        # For audio mode, use liquid-audio server
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
    
    # Determine which model to use based on mode
    if use_text_only:
        # Use text-only model (no audio components needed)
        model_path = Path(config.LFM_TEXT_MODEL_PATH)
        if model_path.exists():
            print(f"[LLM Chat] Using text-only model: {model_path}")
        else:
            # Fallback to audio model if text-only not available
            print(f"[LLM Chat] Text-only model not found, falling back to audio model")
            model_path = Path(config.LFM_AUDIO_MODEL_PATH)
            use_text_only = False  # Force full mode since we're using audio model
    else:
        # Use full audio model
        model_path = Path(config.LFM_AUDIO_MODEL_PATH)
        print(f"[LLM Chat] Using audio model: {model_path}")
    
    mmproj_path = Path(config.LFM_MMPROJ_PATH)
    
    if not model_path.exists():
        print(f"[LLM Chat] Model file not found: {model_path}")
        return False
    
    # Build command
    cmd = [
        str(llama_server),
        "-m", str(model_path),
        "--port", "8080",
        "-c", "4096",
        "--host", "127.0.0.1",
        "--jinja",
    ]
    
    # Only add audio components for full audio model mode
    if not use_text_only:
        if mmproj_path.exists():
            cmd.extend(["-mm", str(mmproj_path)])
        
        # Optional: Add vocoder if available
        vocoder_path = Path(config.LFM_VOCODER_PATH)
        if vocoder_path.exists():
            cmd.extend(["-mv", str(vocoder_path)])
    
    # Add TTS speaker file (tokenizer) only for audio mode
    if not use_text_only:
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
        for i in range(90):  # Wait up to 90 seconds (model loading on Pi is slow)
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
        print("[LLM Chat] Server failed to start within 90 seconds")
        return False
        
    except Exception as e:
        print(f"[LLM Chat] Failed to start server: {e}")
        import traceback
        traceback.print_exc()
        return False


def _start_asr_service() -> bool:
    """Start the local ASR (Whisper) service.
    
    Returns:
        True if service is running or started successfully.
    """
    global _asr_process
    
    # Check if already running
    try:
        response = httpx.get(config.LOCAL_ASR_URL.replace('/recognize', ''), timeout=2.0)
        if response.status_code == 200:
            return True
    except:
        pass
    
    # Start ASR service
    repo_root = Path(__file__).resolve().parent.parent
    asr_script = repo_root / "LLM_Chat" / "local" / "fast-whisper-host.py"
    
    if not asr_script.exists():
        print(f"[ASR] ASR script not found: {asr_script}")
        return False
    
    print("[ASR] Starting local Whisper ASR service...")
    try:
        log_dir = Path(tempfile.gettempdir()) / "spherical_bot_services"
        log_dir.mkdir(exist_ok=True)
        
        _asr_process = subprocess.Popen(
            [sys.executable, str(asr_script), "--port", "8803"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        
        # Wait for service to be ready
        print("[ASR] Waiting for ASR service to start...")
        for i in range(20):
            time.sleep(1)
            try:
                response = httpx.post(
                    config.LOCAL_ASR_URL,
                    json={"base64": ""},
                    timeout=2.0
                )
                # Even if it returns error, service is running
                print("[ASR] ASR service is ready!")
                return True
            except:
                if _asr_process.poll() is not None:
                    print(f"[ASR] ASR process exited with code {_asr_process.poll()}")
                    return False
        
        print("[ASR] ASR service failed to start within 20 seconds")
        return False
        
    except Exception as e:
        print(f"[ASR] Failed to start ASR service: {e}")
        return False


def _start_tts_service() -> bool:
    """Start the local TTS (Piper) service.
    
    Returns:
        True if service is running or started successfully.
    """
    global _tts_process
    
    # Check if already running
    try:
        response = httpx.get(config.LOCAL_TTS_URL, timeout=2.0)
        if response.status_code in [200, 404]:  # 404 means server is up but endpoint not found
            return True
    except:
        pass
    
    # Check if piper is installed (check for .onnx file)
    piper_model = "/home/admin/piper/en_US-amy-medium"
    if not Path(piper_model + ".onnx").exists():
        print(f"[TTS] Piper model not found: {piper_model}.onnx")
        return False
    
    print("[TTS] Starting local Piper TTS service...")
    try:
        _tts_process = subprocess.Popen(
            [sys.executable, "-m", "piper.http_server", "-m", piper_model, "--port", "8805"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        
        # Wait for service to be ready
        print("[TTS] Waiting for TTS service to start...")
        for i in range(20):
            time.sleep(1)
            try:
                response = httpx.get(config.LOCAL_TTS_URL, timeout=2.0)
                print("[TTS] TTS service is ready!")
                return True
            except:
                if _tts_process.poll() is not None:
                    print(f"[TTS] TTS process exited with code {_tts_process.poll()}")
                    return False
        
        print("[TTS] TTS service failed to start within 20 seconds")
        return False
        
    except Exception as e:
        print(f"[TTS] Failed to start TTS service: {e}")
        return False


def _ensure_local_server(use_text_only: bool = False):
    """Ensure local server is running before making requests.

    Args:
        use_text_only: If True, start server with text-only model.
    """
    if not _is_server_running():
        if not _start_local_server(use_text_only=use_text_only):
            raise RuntimeError(
                f"Local LFM2.5 server is not running at {config.LLM_CHAT_LOCAL_BASE_URL}. "
                f"Please start it manually or enable LLM_CHAT_LOCAL_AUTO_START in config.py"
            )


def preload_services():
    """Pre-load all LLM services (ASR, TTS, LLM server) at startup.

    Call this during application startup so the model is ready
    before the first user request arrives.
    """
    print("[Preload] Starting all LLM services...")
    if getattr(config, 'USE_LOCAL_ASR_TTS', False):
        _ensure_asr_tts_services()
        _ensure_local_server(use_text_only=True)
    else:
        _ensure_local_server(use_text_only=False)
    # Pre-generate the processing notification audio so it is ready instantly
    # at interaction time with no synthesis delay.
    generate_processing_audio()
    print("[Preload] All LLM services are ready.")


def _ensure_asr_tts_services():
    """Ensure ASR and TTS services are running."""
    # Start ASR if not running
    try:
        response = httpx.post(
            config.LOCAL_ASR_URL,
            json={"base64": ""},
            timeout=2.0
        )
    except:
        if not _start_asr_service():
            raise RuntimeError("Failed to start ASR service")
    
    # Start TTS if not running (optional - can work without TTS)
    try:
        response = httpx.get(config.LOCAL_TTS_URL, timeout=2.0)
    except:
        if not _start_tts_service():
            print("[WARNING] TTS service not available, will return text only")
            # Don't raise error - TTS is optional


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


def local_chat_with_separate_asr_tts(
    wav_data: bytes,
    session_id: Optional[str] = None,
    reset: bool = False,
    max_tokens: int = 512,
    temperature: float = 0.3,
    system_prompt: Optional[str] = None,
) -> LLMChatResult:
    """Chat using separate local ASR + LLM + TTS services.
    
    This is an alternative to the full LFM audio model. It uses:
    - Faster Whisper for ASR (port 8803)
    - LFM text-only model for LLM (port 8080)
    - Piper for TTS (port 8805)
    
    Args:
        wav_data: WAV audio data bytes
        session_id: Session ID for multi-turn chat
        reset: Whether to reset session context
        max_tokens: Maximum tokens to generate
        temperature: Sampling temperature
        system_prompt: Optional system prompt override
        
    Returns:
        LLMChatResult with response data
    """
    import httpx
    start_time = time.time()
    
    # Ensure all services are running (ASR, TTS, and LLM)
    print("[DEBUG] Ensuring all services are running...")
    _ensure_asr_tts_services()
    _ensure_local_server(use_text_only=True)  # Use text-only model for separate ASR/TTS
    
    session_id = _get_or_create_session(session_id)
    messages = _sessions[session_id]
    
    # Step 1: ASR - Convert audio to text using local Whisper
    print("[DEBUG] Step 1: ASR with local Whisper...")
    try:
        encoded_audio = base64.b64encode(wav_data).decode("utf-8")
        asr_response = httpx.post(
            config.LOCAL_ASR_URL,
            json={"base64": encoded_audio},
            timeout=30.0,
        )
        asr_response.raise_for_status()
        asr_data = asr_response.json()
        transcript = asr_data.get("recognition", "").strip()
        print(f"[DEBUG] ASR result: '{transcript}'")
        
        if not transcript:
            raise RuntimeError("ASR returned empty transcript")
    except Exception as e:
        raise RuntimeError(f"ASR failed: {e}")
    
    # Step 2: LLM - Send text to local LFM model
    print("[DEBUG] Step 2: LLM with local LFM...")
    
    # Add system prompt if first message or reset
    if not messages or reset:
        sys_prompt = system_prompt or config.LLM_CHAT_TEXT_SYSTEM_PROMPT
        messages = [{"role": "system", "content": sys_prompt}]
    
    # Add user message
    messages.append({"role": "user", "content": transcript})
    
    # Trim session history
    max_messages = config.LLM_CHAT_SESSION_MAX_MESSAGES
    if len(messages) > max_messages + 1:
        messages = [messages[0]] + messages[-(max_messages):]
        _sessions[session_id] = messages
    
    # Call local LLM
    client = OpenAI(
        base_url=config.LLM_CHAT_LOCAL_BASE_URL,
        api_key="not-needed"
    )
    
    try:
        # Use streaming API (required by LFM server)
        text_parts = []
        print(f"[DEBUG] Sending {len(messages)} messages to LLM")

        # Retry loop for transient 503 "Loading model" errors
        max_retries = 10
        for attempt in range(max_retries):
            try:
                stream = client.chat.completions.create(
                    model=config.LLM_CHAT_LOCAL_MODEL or "",
                    messages=messages,
                    temperature=temperature,
                    max_tokens=min(max_tokens, 100),  # Limit to 100 tokens for pre-school answers
                    stream=True,
                    extra_body={"reset_context": False},
                )
                break  # Success, exit retry loop
            except openai.InternalServerError as e:
                if "Loading model" in str(e) and attempt < max_retries - 1:
                    wait = 2 * (attempt + 1)
                    print(f"[DEBUG] Model still loading, retrying in {wait}s (attempt {attempt + 1}/{max_retries})...")
                    time.sleep(wait)
                    continue
                raise

        chunk_count = 0
        for chunk in stream:
            chunk_count += 1
            if chunk.choices[0].finish_reason == "stop":
                print(f"[DEBUG] Stream finished after {chunk_count} chunks")
                break
            delta = chunk.choices[0].delta
            if hasattr(delta, 'content') and delta.content:
                text_parts.append(delta.content)
                if chunk_count <= 5:  # Log first few chunks
                    print(f"[DEBUG] Chunk {chunk_count}: '{delta.content[:50]}...'")

        text = "".join(text_parts)
        print(f"[DEBUG] LLM response ({len(text)} chars): '{text}'")
    except Exception as e:
        raise RuntimeError(f"LLM failed: {e}")
    
    # Step 3: TTS - Convert text to audio using local Piper
    print("[DEBUG] Step 3: TTS with local Piper...")
    audio_path = None
    try:
        tts_response = httpx.post(
            config.LOCAL_TTS_URL,
            json={"text": text},
            timeout=30.0,
        )
        tts_response.raise_for_status()
        
        # Save audio response
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            audio_path = f.name
            f.write(tts_response.content)
        print(f"[DEBUG] TTS saved: {audio_path}")
    except Exception as e:
        print(f"[WARNING] TTS failed: {e}")
        # Continue without audio
    
    elapsed_ms = int((time.time() - start_time) * 1000)
    
    return LLMChatResult(
        session_id=session_id,
        text=text,
        transcript=transcript,
        audio_path=audio_path,
        provider="local-asr-tts",
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
