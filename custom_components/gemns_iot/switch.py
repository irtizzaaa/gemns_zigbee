"""Switch platform for Gemns™ IoT integration."""

import json
import logging
from typing import Any, Dict, Optional
from datetime import datetime, timezone

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_NAME,
    ATTR_DEVICE_CLASS,
)

from .const import (
    DOMAIN,
    DEVICE_CATEGORY_SWITCH,
    DEVICE_CATEGORY_LIGHT,
    DEVICE_CATEGORY_DOOR,
    DEVICE_CATEGORY_TOGGLE,
    DEVICE_STATUS_CONNECTED,
    DEVICE_STATUS_OFFLINE,
    SIGNAL_DEVICE_UPDATED,
    SIGNAL_DEVICE_ADDED,
)

_LOGGER = logging.getLogger(__name__)

# Global variable to track entities and add callback
_entities = []
_add_entities_callback = None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gemns™ IoT switches from a config entry."""
    global _entities, _add_entities_callback
    
    # Store the callback for dynamic entity creation
    _add_entities_callback = async_add_entities
    
    # Get device manager
    device_manager = hass.data[DOMAIN][config_entry.entry_id].get("device_manager")
    if not device_manager:
        return
        
    # Get all switch devices
    switch_devices = []
    switch_devices.extend(device_manager.get_devices_by_category(DEVICE_CATEGORY_SWITCH))
    switch_devices.extend(device_manager.get_devices_by_category(DEVICE_CATEGORY_LIGHT))
    switch_devices.extend(device_manager.get_devices_by_category(DEVICE_CATEGORY_DOOR))
    switch_devices.extend(device_manager.get_devices_by_category(DEVICE_CATEGORY_TOGGLE))
    
    # Create switch entities
    entities = []
    for device in switch_devices:
        switch_entity = GemnsSwitch(device_manager, device)
        entities.append(switch_entity)
        _entities.append(switch_entity)
        
    if entities:
        async_add_entities(entities)
    
    # Listen for new devices
    async def handle_new_device(device_data):
        """Handle new device added."""
        category = device_data.get("category")
        if category in [DEVICE_CATEGORY_SWITCH, DEVICE_CATEGORY_LIGHT, DEVICE_CATEGORY_DOOR, DEVICE_CATEGORY_TOGGLE]:
            # Check if entity already exists
            device_id = device_data.get("device_id")
            existing_entity = next((e for e in _entities if e.device_id == device_id), None)
            
            if not existing_entity:
                # Create new entity
                new_entity = GemnsSwitch(device_manager, device_data)
                _entities.append(new_entity)
                _add_entities_callback([new_entity])
                _LOGGER.info(f"Created new switch entity for device: {device_id}")
    
    # Connect to dispatcher
    async_dispatcher_connect(hass, SIGNAL_DEVICE_ADDED, handle_new_device)


class GemnsSwitch(SwitchEntity):
    """Representation of a Gemns™ IoT switch."""

    def __init__(self, device_manager, device: Dict[str, Any]):
        """Initialize the switch."""
        self.device_manager = device_manager
        self.device = device
        self.device_id = device.get("device_id")
        self._attr_name = device.get("name", self.device_id)
        self._attr_unique_id = f"{DOMAIN}_{self.device_id}"
        self._attr_should_poll = False
        
        # Set device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.device_id)},
            name=self._attr_name,
            manufacturer="Gemns™ IoT",
            model=device.get("device_type", "Unknown"),
            sw_version=device.get("firmware_version", "1.0.0"),
        )
        
        # Set switch properties based on device type
        self._set_switch_properties()
        
        # Set initial state
        self._update_state()
        
    def _set_switch_properties(self):
        """Set switch properties based on device type and category."""
        device_type = self.device.get("device_type", "")
        device_category = self.device.get("category", "")
        
        # Default properties
        self._attr_device_class = None
        self._attr_icon = "mdi:power-switch"
        
        # Set properties based on device type and category
        if device_category == DEVICE_CATEGORY_LIGHT:
            self._attr_device_class = "light"
            self._attr_icon = "mdi:lightbulb"
            
        elif device_category == DEVICE_CATEGORY_DOOR:
            self._attr_device_class = "door"
            self._attr_icon = "mdi:door"
            
        elif device_category == DEVICE_CATEGORY_TOGGLE:
            self._attr_device_class = "toggle"
            self._attr_icon = "mdi:toggle-switch"
            
        elif "on_off" in device_type.lower() or "switch" in device_type.lower():
            self._attr_device_class = "switch"
            self._attr_icon = "mdi:power-socket-eu"
            
        # Set color mode for light switches
        if device_category == DEVICE_CATEGORY_LIGHT:
            self._attr_supported_color_modes = ["rgb", "white", "color_temp"]
            self._attr_color_mode = "rgb"
            self._attr_rgb_color = [255, 255, 255]  # Default white
            self._attr_brightness = 255  # Default full brightness
            self._attr_color_temp = 4000  # Default color temperature
            
    def _update_state(self):
        """Update switch state from device data."""
        status = self.device.get("status", DEVICE_STATUS_OFFLINE)
        
        if status == DEVICE_STATUS_CONNECTED:
            # Get switch state from device properties
            properties = self.device.get("properties", {})
            switch_state = properties.get("switch_state", False)
            self._attr_is_on = bool(switch_state)
        else:
            # Device is offline
            self._attr_is_on = False
            
        # Update available state - be more lenient for switches
        if status == DEVICE_STATUS_CONNECTED or self.device_id in self.device_manager.devices:
            self._attr_available = True
        else:
            self._attr_available = False
        
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        try:
            # Handle color mode for light switches
            if self.device.get("category") == DEVICE_CATEGORY_LIGHT:
                await self._turn_on_light(**kwargs)
            else:
                await self._turn_on_switch()
                
            # Update device state in device manager
            if self.device_id in self.device_manager.devices:
                self.device_manager.devices[self.device_id]["properties"]["switch_state"] = True
                self.device_manager.devices[self.device_id]["status"] = "connected"
                
            # Update local state
            self._attr_is_on = True
            self._just_controlled = True
            self.async_write_ha_state()
            
        except Exception as e:
            _LOGGER.error(f"Error turning on switch {self.device_id}: {e}")
            
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        try:
            # Send turn off command
            turn_off_message = {
                "command": "turn_off",
                "device_id": self.device_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            await self.device_manager.publish_mqtt(
                f"gemns/device/{self.device_id}/command",
                json.dumps(turn_off_message)
            )
            
            # Update device state in device manager
            if self.device_id in self.device_manager.devices:
                self.device_manager.devices[self.device_id]["properties"]["switch_state"] = False
                self.device_manager.devices[self.device_id]["status"] = "connected"
            
            # Update local state
            self._attr_is_on = False
            self._just_controlled = True
            self.async_write_ha_state()
            
        except Exception as e:
            _LOGGER.error(f"Error turning off switch {self.device_id}: {e}")
            
    async def _turn_on_switch(self):
        """Turn on a regular switch."""
        turn_on_message = {
            "command": "turn_on",
            "device_id": self.device_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        await self.device_manager.publish_mqtt(
            f"gems/device/{self.device_id}/command",
            json.dumps(turn_on_message)
        )
        
    async def _turn_on_light(self, **kwargs: Any):
        """Turn on a light switch with color options."""
        import json
        
        # Prepare turn on message
        turn_on_message = {
            "command": "turn_on",
            "device_id": self.device_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        # Add color mode if specified
        if "color_mode" in kwargs:
            turn_on_message["color_mode"] = kwargs["color_mode"]
            
        # Add RGB color if specified
        if "rgb_color" in kwargs:
            turn_on_message["rgb_color"] = kwargs["rgb_color"]
            self._attr_rgb_color = kwargs["rgb_color"]
            
        # Add brightness if specified
        if "brightness" in kwargs:
            turn_on_message["brightness"] = kwargs["brightness"]
            self._attr_brightness = kwargs["brightness"]
            
        # Add color temperature if specified
        if "color_temp" in kwargs:
            turn_on_message["color_temp"] = kwargs["color_temp"]
            self._attr_color_temp = kwargs["color_temp"]
            
        # Send command
        await self.device_manager.publish_mqtt(
            f"gems/device/{self.device_id}/command",
            json.dumps(turn_on_message)
        )
        
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return entity specific state attributes."""
        attributes = {
            "device_id": self.device_id,
            "device_type": self.device.get("device_type"),
            "status": self.device.get("status"),
            "last_seen": self.device.get("last_seen"),
            "ble_discovery_mode": self.device.get("ble_discovery_mode"),
            "pairing_status": self.device.get("pairing_status"),
            "firmware_version": self.device.get("firmware_version"),
            "created_manually": self.device.get("created_manually", False),
        }
        
        # Add light-specific attributes
        if self.device.get("category") == DEVICE_CATEGORY_LIGHT:
            attributes.update({
                "color_mode": self._attr_color_mode,
                "rgb_color": self._attr_rgb_color,
                "brightness": self._attr_brightness,
                "color_temp": self._attr_color_temp,
                "supported_color_modes": self._attr_supported_color_modes,
            })
            
        return attributes
        
    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        # Subscribe to device updates
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_DEVICE_UPDATED, self._handle_device_update
            )
        )
        
    def _handle_device_update(self, data):
        """Handle device updates."""
        # Check if this update is for our device
        if isinstance(data, dict) and data.get("device_id") == self.device_id:
            # Preserve current switch state if it exists
            current_state = self._attr_is_on
            self.device = data
            self._update_state()
            
            # If we just turned the switch on/off, preserve that state
            if hasattr(self, '_just_controlled') and self._just_controlled:
                self._attr_is_on = current_state
                self._just_controlled = False
                
            # Schedule the state write in the main event loop
            self.hass.loop.call_soon_threadsafe(
                lambda: self.hass.async_create_task(self._async_write_state())
            )
            
    async def _async_write_state(self):
        """Async helper to write state."""
        self.async_write_ha_state()
        
    async def async_update(self) -> None:
        """Update switch state."""
        # Get latest device data
        updated_device = self.device_manager.get_device(self.device_id)
        if updated_device:
            self.device = updated_device
            self._update_state()
            
    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return self._attr_is_on
        
    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._attr_available
