#!/bin/bash
# Serial port setup script for Raspberry Pi 5

echo "=== Raspberry Pi 5 Serial Port Setup ==="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root: sudo bash setup_serial.sh"
    exit 1
fi

echo "1. Checking current serial configuration..."

# Check config.txt
echo "   Checking /boot/firmware/config.txt..."
if grep -q "enable_uart=1" /boot/firmware/config.txt; then
    echo "   ✓ UART already enabled"
else
    echo "   Enabling UART..."
    echo "enable_uart=1" >> /boot/firmware/config.txt
    echo "   ✓ UART enabled"
fi

# Check for console on serial
if systemctl is-active --quiet serial-getty@ttyAMA0.service 2>/dev/null; then
    echo "   ⚠ Serial console is active on ttyAMA0"
    echo "   Stopping serial console..."
    systemctl stop serial-getty@ttyAMA0.service
    systemctl disable serial-getty@ttyAMA0.service
    echo "   ✓ Serial console disabled"
else
    echo "   ✓ No serial console on ttyAMA0"
fi

# Check device tree overlay
echo ""
echo "2. Checking device tree configuration..."
if grep -q "dtoverlay=disable-bt" /boot/firmware/config.txt; then
    echo "   ✓ Bluetooth disabled (frees up UART0)"
else
    echo "   Note: Bluetooth is using UART0"
    echo "   To disable Bluetooth and use UART0 for GPIO:"
    echo "   Add to /boot/firmware/config.txt: dtoverlay=disable-bt"
fi

echo ""
echo "3. Checking user permissions..."
USERNAME=${SUDO_USER:-$USER}
if groups $USERNAME | grep -q "dialout"; then
    echo "   ✓ User $USERNAME is in dialout group"
else
    echo "   Adding user $USERNAME to dialout group..."
    usermod -a -G dialout $USERNAME
    echo "   ✓ User added to dialout group"
    echo "   ⚠ Please logout and login again for changes to take effect"
fi

echo ""
echo "4. Testing serial port..."
if [ -e /dev/ttyAMA0 ]; then
    echo "   ✓ /dev/ttyAMA0 exists"
    ls -la /dev/ttyAMA0
    
    # Check permissions
    if [ -r /dev/ttyAMA0 ] && [ -w /dev/ttyAMA0 ]; then
        echo "   ✓ Port is readable and writable"
    else
        echo "   ⚠ Port permissions issue"
        chmod 666 /dev/ttyAMA0
        echo "   ✓ Fixed permissions"
    fi
else
    echo "   ✗ /dev/ttyAMA0 does not exist"
fi

echo ""
echo "5. Serial port aliases..."
ls -la /dev/serial* 2>/dev/null || echo "   No serial aliases found"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "1. Reboot the Pi: sudo reboot"
echo "2. After reboot, test with: python3 utils/serial_detect.py"
echo "3. Connect ESP32:"
echo "   Pi5 GPIO14 (TX) -> ESP32 RX"
echo "   Pi5 GPIO15 (RX) -> ESP32 TX"
echo "   GND -> GND"
echo ""
echo "Note: If using GPIO pins, ensure you have disabled serial console."
echo "If using USB-to-Serial adapter, use /dev/ttyUSB0 instead."
