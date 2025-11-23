"""Data coordinator for Gemns™ IoT integration."""

from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SIGNAL_DEVICE_UPDATED

_LOGGER = logging.getLogger(__name__)


class GemnsDataCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the Gemns™ IoT device manager."""

    def __init__(self, hass: HomeAssistant, device_manager) -> None:
        """Initialize the coordinator."""
        self.device_manager = device_manager
        self.hass = hass

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via device manager."""
        try:
            # Get current device data
            devices = self.device_manager.get_all_devices()
        except (ValueError, KeyError, AttributeError, TypeError) as err:
            raise UpdateFailed(f"Error communicating with device manager: {err}") from err
        else:
            return {
                "devices": devices,
                "last_update": self.device_manager.devices,
            }

    async def async_setup(self) -> None:
        """Set up the coordinator."""
        # Listen for device updates
        self._unsub_dispatcher = async_dispatcher_connect(
            self.hass, SIGNAL_DEVICE_UPDATED, self._handle_device_update
        )

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator."""
        if hasattr(self, '_unsub_dispatcher') and self._unsub_dispatcher:
            try:
                self._unsub_dispatcher()
            except (ValueError, KeyError, AttributeError, TypeError) as e:
                _LOGGER.warning("Error removing dispatcher: %s", e)
            else:
                self._unsub_dispatcher = None

    def _handle_device_update(self, device_data: dict[str, Any]) -> None:
        """Handle device update from dispatcher."""
        # Trigger a data update when devices change
        self.async_set_updated_data({
            "devices": self.device_manager.get_all_devices(),
            "last_update": self.device_manager.devices,
        })
