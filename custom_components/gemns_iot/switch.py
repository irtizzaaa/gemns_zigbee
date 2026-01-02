"""Switch platform for Gemns™ IoT integration."""

from datetime import UTC, datetime
import json
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEVICE_CATEGORY_DOOR,
    DEVICE_CATEGORY_LIGHT,
    DEVICE_CATEGORY_SWITCH,
    DEVICE_CATEGORY_TOGGLE,
    DEVICE_STATUS_CONNECTED,
    DEVICE_STATUS_OFFLINE,
    DEVICE_TYPE_ZIGBEE,
    DOMAIN,
    SIGNAL_DEVICE_ADDED,
    SIGNAL_DEVICE_UPDATED,
    ZIGBEE_DEVICE_BULB,
    ZIGBEE_DEVICE_SWITCH,
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
    """Set up Gemns™ IoT switches from a config entry."""
    global _add_entities_callback
    _add_entities_callback = async_add_entities

    # Get device manager
    device_manager = hass.data[DOMAIN][config_entry.entry_id].get("device_manager")
    if not device_manager:
        return

    # Get all switch devices
    # For Zigbee devices, only include actual switches (not bulbs - bulbs are handled by light platform)
    switch_devices = []
    switch_devices.extend(device_manager.get_devices_by_category(DEVICE_CATEGORY_SWITCH))
    # Only add lights for non-Zigbee devices (Zigbee bulbs should only be light entities)
    all_light_devices = device_manager.get_devices_by_category(DEVICE_CATEGORY_LIGHT)
    for light_device in all_light_devices:
        if light_device.get("device_type") != DEVICE_TYPE_ZIGBEE:
            switch_devices.append(light_device)
    switch_devices.extend(device_manager.get_devices_by_category(DEVICE_CATEGORY_DOOR))
    switch_devices.extend(device_manager.get_devices_by_category(DEVICE_CATEGORY_TOGGLE))

    # Create switch entities, avoiding duplicates
    entities = []
    seen_device_ids = set()
    for device in switch_devices:
        device_id = device.get("device_id")
        unique_id = f"{DOMAIN}_{device_id}"
        
        # Skip if we've already seen this device_id or unique_id
        if device_id in seen_device_ids:
            _LOGGER.debug("Skipping duplicate switch device during setup: %s", device_id)
            continue
        
        # Check if entity already exists in _entities
        existing_entity = next(
            (e for e in _entities if e.device_id == device_id or e.unique_id == unique_id), 
            None
        )
        if existing_entity:
            _LOGGER.debug("Switch entity already exists for device: %s, skipping", device_id)
            continue
        
        switch_entity = GemnsSwitch(device_manager, device, hass)
        entities.append(switch_entity)
        _entities.append(switch_entity)
        seen_device_ids.add(device_id)

    if entities:
        async_add_entities(entities)

    # Listen for new devices
    async def handle_new_device(device_data):
        """Handle new device added."""
        _LOGGER.info("handle_new_device called for device: %s, category: %s", device_data.get("device_id"), device_data.get("category"))
        category = device_data.get("category")
        device_type = device_data.get("device_type")
        # Don't create switch entities for Zigbee bulbs (they should only be light entities)
        if category == DEVICE_CATEGORY_LIGHT and device_type == DEVICE_TYPE_ZIGBEE:
            _LOGGER.debug("Skipping Zigbee bulb %s - should only be created as light entity", device_data.get("device_id"))
            return
        if category in [DEVICE_CATEGORY_SWITCH, DEVICE_CATEGORY_LIGHT, DEVICE_CATEGORY_DOOR, DEVICE_CATEGORY_TOGGLE]:
            device_id = device_data.get("device_id")
            unique_id = f"{DOMAIN}_{device_id}"
            
            # Check if entity exists in entity registry
            entity_registry = er.async_get(hass)
            existing_entry = entity_registry.async_get(unique_id)
            
            # Also check our local list
            existing_entity = next((e for e in _entities if e.device_id == device_id), None)

            if not existing_entry and not existing_entity:
                if _add_entities_callback:
                    new_entity = GemnsSwitch(device_manager, device_data, hass)
                    _entities.append(new_entity)
                    _add_entities_callback([new_entity])
                    _LOGGER.info("Created new switch entity for device: %s", device_id)
                else:
                    _LOGGER.error("Cannot create switch entity: _add_entities_callback is None")
            else:
                _LOGGER.debug("Switch entity already exists for device: %s (registry: %s, local: %s), skipping duplicate", 
                             device_id, existing_entry is not None, existing_entity is not None)

    # Connect to dispatcher
    async_dispatcher_connect(hass, SIGNAL_DEVICE_ADDED, handle_new_device)


