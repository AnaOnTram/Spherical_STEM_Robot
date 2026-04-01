#!/usr/bin/env python3
"""Main entry point for Spherical Robot Pi5 controller."""
import argparse
import asyncio
import logging
import signal
import sys
from typing import Awaitable, Callable, Optional

import numpy as np
import uvicorn

from config import (
    API_HOST,
    API_PORT,
    BOOTSTRAP_MAX_ATTEMPTS,
    BOOTSTRAP_PUBLISH_TIMEOUT_SECONDS,
    BOOTSTRAP_READINESS_TIMEOUT_SECONDS,
    BOOTSTRAP_RENDER_TIMEOUT_SECONDS,
    BOOTSTRAP_REQUIRED_COMPONENTS,
    REMOTE_PREEMPT_COOLDOWN_SECONDS,
)
from local_ui.arbitration import ArbitrationController
from local_ui.bootstrap import BootstrapState, run_bootstrap_flow

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
        self.menu_state = None
        self.arbitration_controller = None

        self._running = False
        self._tasks: list[asyncio.Task] = []
        self.bootstrap_state = BootstrapState()
        self._stem_session_active = False

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

            # Initialize remote/local arbitration (available even if serial/video are disabled)
            try:
                from api.websocket import ws_manager

                self.arbitration_controller = ArbitrationController(
                    cooldown_seconds=REMOTE_PREEMPT_COOLDOWN_SECONDS,
                    serial_manager=self.serial_manager,
                    image_processor=self.image_processor,
                    ws_manager=ws_manager,
                )
            except Exception as exc:
                logger.error("arbitration.initialization_failed: %s", exc)
                self.arbitration_controller = None

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

            # Initialize LLM services (ASR, LLM, TTS)
            try:
                from LLM_Chat.service import preload_services
                import concurrent.futures
                logger.info("Starting LLM services (ASR, LLM, TTS)...")
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(preload_services)
                    future.result()  # Block until services are ready
                logger.info("LLM services started successfully")
            except Exception as e:
                logger.warning(f"LLM services failed to start: {e} (will retry on first request)")

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
            bootstrap_state=self.bootstrap_state,
            menu_state=self.menu_state,
            arbitration=self.arbitration_controller,
            on_quiz_finished=self._handle_stem_session_finished,
        )

    async def _publish_boot_home_menu(self, image_payload: bytes):
        """Publish boot home menu payload through the existing serial command path."""
        if not self.serial_manager:
            raise RuntimeError("serial manager unavailable for home-menu publish")

        from esp_serial.commands import CommandBuilder

        command = CommandBuilder.display_image(image_payload)
        return await self.serial_manager.send_command_async(command)

    async def run_bootstrap(self) -> BootstrapState:
        """Run deterministic local UI bootstrap flow before background work starts."""
        components = {
            "serial_manager": self.serial_manager,
            "image_processor": self.image_processor,
        }

        def _render_home_menu(entries):
            if not self.image_processor:
                raise RuntimeError("image processor unavailable for home-menu render")
            return self.image_processor.render_home_menu(entries)

        for attempt in range(1, BOOTSTRAP_MAX_ATTEMPTS + 1):
            logger.info(
                "bootstrap.attempt=%s required=%s",
                attempt,
                list(BOOTSTRAP_REQUIRED_COMPONENTS),
            )
            await run_bootstrap_flow(
                state=self.bootstrap_state,
                components=components,
                render_home_menu=_render_home_menu,
                publish_image=self._publish_boot_home_menu,
                required_keys=BOOTSTRAP_REQUIRED_COMPONENTS,
                readiness_timeout_s=BOOTSTRAP_READINESS_TIMEOUT_SECONDS,
                render_timeout_s=BOOTSTRAP_RENDER_TIMEOUT_SECONDS,
                publish_timeout_s=BOOTSTRAP_PUBLISH_TIMEOUT_SECONDS,
            )

            if self.bootstrap_state.home_menu_ready:
                logger.info("bootstrap.ready after attempt=%s", attempt)
                # Instantiate menu state machine after successful bootstrap
                self._initialize_menu()
                break

            logger.warning(
                "bootstrap.not_ready attempt=%s phase=%s error=%s",
                attempt,
                self.bootstrap_state.snapshot().get("phase"),
                self.bootstrap_state.snapshot().get("last_error"),
            )
            if self.bootstrap_state.phase.value == "error":
                break

        return self.bootstrap_state

    async def _launch_stem_education(self):
        """Launch STEM education flow using the existing quiz API engine path."""
        from api.routes import QuizStartRequest, quiz_start

        self._stem_session_active = True
        logger.info("stem_session_started source=local_menu")
        await quiz_start(QuizStartRequest())

    def _handle_stem_session_finished(self) -> None:
        """Finalize STEM session and deterministically restore home menu interaction state."""
        if not self._stem_session_active:
            return

        self._stem_session_active = False
        logger.info("stem_session_finished source=quiz_engine")

        if self.menu_state is not None and hasattr(self.menu_state, "reset_after_external_session"):
            self.menu_state.reset_after_external_session()
            logger.info("menu_restored source=stem_exit")

    async def _handle_local_stem_commit(
        self,
        selected_item: str,
        launch_fn: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> bool:
        """Dispatch one-shot local STEM commit with arbitration-aware logging."""
        normalized = selected_item.strip().lower()
        if normalized != "stem":
            logger.info(
                "menu.stem_dispatch result=ignored_non_stem item=%s",
                selected_item,
            )
            return False

        arbitration_state = "none"
        arbitration_reason = "none"
        local_allowed = True
        if self.arbitration_controller is not None:
            snapshot = self.arbitration_controller.snapshot()
            arbitration_state = snapshot.get("state", "unknown")
            arbitration_reason = snapshot.get("reason", "unknown")
            local_allowed = self.arbitration_controller.is_local_allowed()

        if not local_allowed:
            logger.info(
                "menu.stem_dispatch result=blocked_by_arbitration item=%s arbitration_state=%s arbitration_reason=%s",
                selected_item,
                arbitration_state,
                arbitration_reason,
            )
            return False

        launch = launch_fn
        if launch is None:
            async def _default_launch(_: str) -> None:
                await self._launch_stem_education()

            launch = _default_launch

        await launch(selected_item)
        logger.info(
            "menu.stem_dispatch result=launched item=%s arbitration_state=%s arbitration_reason=%s",
            selected_item,
            arbitration_state,
            arbitration_reason,
        )
        return True

    def _initialize_menu(self):
        """Initialize menu state machine after bootstrap completes."""
        from local_ui.bootstrap import BASELINE_HOME_MENU_ENTRIES
        from local_ui.menu_state import MenuStateMachine
        from api.routes import set_app_state

        try:
            self.menu_state = MenuStateMachine(
                menu_entries=BASELINE_HOME_MENU_ENTRIES,
                audio_player=self.audio_player,
                serial_manager=self.serial_manager,
                image_processor=self.image_processor,
                arbitration=self.arbitration_controller,
            )
            logger.info("menu.initialized entries=%s", BASELINE_HOME_MENU_ENTRIES)
            set_app_state(menu_state=self.menu_state, arbitration=self.arbitration_controller, on_quiz_finished=self._handle_stem_session_finished)
        except Exception as e:
            logger.error(f"menu.initialization_failed: {e}")
            self.menu_state = None
        self._is_display_updating = False
        self._pending_display_update = False

    async def run_detection_loop(self):
        """Run CV detection loop."""
        if not self.video_encoder or not self.video_encoder.is_running:
            return

        from api.routes import update_gesture_state, _quiz_state
        from cv_engine.gesture_detector import Gesture

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
                        lm = gesture.hand_landmarks
                        finger_count = -1
                        finger_states = [False] * 5
                        hand_up = None
                        landmarks_json = []

                        if lm is not None and self.gesture_detector._use_mediapipe:
                            try:
                                finger_states = self.gesture_detector.get_finger_states(lm)
                                finger_count = sum(finger_states[1:])  # skip thumb
                                hand_up = lm[0].y > lm[9].y
                                landmarks_json = [
                                    {"x": p.x, "y": p.y, "z": p.z} for p in lm
                                ]
                            except Exception:
                                pass

                        update_gesture_state(
                            gesture.gesture.value,
                            gesture.confidence,
                            gesture.handedness,
                            finger_count,
                            finger_states,
                            landmarks_json,
                            hand_up,
                        )

                        # Menu gesture handling (if menu is active)
                        menu_active = bool(self.menu_state and self.menu_state.is_active)
                        if menu_active:
                            relevant_gestures = (
                                Gesture.THUMBS_UP,
                                Gesture.THUMBS_DOWN,
                                Gesture.PEACE,  # Victory
                                Gesture.OPEN_PALM,
                            )
                            if gesture.gesture in relevant_gestures:
                                self.menu_state.handle_gesture(
                                    gesture.gesture,
                                    gesture.confidence,
                                )

                                if self.menu_state.consume_commit_requested():
                                    selected_item = self.menu_state.menu_entries[self.menu_state.selected_index]
                                    if selected_item.strip().lower() == "stem":
                                        asyncio.create_task(self._handle_local_stem_commit(selected_item))
                                    else:
                                        async def do_commit():
                                            self._is_display_updating = True
                                            try:
                                                await self.menu_state.commit_selection()
                                            finally:
                                                self._is_display_updating = False

                                        asyncio.create_task(do_commit())
                                    
                                elif self.menu_state.consume_navigation_requested() or self._pending_display_update:
                                    if self._is_display_updating:
                                        self._pending_display_update = True
                                        continue

                                    async def do_sync():
                                        self._is_display_updating = True
                                        self._pending_display_update = False
                                        try:
                                            await self.menu_state.sync_display()
                                        finally:
                                            self._is_display_updating = False
                                            # If another update became pending while we were sync'ing,
                                            # it will be picked up in the next loop iteration.
                                            
                                    asyncio.create_task(do_sync())

                        # Skip quiz gesture handling while menu is active
                        if not menu_active and finger_count >= 1:
                            try:
                                _engine = _quiz_state.get("engine")
                                if _engine is not None:
                                    _engine.handle_finger_count(finger_count)
                            except Exception:
                                pass

                # Human tracking (detection only — no WebSocket broadcast)
                if self.human_tracker:
                    self.human_tracker.detect(frame)

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

        loop = asyncio.get_event_loop()

        def on_alarm(event: AlarmEvent):
            asyncio.run_coroutine_threadsafe(
                ws_manager.broadcast_alarm(
                    event.state.value,
                    event.duration,
                    event.audio_file,
                ),
                loop,
            )

        self.alarm_manager.add_callback(on_alarm)
        self.alarm_manager.start()

        logger.info("Alarm monitoring started")

        while self._running:
            await asyncio.sleep(1.0)

    async def start(self):
        """Start the application."""
        await self.run_bootstrap()
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

        # Stop LLM services (ASR, LLM, TTS)
        try:
            from LLM_Chat.service import shutdown_all_services
            shutdown_all_services()
        except Exception as e:
            logger.warning(f"LLM service shutdown error: {e}")

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

    app = create_app()

    # Start bot tasks on startup
    @app.on_event("startup")
    async def on_startup():
        await bot.start()

    @app.on_event("shutdown")
    async def on_shutdown():
        await bot.stop()

    # Suppress noisy WebSocket-not-found 403 lines from the access log
    class _SuppressWS403(logging.Filter):
        def filter(self, record):
            msg = record.getMessage()
            return not (
                ('"WebSocket' in msg and '403' in msg)
                or 'connection rejected (403 Forbidden)' in msg
            )
    logging.getLogger("uvicorn.access").addFilter(_SuppressWS403())
    logging.getLogger("uvicorn.error").addFilter(_SuppressWS403())

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
