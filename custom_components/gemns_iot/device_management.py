"""Device management for Gemns™ IoT integration."""

import asyncio
from datetime import UTC, datetime
import json
import logging
import random
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.mqtt import async_publish, async_subscribe
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_MQTT_BROKER,
    DOMAIN,
    MQTT_TOPIC_CONTROL,
    MQTT_TOPIC_DEVICE,
    MQTT_TOPIC_STATUS,
    SIGNAL_DEVICE_UPDATED,
)

_LOGGER = logging.getLogger(__name__)

# Signal for device updates
SIGNAL_DEVICE_ADDED = f"{DOMAIN}_device_added"
SIGNAL_DEVICE_REMOVED = f"{DOMAIN}_device_removed"

class GemnsDeviceManager:
    """Manages Gemns™ IoT devices."""

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]):
        """Initialize the device manager."""
        self.hass = hass
        self.config = config
        self.devices: dict[str, dict[str, Any]] = {}
        self.entity_registry = er.async_get(hass)
        self._subscribers = {}
        self._mqtt_client = None
        self._created_entities = set()
        # Simple persistence file for devices
        self._storage_path = self.hass.config.path(f".{DOMAIN}_devices.json")

    async def start(self):
        """Start the device manager."""
        # Load devices from storage on startup so entities can be recreated
        await self._load_devices()
        # Subscribe to MQTT topics only if MQTT broker is configured
        if self.config.get(CONF_MQTT_BROKER):
            await self._subscribe_to_mqtt()
        else:
            _LOGGER.info("MQTT broker not configured, skipping MQTT subscription")

        # Start device discovery
        discovery_task = asyncio.create_task(self._device_discovery_loop())
        self._discovery_task = discovery_task

    async def stop(self):
        """Stop the device manager."""
        # Cleanup tasks
        await self._save_devices()

    async def add_device(self, device_data: dict[str, Any]) -> bool:
        """Add a new device manually."""
        try:
            device_id = device_data["device_id"]
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            _LOGGER.error("Error adding device: %s", e)
            return False
        else:
            # Create device entry
            device = {
                "device_id": device_id,
                "device_type": device_data.get("device_type", "ble"),
                "category": device_data.get("category", "sensor"),
                "name": device_data.get("name", device_id),
                "ble_discovery_mode": device_data.get("ble_discovery_mode", "v0_manual"),
                "status": device_data.get("status", "disconnected"),
                "last_seen": datetime.now(UTC).isoformat(),
                "created_manually": True,
                "properties": {}
            }

            self.devices[device_id] = device

            # Persist devices
            await self._save_devices()

            # Notify subscribers - this is called from async context, so it's safe
            self.hass.async_create_task(
                self._async_notify_device_added(device)
            )

            _LOGGER.info("Device added: %s", device_id)
            return True

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        """Get a device by ID."""
        return self.devices.get(device_id)

    def get_all_devices(self) -> list[dict[str, Any]]:
        """Get all devices."""
        return list(self.devices.values())

    def get_devices_by_category(self, category: str) -> list[dict[str, Any]]:
        """Get devices by category."""
        return [d for d in self.devices.values() if d.get("category") == category]

    def get_devices_by_type(self, device_type: str) -> list[dict[str, Any]]:
        """Get devices by type."""
        return [d for d in self.devices.values() if d.get("device_type") == device_type]

    def get_devices_by_status(self, status: str) -> list[dict[str, Any]]:
        """Get devices by status."""
        return [d for d in self.devices.values() if d.get("status") == status]

    async def _subscribe_to_mqtt(self):
        """Subscribe to relevant MQTT topics."""
        try:
            # Check if MQTT is available
            if not await mqtt.async_wait_for_mqtt_client(self.hass):
                _LOGGER.warning("MQTT client not available, skipping MQTT subscription")
                return

            # Subscribe to MQTT topics for device updates (removed dongle topics)
            await async_subscribe(
                self.hass,
                MQTT_TOPIC_STATUS,
                self._handle_status_message
            )
            await async_subscribe(
                self.hass,
                f"{MQTT_TOPIC_DEVICE}/+/+",
                self._handle_device_message
            )
            await async_subscribe(
                self.hass,
                f"{MQTT_TOPIC_CONTROL}/+/+",
                self._handle_control_message
            )
            _LOGGER.info("Device manager subscribed to MQTT topics")
        except (ValueError, KeyError, AttributeError, TypeError, ConnectionError) as e:
            _LOGGER.warning("Could not subscribe to MQTT topics: %s", e)

    def _handle_status_message(self, msg):
        """Handle status messages from add-on."""
        try:
            data = json.loads(msg.payload)
            _LOGGER.info("Status message received: %s", data)
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            _LOGGER.error("Error handling status message: %s", e)

    def _handle_device_message(self, msg):
        """Handle device messages."""
        try:
            data = json.loads(msg.payload)
            _LOGGER.info("Device message received: %s", data)

            # Update device status
            device_id = data.get("device_id")
            if device_id:
                # Ensure device has all required fields
                if "name" not in data:
                    data["name"] = device_id
                if "last_seen" not in data:
                    data["last_seen"] = datetime.now(UTC).isoformat()
                if "properties" not in data:
                    data["properties"] = {}

                self.devices[device_id] = data
                _LOGGER.info("Updated device %s with status: %s", device_id, data.get('status'))

                # Persist devices on update
                self.hass.async_create_task(self._save_devices())

                # Schedule the dispatcher call in the main event loop
                self.hass.loop.call_soon_threadsafe(
                    lambda: self.hass.async_create_task(
                        self._async_notify_device_update(data)
                    )
                )

        except (ValueError, KeyError, AttributeError, TypeError) as e:
            _LOGGER.error("Error handling device message: %s", e)

    def _handle_control_message(self, msg):
        """Handle control messages from add-on."""
        try:
            data = json.loads(msg.payload)
            _LOGGER.info("Control message received: %s", data)

            # Handle different control actions
            action = data.get("action")
            if action == "toggle_zigbee":
                enabled = data.get("enabled", False)
                _LOGGER.info("Zigbee toggle command received: %s", enabled)
                # Update Zigbee status in config
                self.config["enable_zigbee"] = enabled

        except (ValueError, KeyError, AttributeError, TypeError) as e:
            _LOGGER.error("Error handling control message: %s", e)

    async def publish_mqtt(self, topic: str, payload: str):
        """Publish MQTT message."""
        try:
            await async_publish(self.hass, topic, payload)
            _LOGGER.debug("Published MQTT message: %s -> %s", topic, payload)
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            _LOGGER.error("Failed to publish MQTT message: %s", e)

    async def _async_notify_device_update(self, device_data):
        """Notify device updates."""
        async_dispatcher_send(self.hass, SIGNAL_DEVICE_UPDATED, device_data)

        # Check if this is a new device that needs entity creation
        device_id = device_data.get("device_id")
        if device_id and device_id not in self._created_entities:
            self._created_entities.add(device_id)
            # Signal that a new device was added
            async_dispatcher_send(self.hass, SIGNAL_DEVICE_ADDED, device_data)

    async def _async_notify_device_added(self, device_data):
        """Notify device added."""
        async_dispatcher_send(self.hass, SIGNAL_DEVICE_ADDED, device_data)

    @property
    def mqtt_client(self):
        """Get MQTT client for compatibility."""
        return self

    async def _device_discovery_loop(self):
        """Main device discovery loop."""
        while True:
            try:
                # Update device statuses
                await self._update_device_statuses()

                # Wait before next scan
                await asyncio.sleep(30)

            except (ValueError, KeyError, AttributeError, TypeError) as e:
                _LOGGER.error("Error in device discovery loop: %s", e)
                await asyncio.sleep(60)

    async def _update_device_statuses(self):
        """Update status of all devices."""
        for device in self.devices.values():
            # Simulate some devices going offline
            if device.get("status") == "connected":
                # Randomly set some devices to offline for testing
                if random.random() < 0.1:  # 10% chance
                    device["status"] = "offline"
                    device["last_seen"] = datetime.now(UTC).isoformat()
                    self.hass.async_create_task(
                        self._async_notify_device_update(device)
                    )

    async def _load_devices(self):
        """Load devices from a simple JSON file."""
        try:
            path = self._storage_path
            
            def _load_file():
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, _load_file)
            
            if isinstance(data, dict):
                self.devices = data
                _LOGGER.info("Loaded %d devices from storage", len(self.devices))
                for device in self.devices.values():
                    await self._async_notify_device_added(device)
            else:
                _LOGGER.warning("Device storage file format invalid, expected dict")
        except FileNotFoundError:
            _LOGGER.info("No existing device storage file found, starting empty")
        except (OSError, ValueError, TypeError) as e:
            _LOGGER.error("Failed to load devices from storage: %s", e)

    async def _save_devices(self):
        """Save devices to a simple JSON file."""
        try:
            path = self._storage_path
            devices_data = self.devices.copy()
            
            def _save_file():
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(devices_data, f, ensure_ascii=False, indent=2)
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _save_file)
            _LOGGER.debug("Saved %d devices to storage", len(self.devices))
        except (OSError, TypeError, ValueError) as e:
            _LOGGER.error("Failed to save devices to storage: %s", e)

    def subscribe_to_device_updates(self, device_id: str, callback):
        """Subscribe to device updates."""
        if device_id not in self._subscribers:
            self._subscribers[device_id] = []
        self._subscribers[device_id].append(callback)

        # Return unsubscribe function
        def unsubscribe():
            if device_id in self._subscribers:
                self._subscribers[device_id].remove(callback)
        return unsubscribe

    def subscribe_to_updates(self, callback):
        """Subscribe to general updates."""
        if "general" not in self._subscribers:
            self._subscribers["general"] = []
        self._subscribers["general"].append(callback)

        # Return unsubscribe function
        def unsubscribe():
            if "general" in self._subscribers:
                self._subscribers["general"].remove(callback)
        return unsubscribe
