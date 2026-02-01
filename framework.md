Spherical Robot Framework Architecture
Executive Summary
Dual-processor architecture with Raspberry Pi 5 as the primary controller and ESP32 as the motion/peripheral controller. Communication via UART serial connection at 115200 baud.
---
Architecture Overview
┌─────────────────────────────────────────────────────────────────────────────┐
│                          SPHERICAL ROBOT SYSTEM                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌────────────────────────────────────┐          ┌────────────────────────┐ │
│  │     RASPBERRY PI 5 (Master)        │          │       ESP32 (Slave)    │ │
│  │                                    │          │                        │ │
│  │  ┌────────────┐  ┌────────────┐   │   UART   │  ┌──────────────────┐  │ │
│  │  │    API     │  │    CV      │   │◄────────┼─►│  Command Parser   │  │ │
│  │  │  Layer     │  │   Engine   │   │          │  └────────┬─────────┘  │ │
│  │  └─────┬──────┘  └─────┬──────┘   │          │           │            │ │
│  │        │              │          │          │           ▼            │ │
│  │  ┌─────▼──────┐  ┌────▼───────┐  │          │  ┌──────────────────┐  │ │
│  │  │  Serial    │  │  Audio     │  │          │  │  Motor Control   │  │ │
│  │  │  Manager   │  │  Manager   │  │          │  │  (L298 Driver)   │  │ │
│  │  └─────┬──────┘  └───────┬────┘  │          │  └────────┬─────────┘  │ │
│  └────────┼────────────────┼───────┘          │           │            │ │
│           │                │                  │           ▼            │ │
│  ┌────────▼────────┐ ┌────▼──────────────┐   │  ┌──────────────────┐  │ │
│  │ Web/Mobile      │ │ HDMI/Audio        │   │  │ E-Ink Display    │  │ │
│  │ Interface       │ │ Output            │   │  │ (SPI)            │  │ │
│  └─────────────────┘ └───────────────────┘   │  └──────────────────┘  │ │
│                                            │                          │ │
└────────────────────────────────────────────┼──────────────────────────┘ │
                                             │                            │
                                    ┌────────▼─────────┐                 │
                                    │   POWER UNIT     │                 │
                                    │  2x 18650 (7.4V) │                 │
                                    │  → Step Down 5V  │                 │
                                    └──────────────────┘                 │
                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
---
Layer 1: Communication Protocol
Physical Layer
- Interface: UART (Serial)
- Baud Rate: 115200
- Data Bits: 8
- Stop Bits: 1
- Parity: None
- Flow Control: None
Protocol Framing
Command Format (RPi5 → ESP32)
<CMD><PARAM_LENGTH>\n<DATA>\n<CRC>
| Component | Format | Description |
|-----------|--------|-------------|
| CMD | String | Command identifier (e.g., MVEL, DIMG) |
| PARAM_LENGTH | Integer | Length of data in bytes |
| DATA | Binary | Command-specific data |
| CRC | Hex | 16-bit CRC-CCITT |
| Terminator | \n | Newline character |
Response Format (ESP32 → RPi5)
<STATUS><MESSAGE_LENGTH>\n<MESSAGE>\n
| Component | Format | Description |
|-----------|--------|-------------|
| STATUS | String | OK, ERR, or PENDING |
| MESSAGE_LENGTH | Integer | Length of message |
| MESSAGE | String | Response message or data |
| Terminator | \n | Newline character |
Command Set
Motion Control Commands
MVEL - Motor Velocity
MVEL<4>\n<left_speed><right_speed><duration_ms>\n<CRC>
- left_speed: int16 (-255 to 255), 0 = stop
- right_speed: int16 (-255 to 255), 0 = stop
- duration_ms: uint16 (0 = indefinite)
- Response: OK or ERR
MSTOP - Emergency Stop
MSTOP<0>\n\n<CRC>
- Immediate motor stop
- Response: OK
Display Commands
DIMG - Display Image
DIMG<15000>\n<15000 bytes of image data>\n<CRC>
- Image data: 1-bit packed, 400x300 pixels (15KB total)
- Response: OK or ERR
DCLEAR - Clear Display
DCLEAR<0>\n\n<CRC>
- Clear to all white
- Response: OK
DSTATUS - Display Status
DSTATUS<0>\n\n<CRC>
- Response: STATUS<json>\n with display info
System Commands
SRESET - Soft Reset
SHALT - Enter Deep Sleep
SPING - Heartbeat/Ping
---
Layer 2: Raspberry Pi 5 Framework
Module Structure
spherical_bot/
├── api/
│   ├── routes.py              # FastAPI endpoints
│   ├── websocket.py           # WebSocket for streaming
│   └── middleware.py          # Auth, logging
├── cv_engine/
│   ├── gesture_detector.py    # MediaPipe hand tracking
│   ├── human_tracker.py       # Person detection
│   ├── image_processor.py     # E-Ink image preparation
│   └── video_encoder.py       # H.264 streaming
├── audio/
│   ├── yamnet_classifier.py   # Sound classification
│   ├── alarm_manager.py       # Alarm triggering
│   ├── player.py              # Audio playback
│   └── recorder.py            # Audio recording
├── serial/
│   ├── manager.py             # Serial connection manager
│   ├── protocol.py            # Protocol encoder/decoder
│   └── commands.py            # Command builders
├── education/
│   ├── content_manager.py     # STEM content rendering
│   ├── lesson_engine.py       # Interactive lessons
│   └── assets/                # Graphics, sounds
└── main.py                    # Application entry point
Key Components
image_processor.py
class EInkImageProcessor:
    """Prepares images for 4.2" E-Ink display (400x300, 1-bit)"""
    
    def process(self, image_path: str) -> bytes:
        """
        Pipeline:
        1. Load image
        2. Crop to 4:3 aspect ratio
        3. Resize to 400x300
        4. Convert to grayscale
        5. Apply Floyd-Steinberg dithering
        6. Pack to 1-bit (MSB first)
        7. Return 15KB bytes
        """
        pass
