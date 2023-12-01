"""Allow to set up simple automation rules via the config file."""
from __future__ import annotations

from dataclasses import dataclass
import logging

import voluptuous as vol

from homeassistant.const import (
    CONF_DESCRIPTION,
    CONF_ICON,
    CONF_ID,
    CONF_NAME,
    SERVICE_RELOAD,
)
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import collection, config_validation as cv, service
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import CONF_COLOR, DOMAIN, LOGGER

STORAGE_KEY = DOMAIN
STORAGE_VERSION = 1

LABEL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_DESCRIPTION): cv.string,
        vol.Optional(CONF_COLOR): cv.string,
        vol.Optional(CONF_ICON): cv.string,
    },
)

CREATE_FIELDS = {
    vol.Required(CONF_NAME): vol.All(str, vol.Length(min=1)),
    vol.Optional(CONF_DESCRIPTION): cv.string,
    vol.Optional(CONF_COLOR): cv.string,
    vol.Optional(CONF_ICON): cv.string,
}


UPDATE_FIELDS = {
    vol.Optional(CONF_NAME): vol.All(str, vol.Length(min=1)),
    vol.Optional(CONF_DESCRIPTION): cv.string,
    vol.Optional(CONF_COLOR): cv.string,
    vol.Optional(CONF_ICON): cv.string,
}


class LabelStore(Store):
    """Label storage."""


class LabelStorageCollection(collection.DictStorageCollection):
    """Person collection stored in storage."""

    CREATE_SCHEMA = vol.Schema(CREATE_FIELDS)
    UPDATE_SCHEMA = vol.Schema(UPDATE_FIELDS)

    def __init__(
        self,
        store: Store,
        id_manager: collection.IDManager,
        yaml_collection: collection.YamlCollection,
    ) -> None:
        """Initialize a label storage collection."""
        super().__init__(store, id_manager)
        self.yaml_collection = yaml_collection

    async def async_load(self) -> None:
        """Load the Storage collection."""
        await super().async_load()

    async def _process_create_data(self, data: dict) -> dict:
        """Validate the config is valid."""
        data = self.CREATE_SCHEMA(data)

        return data

    @callback
    def _get_suggested_id(self, info: dict) -> str:
        """Suggest an ID based on the config."""
        return info[CONF_NAME]

    async def _update_data(self, item: dict, update_data: dict) -> dict:
        """Return a new updated data object."""
        update_data = self.UPDATE_SCHEMA(update_data)

        return {**item, **update_data}


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up all labels."""

    LOGGER.error("async_setup")

    entity_component = EntityComponent[Label](LOGGER, DOMAIN, hass)
    id_manager = collection.IDManager()
    yaml_collection = collection.YamlCollection(
        logging.getLogger(f"{__name__}.yaml_collection"), id_manager
    )

    storage_collection = LabelStorageCollection(
        LabelStore(hass, STORAGE_VERSION, STORAGE_KEY),
        id_manager,
        yaml_collection,
    )

    collection.sync_entity_lifecycle(
        hass, DOMAIN, DOMAIN, entity_component, storage_collection, Label
    )
    collection.sync_entity_lifecycle(
        hass, DOMAIN, DOMAIN, entity_component, yaml_collection, Label
    )

    await yaml_collection.async_load(
        await filter_yaml_data(hass, config.get(DOMAIN, []))
    )
    await storage_collection.async_load()
    collection.DictStorageCollectionWebsocket(
        storage_collection, DOMAIN, DOMAIN, CREATE_FIELDS, UPDATE_FIELDS
    ).async_setup(hass, create_list=False)

    hass.data[DOMAIN] = (yaml_collection, storage_collection, entity_component)

    async def async_reload_yaml(call: ServiceCall) -> None:
        """Reload YAML."""
        conf = await entity_component.async_prepare_reload(skip_reset=True)
        if conf is None:
            return
        await yaml_collection.async_load(
            await filter_yaml_data(hass, conf.get(DOMAIN, []))
        )

    service.async_register_admin_service(
        hass, DOMAIN, SERVICE_RELOAD, async_reload_yaml
    )

    return True


async def filter_yaml_data(hass: HomeAssistant, labels: list[dict]) -> list[dict]:
    """Validate YAML data that we can't validate via schema."""
    filtered = []

    for label_conf in labels:
        filtered.append(label_conf)

    return filtered


class Label(collection.CollectionEntity, RestoreEntity):
    """Entity to show status of entity."""

    _attr_should_poll = False

    def __init__(
        self,
        config: ConfigType,
    ) -> None:
        """Initialize an automation entity."""
        self._config = config

        LOGGER.error("Label.__init__")
        LOGGER.error(config)

    @classmethod
    def from_storage(cls, config: ConfigType) -> Label:
        """Return entity instance initialized from storage."""
        label = cls(config)
        return label

    @classmethod
    def from_yaml(cls, config: ConfigType):
        """Return entity instance initialized from yaml."""
        label = cls(config)
        return label

    async def async_update_config(self, config: ConfigType):
        """Handle when the config is updated."""
        self._config = config

        if self._unsub_track_device is not None:
            self._unsub_track_device()
            self._unsub_track_device = None

        self._update_state()

    @property
    def name(self) -> str:
        return self._config[CONF_NAME]

    @property
    def icon(self) -> str:
        return self._config[CONF_ICON]

    @property
    def unique_id(self):
        return self._config[CONF_ID]

    @property
    def color(self) -> str:
        return self._config[CONF_COLOR]

    @property
    def description(self) -> str:
        return self._config[CONF_DESCRIPTION]


@dataclass(slots=True)
class LabelEntityConfig:
    """Container for prepared automation entity configuration."""

    config_block: ConfigType
    list_no: int
    raw_config: ConfigType | None
    validation_failed: bool
