"""Binary sensor platform for Gemns™ IoT integration."""

from datetime import UTC, datetime
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_DEVICE_UPDATED

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gemns™ IoT binary sensors from a config entry."""

    # Check if this is a BLE device entry (has address field)
    if config_entry.data.get("address"):
        # This is a BLE device entry, don't create dongle status sensors here
        return

    # Get device manager
    device_manager = hass.data[DOMAIN][config_entry.entry_id].get("device_manager")
    if not device_manager:
        return

    # Create binary sensor entities for dongle status
    entities = []

    # Only create Zigbee sensor if Zigbee is enabled
    enable_zigbee = config_entry.data.get("enable_zigbee", True)
    if enable_zigbee:
        zigbee_sensor = GemnsZigbeeSensor(device_manager)
        entities.append(zigbee_sensor)

    # Only create BLE sensor if this is not a Zigbee-only entry
    # (BLE sensor is for general BLE dongle status, not individual devices)
    # For now, skip BLE sensor if Zigbee is selected
    # BLE sensors are created per-device in ble_binary_sensor.py

    if entities:
        async_add_entities(entities)


class GemnsBLESensor(BinarySensorEntity):
    """Representation of BLE connection status."""

    def __init__(self, device_manager):
        """Initialize the BLE sensor."""
        self.device_manager = device_manager
        self._attr_name = "Gemns™ IoT BLE Connected"
        self._attr_unique_id = f"{DOMAIN}_ble_connected"
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        self._attr_icon = "mdi:bluetooth"
        self._attr_should_poll = False

        # Set device info with custom icon
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "ble_dongle")},
            name="Gemns™ IoT BLE Dongle",
            manufacturer="Gemns™ IoT",
            model="BLE Dongle",
            sw_version="1.0.0",
            configuration_url="https://github.com/manaam216/gemns_integration/blob/main/README.md",
        )

        # Set custom icon for BLE dongle
        self._attr_icon = "mdi:bluetooth"

        # Set initial state
        self._update_state()

    def _update_state(self):
        """Update sensor state from device manager."""
        # Since we removed dongles, check if any BLE devices are configured
        ble_devices = [d for d in self.device_manager.get_all_devices()
                      if d.get("device_type") == "ble"]

        self._attr_is_on = len(ble_devices) > 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        ble_devices = [d for d in self.device_manager.get_all_devices()
                      if d.get("device_type") == "ble"]

        return {
            "device_count": len(ble_devices),
            "configured_devices": [d.get("device_id") for d in ble_devices],
            "last_update": datetime.now(UTC).isoformat(),
        }

    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        # Subscribe to device manager updates
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_DEVICE_UPDATED, self._handle_update
            )
        )

    def _handle_update(self, data):
        """Handle device manager updates."""
        self._update_state()
        # Schedule the state write in the main event loop
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self._async_write_state())
        )

    async def _async_write_state(self):
        """Async helper to write state."""
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Update sensor state."""
        self._update_state()


class GemnsZigbeeSensor(BinarySensorEntity):
    """Representation of Zigbee connection status."""

    def __init__(self, device_manager):
        """Initialize the Zigbee sensor."""
        self.device_manager = device_manager
        self._attr_name = "Gemns™ IoT Zigbee Connected"
        self._attr_unique_id = f"{DOMAIN}_zigbee_connected"
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        self._attr_icon = "mdi:zigbee"
        self._attr_should_poll = False

        # Set device info with custom icon
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "zigbee_dongle")},
            name="Gemns™ IoT Zigbee Dongle",
            manufacturer="Gemns™ IoT",
            model="Zigbee Dongle",
            sw_version="1.0.0",
            configuration_url="https://github.com/manaam216/gemns_integration/blob/main/README.md",
        )

        # Set custom icon for Zigbee dongle
        self._attr_icon = "mdi:zigbee"

        # Set initial state
        self._update_state()

    def _update_state(self):
        """Update sensor state from device manager."""
        # Check if any Zigbee devices are configured
        zigbee_devices = [d for d in self.device_manager.get_all_devices()
                         if d.get("device_type") == "zigbee"]

        self._attr_is_on = len(zigbee_devices) > 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        zigbee_devices = [d for d in self.device_manager.get_all_devices()
                         if d.get("device_type") == "zigbee"]

        return {
            "device_count": len(zigbee_devices),
            "configured_devices": [d.get("device_id") for d in zigbee_devices],
            "last_update": datetime.now(UTC).isoformat(),
        }

    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        # Subscribe to device manager updates
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_DEVICE_UPDATED, self._handle_update
            )
        )

    def _handle_update(self, data):
        """Handle device manager updates."""
        self._update_state()
        # Schedule the state write in the main event loop
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self._async_write_state())
        )

    async def _async_write_state(self):
        """Async helper to write state."""
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Update sensor state."""
        self._update_state()
        