serial/manager.py
class SerialManager:
    """Manages UART communication with ESP32"""
    
    def __init__(self, port="/dev/ttyS0", baudrate=115200):
        self.serial_connection = None
        self._lock = threading.Lock()
    
    def send_command(self, command: Command) -> Response:
        """Send command and wait for response (timeout: 5s)"""
        pass
    
    def send_async(self, command: Command, callback: Callable):
        """Send command asynchronously"""
        pass
API Endpoints
- POST /api/movement/move - Control motors
- POST /api/display/update - Update E-Ink display
- GET /api/stream/video - H.264 video stream
- GET /api/stream/audio - Audio stream
- GET /api/education/lessons - STEM content
---
Layer 3: ESP32 Framework
Module Structure
spherical_bot_esp32/
├── config.h                    # Pin definitions
├── communication/
│   ├── serial_protocol.h       # Protocol decoder/encoder
│   ├── command_handler.h       # Command router
│   └── response_builder.h      # Response formatter
├── motor_control/
│   ├── l298_driver.h           # Motor driver
│   ├── pid_controller.h        # PID for smooth movement
│   └── movement_logic.h        # Movement algorithms
├── display/
│   ├── epd_driver.h            # E-Ink SPI driver (reuse existing)
│   ├── image_buffer.h          # Image buffer (reuse existing)
│   └── display_manager.h       # Display state machine
└── system/
    ├── watchdog.h              # System watchdog
    └── power_management.h      # Power control
command_handler.h
class CommandHandler {
public:
    void handleMVEL(uint8_t* data);   // Motor velocity
    void handleMSTOP();                // Stop motors
    void handleDIMG(uint8_t* data);   // Display image
    void handleDCLEAR();              // Clear display
    void handleSRESET();              // Soft reset
};
---
STATE MACHINE DIAGRAMS
System-Level State Machine
┌─────────────┐
│    BOOT     │ ←────────────────────────────────────────────────────────────┐
└──────┬──────┘                                                              │
       │                                                                     │
       ▼                                                                     │
┌──────────────────────┐                                                    │
│  INITIALIZATION      │                                                    │
│  - RPi5 services     │                                                    │
│  - ESP32 init        │                                                    │
│  - Serial connect    │                                                    │
└──────────┬───────────┘                                                    │
           │                                                                 │
           ▼                                                                 │
    ┌──────────────┐                                                        │
    │ ACTIVE STATE │◄────────────────────────────────────────────────────────┘
    └──────┬───────┘  (after reset/error recovery)
           │
    ┌──────┴────────────────────────────┐
    │                                  │
    ▼                                  ▼
┌─────────────────┐          ┌─────────────────┐
│   MONITORING    │          │   EDUCATION     │
│                 │          │                 │
│ - Video stream  │          │ - Display       │
│ - Audio detect  │          │   content       │
│ - Sound detect  │          │ - Audio         │
│ - Alert         │          │   playback      │
└────────┬────────┘          └────────┬────────┘
         │                            │
         │ User command/Event         │
         └────────────┬───────────────┘
                      ▼
              ┌─────────────────┐
              │ PROCESS COMMAND │
              │ - Parse request │
              │ - Execute       │
              │ - Send to ESP32 │
              └────────┬────────┘
                       │
            ┌──────────┴──────────┐
            │                     │
            ▼                     ▼
      ┌──────────┐         ┌──────────┐
      │ LOCAL    │         │ SERIAL   │
      │ ACTION   │         │ COMMAND  │
      └──────────┘         └──────────┘
