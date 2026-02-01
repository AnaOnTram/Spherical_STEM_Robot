"""Serial connection manager for ESP32 communication."""
import asyncio
import logging
import threading
from typing import Callable, Optional

import serial as pyserial  # Rename to avoid confusion with our module

from config import SERIAL_PORT, SERIAL_BAUDRATE, SERIAL_TIMEOUT
from .protocol import Command, Response, ResponseStatus

logger = logging.getLogger(__name__)


def resolve_port(port: str) -> str:
    """Resolve serial port, handling auto-detection.
    
    Args:
        port: Port path or "auto" for auto-detection
        
    Returns:
        Resolved port path
    """
    if port.lower() == "auto":
        try:
            from utils.esp32_port import find_esp32_port
            detected_port = find_esp32_port()
            if detected_port:
                logger.info(f"Auto-detected ESP32 at {detected_port}")
                return detected_port
            else:
                logger.warning("Auto-detection failed, falling back to /dev/ttyACM0")
                return "/dev/ttyACM0"
        except Exception as e:
            logger.error(f"Auto-detection error: {e}, falling back to /dev/ttyACM0")
            return "/dev/ttyACM0"
    return port


class SerialManager:
    """Manages UART communication with ESP32."""

    def __init__(
        self,
        port: str = SERIAL_PORT,
        baudrate: int = SERIAL_BAUDRATE,
        timeout: float = SERIAL_TIMEOUT,
    ):
        self.port = resolve_port(port)
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial: Optional[pyserial.Serial] = None
        self._lock = threading.Lock()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Check if serial connection is active."""
        return self._connected and self._serial is not None and self._serial.is_open

    def connect(self) -> bool:
        """Establish serial connection to ESP32."""
        with self._lock:
            if self._connected:
                return True
            try:
                self._serial = pyserial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    bytesize=pyserial.EIGHTBITS,
                    parity=pyserial.PARITY_NONE,
                    stopbits=pyserial.STOPBITS_ONE,
                    timeout=self.timeout,
                )
                self._connected = True
                logger.info(f"Connected to ESP32 on {self.port} at {self.baudrate} baud")
                return True
            except pyserial.SerialException as e:
                logger.error(f"Failed to connect to ESP32: {e}")
                self._connected = False
                return False

    def disconnect(self) -> None:
        """Close serial connection."""
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()
            self._serial = None
            self._connected = False
            logger.info("Disconnected from ESP32")

    def send_command(self, command: Command) -> Response:
        """Send command and wait for response.

        Args:
            command: Command to send

        Returns:
            Response from ESP32
        """
        with self._lock:
            if not self.is_connected:
                return Response(ResponseStatus.ERR, "Not connected")

            try:
                # Clear input buffer
                self._serial.reset_input_buffer()

                # Send command
                encoded = command.encode()
                self._serial.write(encoded)
                self._serial.flush()
                logger.debug(f"Sent: {command.cmd_type.value}")

                # Read response
                response_data = b""
                lines_read = 0
                while lines_read < 2:  # Status line + message line
                    line = self._serial.readline()
                    if not line:
                        break
                    response_data += line
                    lines_read += 1

                if not response_data:
                    return Response(ResponseStatus.ERR, "No response (timeout)")

                response = Response.decode(response_data)
                logger.debug(f"Received: {response.status.value} - {response.message}")
                return response

            except pyserial.SerialException as e:
                logger.error(f"Serial error: {e}")
                return Response(ResponseStatus.ERR, str(e))

    async def send_command_async(self, command: Command) -> Response:
        """Send command asynchronously."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.send_command, command)

    def send_async(self, command: Command, callback: Callable[[Response], None]) -> None:
        """Send command asynchronously with callback.

        Args:
            command: Command to send
            callback: Callback function to receive response
        """
        def _send():
            response = self.send_command(command)
            callback(response)

        thread = threading.Thread(target=_send, daemon=True)
        thread.start()

    def ping(self) -> bool:
        """Send ping to check ESP32 connection."""
        from .commands import CommandBuilder
        response = self.send_command(CommandBuilder.system_ping())
        return response.status == ResponseStatus.OK