class GemnsSwitch(SwitchEntity):
    """Representation of a Gemns™ IoT switch."""

    def __init__(self, device_manager, device: dict[str, Any], hass=None):
        """Initialize the switch."""
        self.device_manager = device_manager
        self.device = device
        self.device_id = device.get("device_id")
        self.hass = hass
        self._attr_name = device.get("name", self.device_id)
        self._attr_unique_id = f"{DOMAIN}_{self.device_id}"
        self._attr_should_poll = False
        self._attr_brightness = None  # Initialize brightness attribute

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

        self._attr_device_class = None
        self._attr_icon = "mdi:power-switch"

        if device_type == DEVICE_TYPE_ZIGBEE and device_category == DEVICE_CATEGORY_SWITCH:
            self._attr_assumed_state = False
            self._attr_icon = "mdi:gesture-tap-button"
            # Make Zigbee switches read-only
            self._attr_entity_registry_enabled_default = True
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
            self._attr_color_temp = 4000

    def _update_state(self):
        """Update switch state from device data."""
        status = self.device.get("status", DEVICE_STATUS_OFFLINE)
        properties = self.device.get("properties", {})
        cmd_type = properties.get("cmd_type")
        
        is_zigbee_switch = (
            self.device.get("device_type") == DEVICE_TYPE_ZIGBEE and 
            self.device.get("category") == DEVICE_CATEGORY_SWITCH
        )
        
        if is_zigbee_switch and cmd_type == 3:
            self._attr_is_on = (status == DEVICE_STATUS_CONNECTED)
        elif status == DEVICE_STATUS_CONNECTED:
            switch_state = properties.get("switch_state", False)
            self._attr_is_on = bool(switch_state)
        else:
            self._attr_is_on = False
        
        brightness = properties.get("brightness")
        if brightness is not None:
            self._attr_brightness = brightness
        elif not hasattr(self, '_attr_brightness'):
            self._attr_brightness = None

        if status == DEVICE_STATUS_CONNECTED or self.device_id in self.device_manager.devices:
            self._attr_available = True
        else:
            self._attr_available = False
    
    @property
    def state(self) -> str:
        """Return the state of the switch."""
        if not self.available:
            return "unavailable"
        
        properties = self.device.get("properties", {})
        cmd_type = properties.get("cmd_type")
        is_zigbee_switch = (
            self.device.get("device_type") == DEVICE_TYPE_ZIGBEE and 
            self.device.get("category") == DEVICE_CATEGORY_SWITCH
        )
        
        if is_zigbee_switch and cmd_type == 3:
            status = self.device.get("status", DEVICE_STATUS_OFFLINE)
            return "activated" if status == DEVICE_STATUS_CONNECTED else "off"
        elif is_zigbee_switch and self._attr_is_on:
            return "activated"
        elif self._attr_is_on:
            return "on"
        else:
            return "off"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        try:
            is_zigbee = self.device.get("device_type") == DEVICE_TYPE_ZIGBEE
            device_category = self.device.get("category")
            
            if is_zigbee and device_category == DEVICE_CATEGORY_SWITCH:
                _LOGGER.warning(
                    "Zigbee switch %s is read-only (status only). Control request ignored.",
                    self.device_id,
                )
                return
            
            if is_zigbee:
                zigbee_coordinator = None
                for entry_id, data in self.hass.data.get(DOMAIN, {}).items():
                    if isinstance(data, dict) and "zigbee_coordinator" in data:
                        zigbee_coordinator = data.get("zigbee_coordinator")
                        break
                
                if zigbee_coordinator:
                    zigbee_id = self.device.get("zigbee_id")
                    if zigbee_id is None:
                        _LOGGER.error("Zigbee device missing zigbee_id: %s", self.device_id)
                        return
                    
                    # Use correct device type based on category
                    device_type = ZIGBEE_DEVICE_BULB if device_category == DEVICE_CATEGORY_LIGHT else ZIGBEE_DEVICE_SWITCH
                    brightness = kwargs.get("brightness") if device_category == DEVICE_CATEGORY_LIGHT else None
                    await zigbee_coordinator.send_control_command(
                        zigbee_id, device_type, True, brightness
                    )
                else:
                    _LOGGER.error("Zigbee coordinator not available")
                    return
            else:
                if self.device.get("category") == DEVICE_CATEGORY_LIGHT:
                    await self._turn_on_light(**kwargs)
                else:
                    await self._turn_on_switch()

            if self.device_id in self.device_manager.devices:
                self.device_manager.devices[self.device_id]["properties"]["switch_state"] = True
                self.device_manager.devices[self.device_id]["status"] = "connected"

            self._attr_is_on = True
            self._just_controlled = True
            self.async_write_ha_state()

        except (ValueError, KeyError, AttributeError, TypeError) as e:
            _LOGGER.error("Error turning on switch %s: %s", self.device_id, e)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        try:
            is_zigbee = self.device.get("device_type") == DEVICE_TYPE_ZIGBEE
            device_category = self.device.get("category")
            
            if is_zigbee and device_category == DEVICE_CATEGORY_SWITCH:
                _LOGGER.warning(
                    "Zigbee switch %s is read-only (status only). Control request ignored.",
                    self.device_id,
                )
                return
            if not self.hass:
                return
            
            if is_zigbee:
                zigbee_coordinator = None
                for entry_id, data in self.hass.data.get(DOMAIN, {}).items():
                    if isinstance(data, dict) and "zigbee_coordinator" in data:
                        zigbee_coordinator = data.get("zigbee_coordinator")
                        break
                
                if zigbee_coordinator:
                    zigbee_id = self.device.get("zigbee_id")
                    if zigbee_id is None:
                        _LOGGER.error("Zigbee device missing zigbee_id: %s", self.device_id)
                        return
                    
                    # Use correct device type based on category
                    device_type = ZIGBEE_DEVICE_BULB if device_category == DEVICE_CATEGORY_LIGHT else ZIGBEE_DEVICE_SWITCH
                    await zigbee_coordinator.send_control_command(
                        zigbee_id, device_type, False, None
                    )
                else:
                    _LOGGER.error("Zigbee coordinator not available")
                    return
            else:
                turn_off_message = {
                    "command": "turn_off",
                    "device_id": self.device_id,
                    "timestamp": datetime.now(UTC).isoformat()
                }

                await self.device_manager.publish_mqtt(
                    f"gemns/device/{self.device_id}/command",
                    json.dumps(turn_off_message)
                )

            if self.device_id in self.device_manager.devices:
                self.device_manager.devices[self.device_id]["properties"]["switch_state"] = False
                self.device_manager.devices[self.device_id]["status"] = "connected"

            self._attr_is_on = False
            self._just_controlled = True
            self.async_write_ha_state()

        except (ValueError, KeyError, AttributeError, TypeError) as e:
            _LOGGER.error("Error turning off switch %s: %s", self.device_id, e)

    async def _turn_on_switch(self):
        """Turn on a regular switch."""
        turn_on_message = {
            "command": "turn_on",
            "device_id": self.device_id,
            "timestamp": datetime.now(UTC).isoformat()
        }

        await self.device_manager.publish_mqtt(
            f"gems/device/{self.device_id}/command",
            json.dumps(turn_on_message)
        )

    async def _turn_on_light(self, **kwargs: Any):
        """Turn on a light switch with color options."""

        # Prepare turn on message
        turn_on_message = {
            "command": "turn_on",
            "device_id": self.device_id,
            "timestamp": datetime.now(UTC).isoformat()
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
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        is_zigbee = self.device.get("device_type") == DEVICE_TYPE_ZIGBEE
        device_category = self.device.get("category")
        is_read_only = is_zigbee and device_category == DEVICE_CATEGORY_SWITCH
        
        attributes = {
            "device_id": self.device_id,
            "device_type": self.device.get("device_type"),
            "status": self.device.get("status"),
            "last_seen": self.device.get("last_seen"),
            "ble_discovery_mode": self.device.get("ble_discovery_mode"),
            "pairing_status": self.device.get("pairing_status"),
            "firmware_version": self.device.get("firmware_version"),
            "created_manually": self.device.get("created_manually", False),
            "read_only": is_read_only,
        }

        if is_zigbee and device_category == DEVICE_CATEGORY_SWITCH:
            properties = self.device.get("properties", {})
            supports_brightness = properties.get("supports_brightness", False)
            cmd_type = properties.get("cmd_type")
            if cmd_type is not None:
                attributes["cmd_type"] = cmd_type
            if supports_brightness:
                brightness = getattr(self, '_attr_brightness', None)
                if brightness is None:
                    brightness = properties.get("brightness")
                if brightness is not None:
                    attributes["brightness"] = brightness
                    attributes["brightness_pct"] = int((brightness / 255) * 100)
                else:
                    attributes["brightness"] = 0
                    attributes["brightness_pct"] = 0
                attributes["supports_brightness"] = True

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
        if isinstance(data, dict) and data.get("device_id") == self.device_id:
            current_state = self._attr_is_on
            self.device = data
            self._update_state()
            
            is_zigbee_switch = (
                self.device.get("device_type") == DEVICE_TYPE_ZIGBEE and 
                self.device.get("category") == DEVICE_CATEGORY_SWITCH
            )
            
            if hasattr(self, '_just_controlled') and self._just_controlled and not is_zigbee_switch:
                self._attr_is_on = current_state
                self._just_controlled = False

            self.hass.loop.call_soon_threadsafe(
                lambda: self.hass.async_create_task(self._async_write_state())
            )

    async def _async_write_state(self):
        """Write state to Home Assistant."""
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Update switch state."""
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