RPi5 State Machine
┌─────────────┐
│   START     │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                             INITIALIZATION                                  │
│  1. Load Configuration                                                       │
│  2. Initialize Serial Manager                                                │
│  3. Start CV Engine (MediaPipe, YAMNet)                                      │
│  4. Start Audio Manager                                                      │
│  5. Start API Server (FastAPI + WebSocket)                                  │
│  6. Load Education Content                                                   │
│  7. Connect to ESP32 via UART                                                │
└─────────────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│    READY  ◄───────────────────────────────────────────────────────────────┐
└──────┬─────────────────────────────────────────────────────────────────────┤
       │                                                                     │
       │ ┌─────────────────────────────────────────────────────────────┐    │
       ├─▶│ BACKGROUND TASKS                                            │    │
       │  │  ┌──────────┐  ┌──────────┐  ┌──────────┐               │    │
       │  │  │ Video    │  │ Audio    │  │ Gesture  │               │    │
       │  │  │ Stream   │  │ Process  │  │ Detect   │               │    │
       │  │  └────┬─────┘  └────┬─────┘  └────┬─────┘               │    │
       │  └───────┼────────────┼────────────┼─────────────────────────┘    │
       │          ▼            ▼            ▼                             │
       │    ┏━━━━━━━━━━┓ ┏━━━━━━━━━━┓ ┏━━━━━━━━━━┓                    │
       │    ┃  Event   ┃ ┃  Event   ┃ ┃  Event   ┃                    │
       │    ┃  Queues  ┃ ┃  Queues  ┃ ┃  Queues  ┃                    │
       │    ┗━━━━━━┯━━━━┛ ┗━━━━━━┯━━━━┛ ┗━━━━━━┯━━━━┛                    │
┌───────┴───────┬────┴──────────┬────┴──────────┬────┘                 │
│               │               │               │                       │
▼               ▼               ▼               ▼                       │
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│ API      │  │ Serial   │  │ Education│  │ Alarm    │                  │
│ Response │  │ Command  │  │ Router   │  │ Trigger  │                  │
└────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘                  │
     │             │             │             │                         │
     └─────────────┴─────────────┴─────────────┼─────────────────────────┘
                                           │
                                           ▼
                                ┌─────────────────────┐
                                │  SERIAL MANAGER     │
                                │  - Encode Command   │
                                │  - Send via UART    │
                                │  - Wait Response    │
                                └─────────┬───────────┘
                                          │
                     ┌────────────────────┴────────────────────┐
                     │                                         │
                     ▼                                         ▼
              ┌─────────────┐                           ┌─────────────┐
              │    OK       │                           │    ERR      │
              │ Update state│                           │ Log/retry   │
              └─────────────┘                           └─────────────┘
ESP32 State Machine
┌─────────────┐
│   BOOT      │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                             INITIALIZATION                                  │
│  1. Initialize Serial (115200 baud)                                         │
│  2. Initialize Motor Driver (L298)                                          │
│  3. Initialize E-Ink Display (SPI)                                          │
│  4. Initialize Command Handler                                              │
│  5. Initialize Watchdog                                                     │
└─────────────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────┐
│   READY     │
└──────┬──────┘
       │
       │ ┌─────────────────────────────────────────────────────────────┐
       ├─▶│ MAIN LOOP                                                  │
       │  │  ┌──────────┐  ┌──────────┐  ┌──────────┐               │
       │  │  │ Serial   │  │ Motor    │  │ Display  │               │
       │  │  │ Listener │  │ Update   │  │ Update   │               │
       │  │  └────┬─────┘  └────┬─────┘  └────┬─────┘               │
       │  └───────┼────────────┼────────────┼─────────────────────────┘
       │          ▼            │            │                         │
       │    ┌────────┐         │            │                         │
       │    │ Frame  │         │            │                         │
       │    │ Ready  │         │            │                         │
       │    └───┬────┘         │            │                         │
       │        │              │            │                         │
       │        ▼              │            │                         │
       │    ┌─────────────────────────────────────────────┐           │
       │    │         COMMAND ROUTER                       │           │
       │    │  ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐   │           │
       │    │  │ Motion│ │Display│ │ System│ │Status │   │           │
       │    │  └───┬───┘ └───┬───┘ └───┬───┘ └───┬───┘   │           │
       │    └──────┼────────┼────────┼────────┼───────┘           │
       │           │        │        │        │                   │
       ▼           ▼        ▼        ▼        ▼                   │
  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐      │
  │MVEL,    │ │DIMG,    │ │SRESET,  │ │SPING    │ │ERR      │      │
  │MSTOP    │ │DCLEAR   │ │HALT     │ │         │ │Response │      │
  │Set motors│ │Store in │ │Reset/   │ │Build    │ │Send     │      │
  │Send OK  │ │buffer   │ │Sleep    │ │JSON OK  │ │error    │      │
  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └─────────┘      │
       │           │           │           │                         │
       └───────────┼───────────┼───────────┼─────────────────────────┘
                   │           │           │
                   ▼           ▼           ▼
          ┌─────────────────────────────┐
          │     SEND RESPONSE           │
          │  - Build status message     │
          │  - Send via UART            │
          └─────────────────────────────┘
