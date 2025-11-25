"""BLE switch platform for Gemns™ IoT integration."""

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble_coordinator import GemnsBluetoothProcessorCoordinator
from .const import CONF_ADDRESS, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gemns™ IoT BLE switches from a config entry."""
    _LOGGER.info("Setting up BLE switch for entry %s", config_entry.entry_id)
    address = config_entry.unique_id
    if not address:
        _LOGGER.error("No address found in config entry")
        return

    coordinator = config_entry.runtime_data
    if not coordinator:
        _LOGGER.warning("No coordinator in runtime_data, trying hass.data for entry %s", config_entry.entry_id)
        try:
            coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
        except KeyError:
            _LOGGER.error("No coordinator found in runtime_data or hass.data for entry %s", config_entry.entry_id)
            return

    _LOGGER.info("BLE coordinator found for entry %s, creating switch entities", config_entry.entry_id)

    entities = []
    device_type = config_entry.data.get("device_type", "unknown")
    device_type_str = config_entry.data.get("device_name", "unknown")
    
    should_create_switch = (
        device_type in [1, 3, 6, 7, 8, 9] or
        device_type_str in ["on_off_switch", "two_way_switch", "light_switch", "door_switch", "toggle_switch"] or
        "switch" in device_type_str.lower()
    )
    
    if should_create_switch:
        switch_entity = GemnsBLESwitch(coordinator, config_entry)
        entities.append(switch_entity)
        _LOGGER.info("Created switch entity for device type: %s (type: %s)", device_type_str, device_type)
    else:
        _LOGGER.info("Skipping switch entity creation for device type: %s (type: %s)", device_type_str, device_type)

    if entities:
        async_add_entities(entities)


class GemnsBLESwitch(SwitchEntity):
    """Representation of a Gemns™ IoT BLE switch."""

    def __init__(
        self,
        coordinator: GemnsBluetoothProcessorCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the BLE switch."""
        self.coordinator = coordinator
        self.config_entry = config_entry

        self._attr_name = config_entry.data.get("name", "Gemns™ IoT Device")
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_switch"
        self._attr_should_poll = False

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=self._attr_name,
            manufacturer="Gemns™ IoT",
            model="BLE Switch",
            sw_version=self.coordinator.data.get("firmware_version", "1.0.0"),
        )

        self._attr_is_on = None
        self._attr_available = False
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

        if self.coordinator.data:
            attrs.update({
                "rssi": self.coordinator.data.get("rssi"),
                "signal_strength": self.coordinator.data.get("signal_strength"),
                "battery_level": self.coordinator.data.get("battery_level"),
                "last_seen": self.coordinator.data.get("timestamp"),
                "ble_active": True,
                "ble_connected": self.coordinator.available,
                "ble_status": "active" if self.coordinator.available else "inactive",
                "last_update_success": getattr(self.coordinator, 'last_update_success', True),
            })

            if "sensor_data" in self.coordinator.data:
                sensor_data = self.coordinator.data["sensor_data"]
                if "switch_on" in sensor_data:
                    attrs["switch_on"] = sensor_data["switch_on"]
                if "event_counter" in sensor_data:
                    attrs["event_counter"] = sensor_data["event_counter"]
                if "sensor_event" in sensor_data:
                    attrs["sensor_event"] = sensor_data["sensor_event"]

        return attrs

    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        await super().async_added_to_hass()
        self._unsub_coordinator = self.coordinator.async_add_listener(self._handle_coordinator_update)
        self.async_on_remove(self._unsub_coordinator)

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        try:
            previous_state = self._attr_is_on
            self._update_from_coordinator()

            if previous_state != self._attr_is_on:
                _LOGGER.info("SWITCH STATE CHANGED: %s | Previous: %s | New: %s",
                           self.address, previous_state, self._attr_is_on)

            self.async_write_ha_state()
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            _LOGGER.error("Error handling coordinator update for %s: %s", self.address, e)

    def _update_from_coordinator(self) -> None:
        """Update switch state from coordinator data."""
        if not self.coordinator.data:
            self._attr_available = False
            _LOGGER.debug("BLE switch %s: No coordinator data", self.address)
            return

        data = self.coordinator.data
        _LOGGER.info("UPDATING SWITCH: %s | Coordinator data: %s", self.address, data)

        self._device_type = data.get("device_type", "unknown")
        _LOGGER.info("DEVICE TYPE: %s | Type: %s", self.address, self._device_type)

        self._set_switch_properties()
        self._update_device_info()
        self._extract_switch_value(data)
        self._attr_available = True
        _LOGGER.info("SWITCH UPDATED: %s | Available: %s | Value: %s | BLE_active: %s | Coordinator_available: %s",
                     self.address, self._attr_available, self._attr_is_on, True, self.coordinator.available)

    def _set_switch_properties(self) -> None:
        """Set switch properties based on device type."""
        device_type = self._device_type.lower()

        if "light" in device_type:
            self._attr_name = f"Gemns™ IoT Light Switch {self._get_professional_device_id()}"
            self._attr_icon = "mdi:lightbulb"

        elif "door" in device_type:
            self._attr_name = f"Gemns™ IoT Door Switch {self._get_professional_device_id()}"
            self._attr_icon = "mdi:door"

        elif "toggle" in device_type:
            self._attr_name = f"Gemns™ IoT Toggle Switch {self._get_professional_device_id()}"
            self._attr_icon = "mdi:toggle-switch"

        elif "two_way" in device_type or "two-way" in device_type:
            self._attr_name = f"Gemns™ IoT Two-Way Switch {self._get_professional_device_id()}"
            self._attr_icon = "mdi:toggle-switch"

        elif "switch" in device_type:
            self._attr_name = f"Gemns™ IoT On/Off Switch {self._get_professional_device_id()}"
            self._attr_icon = "mdi:power"

        else:
            self._attr_name = f"Gemns™ IoT Switch {self._get_professional_device_id()}"
            self._attr_icon = "mdi:power"

    def _update_device_info(self) -> None:
        """Update device info with proper name and model."""
        device_type = self._device_type.lower()

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

        model = model_map.get(device_type, "IoT Switch")
        device_image = self._get_device_image(device_type)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.address)},
            name=self._attr_name,
            manufacturer="Gemns™ IoT",
            model=model,
            sw_version=self.coordinator.data.get("firmware_version", "1.0.0"),
        )

        if device_image:
            self._attr_device_info["image"] = device_image

    def _get_professional_device_id(self) -> str:
        """Generate a professional device identifier from MAC address."""
        clean_address = self.address.replace(":", "").upper()
        last_6 = clean_address[-6:]
        device_number = int(last_6, 16) % 1000
        return f"Unit-{device_number:03d}"

    def _get_device_image(self, device_type: str) -> str:
        """Get device image URL based on device type."""
        return "https://brands.home-assistant.io/gemns/icon.png"

    def _extract_switch_value(self, data: dict[str, Any]) -> None:
        """Extract switch value from coordinator data."""
        _LOGGER.info("EXTRACTING SWITCH VALUE: %s | Data: %s", self.address, data)

        sensor_data = data.get("sensor_data", {})
        _LOGGER.info("SENSOR DATA: %s | Sensor data: %s", self.address, sensor_data)

        if "switch_on" in sensor_data:
            self._attr_is_on = bool(sensor_data["switch_on"])
            _LOGGER.info("SWITCH VALUE: %s | Switch on: %s | Value: %s",
                        self.address, sensor_data["switch_on"], self._attr_is_on)

        elif "switch_off" in sensor_data:
            self._attr_is_on = False
            _LOGGER.info("SWITCH VALUE: %s | Switch off: %s | Value: %s",
                        self.address, sensor_data["switch_off"], self._attr_is_on)

        elif "event_type" in sensor_data:
            event_type = sensor_data.get("event_type", 0)
            self._attr_is_on = (event_type == 2)
            _LOGGER.info("EVENT TYPE SWITCH: %s | Event type: %s | Value: %s",
                        self.address, event_type, self._attr_is_on)

        elif "sensor_event" in sensor_data:
            sensor_event = sensor_data.get("sensor_event", 0)
            self._attr_is_on = (sensor_event == 2)
            _LOGGER.info("SENSOR EVENT SWITCH: %s | Event: %s | Value: %s",
                        self.address, sensor_event, self._attr_is_on)

        else:
            self._attr_is_on = False
            _LOGGER.warning("NO SWITCH VALUE: %s | No switch data found", self.address)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        _LOGGER.info("TURNING ON SWITCH: %s", self.address)
        _LOGGER.warning("Switch control is read-only. State is determined by device sensor_event data.")
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        _LOGGER.info("TURNING OFF SWITCH: %s", self.address)
        _LOGGER.warning("Switch control is read-only. State is determined by device sensor_event data.")
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Update switch state."""
        await self.coordinator.async_request_refresh()
