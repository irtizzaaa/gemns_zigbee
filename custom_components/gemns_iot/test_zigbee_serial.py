#!/usr/bin/env python3
"""
Test script for Zigbee serial commands.

Examples:
    python test_zigbee_serial.py COM3 pair
    python test_zigbee_serial.py COM3 add bulb 1
    python test_zigbee_serial.py COM3 del bulb
    python test_zigbee_serial.py COM3 state bulb 55424 on
    python test_zigbee_serial.py COM3 state switch 0 on
    python test_zigbee_serial.py COM3 state bulb 55424 brightness 128
"""

import argparse
import serial
import serial.tools.list_ports
import sys
import time

# Command constants
CMD_PREFIX = "$AT"
CMD_ADD = "add"
CMD_DEL = "del"
CMD_STATE = "state"
CMD_PAIR = "pair"
DEVICE_BULB = "bulb"
DEVICE_SWITCH = "switch"

# Serial settings
BAUDRATE = 115200
TIMEOUT = 5.0
LINE_ENDING = "\r\n"


def list_serial_ports():
    """List available serial ports."""
    ports = serial.tools.list_ports.comports()
    print("Available serial ports:")
    for port in ports:
        print(f"  {port.device}: {port.description}")
    return [port.device for port in ports]


def build_command(command, device_type=None, device_id=None, state=None, brightness=None):
    """Build a Zigbee command string."""
    if command == CMD_PAIR:
        return f"{CMD_PREFIX}+{command}{LINE_ENDING}"
    
    if command == CMD_ADD:
        if device_type not in [DEVICE_BULB, DEVICE_SWITCH]:
            raise ValueError(f"Invalid device type: {device_type}")
        if device_id is None:
            raise ValueError("device_id required for add command")
        length = 2
        type_code = 2 if device_type == DEVICE_BULB else 3
        return f"{CMD_PREFIX}+{command} {device_type} {length} {type_code} {device_id}{LINE_ENDING}"
    
    if command == CMD_DEL:
        if device_type not in [DEVICE_BULB, DEVICE_SWITCH]:
            raise ValueError(f"Invalid device type: {device_type}")
        length = 1
        type_code = 2 if device_type == DEVICE_BULB else 3
        return f"{CMD_PREFIX}+{command} {device_type} {length} {type_code}{LINE_ENDING}"
    
    if command == CMD_STATE:
        if device_type not in [DEVICE_BULB, DEVICE_SWITCH]:
            raise ValueError(f"Invalid device type: {device_type}")
        if device_id is None:
            raise ValueError("device_id required for state command")
        
        device_type_code = 0 if device_type == DEVICE_BULB else 1
        device_type_name = "sw" if device_type == DEVICE_SWITCH else device_type
        src_id = int(device_id) & 0xFFFFFFFF
        
        if brightness is not None:
            brightness = max(0, min(255, int(brightness)))
            length = 4
            cmd_type = 3
            return f"{CMD_PREFIX}+{command} {device_type_name} {length} {src_id} {device_type_code} {cmd_type} {brightness}{LINE_ENDING}"
        else:
            length = 3
            cmd_type = 1 if state else 0
            return f"{CMD_PREFIX}+{command} {device_type_name} {length} {src_id} {device_type_code} {cmd_type}{LINE_ENDING}"
    
    raise ValueError(f"Unknown command: {command}")


def send_command(port, command_str):
    """Send command to serial port and read response."""
    try:
        ser = serial.Serial(port, BAUDRATE, timeout=TIMEOUT)
        print(f"Connected to {port}")
        print(f"Sending: {repr(command_str)}")
        
        ser.write(command_str.encode('utf-8'))
        time.sleep(0.5)
        
        if ser.in_waiting > 0:
            response = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
            print(f"Response: {repr(response)}")
        else:
            print("No response received")
        
        ser.close()
        print("Command sent successfully")
        
    except serial.SerialException as e:
        print(f"Serial error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Test Zigbee serial commands",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        "port",
        nargs="?",
        help="Serial port (e.g., COM3, /dev/ttyUSB0). Use 'list' to list available ports."
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=[CMD_PAIR, CMD_ADD, CMD_DEL, CMD_STATE],
        help="Command to send"
    )
    parser.add_argument(
        "args",
        nargs="*",
        help="Command arguments"
    )
    
    args = parser.parse_args()
    
    if args.port == "list" or args.port is None:
        ports = list_serial_ports()
        if args.port == "list":
            sys.exit(0)
        if not ports:
            print("No serial ports found", file=sys.stderr)
            sys.exit(1)
        print(f"\nUsage: python {sys.argv[0]} <port> <command> [args...]")
        print(f"Example: python {sys.argv[0]} {ports[0]} pair")
        sys.exit(0)
    
    if args.command is None:
        print("Error: command is required", file=sys.stderr)
        parser.print_help()
        sys.exit(1)
    
    try:
        if args.command == CMD_PAIR:
            cmd_str = build_command(CMD_PAIR)
        
        elif args.command == CMD_ADD:
            if len(args.args) < 2:
                print("Error: add command requires device_type and device_id", file=sys.stderr)
                sys.exit(1)
            device_type = args.args[0]
            device_id = int(args.args[1])
            cmd_str = build_command(CMD_ADD, device_type=device_type, device_id=device_id)
        
        elif args.command == CMD_DEL:
            if len(args.args) < 1:
                print("Error: del command requires device_type", file=sys.stderr)
                sys.exit(1)
            device_type = args.args[0]
            cmd_str = build_command(CMD_DEL, device_type=device_type)
        
        elif args.command == CMD_STATE:
            if len(args.args) < 3:
                print("Error: state command requires device_type, device_id, and state", file=sys.stderr)
                sys.exit(1)
            device_type = args.args[0]
            device_id = int(args.args[1])
            state_arg = args.args[2].lower()
            
            if state_arg == "brightness" and len(args.args) >= 4:
                brightness = int(args.args[3])
                cmd_str = build_command(CMD_STATE, device_type=device_type, device_id=device_id, brightness=brightness)
            else:
                state = state_arg in ["on", "1", "true"]
                cmd_str = build_command(CMD_STATE, device_type=device_type, device_id=device_id, state=state)
        
        print(f"Command: {cmd_str.strip()}")
        send_command(args.port, cmd_str)
        
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

