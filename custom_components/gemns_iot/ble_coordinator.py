"""BLE coordinator for Gemns™ IoT integration using Home Assistant's Bluetooth infrastructure."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import struct
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfo,
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
    async_last_service_info,
)
from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothDataUpdateCoordinator,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import BLE_COMPANY_ID, CONF_ADDRESS, CONF_DECRYPTION_KEY
from .packet_parser import parse_gems_packet

_LOGGER = logging.getLogger(__name__)

FALLBACK_POLL_INTERVAL = timedelta(seconds=10)


class GemnsBluetoothProcessorCoordinator(
    PassiveBluetoothDataUpdateCoordinator
):
    """Coordinator for Gemns™ IoT Bluetooth devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the Gemns™ IoT Bluetooth processor coordinator."""
        self._entry = entry
        # Always check config data first for real MAC address
        real_address = entry.data.get(CONF_ADDRESS)
        _LOGGER.info("Config data: %s", entry.data)
        _LOGGER.info("Unique ID: %s", entry.unique_id)
        _LOGGER.info("Address from config: %s", real_address)

        # Use real MAC address if available, otherwise use discovery identifier
        if real_address and real_address != "00:00:00:00:00:00":
            address = real_address.upper()
            _LOGGER.info("Using real MAC address: %s", address)
        else:
            address = f"gemns_discovery_{entry.entry_id}"
            _LOGGER.info("Using discovery identifier: %s", address)

        assert address is not None
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            address=address,
            mode=BluetoothScanningMode.PASSIVE,
            connectable=False,
        )
        self.data = {}
        self.last_update_success = True

    async def async_init(self) -> None:
        """Initialize the coordinator."""
        _LOGGER.info("Coordinator async_init with address: %s", self.address)

        # If we're using discovery identifier, try to discover devices
        if self.address.startswith("gemns_discovery_"):
            _LOGGER.warning("Using discovery identifier, will discover real device")
            await self._discover_and_update_address()
            return

        # For event-driven devices (like leak sensors), don't require immediate advertisement
        # These devices may only send data once per year, so we should not fail setup
        service_info = async_last_service_info(self.hass, self.address)
        if service_info:
            _LOGGER.info("Found recent advertisement for %s", self.address)
            # Process the existing advertisement data
            parsed_data = self._parse_advertisement_data(service_info)
            self.data = parsed_data
            self.last_update_success = True
        else:
            _LOGGER.info("No recent advertisement for %s - this is normal for event-driven devices", self.address)
            # Don't fail setup - just mark as waiting for data
            self.data = {}
            self.last_update_success = True

        # Set up fallback polling for devices that don't advertise frequently
        self._entry.async_on_unload(
            async_track_time_interval(
                self.hass, self._async_schedule_poll, FALLBACK_POLL_INTERVAL
            )
        )

        # Add test mode for firmware version testing (when no real device is available)
        if self.address.startswith("gemns_discovery_"):
            _LOGGER.info("TEST MODE: Simulating firmware version 1.0 (byte 16) for testing")
            self._simulate_test_packet()

        # Don't call parent async_init as it may raise ConfigEntryNotReady
        # for devices that don't advertise immediately

    @property
    def available(self) -> bool:
        """Return if coordinator is available."""
        # Always available - just track if we have recent data
        return True

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: BluetoothServiceInfoBleak,
        change: BluetoothChange,
    ) -> None:
        """Handle a Bluetooth event."""
        super()._async_handle_bluetooth_event(service_info, change)
        try:
            _LOGGER.info("BLE EVENT: %s | RSSI: %s | Name: %s | Change: %s",
                        self.address, service_info.rssi, service_info.name, change)

            # Parse the advertisement data and update our data
            parsed_data = self._parse_advertisement_data(service_info)
            self.data = parsed_data
            self.last_update_success = True

            _LOGGER.info("BLE DATA PARSED: %s | Data: %s", self.address, parsed_data)
            self.async_update_listeners()

        except (ValueError, KeyError, AttributeError, TypeError) as e:
            self.last_update_success = False
            _LOGGER.error("BLE PARSE ERROR: %s | Error: %s", self.address, e)

    def _parse_advertisement_data(self, service_info: BluetoothServiceInfo) -> dict[str, Any]:
        """Parse Gemns™ IoT advertisement data using new packet format."""
        # Get professional device ID
        clean_address = service_info.address.replace(":", "").upper()
        last_6 = clean_address[-6:]
        device_number = int(last_6, 16) % 1000  # Get a number between 0-999
        professional_id = f"Unit-{device_number:03d}"

        data = {
            "address": service_info.address,
            "name": service_info.name or f"Gemns™ IoT Device {professional_id}",
            "rssi": service_info.rssi,
            "timestamp": datetime.now().isoformat(),
            "device_type": "unknown",
            "sensor_data": {},
            "battery_level": None,
            "signal_strength": service_info.rssi,
        }

        # Parse manufacturer data for Gemns™ IoT devices using new packet format
        if service_info.manufacturer_data:
            _LOGGER.info("MANUFACTURER DATA: %s | IDs: %s", self.address, list(service_info.manufacturer_data.keys()))
            for manufacturer_id, manufacturer_data in service_info.manufacturer_data.items():
                _LOGGER.info("MANUFACTURER: %s | ID: 0x%04X | Data: %s",
                            self.address, manufacturer_id, manufacturer_data.hex())
                if manufacturer_id == BLE_COMPANY_ID:  # Gemns™ IoT manufacturer ID (0x0F9C)
                    _LOGGER.info("GEMNS™ IOT DEVICE DETECTED: %s | Parsing data...", self.address)
                    parsed_data = self._parse_gems_manufacturer_data(manufacturer_data)
                    if parsed_data:
                        data.update(parsed_data)
                        _LOGGER.info("GEMNS™ IOT DATA PARSED: %s | Result: %s", self.address, parsed_data)
                        _LOGGER.info("FIRMWARE VERSION CHECK: %s | firmware_version in data: %s", self.address, data.get("firmware_version"))
                    else:
                        _LOGGER.warning("GEMNS™ IOT PARSE FAILED: %s | Data: %s", self.address, manufacturer_data.hex())
                else:
                    _LOGGER.debug("NON-GEMNS™ IOT: %s | ID: 0x%04X", self.address, manufacturer_id)
        else:
            _LOGGER.warning("NO MANUFACTURER DATA: %s", self.address)

        if 'sensor_data' in data and 'device_type' in data['sensor_data']:
            device_type = data['sensor_data']['device_type']
            _LOGGER.info("DEVICE TYPE DETECTION: device_type=%d (0x%04X)", device_type, device_type)
            if device_type == 0:
                data["device_type"] = "push_button"
                data["name"] = f"Gemns™ IoT Push Button {professional_id}"
                _LOGGER.info("  Identified as: push_button")
            elif device_type == 1:
                data["device_type"] = "push_button"
                data["name"] = f"Gemns™ IoT Button {professional_id}"
                _LOGGER.info("  Identified as: push_button")
            elif device_type == 2:
                data["device_type"] = "door_sensor"
                data["name"] = f"Gemns™ IoT Door Sensor {professional_id}"
                _LOGGER.info("  Identified as: door_sensor")
            elif device_type == 3:
                data["device_type"] = "two_way_switch"
                data["name"] = f"Gemns™ IoT Two Way Switch {professional_id}"
                _LOGGER.info("  Identified as: two_way_switch")
            elif device_type == 4:
                data["device_type"] = "leak_sensor"
                data["name"] = f"Gemns™ IoT Leak Sensor {professional_id}"
                _LOGGER.info("  Identified as: leak_sensor")
            elif device_type == 5:
                data["device_type"] = "vibration_sensor"
                data["name"] = f"Gemns™ IoT Vibration Sensor {professional_id}"
                _LOGGER.info("  Identified as: vibration_sensor")
            elif device_type == 6:
                data["device_type"] = "on_off_switch"
                data["name"] = f"Gemns™ IoT On/Off Switch {professional_id}"
                _LOGGER.info("  Identified as: on_off_switch")
            elif device_type == 7:
                data["device_type"] = "light_switch"
                data["name"] = f"Gemns™ IoT Light Switch {professional_id}"
                _LOGGER.info("  Identified as: light_switch")
            elif device_type == 8:
                data["device_type"] = "door_switch"
                data["name"] = f"Gemns™ IoT Door Switch {professional_id}"
                _LOGGER.info("  Identified as: door_switch")
            elif device_type == 9:
                data["device_type"] = "toggle_switch"
                data["name"] = f"Gemns™ IoT Toggle Switch {professional_id}"
                _LOGGER.info("  Identified as: toggle_switch")
            else:
                _LOGGER.warning("  Unknown device type: %d (0x%04X)", device_type, device_type)

        _LOGGER.info("FINAL DATA CHECK: %s | firmware_version: %s", self.address, data.get("firmware_version"))
        return data

    def _parse_gems_manufacturer_data(self, data: bytes) -> dict[str, Any]:
        """Parse Gemns™ IoT manufacturer data using 18-byte packet format."""
        _LOGGER.info("PARSING GEMNS DATA: Length=%d | Data=%s", len(data), data.hex())

        if len(data) < 18:  # Gemns™ IoT packet format is 18 bytes
            _LOGGER.warning("INVALID PACKET LENGTH: %d bytes (expected 18)", len(data))
            return {}

        # Parse packet structure: HA BLE driver filters out Company ID (2 bytes)
        # So we receive: Flags (1) + Encrypted Data (16) + CRC (1) = 18 bytes
        if len(data) < 18:
            _LOGGER.error("PACKET TOO SHORT: %d bytes (need 18)", len(data))
            return {}

        _LOGGER.info("PACKET DEBUG: Length=%d, Data=%s", len(data), data.hex())

        try:
            # Company ID is already filtered by HA BLE driver (0x0F9C)
            company_id = 0x0F9C  # Gemns™ IoT company ID (filtered by HA)
            flags = data[0]  # 1 byte
            encrypted_data = data[1:17]  # 16 bytes (positions 1-16)
            crc = data[17]  # 1 byte (position 17, last byte)

            _LOGGER.info("PACKET STRUCTURE: Company ID=0x%04X (filtered by HA), Flags=0x%02X, CRC=0x%02X",
                        company_id, flags, crc)
            _LOGGER.info("ENCRYPTED DATA (%d bytes): %s", len(encrypted_data), encrypted_data.hex())

        except (IndexError, struct.error) as e:
            _LOGGER.error("PACKET PARSING ERROR: %s", e)
            return {}

        # Get decryption key from config entry
        decryption_key = None
        if hasattr(self._entry, 'data') and CONF_DECRYPTION_KEY in self._entry.data:
            try:
                decryption_key = bytes.fromhex(self._entry.data[CONF_DECRYPTION_KEY])
                _LOGGER.info("DECRYPTION KEY: %s", self._entry.data[CONF_DECRYPTION_KEY])
            except ValueError:
                _LOGGER.error("INVALID DECRYPTION KEY FORMAT: %s", self._entry.data[CONF_DECRYPTION_KEY])
        else:
            _LOGGER.warning("NO DECRYPTION KEY FOUND in config entry")

        # Parse the full 18-byte packet using the parser
        _LOGGER.info("CALLING PACKET PARSER: packet_data=%s, key=%s",
                    data.hex(), decryption_key.hex() if decryption_key else "None")

        parsed_packet = parse_gems_packet(data, decryption_key)

        if not parsed_packet:
            _LOGGER.error("PACKET PARSER RETURNED EMPTY RESULT")
            return {}

        _LOGGER.info("PACKET PARSED SUCCESSFULLY: %s", parsed_packet)

        result = {
            "company_id": company_id,
            "flags": flags,
            "crc": crc,
            "packet_structure": {
                "company_id": company_id,
                "flags": flags,
                "encrypted_data_length": len(encrypted_data),
                "crc": crc,
            }
        }

        # Add decrypted data if available
        if 'decrypted_data' in parsed_packet:
            result['decrypted_data'] = parsed_packet['decrypted_data']
            _LOGGER.info("DECRYPTED DATA: %s", parsed_packet['decrypted_data'])

            # Extract firmware version from decrypted data
            decrypted_data = parsed_packet['decrypted_data']
            if 'firmware_version' in decrypted_data:
                result['firmware_version'] = decrypted_data['firmware_version']
                _LOGGER.info("FIRMWARE VERSION: %s", decrypted_data['firmware_version'])

        # Add sensor data if available
        if 'sensor_data' in parsed_packet:
            result['sensor_data'] = parsed_packet['sensor_data']
            _LOGGER.info("SENSOR DATA: %s", parsed_packet['sensor_data'])

            # Extract specific sensor values
            sensor_data = parsed_packet['sensor_data']
            if 'leak_detected' in sensor_data:
                result['leak_detected'] = sensor_data['leak_detected']
                _LOGGER.info("LEAK DETECTED: %s", sensor_data['leak_detected'])
            if 'event_counter' in sensor_data:
                result['event_counter'] = sensor_data['event_counter']
                _LOGGER.info("EVENT COUNTER: %s", sensor_data['event_counter'])
            if 'sensor_event' in sensor_data:
                result['sensor_event'] = sensor_data['sensor_event']
                _LOGGER.info("SENSOR EVENT: %s", sensor_data['sensor_event'])

        _LOGGER.info("FINAL RESULT: %s", result)
        return result

    @callback
    def _async_schedule_poll(self, _: datetime) -> None:
        """Schedule a poll of the device."""
        # Simple restart detection: if device exists but no data, default to off
        if self.data:
            self.last_update_success = True
            self.async_update_listeners()
        else:
            # Device exists but no data (restart scenario) - keep available but no data
            self.last_update_success = False
            _LOGGER.debug("Device %s exists but no data - keeping available but no data (restart scenario)", self.address)
            self.async_update_listeners()

    async def _discover_and_update_address(self) -> None:
        """Discover Gemns™ IoT devices and update the address if found."""
        try:
            _LOGGER.info("Discovering Gemns™ IoT devices...")
            discovered_devices = async_discovered_service_info(self.hass)

            _LOGGER.info("Found %d total Bluetooth devices", len(discovered_devices))
            for device in discovered_devices:
                _LOGGER.info("Checking device: %s (%s)", device.name, device.address)
                if self._is_gems_device(device):
                    _LOGGER.info("Found Gemns™ IoT device: %s (%s)", device.name, device.address)
                    # Update the config entry with the real MAC address
                    new_data = self._entry.data.copy()
                    new_data[CONF_ADDRESS] = device.address.upper()
                    self.hass.config_entries.async_update_entry(self._entry, data=new_data)
                    _LOGGER.info("Updated config entry with real MAC address: %s", device.address)

                    # Update coordinator address dynamically
                    await self._update_coordinator_address(device.address.upper())
                    break
            else:
                _LOGGER.warning("No Gemns™ IoT devices found during discovery")
                # Schedule another discovery attempt in 5 seconds
                self.hass.async_create_task(self._schedule_next_discovery())

        except (ValueError, KeyError, AttributeError, TypeError) as e:
            _LOGGER.error("Discovery error: %s", e)
            # Schedule another discovery attempt in 5 seconds
            self.hass.async_create_task(self._schedule_next_discovery())

    async def _schedule_next_discovery(self) -> None:
        """Schedule the next discovery attempt."""
        await asyncio.sleep(5)  # Wait 5 seconds
        if self.address.startswith("gemns_discovery_"):
            _LOGGER.info("Retrying discovery...")
            await self._discover_and_update_address()

    def _is_gems_device(self, discovery_info: BluetoothServiceInfo) -> bool:
        """Check if this is a Gemns™ IoT device."""
        # Check manufacturer data for Gemns™ IoT Company ID (22352)
        if discovery_info.manufacturer_data:
            for manufacturer_id, data in discovery_info.manufacturer_data.items():
                _LOGGER.info("Checking manufacturer data: Company ID %d, Data length: %d", manufacturer_id, len(data))
                if manufacturer_id == BLE_COMPANY_ID and len(data) >= 20:
                    _LOGGER.info("Found Gemns™ IoT device by manufacturer data")
                    return True

        # Check name patterns as fallback
        name = discovery_info.name or ""
        _LOGGER.info("Checking device name: '%s'", name)
        if any(pattern in name.upper() for pattern in ["GEMNS", "GEMS"]):
            _LOGGER.info("Found Gemns™ IoT device by name pattern")
            return True

        return False

    async def _update_coordinator_address(self, new_address: str) -> None:
        """Update the coordinator's address dynamically."""
        try:
            _LOGGER.info("Updating coordinator address from %s to %s", self.address, new_address)

            # Update the address
            self.address = new_address

            # Try to connect to the new address
            if service_info := async_last_service_info(self.hass, self.address):
                _LOGGER.info("Successfully connected to device at %s", self.address)
                # Process the advertisement data
                parsed_data = self._parse_advertisement_data(service_info)
                self.data = parsed_data
                self.last_update_success = True
                self.async_update_listeners()
                _LOGGER.info("Device data updated: %s", parsed_data)
            else:
                _LOGGER.warning("No advertisement found for device at %s", self.address)

        except (ValueError, KeyError, AttributeError, TypeError) as e:
            _LOGGER.error("Error updating coordinator address: %s", e)

    def _simulate_test_packet(self) -> None:
        """Simulate a test packet with firmware version 1.0 (byte 16) for testing purposes."""
        try:
            _LOGGER.info("SIMULATING TEST PACKET: Creating test data with firmware version 1.0")

            # Create test data that simulates what would come from a real packet
            test_data = {
                "address": self.address,
                "name": "Gemns™ IoT Test Device Unit-001",
                "rssi": -50,
                "timestamp": datetime.now().isoformat(),
                "device_type": "leak_sensor",
                "sensor_data": {
                    "device_type": 4,  # Leak sensor
                    "leak_detected": False,
                    "event_counter": 1
                },
                "battery_level": None,
                "signal_strength": -50,
                "firmware_version": "1.0",  # This simulates the parsed firmware version
                "decrypted_data": {
                    "src_id": 12345,
                    "nwk_id": 6789,
                    "fw_version": 16,  # Raw firmware byte (16 = 0x10)
                    "firmware_version": "1.0",  # Parsed firmware version
                    "device_type": b'\x00\x04',  # Leak sensor
                    "payload": b'\x00\x00\x00\x00\x00\x00\x00\x00',
                    "event_counter_lsb": 1,
                    "payload_length": 0,
                    "encrypt_status": 1,
                    "power_status": 0,
                }
            }

            # Update coordinator data
            self.data = test_data
            self.last_update_success = True

            _LOGGER.info("TEST PACKET SIMULATED: Firmware version 1.0 set in coordinator data")
            _LOGGER.info("TEST DATA: %s", test_data)

            # Notify listeners
            self.async_update_listeners()

        except (ValueError, KeyError, AttributeError, TypeError) as e:
            _LOGGER.error("Error simulating test packet: %s", e)

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator."""
        _LOGGER.info("Shutting down Gemns™ IoT BLE coordinator")
        # Clean up any resources if needed
        self.data = {}
        self.last_update_success = False
