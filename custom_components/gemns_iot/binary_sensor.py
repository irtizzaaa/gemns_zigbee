"""Binary sensor platform for Gemns™ IoT integration."""

import logging
from typing import Any, Dict, Optional
from datetime import datetime, timezone

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_NAME,
)

from .const import (
    DOMAIN,
    DEVICE_STATUS_CONNECTED,
    DEVICE_STATUS_OFFLINE,
    SIGNAL_DEVICE_UPDATED,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gemns™ IoT binary sensors from a config entry."""
    
    # Get device manager
    device_manager = hass.data[DOMAIN][config_entry.entry_id].get("device_manager")
    if not device_manager:
        return
        
    # Create binary sensor entities for dongle status
    entities = []
    
    # BLE Connection Status
    ble_sensor = GemnsBLESensor(device_manager)
    entities.append(ble_sensor)
    
    # Zigbee Connection Status
    zigbee_sensor = GemnsZigbeeSensor(device_manager)
    entities.append(zigbee_sensor)
    
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
            sw_version=device.get("firmware_version", "1.0.0"),
            configuration_url=f"https://github.com/manaam216/gemns_integration/blob/main/README.md",
            image="/local/gemns/ble_dongle.png",
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
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return entity specific state attributes."""
        ble_devices = [d for d in self.device_manager.get_all_devices() 
                      if d.get("device_type") == "ble"]
        
        return {
            "device_count": len(ble_devices),
            "configured_devices": [d.get("device_id") for d in ble_devices],
            "last_update": datetime.now(timezone.utc).isoformat(),
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
            sw_version=device.get("firmware_version", "1.0.0"),
            configuration_url=f"https://github.com/manaam216/gemns_integration/blob/main/README.md",
            image="/local/gemns/zigbee_dongle.png",
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
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return entity specific state attributes."""
        zigbee_devices = [d for d in self.device_manager.get_all_devices() 
                         if d.get("device_type") == "zigbee"]
        
        return {
            "device_count": len(zigbee_devices),
            "configured_devices": [d.get("device_id") for d in zigbee_devices],
            "last_update": datetime.now(timezone.utc).isoformat(),
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
