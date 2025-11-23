
"""BLE sensor platform for Gemns™ IoT integration."""

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, PERCENTAGE, UnitOfPressure, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble_binary_sensor import GemnsBLEBinarySensor
from .ble_coordinator import GemnsBluetoothProcessorCoordinator
from .const import CONF_ADDRESS, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gemns™ IoT BLE sensors from a config entry."""
    _LOGGER.info("Setting up BLE sensor for entry %s", config_entry.entry_id)

    # Get address from config data or unique_id
    address = config_entry.data.get(CONF_ADDRESS)
    if not address or address == "00:00:00:00:00:00":
        address = config_entry.unique_id

    # If still no address, skip BLE sensor setup
    if not address or address.startswith(("gemns_temp_", "gemns_discovery_")):
        _LOGGER.info("No real BLE device address found, skipping BLE sensor setup for entry %s", config_entry.entry_id)
        return

    _LOGGER.info("BLE device address found: %s", address)

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

    _LOGGER.info("BLE coordinator found for entry %s, creating sensor entities", config_entry.entry_id)

    # Create entities based on device type
    entities = []

    # Get device type from config to determine which entities to create
    device_type = config_entry.data.get("device_name", "unknown")
    device_type = config_entry.data.get("device_type", 4)

    _LOGGER.info("Creating entities for device type: %s, device_type: %d", device_type, device_type)

    # Create entities based on device type (matching device_type_t enum)
    if device_type in ["leak_sensor"] or device_type == 4:
        # DEVICE_TYPE_LEAK_SENSOR = 4 - create binary sensor only
        binary_sensor_entity = GemnsBLEBinarySensor(coordinator, config_entry)
        entities.append(binary_sensor_entity)
        _LOGGER.info("Created binary sensor entity for leak sensor")

    elif device_type in ["vibration_sensor"] or device_type == 2:
        # DEVICE_TYPE_VIBRATION_MONITOR = 2 - create binary sensor only
        binary_sensor_entity = GemnsBLEBinarySensor(coordinator, config_entry)
        entities.append(binary_sensor_entity)
        _LOGGER.info("Created binary sensor entity for vibration monitor")

    elif device_type in ["two_way_switch"] or device_type == 3:
        # DEVICE_TYPE_TWO_WAY_SWITCH = 3 - create binary sensor only
        binary_sensor_entity = GemnsBLEBinarySensor(coordinator, config_entry)
        entities.append(binary_sensor_entity)
        _LOGGER.info("Created binary sensor entity for two-way switch")

    elif device_type in ["button", "legacy"] or device_type in [0, 1]:
        # DEVICE_TYPE_LEGACY = 0, DEVICE_TYPE_BUTTON = 1 - create binary sensor only
        binary_sensor_entity = GemnsBLEBinarySensor(coordinator, config_entry)
        entities.append(binary_sensor_entity)
        _LOGGER.info("Created binary sensor entity for button/legacy device")

    else:
        # Unknown device type - create binary sensor (fallback)
        _LOGGER.warning("Unknown device type %s, creating binary sensor", device_type)
        binary_sensor_entity = GemnsBLEBinarySensor(coordinator, config_entry)
        entities.append(binary_sensor_entity)

    if entities:
        async_add_entities(entities)


class GemnsBLESensor(SensorEntity):
    """Representation of a Gemns™ IoT BLE sensor."""

    def __init__(
        self,
        coordinator: GemnsBluetoothProcessorCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the BLE sensor."""
        self.coordinator = coordinator
        self.config_entry = config_entry
        # Don't store address statically - get it dynamically from config data

        # Set up basic entity properties
        self._attr_name = config_entry.data.get(CONF_NAME, "Gemns™ IoT Device")
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}"
        self._attr_should_poll = False

        # Set device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=self._attr_name,
            manufacturer="Gemns™ IoT",
            model="BLE Sensor",
            sw_version=self.coordinator.data.get("firmware_version", "1.0.0"),
        )

        # Initialize sensor properties
        self._attr_device_class = None
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = None
        self._attr_native_value = None
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
            "signal_strength": None,
            "battery_level": None,
            "last_seen": None,
            "ble_active": False,
            "ble_connected": False,
            "ble_status": "inactive",
        }

        # Add data from coordinator if available
        if self.coordinator.data:
            attrs.update({
                "rssi": self.coordinator.data.get("rssi"),
                "signal_strength": self.coordinator.data.get("signal_strength"),
                "battery_level": self.coordinator.data.get("battery_level"),
                "last_seen": self.coordinator.data.get("timestamp"),
                "ble_active": True,  # If we have data, BLE is active
                "ble_connected": self.coordinator.available,  # Use coordinator availability
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
            self._update_from_coordinator()
            self.async_write_ha_state()
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            _LOGGER.error("Error handling coordinator update for %s: %s", self.address, e)

    def _update_from_coordinator(self) -> None:
        """Update sensor state from coordinator data."""
        if not self.coordinator.data:
            # Simple restart detection: if device exists but no data, keep available but no value
            self._attr_available = True  # Keep available, just no data
            self._attr_native_value = None
            _LOGGER.debug("BLE sensor %s: No coordinator data - device available but no data (restart scenario)", self.address)
            return

        data = self.coordinator.data
        _LOGGER.info("UPDATING SENSOR: %s | Coordinator data: %s", self.address, data)

        # Update device type
        self._device_type = data.get("device_type", "unknown")
        _LOGGER.info("DEVICE TYPE: %s | Type: %s", self.address, self._device_type)

        # Set sensor properties based on device type
        self._set_sensor_properties()

        # Update device info with proper name and model
        self._update_device_info()

        # Extract sensor value
        self._extract_sensor_value(data)

        # Update availability
        self._attr_available = True
        _LOGGER.info("SENSOR UPDATED: %s | Available: %s | Value: %s | BLE_active: %s | Coordinator_available: %s",
                     self.address, self._attr_available, self._attr_native_value, True, self.coordinator.available)

    def _set_sensor_properties(self) -> None:
        """Set sensor properties based on device type."""
        device_type = self._device_type.lower()

        # Reset to defaults
        self._attr_device_class = None
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = None
        self._attr_icon = None

        # Set properties based on device type
        # Skip leak sensors - they should be handled by binary sensor
        if "leak" in device_type:
            # Don't create sensor entities for leak sensors
            return

        # Skip switch devices - they should be handled by switch platform
        if "switch" in device_type:
            # Don't create sensor entities for switch devices
            return

        if "temperature" in device_type:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_name = f"Gemns™ IoT Button {self._get_professional_device_id()}"
            self._attr_icon = "mdi:thermometer"

        elif "humidity" in device_type:
            self._attr_device_class = SensorDeviceClass.HUMIDITY
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_name = f"Gemns™ IoT Vibration Monitor {self._get_professional_device_id()}"
            self._attr_icon = "mdi:water-percent"

        elif "pressure" in device_type:
            self._attr_device_class = SensorDeviceClass.PRESSURE
            self._attr_native_unit_of_measurement = UnitOfPressure.HPA
            self._attr_name = f"Gemns™ IoT Two Way Switch {self._get_professional_device_id()}"
            self._attr_icon = "mdi:gauge"

        elif "vibration" in device_type:
            self._attr_device_class = SensorDeviceClass.VIBRATION
            self._attr_native_unit_of_measurement = "m/s²"
            self._attr_name = f"Gemns™ IoT Vibration Sensor {self._get_professional_device_id()}"
            self._attr_icon = "mdi:vibrate"

        else:
            # Generic sensor
            self._attr_name = f"Gemns™ IoT Sensor {self._get_professional_device_id()}"
            self._attr_icon = "mdi:chip"

    def _update_device_info(self) -> None:
        """Update device info with proper name and model."""
        device_type = self._device_type.lower()

        # Set model based on device type
        model_map = {
            "leak_sensor": "Leak Sensor",
            "button": "Button",
            "vibration_sensor": "Vibration Monitor",
            "two_way_switch": "Two Way Switch",
            "on_off_switch": "On/Off Switch",
            "light_switch": "Light Switch",
            "door_switch": "Door Switch",
            "toggle_switch": "Toggle Switch",
            "unknown_device": "IoT Device"
        }

        model = model_map.get(device_type, "IoT Sensor")

        # Set device image based on device type
        device_image = self._get_device_image(device_type)

        # Update device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.address)},
            name=self._attr_name,
            manufacturer="Gemns™ IoT",
            model=model,
            sw_version=self.coordinator.data.get("firmware_version", "1.0.0"),
        )

        # Set device image if available
        if device_image:
            self._attr_device_info["image"] = device_image

    def _get_device_image(self, device_type: str) -> str:
        """Get device image URL based on device type."""
        # Map device types to their corresponding image paths
        image_map = {
            "temperature_sensor": "/local/custom_components/gemns_iot/static/icon.png",
            "humidity_sensor": "/local/custom_components/gemns_iot/static/icon.png",
            "pressure_sensor": "/local/custom_components/gemns_iot/static/icon.png",
            "vibration_sensor": "/local/custom_components/gemns_iot/static/icon.png",
            "leak_sensor": "/local/custom_components/gemns_iot/static/icon.png",
            "on_off_switch": "/local/custom_components/gemns_iot/static/icon.png",
            "light_switch": "/local/custom_components/gemns_iot/static/icon.png",
            "door_switch": "/local/custom_components/gemns_iot/static/icon.png",
            "toggle_switch": "/local/custom_components/gemns_iot/static/icon.png",
        }

        return image_map.get(device_type.lower(), "/local/custom_components/gemns_iot/static/icon.png")

    def _extract_sensor_value(self, data: dict[str, Any]) -> None:
        """Extract sensor value from coordinator data."""
        _LOGGER.info("EXTRACTING SENSOR VALUE: %s | Data: %s", self.address, data)

        # Try to get sensor value from sensor_data
        sensor_data = data.get("sensor_data", {})
        _LOGGER.info("SENSOR DATA: %s | Sensor data: %s", self.address, sensor_data)

        # Skip leak sensors - they should be handled by binary sensor
        if "leak_detected" in sensor_data:
            # Don't process leak sensors in regular sensor
            _LOGGER.info("LEAK SENSOR SKIPPED: %s | Leak detected: %s (handled by binary sensor)",
                        self.address, sensor_data["leak_detected"])

        elif "temperature" in sensor_data:
            self._attr_native_value = sensor_data["temperature"]
            _LOGGER.info("TEMPERATURE SENSOR: %s | Temperature: %s",
                        self.address, self._attr_native_value)

        elif "humidity" in sensor_data:
            self._attr_native_value = sensor_data["humidity"]
            _LOGGER.info("HUMIDITY SENSOR: %s | Humidity: %s",
                        self.address, self._attr_native_value)

        elif "pressure" in sensor_data:
            self._attr_native_value = sensor_data["pressure"]
            _LOGGER.info("PRESSURE SENSOR: %s | Pressure: %s",
                        self.address, self._attr_native_value)

        elif "vibration" in sensor_data:
            self._attr_native_value = sensor_data["vibration"]
            _LOGGER.info("VIBRATION SENSOR: %s | Vibration: %s",
                        self.address, self._attr_native_value)

        elif "battery_level" in data and data["battery_level"] is not None:
            # Use battery level as a fallback sensor value
            self._attr_native_value = data["battery_level"]
            _LOGGER.info("BATTERY LEVEL: %s | Battery: %s",
                        self.address, self._attr_native_value)

        else:
            # No specific sensor value found, use RSSI as a signal strength indicator
            rssi = data.get("rssi")
            if rssi is not None:
                # Convert RSSI to a percentage (rough approximation)
                # RSSI typically ranges from -100 (very weak) to -30 (very strong)
                signal_percentage = max(0, min(100, (rssi + 100) * 100 / 70))
                self._attr_native_value = round(signal_percentage, 1)
                _LOGGER.info("RSSI SIGNAL: %s | RSSI: %s dBm | Signal: %s%%",
                            self.address, rssi, self._attr_native_value)
            else:
                self._attr_native_value = None
                _LOGGER.warning("NO SENSOR VALUE: %s | No RSSI or sensor data found", self.address)

    async def async_update(self) -> None:
        """Update sensor state."""
        await self.coordinator.async_request_refresh()
