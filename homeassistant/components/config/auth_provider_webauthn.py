"""Offer API to configure the Home Assistant auth provider."""
from typing import Any

import voluptuous as vol

from homeassistant.auth.providers import webauthn as auth_webauthn
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import Unauthorized
import homeassistant.helpers.config_validation as cv

import logging

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass):
    """Enable the Home Assistant views."""
    websocket_api.async_register_command(hass, websocket_register)
    websocket_api.async_register_command(hass, websocket_register_validate)
    return True


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/auth_provider/passkey/register",
    },
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_register(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create credentials and attach to a user."""
    _LOGGER.warning(msg)
    _LOGGER.warning(connection)

    provider = auth_webauthn.async_get_provider(hass)

    options = await provider.async_generate_webauthn_options(connection.user)

    connection.send_result(
        msg["id"],
        {
            "options": options,
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/auth_provider/passkey/register_validate",
        vol.Required("credential"): object,
    },
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_register_validate(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create credentials and attach to a user."""
    _LOGGER.warning(msg)
    _LOGGER.warning(connection)

    provider = auth_webauthn.async_get_provider(hass)

    result = await provider.async_add_auth(connection.user, msg["credential"])

    connection.send_result(
        msg["id"],
        {
            "result": result,
        },
    )
