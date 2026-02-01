#!/usr/bin/env python3
"""Serial port detection utility for Raspberry Pi."""
import glob
import sys


def list_serial_ports():
    """List available serial ports on Raspberry Pi."""
    print("Scanning for serial ports...\n")
    
    # Common serial port patterns on Raspberry Pi
    patterns = [
        "/dev/ttyUSB*",    # USB-to-Serial adapters
        "/dev/ttyACM*",    # USB CDC devices (Arduino, etc.)
        "/dev/ttyAMA*",    # Raspberry Pi UART (AMA0, AMA1, etc.)
        "/dev/ttyS*",      # Serial ports
        "/dev/serial*",    # Serial aliases
    ]
    
    found_ports = []
    
    for pattern in patterns:
        ports = glob.glob(pattern)
        for port in ports:
            found_ports.append(port)
    
    # Remove duplicates and sort
    found_ports = sorted(set(found_ports))
    
    if found_ports:
        print("Available serial ports:")
        print("-" * 50)
        for port in found_ports:
            print(f"  {port}")
        print("-" * 50)
        print(f"\nTotal: {len(found_ports)} port(s) found")
        
        # Suggest the most likely port
        if "/dev/ttyAMA0" in found_ports:
            print("\n✓ Recommended for Pi5 GPIO14/15 UART: /dev/ttyAMA0")
        elif "/dev/ttyUSB0" in found_ports:
            print("\n✓ USB Serial found: /dev/ttyUSB0")
        elif "/dev/ttyACM0" in found_ports:
            print("\n✓ USB CDC found: /dev/ttyACM0")
            
    else:
        print("No serial ports found!")
        print("\nTroubleshooting:")
        print("1. Check if serial port is enabled: sudo raspi-config")
        print("2. For GPIO UART: enable serial in Interface Options")
        print("3. Check connections and power")
        print("4. Try: ls -la /dev/tty*")
    
    return found_ports


def check_port_permissions(port):
    """Check if user has permission to access serial port."""
    import os
    import grp
    
    print(f"\nChecking permissions for {port}...")
    
    if not os.path.exists(port):
        print(f"  ✗ Port {port} does not exist")
        return False
    
    # Check if user is in dialout group
    try:
        dialout_group = grp.getgrnam('dialout')
        user_groups = [g.gr_name for g in grp.getgrall() if os.getlogin() in g.gr_mem]
        
        if 'dialout' in user_groups:
            print(f"  ✓ User is in 'dialout' group")
        else:
            print(f"  ⚠ User NOT in 'dialout' group")
            print(f"    Fix: sudo usermod -a -G dialout $USER")
            print(f"    Then logout and login again")
    except Exception as e:
        print(f"  ⚠ Could not check group membership: {e}")
    
    # Check permissions
    import stat
    port_stat = os.stat(port)
    mode = port_stat.st_mode
    
    if mode & stat.S_IRUSR:
        print(f"  ✓ Port is readable")
    else:
        print(f"  ✗ Port is NOT readable")
    
    if mode & stat.S_IWUSR:
        print(f"  ✓ Port is writable")
    else:
        print(f"  ✗ Port is NOT writable")
    
    return True


if __name__ == "__main__":
    ports = list_serial_ports()
    
    if len(sys.argv) > 1:
        # Check specific port
        check_port_permissions(sys.argv[1])
    elif ports:
        # Check first available port
        check_port_permissions(ports[0])
