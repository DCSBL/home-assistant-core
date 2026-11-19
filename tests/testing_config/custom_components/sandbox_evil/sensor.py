"""Report what the evil integration managed to do."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the report."""
    async_add_entities([Report(entry, hass.data["sandbox_evil"])])


class Report(SensorEntity):
    """What the evil integration reached."""

    _attr_name = "Evil report"

    def __init__(self, entry: ConfigEntry, results: dict[str, str]) -> None:
        """Initialize."""
        self._attr_unique_id = entry.entry_id
        self._attr_native_value = "done"
        self._attr_extra_state_attributes = results
