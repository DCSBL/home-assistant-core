"""Allow to set up simple automation rules via the config file."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.automation import DOMAIN as AUTOMATION_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_ID,
    CONF_DESCRIPTION,
    CONF_ICON,
    CONF_ID,
    CONF_NAME,
    CONF_TYPE,
)
from homeassistant.core import (
    Event,
    HomeAssistant,
    ServiceCall,
    callback,
    split_entity_id,
)
from homeassistant.helpers import (
    collection,
    config_validation as cv,
    entity_registry as er,
)
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType, StateType

from .const import CONF_COLOR, DOMAIN, LOGGER

ATTR_AUTOMATIONS = "automations"
CONF_AUTOMATIONS = "automations"

STORAGE_KEY = DOMAIN
STORAGE_VERSION = 1

LABEL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_DESCRIPTION): cv.string,
        vol.Optional(CONF_COLOR): cv.string,
        vol.Optional(CONF_ICON): cv.string,
        vol.Optional(CONF_AUTOMATIONS, default=[]): vol.All(
            cv.ensure_list, cv.entities_domain(CONF_AUTOMATIONS)
        ),
    },
)

CREATE_FIELDS = {
    vol.Required(CONF_NAME): vol.All(str, vol.Length(min=1)),
    vol.Optional(CONF_DESCRIPTION): cv.string,
    vol.Optional(CONF_COLOR): cv.string,
    vol.Optional(CONF_ICON): cv.string,
    vol.Optional(CONF_AUTOMATIONS, default=list): vol.All(
        cv.ensure_list, cv.entities_domain(AUTOMATION_DOMAIN)
    ),
}


UPDATE_FIELDS = {
    vol.Optional(CONF_NAME): vol.All(str, vol.Length(min=1)),
    vol.Optional(CONF_DESCRIPTION): cv.string,
    vol.Optional(CONF_COLOR): cv.string,
    vol.Optional(CONF_ICON): cv.string,
    vol.Optional(CONF_AUTOMATIONS, default=list): vol.All(
        cv.ensure_list, cv.entities_domain(AUTOMATION_DOMAIN)
    ),
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
        self.hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            self._entity_registry_updated,
            event_filter=self._entity_registry_filter,
        )

    @callback
    def _entity_registry_filter(self, event: Event) -> bool:
        """Filter entity registry events."""
        return (
            event.data["action"] == "remove"
            and split_entity_id(event.data[ATTR_ENTITY_ID])[0] == AUTOMATION_DOMAIN
        )

    async def _entity_registry_updated(self, event: Event) -> None:
        """Handle entity registry updated."""
        entity_id = event.data[ATTR_ENTITY_ID]
        for label in list(self.data.values()):
            if entity_id not in label[CONF_AUTOMATIONS]:
                continue

            await self.async_update_item(
                label[CONF_ID],
                {
                    CONF_AUTOMATIONS: [
                        devt for devt in label[CONF_AUTOMATIONS] if devt != entity_id
                    ]
                },
            )

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

    hass.data[DOMAIN] = (yaml_collection, storage_collection, entity_component)

    collection.DictStorageCollectionWebsocket(
        storage_collection, DOMAIN, DOMAIN, CREATE_FIELDS, UPDATE_FIELDS
    ).async_setup(hass, create_list=False)

    websocket_api.async_register_command(hass, ws_list_label)

    async def async_reload_yaml(call: ServiceCall) -> None:
        """Reload YAML."""
        conf = await entity_component.async_prepare_reload(skip_reset=True)
        if conf is None:
            return
        await yaml_collection.async_load(
            await filter_yaml_data(hass, conf.get(DOMAIN, []))
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

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return self._config[CONF_NAME]

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._config[CONF_ID]

    @property
    def icon(self) -> str | None:
        """Return the icon to use in the frontend, if any."""
        return self._config.get(CONF_ICON)

    @property
    def state(self) -> StateType:
        """Return the state of the entity."""
        return len(self.automations)

    @property
    def color(self) -> str | None:
        """Return the color to use in the frontend, if any."""
        return self._config.get(CONF_COLOR)

    @property
    def description(self) -> str | None:
        """Return the description of the entity."""
        return self._config.get(CONF_DESCRIPTION)

    @property
    def automations(self):
        """Return the automations of the entity."""
        return self._config[CONF_AUTOMATIONS]

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the entity."""
        data = {}
        data[ATTR_AUTOMATIONS] = self.automations
        return data


@dataclass(slots=True)
class LabelEntityConfig:
    """Container for prepared automation entity configuration."""

    config_block: ConfigType
    list_no: int
    raw_config: ConfigType | None
    validation_failed: bool


@websocket_api.websocket_command({vol.Required(CONF_TYPE): "label/list"})
def ws_list_label(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List persons."""
    yaml, storage, _ = hass.data[DOMAIN]
    connection.send_result(
        msg[ATTR_ID], {"storage": storage.async_items(), "config": yaml.async_items()}
    )
