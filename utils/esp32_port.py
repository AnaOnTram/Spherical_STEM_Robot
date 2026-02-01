"""ESP32 Serial Port Auto-Detector

This module automatically finds the ESP32 serial port regardless of whether
it appears as /dev/ttyACM0, /dev/ttyACM1, /dev/ttyUSB0, etc.

Usage:
    from utils.esp32_port import find_esp32_port, get_esp32_serial
    
    # Find port
    port = find_esp32_port()
    if port:
        print(f"ESP32 found at: {port}")
    
    # Or use the serial manager with auto-detection
    from esp_serial import SerialManager
    manager = SerialManager(get_esp32_serial())
"""

import glob
import os
import subprocess
from typing import Optional


def get_usb_device_info(port: str) -> dict:
    """Get USB device information using udevadm."""
    info = {
        'vendor': '',
        'product': '',
        'serial': '',
        'manufacturer': ''
    }
    
    try:
        # Run udevadm to get device info
        result = subprocess.run(
            ['udevadm', 'info', '-a', '-n', port],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'ATTRS{idVendor}' in line:
                    info['vendor'] = line.split('==')[-1].strip('"')
                elif 'ATTRS{idProduct}' in line:
                    info['product'] = line.split('==')[-1].strip('"')
                elif 'ATTRS{serial}' in line:
                    info['serial'] = line.split('==')[-1].strip('"')
                elif 'ATTRS{manufacturer}' in line:
                    info['manufacturer'] = line.split('==')[-1].strip('"')
    except Exception:
        pass
    
    return info


def is_esp32_device(port: str) -> bool:
    """Check if a serial port is an ESP32 device."""
    info = get_usb_device_info(port)
    
    # Common ESP32 USB VID/PID combinations:
    # - Silicon Labs CP210x: 10c4:ea60, 10c4:ea70
    # - QinHeng CH340: 1a86:7523
    # - Espressif USB JTAG/serial: 303a:*
    esp32_vendors = {
        '10c4': ['ea60', 'ea70'],  # Silicon Labs
        '1a86': ['7523'],           # QinHeng
        '303a': None                # Espressif (any product)
    }
    
    vendor = info.get('vendor', '').lower()
    product = info.get('product', '').lower()
    manufacturer = info.get('manufacturer', '').lower()
    
    # Check by vendor/product ID
    if vendor in esp32_vendors:
        allowed_products = esp32_vendors[vendor]
        if allowed_products is None or product in allowed_products:
            return True
    
    # Check by manufacturer string
    if any(x in manufacturer for x in ['silicon labs', 'espressif', 'qinheng', 'wch']):
        return True
    
    return False


def find_esp32_port() -> Optional[str]:
    """Find the ESP32 serial port.
    
    Returns:
        Path to ESP32 serial port (e.g., '/dev/ttyACM0') or None if not found
    """
    # First check for persistent symlink
    if os.path.islink('/dev/esp32') and os.path.exists('/dev/esp32'):
        return '/dev/esp32'
    
    # Search for USB serial devices
    patterns = [
        '/dev/ttyACM*',  # USB CDC devices
        '/dev/ttyUSB*',  # USB-to-Serial adapters
    ]
    
    for pattern in patterns:
        for port in sorted(glob.glob(pattern)):
            if is_esp32_device(port):
                return port
    
    return None


def get_esp32_serial() -> str:
    """Get ESP32 serial port with fallback.
    
    Returns:
        Serial port path. Raises RuntimeError if not found.
    """
    port = find_esp32_port()
    if port:
        return port
    
    raise RuntimeError(
        "ESP32 not found! Please ensure:\n"
        "1. ESP32 is connected via USB\n"
        "2. ESP32 is powered on\n"
        "3. Run: python3 utils/esp32_port.py to scan for devices"
    )


def list_all_serial_ports():
    """List all serial ports with their device info."""
    print("Scanning for serial ports...\n")
    
    patterns = [
        '/dev/ttyACM*',
        '/dev/ttyUSB*',
        '/dev/ttyAMA*',
    ]
    
    found = False
    for pattern in patterns:
        ports = glob.glob(pattern)
        for port in sorted(ports):
            found = True
            info = get_usb_device_info(port)
            is_esp = is_esp32_device(port)
            
            print(f"Port: {port}")
            print(f"  Vendor ID:     {info['vendor'] or 'N/A'}")
            print(f"  Product ID:    {info['product'] or 'N/A'}")
            print(f"  Serial:        {info['serial'] or 'N/A'}")
            print(f"  Manufacturer:  {info['manufacturer'] or 'N/A'}")
            print(f"  Is ESP32:      {'✓ YES' if is_esp else 'No'}")
            print()
    
    if not found:
        print("No serial ports found!")
        print("\nTroubleshooting:")
        print("1. Check USB connection")
        print("2. Try: lsusb")
        print("3. Check dmesg: dmesg | tail -20")
    else:
        esp_port = find_esp32_port()
        if esp_port:
            print(f"✓ ESP32 detected at: {esp_port}")
        else:
            print("⚠ No ESP32 detected among the ports above")
            print("   Check if your ESP32 uses a different USB chip")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--find":
        port = find_esp32_port()
        if port:
            print(port)
        else:
            print("NOT_FOUND")
            sys.exit(1)
    else:
        list_all_serial_ports()
