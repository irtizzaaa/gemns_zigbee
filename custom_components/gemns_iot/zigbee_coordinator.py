"""Zigbee coordinator for Gemns™ IoT integration using serial communication."""

import asyncio
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    _LOGGER = logging.getLogger(__name__)
    _LOGGER.warning("pyserial not available, Zigbee functionality will be limited")

from .const import (
    DEVICE_CATEGORY_LIGHT,
    DEVICE_CATEGORY_SWITCH,
    DEVICE_STATUS_CONNECTED,
    DEVICE_STATUS_OFFLINE,
    DEVICE_TYPE_ZIGBEE,
    DOMAIN,
    SIGNAL_DEVICE_ADDED,
    SIGNAL_DEVICE_UPDATED,
)

_LOGGER = logging.getLogger(__name__)

ZIGBEE_CMD_PREFIX = "$AT"
ZIGBEE_CMD_ADD = "add"
ZIGBEE_CMD_DEL = "del"
ZIGBEE_CMD_STATE = "state"
ZIGBEE_CMD_PAIR = "pair"
ZIGBEE_DEVICE_BULB = "bulb"
ZIGBEE_DEVICE_SWITCH = "switch"

SERIAL_BAUDRATE = 115200
SERIAL_TIMEOUT = 5.0
SERIAL_LINE_ENDING = "\r\n"


class ZigbeeCommandParser:
    """Parser for Zigbee serial commands."""

    @staticmethod
    def parse_command(line: str) -> dict[str, Any] | None:
        """Parse a Zigbee command line."""
        _LOGGER.debug("Parsing command line: %s", repr(line))
        line = line.strip()
        
        if not line.startswith(ZIGBEE_CMD_PREFIX):
            _LOGGER.debug("Line does not start with %s, ignoring", ZIGBEE_CMD_PREFIX)
            return None
        
        line_suffix = line[len(ZIGBEE_CMD_PREFIX):].strip()
        _LOGGER.debug("Command suffix after prefix: %s", repr(line_suffix))
        
        pattern_new = r'\+(\w+)\s+(\w+)\s+(\d+)\s+(\d+)\s+(\d+)(?:\s+(\d+))?'
        match = re.match(pattern_new, line_suffix)
        
        if match and match.group(1) == ZIGBEE_CMD_STATE:
            command = match.group(1)
            device_type_str = match.group(2)
            length = int(match.group(3))
            src_id = int(match.group(4)) & 0xFFFFFFFF
            state_value = int(match.group(5))
            brightness = match.group(6) if match.group(6) else None
            
            if device_type_str == "sw":
                device_type_str = ZIGBEE_DEVICE_SWITCH
                _LOGGER.debug("Converted 'sw' to 'switch'")
            
            supports_brightness = (length == 4)
            cmd_type = state_value
            
            _LOGGER.debug(
                "Parsed new STATE format: command=%s, device_type_str=%s, length=%d, "
                "src_id=%d, state=%d, brightness=%s, supports_brightness=%s",
                command,
                device_type_str,
                length,
                src_id,
                state_value,
                brightness,
                supports_brightness,
            )
            
            result = {
                "command": command,
                "device_type": device_type_str,
                "length": length,
                "device_id": src_id,
                "cmd_type": cmd_type,
                "supports_brightness": supports_brightness,
            }
            
            if supports_brightness and brightness:
                result["brightness"] = max(0, min(255, int(brightness)))
                _LOGGER.debug("Added brightness to result: %d", result["brightness"])
            
            _LOGGER.debug("Parse result (new format): %s", result)
            return result
        
        pattern_old = r'\+(\w+)\s+(\w+)\s+(\d+)\s+(\d+)\s*(\d*)\s*(\d*)'
        match = re.match(pattern_old, line_suffix)
        
        if not match:
            _LOGGER.warning("Failed to parse Zigbee command: %s", line)
            _LOGGER.debug("Tried patterns: new format (STATE only) and old format, neither matched")
            return None
        
        _LOGGER.debug("Matched old format pattern")
        command = match.group(1)
        device_type = match.group(2)
        length = int(match.group(3))
        type_code = int(match.group(4))
        device_id = match.group(5) if match.group(5) else None
        brightness = match.group(6) if match.group(6) else None
        
        _LOGGER.debug("Parsed old format: command=%s, device_type=%s, length=%d, type_code=%d, device_id=%s, brightness=%s",
                     command, device_type, length, type_code, device_id, brightness)
        
        result = {
            "command": command,
            "device_type": device_type,
            "length": length,
            "type": type_code,
        }
        
        if device_id:
            result["device_id"] = int(device_id)
        
        if brightness:
            result["brightness"] = max(0, min(255, int(brightness)))
        
        _LOGGER.debug("Parse result (old format): %s", result)
        return result

    @staticmethod
    def build_command(command: str, device_type: str, device_id: int | None = None, 
                     state: bool | None = None, brightness: int | None = None) -> str:
        """Build a Zigbee command string."""
        if command == ZIGBEE_CMD_PAIR:
            return f"{ZIGBEE_CMD_PREFIX}+{command}{SERIAL_LINE_ENDING}"
        
        if command == ZIGBEE_CMD_ADD:
            length = 2
            type_code = 2 if device_type == ZIGBEE_DEVICE_BULB else 3
            return f"{ZIGBEE_CMD_PREFIX}+{command} {device_type} {length} {type_code} {device_id}{SERIAL_LINE_ENDING}"
        
        elif command == ZIGBEE_CMD_DEL:
            length = 1
            type_code = 2 if device_type == ZIGBEE_DEVICE_BULB else 3
            return f"{ZIGBEE_CMD_PREFIX}+{command} {device_type} {length} {type_code}{SERIAL_LINE_ENDING}"
        
        elif command == ZIGBEE_CMD_STATE:
            state_value = 1 if state else 0
            
            if brightness is not None:
                brightness = max(0, min(255, int(brightness)))
                length = 4
                return (
                    f"{ZIGBEE_CMD_PREFIX}+{command} {device_type} {length} "
                    f"{device_id} {state_value} {brightness}{SERIAL_LINE_ENDING}"
                )
            else:
                length = 3
                return (
                    f"{ZIGBEE_CMD_PREFIX}+{command} {device_type} {length} "
                    f"{device_id} {state_value}{SERIAL_LINE_ENDING}"
                )
        
        return ""


