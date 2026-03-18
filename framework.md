# Spherical Robot Framework Architecture

## Executive Summary

Dual-processor architecture with Raspberry Pi 5 as the primary controller and ESP32 as the motion/peripheral controller. Communication via UART serial at 115200 baud. Local AI services (Faster Whisper ASR, llama.cpp/Qwen3.5, Piper TTS) run on the Pi for spoken interaction without cloud dependency.

---

## Architecture Overview

```mermaid
graph TB
    Client["Web / Mobile Client"]

    subgraph Pi5["Raspberry Pi 5 (Master)"]
        API["API Layer\n(FastAPI + WebSocket :8000)"]
        CV["CV Engine\n(MediaPipe · YAMNet)"]
        AudioMgr["Audio Manager\n(recorder · player)"]
        LLMChat["LLM Chat Service\n(oral_chat_with_llm)"]
        Education["Education / Quiz Engine"]
        EspSerial["ESP Serial Manager"]
    end

    subgraph LocalAI["Local AI Services (on Pi)"]
        Whisper["Faster Whisper ASR\n:8803"]
        LlamaCpp["llama.cpp / Qwen3.5\n:8080"]
        Piper["Piper TTS\n:8805"]
    end

    subgraph ESP32Sys["ESP32 (Slave)"]
        CmdParser["Command Parser"]
        MotorCtrl["Motor Control\n(L298 Driver)"]
        EInk["E-Ink Display\n(SPI)"]
    end

    Power["Power Unit\n2×18650 7.4V → Step-down 5V"]

    Client -->|"HTTP / WebSocket"| API
    API --> AudioMgr
    API --> LLMChat
    API --> Education
    API --> EspSerial
    CV -->|"gesture / alarm events"| API
    EspSerial -->|"UART 115200 baud"| CmdParser
    CmdParser --> MotorCtrl
    CmdParser --> EInk
    LLMChat --> Whisper
    LLMChat --> LlamaCpp
    LLMChat --> Piper
    Power -.->|"5V"| Pi5
    Power -.->|"5V"| ESP32Sys
```

---

## Layer 1: Communication Protocol

### Physical Layer
- **Interface:** UART (Serial)
- **Baud Rate:** 115200
- **Data Bits:** 8 / Stop Bits: 1 / Parity: None / Flow Control: None

### Protocol Framing

**Command Format (RPi5 → ESP32)**
```
<CMD><PARAM_LENGTH>\n<DATA>\n<CRC>
```
| Component | Format | Description |
|-----------|--------|-------------|
| CMD | String | Command identifier (e.g., `MVEL`, `DIMG`) |
| PARAM_LENGTH | Integer | Length of data in bytes |
| DATA | Binary | Command-specific data |
| CRC | Hex | 16-bit CRC-CCITT |
| Terminator | `\n` | Newline character |

**Response Format (ESP32 → RPi5)**
```
<STATUS><MESSAGE_LENGTH>\n<MESSAGE>\n
```
| Component | Format | Description |
|-----------|--------|-------------|
| STATUS | String | `OK`, `ERR`, or `PENDING` |
| MESSAGE_LENGTH | Integer | Length of message |
| MESSAGE | String | Response message or data |
| Terminator | `\n` | Newline character |

### Command Set

**Motion Control**

`MVEL` — Motor Velocity
```
MVEL<4>\n<left_speed><right_speed><duration_ms>\n<CRC>
```
- `left_speed`: int16 (−255 to 255), 0 = stop
- `right_speed`: int16 (−255 to 255), 0 = stop
- `duration_ms`: uint16 (0 = indefinite)

`MSTOP` — Emergency Stop
```
MSTOP<0>\n\n<CRC>
```

**Display**

`DIMG` — Display Image
```
DIMG<15000>\n<15000 bytes of image data>\n<CRC>
```
- Image data: 1-bit packed, 400×300 pixels (15 KB total)

`DCLEAR` — Clear Display | `DSTATUS` — Display Status

**System**

| Command | Description |
|---------|-------------|
| `SRESET` | Soft reset |
| `SHALT` | Enter deep sleep |
| `SPING` | Heartbeat / ping |

---

## Layer 2: Raspberry Pi 5 Framework

### Module Structure

