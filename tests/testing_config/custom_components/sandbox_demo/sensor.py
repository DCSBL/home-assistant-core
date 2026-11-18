"""Sensors of the sandbox demo integration."""

import os

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the device of an entry."""
    return DeviceInfo(
        identifiers={("sandbox_demo", entry.entry_id)},
        name="Demo meter",
        manufacturer="Sandbox",
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    async_add_entities([PowerSensor(entry), PidSensor(entry)])


class PowerSensor(SensorEntity):
    """A power sensor."""

    _attr_has_entity_name = True
    _attr_name = "Power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_value = 1234.5

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize."""
        self._attr_unique_id = f"{entry.entry_id}_power"
        self._attr_device_info = device_info(entry)
        self._attr_extra_state_attributes = {"phase": 1}


class PidSensor(SensorEntity):
    """Reports the process it runs in, disabled by default."""

    _attr_has_entity_name = True
    _attr_name = "Process"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize."""
        self._attr_unique_id = f"{entry.entry_id}_pid"
        self._attr_device_info = device_info(entry)
        self._attr_native_value = os.getpid()
