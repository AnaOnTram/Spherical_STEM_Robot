"""Command builders for ESP32 communication."""
from .protocol import Command, CommandType, Protocol


class CommandBuilder:
    """Builder for ESP32 commands."""

    @staticmethod
    def motor_velocity(left: int, right: int, duration_ms: int = 0) -> Command:
        """Build motor velocity command.

        Args:
            left: Left motor speed (-255 to 255)
            right: Right motor speed (-255 to 255)
            duration_ms: Duration in milliseconds (0 = indefinite)
        """
        data = Protocol.pack_motor_velocity(left, right, duration_ms)
        return Command(CommandType.MVEL, data)

    @staticmethod
    def motor_stop() -> Command:
        """Build emergency stop command."""
        return Command(CommandType.MSTOP)

    @staticmethod
    def display_image(image_data: bytes) -> Command:
        """Build display image command.

        Args:
            image_data: 1-bit packed image data (15000 bytes for 400x300)
        """
        if len(image_data) != 15000:
            raise ValueError(f"Image data must be 15000 bytes, got {len(image_data)}")
        return Command(CommandType.DIMG, image_data)

    @staticmethod
    def display_clear() -> Command:
        """Build clear display command."""
        return Command(CommandType.DCLEAR)

    @staticmethod
    def display_status() -> Command:
        """Build display status command."""
        return Command(CommandType.DSTATUS)

    @staticmethod
    def system_reset() -> Command:
        """Build soft reset command."""
        return Command(CommandType.SRESET)

    @staticmethod
    def system_halt() -> Command:
        """Build halt/deep sleep command."""
        return Command(CommandType.SHALT)

    @staticmethod
    def system_ping() -> Command:
        """Build heartbeat/ping command."""
        return Command(CommandType.SPING)
