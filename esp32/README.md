# ESP32 Combined Firmware for Spherical Robot

This firmware combines motor control and E-Paper display functionality for the ESP32, designed to work with the Raspberry Pi 5 via serial communication.

## Features

- **Motor Control**: Dual motor control with PWM (A1/A2 for left, B1/B2 for right)
- **E-Paper Display**: 4.2" V2 E-Paper display control (400x300 pixels, 1-bit monochrome)
- **Serial Protocol**: Binary protocol with CRC checking for reliable communication
- **Real-time Control**: Motor timeout support for timed movements

## Hardware Connections

### Motor Control Pins
- **Motor A (Left)**: GPIO 1 (A1), GPIO 2 (A2)
- **Motor B (Right)**: GPIO 42 (B1), GPIO 41 (B2)

### E-Paper Display Pins (SPI)
- **SCK**: GPIO 13
- **MOSI (DIN)**: GPIO 14
- **CS**: GPIO 15
- **BUSY**: GPIO 25 (Input)
- **RST**: GPIO 26
- **DC**: GPIO 27
- **PWR**: GPIO 33

### Serial Communication
- **UART**: Serial0 (USB/UART bridge)
- **Baud Rate**: 115200
- **Pi5 Connection**: Connect Pi5 GPIO14 (TX) to ESP32 GPIO16 (RX), Pi5 GPIO15 (RX) to ESP32 GPIO17 (TX), and GND to GND

## Protocol Format

Commands are sent in the format:
```
<CMD><PARAM_LENGTH>\n<DATA>\n<CRC>\n
```

### Examples:

**Motor Velocity (MVEL)**:
```
MVEL6\n<6 bytes of data>\n<4 hex CRC chars>\n
```
Data format (little-endian):
- Bytes 0-1: Left motor speed (int16, -255 to 255)
- Bytes 2-3: Right motor speed (int16, -255 to 255)
- Bytes 4-5: Duration in ms (uint16, 0 = indefinite)

**Display Image (DIMG)**:
```
DIMG15000\n<15000 bytes of 1-bit packed image>\n<4 hex CRC chars>\n
```

**Clear Display (DCLEAR)**:
```
DCLEAR0\n\n0000\n
```

## Command Reference

### Motion Commands

| Command | Description | Data Format |
|---------|-------------|-------------|
| `MVEL` | Set motor velocity | 6 bytes: left(int16), right(int16), duration(uint16) |
| `MSTOP` | Emergency stop | No data |

### Display Commands

| Command | Description | Data Format |
|---------|-------------|-------------|
| `DIMG` | Display image | 15000 bytes (400x300/8, 1-bit packed) |
| `DCLEAR` | Clear display | No data |
| `DSTATUS` | Get display status | No data |

### System Commands

| Command | Description | Data Format |
|---------|-------------|-------------|
| `SRESET` | Soft reset | No data |
| `SHALT` | Enter deep sleep | No data |
| `SPING` | Heartbeat/ping | No data |

## Response Format

Responses are sent as:
```
<STATUS><MSG_LENGTH>\n<MESSAGE>\n
```

Status codes:
- `OK` - Command successful
- `ERR` - Command failed
- `PENDING` - Command in progress

## Installation

1. Install Arduino IDE or PlatformIO
2. Install ESP32 board support (version 3.x or later)
3. Open `esp32_firmware.ino` in Arduino IDE
4. Select board: "ESP32-S3 Dev Module" (or your specific ESP32 variant)
5. Upload to ESP32

## Testing

Use the Python serial manager to test:

```python
from esp_serial import SerialManager, CommandBuilder

# Initialize
manager = SerialManager("/dev/ttyUSB0")
manager.connect()

# Test motors
cmd = CommandBuilder.motor_velocity(200, 200, 1000)  # Forward for 1 second
manager.send_command(cmd)

# Test display
cmd = CommandBuilder.display_clear()
manager.send_command(cmd)
```

## Notes

- Motor speeds are automatically constrained to -255 to 255
- Display image data must be exactly 15000 bytes (400x300 pixels, 1-bit packed)
- In 1-bit packed format: 0 = black, 1 = white
- CRC is calculated using CRC-CCITT (polynomial 0x1021)
- Motors automatically stop after specified duration (if set)
- E-Paper display enters deep sleep after refresh to save power

## Troubleshooting

1. **No response from ESP32**: Check serial connections and baud rate (115200)
2. **CRC errors**: Verify data length and CRC calculation
3. **Motors not moving**: Check motor driver connections and power supply
4. **Display not updating**: Verify SPI connections and image buffer size (15000 bytes)
5. **Image appears inverted**: Check bit packing (0=black, 1=white)