```
spherical_bot/
├── api/
│   ├── routes.py              # FastAPI REST endpoints
│   └── websocket.py           # WebSocket event broadcasting
├── audio/
│   ├── yamnet_classifier.py   # YAMNet sound classification
│   ├── alarm_manager.py       # Crying detection & alarm state machine
│   ├── notification_manager.py# Webhook / local notification dispatch
│   ├── player.py              # Audio playback (ALSA)
│   ├── recorder.py            # Audio recording (ALSA)
│   └── cross_platform_recorder.py
├── cv_engine/
│   ├── gesture_detector.py    # MediaPipe hand tracking & finger count
│   ├── human_tracker.py       # Person detection
│   ├── image_processor.py     # E-Ink image prep (Floyd-Steinberg dither)
│   └── video_encoder.py       # MJPEG streaming
├── education/
│   ├── content_manager.py     # STEM content loading
│   ├── lesson_engine.py       # Lesson sequencing
│   └── quiz_engine.py         # Gesture-based MCQ quiz
├── LLM_Chat/
│   ├── service.py             # oral_chat_with_llm + synthesize_speech
│   ├── __init__.py
│   └── local/
│       └── fast-whisper-host.py  # Faster Whisper ASR HTTP service (:8803)
├── esp_serial/
│   ├── manager.py             # Serial connection & auto-detect
│   ├── protocol.py            # Protocol encoder / decoder
│   └── commands.py            # Command builders
├── utils/
│   ├── audio_detect.py
│   ├── esp32_port.py
│   └── serial_detect.py
├── config.py                  # All runtime configuration
└── main.py                    # Application entry point
```

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/api/status` | System status |
| POST | `/api/system/ping` | Ping ESP32 |
| POST | `/api/system/reset` | Reset ESP32 |
| POST | `/api/movement/move` | Control motors |
| POST | `/api/movement/stop` | Emergency stop |
| GET | `/api/stream/video` | MJPEG video stream |
| GET | `/api/stream/snapshot` | JPEG snapshot |
| GET | `/api/stream/audio` | WAV audio stream |
| WS | `/ws/audio` | PCM audio WebSocket |
| WS | `/ws` | Event WebSocket |
| POST | `/api/audio/upload` | Play uploaded audio file |
| POST | `/api/audio/play-base64` | Play base64 audio |
| POST | `/api/audio/tone` | Play tone |
| POST | `/api/audio/stop` | Stop playback |
| POST | `/api/tts/speak` | Edge TTS → speaker |
| POST | `/api/llm_chat/local` | Voice chat (ASR → LLM → TTS) |
| POST | `/api/display/update` | Update E-Ink display |
| POST | `/api/display/clear` | Clear E-Ink display |
| POST | `/api/quiz/start` | Start STEM gesture quiz |
| POST | `/api/quiz/stop` | Stop quiz |
| GET | `/api/quiz/status` | Quiz state |
| GET | `/api/gesture/status` | Live gesture debug |
| GET/POST | `/api/alarm/*` | Alarm control & configuration |

---

## Layer 3: ESP32 Framework

### Module Structure

```
spherical_bot_esp32/
├── config.h
├── communication/
│   ├── serial_protocol.h
│   ├── command_handler.h
│   └── response_builder.h
├── motor_control/
│   ├── l298_driver.h
│   ├── pid_controller.h
│   └── movement_logic.h
├── display/
│   ├── epd_driver.h
│   ├── image_buffer.h
│   └── display_manager.h
└── system/
    ├── watchdog.h
    └── power_management.h
```

---

## State Machine Diagrams

### System-Level State Machine

```mermaid
stateDiagram-v2
    [*] --> BOOT
    BOOT --> INITIALIZATION
    INITIALIZATION --> ACTIVE : all services ready

    state ACTIVE {
        [*] --> MONITORING
        MONITORING --> EDUCATION : start quiz
        MONITORING --> VOICE_CHAT : oral chat request
        EDUCATION --> MONITORING : quiz stopped
        VOICE_CHAT --> MONITORING : response delivered
    }

    ACTIVE --> PROCESS_COMMAND : API request / event
    PROCESS_COMMAND --> LOCAL_ACTION : local only
    PROCESS_COMMAND --> SERIAL_COMMAND : requires ESP32
    LOCAL_ACTION --> ACTIVE
    SERIAL_COMMAND --> ACTIVE
    ACTIVE --> INITIALIZATION : reset / error recovery
```

### RPi5 Startup Sequence

```mermaid
flowchart TD
    START([Start]) --> INIT

    subgraph INIT["Initialization"]
        I1[1. Load config.py]
        I2[2. Initialize Serial Manager\nauto-detect ESP32]
        I3[3. Start CV Engine\nMediaPipe · YAMNet]
        I4[4. Start Audio Manager\nrecorder · player]
        I5[5. Start API Server\nFastAPI + WebSocket :8000]
        I6[6. Start LLM Services\nWhisper · llama.cpp · Piper]
        I7[7. Pre-cache processing audio]
        I1 --> I2 --> I3 --> I4 --> I5 --> I6 --> I7
    end

    INIT --> READY([Ready])
    READY --> BG

    subgraph BG["Background Tasks (concurrent)"]
        B1[Video Stream\nMJPEG encoder]
        B2[Audio Processor\nYAMNet classifier]
        B3[Gesture Detector\nMediaPipe]
    end

    BG --> EVENT[Event / API Request]
    EVENT --> API_RESP[API Response]
    EVENT --> SERIAL[Serial Command → ESP32]
    EVENT --> EDU[Education / Quiz]
    EVENT --> ALARM[Alarm Trigger]

    API_RESP --> READY
    EDU --> READY
    ALARM --> READY
    SERIAL --> OK_ERR{OK / ERR?}
    OK_ERR -->|OK| READY
    OK_ERR -->|ERR| LOG[Log / Retry] --> READY
```

### ESP32 State Machine

```mermaid
flowchart TD
    BOOT([Boot]) --> INIT

    subgraph INIT["Initialization"]
        E1[1. Serial 115200 baud]
        E2[2. Motor Driver L298]
        E3[3. E-Ink Display SPI]
        E4[4. Command Handler]
        E5[5. Watchdog]
        E1 --> E2 --> E3 --> E4 --> E5
    end

    INIT --> READY([Ready])
    READY --> LOOP

    subgraph LOOP["Main Loop"]
        L1[Serial Listener]
        L2[Motor Update]
        L3[Display Update]
    end

    LOOP --> FRAME[Frame Ready]
    FRAME --> ROUTER

    subgraph ROUTER["Command Router"]
        R1["Motion\nMVEL · MSTOP"]
        R2["Display\nDIMG · DCLEAR · DSTATUS"]
        R3["System\nSRESET · SHALT · SPING"]
    end

    ROUTER --> RESP[Send Response via UART]
    RESP --> READY
```

### Motor Control State Machine

```mermaid
stateDiagram-v2
    [*] --> IDLE

    IDLE --> PARSING : MVEL received
    PARSING --> VALIDATE : speeds & duration parsed
    VALIDATE --> ACCEL : valid
    VALIDATE --> ERROR_RESP : invalid
    ERROR_RESP --> IDLE

    ACCEL --> MOVING : PWM ramped up (PID)

    state MOVING {
        [*] --> RUNNING
        RUNNING --> TIMER_CHECK
        TIMER_CHECK --> RUNNING : within duration
        TIMER_CHECK --> DECEL : duration elapsed
    }

    MOVING --> DECEL : MSTOP received
    DECEL --> IDLE : gradual stop complete

    IDLE --> EMERGENCY_STOP : MSTOP / watchdog timeout
    MOVING --> EMERGENCY_STOP : MSTOP / watchdog timeout
    EMERGENCY_STOP --> IDLE : speed=0, PWM disabled, OK sent
```

### Display Update State Machine

```mermaid
stateDiagram-v2
    [*] --> IDLE

    IDLE --> VALIDATE : DIMG received
    VALIDATE --> STORE_BUFFER : length == 15000
    VALIDATE --> ERROR_RESP : length ≠ 15000
    ERROR_RESP --> IDLE

    STORE_BUFFER --> READY : stored in PSRAM

    IDLE --> REFRESH : DCLEAR
    READY --> REFRESH : display command

    state REFRESH {
        [*] --> RESET_EPD
        RESET_EPD --> INIT_CMDS
        INIT_CMDS --> WRITE_DATA : write 15000 bytes via SPI
        WRITE_DATA --> TRIGGER_REFRESH
    }

    REFRESH --> WAIT_DONE
    WAIT_DONE --> SLEEP_MODE : BUSY pin low (2–4 s)
    WAIT_DONE --> ERROR_RESP : timeout
    SLEEP_MODE --> IDLE : sleep cmd 0x10 sent
```

### Sound Detection & Alarm State Machine

```mermaid
stateDiagram-v2
    [*] --> IDLE

    state IDLE {
        [*] --> LISTENING
        LISTENING --> ANALYZING : 1s audio chunk ready
        ANALYZING --> LISTENING : confidence ≤ 0.8
        ANALYZING --> CRYING_DETECTED : confidence > 0.8
    }

    CRYING_DETECTED --> ALARM_ACTIVE : still crying after timer
    CRYING_DETECTED --> IDLE : crying stopped (cancel)

    state ALARM_ACTIVE {
        [*] --> PLAY_ALARM
        PLAY_ALARM --> RECORD_AUDIO : 10s clip
        RECORD_AUDIO --> NOTIFY : webhook + local log
    }

    ALARM_ACTIVE --> COOLDOWN : notification sent (30 s cooldown)
    COOLDOWN --> IDLE : acknowledged / timeout
```

### LLM Voice Chat Pipeline

```mermaid
sequenceDiagram
    participant Mic as USB Microphone
    participant API as POST /api/llm_chat/local
    participant ASR as Faster Whisper :8803
    participant LLM as llama.cpp / Qwen3.5 :8080
    participant TTS as Piper TTS :8805
    participant Spk as USB Speaker

    API->>Mic: record WAV (configurable duration)
    API->>Spk: play "Processing. Please wait."
    API->>ASR: POST base64 WAV
    ASR-->>API: transcript text
    API->>LLM: POST chat messages (streaming, max 100 tokens)
    LLM-->>API: text response chunks
    API->>TTS: POST text
    TTS-->>API: WAV audio bytes
    API->>Spk: play response audio
    API-->>API: return LLMChatResult\n(session_id, transcript, text, audio_path, elapsed_ms)
```
