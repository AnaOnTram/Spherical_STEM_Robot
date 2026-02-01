"""FastAPI routes for robot control."""
import asyncio
import logging
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Response, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Temporary directory for uploaded audio files
TEMP_AUDIO_DIR = Path(tempfile.gettempdir()) / "spherical_bot_audio"
TEMP_AUDIO_DIR.mkdir(exist_ok=True)


# Request/Response models
class MovementRequest(BaseModel):
    """Motor movement request."""
    left_speed: int = Field(..., ge=-255, le=255, description="Left motor speed")
    right_speed: int = Field(..., ge=-255, le=255, description="Right motor speed")
    duration_ms: int = Field(0, ge=0, le=65535, description="Duration in ms (0=indefinite)")


class MovementResponse(BaseModel):
    """Motor movement response."""
    success: bool
    message: str


class DisplayImageRequest(BaseModel):
    """Display image request."""
    image_base64: Optional[str] = None
    text: Optional[str] = None
    pattern: Optional[str] = None  # "checkerboard", "gradient", "border"


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
}


def set_app_state(**kwargs) -> None:
    """Set application state components."""
    _app_state.update(kwargs)


def get_app_state():
    """Get application state."""
    return _app_state


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
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health check
    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "ok"}

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
            esp32_status="connected" if serial_mgr and serial_mgr.ping() else "disconnected",
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

    @app.post("/api/movement/stop", response_model=MovementResponse)
    async def stop():
        """Emergency stop."""
        serial_mgr = _app_state.get("serial_manager")
        if not serial_mgr:
            raise HTTPException(status_code=503, detail="Serial manager not available")

        from esp_serial.commands import CommandBuilder
        from esp_serial.protocol import ResponseStatus

        cmd = CommandBuilder.motor_stop()
        response = await serial_mgr.send_command_async(cmd)

        return MovementResponse(
            success=response.status == ResponseStatus.OK,
            message=response.message or "Stopped",
        )

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
                        raise HTTPException(status_code=400, detail=f"Invalid image data: {img_err}")
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

    @app.post("/api/display/clear", response_model=DisplayResponse)
    async def clear_display():
        """Clear E-Ink display."""
        serial_mgr = _app_state.get("serial_manager")
        if not serial_mgr:
            raise HTTPException(status_code=503, detail="Serial manager not available")

        from esp_serial.commands import CommandBuilder
        from esp_serial.protocol import ResponseStatus

        cmd = CommandBuilder.display_clear()
        response = await serial_mgr.send_command_async(cmd)

        return DisplayResponse(
            success=response.status == ResponseStatus.OK,
            message=response.message or "Display cleared",
        )

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
                executor, 
                lambda: iter(video_enc.generate_mjpeg_stream(quality=70))
            )
            
            frame_count = 0
            last_yield_time = loop.time()
            target_fps = 15  # Target 15 FPS for smooth streaming
            frame_interval = 1.0 / target_fps
            
            while True:
                try:
                    # Get next frame in thread pool
                    jpeg_bytes = await loop.run_in_executor(executor, next, iterator, None)
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
            }
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
                "size": len(content)
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
                "size": len(audio_bytes)
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
        
        return {
            "available": True,
            "is_playing": player.is_playing
        }

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

    return app
