"""The Homewizard integration."""

from homewizard_energy import (
    HomeWizardEnergy,
    HomeWizardEnergyV1,
    HomeWizardEnergyV2,
    has_v2_api,
)

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.const import CONF_IP_ADDRESS, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue

from .const import DOMAIN, LOGGER
from .coordinator import Api, HWEnergyDeviceUpdateCoordinator

type HomeWizardConfigEntry = ConfigEntry[HWEnergyDeviceUpdateCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: HomeWizardConfigEntry) -> bool:
    """Set up Homewizard from a config entry."""

    api = Api()

    LOGGER.debug("Setting up entry %s", entry.entry_id)
    coordinator = HWEnergyDeviceUpdateCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    # Finalize
    await hass.config_entries.async_forward_entry_setups(entry, [])

    LOGGER.debug("Entry %s is ready", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HomeWizardConfigEntry) -> bool:
    """Unload a config entry."""
    LOGGER.debug("Unloading entry %s", entry.entry_id)
    return await hass.config_entries.async_unload_platforms(entry, [])
