#!/usr/bin/env python3
"""Main entry point for Spherical Robot Pi5 controller."""
import argparse
import asyncio
import logging
import signal
import sys
from typing import Optional

import numpy as np
import uvicorn

from config import API_HOST, API_PORT

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class SphericalBot:
    """Main application controller."""

    def __init__(
        self,
        enable_video: bool = True,
        enable_audio: bool = True,
        enable_serial: bool = True,
        enable_alarm: bool = True,
        audio_record_device: Optional[str] = None,
        audio_playback_device: Optional[str] = None,
    ):
        self.enable_video = enable_video
        self.enable_audio = enable_audio
        self.enable_serial = enable_serial
        self.enable_alarm = enable_alarm
        self.audio_record_device = audio_record_device
        self.audio_playback_device = audio_playback_device

        # Components
        self.serial_manager = None
        self.video_encoder = None
        self.audio_recorder = None
        self.audio_player = None
        self.alarm_manager = None
        self.image_processor = None
        self.gesture_detector = None
        self.human_tracker = None
        self.yamnet_classifier = None

        self._running = False
        self._tasks: list[asyncio.Task] = []

    def initialize(self) -> bool:
        """Initialize all components."""
        logger.info("Initializing Spherical Robot...")

        try:
            # Initialize serial communication
            if self.enable_serial:
                from esp_serial import SerialManager
                self.serial_manager = SerialManager()
                if not self.serial_manager.connect():
                    logger.warning("Serial connection failed, continuing without ESP32")

            # Initialize video
            if self.enable_video:
                from cv_engine import VideoEncoder, GestureDetector, HumanTracker, EInkImageProcessor

                self.video_encoder = VideoEncoder()
                self.gesture_detector = GestureDetector()
                self.human_tracker = HumanTracker()
                self.image_processor = EInkImageProcessor()

                if not self.video_encoder.start():
                    logger.warning("Video capture failed to start")

            # Initialize audio
            if self.enable_audio:
                from audio import AudioRecorder, AudioPlayer, YAMNetClassifier

                # Use custom devices if specified
                if self.audio_record_device:
                    self.audio_recorder = AudioRecorder(device=self.audio_record_device)
                else:
                    self.audio_recorder = AudioRecorder()

                if self.audio_playback_device:
                    self.audio_player = AudioPlayer(device=self.audio_playback_device)
                else:
                    self.audio_player = AudioPlayer()

                self.yamnet_classifier = YAMNetClassifier()

                try:
                    self.audio_recorder.start()
                except Exception as e:
                    logger.warning(f"Audio recording failed to start: {e}")

            # Initialize alarm manager
            if self.enable_alarm and self.audio_recorder and self.yamnet_classifier:
                from audio import AlarmManager
                self.alarm_manager = AlarmManager(
                    recorder=self.audio_recorder,
                    player=self.audio_player,
                    classifier=self.yamnet_classifier,
                )

            logger.info("Initialization complete")
            return True

        except Exception as e:
            logger.error(f"Initialization failed: {e}")
            return False

    def setup_api(self):
        """Set up API with component references."""
        from api.routes import set_app_state

        set_app_state(
            serial_manager=self.serial_manager,
            video_encoder=self.video_encoder,
            audio_recorder=self.audio_recorder,
            audio_player=self.audio_player,
            alarm_manager=self.alarm_manager,
            image_processor=self.image_processor,
            gesture_detector=self.gesture_detector,
            human_tracker=self.human_tracker,
        )

    async def run_detection_loop(self):
        """Run CV detection loop."""
        if not self.video_encoder or not self.video_encoder.is_running:
            return

        from api.websocket import ws_manager

        logger.info("Starting detection loop")

        while self._running:
            try:
                frame = self.video_encoder.get_frame(timeout=0.5)
                if frame is None:
                    continue

                # Gesture detection
                if self.gesture_detector:
                    gestures = self.gesture_detector.detect(frame)
                    for gesture in gestures:
                        if gesture.gesture.value != "none":
                            await ws_manager.broadcast_gesture(
                                gesture.gesture.value,
                                gesture.confidence,
                                gesture.handedness,
                            )

                # Human tracking
                if self.human_tracker:
                    persons = self.human_tracker.detect(frame)
                    for person in persons:
                        await ws_manager.broadcast_person(
                            person.id,
                            {
                                "x": person.bbox.x,
                                "y": person.bbox.y,
                                "width": person.bbox.width,
                                "height": person.bbox.height,
                            },
                            person.confidence,
                        )

                # Small delay to prevent CPU overload
                await asyncio.sleep(0.033)  # ~30 FPS

            except Exception as e:
                logger.error(f"Detection loop error: {e}")
                await asyncio.sleep(1.0)

    async def run_alarm_loop(self):
        """Run alarm event broadcasting loop."""
        if not self.alarm_manager:
            return

        from api.websocket import ws_manager
        from audio.alarm_manager import AlarmEvent

        def on_alarm(event: AlarmEvent):
            asyncio.create_task(
                ws_manager.broadcast_alarm(
                    event.state.value,
                    event.duration,
                    event.audio_file,
                )
            )

        self.alarm_manager.add_callback(on_alarm)
        self.alarm_manager.start()

        logger.info("Alarm monitoring started")

        while self._running:
            await asyncio.sleep(1.0)

    async def start(self):
        """Start the application."""
        self._running = True

        # Start background tasks
        if self.enable_video:
            self._tasks.append(
                asyncio.create_task(self.run_detection_loop())
            )

        if self.enable_alarm:
            self._tasks.append(
                asyncio.create_task(self.run_alarm_loop())
            )

        logger.info("Background tasks started")

    async def stop(self):
        """Stop the application."""
        logger.info("Stopping Spherical Robot...")
        self._running = False

        # Cancel background tasks
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Stop components
        if self.alarm_manager:
            self.alarm_manager.stop()

        if self.audio_recorder:
            self.audio_recorder.stop()

        if self.audio_player:
            self.audio_player.stop()

        if self.video_encoder:
            self.video_encoder.stop()

        if self.gesture_detector:
            self.gesture_detector.close()

        if self.human_tracker:
            self.human_tracker.close()

        if self.serial_manager:
            self.serial_manager.disconnect()

        logger.info("Spherical Robot stopped")


