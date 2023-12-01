"""Config validation helper for the automation integration."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import voluptuous as vol

from homeassistant.config import config_without_domain
from homeassistant.const import CONF_DESCRIPTION, CONF_ICON, CONF_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_per_platform, config_validation as cv, script
from homeassistant.helpers.typing import ConfigType

from .const import CONF_COLOR, DOMAIN

PACKAGE_MERGE_HINT = "list"

PLATFORM_SCHEMA = vol.All(
    script.make_script_schema(
        {
            # str on purpose
            CONF_ID: str,
            vol.Optional(CONF_DESCRIPTION): cv.string,
            vol.Optional(CONF_COLOR): cv.string,
            vol.Optional(CONF_ICON): cv.string,
        },
        script.SCRIPT_MODE_SINGLE,
    ),
)


async def _async_validate_config_item(
    hass: HomeAssistant,
    config: ConfigType,
    raise_on_errors: bool,
    warn_on_errors: bool,
) -> LabelConfig:
    with suppress(ValueError):
        raw_config = dict(config)

    label_config = LabelConfig(raw_config)
    label_config.raw_config = raw_config

    return label_config


class LabelConfig(dict):
    """Dummy class to allow adding attributes."""

    raw_config: dict[str, Any] | None = None
    validation_failed: bool = False


async def _try_async_validate_config_item(
    hass: HomeAssistant,
    config: dict[str, Any],
) -> LabelConfig | None:
    """Validate config item."""
    try:
        return await _async_validate_config_item(hass, config, False, True)
    except (vol.Invalid, HomeAssistantError):
        return None


async def async_validate_config_item(
    hass: HomeAssistant,
    config_key: str,
    config: dict[str, Any],
) -> LabelConfig | None:
    """Validate config item, called by EditLabelConfigView."""
    return await _async_validate_config_item(hass, config, True, False)


async def async_validate_config(hass: HomeAssistant, config: ConfigType) -> ConfigType:
    """Validate config."""
    labels = list(
        filter(
            lambda x: x is not None,
            await asyncio.gather(
                *(
                    _try_async_validate_config_item(hass, p_config)
                    for _, p_config in config_per_platform(config, DOMAIN)
                )
            ),
        )
    )

    # Create a copy of the configuration with all config for current
    # component removed and add validated config back in.
    config = config_without_domain(config, DOMAIN)
    config[DOMAIN] = labels

    return config
