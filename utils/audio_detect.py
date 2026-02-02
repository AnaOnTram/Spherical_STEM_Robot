"""USB Audio device auto-detection utility for robust device mapping."""
import logging
import re
import subprocess
from typing import Optional, List, Dict, Tuple

logger = logging.getLogger(__name__)


class AudioDeviceInfo:
    """Information about an audio device."""
    
    def __init__(
        self,
        card_num: int,
        device_num: int,
        name: str,
        description: str,
        is_usb: bool = False,
        is_hdmi: bool = False,
        is_bluetooth: bool = False,
    ):
        self.card_num = card_num
        self.device_num = device_num
        self.name = name
        self.description = description
        self.is_usb = is_usb
        self.is_hdmi = is_hdmi
        self.is_bluetooth = is_bluetooth
    
    @property
    def hw_device(self) -> str:
        """Get hw:X,Y format device string."""
        return f"hw:{self.card_num},{self.device_num}"
    
    @property
    def plug_device(self) -> str:
        """Get plughw:X,Y format device string."""
        return f"plughw:{self.card_num},{self.device_num}"
    
    def __repr__(self) -> str:
        return f"AudioDevice({self.hw_device}: {self.description})"


def _run_aplay() -> str:
    """Run aplay -l and return output."""
    try:
        result = subprocess.run(
            ["aplay", "-l"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.stdout if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        logger.debug(f"aplay command failed: {e}")
        return ""


def _run_arecord() -> str:
    """Run arecord -l and return output."""
    try:
        result = subprocess.run(
            ["arecord", "-l"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.stdout if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        logger.debug(f"arecord command failed: {e}")
        return ""


def _parse_device_list(output: str) -> List[AudioDeviceInfo]:
    """Parse aplay/arecord -l output into device list."""
    devices = []
    
    # Pattern to match: card X: NAME [ID], device Y: DESCRIPTION
    # Example: card 0: b1 [bcm2835 HDMI 1], device 0: MAI PCM [bcm2835 HDMI 1]
    pattern = r'card\s+(\d+):\s+([^[]+)\[([^\]]+)\],\s+device\s+(\d+):\s+([^[]+)\[([^\]]+)\]'
    
    for match in re.finditer(pattern, output):
        card_num = int(match.group(1))
        card_name = match.group(2).strip()
        card_id = match.group(3).strip()
        device_num = int(match.group(4))
        device_name = match.group(5).strip()
        device_desc = match.group(6).strip()
        
        # Determine device type from card ID and description
        desc_lower = (card_id + " " + device_desc).lower()
        is_usb = "usb" in desc_lower
        is_hdmi = "hdmi" in desc_lower or "bcm2835 hdmi" in desc_lower
        is_bluetooth = "bluetooth" in desc_lower or "bluez" in desc_lower
        
        device = AudioDeviceInfo(
            card_num=card_num,
            device_num=device_num,
            name=card_name,
            description=f"{card_id}: {device_desc}",
            is_usb=is_usb,
            is_hdmi=is_hdmi,
            is_bluetooth=is_bluetooth,
        )
        devices.append(device)
    
    return devices


def get_playback_devices() -> List[AudioDeviceInfo]:
    """Get list of available playback devices."""
    output = _run_aplay()
    return _parse_device_list(output)


def get_capture_devices() -> List[AudioDeviceInfo]:
    """Get list of available capture (recording) devices."""
    output = _run_arecord()
    return _parse_device_list(output)


def find_usb_playback_device() -> Optional[AudioDeviceInfo]:
    """Find a USB playback device.
    
    Returns:
        AudioDeviceInfo for first USB playback device, or None if not found.
    """
    devices = get_playback_devices()
    
    # First try to find USB device
    for device in devices:
        if device.is_usb:
            logger.info(f"Found USB playback device: {device}")
            return device
    
    # If no USB, try to find any non-HDMI, non-bluetooth device
    for device in devices:
        if not device.is_hdmi and not device.is_bluetooth:
            logger.info(f"Found non-HDMI playback device: {device}")
            return device
    
    # Last resort: return any device that's not HDMI
    for device in devices:
        if not device.is_hdmi:
            logger.info(f"Found fallback playback device: {device}")
            return device
    
    logger.warning("No suitable playback device found")
    return None


def find_usb_capture_device() -> Optional[AudioDeviceInfo]:
    """Find a USB capture (microphone) device.
    
    Returns:
        AudioDeviceInfo for first USB capture device, or None if not found.
    """
    devices = get_capture_devices()
    
    # First try to find USB device
    for device in devices:
        if device.is_usb:
            logger.info(f"Found USB capture device: {device}")
            return device
    
    # If no USB, try to find any non-bluetooth device
    for device in devices:
        if not device.is_bluetooth:
            logger.info(f"Found capture device: {device}")
            return device
    
    logger.warning("No suitable capture device found")
    return None


def get_auto_playback_device(fallback: str = "default") -> str:
    """Get playback device string with auto-detection.
    
    Args:
        fallback: Fallback device string if auto-detection fails.
        
    Returns:
        Device string (e.g., "plughw:1,0" or "default").
    """
    device = find_usb_playback_device()
    if device:
        return device.plug_device
    logger.warning(f"Auto-detection failed, using fallback: {fallback}")
    return fallback


def get_auto_capture_device(fallback: str = "default") -> str:
    """Get capture device string with auto-detection.
    
    Args:
        fallback: Fallback device string if auto-detection fails.
        
    Returns:
        Device string (e.g., "hw:2,0" or "default").
    """
    device = find_usb_capture_device()
    if device:
        # For capture, prefer hw: device for lower latency
        return device.hw_device
    logger.warning(f"Auto-detection failed, using fallback: {fallback}")
    return fallback


def test_device_access(device: str, is_capture: bool = False) -> bool:
    """Test if an audio device is accessible.
    
    Args:
        device: Device string to test.
        is_capture: True for capture device, False for playback.
        
    Returns:
        True if device is accessible.
    """
    try:
        import alsaaudio
        
        pcm_type = alsaaudio.PCM_CAPTURE if is_capture else alsaaudio.PCM_PLAYBACK
        pcm = alsaaudio.PCM(
            type=pcm_type,
            mode=alsaaudio.PCM_NONBLOCK,
            device=device,
        )
        pcm.close()
        return True
    except Exception as e:
        logger.debug(f"Device {device} not accessible: {e}")
        return False


def get_working_playback_device(preferred: Optional[str] = None) -> str:
    """Get a working playback device, with optional preference.
    
    Args:
        preferred: Preferred device string to try first.
        
    Returns:
        Working device string.
    """
    # Try preferred device first
    if preferred and preferred != "auto":
        if test_device_access(preferred, is_capture=False):
            logger.info(f"Using preferred playback device: {preferred}")
            return preferred
        logger.warning(f"Preferred playback device {preferred} not accessible")
    
    # Try auto-detection
    auto_device = get_auto_playback_device()
    if auto_device != "default" and test_device_access(auto_device, is_capture=False):
        logger.info(f"Using auto-detected playback device: {auto_device}")
        return auto_device
    
    # Try "default"
    if test_device_access("default", is_capture=False):
        logger.info("Using default playback device")
        return "default"
    
    # Last resort: return preferred anyway, let it fail gracefully
    logger.error("No working playback device found")
    return preferred or "default"


def get_working_capture_device(preferred: Optional[str] = None) -> str:
    """Get a working capture device, with optional preference.
    
    Args:
        preferred: Preferred device string to try first.
        
    Returns:
        Working device string.
    """
    # Try preferred device first
    if preferred and preferred != "auto":
        if test_device_access(preferred, is_capture=True):
            logger.info(f"Using preferred capture device: {preferred}")
            return preferred
        logger.warning(f"Preferred capture device {preferred} not accessible")
    
    # Try auto-detection
    auto_device = get_auto_capture_device()
    if auto_device != "default" and test_device_access(auto_device, is_capture=True):
        logger.info(f"Using auto-detected capture device: {auto_device}")
        return auto_device
    
    # Try "default"
    if test_device_access("default", is_capture=True):
        logger.info("Using default capture device")
        return "default"
    
    # Last resort: return preferred anyway, let it fail gracefully
    logger.error("No working capture device found")
    return preferred or "default"


def list_all_devices() -> Tuple[List[AudioDeviceInfo], List[AudioDeviceInfo]]:
    """List all audio devices for debugging.
    
    Returns:
        Tuple of (playback_devices, capture_devices).
    """
    playback = get_playback_devices()
    capture = get_capture_devices()
    
    print("\n=== Playback Devices ===")
    for device in playback:
        usb_marker = " [USB]" if device.is_usb else ""
        print(f"  {device.hw_device}: {device.description}{usb_marker}")
    
    print("\n=== Capture Devices ===")
    for device in capture:
        usb_marker = " [USB]" if device.is_usb else ""
        print(f"  {device.hw_device}: {device.description}{usb_marker}")
    
    return playback, capture


if __name__ == "__main__":
    # Test the module
    logging.basicConfig(level=logging.INFO)
    
    print("Audio Device Auto-Detection Test")
    print("=" * 50)
    
    list_all_devices()
    
    print("\n=== Auto-Detection Results ===")
    playback = get_auto_playback_device()
    capture = get_auto_capture_device()
    print(f"Auto playback device: {playback}")
    print(f"Auto capture device: {capture}")
    
    print("\n=== Working Device Test ===")
    working_playback = get_working_playback_device()
    working_capture = get_working_capture_device()
    print(f"Working playback device: {working_playback}")
    print(f"Working capture device: {working_capture}")
