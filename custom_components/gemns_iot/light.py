"""Light platform for Gemns™ IoT integration."""

from datetime import UTC, datetime
import json
import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEVICE_CATEGORY_LIGHT,
    DEVICE_STATUS_CONNECTED,
    DEVICE_STATUS_OFFLINE,
    DEVICE_TYPE_ZIGBEE,
    DOMAIN,
    SIGNAL_DEVICE_ADDED,
    SIGNAL_DEVICE_UPDATED,
    ZIGBEE_DEVICE_BULB,
)

_LOGGER = logging.getLogger(__name__)

# Global variable to track entities and add callback
_entities: list = []
_add_entities_callback: AddEntitiesCallback | None = None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gemns™ IoT lights from a config entry."""
    global _add_entities_callback
    _add_entities_callback = async_add_entities

    # Get device manager
    device_manager = hass.data[DOMAIN][config_entry.entry_id].get("device_manager")
    if not device_manager:
        return

    # Get all light devices
    light_devices = device_manager.get_devices_by_category(DEVICE_CATEGORY_LIGHT)

    # Create light entities, avoiding duplicates
    entities = []
    seen_device_ids = set()
    for device in light_devices:
        device_id = device.get("device_id")
        unique_id = f"{DOMAIN}_{device_id}"
        
        # Skip if we've already seen this device_id or unique_id
        if device_id in seen_device_ids:
            _LOGGER.debug("Skipping duplicate light device during setup: %s", device_id)
            continue
        
        # Check if entity already exists in _entities
        existing_entity = next(
            (e for e in _entities if e.device_id == device_id or e.unique_id == unique_id), 
            None
        )
        if existing_entity:
            _LOGGER.debug("Light entity already exists for device: %s, skipping", device_id)
            continue
        
        light_entity = GemnsLight(device_manager, device, hass)
        entities.append(light_entity)
        _entities.append(light_entity)
        seen_device_ids.add(device_id)

    if entities:
        async_add_entities(entities)

    # Listen for new devices
    async def handle_new_device(device_data):
        """Handle new device added."""
        _LOGGER.info("handle_new_device called for device: %s, category: %s", device_data.get("device_id"), device_data.get("category"))
        if device_data.get("category") == DEVICE_CATEGORY_LIGHT:
            device_id = device_data.get("device_id")
            unique_id = f"{DOMAIN}_{device_id}"
            
            # Check if entity exists in entity registry
            entity_registry = er.async_get(hass)
            existing_entry = entity_registry.async_get(unique_id)
            
            # Also check our local list
            existing_entity = next(
                (e for e in _entities if e.device_id == device_id or e.unique_id == unique_id), 
                None
            )

            if not existing_entry and not existing_entity:
                if _add_entities_callback:
                    new_entity = GemnsLight(device_manager, device_data, hass)
                    _entities.append(new_entity)
                    _add_entities_callback([new_entity])
                    _LOGGER.info("Created new light entity for device: %s", device_id)
                else:
                    _LOGGER.error("Cannot create light entity: _add_entities_callback is None")
            else:
                _LOGGER.debug("Light entity already exists for device: %s (registry: %s, local: %s), skipping duplicate", 
                             device_id, existing_entry is not None, existing_entity is not None)

    # Connect to dispatcher
    async_dispatcher_connect(hass, SIGNAL_DEVICE_ADDED, handle_new_device)


class GemnsLight(LightEntity):
    """Representation of a Gemns™ IoT light."""

    def __init__(self, device_manager, device: dict[str, Any], hass=None):
        """Initialize the light."""
        self.device_manager = device_manager
        self.device = device
        self.device_id = device.get("device_id")
        self.hass = hass
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
        # For Zigbee bulbs, check if brightness is supported
        if self.device.get("device_type") == DEVICE_TYPE_ZIGBEE:
            properties = self.device.get("properties", {})
            supports_brightness = properties.get("supports_brightness", True)  # Default to True for backward compatibility
            
            if supports_brightness:
                self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
                self._attr_color_mode = ColorMode.BRIGHTNESS
                self._attr_brightness = 255
            else:
                # Simple on/off bulb (length == 3, no brightness)
                self._attr_supported_color_modes = {ColorMode.ONOFF}
                self._attr_color_mode = ColorMode.ONOFF
                self._attr_brightness = None
        else:
            # Default light properties for non‑Zigbee lights
            self._attr_supported_color_modes = {
                ColorMode.RGB,
                ColorMode.COLOR_TEMP,
                ColorMode.WHITE,
            }
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
            properties = self.device.get("properties", {})
            light_state = properties.get("light_state", False)
            self._attr_is_on = bool(light_state)

            brightness = properties.get("brightness")
            if brightness is not None:
                self._attr_brightness = brightness

            rgb_color = properties.get("rgb_color")
            if rgb_color:
                self._attr_rgb_color = tuple(rgb_color)

            color_temp = properties.get("color_temp")
            if color_temp is not None:
                self._attr_color_temp = color_temp

        else:
            self._attr_is_on = False

        if status == DEVICE_STATUS_CONNECTED or self.device_id in self.device_manager.devices:
            self._attr_available = True
        else:
            self._attr_available = False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        try:
            # Check if this is a Zigbee device
            is_zigbee = self.device.get("device_type") == DEVICE_TYPE_ZIGBEE
            
            if is_zigbee:
                # Get Zigbee coordinator from hass data
                config_entry_id = None
                zigbee_coordinator = None
                
                # Find the config entry for this device
                for entry_id, data in self.hass.data.get(DOMAIN, {}).items():
                    if isinstance(data, dict) and "zigbee_coordinator" in data:
                        zigbee_coordinator = data.get("zigbee_coordinator")
                        break
                
                if zigbee_coordinator:
                    # Get Zigbee device ID
                    zigbee_id = self.device.get("zigbee_id")
                    if zigbee_id is None:
                        _LOGGER.error("Zigbee device missing zigbee_id: %s", self.device_id)
                        return
                    
                    # Check if device supports brightness
                    properties = self.device.get("properties", {})
                    supports_brightness = properties.get("supports_brightness", True)
                    
                    if supports_brightness:
                        if ATTR_BRIGHTNESS in kwargs:
                            brightness = kwargs[ATTR_BRIGHTNESS]
                            self._attr_brightness = brightness
                        else:
                            brightness = self._attr_brightness if self._attr_brightness is not None else 255
                        brightness = max(0, min(255, int(brightness)))
                    else:
                        brightness = None
                    
                    await zigbee_coordinator.send_control_command(
                        zigbee_id, ZIGBEE_DEVICE_BULB, True, brightness
                    )
                else:
                    _LOGGER.error("Zigbee coordinator not available")
                    return
            
            # Prepare turn on message (for MQTT or logging)
            turn_on_message = {
                "command": "turn_on",
                "device_id": self.device_id,
                "timestamp": datetime.now(UTC).isoformat()
            }

            # Handle brightness
            if ATTR_BRIGHTNESS in kwargs:
                brightness = kwargs[ATTR_BRIGHTNESS]
                turn_on_message["brightness"] = brightness
                self._attr_brightness = brightness

            # Handle RGB color for non‑Zigbee lights only
            if not is_zigbee and ATTR_RGB_COLOR in kwargs:
                rgb_color = kwargs[ATTR_RGB_COLOR]
                turn_on_message["rgb_color"] = list(rgb_color)
                self._attr_rgb_color = rgb_color
                self._attr_color_mode = ColorMode.RGB

            # Handle color temperature for non‑Zigbee lights only
            if not is_zigbee and ATTR_COLOR_TEMP_KELVIN in kwargs:
                color_temp_kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
                # Convert Kelvin to mireds for Home Assistant
                color_temp_mireds = 1000000 // color_temp_kelvin
                turn_on_message["color_temp"] = color_temp_mireds
                self._attr_color_temp = color_temp_mireds
                self._attr_color_mode = ColorMode.COLOR_TEMP
            elif not is_zigbee and "color_temp" in kwargs:  # Fallback for backward compatibility
                color_temp = kwargs["color_temp"]
                turn_on_message["color_temp"] = color_temp
                self._attr_color_temp = color_temp
                self._attr_color_mode = ColorMode.COLOR_TEMP

            # Handle transition
            if ATTR_TRANSITION in kwargs:
                transition = kwargs[ATTR_TRANSITION]
                turn_on_message["transition"] = transition

            # Send command via MQTT if not Zigbee
            if not is_zigbee:
                await self.device_manager.publish_mqtt(
                    f"gemns/device/{self.device_id}/command",
                    json.dumps(turn_on_message)
                )

            # Log the command for debugging
            _LOGGER.info("Light command sent: %s", turn_on_message)

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

        except (ValueError, KeyError, AttributeError, TypeError) as e:
            _LOGGER.error("Error turning on light %s: %s", self.device_id, e)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        try:
            # Check if this is a Zigbee device
            is_zigbee = self.device.get("device_type") == DEVICE_TYPE_ZIGBEE
            
            if is_zigbee:
                # Get Zigbee coordinator from hass data
                zigbee_coordinator = None
                for entry_id, data in self.hass.data.get(DOMAIN, {}).items():
                    if isinstance(data, dict) and "zigbee_coordinator" in data:
                        zigbee_coordinator = data.get("zigbee_coordinator")
                        break
                
                if zigbee_coordinator:
                    # Get Zigbee device ID
                    zigbee_id = self.device.get("zigbee_id")
                    if zigbee_id is None:
                        _LOGGER.error("Zigbee device missing zigbee_id: %s", self.device_id)
                        return
                    
                    # Send Zigbee serial command
                    await zigbee_coordinator.send_control_command(
                        zigbee_id, ZIGBEE_DEVICE_BULB, False
                    )
                else:
                    _LOGGER.error("Zigbee coordinator not available")
                    return
            
            # Send turn off command (for MQTT or logging)
            turn_off_message = {
                "command": "turn_off",
                "device_id": self.device_id,
                "timestamp": datetime.now(UTC).isoformat()
            }

            # Handle transition
            if ATTR_TRANSITION in kwargs:
                transition = kwargs[ATTR_TRANSITION]
                turn_off_message["transition"] = transition

            # Send via MQTT if not Zigbee
            if not is_zigbee:
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

        except (ValueError, KeyError, AttributeError, TypeError) as e:
            _LOGGER.error("Error turning off light %s: %s", self.device_id, e)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
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
            
            self._set_light_properties()
            self._update_state()

            if hasattr(self, '_just_controlled') and self._just_controlled:
                self._attr_is_on = current_state
                self._attr_brightness = current_brightness
                self._attr_rgb_color = current_color
                self._just_controlled = False

            self.hass.loop.call_soon_threadsafe(
                lambda: self.hass.async_create_task(self._async_write_state())
            )

    async def _async_write_state(self):
        """Write state to Home Assistant."""
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
    def brightness(self) -> int | None:
        """Return the brightness of this light between 0..255."""
        return self._attr_brightness

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the rgb color value [int, int, int]."""
        return self._attr_rgb_color

    @property
    def color_temp(self) -> int | None:
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
