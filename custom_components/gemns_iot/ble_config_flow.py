"""Config flow for Gemns™ IoT BLE integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothServiceInfo,
    async_discovered_service_info,
    async_process_advertisements,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, FlowResult
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
import voluptuous as vol

from .const import DOMAIN, BLE_COMPANY_ID, CONF_DECRYPTION_KEY, CONF_DEVICE_NAME, CONF_DEVICE_TYPE

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Required(CONF_ADDRESS): str,
        vol.Required(CONF_DECRYPTION_KEY): str,
        vol.Optional(CONF_DEVICE_NAME): str,
        vol.Optional(CONF_DEVICE_TYPE, default=4): vol.In({
            "1": "Button",
            "2": "Vibration Monitor", 
            "3": "Two Way Switch",
            "4": "Leak Sensor"
        }),
    }
)

STEP_DISCOVERY_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DECRYPTION_KEY): str,
        vol.Optional(CONF_DEVICE_NAME): str,
        vol.Optional(CONF_DEVICE_TYPE, default=4): vol.In({
            "1": "Button",
            "2": "Vibration Monitor", 
            "3": "Two Way Switch",
            "4": "Leak Sensor"
        }),
    }
)


class GemnsBluetoothConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Gemns™ IoT BLE."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_devices: dict[str, BluetoothServiceInfo] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - manual device provisioning."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS].upper()
            name = user_input[CONF_NAME]
            decryption_key = user_input[CONF_DECRYPTION_KEY]
            device_name = user_input.get(CONF_DEVICE_NAME, name)
            device_type = int(user_input.get(CONF_DEVICE_TYPE, "4"))  # Convert string to int, default to leak sensor
            
            # Validate decryption key format
            try:
                bytes.fromhex(decryption_key)
                if len(decryption_key) != 32:  # 16 bytes = 32 hex chars
                    return self.async_show_form(
                        step_id="user",
                        data_schema=STEP_USER_DATA_SCHEMA,
                        errors={"base": "invalid_decryption_key_length"},
                    )
            except ValueError:
                return self.async_show_form(
                    step_id="user",
                    data_schema=STEP_USER_DATA_SCHEMA,
                    errors={"base": "invalid_decryption_key_format"},
                )
            
            # Check if already configured
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            
            # Create the config entry
            return self.async_create_entry(
                title=device_name,
                data={
                    CONF_NAME: name,
                    CONF_ADDRESS: address,
                    CONF_DECRYPTION_KEY: decryption_key,
                    CONF_DEVICE_NAME: device_name,
                    CONF_DEVICE_TYPE: device_type,
                },
            )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            description_placeholders={
                "message": "Manually provision a Gemns™ IoT device by entering its MAC address and decryption key.\n\nDevice Types:\n• Type 1: Button\n• Type 2: Vibration Monitor\n• Type 3: Two Way Switch\n• Type 4: Leak Sensor\n\nDecryption Key: 32-character hex string (16 bytes)",
                "integration_icon": "/local/custom_components/gemns/static/icon.png"
            }
        )

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfo
    ) -> FlowResult:
        """Handle the bluetooth discovery step - but we don't auto-configure."""
        # We don't auto-configure devices anymore, just show them as available
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        
        # Check if this looks like a Gemns™ IoT device
        if not self._is_gems_device(discovery_info):
            return self.async_abort(reason="not_supported")
        
        # Extract device type from beacon data for better discovery display
        device_type, device_name = self._extract_device_info_from_beacon(discovery_info)
        
        # Store the device info with extracted type
        self._discovered_devices[discovery_info.address] = {
            "discovery_info": discovery_info,
            "device_type": device_type,
            "device_name": device_name
        }
        
        return self.async_abort(reason="device_selection_required")

    async def async_step_device_selection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle device selection step."""
        if user_input is None:
            # Show discovered devices
            devices = []
            for address, device_info in self._discovered_devices.items():
                device_type = device_info.get("device_type", "unknown")
                device_name = device_info.get("device_name", "Gemns™ IoT Device")
                
                # Add device type icon
                icon_map = {
                    "leak_sensor": "mdi:water",
                    "vibration_sensor": "mdi:vibrate", 
                    "two_way_switch": "mdi:toggle-switch",
                    "button": "mdi:gesture-tap-button",
                    "legacy": "mdi:chip",
                    "unknown": "mdi:chip"
                }
                
                devices.append({
                    "value": address,
                    "label": f"{device_name} ({address})",
                    "icon": icon_map.get(device_type, "mdi:chip")
                })
            
            return self.async_show_form(
                step_id="device_selection",
                data_schema=vol.Schema({
                    vol.Required("device"): vol.In({
                        device["value"]: device["label"] 
                        for device in devices
                    })
                }),
                description_placeholders={
                    "message": "Select the Gemns™ IoT device you want to configure:"
                }
            )
        
        # Device selected, proceed to configuration
        selected_address = user_input["device"]
        device_info = self._discovered_devices[selected_address]
        
        # Store selected device info for next step
        self._selected_device = device_info
        
        return await self.async_step_user_config()

    async def async_step_user_config(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle user configuration step with device-specific info."""
        if user_input is None:
            device_info = self._selected_device
            device_type = device_info.get("device_type", "unknown")
            device_name = device_info.get("device_name", "Gemns™ IoT Device")
            
            # Create device-specific schema
            schema = {
                vol.Required(CONF_DECRYPTION_KEY): str,
            }
            
            # Add device-specific description
            descriptions = {
                "leak_sensor": "Configure your Gemns™ IoT Leak Sensor. This device detects water leaks and moisture.",
                "vibration_sensor": "Configure your Gemns™ IoT Vibration Monitor. This device detects vibrations and movement.",
                "two_way_switch": "Configure your Gemns™ IoT Two-Way Switch. This device can be turned on/off remotely.",
                "button": "Configure your Gemns™ IoT Button. This device sends signals when pressed.",
                "legacy": "Configure your Gemns™ IoT Legacy Device. This device provides basic IoT functionality.",
                "unknown": "Configure your Gemns™ IoT Device. This device provides IoT functionality."
            }
            
            return self.async_show_form(
                step_id="user_config",
                data_schema=vol.Schema(schema),
                description_placeholders={
                    "message": descriptions.get(device_type, descriptions["unknown"]),
                    "device_name": device_name,
                    "device_type": device_type.replace("_", " ").title(),
                    "integration_icon": "/local/custom_components/gemns/static/icon.png"
                }
            )
        
        # Configuration complete
        device_info = self._selected_device
        discovery_info = device_info["discovery_info"]
        device_type = device_info.get("device_type", "unknown")
        device_name = device_info.get("device_name", "Gemns™ IoT Device")
        
        # Map device type to sensor type
        device_type_map = {
            "legacy": 0,
            "button": 1,
            "vibration_sensor": 2,
            "two_way_switch": 3,
            "leak_sensor": 4,
            "unknown": 4  # Default to leak sensor
        }
        
        device_type = device_type_map.get(device_type, 4)
        
        # Create the config entry
        return self.async_create_entry(
            title=device_name,
            data={
                CONF_NAME: device_name,
                CONF_ADDRESS: discovery_info.address,
                CONF_DECRYPTION_KEY: user_input[CONF_DECRYPTION_KEY],
                CONF_DEVICE_NAME: device_name,
                CONF_DEVICE_TYPE: device_type,
            },
        )

    def _is_gems_device(self, discovery_info: BluetoothServiceInfo) -> bool:
        """Check if this is a Gemns™ IoT device using new packet format."""
        # Check manufacturer data for new Company ID
        if discovery_info.manufacturer_data:
            for manufacturer_id, data in discovery_info.manufacturer_data.items():
                if manufacturer_id == BLE_COMPANY_ID and len(data) >= 20:
                    return True
        
        # Check name patterns as fallback
        name = discovery_info.name or ""
        if any(pattern in name.upper() for pattern in ["GEMNS", "GEMS"]):
            return True
        
        return False
    
    def _extract_device_info_from_beacon(self, discovery_info: BluetoothServiceInfo) -> tuple[str, str]:
        """Extract device type and name from beacon data."""
        try:
            # Try to parse manufacturer data to get device type
            if discovery_info.manufacturer_data:
                for manufacturer_id, data in discovery_info.manufacturer_data.items():
                    if manufacturer_id == BLE_COMPANY_ID and len(data) >= 20:
                        # Try to parse the packet to get device type
                        try:
                            from .packet_parser import GemnsPacket
                            packet = GemnsPacket(data)
                            if packet.is_valid():
                                device_type = packet.device_type
                                
                                # Map sensor type to device type and name
                                device_type_map = {
                                    0: ("legacy", "Legacy Device"),
                                    1: ("button", "Button"),
                                    2: ("vibration_sensor", "Vibration Monitor"),
                                    3: ("two_way_switch", "Two-Way Switch"),
                                    4: ("leak_sensor", "Leak Sensor"),
                                }
                                
                                device_type, device_name = device_type_map.get(device_type, ("unknown", "IoT Device"))
                                
                                # Generate professional device name
                                short_address = discovery_info.address.replace(":", "")[-6:].upper()
                                device_number = int(short_address, 16) % 1000
                                professional_name = f"Gemns™ IoT {device_name} Unit-{device_number:03d}"
                                
                                return device_type, professional_name
                        except Exception as e:
                            print(f"Error parsing beacon data: {e}")
                            pass
            
            # Fallback: use device name or generate generic name
            device_name = discovery_info.name or "Gemns™ IoT Device"
            if "Gemns" in device_name:
                # Try to extract device type from name
                if "Leak" in device_name or "leak" in device_name:
                    return "leak_sensor", "Gemns™ IoT Leak Sensor"
                elif "Vibration" in device_name or "vibration" in device_name:
                    return "vibration_sensor", "Gemns™ IoT Vibration Monitor"
                elif "Switch" in device_name or "switch" in device_name:
                    return "two_way_switch", "Gemns™ IoT Two-Way Switch"
                elif "Button" in device_name or "button" in device_name:
                    return "button", "Gemns™ IoT Button"
            
            # Default fallback
            return "unknown", "Gemns™ IoT Device"
            
        except Exception as e:
            print(f"Error extracting device info: {e}")
            return "unknown", "Gemns™ IoT Device"

    async def async_step_import(self, import_data: dict[str, Any]) -> FlowResult:
        """Handle import from configuration.yaml."""
        return await self.async_step_user(import_data)

