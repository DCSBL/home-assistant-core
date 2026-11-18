"""Switch of the sandbox demo integration."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .sensor import device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switch."""
    async_add_entities([DemoSwitch(entry)])


class DemoSwitch(SwitchEntity):
    """A switch that remembers its state."""

    _attr_has_entity_name = True
    _attr_name = "Relay"
    _attr_is_on = False

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize."""
        self._attr_unique_id = f"{entry.entry_id}_relay"
        self._attr_device_info = device_info(entry)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        self._attr_is_on = False
        self.async_write_ha_state()
