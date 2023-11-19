"""Helpers to setup multi-factor auth module."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import data_entry_flow
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
import homeassistant.helpers.config_validation as cv

WS_TYPE_PASSKEY_REGISTER = "auth/passkey_register"
SCHEMA_WS_PASSKEY_REGISTER = vol.All(
    websocket_api.BASE_COMMAND_MESSAGE_SCHEMA.extend(
        {
            vol.Required("type"): WS_TYPE_PASSKEY_REGISTER,
            vol.Exclusive("passkey_module_id", "module_or_flow_id"): str,
            vol.Exclusive("flow_id", "module_or_flow_id"): str,
            vol.Optional("credential", "module_or_flow_id"): object,
        }
    ),
    cv.has_at_least_one_key("passkey_module_id", "flow_id"),
)

DATA_SETUP_FLOW_MGR = "auth_passkey_setup_flow_manager"

_LOGGER = logging.getLogger(__name__)


class PasskeyFlowManager(data_entry_flow.FlowManager):
    """Manage multi factor authentication flows."""

    async def async_create_flow(  # type: ignore[override]
        self,
        handler_key: str,
        *,
        context: dict[str, Any],
        data: dict[str, Any],
    ) -> data_entry_flow.FlowHandler:
        """Create a setup flow. handler is a passkey module."""
        passkey_module = self.hass.auth.get_auth_passkey_module(handler_key)
        if passkey_module is None:
            raise ValueError(f"Passkey module {handler_key} is not found")

        user_id = data.pop("user_id")
        return await passkey_module.async_setup_flow(user_id)

    async def async_finish_flow(
        self, flow: data_entry_flow.FlowHandler, result: data_entry_flow.FlowResult
    ) -> data_entry_flow.FlowResult:
        """Complete an mfs setup flow."""
        _LOGGER.debug("flow_result: %s", result)
        return result


async def async_setup(hass: HomeAssistant) -> None:
    """Init passkey setup flow manager."""
    hass.data[DATA_SETUP_FLOW_MGR] = PasskeyFlowManager(hass)

    websocket_api.async_register_command(
        hass,
        WS_TYPE_PASSKEY_REGISTER,
        websocket_passkey_register_request,
        SCHEMA_WS_PASSKEY_REGISTER,
    )


@callback
@websocket_api.ws_require_user(allow_system_user=False)
def websocket_passkey_register_request(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return a setup flow for passkey auth module."""

    async def async_setup_flow(msg: dict[str, Any]) -> None:
        """Return a setup flow for mfa auth module."""
        flow_manager: PasskeyFlowManager = hass.data[DATA_SETUP_FLOW_MGR]

        if (flow_id := msg.get("flow_id")) is not None:
            _LOGGER.warning(msg.get("credential"))
            result = await flow_manager.async_configure(flow_id, msg.get("credential"))
            _LOGGER.warning("Done!")
            # connection.send_message(
            #     websocket_api.result_message(msg["id"], _prepare_result_json(result))
            # )
            return

        passkey_module_id = msg["passkey_module_id"]
        if hass.auth.get_auth_passkey_module(passkey_module_id) is None:
            connection.send_message(
                websocket_api.error_message(
                    msg["id"],
                    "no_module",
                    f"Passkey module {passkey_module_id} is not found",
                )
            )
            return

        result = await flow_manager.async_init(
            passkey_module_id, data={"user_id": connection.user.id}
        )

        _LOGGER.info(result)
        connection.send_message(
            websocket_api.result_message(
                msg["id"],
                {
                    "flow_id": result["flow_id"],
                    "options": result["description_placeholders"]["options"],
                },
            )
        )

    hass.async_create_task(async_setup_flow(msg))
