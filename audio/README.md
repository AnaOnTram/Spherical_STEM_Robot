# Sound Detection System

This module provides real-time crying detection using Google's YAMNet sound classification model. It runs as part of the main robot system and supports multiple notification channels.

## Features

- **Real-time crying detection** using YAMNet TFLite model
- **Cross-platform audio recording** (ALSA on Linux, SoundDevice on macOS/Windows)
- **Multi-channel notifications**:
  - WebSocket (for mobile app)
  - HTTP Webhook (for external services)
  - Local sound alarm
  - File logging
- **Configurable detection parameters**
- **Event history** (last 100 events)

## Setup

### 1. Download YAMNet Model

```bash
cd audio/models
python download_yamnet.py
```

Or from project root:
```bash
python -m audio.models.download_yamnet
```

This downloads:
- `yamnet.tflite` (3.9MB) - The TFLite model
- `yamnet_class_map.csv` - Class name mappings

### 2. Install Dependencies

**For macOS (development):**
```bash
pip install sounddevice numpy
```

**For Raspberry Pi 5 (production):**
```bash
pip install pyalsaaudio numpy
```

**Optional: TensorFlow Lite**
```bash
# Option 1: tflite-runtime (lightweight, recommended for Pi)
pip install tflite-runtime

# Option 2: Full TensorFlow
pip install tensorflow
```

## Configuration

Edit `config.py`:

```python
# YAMNet Sound Detection
YAMNET_THRESHOLD = 0.8  # Confidence threshold (0.0-1.0)
CRYING_DETECTION_DURATION = 3  # Seconds of sustained crying to trigger alarm

# Notification Settings
NOTIFICATION_WEBHOOK_URL = "https://your-api.com/alerts"  # Or None
NOTIFICATION_LOCAL_SOUND_ENABLED = True  # Play alarm sound
NOTIFICATION_LOG_FILE = "/tmp/spherical_bot/alerts.log"
NOTIFICATION_MAX_HISTORY = 100

# Alarm Settings
ALARM_COOLDOWN_DURATION = 30.0  # Seconds between alarms
ALARM_RECORDING_DURATION = 10.0  # Seconds to record on alarm
```

## Usage

### Starting the System

```bash
# Start with alarm monitoring enabled (default)
python main.py

# Start without alarm
python main.py --no-alarm

# List audio devices
python main.py --list-audio

# Specify audio device
python main.py --audio-in "plughw:2,0"
```

### API Endpoints

#### Get Alarm Status
```bash
GET /api/alarm/status
```

Response:
```json
{
  "enabled": true,
  "state": "idle"
}
```

States: `idle`, `detecting`, `confirmed`, `alarming`, `cooldown`

#### Enable/Disable Alarm
```bash
POST /api/alarm/enable
POST /api/alarm/disable
```

#### Get Configuration
```bash
GET /api/alarm/config
```

Response:
```json
{
  "detection_duration": 3.0,
  "cooldown_duration": 30.0,
  "recording_duration": 10.0,
  "recordings_dir": "/tmp/spherical_bot/recordings",
  "alarm_sound_path": null
}
```

#### Update Configuration
```bash
POST /api/alarm/config
Content-Type: application/json

{
  "threshold": 0.85,
  "detection_duration": 5.0
}
```

#### Get Detection History
```bash
GET /api/alarm/history?limit=50&event_type=alarm_triggered
```

Response:
```json
{
  "events": [
    {
      "timestamp": "2026-02-02T10:30:00",
      "event_type": "alarm_triggered",
      "confidence": 0.92,
      "duration": 3.5,
      "audio_file": "/tmp/spherical_bot/recordings/crying_20260202_103000.wav",
      "metadata": {}
    }
  ],
  "count": 1
}
```

#### Clear History
```bash
POST /api/alarm/history/clear
```

#### Set Webhook URL
```bash
POST /api/alarm/webhook?url=https://your-api.com/alerts
```

To clear: `POST /api/alarm/webhook?url=`

#### Test Alarm
```bash
POST /api/alarm/test
```

#### Acknowledge Alarm
```bash
POST /api/alarm/acknowledge
```

