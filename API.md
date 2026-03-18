# Spherical Robot API Documentation

Base URL: `http://<raspberry-pi-ip>:8000`

WebSocket Base URL: `ws://<raspberry-pi-ip>:8000`

---

## Table of Contents
- [System](#system)
- [Movement Control](#movement-control)
- [Video Streaming](#video-streaming)
- [Audio Streaming](#audio-streaming)
- [Audio Playback](#audio-playback)
- [LLM Voice Chat](#llm-voice-chat)
- [STEM Education (Hand Gesture Quiz)](#stem-education-hand-gesture-quiz)
- [E-Ink Display](#e-ink-display)
- [Alarm Control](#alarm-control)
- [WebSocket Events](#websocket-events)
- [Error Handling](#error-handling)

---

## System

### Health Check
```
GET /health
```
**Response:**
```json
{"status": "ok"}
```

### Get System Status
```
GET /api/status
```
**Response:**
```json
{
  "connected": true,
  "esp32_status": "connected",
  "video_running": true,
  "audio_running": true,
  "alarm_state": "idle"
}
```
`alarm_state` values: `idle`, `detecting`, `confirmed`, `alarming`, `cooldown`, `disabled`

### Ping ESP32
```
POST /api/system/ping
```
**Response (success):**
```json
{"success": true, "message": "pong"}
```
**Response (no reply from ESP32):**
```json
{"success": false, "message": "no response"}
```

### Reset ESP32
```
POST /api/system/reset
```
**Response:**
```json
{"success": true, "message": "Reset sent"}
```

---

## Movement Control

### Move Robot
```
POST /api/movement/move
Content-Type: application/json
```
**Request Body:**
```json
{
  "left_speed": 150,
  "right_speed": 150,
  "duration_ms": 0
}
```
| Parameter | Type | Range | Description |
|-----------|------|-------|-------------|
| left_speed | int | -255 to 255 | Left motor speed (negative = reverse) |
| right_speed | int | -255 to 255 | Right motor speed (negative = reverse) |
| duration_ms | int | 0 to 65535 | Duration in ms (0 = indefinite) |

**Response:**
```json
{"success": true, "message": "OK"}
```

**Movement Examples:**
| Action | left_speed | right_speed |
|--------|------------|-------------|
| Forward | 150 | 150 |
| Backward | -150 | -150 |
| Spin Left | -150 | 150 |
| Spin Right | 150 | -150 |
| Turn Left | 75 | 150 |
| Turn Right | 150 | 75 |

### Emergency Stop
```
POST /api/movement/stop
```
**Response:**
```json
{"success": true, "message": "Stopped"}
```

---

## Video Streaming

### MJPEG Stream
```
GET /api/stream/video
```
**Response:** `multipart/x-mixed-replace; boundary=frame`

Use in HTML:
```html
<img src="http://raspberrypi:8000/api/stream/video">
```

### Snapshot
```
GET /api/stream/snapshot
```
**Response:** `image/jpeg`

---

## Audio Streaming

### WebSocket Audio Stream
```
WebSocket: ws://<ip>:8000/ws/audio
```
Streams raw 16-bit PCM audio at 48kHz mono.

**Connection Flow:**
1. Connect to WebSocket
2. Receive JSON config message:
   ```json
   {
     "type": "audio_config",
     "sample_rate": 48000,
     "channels": 1,
     "format": "int16"
   }
   ```
3. Receive binary audio data (Int16 PCM)

**JavaScript Example:**
```javascript
const ws = new WebSocket('ws://raspberrypi:8000/ws/audio');
ws.binaryType = 'arraybuffer';

ws.onmessage = (event) => {
  if (typeof event.data === 'string') {
    // JSON config message
    const config = JSON.parse(event.data);
    console.log(`Audio: ${config.sample_rate}Hz, ${config.channels}ch`);
  } else {
    // Binary audio data
    const audioData = new Int16Array(event.data);
    playAudioChunk(audioData);
  }
};
```

### HTTP Audio Stream (WAV)
```
GET /api/stream/audio
```
**Response:** `audio/wav`

---

## Audio Playback

### Play Audio File
```
POST /api/audio/upload
Content-Type: multipart/form-data
```
**Request:**
- `file`: Audio file (MP3, WAV, OGG, M4A)

**Response:**
```json
{
  "success": true,
  "message": "Playing filename.mp3",
  "filename": "filename.mp3",
  "size": 12345
}
```

### Play Base64 Audio
```
POST /api/audio/play-base64
Content-Type: application/json
```
**Request Body:**
```json
{
  "audio_data": "<base64-encoded-audio>",
  "format": "wav"
}
```

### Play Tone
```
POST /api/audio/tone?frequency=440&duration=1.0
```
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| frequency | float | 440.0 | Tone frequency in Hz |
| duration | float | 1.0 | Duration in seconds |

**Response:**
```json
{"success": true, "message": "Playing 440Hz tone"}
```

### Stop Audio
```
POST /api/audio/stop
```
**Response:**
```json
{"success": true, "message": "Audio stopped"}
```

### Get Playback Status
```
GET /api/audio/playback-status
```
**Response:**
```json
{
  "available": true,
  "is_playing": false
}
```

### Get Audio System Status
```
GET /api/audio/status
```
Returns detailed audio recorder configuration.

**Response:**
```json
{
  "available": true,
  "recording": true,
  "sample_rate": 48000,
  "channels": 2,
  "noise_reduction": true,
  "dual_mic": false
}
```
When no audio recorder is initialised: `{"available": false}`

---

## LLM Voice Chat

Records from the robot's USB microphone, runs the audio through a local
ASR → LLM → TTS pipeline, and optionally plays the spoken response on the
robot speaker.

### Local Voice Chat
```
POST /api/llm_chat/local
Content-Type: application/json
```

Pipeline: **Faster Whisper** (ASR, port 8803) → **llama.cpp / Qwen3.5** (LLM, port 8080) → **Piper** (TTS, port 8805)

**Request Body:**
```json
{
  "duration": 4.0,
  "session_id": null,
  "reset": false,
  "max_tokens": 512,
  "temperature": 0.3,
  "play_audio": true,
  "system_prompt": null
}
```

**Request Fields:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| duration | float | 4.0 | Microphone recording duration in seconds (0.5–30) |
| session_id | string\|null | null | Session ID for multi-turn conversation (null creates a new session) |
| reset | bool | false | Clear session context before this turn |
| max_tokens | int | 512 | Maximum tokens for the LLM response (16–2048) |
| temperature | float | 0.3 | LLM sampling temperature (0.0–2.0) |
| play_audio | bool | true | Play the synthesized response audio on the robot speaker |
| system_prompt | string\|null | null | Override the default system prompt for this session |

**Response:**
```json
{
  "success": true,
  "session_id": "a3f1c2d4-...",
  "text": "Water is made of tiny pieces called atoms!",
  "transcript": "what is water made of",
  "audio_file": "/tmp/tmpXXXXXX.wav",
  "provider": "local-asr-llm-tts",
  "elapsed_ms": 3240
}
```

**Response Fields:**
| Field | Type | Description |
|-------|------|-------------|
| success | bool | True if the full pipeline completed |
| session_id | string | Session ID (use in subsequent requests for multi-turn chat) |
| text | string | LLM text response |
| transcript | string\|null | ASR transcript of the user's speech |
| audio_file | string\|null | Path to synthesized WAV file on the robot (null if TTS unavailable) |
| provider | string | Provider identifier returned by the LLM service (e.g. `"local-asr-llm-tts"`) |
| elapsed_ms | int | Total pipeline duration in milliseconds |

---

## STEM Education (Hand Gesture Quiz)

The current hand-gesture STEM education flow runs as an interactive multiple-choice quiz.

- Start quiz: `POST /api/quiz/start`
- Stop quiz: `POST /api/quiz/stop`
- Check running state: `GET /api/quiz/status`
- Read live hand state (debug): `GET /api/gesture/status`

### Activate Hand-Gesture Quiz
```
POST /api/quiz/start
Content-Type: application/json
```

If `questions` is omitted, the built-in question bank is used.

**Request Body (minimal):**
```json
{}
```

**Request Body (customized):**
```json
{
  "voice": "en-US-AriaNeural",
  "shuffle": false,
  "result_delay": 2.5,
  "questions": [
    {
      "question": "What force keeps planets in orbit around the Sun?",
      "options": ["Magnetism", "Gravity", "Friction", "Pressure"],
      "correct_index": 1,
      "title": "WonderBall STEM"
    }
  ]
}
```

| Field | Type | Default | Description |
|------|------|---------|-------------|
| voice | string | `en-US-AriaNeural` | Edge TTS voice used to read questions |
| shuffle | bool | `false` | Randomize question order |
| result_delay | float | `2.5` | Delay in seconds after answering (range 0.5-10.0) |
| questions | array\|null | `null` | Optional custom question list |

Each custom question object must include:
- `question` (string)
- `options` (array of exactly 4 choices)
- `correct_index` (int, 0-3)
- `title` (string, optional)

**Response:**
```json
{
  "success": true,
  "message": "Quiz started with 1 questions",
  "total_questions": 1
}
```

### How Hand Gestures Are Used During Quiz

When the quiz is active and waiting for an answer:

- 1 finger -> A
- 2 fingers -> B
- 3 fingers -> C
- 4 fingers -> D

The detection loop injects recognized finger counts into the quiz engine automatically.

### Check Quiz Status
```
GET /api/quiz/status
```

**Response Example:**
```json
{
  "active": true,
  "state": "waiting_answer",
  "question_index": 0,
  "total_questions": 5,
  "score": 2,
  "current_question": "What is H2O commonly called?",
  "options": ["Hydrogen", "Oxygen", "Water", "Salt"],
  "correct_answer_index": null,
  "last_answer_correct": null
}
```

### Inject Gesture Answer (Testing)
```
POST /api/quiz/gesture
Content-Type: application/json
```
Manually injects a finger-count answer into the active quiz session.
Useful for testing without a physical camera.

**Request Body:**
```json
{"finger_count": 2}
```
| Field | Type | Range | Description |
|-------|------|-------|-------------|
| finger_count | int | 1–4 | 1=A, 2=B, 3=C, 4=D |

**Response:**
```json
{
  "success": true,
  "handled": true,
  "message": "Gesture processed"
}
```
`handled` is `false` when the quiz is not currently waiting for an answer.

**Error (no active quiz):** HTTP 404

### Stop Hand-Gesture Quiz
```
POST /api/quiz/stop
```

This cleanly ends the current quiz session.

**Response (active quiz):**
```json
{
  "success": true,
  "message": "Quiz stopped"
}
```

**Response (no active quiz):**
```json
{
  "success": true,
  "message": "No active quiz to stop"
}
```

### Gesture Status (Debug)
```
GET /api/gesture/status
```

Returns the latest recognized hand information from the vision loop.

**Response Example:**
```json
{
  "gesture": "victory",
  "confidence": 0.93,
  "handedness": "right",
  "finger_count": 2,
  "finger_states": [false, true, true, false, false],
  "landmarks": [],
  "hand_up": true,
  "timestamp": "2026-03-18T10:23:11.110000"
}
```

---

## E-Ink Display

### Update Display
```
POST /api/display/update
Content-Type: application/json
```

**Option 1: Display Text**
```json
{"text": "Hello World!"}
```

**Option 2: Display Pattern**
```json
{"pattern": "checkerboard"}
```
Available patterns: `checkerboard`, `gradient`, `border`

**Option 3: Display Image**
```json
{"image_base64": "<base64-encoded-binary-data>"}
```
- Image must be pre-processed to 400x300 pixels
- 1-bit black & white format
- 15000 bytes total (400 * 300 / 8)
- 0 = black, 1 = white

**Response:**
```json
{"success": true, "message": "Display updated"}
```

### Clear Display
```
POST /api/display/clear
```
**Response:**
```json
{"success": true, "message": "Display cleared"}
```

### Display Lesson Card (MCQ)
```
POST /api/display/lesson
Content-Type: application/json
```
Renders a structured multiple-choice question card on the E-Ink display.
Emojis in option strings are stripped server-side; CJK characters are supported.

**Request Body:**
```json
{
  "question": "What force keeps planets in orbit around the Sun?",
  "options": ["Magnetism", "Gravity", "Friction", "Pressure"],
  "title": "WonderBall STEM"
}
```
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| question | string | yes | Question text |
| options | array | yes | Exactly 4 answer choices |
| title | string | no | Header text (default: `"WonderBall STEM"`, max 40 chars) |

**Response:**
```json
{"success": true, "message": "Lesson displayed"}
```

---

## Alarm Control

### Get Alarm Status
```
GET /api/alarm/status
```
**Response:**
```json
{
  "enabled": true,
  "state": "idle"
}
```
States: `idle`, `detecting`, `confirmed`, `alarming`, `cooldown`, `disabled`

### Enable Alarm Monitoring
```
POST /api/alarm/enable
```
**Response:**
```json
{
  "success": true,
  "message": "Alarm monitoring enabled"
}
```

### Disable Alarm Monitoring
```
POST /api/alarm/disable
```
**Response:**
```json
{
  "success": true,
  "message": "Alarm monitoring disabled"
}
```

### Acknowledge Alarm
```
POST /api/alarm/acknowledge
```
Skips cooldown and resumes monitoring immediately.

**Response:**
```json
{
  "success": true,
  "message": "Alarm acknowledged"
}
```

### Test Alarm
```
POST /api/alarm/test
```
Triggers a test alarm to verify the notification system.

**Response:**
```json
{
  "success": true,
  "message": "Test alarm triggered"
}
```

### Get Alarm Configuration
```
GET /api/alarm/config
```
**Response:**
```json
{
  "detection_duration": 3.0,
  "cooldown_duration": 30.0,
  "recording_duration": 10.0,
  "recordings_dir": "/tmp/spherical_bot/recordings",
  "alarm_sound_path": null
}
```

### Update Alarm Configuration
```
POST /api/alarm/config
Content-Type: application/json
```
**Request Body:**
```json
{
  "threshold": 0.85,
  "detection_duration": 5.0
}
```
| Parameter | Type | Range | Description |
|-----------|------|-------|-------------|
| threshold | float | 0.0 - 1.0 | Confidence threshold for crying detection |
| detection_duration | float | 1.0 - 30.0 | Seconds of sustained crying to trigger alarm |

**Response:**
```json
{
  "success": true,
  "message": "Configuration updated"
}
```

### Get Detection History
```
GET /api/alarm/history?limit=50&event_type=alarm_triggered
```
**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| limit | int | 100 | Maximum number of events to return |
| event_type | string | - | Filter by event type (optional) |

Event types: `crying_detected`, `crying_confirmed`, `alarm_triggered`, `alarm_acknowledged`

**Response:**
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

### Clear Detection History
```
POST /api/alarm/history/clear
```
**Response:**
```json
{
  "success": true,
  "message": "History cleared"
}
```

### Configure Webhook URL
```
POST /api/alarm/webhook?url=https://your-api.com/alerts
```
**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| url | string | Webhook URL for notifications. Pass an empty string to clear. |

**Response (URL set):**
```json
{
  "success": true,
  "message": "Webhook set",
  "url": "https://your-api.com/alerts"
}
```
**Response (URL cleared):**
```json
{
  "success": true,
  "message": "Webhook cleared",
  "url": null
}
```

---

## WebSocket Events

### Event WebSocket
```
WebSocket: ws://<ip>:8000/ws
```

**Message Format:**
```json
{
  "type": "event_type",
  "data": {...},
  "timestamp": "2024-01-29T12:00:00.000Z"
}
```

### Event Types

| Type | Description | Data Fields |
|------|-------------|-------------|
| `connected` | Client connected | `message` |
| `gesture_detected` | Hand gesture detected | `gesture`, `confidence`, `handedness`, `finger_count` |
| `person_detected` | Person tracked | `id`, `bbox`, `confidence` |
| `sound_detected` | Sound classified | `category`, `confidence`, `class_name` |
| `alarm_triggered` | Alarm triggered | `state`, `duration`, `audio_file` |
| `movement_update` | Motor state changed | `left_speed`, `right_speed`, `status` |

**Subscribe to Events:**
```json
{"type": "subscribe", "events": ["gesture_detected"]}
```

**Unsubscribe:**
```json
{"type": "unsubscribe", "events": ["gesture_detected"]}
```

---

## Error Handling

All endpoints may return error responses:

```json
{
  "detail": "Error message"
}
```

| HTTP Code | Description |
|-----------|-------------|
| 400 | Bad request (invalid parameters) |
| 404 | Resource not found |
| 500 | Internal server error |
| 503 | Service unavailable (component not ready) |

---

## Command Line Options

```bash
python main.py [OPTIONS]

Options:
  --host TEXT         API host (default: 0.0.0.0)
  --port INT          API port (default: 8000)
  --audio-in DEV      Audio input device (e.g., plughw:2,0)
  --audio-out DEV     Audio output device (e.g., plughw:3,0)
  --no-video          Disable video capture
  --no-audio          Disable audio
  --no-serial         Disable ESP32 serial communication
  --list-audio        List audio devices and exit
  --debug             Enable debug logging
```

**Example:**
```bash
python main.py --audio-in plughw:2,0 --audio-out plughw:3,0 --no-serial
```

---

## ESP32 Serial Protocol

When communicating directly with ESP32 via serial, use this binary protocol:

### Command Format
```
<CMD><PARAM_LENGTH>\n<DATA>\n<CRC>\n
```

### Commands

| Command | Description | Data Size | Data Format |
|---------|-------------|-----------|-------------|
| `MVEL` | Motor velocity | 6 bytes | left(int16), right(int16), duration(uint16) |
| `MSTOP` | Emergency stop | 0 bytes | - |
| `DIMG` | Display image | 15000 bytes | 1-bit packed image data |
| `DCLEAR` | Clear display | 0 bytes | - |
| `DSTATUS` | Display status | 0 bytes | - |
| `SRESET` | Soft reset | 0 bytes | - |
| `SHALT` | Deep sleep | 0 bytes | - |
| `SPING` | Ping | 0 bytes | - |

### Response Format
```
<STATUS><MSG_LENGTH>\n<MESSAGE>\n
```

**Status Codes:**
- `OK` - Command successful
- `ERR` - Command failed
- `PENDING` - Command in progress

### CRC Calculation
CRC-CCITT (polynomial 0x1021):
```python
def calculate_crc(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return f"{crc:04X}"
```

---

## Configuration

### Alarm Settings
Edit `config.py` to customize alarm behavior:
```python
# YAMNet Sound Detection
YAMNET_THRESHOLD = 0.8  # Confidence threshold (0.0-1.0)
CRYING_DETECTION_DURATION = 3  # Seconds of sustained crying to trigger alarm

# Notification Settings
NOTIFICATION_WEBHOOK_URL = "https://your-api.com/alerts"  # Or None
NOTIFICATION_LOCAL_SOUND_ENABLED = True  # Play alarm sound locally
NOTIFICATION_LOG_FILE = "/tmp/spherical_bot/alerts.log"
NOTIFICATION_MAX_HISTORY = 100  # Number of events to keep in memory

# Alarm Settings
ALARM_COOLDOWN_DURATION = 30.0  # Seconds between alarms
ALARM_RECORDING_DURATION = 10.0  # Seconds to record on alarm
```

### Serial Port
Edit `config.py`:
```python
SERIAL_PORT = "auto"  # Auto-detect ESP32
# or
SERIAL_PORT = "/dev/esp32"  # Persistent symlink
# or
SERIAL_PORT = "/dev/ttyACM0"  # Direct path
```

### Audio Devices
```python
AUDIO_PLAYBACK_DEVICE = "plughw:3,0"  # USB speaker
AUDIO_RECORD_DEVICE = "hw:2,0"        # USB camera mic
AUDIO_SAMPLE_RATE = 48000             # 48kHz
AUDIO_CHANNELS = 2                    # Stereo input
```

### Video Settings
```python
CAMERA_DEVICE = "/dev/video0"
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 30
```
