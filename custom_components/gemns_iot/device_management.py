"""Device management for Gemns™ IoT integration."""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_DEVICE_ID, CONF_NAME
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.components import mqtt
from homeassistant.components.mqtt import async_publish, async_subscribe

from .const import (
    DOMAIN,
    CONF_MQTT_BROKER,
    DEVICE_TYPE_ZIGBEE,
    DEVICE_CATEGORY_SENSOR,
    DEVICE_CATEGORY_SWITCH,
    DEVICE_CATEGORY_LIGHT,
    DEVICE_CATEGORY_DOOR,
    DEVICE_CATEGORY_TOGGLE,
    DEVICE_STATUS_CONNECTED,
    DEVICE_STATUS_OFFLINE,
    MQTT_TOPIC_STATUS,
    MQTT_TOPIC_DEVICE,
    MQTT_TOPIC_CONTROL,
    SIGNAL_DEVICE_UPDATED,
)

_LOGGER = logging.getLogger(__name__)

# Signal for device updates
SIGNAL_DEVICE_ADDED = f"{DOMAIN}_device_added"
SIGNAL_DEVICE_REMOVED = f"{DOMAIN}_device_removed"

class GemnsDeviceManager:
    """Manages Gemns™ IoT devices."""

    def __init__(self, hass: HomeAssistant, config: Dict[str, Any]):
        """Initialize the device manager."""
        self.hass = hass
        self.config = config
        self.devices: Dict[str, Dict[str, Any]] = {}
        self.entity_registry = er.async_get(hass)
        self._subscribers = {}
        self._mqtt_client = None
        self._created_entities = set()
        
    async def start(self):
        """Start the device manager."""
        # Subscribe to MQTT topics only if MQTT broker is configured
        if self.config.get(CONF_MQTT_BROKER):
            await self._subscribe_to_mqtt()
        else:
            _LOGGER.info("MQTT broker not configured, skipping MQTT subscription")
        
        # Start device discovery
        asyncio.create_task(self._device_discovery_loop())
        
    async def stop(self):
        """Stop the device manager."""
        # Cleanup tasks
        pass
        
    async def add_device(self, device_data: Dict[str, Any]) -> bool:
        """Add a new device manually."""
        try:
            device_id = device_data["device_id"]
            
            # Create device entry
            device = {
                "device_id": device_id,
                "device_type": device_data.get("device_type", "ble"),
                "category": device_data.get("category", "sensor"),
                "name": device_data.get("name", device_id),
                "ble_discovery_mode": device_data.get("ble_discovery_mode", "v0_manual"),
                "status": device_data.get("status", "disconnected"),
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "created_manually": True,
                "properties": {}
            }
            
            self.devices[device_id] = device
            
            # Notify subscribers - this is called from async context, so it's safe
            self.hass.async_create_task(
                self._async_notify_device_added(device)
            )
            
            _LOGGER.info(f"Device added: {device_id}")
            return True
            
        except Exception as e:
            _LOGGER.error(f"Error adding device: {e}")
            return False
            
    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get a device by ID."""
        return self.devices.get(device_id)
        
    def get_all_devices(self) -> List[Dict[str, Any]]:
        """Get all devices."""
        return list(self.devices.values())
        
    def get_devices_by_category(self, category: str) -> List[Dict[str, Any]]:
        """Get devices by category."""
        return [d for d in self.devices.values() if d.get("category") == category]
        
    def get_devices_by_type(self, device_type: str) -> List[Dict[str, Any]]:
        """Get devices by type."""
        return [d for d in self.devices.values() if d.get("device_type") == device_type]
        
    def get_devices_by_status(self, status: str) -> List[Dict[str, Any]]:
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
        except Exception as e:
            _LOGGER.warning(f"Could not subscribe to MQTT topics: {e}")
            
    def _handle_status_message(self, msg):
        """Handle status messages from add-on."""
        try:
            data = json.loads(msg.payload)
            _LOGGER.info(f"Status message received: {data}")
        except Exception as e:
            _LOGGER.error(f"Error handling status message: {e}")
            
    def _handle_device_message(self, msg):
        """Handle device messages."""
        try:
            data = json.loads(msg.payload)
            _LOGGER.info(f"Device message received: {data}")
            
            # Update device status
            device_id = data.get("device_id")
            if device_id:
                # Ensure device has all required fields
                if "name" not in data:
                    data["name"] = device_id
                if "last_seen" not in data:
                    from datetime import datetime, timezone
                    data["last_seen"] = datetime.now(timezone.utc).isoformat()
                if "properties" not in data:
                    data["properties"] = {}
                    
                self.devices[device_id] = data
                _LOGGER.info(f"Updated device {device_id} with status: {data.get('status')}")
                
                # Schedule the dispatcher call in the main event loop
                self.hass.loop.call_soon_threadsafe(
                    lambda: self.hass.async_create_task(
                        self._async_notify_device_update(data)
                    )
                )
                
        except Exception as e:
            _LOGGER.error(f"Error handling device message: {e}")
            
    def _handle_control_message(self, msg):
        """Handle control messages from add-on."""
        try:
            data = json.loads(msg.payload)
            _LOGGER.info(f"Control message received: {data}")
            
            # Handle different control actions
            action = data.get("action")
            if action == "toggle_zigbee":
                enabled = data.get("enabled", False)
                _LOGGER.info(f"Zigbee toggle command received: {enabled}")
                # Update Zigbee status in config
                self.config["enable_zigbee"] = enabled
                
        except Exception as e:
            _LOGGER.error(f"Error handling control message: {e}")
        
    async def publish_mqtt(self, topic: str, payload: str):
        """Publish MQTT message."""
        try:
            await async_publish(self.hass, topic, payload)
            _LOGGER.debug(f"Published MQTT message: {topic} -> {payload}")
        except Exception as e:
            _LOGGER.error(f"Failed to publish MQTT message: {e}")
            
    async def _async_notify_device_update(self, device_data):
        """Async helper to notify device updates."""
        async_dispatcher_send(self.hass, SIGNAL_DEVICE_UPDATED, device_data)
        
        # Check if this is a new device that needs entity creation
        device_id = device_data.get("device_id")
        if device_id and device_id not in self._created_entities:
            self._created_entities.add(device_id)
            # Signal that a new device was added
            async_dispatcher_send(self.hass, SIGNAL_DEVICE_ADDED, device_data)
        
    async def _async_notify_device_added(self, device_data):
        """Async helper to notify device added."""
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
                
            except Exception as e:
                _LOGGER.error(f"Error in device discovery loop: {e}")
                await asyncio.sleep(60)
                
    async def _update_device_statuses(self):
        """Update status of all devices."""
        for device_id, device in self.devices.items():
            # Simulate some devices going offline
            if device.get("status") == "connected":
                # Randomly set some devices to offline for testing
                import random
                if random.random() < 0.1:  # 10% chance
                    device["status"] = "offline"
                    device["last_seen"] = datetime.now(timezone.utc).isoformat()
                    self.hass.async_create_task(
                        self._async_notify_device_update(device)
                    )
                    
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
