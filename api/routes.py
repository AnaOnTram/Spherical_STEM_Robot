"""FastAPI routes for robot control."""

import asyncio
import logging
import os
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional
import sys
import importlib

from fastapi import FastAPI, File, HTTPException, Response, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

import config
from api.websocket import ws_manager

logger = logging.getLogger(__name__)

# Temporary directory for uploaded audio files
TEMP_AUDIO_DIR = Path(tempfile.gettempdir()) / "spherical_bot_audio"
TEMP_AUDIO_DIR.mkdir(exist_ok=True)

_PROCESSING_INTERVAL = 5.0  # seconds between each "Processing. Please wait." repeat


async def _play_processing_loop(
    audio_player, processing_path: str, done: threading.Event
) -> None:
    """Play 'Processing. Please wait.' repeatedly every 5 s until done is set.

    Runs as an asyncio task concurrent with the LLM pipeline task.
    Each playback is dispatched to a thread so it blocks correctly on ALSA,
    then we wait up to 5 s before replaying — stopping as soon as done is set.
    """
    while not done.is_set():
        await asyncio.to_thread(audio_player.play_file, processing_path, True)
        # Wait up to _PROCESSING_INTERVAL seconds, but wake immediately if done
        try:
            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, done.wait, _PROCESSING_INTERVAL
                ),
                timeout=_PROCESSING_INTERVAL + 0.1,
            )
        except asyncio.TimeoutError:
            pass


def _import_llm_chat():
    """Import LLM chat module with repo root on sys.path (for direct runs)."""
    candidates = ["LLM_Chat.service", "llm_chat.service", "LLM_Chat", "llm_chat"]
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    last_error = None
    for name in candidates:
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as e:
            last_error = e
        except ImportError as e:
            # If package __init__ fails, try service module directly
            last_error = e
            continue
    raise ModuleNotFoundError("Could not import LLM_Chat or llm_chat") from last_error


# Request/Response models
class MovementRequest(BaseModel):
    """Motor movement request."""

    left_speed: int = Field(..., ge=-255, le=255, description="Left motor speed")
    right_speed: int = Field(..., ge=-255, le=255, description="Right motor speed")
    duration_ms: int = Field(
        0, ge=0, le=65535, description="Duration in ms (0=indefinite)"
    )


class MovementResponse(BaseModel):
    """Motor movement response."""

    success: bool
    message: str


class DisplayImageRequest(BaseModel):
    """Display image request."""

    image_base64: Optional[str] = None
    text: Optional[str] = None
    pattern: Optional[str] = None  # "checkerboard", "gradient", "border"


class DisplayLessonRequest(BaseModel):
    """Structured MCQ lesson display request."""

    question: str = Field(..., min_length=1)
    options: List[str] = Field(..., min_length=4, max_length=4)
    title: str = Field("WonderBall STEM", max_length=40)


class DisplayResponse(BaseModel):
    """Display operation response."""

    success: bool
    message: str


class StatusResponse(BaseModel):
    """System status response."""

    connected: bool
    esp32_status: str
    video_running: bool
    audio_running: bool
    alarm_state: str


class AlarmSettingsRequest(BaseModel):
    """Alarm settings request."""

    enabled: bool = True
    threshold: float = Field(0.8, ge=0.0, le=1.0)
    detection_duration: float = Field(3.0, ge=1.0, le=30.0)


class TTSSpeakRequest(BaseModel):
    """Text-to-speech request."""

    text: str = Field(..., min_length=1, description="Text to synthesise")
    voice: str = Field("zh-HK-HiuGaaiNeural", description="Edge TTS voice name")


class QuizStartRequest(BaseModel):
    """Start a quiz session."""

    questions: Optional[List[dict]] = Field(
        None,
        description=(
            "List of question dicts with keys: question, options (4 items), "
            "correct_index (0-3), title (optional). "
            "Omit to use the built-in DEFAULT_QUESTIONS bank."
        ),
    )
    voice: str = Field("en-US-AriaNeural", description="Edge TTS voice for quiz")
    shuffle: bool = Field(False, description="Randomise question order")
    result_delay: float = Field(
        2.5, ge=0.5, le=10.0, description="Seconds to pause after each answer"
    )


class QuizGestureRequest(BaseModel):
    """Manually inject a finger-count answer (for testing without a camera)."""

    finger_count: int = Field(
        ..., ge=1, le=4, description="Number of fingers held up: 1=A, 2=B, 3=C, 4=D"
    )


class LLMChatRequest(BaseModel):
    """LLM voice chat request."""

    duration: float = Field(
        4.0, ge=0.5, le=30.0, description="Recording duration in seconds"
    )
    session_id: Optional[str] = Field(
        None, description="Session id for multi-turn chat"
    )
    reset: bool = Field(False, description="Reset session context")
    max_tokens: int = Field(512, ge=16, le=2048)
    temperature: float = Field(0.3, ge=0.0, le=2.0)
    play_audio: bool = Field(True, description="Play response audio locally")
    system_prompt: Optional[str] = Field(None, description="Override system prompt")