class ZigbeeCoordinator:
    """Coordinator for Zigbee serial communication."""

    def __init__(self, hass: HomeAssistant, device_manager, serial_port: str | None = None):
        """Initialize the Zigbee coordinator."""
        self.hass = hass
        self.device_manager = device_manager
        self.serial_port = serial_port
        self.serial_connection = None
        self.parser = ZigbeeCommandParser()
        self._running = False
        self._read_task = None
        self._devices: dict[int, dict[str, Any]] = {}

    async def async_start(self):
        """Start the Zigbee coordinator."""
        _LOGGER.info("Starting Zigbee coordinator...")
        
        if not SERIAL_AVAILABLE:
            _LOGGER.error("pyserial not available, cannot start Zigbee coordinator")
            return False
        
        _LOGGER.info("pyserial is available")
        
        if not self.serial_port:
            _LOGGER.info("No serial port specified, attempting auto-detection...")
            self.serial_port = await self._find_serial_port()
        else:
            _LOGGER.info("Using configured serial port: %s", self.serial_port)
        
        if not self.serial_port:
            _LOGGER.warning("No Zigbee serial port found - please check your USB connection and try specifying the port manually")
            _LOGGER.info("You can manually specify the port in the integration configuration")
            return False
        
        if not os.path.exists(self.serial_port):
            _LOGGER.error("Serial port %s does not exist - please check if the device is connected", self.serial_port)
            return False
        
        _LOGGER.info("Attempting to connect to serial port: %s (baudrate: %d)", 
                      self.serial_port, SERIAL_BAUDRATE)
        
        try:
            self.serial_connection = serial.Serial(
                port=self.serial_port,
                baudrate=SERIAL_BAUDRATE,
                timeout=SERIAL_TIMEOUT,
                write_timeout=SERIAL_TIMEOUT
            )
            _LOGGER.info("Connected to Zigbee dongle on %s", self.serial_port)
            _LOGGER.info("Serial connection details: port=%s, baudrate=%d, timeout=%s, is_open=%s",
                         self.serial_port, SERIAL_BAUDRATE, SERIAL_TIMEOUT, self.serial_connection.is_open)
        except Exception as e:
            _LOGGER.error("Failed to open serial port %s: %s", self.serial_port, e)
            _LOGGER.error("Exception details: %s", type(e).__name__, exc_info=True)
            return False
        
        self._running = True
        self._read_task = asyncio.create_task(self._read_serial_loop())
        _LOGGER.info("Zigbee coordinator started successfully, read loop task created")
        
        return True

    async def async_stop(self):
        """Stop the Zigbee coordinator."""
        self._running = False
        
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        
        if self.serial_connection and self.serial_connection.is_open:
            self.serial_connection.close()
            _LOGGER.info("Closed Zigbee serial connection")

    async def _find_serial_port(self) -> str | None:
        """Find the Zigbee serial port."""
        _LOGGER.info("Scanning for available serial ports...")
        try:
            loop = asyncio.get_event_loop()
            ports = await loop.run_in_executor(None, serial.tools.list_ports.comports)
            _LOGGER.info("Found %d serial port(s) total", len(ports))
            
            _LOGGER.info("All available serial ports:")
            if len(ports) == 0:
                _LOGGER.info("  (none)")
                _LOGGER.info("")
                _LOGGER.info("No serial ports found - this is normal if no USB devices are connected")
                return None
            
            for i, port in enumerate(ports, 1):
                vid = getattr(port, 'vid', None)
                pid = getattr(port, 'pid', None)
                vid_str = f"0x{vid:04X}" if vid else "N/A"
                pid_str = f"0x{pid:04X}" if pid else "N/A"
                _LOGGER.info("  [%d] %s", i, port.device)
                _LOGGER.info("      Description: %s", port.description or "N/A")
                _LOGGER.info("      Hardware ID: %s", port.hwid or "N/A")
                _LOGGER.info("      VID: %s, PID: %s", vid_str, pid_str)
                _LOGGER.info("")
            
            usb_ports = []
            for p in ports:
                if 'ttyUSB' in p.device or 'ttyACM' in p.device:
                    usb_ports.append(p)
            
            if usb_ports:
                if len(usb_ports) == 1:
                    selected_port = usb_ports[0]
                    _LOGGER.info("Auto-selected USB serial port: %s", selected_port.device)
                    return selected_port.device
                else:
                    ttyusb_ports = [p for p in usb_ports if 'ttyUSB' in p.device]
                    if ttyusb_ports:
                        selected_port = ttyusb_ports[0]
                        _LOGGER.info("Auto-selected USB serial port (preferred ttyUSB): %s", selected_port.device)
                        return selected_port.device
                    else:
                        selected_port = usb_ports[0]
                        _LOGGER.info("Auto-selected USB serial port: %s", selected_port.device)
                        _LOGGER.info("Multiple USB ports available. If this is incorrect, specify the port manually.")
                        return selected_port.device
            elif len(ports) == 1:
                _LOGGER.info("Auto-selected only available port: %s", ports[0].device)
                return ports[0].device
            else:
                _LOGGER.warning("Multiple ports found but none are USB serial adapters. Please specify the port manually.")
                _LOGGER.info("Available ports: %s", ", ".join([p.device for p in ports]))
                return None
                
        except Exception as e:
            _LOGGER.error("Error finding serial port: %s", e)
            _LOGGER.error("Exception details: %s", type(e).__name__, exc_info=True)
        
        return None

    async def _read_serial_loop(self):
        """Read loop for serial messages."""
        _LOGGER.debug("Serial read loop started")
        buffer = ""
        
        while self._running:
            try:
                if self.serial_connection and self.serial_connection.is_open:
                    bytes_waiting = self.serial_connection.in_waiting
                    if bytes_waiting > 0:
                        data = self.serial_connection.read(bytes_waiting).decode('utf-8', errors='ignore')
                        _LOGGER.debug("Read %d bytes from serial: %s", len(data), repr(data))
                        buffer += data
                        
                        while SERIAL_LINE_ENDING in buffer:
                            line, buffer = buffer.split(SERIAL_LINE_ENDING, 1)
                            if line.strip():
                                _LOGGER.debug("Processing complete line from buffer: %s", repr(line))
                                await self._handle_serial_message(line)
                else:
                    _LOGGER.warning("Serial connection not open or not available")
                    if not self.serial_connection:
                        _LOGGER.debug("serial_connection is None")
                    elif not self.serial_connection.is_open:
                        _LOGGER.debug("serial_connection.is_open is False")
                
                await asyncio.sleep(0.1)
                
            except Exception as e:
                _LOGGER.error("Error reading from serial: %s", e)
                _LOGGER.debug("Exception in read loop: %s", type(e).__name__, exc_info=True)
                await asyncio.sleep(1)

    async def _handle_serial_message(self, line: str):
        """Handle a message from the serial port."""
        _LOGGER.debug("Received serial message: %s (length: %d)", line, len(line))
        
        parsed = self.parser.parse_command(line)
        if not parsed:
            _LOGGER.debug("Failed to parse message as Zigbee command: %s", line)
            return
        
        _LOGGER.debug("Parsed command: %s", parsed)
        command = parsed.get("command")
        device_type = parsed.get("device_type")
        device_id = parsed.get("device_id")
        
        # Normalize device_type to lowercase to prevent duplicates
        if device_type:
            device_type = device_type.lower()
            parsed["device_type"] = device_type
            # Convert "sw" to "switch" if needed
            if device_type == "sw":
                device_type = ZIGBEE_DEVICE_SWITCH
                parsed["device_type"] = device_type
        
        _LOGGER.debug("Command type: %s, Device type: %s, Device ID: %s", command, device_type, device_id)
        
        if command == ZIGBEE_CMD_ADD:
            _LOGGER.debug("Handling ADD device command")
            await self._handle_add_device(parsed)
        elif command == ZIGBEE_CMD_DEL:
            _LOGGER.debug("Handling DEL device command")
            await self._handle_delete_device(parsed)
        elif command == ZIGBEE_CMD_STATE:
            _LOGGER.debug("Handling STATE update command")
            await self._handle_state_update(parsed)
        else:
            _LOGGER.debug("Unknown command type: %s", command)

    async def _handle_add_device(self, parsed: dict[str, Any]):
        """Handle device addition."""
        device_id = parsed.get("device_id")
        device_type = parsed.get("device_type")
        
        if device_id is None:
            _LOGGER.warning("Add device command missing device_id")
            return
        
        device_manager_id = f"zigbee_{device_type}_{device_id}"
        
        # Check if device already exists in either _devices or device_manager
        if device_id in self._devices:
            _LOGGER.debug("Device already exists in _devices, updating: %s (ID: %d)", device_manager_id, device_id)
            device_data = self._devices[device_id]
            device_data.update({
                "zigbee_id": device_id,
                "device_type": DEVICE_TYPE_ZIGBEE,
                "status": DEVICE_STATUS_CONNECTED,
            })
            # Update device_manager if it exists there too
            if device_manager_id in self.device_manager.devices:
                self.device_manager.devices[device_manager_id].update(device_data)
            return
        
        if device_manager_id in self.device_manager.devices:
            _LOGGER.debug("Device already exists in device_manager, updating: %s (ID: %d)", device_manager_id, device_id)
            device_data = self.device_manager.devices[device_manager_id]
            device_data.update({
                "zigbee_id": device_id,
                "device_type": DEVICE_TYPE_ZIGBEE,
                "status": DEVICE_STATUS_CONNECTED,
            })
            self._devices[device_id] = device_data
            return
        
        category = DEVICE_CATEGORY_LIGHT if device_type == ZIGBEE_DEVICE_BULB else DEVICE_CATEGORY_SWITCH
        device_data = {
            "device_id": device_manager_id,
            "zigbee_id": device_id,
            "device_type": DEVICE_TYPE_ZIGBEE,
            "category": category,
            "name": f"Zigbee {device_type.title()} {device_id}",
            "status": DEVICE_STATUS_CONNECTED,
            "properties": {
                "switch_state": False,
                "light_state": False,
            }
        }
        
        self._devices[device_id] = device_data
        await self.device_manager.add_device(device_data)
        
        _LOGGER.info("Zigbee device added: %s (ID: %d)", device_data["name"], device_id)

    async def _handle_delete_device(self, parsed: dict[str, Any]):
        """Handle device deletion."""
        device_type = parsed.get("device_type")
        type_code = parsed.get("type")
        _LOGGER.info("Delete device command received for type: %s (code: %d)", device_type, type_code)

    async def _handle_state_update(self, parsed: dict[str, Any]):
        """Handle state update from device."""
        device_id = parsed.get("device_id")
        device_type = parsed.get("device_type")
        brightness = parsed.get("brightness")
        cmd_type = parsed.get("cmd_type")
        supports_brightness = parsed.get("supports_brightness", False)
        length = parsed.get("length")
        
        if device_id is None:
            _LOGGER.warning("State update missing device_id")
            return
        
        device_manager_id = f"zigbee_{device_type}_{device_id}"
        _LOGGER.info("Processing state update: device_id=%d, device_type=%s, device_manager_id=%s", device_id, device_type, device_manager_id)
        _LOGGER.info("Checking _devices: device_id %d in _devices: %s", device_id, device_id in self._devices)
        _LOGGER.info("Checking device_manager.devices: %s in devices: %s (total: %d)", device_manager_id, device_manager_id in self.device_manager.devices, len(self.device_manager.devices))
        
        device_data = self._devices.get(device_id)
        if not device_data:
            if device_manager_id in self.device_manager.devices:
                device_data = self.device_manager.devices[device_manager_id]
                self._devices[device_id] = device_data
                _LOGGER.info("Found existing device in device_manager: %s (zigbee_id: %d)", device_manager_id, device_id)
                # Check if entity creation signal was already sent
                if device_manager_id not in self.device_manager._created_entities:
                    _LOGGER.info("Existing device %s doesn't have entity yet, sending SIGNAL_DEVICE_ADDED", device_manager_id)
                    self.device_manager._created_entities.add(device_manager_id)
                    self.hass.async_create_task(
                        self.device_manager._async_notify_device_added(device_data)
                    )
            else:
                _LOGGER.info(
                    "State update for unknown device ID: %d (%s). Creating device from state.",
                    device_id,
                    device_type,
                )
                category = (
                    DEVICE_CATEGORY_LIGHT
                    if device_type == ZIGBEE_DEVICE_BULB
                    else DEVICE_CATEGORY_SWITCH
                )
                device_data = {
                    "device_id": device_manager_id,
                    "zigbee_id": device_id,
                    "device_type": DEVICE_TYPE_ZIGBEE,
                    "category": category,
                    "name": f"Zigbee {device_type.title()} {device_id}",
                    "status": DEVICE_STATUS_CONNECTED,
                    "properties": {
                        "switch_state": False,
                        "light_state": False,
                        "supports_brightness": supports_brightness,
                    },
                }
                self._devices[device_id] = device_data
                _LOGGER.info("Adding device to device_manager: %s (zigbee_id: %d, category: %s)", device_manager_id, device_id, category)
                result = await self.device_manager.add_device(device_data)
                _LOGGER.info("Device add result: %s for device_id: %s", result, device_manager_id)
                if result:
                    _LOGGER.info("Device successfully added, checking if in device_manager: %s", device_manager_id in self.device_manager.devices)
                    if device_manager_id not in self.device_manager.devices:
                        _LOGGER.error("Device %s was not added to device_manager.devices!", device_manager_id)
        else:
            # Update supports_brightness property if not already set
            if "supports_brightness" not in device_data.get("properties", {}):
                device_data.setdefault("properties", {})["supports_brightness"] = supports_brightness
        
        if device_type == ZIGBEE_DEVICE_BULB:
            if supports_brightness and brightness is not None:
                brightness = max(0, min(255, int(brightness)))
                device_data["properties"]["brightness"] = brightness
                if cmd_type is not None:
                    device_data["properties"]["light_state"] = (cmd_type == 1 or cmd_type == 3)
                else:
                    device_data["properties"]["light_state"] = True
            else:
                if cmd_type is not None:
                    device_data["properties"]["light_state"] = (cmd_type == 1)
                else:
                    device_data["properties"]["light_state"] = True
        
        elif device_type == ZIGBEE_DEVICE_SWITCH:
            if cmd_type is not None:
                device_data["properties"]["switch_state"] = (cmd_type == 1 or cmd_type == 3)
                device_data["properties"]["cmd_type"] = cmd_type
                _LOGGER.info("Zigbee switch %d state: %s (cmd_type=%d)", device_id, "ON" if cmd_type == 3 else ("on" if (cmd_type == 1 or cmd_type == 3) else "off"), cmd_type)
            else:
                device_data["properties"]["switch_state"] = True
                _LOGGER.info("Zigbee switch %d pressed (no cmd_type)", device_id)
            
            if supports_brightness and brightness is not None:
                brightness = max(0, min(255, int(brightness)))
                device_data["properties"]["brightness"] = brightness
                _LOGGER.info("Zigbee switch %d brightness: %d", device_id, brightness)
        
        device_manager_id = device_data["device_id"]
        if device_manager_id in self.device_manager.devices:
            self.device_manager.devices[device_manager_id].update(device_data)
            self.device_manager.devices[device_manager_id]["last_seen"] = datetime.now(UTC).isoformat()
            await self.device_manager._async_notify_device_update(
                self.device_manager.devices[device_manager_id]
            )

    async def send_pairing_command(self):
        """Send pairing command to enter pairing mode."""
        _LOGGER.debug("Building pairing command...")
        command = self.parser.build_command(ZIGBEE_CMD_PAIR, "")
        _LOGGER.debug("Built pairing command: %s", repr(command))
        await self._write_serial(command)
        _LOGGER.info("Sent pairing command")

    async def send_control_command(self, device_id: int, device_type: str, state: bool, brightness: int | None = None):
        """Send control command to device."""
        _LOGGER.debug("Building control command: device_id=%d, device_type=%s, state=%s, brightness=%s",
                     device_id, device_type, state, brightness)
        command = self.parser.build_command(
            ZIGBEE_CMD_STATE,
            device_type,
            device_id=device_id,
            state=state,
            brightness=brightness
        )
        _LOGGER.debug("Built command string: %s", repr(command))
        await self._write_serial(command)
        _LOGGER.info("Sent control command: device_id=%d, type=%s, state=%s, brightness=%s", 
                    device_id, device_type, state, brightness)

    async def _write_serial(self, data: str):
        """Write data to serial port."""
        _LOGGER.debug("Attempting to write to serial: %s (length: %d bytes)", repr(data), len(data.encode('utf-8')))
        try:
            if self.serial_connection and self.serial_connection.is_open:
                encoded_data = data.encode('utf-8')
                bytes_written = self.serial_connection.write(encoded_data)
                _LOGGER.debug("Wrote %d bytes to serial: %s", bytes_written, data.strip())
                _LOGGER.debug("Serial connection status: is_open=%s, in_waiting=%d", 
                            self.serial_connection.is_open, self.serial_connection.in_waiting)
            else:
                _LOGGER.error("Serial connection not open - cannot write")
                _LOGGER.debug("Connection state: connection=%s, is_open=%s", 
                            self.serial_connection is not None,
                            self.serial_connection.is_open if self.serial_connection else False)
        except Exception as e:
            _LOGGER.error("Error writing to serial: %s", e)
            _LOGGER.debug("Exception details: %s", type(e).__name__, exc_info=True)

    def get_device_by_zigbee_id(self, zigbee_id: int) -> dict[str, Any] | None:
        """Get device data by Zigbee ID."""
        return self._devices.get(zigbee_id)

