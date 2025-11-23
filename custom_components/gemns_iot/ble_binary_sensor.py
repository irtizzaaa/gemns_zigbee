"""BLE binary sensor platform for Gemns™ IoT integration."""

import hashlib
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble_coordinator import GemnsBluetoothProcessorCoordinator
from .const import CONF_ADDRESS, CONF_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gemns™ IoT BLE binary sensors from a config entry."""
    _LOGGER.info("Setting up BLE binary sensor for entry %s", config_entry.entry_id)
    address = config_entry.unique_id
    if not address:
        _LOGGER.error("No address found in config entry")
        return

    # Get the BLE coordinator from runtime_data
    coordinator = config_entry.runtime_data
    if not coordinator:
        # Fallback: try to get from hass.data
        _LOGGER.warning("No coordinator in runtime_data, trying hass.data for entry %s", config_entry.entry_id)
        try:
            coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
        except KeyError:
            _LOGGER.error("No coordinator found in runtime_data or hass.data for entry %s", config_entry.entry_id)
            return

    _LOGGER.info("BLE coordinator found for entry %s, creating binary sensor entities", config_entry.entry_id)

    # Create binary sensor entities based on device type
    entities = []

    # Create a binary sensor entity for leak detection
    binary_sensor_entity = GemnsBLEBinarySensor(coordinator, config_entry)
    entities.append(binary_sensor_entity)

    if entities:
        async_add_entities(entities)


class GemnsBLEBinarySensor(BinarySensorEntity):
    """Representation of a Gemns™ IoT BLE binary sensor."""

    def __init__(
        self,
        coordinator: GemnsBluetoothProcessorCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the BLE binary sensor."""
        self.coordinator = coordinator
        self.config_entry = config_entry
        # Don't store address statically - get it dynamically from config data

        # Set up basic entity properties
        self._attr_name = config_entry.data.get(CONF_NAME, "Gemns™ IoT Device")
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_binary"
        self._attr_should_poll = False

        # Set device info - will be updated when device type is known
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=self._attr_name,
            manufacturer="Gemns™ IoT",
            model="Batteryless IoT Device",  # Generic model, will be updated
            sw_version=self.coordinator.data.get("firmware_version", "1.0.0"),
        )

        # Initialize binary sensor properties
        self._attr_device_class = None
        self._attr_is_on = None
        self._attr_available = False

        # Device type will be determined from coordinator data
        self._device_type = "unknown"

    @property
    def address(self) -> str:
        """Get the current MAC address from config data."""
        return self.config_entry.data.get(CONF_ADDRESS, self.config_entry.unique_id)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.available and self._attr_available

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        attrs = {
            "address": self.address,
            "device_type": self._device_type,
            "rssi": None,
            "last_seen": None,
            "ble_status": "inactive",
        }

        # Add data from coordinator if available
        if self.coordinator.data:
            attrs.update({
                "rssi": self.coordinator.data.get("rssi"),
                "last_seen": self.coordinator.data.get("timestamp"),
                "ble_status": "active" if self.coordinator.available else "inactive",
                "last_update_success": getattr(self.coordinator, 'last_update_success', True),
            })

            # Add sensor-specific attributes
            if "sensor_data" in self.coordinator.data:
                sensor_data = self.coordinator.data["sensor_data"]
                if "leak_detected" in sensor_data:
                    attrs["leak_detected"] = sensor_data["leak_detected"]
                if "event_counter" in sensor_data:
                    attrs["event_counter"] = sensor_data["event_counter"]
                if "sensor_event" in sensor_data:
                    attrs["sensor_event"] = sensor_data["sensor_event"]

        return attrs

    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        await super().async_added_to_hass()
        # Register with coordinator to receive updates
        self._unsub_coordinator = self.coordinator.async_add_listener(self._handle_coordinator_update)
        # Set up cleanup when entity is removed
        self.async_on_remove(self._unsub_coordinator)

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        try:
            # Store previous state to detect changes
            previous_state = self._attr_is_on

            self._update_from_coordinator()

            # Check if state changed and log for automation debugging
            if previous_state != self._attr_is_on:
                _LOGGER.info("BINARY SENSOR STATE CHANGED: %s | Previous: %s | New: %s",
                           self.address, previous_state, self._attr_is_on)

            self.async_write_ha_state()
        except (ValueError, KeyError, AttributeError) as e:
            _LOGGER.error("Error handling coordinator update for %s: %s", self.address, e)

    def _update_from_coordinator(self) -> None:
        """Update binary sensor state from coordinator data."""
        if not self.coordinator.data:
            # Simple restart detection: if device exists but no data, default to off
            self._attr_available = True
            self._attr_is_on = False  # Default to off when device restarts
            _LOGGER.debug("BLE binary sensor %s: No coordinator data - defaulting to off (restart scenario)", self.address)
            return

        data = self.coordinator.data
        _LOGGER.info("UPDATING BINARY SENSOR: %s | Coordinator data: %s", self.address, data)

        # Update device type and name from coordinator data
        self._device_type = data.get("device_type", "unknown")
        coordinator_name = data.get("name", "Gemns™ IoT Device")

        # Update the entity name if coordinator has a better name
        if coordinator_name != "Gemns™ IoT Device":
            self._attr_name = coordinator_name

        _LOGGER.info("DEVICE TYPE: %s | Type: %s | Name: %s", self.address, self._device_type, self._attr_name)

        # Set sensor properties based on device type
        self._set_sensor_properties()

        # Update device info with proper name and model
        self._update_device_info()

        # Extract binary sensor value
        self._extract_binary_sensor_value(data)

        # Update availability
        self._attr_available = True
        _LOGGER.info("BINARY SENSOR UPDATED: %s | Available: %s | Value: %s | BLE_active: %s | Coordinator_available: %s",
                     self.address, self._attr_available, self._attr_is_on, True, self.coordinator.available)

    def _set_sensor_properties(self) -> None:
        """Set binary sensor properties based on device type (matching device_type_t enum)."""
        device_type = self._device_type.lower()

        if device_type == "push_button":
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM
            self._attr_name = f"Gemns™ IoT Push Button {self._get_professional_device_id()}"
            self._attr_icon = "mdi:gesture-tap-button"

        elif device_type == "light_switch":
            self._attr_device_class = BinarySensorDeviceClass.OPENING
            self._attr_name = f"Gemns™ IoT Light Switch {self._get_professional_device_id()}"
            self._attr_icon = "mdi:light-switch"

        elif device_type == "door_sensor":
            self._attr_device_class = BinarySensorDeviceClass.DOOR
            self._attr_name = f"Gemns™ IoT Door Sensor {self._get_professional_device_id()}"
            self._attr_icon = "mdi:door"

        elif device_type == "leak_sensor":
            self._attr_device_class = BinarySensorDeviceClass.MOISTURE
            self._attr_name = f"Gemns™ IoT Leak Sensor {self._get_professional_device_id()}"
            self._attr_icon = "mdi:water"

        elif device_type == "vibration_sensor":
            self._attr_device_class = BinarySensorDeviceClass.VIBRATION
            self._attr_name = f"Gemns™ IoT Vibration Monitor {self._get_professional_device_id()}"
            self._attr_icon = "mdi:vibrate"

        elif device_type == "two_way_switch":
            self._attr_device_class = BinarySensorDeviceClass.OPENING
            self._attr_name = f"Gemns™ IoT Two-Way Switch {self._get_professional_device_id()}"
            self._attr_icon = "mdi:toggle-switch"

        elif device_type in ["button", "legacy"]:
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM
            if device_type == "button":
                self._attr_name = f"Gemns™ IoT Button {self._get_professional_device_id()}"
                self._attr_icon = "mdi:gesture-tap-button"
            else:
                self._attr_name = f"Gemns™ IoT Legacy Device {self._get_professional_device_id()}"
                self._attr_icon = "mdi:chip"

        else:
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM
            self._attr_name = f"Gemns™ IoT Alert {self._get_professional_device_id()}"
            self._attr_icon = "mdi:alert"

    def _update_device_info(self) -> None:
        """Update device info with proper name and model."""
        device_type = self._device_type.lower()

        model_map = {
            "push_button": "Batteryless Push Button",
            "light_switch": "Batteryless Light Switch",
            "door_sensor": "Batteryless Door Sensor",
            "two_way_switch": "Batteryless Two-Way Switch",
            "leak_sensor": "Batteryless Leak Sensor",
            "vibration_sensor": "Batteryless Vibration Monitor",
            "legacy": "Batteryless Legacy Device",
            "button": "Batteryless Button",
            "unknown_device": "Batteryless IoT Device"
        }

        model = model_map.get(device_type, "IoT Sensor")

        # Set suggested area based on device type
        area_map = {
            "leak_sensor": "Kitchen",
            "vibration_sensor": "Garage",
            "button": "Living Room",
            "two_way_switch": "Bedroom",
            "legacy": "Office"
        }
        suggested_area = area_map.get(device_type, "Home")

        # Set device image based on device type
        device_image = self._get_device_image(device_type)

        # Update device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.entry_id)},
            name=self._attr_name,
            manufacturer="Gemns™ IoT",
            model=model,
            sw_version=self.coordinator.data.get("firmware_version", "1.0.0"),
            suggested_area=suggested_area,
        )

        # Set device image if available
        if device_image:
            self._attr_device_info["image"] = device_image

    def _get_professional_device_id(self) -> str:
        """Generate a professional device identifier from MAC address."""
        # Handle test/discovery addresses
        if self.address.startswith("gemns_") or self.address == "00:00:00:00:00:00":
            # For test devices, use entry ID to generate a consistent ID
            entry_id = self.config_entry.entry_id
            # Extract numbers from entry ID or use a hash
            hash_obj = hashlib.md5(entry_id.encode())
            hash_hex = hash_obj.hexdigest()
            device_number = int(hash_hex[:3], 16) % 1000
            return f"Test-{device_number:03d}"

        # Remove colons and get last 6 characters
        clean_address = self.address.replace(":", "").upper()
        last_6 = clean_address[-6:]

        # Convert to a more professional format
        device_number = int(last_6, 16) % 1000  # Get a number between 0-999
        return f"Unit-{device_number:03d}"

    def _get_device_image(self, device_type: str) -> str:
        """Get device image URL based on device type."""
        # Use Home Assistant brand repository for icons
        return "https://brands.home-assistant.io/gemns/icon.png"

    def _extract_binary_sensor_value(self, data: dict[str, Any]) -> None:
        """Extract binary sensor value from coordinator data."""
        _LOGGER.info("EXTRACTING BINARY SENSOR VALUE: %s | Data: %s", self.address, data)

        sensor_data = data.get("sensor_data", {})
        _LOGGER.info("SENSOR DATA: %s | Sensor data: %s", self.address, sensor_data)

        if "button_pressed" in sensor_data:
            self._attr_is_on = sensor_data["button_pressed"]
            _LOGGER.info("PUSH BUTTON BINARY SENSOR: %s | Button pressed: %s | Value: %s",
                        self.address, sensor_data["button_pressed"], self._attr_is_on)

        elif "switch_on" in sensor_data:
            self._attr_is_on = sensor_data["switch_on"]
            _LOGGER.info("SWITCH BINARY SENSOR: %s | Switch on: %s | Value: %s",
                        self.address, sensor_data["switch_on"], self._attr_is_on)

        elif "door_open" in sensor_data:
            self._attr_is_on = sensor_data["door_open"]
            _LOGGER.info("DOOR SENSOR BINARY SENSOR: %s | Door open: %s | Value: %s",
                        self.address, sensor_data["door_open"], self._attr_is_on)

        elif "leak_detected" in sensor_data:
            self._attr_is_on = sensor_data["leak_detected"]
            _LOGGER.info("LEAK BINARY SENSOR: %s | Leak detected: %s | Value: %s",
                        self.address, sensor_data["leak_detected"], self._attr_is_on)

        elif "vibration_detected" in sensor_data:
            self._attr_is_on = sensor_data["vibration_detected"]
            _LOGGER.info("VIBRATION BINARY SENSOR: %s | Vibration detected: %s | Value: %s",
                        self.address, sensor_data["vibration_detected"], self._attr_is_on)

        elif "sensor_event" in sensor_data:
            self._attr_is_on = sensor_data["sensor_event"] > 0
            _LOGGER.info("SENSOR EVENT BINARY: %s | Event: %s | Value: %s",
                        self.address, sensor_data["sensor_event"], self._attr_is_on)

        elif "leak" in self._device_type.lower():
            self._attr_is_on = False
            _LOGGER.info("LEAK SENSOR DEFAULT: %s | No leak data, assuming no leak (False)", self.address)
        else:
            self._attr_is_on = False
            _LOGGER.warning("NO BINARY VALUE: %s | No leak detection or sensor event found", self.address)

    async def async_update(self) -> None:
        """Update binary sensor state."""
        await self.coordinator.async_request_refresh()
