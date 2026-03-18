"""Configuration for Spherical Robot Pi5 Controller."""

import os
import platform

# Serial Communication
# For Raspberry Pi 5, use /dev/ttyAMA0 (UART0) or /dev/serial0
# For USB-to-Serial adapters, use /dev/ttyUSB0 or /dev/ttyACM0
# For automatic ESP32 detection, use: SERIAL_PORT = "auto"
# To find available ports: ls /dev/tty*
#
# Options:
# - "auto" - Automatically detect ESP32 (recommended for USB)
# - "/dev/esp32" - Persistent symlink (run utils/setup_esp32_udev.sh)
# - "/dev/ttyACM0" - Direct USB CDC device
# - "/dev/ttyUSB0" - USB-to-Serial adapter
# - "/dev/ttyAMA0" - Pi5 GPIO UART (GPIO14/15)
SERIAL_PORT = "auto"  # Auto-detect ESP32
SERIAL_BAUDRATE = 115200
SERIAL_TIMEOUT = 5.0

# Camera (USB OV5695 module)
CAMERA_DEVICE = "/dev/video0"
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 30
# Camera image rotation in degrees (clockwise): 0, 90, 180, 270
# Useful when camera mounting orientation is not fixed yet.
CAMERA_ROTATION_DEGREES = 0

# Audio (USB Audio Device)
# Run `arecord -l` and `aplay -l` to find correct card numbers
# Common formats: "plughw:CARD,DEVICE", "hw:CARD,DEVICE", "default"
# For USB devices, card number may change on reboot
#
# To find your devices:
#   arecord -l   # List capture (mic) devices
#   aplay -l     # List playback (speaker) devices
#
# Options:
# - "auto" - Automatically detect USB audio devices (recommended)
# - "plughw:X,Y" - Specific playback device
# - "hw:X,Y" - Specific capture device (lower latency)
# - "default" - System default device
#
AUDIO_PLAYBACK_DEVICE = "auto"  # Auto-detect USB speaker, or use "plughw:3,0"
AUDIO_RECORD_DEVICE = "auto"  # Auto-detect USB mic, or use "hw:2,0"
AUDIO_RECORD_DEVICE_2 = None  # Secondary mic for noise cancellation (disabled - using stereo channels instead)
AUDIO_SAMPLE_RATE = 48000  # USB camera mic native rate (don't use 44100)
AUDIO_CHANNELS = 2  # Stereo input from USB camera mic (2 channels)
AUDIO_OUTPUT_CHANNELS = 1  # Mono output after processing
AUDIO_CHUNK_SIZE = 1024  # Larger chunks for stereo input (~21ms at 48kHz stereo)

# Audio Processing
AUDIO_NOISE_REDUCTION = True  # Enable noise reduction
AUDIO_DUAL_MIC_ENABLED = (
    False  # Disabled - using stereo mic channels instead of dual mics
)

# E-Ink Display (via ESP32)
EINK_WIDTH = 400
EINK_HEIGHT = 300
EINK_IMAGE_SIZE = 15000  # 400 * 300 / 8 bytes (1-bit packed)

# YAMNet Sound Detection
YAMNET_THRESHOLD = 0.8
CRYING_DETECTION_DURATION = 3  # seconds of sustained crying to trigger alarm

# Notification Settings
NOTIFICATION_WEBHOOK_URL = None  # e.g., "https://api.example.com/alerts"
NOTIFICATION_LOCAL_SOUND_ENABLED = True  # Play alarm sound locally
NOTIFICATION_LOG_FILE = "/tmp/spherical_bot/alerts.log"  # Event log file
NOTIFICATION_MAX_HISTORY = 100  # Number of events to keep in memory

# Alarm Settings
ALARM_COOLDOWN_DURATION = 30.0  # seconds between alarms
ALARM_RECORDING_DURATION = 10.0  # seconds to record on alarm

# API Server
API_HOST = "0.0.0.0"
API_PORT = 8000

# Motor Control
MOTOR_SPEED_MIN = -255
MOTOR_SPEED_MAX = 255

# LLM Chat (local llama.cpp server)
LLM_CHAT_LOCAL_BASE_URL = os.getenv(
    "LLM_CHAT_LOCAL_BASE_URL", "http://127.0.0.1:8080/v1"
)
LLM_CHAT_LOCAL_MODEL = os.getenv("LLM_CHAT_LOCAL_MODEL", "")

# System prompt for the local LLM
# Tuned for Qwen3-1.7B: small model benefits from explicit role, hard length cap,
# a concrete answer pattern, and /no_think to suppress chain-of-thought overhead.
LLM_CHAT_TEXT_SYSTEM_PROMPT = os.getenv(
    "LLM_CHAT_TEXT_SYSTEM_PROMPT",
    "You are a cheerful robot teaching STEM to young children. "
    "Rules: answer in 1-2 short sentences only; use simple everyday words; "
    "always connect the answer to something the child can see or touch; "
    "if asked something off-topic, pivot with 'Great question! Did you know...' "
    "and share a related science fact. Never use emoji, lists, or markdown. /no_think",
)
LLM_CHAT_SESSION_MAX_MESSAGES = int(os.getenv("LLM_CHAT_SESSION_MAX_MESSAGES", "20"))

# Auto-start the llama.cpp server on first request.
# Set to False if you prefer to start the server manually.
LLM_CHAT_LOCAL_AUTO_START = os.getenv("LLM_CHAT_LOCAL_AUTO_START", "true").lower() in (
    "true",
    "1",
    "yes",
)

# Text model path (Qwen3.5 or compatible GGUF)
_DEFAULT_MODEL_DIR = "/home/admin/model"
LFM_MODEL_DIR = os.getenv("LFM_MODEL_DIR", _DEFAULT_MODEL_DIR)
LFM_TEXT_MODEL_PATH = os.getenv(
    "LFM_TEXT_MODEL_PATH", f"{LFM_MODEL_DIR}/Qwen3.5-0.8B-UD-K4_K_XL.gguf"
)

# Local ASR + TTS Service URLs
LOCAL_ASR_URL = os.getenv(
    "LOCAL_ASR_URL", "http://127.0.0.1:8803/recognize"
)  # Faster Whisper
LOCAL_TTS_URL = os.getenv("LOCAL_TTS_URL", "http://127.0.0.1:8805")  # Piper TTS
LOCAL_TTS_VOICE = os.getenv("LOCAL_TTS_VOICE", "en_US-amy-medium")
