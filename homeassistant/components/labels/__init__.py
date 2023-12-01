"""Allow to set up simple automation rules via the config file."""
from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import cast

from homeassistant.const import CONF_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.typing import ConfigType

from .config import LabelConfig
from .const import DOMAIN, LOGGER

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up all labels."""
    hass.data[DOMAIN] = component = EntityComponent[LabelEntity](LOGGER, DOMAIN, hass)

    labels_config = await _prepare_labels_config(hass, config)
    entities = await _create_labels_entities(hass, labels_config)
    await component.async_add_entities(entities)

    return True


class LabelEntity(Entity, ABC):
    """Entity to show status of entity."""

    _attr_should_poll = False

    def __init__(
        self,
        label_id: str | None,
        raw_config: ConfigType | None,
    ) -> None:
        """Initialize an automation entity."""
        self.raw_config = raw_config
        self._attr_unique_id = label_id

    @property
    def name(self) -> str:
        return self.raw_config.get("name")

    @property
    def icon(self) -> str:
        return self.raw_config.get("icon")

    @property
    def color(self) -> str:
        return self.raw_config.get("color")

    @property
    def description(self) -> str:
        return self.raw_config.get("description")


@dataclass(slots=True)
class LabelEntityConfig:
    """Container for prepared automation entity configuration."""

    config_block: ConfigType
    list_no: int
    raw_config: ConfigType | None
    validation_failed: bool


async def _prepare_labels_config(
    hass: HomeAssistant,
    config: ConfigType,
) -> list[LabelEntityConfig]:
    """Parse configuration and prepare automation entity configuration."""
    labels_config: list[LabelEntityConfig] = []

    conf: list[ConfigType] = config[DOMAIN]

    for list_no, config_block in enumerate(conf):
        raw_config = cast(LabelConfig, config_block).raw_config
        validation_failed = cast(LabelConfig, config_block).validation_failed
        labels_config.append(
            LabelEntityConfig(
                config_block,
                list_no,
                raw_config,
                validation_failed,
            )
        )

    return labels_config


async def _create_labels_entities(
    hass: HomeAssistant, label_configs: list[LabelEntityConfig]
) -> list[LabelEntity]:
    """Create automation entities from prepared configuration."""
    entities: list[LabelEntity] = []

    for label_config in label_configs:
        config_block = label_config.config_block

        label_id: str | None = config_block.get(CONF_ID)
        entities.append(
            LabelEntity(
                label_id,
                config_block,
            )
        )

    return entities


# @websocket_api.websocket_command({"type": "labels", "entity_id": str})
# def websocket_config(
#     hass: HomeAssistant,
#     connection: websocket_api.ActiveConnection,
#     msg: dict[str, Any],
# ) -> None:
#     """Get automation config."""
#     component: EntityComponent[LabelEntity] = hass.data[DOMAIN]

#     automation = component.get_entity(msg["entity_id"])

#     if automation is None:
#         connection.send_error(
#             msg["id"], websocket_api.const.ERR_NOT_FOUND, "Entity not found"
#         )
#         return

#     connection.send_result(
#         msg["id"],
#         {
#             "config": automation.raw_config,
#         },
#     )
