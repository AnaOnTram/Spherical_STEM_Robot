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
- [E-Ink Display](#e-ink-display)
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
  "audio_running": true
}
```

### Ping ESP32
```
POST /api/system/ping
```
**Response:**
```json
{"success": true, "message": "pong"}
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
| `gesture_detected` | Hand gesture detected | `gesture`, `confidence`, `handedness` |
| `person_detected` | Person tracked | `id`, `bbox`, `confidence` |
| `sound_detected` | Sound classified | `category`, `confidence`, `class_name` |
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
