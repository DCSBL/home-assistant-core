"""An ordinary integration, used to test that it runs unchanged in a sandbox."""

import os

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)

DOMAIN = "sandbox_demo"
PLATFORMS = [Platform.SENSOR, Platform.SWITCH]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register a service that answers with where it runs."""

    async def ping(call: ServiceCall) -> ServiceResponse:
        return {"pong": call.data.get("value"), "pid": os.getpid()}

    hass.services.async_register(
        DOMAIN, "ping", ping, supports_response=SupportsResponse.ONLY
    )
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate from minor version 1."""
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "migrated": True}, minor_version=2
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the demo entities."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    hass.bus.async_fire(f"{DOMAIN}_started", {"entry_id": entry.entry_id})
    # The event of another integration: core must never see this one.
    hass.bus.async_fire("zwave_js_value_notification", {"value": "spoofed"})
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the demo entities."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
