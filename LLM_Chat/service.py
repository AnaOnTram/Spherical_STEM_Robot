"""LLM Chat service — spoken conversation via separate ASR, LLM, and TTS services."""

import atexit
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import httpx
import openai
from openai import OpenAI

import config


@dataclass
class LLMChatResult:
    """Result from an oral_chat_with_llm call."""
    session_id: str
    text: str
    transcript: Optional[str] = None
    audio_path: Optional[str] = None
    provider: str = "unknown"
    elapsed_ms: int = 0


# Session management for multi-turn conversations
_sessions: Dict[str, List[dict]] = {}

# Handles to background service processes started by this module
_local_server_process: Optional[subprocess.Popen] = None
_asr_process: Optional[subprocess.Popen] = None
_tts_process: Optional[subprocess.Popen] = None

# Cached path to the pre-generated "Processing. Please wait." audio file
_PROCESSING_AUDIO_PATH = Path(tempfile.gettempdir()) / "spherical_bot_processing.wav"


# ---------------------------------------------------------------------------
# Processing-audio helpers
# ---------------------------------------------------------------------------

def get_processing_audio_path() -> Optional[str]:
    """Return the path to the pre-generated processing notification audio.

    Returns None if the file has not been generated yet.
    """
    if _PROCESSING_AUDIO_PATH.exists():
        return str(_PROCESSING_AUDIO_PATH)
    return None


def generate_processing_audio() -> bool:
    """Pre-generate 'Processing. Please wait.' WAV via Piper TTS and cache it.

    Called once at startup so the file is ready instantly at interaction time
    with no synthesis delay.

    Returns True if the file was created successfully, False otherwise.
    """
    path = synthesize_speech("Processing. Please wait.", timeout=15.0)
    if path:
        import shutil
        shutil.move(path, str(_PROCESSING_AUDIO_PATH))
        print(f"[Preload] Processing audio cached: {_PROCESSING_AUDIO_PATH}")
        return True
    print("[Preload] Could not pre-generate processing audio.")
    return False


# ---------------------------------------------------------------------------
# Service lifecycle
# ---------------------------------------------------------------------------

def shutdown_all_services():
    """Terminate all locally-started services (LLM, ASR, TTS).

    Called automatically on program exit via atexit, and explicitly by
    bot.stop() in main.py.
    """
    global _local_server_process, _asr_process, _tts_process

    print("[LLM Chat] Shutting down all services...")
    for proc, label in [
        (_local_server_process, "LLM server"),
        (_asr_process, "ASR service"),
        (_tts_process, "TTS service"),
    ]:
        if proc is not None:
            print(f"[LLM Chat] Stopping {label}...")
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    _local_server_process = None
    _asr_process = None
    _tts_process = None
    print("[LLM Chat] All services stopped.")


# Last-resort safety net; signal handling and explicit shutdown are in main.py.
atexit.register(shutdown_all_services)


def preload_services():
    """Start ASR, TTS, and LLM server at application startup.

    Call this during startup so all models are loaded before the first user
    request arrives.
    """
    print("[Preload] Starting all LLM services...")
    _ensure_asr_tts_services()
    _ensure_local_server()
    generate_processing_audio()
    print("[Preload] All LLM services are ready.")


# ---------------------------------------------------------------------------
# Internal: service start helpers
# ---------------------------------------------------------------------------

