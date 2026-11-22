"""Tests for the RESTful Command diagnostics."""

from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant.typing import ClientSessionGenerator

from homeassistant.components.rest_command.const import DOMAIN, SUBENTRY_TYPE_COMMAND
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant


async def test_diagnostics(
    hass: HomeAssistant, hass_client: ClientSessionGenerator
) -> None:
    """Test the diagnostics leave out credentials and headers."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="https://example.com",
        data={
            "url": "https://example.com",
            "auth": {"username": "user", "password": "secret"},
            "additional": {"headers": {"Authorization": "Bearer token"}},
        },
        subentries_data=[
            ConfigSubentryData(
                data={
                    "path": "/door",
                    "payload": '{"token": "secret"}',
                    "additional": {"headers": {"X-Api-Key": "key"}},
                },
                subentry_type=SUBENTRY_TYPE_COMMAND,
                title="Open door",
            )
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await get_diagnostics_for_config_entry(hass, hass_client, entry) == {
        "data": {
            "url": "https://example.com",
            "auth": {"username": "**REDACTED**", "password": "**REDACTED**"},
            "additional": {"headers": "**REDACTED**"},
        },
        "commands": [
            {
                "title": "Open door",
                "data": {
                    "path": "/door",
                    "payload": "**REDACTED**",
                    "additional": {"headers": "**REDACTED**"},
                },
            }
        ],
        "actions": ["open_door"],
    }
