"""Zigbee coordinator for Gemns™ IoT integration using serial communication."""

import asyncio
import logging
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

# Zigbee command constants
ZIGBEE_CMD_PREFIX = "$AT"
ZIGBEE_CMD_ADD = "add"
ZIGBEE_CMD_DEL = "del"
ZIGBEE_CMD_STATE = "state"
ZIGBEE_CMD_PAIR = "pair"
ZIGBEE_DEVICE_BULB = "bulb"
ZIGBEE_DEVICE_SWITCH = "switch"

# Serial settings
SERIAL_BAUDRATE = 115200
SERIAL_TIMEOUT = 5.0
SERIAL_LINE_ENDING = "\r\n"


class ZigbeeCommandParser:
    """Parser for Zigbee serial commands."""

    @staticmethod
    def parse_command(line: str) -> dict[str, Any] | None:
        """Parse a Zigbee command line."""
        line = line.strip()
        
        if not line.startswith(ZIGBEE_CMD_PREFIX):
            return None
        
        line = line[len(ZIGBEE_CMD_PREFIX):].strip()
        pattern = r'\+(\w+)\s+(\w+)\s+(\d+)\s+(\d+)\s*(\d*)\s*(\d*)'
        match = re.match(pattern, line)
        
        if not match:
            _LOGGER.warning("Failed to parse Zigbee command: %s", line)
            return None
        
        command = match.group(1)
        device_type = match.group(2)
        length = int(match.group(3))
        type_code = int(match.group(4))
        device_id = match.group(5) if match.group(5) else None
        brightness = match.group(6) if match.group(6) else None
        
        result = {
            "command": command,
            "device_type": device_type,
            "length": length,
            "type": type_code,
        }
        
        if device_id:
            result["device_id"] = int(device_id)
        
        if brightness:
            result["brightness"] = int(brightness)
        
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
            if device_type == ZIGBEE_DEVICE_BULB:
                if brightness is not None:
                    brightness = max(0, min(255, int(brightness)))
                    length = 3
                    type_code = 2
                    return f"{ZIGBEE_CMD_PREFIX}+{command} {device_type} {length} {type_code} {device_id} {brightness}{SERIAL_LINE_ENDING}"
                else:
                    length = 2
                    type_code = 2
                    state_val = 1 if state else 0
                    return f"{ZIGBEE_CMD_PREFIX}+{command} {device_type} {length} {type_code} {device_id} {state_val}{SERIAL_LINE_ENDING}"
            else:
                length = 3
                type_code = 3
                state_val = 1 if state else 0
                return f"{ZIGBEE_CMD_PREFIX}+{command} {device_type} {length} {type_code} {device_id} {state_val}{SERIAL_LINE_ENDING}"
        
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
        self._devices: dict[int, dict[str, Any]] = {}  # device_id -> device_data

    async def async_start(self):
        """Start the Zigbee coordinator."""
        if not SERIAL_AVAILABLE:
            _LOGGER.error("pyserial not available, cannot start Zigbee coordinator")
            return False
        
        if not self.serial_port:
            self.serial_port = await self._find_serial_port()
        
        if not self.serial_port:
            _LOGGER.error("No Zigbee serial port found")
            return False
        
        try:
            self.serial_connection = serial.Serial(
                port=self.serial_port,
                baudrate=SERIAL_BAUDRATE,
                timeout=SERIAL_TIMEOUT,
                write_timeout=SERIAL_TIMEOUT
            )
            _LOGGER.info("Connected to Zigbee dongle on %s", self.serial_port)
        except Exception as e:
            _LOGGER.error("Failed to open serial port %s: %s", self.serial_port, e)
            return False
        
        self._running = True
        self._read_task = asyncio.create_task(self._read_serial_loop())
        
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
        try:
            ports = serial.tools.list_ports.comports()
            for port in ports:
                if any(keyword in port.description.lower() for keyword in 
                       ['zigbee', 'cc2531', 'cc2538', 'znp', 'zstack', 'usb', 'serial']):
                    _LOGGER.info("Found potential Zigbee port: %s (%s)", port.device, port.description)
                    return port.device
        except Exception as e:
            _LOGGER.warning("Error finding serial port: %s", e)
        
        return None

    async def _read_serial_loop(self):
        """Read loop for serial messages."""
        buffer = ""
        
        while self._running:
            try:
                if self.serial_connection and self.serial_connection.in_waiting > 0:
                    data = self.serial_connection.read(self.serial_connection.in_waiting).decode('utf-8', errors='ignore')
                    buffer += data
                    
                    while SERIAL_LINE_ENDING in buffer:
                        line, buffer = buffer.split(SERIAL_LINE_ENDING, 1)
                        if line.strip():
                            await self._handle_serial_message(line)
                
                await asyncio.sleep(0.1)
                
            except Exception as e:
                _LOGGER.error("Error reading from serial: %s", e)
                await asyncio.sleep(1)

    async def _handle_serial_message(self, line: str):
        """Handle a message from the serial port."""
        _LOGGER.debug("Received serial message: %s", line)
        
        parsed = self.parser.parse_command(line)
        if not parsed:
            return
        
        command = parsed.get("command")
        device_type = parsed.get("device_type")
        device_id = parsed.get("device_id")
        
        if command == ZIGBEE_CMD_ADD:
            await self._handle_add_device(parsed)
        elif command == ZIGBEE_CMD_DEL:
            await self._handle_delete_device(parsed)
        elif command == ZIGBEE_CMD_STATE:
            await self._handle_state_update(parsed)

    async def _handle_add_device(self, parsed: dict[str, Any]):
        """Handle device addition."""
        device_id = parsed.get("device_id")
        device_type = parsed.get("device_type")
        
        if device_id is None:
            _LOGGER.warning("Add device command missing device_id")
            return
        
        category = DEVICE_CATEGORY_LIGHT if device_type == ZIGBEE_DEVICE_BULB else DEVICE_CATEGORY_SWITCH
        device_data = {
            "device_id": f"zigbee_{device_type}_{device_id}",
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
        
        if device_id is None:
            _LOGGER.warning("State update missing device_id")
            return
        
        device_data = self._devices.get(device_id)
        if not device_data:
            _LOGGER.warning("State update for unknown device ID: %d", device_id)
            return
        
        if device_type == ZIGBEE_DEVICE_BULB:
            if brightness is not None:
                brightness = max(0, min(255, int(brightness)))
                device_data["properties"]["brightness"] = brightness
                device_data["properties"]["light_state"] = True
            else:
                device_data["properties"]["light_state"] = True
        
        elif device_type == ZIGBEE_DEVICE_SWITCH:
            device_data["properties"]["switch_state"] = True
            _LOGGER.info("Zigbee switch %d pressed", device_id)
        
        device_manager_id = device_data["device_id"]
        if device_manager_id in self.device_manager.devices:
            self.device_manager.devices[device_manager_id].update(device_data)
            self.device_manager.devices[device_manager_id]["last_seen"] = datetime.now(UTC).isoformat()
            await self.device_manager._async_notify_device_update(
                self.device_manager.devices[device_manager_id]
            )

    async def send_pairing_command(self):
        """Send pairing command to enter pairing mode."""
        command = self.parser.build_command(ZIGBEE_CMD_PAIR, "")
        await self._write_serial(command)
        _LOGGER.info("Sent pairing command")

    async def send_control_command(self, device_id: int, device_type: str, state: bool, brightness: int | None = None):
        """Send control command to device."""
        command = self.parser.build_command(
            ZIGBEE_CMD_STATE,
            device_type,
            device_id=device_id,
            state=state,
            brightness=brightness
        )
        await self._write_serial(command)
        _LOGGER.info("Sent control command: device_id=%d, type=%s, state=%s, brightness=%s", 
                    device_id, device_type, state, brightness)

    async def _write_serial(self, data: str):
        """Write data to serial port."""
        try:
            if self.serial_connection and self.serial_connection.is_open:
                self.serial_connection.write(data.encode('utf-8'))
                _LOGGER.debug("Sent serial command: %s", data.strip())
            else:
                _LOGGER.error("Serial connection not open")
        except Exception as e:
            _LOGGER.error("Error writing to serial: %s", e)

    def get_device_by_zigbee_id(self, zigbee_id: int) -> dict[str, Any] | None:
        """Get device data by Zigbee ID."""
        return self._devices.get(zigbee_id)

