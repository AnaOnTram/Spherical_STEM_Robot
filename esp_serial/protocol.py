"""Protocol encoder/decoder for ESP32 communication."""
import struct
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class CommandType(Enum):
    """Command types for ESP32 communication."""
    # Motion commands
    MVEL = "MVEL"      # Motor velocity
    MSTOP = "MSTOP"    # Emergency stop
    # Display commands
    DIMG = "DIMG"      # Display image
    DCLEAR = "DCLEAR"  # Clear display
    DSTATUS = "DSTATUS"  # Display status
    # System commands
    SRESET = "SRESET"  # Soft reset
    SHALT = "SHALT"    # Enter deep sleep
    SPING = "SPING"    # Heartbeat/ping


class ResponseStatus(Enum):
    """Response status from ESP32."""
    OK = "OK"
    ERR = "ERR"
    PENDING = "PENDING"


@dataclass
class Command:
    """Command to send to ESP32."""
    cmd_type: CommandType
    data: bytes = b""

    def encode(self) -> bytes:
        """Encode command to wire format: <CMD><PARAM_LENGTH>\n<DATA>\n<CRC>"""
        header = f"{self.cmd_type.value}{len(self.data)}\n".encode()
        crc = Protocol.calculate_crc(self.data)
        return header + self.data + b"\n" + crc.encode() + b"\n"


@dataclass
class Response:
    """Response from ESP32."""
    status: ResponseStatus
    message: str = ""

    @classmethod
    def decode(cls, data: bytes) -> "Response":
        """Decode response from wire format: <STATUS><MESSAGE_LENGTH>\n<MESSAGE>\n"""
        try:
            lines = data.decode().strip().split("\n")
            if not lines:
                return cls(ResponseStatus.ERR, "Empty response")

            # Parse status and length from first line
            status_line = lines[0]
            for status in ResponseStatus:
                if status_line.startswith(status.value):
                    length_str = status_line[len(status.value):]
                    msg_length = int(length_str) if length_str else 0
                    message = lines[1] if len(lines) > 1 else ""
                    return cls(status, message[:msg_length] if msg_length else message)

            return cls(ResponseStatus.ERR, f"Unknown status: {status_line}")
        except Exception as e:
            return cls(ResponseStatus.ERR, str(e))


class Protocol:
    """Protocol utilities for ESP32 communication."""

    CRC_POLYNOMIAL = 0x1021  # CRC-CCITT

    @staticmethod
    def calculate_crc(data: bytes) -> str:
        """Calculate 16-bit CRC-CCITT for data."""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte << 8
            for _ in range(8):
                if crc & 0x8000:
                    crc = (crc << 1) ^ Protocol.CRC_POLYNOMIAL
                else:
                    crc <<= 1
                crc &= 0xFFFF
        return f"{crc:04X}"

    @staticmethod
    def pack_motor_velocity(left: int, right: int, duration_ms: int = 0) -> bytes:
        """Pack motor velocity command data.

        Args:
            left: Left motor speed (-255 to 255)
            right: Right motor speed (-255 to 255)
            duration_ms: Duration in milliseconds (0 = indefinite)
        """
        left = max(-255, min(255, left))
        right = max(-255, min(255, right))
        duration_ms = max(0, min(65535, duration_ms))
        return struct.pack("<hhH", left, right, duration_ms)

    @staticmethod
    def unpack_motor_velocity(data: bytes) -> tuple[int, int, int]:
        """Unpack motor velocity data."""
        return struct.unpack("<hhH", data)