Skips cooldown and resumes monitoring immediately.

### WebSocket Events

Connect to `ws://localhost:8000/ws` and subscribe to events:

```json
{
  "type": "subscribe",
  "events": ["ALARM_TRIGGERED", "SOUND_DETECTED"]
}
```

Events:
- `ALARM_TRIGGERED` - Alarm was triggered
- `SOUND_DETECTED` - Sound detected (any category)
- `GESTURE_DETECTED` - Hand gesture detected
- `PERSON_DETECTED` - Person detected

Example alarm event:
```json
{
  "type": "ALARM_TRIGGERED",
  "data": {
    "state": "alarming",
    "duration": 3.5,
    "audio_file": "/tmp/spherical_bot/recordings/crying_20260202_103000.wav"
  },
  "timestamp": "2026-02-02T10:30:00"
}
```

## Webhook Format

When configured, the system sends POST requests to your webhook URL:

```json
{
  "timestamp": "2026-02-02T10:30:00",
  "event_type": "alarm_triggered",
  "confidence": 0.92,
  "duration": 3.5,
  "audio_file": "/tmp/spherical_bot/recordings/crying_20260202_103000.wav",
  "metadata": {},
  "logged_at": "2026-02-02T10:30:00"
}
```

Event types:
- `crying_detected` - Initial crying detection
- `crying_confirmed` - Sustained crying confirmed
- `alarm_triggered` - Alarm triggered
- `alarm_acknowledged` - Alarm acknowledged

## How It Works

1. **Audio Recording**: Continuously records audio from USB microphone
2. **Classification**: Every second, sends audio to YAMNet model
3. **Crying Detection**: Checks if crying classes (20-22) exceed threshold
4. **State Machine**:
   - `IDLE` → `DETECTING` (crying detected)
   - `DETECTING` → `CONFIRMED` (sustained for N seconds)
   - `CONFIRMED` → `ALARMING` (trigger alarm)
   - `ALARMING` → `COOLDOWN` (prevent spam)
   - `COOLDOWN` → `IDLE` (after cooldown period)
5. **Notifications**: Sends alerts through all configured channels

## File Structure

```
audio/
├── __init__.py                 # Module exports
├── alarm_manager.py            # Main alarm logic
├── cross_platform_recorder.py  # macOS/Linux recording
├── notification_manager.py     # Multi-channel notifications
├── player.py                   # Audio playback
├── recorder.py                 # ALSA recording (Linux)
├── yamnet_classifier.py        # YAMNet model wrapper
└── models/
    ├── download_yamnet.py      # Model download script
    ├── yamnet.tflite           # YAMNet model (downloaded)
    └── yamnet_class_map.csv    # Class mappings (downloaded)
```

## Troubleshooting

### Model not found
```bash
python audio/models/download_yamnet.py --verbose
```

### No audio input on macOS
```bash
# List devices
python -c "import sounddevice as sd; print(sd.query_devices())"

# Update config
AUDIO_RECORD_DEVICE = "0"  # Use device index
```

### High CPU usage
- Increase `AUDIO_CHUNK_SIZE` in config
- Increase detection interval in `alarm_manager.py`

### False positives
- Increase `YAMNET_THRESHOLD` (e.g., 0.85 or 0.9)
- Increase `CRYING_DETECTION_DURATION` (e.g., 5 seconds)

### Webhook not receiving
Check logs for errors:
```bash
tail -f /tmp/spherical_bot/alerts.log
```

## Development

### Testing on macOS

The system automatically detects macOS and uses SoundDevice instead of ALSA:

```python
from audio.cross_platform_recorder import create_recorder

recorder = create_recorder()
recorder.start()

# Get audio
audio = recorder.get_audio_buffer(1.0)
print(f"Got {len(audio)} samples")

recorder.stop()
```

### Custom Notification Handler

```python
from audio.notification_manager import NotificationManager, DetectionEvent

def my_handler(event: DetectionEvent):
    print(f"Event: {event.event_type}, Confidence: {event.confidence}")

manager = NotificationManager()
manager.add_websocket_callback(my_handler)
```

## License

This project uses YAMNet from TensorFlow Hub, licensed under Apache 2.0.
