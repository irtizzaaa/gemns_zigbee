"""Light platform for Gemns™ IoT integration."""

import logging
from typing import Any, Dict, Optional, Tuple
from datetime import datetime, timezone
import json

from homeassistant.components.light import (
    LightEntity,
    ColorMode,
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_TRANSITION,
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
    DEVICE_CATEGORY_LIGHT,
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
    """Set up Gemns™ IoT lights from a config entry."""
    global _entities, _add_entities_callback
    
    # Store the callback for dynamic entity creation
    _add_entities_callback = async_add_entities
    
    # Get device manager
    device_manager = hass.data[DOMAIN][config_entry.entry_id].get("device_manager")
    if not device_manager:
        return
        
    # Get all light devices
    light_devices = device_manager.get_devices_by_category(DEVICE_CATEGORY_LIGHT)
    
    # Create light entities
    entities = []
    for device in light_devices:
        light_entity = GemnsLight(device_manager, device)
        entities.append(light_entity)
        _entities.append(light_entity)
        
    if entities:
        async_add_entities(entities)
    
    # Listen for new devices
    async def handle_new_device(device_data):
        """Handle new device added."""
        if device_data.get("category") == DEVICE_CATEGORY_LIGHT:
            # Check if entity already exists
            device_id = device_data.get("device_id")
            existing_entity = next((e for e in _entities if e.device_id == device_id), None)
            
            if not existing_entity:
                # Create new entity
                new_entity = GemnsLight(device_manager, device_data)
                _entities.append(new_entity)
                _add_entities_callback([new_entity])
                _LOGGER.info(f"Created new light entity for device: {device_id}")
    
    # Connect to dispatcher
    async_dispatcher_connect(hass, SIGNAL_DEVICE_ADDED, handle_new_device)


class GemnsLight(LightEntity):
    """Representation of a Gemns™ IoT light."""

    def __init__(self, device_manager, device: Dict[str, Any]):
        """Initialize the light."""
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
        
        # Set light properties
        self._set_light_properties()
        
        # Set initial state
        self._update_state()
        
    def _set_light_properties(self):
        """Set light properties based on device capabilities."""
        # Default light properties
        self._attr_supported_color_modes = {ColorMode.RGB, ColorMode.COLOR_TEMP, ColorMode.WHITE}
        self._attr_color_mode = ColorMode.RGB
        self._attr_brightness = 255
        self._attr_rgb_color = (255, 255, 255)
        self._attr_color_temp_kelvin = 4000
        self._attr_min_color_temp_kelvin = 2000  # Warm white
        self._attr_max_color_temp_kelvin = 6500  # Cool white
        
    def _update_state(self):
        """Update light state from device data."""
        status = self.device.get("status", DEVICE_STATUS_OFFLINE)
        
        if status == DEVICE_STATUS_CONNECTED:
            # Get light state from device properties
            properties = self.device.get("properties", {})
            light_state = properties.get("light_state", False)
            self._attr_is_on = bool(light_state)
            
            # Get brightness if available
            brightness = properties.get("brightness")
            if brightness is not None:
                self._attr_brightness = brightness
                
            # Get RGB color if available
            rgb_color = properties.get("rgb_color")
            if rgb_color:
                self._attr_rgb_color = tuple(rgb_color)
                
            # Get color temperature if available
            color_temp = properties.get("color_temp")
            if color_temp is not None:
                self._attr_color_temp = color_temp
                
        else:
            # Device is offline
            self._attr_is_on = False
            
        # Update available state - be more lenient for lights
        if status == DEVICE_STATUS_CONNECTED or self.device_id in self.device_manager.devices:
            self._attr_available = True
        else:
            self._attr_available = False
        
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        try:
            # Prepare turn on message
            turn_on_message = {
                "command": "turn_on",
                "device_id": self.device_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Handle brightness
            if ATTR_BRIGHTNESS in kwargs:
                brightness = kwargs[ATTR_BRIGHTNESS]
                turn_on_message["brightness"] = brightness
                self._attr_brightness = brightness
                
            # Handle RGB color
            if ATTR_RGB_COLOR in kwargs:
                rgb_color = kwargs[ATTR_RGB_COLOR]
                turn_on_message["rgb_color"] = list(rgb_color)
                self._attr_rgb_color = rgb_color
                self._attr_color_mode = ColorMode.RGB
                
            # Handle color temperature
            if ATTR_COLOR_TEMP_KELVIN in kwargs:
                color_temp_kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
                # Convert Kelvin to mireds for Home Assistant
                color_temp_mireds = 1000000 // color_temp_kelvin
                turn_on_message["color_temp"] = color_temp_mireds
                self._attr_color_temp = color_temp_mireds
                self._attr_color_mode = ColorMode.COLOR_TEMP
            elif "color_temp" in kwargs:  # Fallback for backward compatibility
                color_temp = kwargs["color_temp"]
                turn_on_message["color_temp"] = color_temp
                self._attr_color_temp = color_temp
                self._attr_color_mode = ColorMode.COLOR_TEMP
                
            # Handle transition
            if ATTR_TRANSITION in kwargs:
                transition = kwargs[ATTR_TRANSITION]
                turn_on_message["transition"] = transition
                
            # Send command
            await self.device_manager.publish_mqtt(
                f"gemns/device/{self.device_id}/command",
                json.dumps(turn_on_message)
            )
            
            # Log the command for debugging
            _LOGGER.info(f"Light command sent: {turn_on_message}")
            
            # Update device state in device manager
            if self.device_id in self.device_manager.devices:
                self.device_manager.devices[self.device_id]["properties"]["light_state"] = True
                self.device_manager.devices[self.device_id]["status"] = "connected"
                if "brightness" in turn_on_message:
                    self.device_manager.devices[self.device_id]["properties"]["brightness"] = turn_on_message["brightness"]
                if "rgb_color" in turn_on_message:
                    self.device_manager.devices[self.device_id]["properties"]["rgb_color"] = turn_on_message["rgb_color"]
                if "color_temp" in turn_on_message:
                    self.device_manager.devices[self.device_id]["properties"]["color_temp"] = turn_on_message["color_temp"]
            
            # Update local state
            self._attr_is_on = True
            self._just_controlled = True
            self.async_write_ha_state()
            
        except Exception as e:
            _LOGGER.error(f"Error turning on light {self.device_id}: {e}")
            
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        try:
            # Send turn off command
            turn_off_message = {
                "command": "turn_off",
                "device_id": self.device_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Handle transition
            if ATTR_TRANSITION in kwargs:
                transition = kwargs[ATTR_TRANSITION]
                turn_off_message["transition"] = transition
                
            await self.device_manager.publish_mqtt(
                f"gemns/device/{self.device_id}/command",
                json.dumps(turn_off_message)
            )
            
            # Update device state in device manager
            if self.device_id in self.device_manager.devices:
                self.device_manager.devices[self.device_id]["properties"]["light_state"] = False
                self.device_manager.devices[self.device_id]["status"] = "connected"
            
            # Update local state
            self._attr_is_on = False
            self._just_controlled = True
            self.async_write_ha_state()
            
        except Exception as e:
            _LOGGER.error(f"Error turning off light {self.device_id}: {e}")
            
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return entity specific state attributes."""
        return {
            "device_id": self.device_id,
            "device_type": self.device.get("device_type"),
            "status": self.device.get("status"),
            "last_seen": self.device.get("last_seen"),
            "ble_discovery_mode": self.device.get("ble_discovery_mode"),
            "pairing_status": self.device.get("pairing_status"),
            "firmware_version": self.device.get("firmware_version"),
            "created_manually": self.device.get("created_manually", False),
            "color_mode": self._attr_color_mode,
            "rgb_color": self._attr_rgb_color,
            "brightness": self._attr_brightness,
            "color_temp": self._attr_color_temp,
            "min_mireds": self._attr_min_mireds,
            "max_mireds": self._attr_max_mireds,
        }
        
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
            # Preserve current light state if it exists
            current_state = self._attr_is_on
            current_brightness = self._attr_brightness
            current_color = self._attr_rgb_color
            self.device = data
            self._update_state()
            
            # If we just controlled the light, preserve that state
            if hasattr(self, '_just_controlled') and self._just_controlled:
                self._attr_is_on = current_state
                self._attr_brightness = current_brightness
                self._attr_rgb_color = current_color
                self._just_controlled = False
                
            # Schedule the state write in the main event loop
            self.hass.loop.call_soon_threadsafe(
                lambda: self.hass.async_create_task(self._async_write_state())
            )
            
    async def _async_write_state(self):
        """Async helper to write state."""
        self.async_write_ha_state()
        
    async def async_update(self) -> None:
        """Update light state."""
        # Get latest device data
        updated_device = self.device_manager.get_device(self.device_id)
        if updated_device:
            self.device = updated_device
            self._update_state()
            
    @property
    def is_on(self) -> bool:
        """Return true if the light is on."""
        return self._attr_is_on
        
    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._attr_available
        
    @property
    def brightness(self) -> Optional[int]:
        """Return the brightness of this light between 0..255."""
        return self._attr_brightness
        
    @property
    def rgb_color(self) -> Optional[Tuple[int, int, int]]:
        """Return the rgb color value [int, int, int]."""
        return self._attr_rgb_color
        
    @property
    def color_temp(self) -> Optional[int]:
        """Return the color temperature in mireds."""
        return self._attr_color_temp
        
    @property
    def color_mode(self) -> ColorMode:
        """Return the color mode of the light."""
        return self._attr_color_mode
        
    @property
    def supported_color_modes(self) -> set[ColorMode]:
        """Flag supported features."""
        return self._attr_supported_color_modes
        
    @property
    def min_mireds(self) -> int:
        """Return the coldest color_temp that this light supports."""
        return self._attr_min_mireds
        
    @property
    def max_mireds(self) -> int:
        """Return the warmest color_temp that this light supports."""
        return self._attr_max_mireds
