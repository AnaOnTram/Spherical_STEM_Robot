# EPaper AP Portal

ESP32-based Access Point with web portal for 4.2" V2 E-Paper Display (400x300 pixels).

## Features

- **WiFi Access Point**: Direct connection without external network
- **Captive Portal**: Auto-redirects to web interface on connection
- **Mobile-Friendly UI**: Touch-optimized responsive design
- **Image Cropping**: Interactive crop area with aspect ratio lock
- **Floyd-Steinberg Dithering**: High-quality mono conversion
- **Brightness/Contrast**: Real-time adjustment controls
- **HEIC Support**: Native support on Safari (iOS/macOS)
- **REST API**: Full API support for programmatic access

## Hardware Requirements

- ESP32 Development Board (Waveshare E-Paper Driver Board recommended)
- 4.2" V2 E-Paper Display (400x300, monochrome)

## Pin Configuration

| Signal | GPIO |
|--------|------|
| SCK    | 13   |
| DIN    | 14   |
| CS     | 15   |
| BUSY   | 25   |
| RST    | 26   |
| DC     | 27   |
| PWR    | 33   |

## Installation

1. Install Arduino IDE with ESP32 board support
2. Install required libraries:
   - ArduinoJson (v6.x)
3. Open `EPaper_AP_Portal.ino`
4. Select your ESP32 board (ESP32 Dev Module)
5. Upload to device

## Usage

1. Power on the ESP32
2. Connect to WiFi: `EPaper-Display` (password: configurable in config.h)
3. Open browser - captive portal will redirect to interface
4. Or navigate to `http://192.168.4.1`

## Web Interface

- **Upload**: Tap drop zone or drag & drop image
- **Crop**: Drag crop overlay or resize using corner handles
- **Adjust**: Use brightness/contrast sliders
- **Send**: Tap "Send to Display" button
- **Gen Test Image**: Generate a test gradient image for verification

## Supported Image Formats

| Format | Chrome | Safari (iOS/Mac) | Firefox |
|--------|--------|------------------|---------|
| JPEG   | ✅     | ✅               | ✅      |
| PNG    | ✅     | ✅               | ✅      |
| WebP   | ✅     | ✅               | ✅      |
| HEIC   | ❌     | ✅               | ❌      |

*HEIC files from iPhone work natively on Safari. For other browsers, convert to JPG first.*

## State Machine Diagram

