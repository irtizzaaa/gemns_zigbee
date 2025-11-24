"""Input boolean platform for Gemns™ IoT integration."""

import logging
from typing import Any

from homeassistant.components.input_boolean import InputBoolean
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gemns™ IoT input booleans from a config entry."""

    # Get device manager
    device_manager = hass.data[DOMAIN][config_entry.entry_id].get("device_manager")
    if not device_manager:
        return

    # Create input boolean entities for toggles
    entities = []

    # BLE Toggle
    ble_toggle = GemnsBLEToggle(device_manager)
    entities.append(ble_toggle)

    # Zigbee Toggle
    zigbee_toggle = GemnsZigbeeToggle(device_manager, hass)
    entities.append(zigbee_toggle)

    if entities:
        async_add_entities(entities)


class GemnsBLEToggle(InputBoolean):
    """Representation of BLE toggle."""

    def __init__(self, device_manager):
        """Initialize the BLE toggle."""
        self.device_manager = device_manager
        self._attr_name = "Gemns™ IoT BLE Enabled"
        self._attr_unique_id = f"{DOMAIN}_ble_enabled"
        self._attr_icon = "mdi:bluetooth"
        self._attr_should_poll = False

        # Set device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "ble_toggle")},
            name="Gemns™ IoT BLE Toggle",
            manufacturer="Gemns™ IoT",
            model="BLE Toggle",
            sw_version="1.0.0",
        )

        # Set initial state
        self._attr_is_on = self.device_manager.config.get("enable_ble", True)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the toggle on."""
        self._attr_is_on = True
        self.async_write_ha_state()

        # Update config
        self.device_manager.config["enable_ble"] = True

        # Fire event
        self.hass.bus.async_fire(f"{DOMAIN}_ble_toggled", {"enabled": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the toggle off."""
        self._attr_is_on = False
        self.async_write_ha_state()

        # Update config
        self.device_manager.config["enable_ble"] = False

        # Fire event
        self.hass.bus.async_fire(f"{DOMAIN}_ble_toggled", {"enabled": False})


class GemnsZigbeeToggle(InputBoolean):
    """Representation of Zigbee toggle."""

    def __init__(self, device_manager, hass=None):
        """Initialize the Zigbee toggle."""
        self.device_manager = device_manager
        self.hass = hass
        self._attr_name = "Gemns™ IoT Zigbee Enabled"
        self._attr_unique_id = f"{DOMAIN}_zigbee_enabled"
        self._attr_icon = "mdi:zigbee"
        self._attr_should_poll = False

        # Set device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "zigbee_toggle")},
            name="Gemns™ IoT Zigbee Toggle",
            manufacturer="Gemns™ IoT",
            model="Zigbee Toggle",
            sw_version="1.0.0",
        )

        # Set initial state
        self._attr_is_on = self.device_manager.config.get("enable_zigbee", True)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the toggle on."""
        self._attr_is_on = True
        self.async_write_ha_state()

        # Update config
        self.device_manager.config["enable_zigbee"] = True

        # Start Zigbee coordinator if available
        if self.hass:
            for entry_id, data in self.hass.data.get(DOMAIN, {}).items():
                if isinstance(data, dict):
                    zigbee_coordinator = data.get("zigbee_coordinator")
                    if zigbee_coordinator and not zigbee_coordinator._running:
                        await zigbee_coordinator.async_start()

        # Fire event
        self.hass.bus.async_fire(f"{DOMAIN}_zigbee_toggled", {"enabled": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the toggle off."""
        self._attr_is_on = False
        self.async_write_ha_state()

        # Update config
        self.device_manager.config["enable_zigbee"] = False

        # Stop Zigbee coordinator if available
        if self.hass:
            for entry_id, data in self.hass.data.get(DOMAIN, {}).items():
                if isinstance(data, dict):
                    zigbee_coordinator = data.get("zigbee_coordinator")
                    if zigbee_coordinator and zigbee_coordinator._running:
                        await zigbee_coordinator.async_stop()

        # Fire event
        self.hass.bus.async_fire(f"{DOMAIN}_zigbee_toggled", {"enabled": False})
