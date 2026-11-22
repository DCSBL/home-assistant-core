"""Diagnostics support for RESTful Command."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HEADERS, CONF_PASSWORD, CONF_PAYLOAD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import RestCommandConfigEntry

# Headers and payloads often hold tokens, like Authorization: Bearer
TO_REDACT = {CONF_HEADERS, CONF_PASSWORD, CONF_PAYLOAD, CONF_USERNAME}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: RestCommandConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return {
        "data": async_redact_data(entry.data, TO_REDACT),
        "commands": [
            {
                "title": subentry.title,
                "data": async_redact_data(subentry.data, TO_REDACT),
            }
            for subentry in entry.subentries.values()
        ],
        "actions": sorted(entry.runtime_data),
    }
