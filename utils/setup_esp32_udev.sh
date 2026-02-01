#!/bin/bash
# Setup persistent USB serial port for ESP32
# This creates a udev rule so the device always appears at /dev/esp32

echo "=== ESP32 Persistent Serial Port Setup ==="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root: sudo bash setup_esp32_udev.sh"
    exit 1
fi

echo "1. Detecting ESP32 USB device..."

# Find ESP32 device
ESP32_FOUND=false
ESP32_ID_VENDOR=""
ESP32_ID_PRODUCT=""
ESP32_SERIAL=""

# Check for common ESP32 USB IDs
for sysdevpath in $(find /sys/bus/usb/devices/usb*/ -name dev); do
    syspath="${sysdevpath%/dev}"
    devname="$(udevadm info -q name -p $syspath)"
    
    # Check if it's a tty device
    if [[ "$devname" == "tty"* ]]; then
        # Get device info
        vendor=$(cat "$syspath/idVendor" 2>/dev/null)
        product=$(cat "$syspath/idProduct" 2>/dev/null)
        serial=$(cat "$syspath/serial" 2>/dev/null)
        manufacturer=$(cat "$syspath/manufacturer" 2>/dev/null)
        
        # Check if it's an ESP32 (Silicon Labs CP210x or USB JTAG/serial)
        if [[ "$vendor" == "10c4" && "$product" == "ea60" ]] || \
           [[ "$vendor" == "10c4" && "$product" == "ea70" ]] || \
           [[ "$vendor" == "1a86" && "$product" == "7523" ]] || \
           [[ "$vendor" == "303a" && "$product" == "*" ]] || \
           [[ "$manufacturer" == *"Silicon Labs"* ]] || \
           [[ "$manufacturer" == *"Espressif"* ]]; then
            
            echo "   Found ESP32 device:"
            echo "   - Port: /dev/$devname"
            echo "   - Vendor ID: $vendor"
            echo "   - Product ID: $product"
            [ -n "$serial" ] && echo "   - Serial: $serial"
            [ -n "$manufacturer" ] && echo "   - Manufacturer: $manufacturer"
            
            ESP32_FOUND=true
            ESP32_ID_VENDOR="$vendor"
            ESP32_ID_PRODUCT="$product"
            ESP32_SERIAL="$serial"
            break
        fi
    fi
done

if [ "$ESP32_FOUND" = false ]; then
    echo ""
    echo "⚠ ESP32 not found!"
    echo ""
    echo "Please ensure:"
    echo "1. ESP32 is connected via USB"
    echo "2. ESP32 is powered on"
    echo "3. USB cable supports data (not charge-only)"
    echo ""
    echo "Current USB serial devices:"
    ls -la /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || echo "   No USB serial devices found"
    exit 1
fi

echo ""
echo "2. Creating udev rule..."

# Create udev rule file
UDEV_RULE_FILE="/etc/udev/rules.d/99-esp32.rules"

if [ -n "$ESP32_SERIAL" ]; then
    # Use serial number if available (most specific)
    echo "SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"$ESP32_ID_VENDOR\", ATTRS{idProduct}==\"$ESP32_ID_PRODUCT\", ATTRS{serial}==\"$ESP32_SERIAL\", SYMLINK+=\"esp32\", MODE=\"0666\"" > "$UDEV_RULE_FILE"
    echo "   Rule created with serial number matching"
else
    # Use vendor/product only
    echo "SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"$ESP32_ID_VENDOR\", ATTRS{idProduct}==\"$ESP32_ID_PRODUCT\", SYMLINK+=\"esp32\", MODE=\"0666\"" > "$UDEV_RULE_FILE"
    echo "   Rule created with vendor/product matching"
fi

echo "   Rule file: $UDEV_RULE_FILE"
cat "$UDEV_RULE_FILE"

echo ""
echo "3. Setting permissions..."
chmod 644 "$UDEV_RULE_FILE"

echo ""
echo "4. Reloading udev rules..."
udevadm control --reload-rules
udevadm trigger

echo ""
echo "5. Testing..."
sleep 2

if [ -L /dev/esp32 ]; then
    echo "   ✓ Symlink created: /dev/esp32 -> $(readlink -f /dev/esp32)"
    ls -la /dev/esp32
    
    # Test permissions
    if [ -r /dev/esp32 ] && [ -w /dev/esp32 ]; then
        echo "   ✓ Device is readable and writable"
    else
        echo "   ⚠ Permission issue detected"
        chmod 666 /dev/esp32
        echo "   ✓ Fixed permissions"
    fi
else
    echo "   ⚠ Symlink not created yet (may need reconnect)"
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "1. Disconnect and reconnect ESP32 USB cable"
echo "2. Verify: ls -la /dev/esp32"
echo "3. Update config.py: SERIAL_PORT = \"/dev/esp32\""
echo ""
echo "Note: If you have multiple ESP32 devices with the same vendor/product ID,"
echo "they may conflict. In that case, use the ESP32's unique serial number."
echo ""
echo "To find your ESP32's serial number:"
echo "  udevadm info -a -n /dev/ttyACM0 | grep serial"
