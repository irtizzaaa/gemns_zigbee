"""The Gemns™ IoT integration."""

import logging
import pathlib

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .ble_coordinator import GemnsBluetoothProcessorCoordinator
from .const import CONF_ENABLE_ZIGBEE, CONF_SERIAL_PORT, DOMAIN
from .coordinator import GemnsDataCoordinator
from .device_management import GemnsDeviceManager
from .zigbee_coordinator import ZigbeeCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.LIGHT,
]

# BLE platform
BLE_PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Gemns™ IoT from a config entry."""
    _LOGGER.info("Setting up Gemns™ IoT integration (entry_id: %s)", entry.entry_id)
    _LOGGER.info("Config entry data: %s", entry.data)
    hass.data.setdefault(DOMAIN, {})
    
    # Register static path for integration assets
    if "static_registered" not in hass.data[DOMAIN]:
        integration_path = pathlib.Path(__file__).parent
        static_path = integration_path / "static"
        if static_path.exists():
            await hass.http.async_register_static_paths(
                [
                    StaticPathConfig(
                        f"/local/custom_components/{DOMAIN}/static",
                        str(static_path),
                        cache_headers=True,
                    )
                ]
            )
            hass.data[DOMAIN]["static_registered"] = True

    # Check if this is a BLE device entry (has address and decryption_key)
    if entry.data.get("address") and entry.data.get("decryption_key"):
        # This is a BLE device entry
        _LOGGER.info("Detected BLE device entry, initializing BLE coordinator...")
        coordinator = GemnsBluetoothProcessorCoordinator(hass, entry)
        await coordinator.async_init()
        entry.runtime_data = coordinator

        # Store coordinator in hass.data for consistency with unload process
        hass.data[DOMAIN][entry.entry_id] = {
            "coordinator": coordinator,
            "config": entry.data
        }

        # Forward the setup to BLE platforms
        await hass.config_entries.async_forward_entry_setups(entry, BLE_PLATFORMS)

        # Start the coordinator
        entry.async_on_unload(coordinator.async_start())

        return True
    
    # Check if this is a Zigbee-only entry (no address/decryption_key, but has enable_zigbee)
    if not entry.data.get("address") and not entry.data.get("decryption_key"):
        _LOGGER.info("Detected Zigbee-only entry, skipping BLE initialization...")
    # This is a traditional MQTT-based entry
    # Create device manager
    device_manager = GemnsDeviceManager(hass, entry.data)
    await device_manager.start()

    # Create coordinator
    coordinator = GemnsDataCoordinator(hass, device_manager)
    await coordinator.async_setup()

    # Initialize Zigbee coordinator if enabled
    zigbee_coordinator = None
    enable_zigbee = entry.data.get(CONF_ENABLE_ZIGBEE, True)
    _LOGGER.info("Zigbee enabled: %s", enable_zigbee)
    
    if enable_zigbee:
        serial_port = entry.data.get(CONF_SERIAL_PORT)
        _LOGGER.info("Initializing Zigbee coordinator...")
        _LOGGER.info("Zigbee serial port from config: %s", serial_port if serial_port else "None (will auto-detect)")
        zigbee_coordinator = ZigbeeCoordinator(hass, device_manager, serial_port)
        _LOGGER.info("ZigbeeCoordinator instance created, attempting to start...")
        if await zigbee_coordinator.async_start():
            _LOGGER.info("Zigbee coordinator started successfully")
        else:
            _LOGGER.warning("Failed to start Zigbee coordinator")
            zigbee_coordinator = None
    else:
        _LOGGER.info("Zigbee is disabled in configuration - skipping Zigbee coordinator initialization")

    # Store device manager and coordinator in hass data
    hass.data[DOMAIN][entry.entry_id] = {
        "device_manager": device_manager,
        "coordinator": coordinator,
        "zigbee_coordinator": zigbee_coordinator,
        "config": entry.data
    }

    # Register services
    await _register_services(hass, device_manager, entry, zigbee_coordinator)

    # Forward the setup to the relevant platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Check if this is a BLE device entry
    if entry.data.get("address") and entry.data.get("decryption_key"):
        # Unload BLE platforms
        unload_ok = await hass.config_entries.async_unload_platforms(entry, BLE_PLATFORMS)
    else:
        # Unload traditional platforms (Zigbee or MQTT)
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # Clean up device manager and coordinator
        if entry.entry_id in hass.data[DOMAIN]:
            device_manager = hass.data[DOMAIN][entry.entry_id].get("device_manager")
            coordinator = hass.data[DOMAIN][entry.entry_id].get("coordinator")
            zigbee_coordinator = hass.data[DOMAIN][entry.entry_id].get("zigbee_coordinator")
            if device_manager:
                await device_manager.stop()
            if coordinator:
                await coordinator.async_shutdown()
            if zigbee_coordinator:
                await zigbee_coordinator.async_stop()
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def _register_services(hass: HomeAssistant, device_manager: GemnsDeviceManager, entry: ConfigEntry, zigbee_coordinator: ZigbeeCoordinator | None = None):
    """Register Gemns™ IoT services."""

    async def add_device(service_call):
        """Add a new device."""
        data = service_call.data
        await device_manager.add_device(data)

    async def remove_device(service_call):
        """Remove a device."""
        device_id = service_call.data.get("device_id")
        if device_id and device_id in device_manager.devices:
            del device_manager.devices[device_id]
            hass.bus.async_fire(f"{DOMAIN}_device_removed", {"device_id": device_id})

    async def create_entities_for_devices(service_call):
        """Create entities for all devices in device manager."""
        # Get all devices and create entities for them
        all_devices = device_manager.get_all_devices()
        _LOGGER.info("Found %d devices to create entities for", len(all_devices))

        # Trigger platform reload to create entities for new devices
        await hass.config_entries.async_reload(entry.entry_id)

    async def start_pairing(service_call):
        """Start Zigbee pairing mode."""
        if zigbee_coordinator:
            await zigbee_coordinator.send_pairing_command()
            _LOGGER.info("Zigbee pairing mode started")
        else:
            _LOGGER.error("Zigbee coordinator not available")

    # Register services (removed MQTT dongle services)
    hass.services.async_register(DOMAIN, "add_device", add_device)
    hass.services.async_register(DOMAIN, "remove_device", remove_device)
    hass.services.async_register(DOMAIN, "create_entities", create_entities_for_devices)
    hass.services.async_register(DOMAIN, "start_pairing", start_pairing)