### System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            ESP32 E-Paper Portal                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│              │     │              │     │              │     │              │
│    BOOT      │────▶│   AP_INIT    │────▶│   READY      │────▶│   SERVING    │
│              │     │              │     │              │     │              │
└──────────────┘     └──────────────┘     └──────────────┘     └──────┬───────┘
                                                                       │
                                          ┌────────────────────────────┘
                                          ▼
                     ┌─────────────────────────────────────────────────────────┐
                     │                    REQUEST HANDLER                       │
                     ├─────────────────────────────────────────────────────────┤
                     │  GET /           → Serve HTML                           │
                     │  GET /styles.css → Serve CSS                            │
                     │  GET /app.js     → Serve JavaScript                     │
                     │  POST /api/*     → Handle API calls                     │
                     └─────────────────────────────────────────────────────────┘
```

### Image Upload State Machine

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CLIENT (Browser JavaScript)                          │
└─────────────────────────────────────────────────────────────────────────────┘

    ┌───────────┐
    │   IDLE    │◀──────────────────────────────────────────────────────┐
    └─────┬─────┘                                                       │
          │ User selects image                                          │
          ▼                                                             │
    ┌───────────┐                                                       │
    │   LOAD    │  Try: createImageBitmap → DataURL → BlobURL          │
    └─────┬─────┘                                                       │
          │ Image loaded                                                │
          ▼                                                             │
    ┌───────────┐                                                       │
    │   CROP    │  User adjusts crop area (4:3 aspect ratio)           │
    └─────┬─────┘                                                       │
          │ User clicks "Send to Display"                               │
          ▼                                                             │
    ┌───────────┐                                                       │
    │  DITHER   │  Floyd-Steinberg algorithm → 1-bit mono              │
    └─────┬─────┘                                                       │
          │                                                             │
          ▼                                                             │
    ┌───────────┐                                                       │
    │   PACK    │  8 pixels → 1 byte (MSB first)                       │
    └─────┬─────┘                                                       │
          │                                                             │
          ▼                                                             │
    ┌───────────┐                                                       │
    │  ENCODE   │  Byte → 2 chars (nibble + 'a')                       │
    └─────┬─────┘                                                       │
          │                                                             │
          ▼                                                             │
    ┌───────────┐      POST /api/upload                                 │
    │  UPLOAD   │───────────────────────────────────────┐               │
    │ (chunked) │      X-Upload-Start header (1st chunk)│               │
    └─────┬─────┘                                       │               │
          │                                             ▼               │
          │                              ┌──────────────────────────┐   │
          │                              │      ESP32 SERVER        │   │
          │                              ├──────────────────────────┤   │
          │                              │ 1. Clear buffer (0x00)   │   │
          │                              │ 2. Decode nibbles        │   │
          │                              │ 3. Store in buffer       │   │
          │                              │ 4. Return progress       │   │
          │                              └──────────────────────────┘   │
          │ All chunks sent                                             │
          ▼                                                             │
    ┌───────────┐      POST /api/display                                │
    │  DISPLAY  │─────────────────────────────────────────┐             │
    └─────┬─────┘                                         │             │
          │                                               ▼             │
          │                              ┌──────────────────────────┐   │
          │                              │    EPD CONTROLLER        │   │
          │                              ├──────────────────────────┤   │
          │                              │ 1. Reset display         │   │
          │                              │ 2. Send init commands    │   │
          │                              │ 3. Write image RAM       │   │
          │                              │ 4. Trigger refresh       │   │
          │                              │ 5. Enter deep sleep      │   │
          │                              └──────────────────────────┘   │
          │                                                             │
          ▼                                                             │
    ┌───────────┐                                                       │
    │ COMPLETE  │───────────────────────────────────────────────────────┘
    └───────────┘
```

### E-Paper Display State Machine

```
    ┌───────────┐
    │   SLEEP   │◀─────────────────────────────────────────┐
    └─────┬─────┘                                          │
          │ Display command received                       │
          ▼                                                │
    ┌───────────┐                                          │
    │   RESET   │  RST pin: HIGH→LOW→HIGH (200ms delays)  │
    └─────┬─────┘                                          │
          │                                                │
          ▼                                                │
    ┌───────────┐                                          │
    │WAIT_READY │  Poll BUSY pin until LOW                │
    └─────┬─────┘                                          │
          │                                                │
          ▼                                                │
    ┌───────────┐                                          │
    │   INIT    │  Send configuration commands            │
    │           │  - Display update control               │
    │           │  - Border waveform                      │
    │           │  - Data entry mode                      │
    │           │  - RAM address setup                    │
    └─────┬─────┘                                          │
          │                                                │
          ▼                                                │
    ┌───────────┐                                          │
    │WRITE_RAM  │  Send 15000 bytes via SPI               │
    │           │  Command 0x24 + image data              │
    └─────┬─────┘                                          │
          │                                                │
          ▼                                                │
    ┌───────────┐                                          │
    │  REFRESH  │  Command 0x22 (0xF7) + 0x20            │
    └─────┬─────┘                                          │
          │                                                │
          ▼                                                │
    ┌───────────┐                                          │
    │WAIT_DONE  │  Poll BUSY pin until LOW (~2-4 sec)    │
    └─────┬─────┘                                          │
          │                                                │
          ▼                                                │
    ┌───────────┐                                          │
    │DEEP_SLEEP │  Command 0x10 (0x01)                    │
    └─────┬─────┘                                          │
          │                                                │
          └────────────────────────────────────────────────┘
```

### Data Encoding Flow

```
Original Image (RGB)
        │
        ▼
┌───────────────────┐
│  Grayscale Conv.  │  Y = 0.299R + 0.587G + 0.114B
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Brightness/Contrast│  Adjustable -100 to +100
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Floyd-Steinberg   │  Error diffusion dithering
│    Dithering      │  Threshold: 128
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│  1-bit Packing    │  8 pixels → 1 byte
│                   │  White=1, Black=0, MSB first
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Nibble Encoding   │  Byte 0xAB → "ba" (low, high + 'a')
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│  Chunked Upload   │  1000 chars per chunk
│                   │  (~500 bytes decoded)
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│   Image Buffer    │  15000 bytes (400×300÷8)
└───────────────────┘
```

## REST API

### GET /api/status
Returns display and system status.

```json
{
  "status": "success",
  "display": { "width": 400, "height": 300, "model": "4.2 inch V2" },
  "buffer": { "size": 15000, "filled": 0, "ready": false },
  "api": { "version": "1.0" },
  "heap": { "free": 200000, "total": 320000 }
}
```

### POST /api/upload
Upload image data as nibble-encoded 1-bit packed format.

**Headers:**
- `Content-Type: text/plain`
- `X-Upload-Start: 1` (required for first chunk - triggers buffer clear)

**Body:** Encoded string where each byte becomes 2 characters:
- First char: `(low_nibble + 'a')`
- Second char: `(high_nibble + 'a')`

Example: Byte `0xF0` → `"ap"` (0+'a'=a, 15+'a'=p)

### POST /api/display
Render buffered image to display.

### POST /api/clear
Clear display (all white).

### POST /api/sleep
Put display into deep sleep mode.

### POST /api/test
Display test pattern (checkerboard).

## Configuration

Edit `config.h` to customize:

```cpp
// WiFi Settings
const char* AP_SSID = "EPaper-Display";
const char* AP_PASSWORD = "epaper123";

// Display Settings
#define EPD_WIDTH   400
#define EPD_HEIGHT  300
```

## Image Processing Pipeline

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  SELECT  │───▶│   CROP   │───▶│  DITHER  │───▶│   PACK   │───▶│  UPLOAD  │
│  IMAGE   │    │  (4:3)   │    │  (F-S)   │    │ (1-bit)  │    │ (chunks) │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
     │               │               │               │               │
     ▼               ▼               ▼               ▼               ▼
  Browser        Browser         Browser         Browser          ESP32
  loads file     canvas          canvas          Uint8Array       receives
  (HEIC/JPG/     shows crop      shows B&W       15000 bytes      & stores
   PNG)          preview         preview         packed           in buffer
```

## Memory Usage

- Image buffer: 15KB (in PSRAM if available)
- Web resources: ~35KB (stored in flash via PROGMEM)
- Free heap for operation: ~200KB typical

## File Structure

```
EPaper_AP_Portal/
├── EPaper_AP_Portal.ino  # Main sketch
├── config.h              # Configuration
├── epd_driver.h          # E-Paper driver (4.2" V2)
├── image_buffer.h        # Image buffer management
├── web_server.h          # HTTP server & REST API
├── web_html.h            # HTML interface
├── web_css.h             # CSS styles (mobile-optimized)
├── web_js.h              # JavaScript (dithering, upload)
└── README.md             # This file
```

## Changelog

### v1.1.0
- Added HEIC image support (Safari on iOS/macOS)
- Fixed buffer clearing issue - new uploads now properly replace previous images
- Added "Gen Test Image" button for testing without file upload
- Improved image loading with multiple fallback methods
- Added `X-Upload-Start` header detection for automatic buffer clearing

### v1.0.0
- Initial release
- WiFi AP with captive portal
- Floyd-Steinberg dithering
- Mobile-friendly responsive UI
- REST API support

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Display shows all black | Check SPI connections, verify BUSY pin |
| New image shows old content | Ensure `X-Upload-Start` header is sent |
| HEIC won't load | Use Safari on iOS/Mac, or convert to JPG |
| Upload stuck | Check serial monitor for buffer status |
| Preview looks wrong | Adjust brightness/contrast sliders |

## License

MIT License