# Global bot instance
bot: Optional[SphericalBot] = None


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    logger.info(f"Received signal {signum}")
    if bot:
        asyncio.create_task(bot.stop())
    sys.exit(0)


def main():
    """Main entry point."""
    global bot

    parser = argparse.ArgumentParser(description="Spherical Robot Controller")
    parser.add_argument(
        "--host", default=API_HOST, help=f"API host (default: {API_HOST})"
    )
    parser.add_argument(
        "--port", type=int, default=API_PORT, help=f"API port (default: {API_PORT})"
    )
    parser.add_argument(
        "--no-video", action="store_true", help="Disable video capture"
    )
    parser.add_argument(
        "--no-audio", action="store_true", help="Disable audio"
    )
    parser.add_argument(
        "--no-serial", action="store_true", help="Disable serial communication"
    )
    parser.add_argument(
        "--no-alarm", action="store_true", help="Disable alarm monitoring"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    parser.add_argument(
        "--audio-in", type=str, default=None,
        help="Audio input device (e.g., 'plughw:2,0' or 'default')"
    )
    parser.add_argument(
        "--audio-out", type=str, default=None,
        help="Audio output device (e.g., 'plughw:3,0' or 'default')"
    )
    parser.add_argument(
        "--list-audio", action="store_true",
        help="List available audio devices and exit"
    )

    args = parser.parse_args()

    # List audio devices if requested
    if args.list_audio:
        try:
            from utils.audio_detect import list_all_devices
            list_all_devices()
        except ImportError:
            import subprocess
            print("\n=== CAPTURE (Recording) Devices ===")
            subprocess.run(["arecord", "-l"], check=False)
            print("\n=== PLAYBACK Devices ===")
            subprocess.run(["aplay", "-l"], check=False)
            print("\nUse device format: plughw:CARD,DEVICE (e.g., plughw:2,0)")
            print("Or use 'auto' for automatic USB device detection")
        sys.exit(0)

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Create and initialize bot
    bot = SphericalBot(
        enable_video=not args.no_video,
        enable_audio=not args.no_audio,
        enable_serial=not args.no_serial,
        enable_alarm=not args.no_alarm,
        audio_record_device=args.audio_in,
        audio_playback_device=args.audio_out,
    )

    if not bot.initialize():
        logger.error("Failed to initialize, exiting")
        sys.exit(1)

    # Set up API
    bot.setup_api()

    # Create FastAPI app
    from api.routes import create_app
    from api.websocket import ws_manager

    app = create_app()

    # Add WebSocket endpoint
    @app.websocket("/ws")
    async def websocket_endpoint(websocket):
        await ws_manager.handle_connection(websocket)

    # Add Audio WebSocket endpoint for live audio streaming
    from fastapi import WebSocket, WebSocketDisconnect

    @app.websocket("/ws/audio")
    async def audio_websocket(websocket: WebSocket):
        """Bidirectional audio WebSocket - stream from server and receive from client."""
        await websocket.accept()
        logger.info("Audio WebSocket client connected")

        if not bot.audio_recorder or not bot.audio_recorder.is_recording:
            await websocket.close(code=1011, reason="Audio not available")
            return

        try:
            # Send audio metadata first
            await websocket.send_json({
                "type": "audio_config",
                "sample_rate": bot.audio_recorder.sample_rate,
                "channels": 1,
                "format": "int16",
                "noise_reduction": bot.audio_recorder.noise_reduction_enabled,
            })

            # Start playback handler task
            playback_task = asyncio.create_task(
                handle_audio_playback_websocket(websocket, bot.audio_player)
            )

            # Stream audio chunks to client
            buffer = []
            chunk_duration_ms = 100
            buffer_samples = int(bot.audio_recorder.sample_rate * chunk_duration_ms / 1000)
            
            while True:
                chunk = bot.audio_recorder.get_audio(timeout=0.02)
                
                if chunk is not None:
                    buffer.append(chunk)
                    total_samples = sum(len(c) for c in buffer)
                    
                    if total_samples >= buffer_samples:
                        combined = np.concatenate(buffer)
                        samples_to_send = min(len(combined), buffer_samples)
                        await websocket.send_bytes(combined[:samples_to_send].tobytes())
                        
                        if len(combined) > samples_to_send:
                            buffer = [combined[samples_to_send:]]
                        else:
                            buffer = []
                        
                        await asyncio.sleep(0.001)
                else:
                    if buffer:
                        combined = np.concatenate(buffer)
                        await websocket.send_bytes(combined.tobytes())
                        buffer = []
                    await asyncio.sleep(0.01)
                    
        except WebSocketDisconnect:
            logger.info("Audio WebSocket client disconnected")
        except Exception as e:
            logger.error(f"Audio WebSocket error: {e}")
        finally:
            if playback_task:
                playback_task.cancel()
                try:
                    await playback_task
                except asyncio.CancelledError:
                    pass

    async def handle_audio_playback_websocket(websocket: WebSocket, player):
        """Handle incoming audio data from client for playback."""
        try:
            while True:
                # Receive audio data from client
                data = await websocket.receive()
                
                if "bytes" in data:
                    # Received binary audio data
                    audio_bytes = data["bytes"]
                    if player and not player.is_playing:
                        # Play the received audio
                        player.play_audio_data(audio_bytes)
                        logger.debug(f"Playing received audio: {len(audio_bytes)} bytes")
                elif "text" in data:
                    # Handle text commands
                    import json
                    try:
                        msg = json.loads(data["text"])
                        if msg.get("type") == "play_audio":
                            # Client wants to play specific audio
                            pass
                    except json.JSONDecodeError:
                        pass
                        
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"Audio playback handler error: {e}")

    # Start bot tasks on startup
    @app.on_event("startup")
    async def on_startup():
        await bot.start()

    @app.on_event("shutdown")
    async def on_shutdown():
        await bot.stop()

    # Run server
    logger.info(f"Starting API server on {args.host}:{args.port}")
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info" if not args.debug else "debug",
    )


if __name__ == "__main__":
    main()