Motor Control State Machine
    ┌────────┐
    │  IDLE  │
    └───┬────┘
        │ MVEL command
        ▼
  ┌───────────┐
  │  PARSING  │
  └─────┬─────┘
        │ Parse speeds/duration
        ▼
  ┌───────────┐
  │ VALIDATE  │
  └─────┬─────┘
        │
   ┌────┴────┐
   │ Valid?  │
   └─┬─────┬─┘
     │Yes  │No
     ▼     ▼
┌────────┐ ┌──────┐
│ACCCEL  │ │ ERR  │
│(PID)   │ │ RESP │
└───┬────┘ └──────┘
    │ Set PWM gradually
    ▼
┌────────┐
│MOVING  │◀──────────────────┐
└───┬────┘                   │
    │                        │
    │ ┌────────────────────┐ │
    │ │ TIMER CHECK        │ │
    │ │ - Duration done?   │ │
    │ │ - MSTOP received?  │ │
    │ └───┬────────────────┘ │
    │     │                  │
    │     │ Done/Stop        │
    │     ▼                  │
    │  ┌────────┐            │
    │  │DECEL   │ ◀──────────┘
    │  │(PID)   │
    │  └───┬────┘
    │      │
    │      │ Gradual stop
    └──────┘
           ▼
        ┌────────┐
        │  IDLE  │
        └────────┘
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
          EMERGENCY STOP (MSTOP or watchdog timeout)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
     ▼
┌─────────────────┐
│ EMERGENCY STOP  │
│ - Set speed = 0 │
│ - Disable PWM   │
│ - Send OK       │
└────────┬────────┘
         ▼
      ┌────────┐
      │  IDLE  │
      └────────┘
Display Update State Machine
    ┌────────┐
    │  IDLE  │
    └───┬────┘
        │ DIMG command (15000 bytes)
        ▼
  ┌───────────┐
  │ VALIDATE  │
  └─────┬─────┘
        │ Check length == 15000
   ┌────┴────┐
   │ Valid?  │
   └─┬─────┬─┘
     │Yes  │No
     ▼     ▼
┌────────┐ ┌──────┐
│ STORE  │ │ ERR  │
│BUFFER  │ │ RESP │
└───┬────┘ └──────┘
    │ Store in PSRAM
    ▼
┌────────┐
│ READY  │
└───┬────┘
    │ DDISPLAY command
    ▼
┌───────────┐
│ REFRESH   │
└─────┬─────┘
      │
      │ 1. Reset EPD
      │ 2. Init commands
      │ 3. Write 15000 bytes
      │ 4. Trigger refresh
      ▼
┌───────────┐
│ WAIT_DONE │
└─────┬─────┘
      │ Wait for BUSY pin (2-4s)
   ┌──┴─┐
   │Done?│
   └─┬──┴┬─┘
     │Yes│No
     ▼   ▼
 ┌─────┐ ┌──────┐
 │SLEEP│ │ ERR  │
 │0x10 │ │ RESP │
 └─┬───┘ └───┬──┘
   │         │
   └────┬────┘
        ▼
     ┌────────┐
     │  IDLE  │
     └────────┘
Sound Detection & Alarm State Machine
    ┌────────┐
    │  IDLE  │
    └───┬────┘
        │
        │ ┌─────────────────────────────────────┐
        ├─▶│ BACKGROUND: Audio Processing        │
        │  │                                    │
        │  │ Record 1s → YAMNet → Check Crying │
        │  └────────┬───────────────────────────┘
        │           │
        │           │ confidence > 0.8
        │           ▼
        │    ┌────────────────┐
        │    │ CRYING DETECTED│
        │    │ - Log event    │
        │    │ - Start timer  │
        │    └────────┬───────┘
        │             │
        │             │ Still crying?
        │        ┌────┴────┐
        │        │Yes     │No
        │        ▼        ▼
        │   ┌────────┐ ┌────────┐
        │   │ ALARM  │ │ CANCEL │
        │   │ ACTIVE │ │ ALARM  │
        │   └───┬────┘ └────────┘
        │       │
        │       │ 1. Play alarm
        │       │ 2. Record audio
        │       │ 3. Notify user
        │       ▼
        │   ┌────────────┐
        │   │  SENT      │
        │   └─────┬──────┘
        │         │ Ack/timeout
        └─────────┼─────────┐
                  ▼         │
               ┌────────┐   │
               │  IDLE  │◀──┘
               └────────┘
---