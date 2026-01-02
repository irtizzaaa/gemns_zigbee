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
    DEVICE_CATEGORY_SWITCH,
    DEVICE_STATUS_OFFLINE,
    DEVICE_TYPE_ZIGBEE,
    DOMAIN,
    MQTT_TOPIC_CONTROL,
    MQTT_TOPIC_DEVICE,
    MQTT_TOPIC_STATUS,
    SIGNAL_DEVICE_UPDATED,
)

_LOGGER = logging.getLogger(__name__)

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
        self._storage_path = self.hass.config.path(f".{DOMAIN}_devices.json")

    async def start(self):
        """Start the device manager."""
        await self._load_devices()
        if self.config.get(CONF_MQTT_BROKER):
            await self._subscribe_to_mqtt()
        else:
            _LOGGER.info("MQTT broker not configured, skipping MQTT subscription")

        discovery_task = asyncio.create_task(self._device_discovery_loop())
        self._discovery_task = discovery_task

    async def stop(self):
        """Stop the device manager."""
        await self._save_devices()

    async def add_device(self, device_data: dict[str, Any]) -> bool:
        """Add a new device manually."""
        try:
            device_id = device_data["device_id"]
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            _LOGGER.error("Error adding device: %s", e)
            return False
        
        if device_id in self.devices:
            _LOGGER.debug("Device %s already exists, updating instead", device_id)
            self.devices[device_id].update(device_data)
            self.devices[device_id]["last_seen"] = datetime.now(UTC).isoformat()
            await self._save_devices()
            self.hass.async_create_task(
                self._async_notify_device_update(self.devices[device_id])
            )
            return True
        
        device = {
            "device_id": device_id,
            "device_type": device_data.get("device_type", "ble"),
            "category": device_data.get("category", "sensor"),
            "name": device_data.get("name", device_id),
            "zigbee_id": device_data.get("zigbee_id"),
            "ble_discovery_mode": device_data.get("ble_discovery_mode", "v0_manual"),
            "status": device_data.get("status", "disconnected"),
            "last_seen": datetime.now(UTC).isoformat(),
            "created_manually": device_data.get("created_manually", False),
            "properties": device_data.get("properties", {}).copy()
        }

        self.devices[device_id] = device

        await self._save_devices()

        self.hass.async_create_task(
            self._async_notify_device_added(device)
        )

        _LOGGER.info("Device added: %s (category: %s, type: %s)", device_id, device.get("category"), device.get("device_type"))
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
            if not await mqtt.async_wait_for_mqtt_client(self.hass):
                _LOGGER.warning("MQTT client not available, skipping MQTT subscription")
                return

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
                if "name" not in data:
                    data["name"] = device_id
                if "last_seen" not in data:
                    data["last_seen"] = datetime.now(UTC).isoformat()
                if "properties" not in data:
                    data["properties"] = {}

                self.devices[device_id] = data
                _LOGGER.info("Updated device %s with status: %s", device_id, data.get('status'))

                self.hass.async_create_task(self._save_devices())

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

            action = data.get("action")
            if action == "toggle_zigbee":
                enabled = data.get("enabled", False)
                _LOGGER.info("Zigbee toggle command received: %s", enabled)
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

        device_id = device_data.get("device_id")
        if device_id and device_id not in self._created_entities:
            self._created_entities.add(device_id)
            async_dispatcher_send(self.hass, SIGNAL_DEVICE_ADDED, device_data)

    async def _async_notify_device_added(self, device_data):
        """Notify device added."""
        device_id = device_data.get("device_id")
        category = device_data.get("category")
        _LOGGER.info("Sending SIGNAL_DEVICE_ADDED for device: %s, category: %s", device_id, category)
        async_dispatcher_send(self.hass, SIGNAL_DEVICE_ADDED, device_data)

    @property
    def mqtt_client(self):
        """Get MQTT client for compatibility."""
        return self

    async def _device_discovery_loop(self):
        """Main device discovery loop."""
        while True:
            try:
                await self._update_device_statuses()
                await asyncio.sleep(30)

            except (ValueError, KeyError, AttributeError, TypeError) as e:
                _LOGGER.error("Error in device discovery loop: %s", e)
                await asyncio.sleep(60)

    async def _update_device_statuses(self):
        """Update status of all devices."""
        current_time = datetime.now(UTC)
        timeout_seconds = 5
        
        for device in self.devices.values():
            if device.get("status") == "connected":
                device_type = device.get("device_type")
                category = device.get("category")
                properties = device.get("properties", {})
                cmd_type = properties.get("cmd_type")
                
                is_zigbee_switch_cmd3 = (
                    device_type == DEVICE_TYPE_ZIGBEE and
                    category == DEVICE_CATEGORY_SWITCH and
                    cmd_type == 3
                )
                
                if is_zigbee_switch_cmd3:
                    last_seen_str = device.get("last_seen")
                    if last_seen_str:
                        try:
                            last_seen_str_clean = last_seen_str.replace('Z', '+00:00')
                            last_seen = datetime.fromisoformat(last_seen_str_clean)
                            if last_seen.tzinfo is None:
                                last_seen = last_seen.replace(tzinfo=UTC)
                            time_diff = (current_time - last_seen).total_seconds()
                            
                            if time_diff > timeout_seconds:
                                device["status"] = DEVICE_STATUS_OFFLINE
                                self.hass.async_create_task(
                                    self._async_notify_device_update(device)
                                )
                                _LOGGER.debug("Switch %s (cmd_type=3) set to offline - no message for %.1f seconds", 
                                            device.get("device_id"), time_diff)
                        except (ValueError, TypeError, AttributeError) as e:
                            _LOGGER.warning("Error parsing last_seen for device %s: %s", 
                                          device.get("device_id"), e)
                else:
                    if random.random() < 0.1:
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
                    device_id = device.get("device_id")
                    if device_id:
                        self._created_entities.add(device_id)
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

        def unsubscribe():
            if device_id in self._subscribers:
                self._subscribers[device_id].remove(callback)
        return unsubscribe

    def subscribe_to_updates(self, callback):
        """Subscribe to general updates."""
        if "general" not in self._subscribers:
            self._subscribers["general"] = []
        self._subscribers["general"].append(callback)

        def unsubscribe():
            if "general" in self._subscribers:
                self._subscribers["general"].remove(callback)
        return unsubscribe