class LLMChatResponse(BaseModel):
    """LLM voice chat response."""

    success: bool
    session_id: str
    text: str
    transcript: Optional[str] = None
    audio_file: Optional[str] = None
    provider: str
    elapsed_ms: int


# Global app state (set by main.py)
_app_state = {
    "serial_manager": None,
    "video_encoder": None,
    "audio_recorder": None,
    "audio_player": None,
    "alarm_manager": None,
    "image_processor": None,
    "gesture_detector": None,
    "human_tracker": None,
    "bootstrap_state": None,
    "menu_state": None,
    "arbitration": None,
}

# Active quiz session state (module-level so main.py detection loop can reach it)
_quiz_state: dict = {"engine": None, "voice": "en-US-AriaNeural", "on_finish": None}

# Latest gesture detection state (updated by detection loop, read by /api/gesture/status)
_gesture_state: dict = {
    "gesture": "none",
    "confidence": 0.0,
    "handedness": "unknown",
    "finger_count": 0,
    "finger_states": [False, False, False, False, False],
    "landmarks": [],
    "hand_up": None,
    "timestamp": None,
}


def update_gesture_state(
    gesture: str,
    confidence: float,
    handedness: str,
    finger_count: int,
    finger_states: list,
    landmarks: list,
    hand_up=None,
) -> None:
    """Called by the detection loop to publish the latest gesture result."""
    from datetime import datetime

    _gesture_state.update(
        {
            "gesture": gesture,
            "confidence": confidence,
            "handedness": handedness,
            "finger_count": finger_count,
            "finger_states": finger_states,
            "landmarks": landmarks,
            "hand_up": hand_up,
            "timestamp": datetime.now().isoformat(),
        }
    )


def set_app_state(**kwargs) -> None:
    """Set application state components."""
    on_quiz_finished = kwargs.pop("on_quiz_finished", None)
    if on_quiz_finished is not None:
        _quiz_state["on_finish"] = on_quiz_finished
    _app_state.update(kwargs)


def get_app_state():
    """Get application state."""
    return _app_state


def _preempt_remote_control(endpoint: str, reason: str):
    """Preempt local control for remote endpoint execution."""
    arbitration = _app_state.get("arbitration")
    if arbitration is None:
        return None

    logger.info("api.preempt_remote endpoint=%s reason=%s", endpoint, reason)
    arbitration.preempt_local(reason)
    return arbitration


def _release_remote_control(arbitration, endpoint: str) -> None:
    """Release remote control and start cooldown when arbitration is available."""
    if arbitration is None:
        return

    logger.info("api.release_remote endpoint=%s", endpoint)
    arbitration.release_remote()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("API server starting up")
    yield
    logger.info("API server shutting down")