def _is_server_running(host: str = "127.0.0.1", port: int = 8080, timeout: float = 2.0) -> bool:
    """Return True if the llama.cpp server is up and the model is loaded."""
    try:
        response = httpx.get(f"http://{host}:{port}/health", timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


def _start_local_server() -> bool:
    """Start the llama.cpp text inference server if not already running.

    Returns True if the server is running after this call, False otherwise.
    """
    global _local_server_process

    if _is_server_running():
        return True

    if not getattr(config, 'LLM_CHAT_LOCAL_AUTO_START', True):
        return False

    repo_root = Path(__file__).resolve().parent.parent
    search_paths = [
        repo_root / "llama_server" / "llama-server",
        Path.home() / "llama.cpp" / "build" / "bin" / "llama-server",
        Path.home() / "llama.cpp" / "llama-server",
        Path("/usr/local/bin/llama-server"),
        Path("/usr/bin/llama-server"),
    ]

    llama_server = next((p for p in search_paths if p.exists()), None)
    if llama_server is None:
        import shutil
        found = shutil.which("llama-server")
        if found:
            llama_server = Path(found)

    if llama_server is None:
        print("[LLM Chat] llama-server not found. Searched:")
        for p in search_paths:
            print(f"  - {p}")
        print("[LLM Chat] Please build llama.cpp or set LLM_CHAT_LOCAL_AUTO_START=False in config.py")
        return False

    model_path = Path(config.LFM_TEXT_MODEL_PATH)
    if not model_path.exists():
        print(f"[LLM Chat] Model file not found: {model_path}")
        return False

    print(f"[LLM Chat] Using model: {model_path}")

    cmd = [
        str(llama_server),
        "-m", str(model_path),
        "--port", "8080",
        "-c", "4096",
        "--host", "127.0.0.1",
        "--jinja",
    ]
    print(f"[LLM Chat] Starting llama.cpp server...")
    print(f"[LLM Chat] Command: {' '.join(cmd)}")

    log_dir = Path(tempfile.gettempdir()) / "spherical_bot_llm"
    log_dir.mkdir(exist_ok=True)
    stdout_log = log_dir / "llama-server.stdout.log"
    stderr_log = log_dir / "llama-server.stderr.log"

    try:
        stdout_file = open(stdout_log, 'w')
        stderr_file = open(stderr_log, 'w')

        # Add the server directory to LD_LIBRARY_PATH so bundled .so files are found
        server_env = os.environ.copy()
        lib_dir = str(llama_server.parent)
        server_env["LD_LIBRARY_PATH"] = lib_dir + (":" + server_env["LD_LIBRARY_PATH"] if server_env.get("LD_LIBRARY_PATH") else "")

        _local_server_process = subprocess.Popen(
            cmd,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
            cwd=llama_server.parent,
            env=server_env,
        )

        print("[LLM Chat] Waiting for server to start...")
        for _ in range(90):  # Pi is slow to load models
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
                for log_path in (stderr_log, stdout_log):
                    if log_path.exists():
                        content = log_path.read_text()
                        if content:
                            print(f"[LLM Chat] {log_path.name}:\n{content}")
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
    """Start the local Faster Whisper ASR service (port 8803).

    Returns True if the service is running after this call.
    """
    global _asr_process

    try:
        httpx.get(config.LOCAL_ASR_URL.replace('/recognize', ''), timeout=2.0)
        return True
    except Exception:
        pass

    repo_root = Path(__file__).resolve().parent.parent
    asr_script = repo_root / "LLM_Chat" / "local" / "fast-whisper-host.py"

    if not asr_script.exists():
        print(f"[ASR] ASR script not found: {asr_script}")
        return False

    print("[ASR] Starting local Whisper ASR service...")
    try:
        _asr_process = subprocess.Popen(
            [sys.executable, str(asr_script), "--port", "8803"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        print("[ASR] Waiting for ASR service to start...")
        for _ in range(20):
            time.sleep(1)
            try:
                httpx.post(config.LOCAL_ASR_URL, json={"base64": ""}, timeout=2.0)
                print("[ASR] ASR service is ready!")
                return True
            except Exception:
                if _asr_process.poll() is not None:
                    print(f"[ASR] ASR process exited with code {_asr_process.poll()}")
                    return False

        print("[ASR] ASR service failed to start within 20 seconds")
        return False

    except Exception as e:
        print(f"[ASR] Failed to start ASR service: {e}")
        return False


def _start_tts_service() -> bool:
    """Start the local Piper TTS service (port 8805).

    Returns True if the service is running after this call.
    """
    global _tts_process

    try:
        response = httpx.get(config.LOCAL_TTS_URL, timeout=2.0)
        if response.status_code in [200, 404]:
            return True
    except Exception:
        pass

    repo_root = Path(__file__).resolve().parent.parent
    piper_model = str(repo_root / "LLM_Chat" / "models" / "piper" / config.LOCAL_TTS_VOICE)
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

        print("[TTS] Waiting for TTS service to start...")
        for _ in range(20):
            time.sleep(1)
            try:
                httpx.get(config.LOCAL_TTS_URL, timeout=2.0)
                print("[TTS] TTS service is ready!")
                return True
            except Exception:
                if _tts_process.poll() is not None:
                    print(f"[TTS] TTS process exited with code {_tts_process.poll()}")
                    return False

        print("[TTS] TTS service failed to start within 20 seconds")
        return False

    except Exception as e:
        print(f"[TTS] Failed to start TTS service: {e}")
        return False


def _ensure_local_server():
    """Raise RuntimeError if the llama.cpp server cannot be reached or started."""
    if not _is_server_running():
        if not _start_local_server():
            raise RuntimeError(
                f"Local LLM server is not running at {config.LLM_CHAT_LOCAL_BASE_URL}. "
                "Please start it manually or enable LLM_CHAT_LOCAL_AUTO_START in config.py"
            )


def _ensure_asr_tts_services():
    """Raise RuntimeError if ASR cannot start; warn (non-fatal) if TTS cannot start."""
    try:
        httpx.post(config.LOCAL_ASR_URL, json={"base64": ""}, timeout=2.0)
    except Exception:
        if not _start_asr_service():
            raise RuntimeError("Failed to start ASR service")

    try:
        httpx.get(config.LOCAL_TTS_URL, timeout=2.0)
    except Exception:
        if not _start_tts_service():
            print("[WARNING] TTS service not available — responses will be text only")


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _get_or_create_session(session_id: Optional[str]) -> str:
    if session_id is None:
        session_id = str(uuid.uuid4())
    if session_id not in _sessions:
        _sessions[session_id] = []
    return session_id


def reset_session(session_id: str) -> None:
    """Clear all context for the given session."""
    if session_id in _sessions:
        del _sessions[session_id]


# ---------------------------------------------------------------------------
# Public TTS utility — usable by any service
# ---------------------------------------------------------------------------

def synthesize_speech(text: str, timeout: float = 30.0) -> Optional[str]:
    """Convert text to speech using the local Piper TTS service.

    This is a general-purpose utility and is not limited to the LLM chat
    pipeline — it can be called by alarm announcements, quiz feedback,
    notifications, or any other feature that needs spoken audio output.

    Args:
        text: The text to synthesize.
        timeout: HTTP request timeout in seconds.

    Returns:
        Path to a temporary WAV file containing the spoken audio,
        or None if the TTS service is unavailable or the request fails.
    """
    try:
        response = httpx.post(
            config.LOCAL_TTS_URL,
            json={"text": text},
            timeout=timeout,
        )
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(response.content)
            return f.name
    except Exception as e:
        print(f"[TTS] synthesize_speech failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def oral_chat_with_llm(
    wav_data: bytes,
    session_id: Optional[str] = None,
    reset: bool = False,
    max_tokens: int = 512,
    temperature: float = 0.3,
    system_prompt: Optional[str] = None,
) -> LLMChatResult:
    """Conduct a spoken conversation with the local LLM.

    Pipeline:
        1. ASR  — Faster Whisper converts spoken audio to text (port 8803)
        2. LLM  — llama.cpp generates a text response (port 8080)
        3. TTS  — Piper synthesizes the response to audio (port 8805)

    Args:
        wav_data: WAV audio bytes containing the user's speech.
        session_id: Session ID for multi-turn conversation (None starts a new session).
        reset: If True, clear session context before this turn.
        max_tokens: Maximum tokens for the LLM response.
        temperature: Sampling temperature.
        system_prompt: Override the default system prompt for this session.

    Returns:
        LLMChatResult with the ASR transcript, LLM text response, and path to
        the synthesized audio file.
    """
    start_time = time.time()

    _ensure_asr_tts_services()
    _ensure_local_server()

    session_id = _get_or_create_session(session_id)
    messages = _sessions[session_id]

    # Step 1: ASR — spoken audio → text
    print("[DEBUG] Step 1: ASR with local Whisper...")
    try:
        encoded_audio = base64.b64encode(wav_data).decode("utf-8")
        asr_response = httpx.post(
            config.LOCAL_ASR_URL,
            json={"base64": encoded_audio},
            timeout=30.0,
        )
        asr_response.raise_for_status()
        transcript = asr_response.json().get("recognition", "").strip()
        print(f"[DEBUG] ASR result: '{transcript}'")
        if not transcript:
            raise RuntimeError("ASR returned empty transcript")
    except Exception as e:
        raise RuntimeError(f"ASR failed: {e}")

    # Step 2: LLM — text → text response
    print("[DEBUG] Step 2: LLM inference...")
    if not messages or reset:
        sys_prompt = system_prompt or config.LLM_CHAT_TEXT_SYSTEM_PROMPT
        messages = [{"role": "system", "content": sys_prompt}]

    messages.append({"role": "user", "content": transcript})

    max_history = config.LLM_CHAT_SESSION_MAX_MESSAGES
    if len(messages) > max_history + 1:
        messages = [messages[0]] + messages[-max_history:]
        _sessions[session_id] = messages

    client = OpenAI(base_url=config.LLM_CHAT_LOCAL_BASE_URL, api_key="not-needed")

    try:
        text_parts = []
        print(f"[DEBUG] Sending {len(messages)} messages to LLM")

        max_retries = 10
        for attempt in range(max_retries):
            try:
                stream = client.chat.completions.create(
                    model=config.LLM_CHAT_LOCAL_MODEL or "",
                    messages=messages,
                    temperature=temperature,
                    max_tokens=min(max_tokens, 100),
                    stream=True,
                    extra_body={
                        "reset_context": False,
                        "chat_template_kwargs": {"enable_thinking": False},
                    },
                )
                break
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
                if chunk_count <= 5:
                    print(f"[DEBUG] Chunk {chunk_count}: '{delta.content[:50]}...'")

        text = "".join(text_parts)
        print(f"[DEBUG] LLM response ({len(text)} chars): '{text}'")
    except Exception as e:
        raise RuntimeError(f"LLM failed: {e}")

    # Step 3: TTS — text → spoken audio
    print("[DEBUG] Step 3: TTS with local Piper...")
    audio_path = synthesize_speech(text)
    if audio_path:
        print(f"[DEBUG] TTS saved: {audio_path}")
    else:
        print("[WARNING] TTS produced no audio, returning text only")

    elapsed_ms = int((time.time() - start_time) * 1000)

    return LLMChatResult(
        session_id=session_id,
        text=text,
        transcript=transcript,
        audio_path=audio_path,
        provider="local-asr-llm-tts",
        elapsed_ms=elapsed_ms,
    )
