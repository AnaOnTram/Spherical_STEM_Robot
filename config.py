"""Configuration for Spherical Robot Pi5 Controller."""

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
AUDIO_RECORD_DEVICE = "auto"    # Auto-detect USB mic, or use "hw:2,0"
AUDIO_RECORD_DEVICE_2 = None          # Secondary mic for noise cancellation (disabled - using stereo channels instead)
AUDIO_SAMPLE_RATE = 48000             # USB camera mic native rate (don't use 44100)
AUDIO_CHANNELS = 2                    # Stereo input from USB camera mic (2 channels)
AUDIO_OUTPUT_CHANNELS = 1             # Mono output after processing
AUDIO_CHUNK_SIZE = 1024               # Larger chunks for stereo input (~21ms at 48kHz stereo)

# Audio Processing
AUDIO_NOISE_REDUCTION = True          # Enable noise reduction
AUDIO_DUAL_MIC_ENABLED = False        # Disabled - using stereo mic channels instead of dual mics

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