def create_app() -> FastAPI:
    """Create FastAPI application."""
    app = FastAPI(
        title="Spherical Robot API",
        description="API for controlling the spherical robot",
        version="1.0.0",
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health check
    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "ok"}

    @app.get("/api/local-ui/status")
    async def get_local_ui_status():
        """Get local UI bootstrap status."""
        bootstrap_state = _app_state.get("bootstrap_state")
        if not bootstrap_state:
            return {
                "phase": "error",
                "home_menu_ready": False,
                "last_error": "bootstrap_state not found in app state",
            }
        
        return bootstrap_state.snapshot()

    @app.get("/api/local-ui/menu/status")
    async def get_menu_status():
        """Get current menu state snapshot for gesture navigation debugging."""
        menu_state = _app_state.get("menu_state")
        if not menu_state:
            raise HTTPException(status_code=404, detail="menu_state not initialized")

        snapshot = menu_state.snapshot()
        return {
            "state": snapshot.state,
            "selected_index": snapshot.selected_index,
            "menu_entries": list(snapshot.menu_entries),
            "locked": snapshot.locked,
            "victory_hold_progress": snapshot.victory_hold_progress,
            "timestamp": snapshot.timestamp,
        }

    @app.get("/api/gesture/status")
    async def get_gesture_status():
        """Latest gesture detection result (poll this for debugging)."""
        return _gesture_state

    @app.get("/api/arbitration/status")
    async def get_arbitration_status():
        """Get current remote/local arbitration status snapshot."""
        arbitration = _app_state.get("arbitration")
        if not arbitration:
            raise HTTPException(status_code=503, detail="Arbitration not available")

        return arbitration.snapshot()

    @app.post("/api/arbitration/force-local")
    async def force_local_control():
        """Force arbitration back to local control (testing/debugging)."""
        arbitration = _app_state.get("arbitration")
        if not arbitration:
            raise HTTPException(status_code=503, detail="Arbitration not available")

        arbitration.force_local()
        return {"status": "local", "message": "Forced to local control"}

    # System status
    @app.get("/api/status", response_model=StatusResponse)
    async def get_status():
        """Get system status."""
        serial_mgr = _app_state.get("serial_manager")
        video_enc = _app_state.get("video_encoder")
        audio_rec = _app_state.get("audio_recorder")
        alarm_mgr = _app_state.get("alarm_manager")

        return StatusResponse(
            connected=serial_mgr.is_connected if serial_mgr else False,
            esp32_status="connected"
            if serial_mgr and serial_mgr.ping()
            else "disconnected",
            video_running=video_enc.is_running if video_enc else False,
            audio_running=audio_rec.is_recording if audio_rec else False,
            alarm_state=alarm_mgr.state.value if alarm_mgr else "disabled",
        )

    # Movement control
    @app.post("/api/movement/move", response_model=MovementResponse)
    async def move(request: MovementRequest):
        """Control motor movement."""
        serial_mgr = _app_state.get("serial_manager")
        if not serial_mgr:
            raise HTTPException(status_code=503, detail="Serial manager not available")

        from esp_serial.commands import CommandBuilder
        from esp_serial.protocol import ResponseStatus

        arbitration = _preempt_remote_control(endpoint="/api/movement/move", reason="movement")
        try:
            cmd = CommandBuilder.motor_velocity(
                request.left_speed,
                request.right_speed,
                request.duration_ms,
            )
            response = await serial_mgr.send_command_async(cmd)

            return MovementResponse(
                success=response.status == ResponseStatus.OK,
                message=response.message or response.status.value,
            )
        finally:
            _release_remote_control(arbitration, endpoint="/api/movement/move")

    @app.post("/api/movement/stop", response_model=MovementResponse)
    async def stop():
        """Emergency stop."""
        serial_mgr = _app_state.get("serial_manager")
        if not serial_mgr:
            raise HTTPException(status_code=503, detail="Serial manager not available")

        from esp_serial.commands import CommandBuilder
        from esp_serial.protocol import ResponseStatus

        arbitration = _preempt_remote_control(endpoint="/api/movement/stop", reason="movement_stop")
        try:
            cmd = CommandBuilder.motor_stop()
            response = await serial_mgr.send_command_async(cmd)

            return MovementResponse(
                success=response.status == ResponseStatus.OK,
                message=response.message or "Stopped",
            )
        finally:
            _release_remote_control(arbitration, endpoint="/api/movement/stop")

    # Display control
    @app.post("/api/display/update", response_model=DisplayResponse)
    async def update_display(request: DisplayImageRequest):
        """Update E-Ink display."""
        serial_mgr = _app_state.get("serial_manager")
        img_processor = _app_state.get("image_processor")

        if not serial_mgr or not img_processor:
            raise HTTPException(status_code=503, detail="Components not available")

        from esp_serial.commands import CommandBuilder
        from esp_serial.protocol import ResponseStatus
        import base64

        arbitration = _preempt_remote_control(endpoint="/api/display/update", reason="display_update")
        try:
            try:
                if request.image_base64:
                    # Decode base64 - this could be either:
                    # 1. Raw binary 1-bit packed data (from frontend dithering)
                    # 2. Base64 encoded image file (PNG/JPG)
                    image_bytes = base64.b64decode(request.image_base64)

                    # Check if it's already the correct size (15000 bytes = pre-processed)
                    if len(image_bytes) == 15000:
                        # Already processed 1-bit packed data
                        packed = image_bytes
                        logger.info(f"Using pre-processed image data: {len(packed)} bytes")
                    else:
                        # Try to open as image file
                        try:
                            from io import BytesIO
                            from PIL import Image

                            img = Image.open(BytesIO(image_bytes))
                            packed = img_processor.process(img)
                            logger.info(f"Processed image file: {len(packed)} bytes")
                        except Exception as img_err:
                            logger.error(f"Failed to open as image: {img_err}")
                            raise HTTPException(
                                status_code=400, detail=f"Invalid image data: {img_err}"
                            )
                elif request.text:
                    packed = img_processor.process_text(request.text)
                elif request.pattern:
                    packed = img_processor.create_pattern(request.pattern)
                else:
                    raise HTTPException(status_code=400, detail="No image data provided")

                cmd = CommandBuilder.display_image(packed)
                response = await serial_mgr.send_command_async(cmd)

                return DisplayResponse(
                    success=response.status == ResponseStatus.OK,
                    message=response.message or "Display updated",
                )
            except Exception as e:
                logger.error(f"Display update error: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        finally:
            _release_remote_control(arbitration, endpoint="/api/display/update")

    @app.post("/api/display/clear", response_model=DisplayResponse)
    async def clear_display():
        """Clear E-Ink display."""
        serial_mgr = _app_state.get("serial_manager")
        if not serial_mgr:
            raise HTTPException(status_code=503, detail="Serial manager not available")

        from esp_serial.commands import CommandBuilder
        from esp_serial.protocol import ResponseStatus

        arbitration = _preempt_remote_control(endpoint="/api/display/clear", reason="display_clear")
        try:
            cmd = CommandBuilder.display_clear()
            response = await serial_mgr.send_command_async(cmd)

            return DisplayResponse(
                success=response.status == ResponseStatus.OK,
                message=response.message or "Display cleared",
            )
        finally:
            _release_remote_control(arbitration, endpoint="/api/display/clear")

    @app.post("/api/display/lesson", response_model=DisplayResponse)
    async def display_lesson(request: DisplayLessonRequest):
        """Render a structured MCQ lesson card on the e-ink display.

        Accepts question + 4 option strings (emojis are stripped server-side).
        Generates a properly laid-out 400×300 1-bit image using a CJK font.
        """
        serial_mgr = _app_state.get("serial_manager")
        img_processor = _app_state.get("image_processor")

        if not serial_mgr or not img_processor:
            raise HTTPException(status_code=503, detail="Components not available")

        from esp_serial.commands import CommandBuilder
        from esp_serial.protocol import ResponseStatus

        arbitration = _preempt_remote_control(endpoint="/api/display/lesson", reason="display_lesson")
        try:
            try:
                packed = img_processor.render_lesson(
                    question=request.question,
                    options=request.options,
                    title=request.title,
                )
                cmd = CommandBuilder.display_image(packed)
                response = await serial_mgr.send_command_async(cmd)
                return DisplayResponse(
                    success=response.status == ResponseStatus.OK,
                    message=response.message or "Lesson displayed",
                )
            except Exception as e:
                logger.error(f"Lesson display error: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        finally:
            _release_remote_control(arbitration, endpoint="/api/display/lesson")

    # Video streaming
    @app.get("/api/stream/video")
    async def video_stream():
        """MJPEG video stream."""
        video_enc = _app_state.get("video_encoder")
        if not video_enc or not video_enc.is_running:
            raise HTTPException(status_code=503, detail="Video not available")

        async def async_mjpeg_stream():
            """Async wrapper for MJPEG stream to prevent blocking."""
            import asyncio
            from concurrent.futures import ThreadPoolExecutor

            loop = asyncio.get_event_loop()
            executor = ThreadPoolExecutor(max_workers=1)

            # Use thread pool to prevent blocking the event loop
            iterator = await loop.run_in_executor(
                executor, lambda: iter(video_enc.generate_mjpeg_stream(quality=70))
            )

            frame_count = 0
            last_yield_time = loop.time()
            target_fps = 15  # Target 15 FPS for smooth streaming
            frame_interval = 1.0 / target_fps

            while True:
                try:
                    # Get next frame in thread pool
                    jpeg_bytes = await loop.run_in_executor(
                        executor, next, iterator, None
                    )
                    if jpeg_bytes is None:
                        break

                    frame_count += 1
                    current_time = loop.time()
                    elapsed = current_time - last_yield_time

                    # Yield frame
                    yield jpeg_bytes
                    last_yield_time = loop.time()

                    # Adaptive delay: if we're ahead of schedule, yield control
                    if elapsed < frame_interval:
                        await asyncio.sleep(0.001)
                    else:
                        # We're behind, just yield briefly
                        await asyncio.sleep(0)

                except StopIteration:
                    break
                except Exception as e:
                    logger.error(f"Video stream error: {e}")
                    break

        return StreamingResponse(
            async_mjpeg_stream(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.get("/api/stream/snapshot")
    async def get_snapshot():
        """Get single frame snapshot."""
        video_enc = _app_state.get("video_encoder")
        if not video_enc or not video_enc.is_running:
            raise HTTPException(status_code=503, detail="Video not available")

        frame = video_enc.get_frame(timeout=2.0)
        if frame is None:
            raise HTTPException(status_code=503, detail="No frame available")

        jpeg = video_enc.encode_frame(frame, quality=90)
        return Response(content=jpeg, media_type="image/jpeg")

    @app.get("/api/stream/info")
    async def get_stream_info():
        """Get video stream information including dimensions after rotation."""
        video_enc = _app_state.get("video_encoder")
        if not video_enc:
            raise HTTPException(status_code=503, detail="Video encoder not available")

        width, height = video_enc.frame_size
        return {
            "width": width,
            "height": height,
            "rotation_degrees": video_enc.config.rotation_degrees,
            "fps": video_enc.config.fps,
            "codec": video_enc.config.codec,
            "is_running": video_enc.is_running,
        }

    # Audio streaming (WAV format for HTTP clients)
    @app.get("/api/stream/audio")
    async def audio_stream():
        """Live audio stream (WAV format). For lower latency, use WebSocket /ws/audio."""
        audio_rec = _app_state.get("audio_recorder")
        if not audio_rec or not audio_rec.is_recording:
            raise HTTPException(status_code=503, detail="Audio not available")

        from audio.recorder import generate_wav_stream

        return StreamingResponse(
            generate_wav_stream(audio_rec),
            media_type="audio/wav",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    @app.get("/api/audio/status")
    async def audio_status():
        """Get audio system status."""
        audio_rec = _app_state.get("audio_recorder")
        if not audio_rec:
            return {"available": False}

        return {
            "available": True,
            "recording": audio_rec.is_recording,
            "sample_rate": audio_rec.sample_rate,
            "channels": audio_rec.channels,
            "noise_reduction": audio_rec.noise_reduction_enabled,
            "dual_mic": audio_rec.dual_mic_enabled,
        }

    # Audio control
    @app.post("/api/audio/play")
    async def play_audio(file_path: str):
        """Play audio file."""
        player = _app_state.get("audio_player")
        if not player:
            raise HTTPException(status_code=503, detail="Audio player not available")

        try:
            player.play_file(file_path)
            return {"success": True, "message": "Playing audio"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/audio/stop")
    async def stop_audio():
        """Stop audio playback."""
        player = _app_state.get("audio_player")
        if player:
            player.stop()
        return {"success": True, "message": "Audio stopped"}

    @app.post("/api/audio/tone")
    async def play_tone(frequency: float = 440.0, duration: float = 1.0):
        """Play a tone."""
        player = _app_state.get("audio_player")
        if not player:
            raise HTTPException(status_code=503, detail="Audio player not available")

        player.play_tone(frequency, duration)
        return {"success": True, "message": f"Playing {frequency}Hz tone"}

    @app.post("/api/audio/upload")
    async def upload_audio(file: UploadFile = File(...)):
        """Upload and play audio file (MP3, WAV, OGG, etc.)."""
        player = _app_state.get("audio_player")
        if not player:
            raise HTTPException(status_code=503, detail="Audio player not available")

        try:
            # Generate unique filename
            file_id = str(uuid.uuid4())
            file_ext = Path(file.filename).suffix.lower()
            if not file_ext:
                file_ext = ".wav"

            temp_path = TEMP_AUDIO_DIR / f"{file_id}{file_ext}"

            # Save uploaded file
            content = await file.read()
            with open(temp_path, "wb") as f:
                f.write(content)

            logger.info(f"Uploaded audio: {file.filename} ({len(content)} bytes)")

            # Play the file
            player.play_file(str(temp_path))

            return {
                "success": True,
                "message": f"Playing {file.filename}",
                "filename": file.filename,
                "size": len(content),
            }
        except Exception as e:
            logger.error(f"Audio upload error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/audio/play-base64")
    async def play_audio_base64(request: dict):
        """Play audio from base64-encoded data."""
        player = _app_state.get("audio_player")
        if not player:
            raise HTTPException(status_code=503, detail="Audio player not available")

        try:
            import base64

            audio_data = request.get("audio_data")
            format_type = request.get("format", "wav")

            if not audio_data:
                raise HTTPException(status_code=400, detail="No audio data provided")

            # Decode base64
            audio_bytes = base64.b64decode(audio_data)

            # Save to temp file
            file_id = str(uuid.uuid4())
            temp_path = TEMP_AUDIO_DIR / f"{file_id}.{format_type}"

            with open(temp_path, "wb") as f:
                f.write(audio_bytes)

            # Play the file
            player.play_file(str(temp_path))

            return {
                "success": True,
                "message": "Playing audio",
                "size": len(audio_bytes),
            }
        except Exception as e:
            logger.error(f"Base64 audio playback error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/audio/playback-status")
    async def get_playback_status():
        """Get current playback status."""
        player = _app_state.get("audio_player")
        if not player:
            return {"available": False}

        return {"available": True, "is_playing": player.is_playing}

    # Text-to-speech (server-side, supports Cantonese and all Edge TTS languages)
    @app.post("/api/tts/speak")
    async def tts_speak(request: TTSSpeakRequest):
        """Synthesise text with Edge TTS and play it on the robot speaker.

        Uses Microsoft Edge TTS (requires internet).  Default voice is
        zh-HK-HiuGaaiNeural (Cantonese).  Other voices:
          zh-HK-WanLungNeural  – Cantonese male
          en-US-AriaNeural     – English female
        """
        player = _app_state.get("audio_player")
        if not player:
            raise HTTPException(status_code=503, detail="Audio player not available")

        try:
            import edge_tts
        except ImportError:
            raise HTTPException(
                status_code=503,
                detail="edge-tts not installed. Run: pip install edge-tts",
            )

        file_id = str(uuid.uuid4())
        temp_path = TEMP_AUDIO_DIR / f"tts_{file_id}.mp3"

        try:
            communicate = edge_tts.Communicate(request.text, request.voice)
            await communicate.save(str(temp_path))
        except Exception as e:
            logger.error(f"Edge TTS synthesis error: {e}")
            raise HTTPException(status_code=500, detail=f"TTS synthesis failed: {e}")

        # Play in background thread and clean up the temp file when done
        def _play_and_cleanup():
            try:
                player.play_file(str(temp_path), blocking=True)
            finally:
                temp_path.unlink(missing_ok=True)

        threading.Thread(target=_play_and_cleanup, daemon=True).start()

        return {"success": True, "message": f"Speaking ({request.voice})"}

    # LLM voice chat
    @app.post("/api/llm_chat/local", response_model=LLMChatResponse)
    async def llm_chat_local(request: LLMChatRequest):
        """Spoken conversation: record mic → Whisper ASR → LLM → Piper TTS → speaker."""
        audio_rec = _app_state.get("audio_recorder")
        audio_player = _app_state.get("audio_player")
        if not audio_rec or not audio_rec.is_recording:
            raise HTTPException(status_code=503, detail="Audio recorder not available")

        llm_chat = _import_llm_chat()

        if request.reset and request.session_id:
            llm_chat.reset_session(request.session_id)

        # Record audio to temp WAV
        record_dir = TEMP_AUDIO_DIR / "llm_chat"
        record_dir.mkdir(exist_ok=True)
        record_path = record_dir / f"input_{uuid.uuid4().hex}.wav"

        await asyncio.to_thread(
            audio_rec.record_to_file, str(record_path), request.duration
        )
        wav_bytes = record_path.read_bytes()

        # Play "Processing. Please wait." every 5s until the pipeline finishes.
        processing_path = llm_chat.get_processing_audio_path()
        done_event = threading.Event()

        if audio_player and processing_path:
            processing_task = asyncio.create_task(
                _play_processing_loop(audio_player, processing_path, done_event)
            )
        else:
            processing_task = None

        try:
            result = await asyncio.to_thread(
                llm_chat.oral_chat_with_llm,
                wav_bytes,
                request.session_id,
                request.reset,
                request.max_tokens,
                request.temperature,
                request.system_prompt,
            )
        finally:
            done_event.set()
            if processing_task:
                await processing_task

        if request.play_audio and result.audio_path and audio_player:
            audio_player.play_file(result.audio_path)

        return LLMChatResponse(
            success=True,
            session_id=result.session_id,
            text=result.text,
            transcript=result.transcript,
            audio_file=result.audio_path,
            provider=result.provider,
            elapsed_ms=result.elapsed_ms,
        )

    # Alarm control
    @app.get("/api/alarm/status")
    async def get_alarm_status():
        """Get alarm status."""
        alarm_mgr = _app_state.get("alarm_manager")
        if not alarm_mgr:
            return {"enabled": False, "state": "disabled"}

        return {
            "enabled": alarm_mgr.is_running,
            "state": alarm_mgr.state.value,
        }

    @app.post("/api/alarm/enable")
    async def enable_alarm():
        """Enable alarm monitoring."""
        alarm_mgr = _app_state.get("alarm_manager")
        if not alarm_mgr:
            raise HTTPException(status_code=503, detail="Alarm manager not available")

        alarm_mgr.start()
        return {"success": True, "message": "Alarm monitoring enabled"}

    @app.post("/api/alarm/disable")
    async def disable_alarm():
        """Disable alarm monitoring."""
        alarm_mgr = _app_state.get("alarm_manager")
        if alarm_mgr:
            alarm_mgr.stop()
        return {"success": True, "message": "Alarm monitoring disabled"}

    @app.post("/api/alarm/acknowledge")
    async def acknowledge_alarm():
        """Acknowledge alarm."""
        alarm_mgr = _app_state.get("alarm_manager")
        if alarm_mgr:
            alarm_mgr.acknowledge()
        return {"success": True, "message": "Alarm acknowledged"}

    @app.post("/api/alarm/test")
    async def test_alarm():
        """Trigger test alarm."""
        alarm_mgr = _app_state.get("alarm_manager")
        if not alarm_mgr:
            raise HTTPException(status_code=503, detail="Alarm manager not available")

        alarm_mgr.test_alarm()
        return {"success": True, "message": "Test alarm triggered"}

    @app.get("/api/alarm/config")
    async def get_alarm_config():
        """Get alarm configuration."""
        alarm_mgr = _app_state.get("alarm_manager")
        if not alarm_mgr:
            raise HTTPException(status_code=503, detail="Alarm manager not available")

        return alarm_mgr.get_config()

    @app.post("/api/alarm/config")
    async def update_alarm_config(request: AlarmSettingsRequest):
        """Update alarm configuration."""
        alarm_mgr = _app_state.get("alarm_manager")
        if not alarm_mgr:
            raise HTTPException(status_code=503, detail="Alarm manager not available")

        alarm_mgr.update_config(
            detection_duration=request.detection_duration,
            threshold=request.threshold,
        )
        return {"success": True, "message": "Configuration updated"}

    @app.get("/api/alarm/history")
    async def get_alarm_history(limit: int = 100, event_type: str = None):
        """Get detection history."""
        alarm_mgr = _app_state.get("alarm_manager")
        if not alarm_mgr:
            raise HTTPException(status_code=503, detail="Alarm manager not available")

        history = alarm_mgr.get_detection_history(limit=limit)

        # Filter by event type if specified
        if event_type:
            history = [h for h in history if h.event_type == event_type]

        return {
            "events": [h.to_dict() for h in history],
            "count": len(history),
        }

    @app.post("/api/alarm/history/clear")
    async def clear_alarm_history():
        """Clear detection history."""
        alarm_mgr = _app_state.get("alarm_manager")
        if not alarm_mgr:
            raise HTTPException(status_code=503, detail="Alarm manager not available")

        alarm_mgr.clear_detection_history()
        return {"success": True, "message": "History cleared"}

    @app.post("/api/alarm/webhook")
    async def set_alarm_webhook(url: str):
        """Configure webhook URL for notifications."""
        from config import NOTIFICATION_WEBHOOK_URL
        import config

        # Update the config module
        config.NOTIFICATION_WEBHOOK_URL = url if url else None

        # Update the notification manager if available
        alarm_mgr = _app_state.get("alarm_manager")
        if alarm_mgr and hasattr(alarm_mgr, "_notification_manager"):
            alarm_mgr._notification_manager.webhook_url = url if url else None

        return {
            "success": True,
            "message": f"Webhook {'set' if url else 'cleared'}",
            "url": url if url else None,
        }

    # System control
    @app.post("/api/system/ping")
    async def ping_esp32():
        """Ping ESP32."""
        serial_mgr = _app_state.get("serial_manager")
        if not serial_mgr:
            raise HTTPException(status_code=503, detail="Serial manager not available")

        success = serial_mgr.ping()
        return {"success": success, "message": "pong" if success else "no response"}

    @app.post("/api/system/reset")
    async def reset_esp32():
        """Reset ESP32."""
        serial_mgr = _app_state.get("serial_manager")
        if not serial_mgr:
            raise HTTPException(status_code=503, detail="Serial manager not available")

        from esp_serial.commands import CommandBuilder
        from esp_serial.protocol import ResponseStatus

        cmd = CommandBuilder.system_reset()
        response = await serial_mgr.send_command_async(cmd)

        return {
            "success": response.status == ResponseStatus.OK,
            "message": response.message or "Reset sent",
        }

    # -----------------------------------------------------------------------
    # Quiz endpoints  (education/quiz_engine.py)
    # -----------------------------------------------------------------------

    async def _finalize_quiz_session() -> None:
        """Clear active quiz engine and notify lifecycle listener when a session ends."""
        _quiz_state["engine"] = None
        on_finish = _quiz_state.get("on_finish")
        if callable(on_finish):
            try:
                on_finish()
            except Exception as exc:
                logger.error("quiz.on_finish_callback_failed err=%s", exc)

    @app.post("/api/quiz/start")
    async def quiz_start(request: QuizStartRequest):
        """Start an interactive MCQ quiz session.

        Renders each question on the E-ink display and reads it aloud via
        Edge TTS.  Answers are accepted by holding up fingers in front of
        the camera (MediaPipe finger counting):
          1 finger → A   2 fingers → B   3 fingers → C   4 fingers → D
        """
        from education.quiz_engine import QuizEngine, QuizQuestion, DEFAULT_QUESTIONS

        img_processor = _app_state.get("image_processor")
        serial_mgr = _app_state.get("serial_manager")
        player = _app_state.get("audio_player")

        # Stop any running quiz first
        if _quiz_state["engine"] is not None:
            await _quiz_state["engine"].stop()
            _quiz_state["engine"] = None

        # Build question list
        if request.questions:
            try:
                questions = [
                    QuizQuestion(
                        question=q["question"],
                        options=q["options"],
                        correct_index=q["correct_index"],
                        title=q.get("title", "WonderBall STEM"),
                    )
                    for q in request.questions
                ]
            except (KeyError, ValueError) as exc:
                raise HTTPException(
                    status_code=422, detail=f"Invalid question format: {exc}"
                )
        else:
            questions = list(DEFAULT_QUESTIONS)

        _quiz_state["voice"] = request.voice

        # --- TTS function (async, non-blocking) ---
        async def _tts(text: str) -> None:
            if not player:
                return
            try:
                import edge_tts
            except ImportError:
                logger.warning("edge-tts not installed; quiz will run silently")
                return
            file_id = str(uuid.uuid4())
            temp_path = TEMP_AUDIO_DIR / f"quiz_tts_{file_id}.mp3"
            try:
                communicate = edge_tts.Communicate(text, request.voice)
                await communicate.save(str(temp_path))
                # Play blocking in thread so TTS finishes before next step
                await asyncio.to_thread(player.play_file, str(temp_path), True)
            except Exception as exc:
                logger.error(f"Quiz TTS error: {exc}")
            finally:
                temp_path.unlink(missing_ok=True)

        # --- Display function ---
        async def _display(question: str, options: list, title: str) -> None:
            if not img_processor or not serial_mgr:
                return
            try:
                from esp_serial.commands import CommandBuilder

                packed = img_processor.render_lesson(
                    question=question, options=options, title=title
                )
                cmd = CommandBuilder.display_image(packed)
                await serial_mgr.send_command_async(cmd)
            except Exception as exc:
                logger.error(f"Quiz display error: {exc}")

        engine = QuizEngine(
            questions=questions,
            tts_fn=_tts,
            display_fn=_display,
            result_delay=request.result_delay,
            shuffle=request.shuffle,
        )
        _quiz_state["engine"] = engine

        async def _run_engine() -> None:
            try:
                await engine.start()
            finally:
                if _quiz_state.get("engine") is engine:
                    await _finalize_quiz_session()

        # Fire quiz in background so this endpoint returns immediately
        asyncio.create_task(_run_engine())

        return {
            "success": True,
            "message": f"Quiz started with {len(questions)} questions",
            "total_questions": len(questions),
        }

    @app.get("/api/quiz/status")
    async def quiz_status():
        """Return current quiz state and score."""
        engine = _quiz_state.get("engine")
        if engine is None:
            return {"active": False, "state": "idle", "score": 0, "total": 0}

        from education.quiz_engine import QuizState

        q_index = engine._index
        questions = engine._questions
        current_q = questions[q_index] if q_index < len(questions) else None

        return {
            "active": engine.state not in (QuizState.IDLE, QuizState.COMPLETED),
            "state": engine.state.value,
            "question_index": q_index,
            "total_questions": engine.total,
            "score": engine.score,
            "current_question": current_q.question if current_q else None,
            "options": current_q.options if current_q else [],
            "correct_answer_index": (
                questions[q_index - 1].correct_index if q_index > 0 else None
            ),
            "last_answer_correct": engine._last_correct,
        }

    @app.post("/api/quiz/gesture")
    async def quiz_inject_gesture(request: QuizGestureRequest):
        """Manually inject a finger-count answer (useful for testing without camera).

        finger_count: 1=A, 2=B, 3=C, 4=D
        """
        engine = _quiz_state.get("engine")
        if engine is None:
            raise HTTPException(status_code=404, detail="No active quiz session")

        handled = engine.handle_finger_count(request.finger_count)
        return {
            "success": True,
            "handled": handled,
            "message": "Gesture processed"
            if handled
            else "Gesture ignored (not waiting for answer)",
        }

    @app.post("/api/quiz/stop")
    async def quiz_stop():
        """Stop the current quiz session."""
        engine = _quiz_state.get("engine")
        if engine is None:
            return {"success": True, "message": "No active quiz to stop"}
        await engine.stop()
        _quiz_state["engine"] = None
        return {"success": True, "message": "Quiz stopped"}

    # -----------------------------------------------------------------------
    # WebSocket endpoints
    # -----------------------------------------------------------------------

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """Event WebSocket — subscribe to real-time robot events."""
        await ws_manager.handle_connection(websocket)

    @app.websocket("/ws/audio")
    async def websocket_audio(websocket: WebSocket):
        """Audio WebSocket — streams raw 16-bit mono PCM.

        Connection flow:
          1. Server sends a JSON ``audio_config`` message.
          2. Server sends binary Int16 PCM chunks continuously.
        """
        import json as _json

        try:
            await websocket.accept()
        except Exception:
            return

        audio_rec = _app_state.get("audio_recorder")
        if not audio_rec or not audio_rec.is_recording:
            await websocket.send_text(_json.dumps({"error": "Audio not available"}))
            await websocket.close()
            return

        await websocket.send_text(
            _json.dumps(
                {
                    "type": "audio_config",
                    "sample_rate": audio_rec.sample_rate,
                    "channels": 1,
                    "format": "int16",
                }
            )
        )

        # Use a per-client asyncio queue fed by a recorder callback so this
        # WebSocket gets its own copy of every chunk without competing with the
        # ASR pipeline, which also drains the shared _audio_queue.
        client_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        loop = asyncio.get_event_loop()

        def _on_chunk(chunk) -> None:
            data = chunk.tobytes()
            try:
                loop.call_soon_threadsafe(client_queue.put_nowait, data)
            except Exception:
                pass  # queue full or loop closed — drop the frame

        audio_rec.add_callback(_on_chunk)
        try:
            while True:
                data = await asyncio.wait_for(client_queue.get(), timeout=2.0)
                await websocket.send_bytes(data)
        except Exception:
            pass
        finally:
            audio_rec.remove_callback(_on_chunk)

    return app
