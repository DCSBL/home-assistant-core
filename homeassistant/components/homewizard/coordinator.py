"""Update coordinator for HomeWizard."""

from __future__ import annotations

from asyncio import sleep

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, LOGGER, UPDATE_INTERVAL


class Api:
    """Some API"""

    def __init__(self) -> None:
        LOGGER.warning("Api __init__")

    def __del__(self):
        print("Api __del__")


class HWEnergyDeviceUpdateCoordinator(DataUpdateCoordinator[None]):
    """Gather data for the energy device."""

    def __init__(self, hass, api: Api) -> None:
        """Initialize update coordinator."""
        super().__init__(hass, LOGGER, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        LOGGER.warning("__init__ HWEnergyDeviceUpdateCoordinator")

        self.api = api

    def __del__(self):
        print("__del__ from HWEnergyDeviceUpdateCoordinator")

    async def _async_update_data(self) -> None:
        """Fetch all device and sensor data from api."""

        LOGGER.warning("Updating")
        await sleep(1)
        LOGGER.warning("Done")
