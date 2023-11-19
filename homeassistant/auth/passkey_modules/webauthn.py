"""Time-based One Time Password auth module."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional, cast

import voluptuous as vol

from homeassistant.auth.models import User
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.storage import Store

from . import (
    PASSKEY_AUTH_MODULE_SCHEMA,
    PASSKEY_AUTH_MODULES,
    PasskeyAuthModule,
    SetupFlow,
)

_LOGGER = logging.getLogger(__name__)

REQUIREMENTS = ["webauthn==1.11.1"]

CONFIG_SCHEMA = PASSKEY_AUTH_MODULE_SCHEMA.extend({}, extra=vol.PREVENT_EXTRA)

STORAGE_VERSION = 1
STORAGE_KEY = "auth_module.webauthn"
STORAGE_USERS = "users"
STORAGE_USER_ID = "user_id"
STORAGE_OTA_SECRET = "ota_secret"

INPUT_FIELD_CODE = "code"


def _generate_options(
    user_id: str, username: str
) -> tuple[dict[str, str], Optional[bytes]]:
    """Generate a webauthn options."""
    from webauthn import generate_registration_options, options_to_json

    options = generate_registration_options(
        rp_id="localhost",  # TODO: Find actual url
        rp_name="Home Assistant",
        user_id=user_id,
        user_name=username,
    )

    # Hacky method to get an object we can send
    # Simply calling json.dumps() on the options object fails
    json_options = options_to_json(options)
    return (json.loads(json_options), options.challenge)


def _generate_verification(
    credential: dict[str:str], challenge: Optional[bytes]
) -> dict[str, str]:
    """Generate a secret, url, and QR code."""
    from webauthn import options_to_json, verify_registration_response

    registration_verification = verify_registration_response(
        credential=credential,
        expected_challenge=challenge,
        expected_origin="http://localhost:8123",  # TODO: Find actual origin
        expected_rp_id="localhost",  # TODO: Find actual URL
        require_user_verification=True,
    )

    # Hacky method to get an object we can send
    # Simply calling json.dumps() on the options object fails
    json_options = options_to_json(registration_verification)
    return json.loads(json_options)


@PASSKEY_AUTH_MODULES.register("webauthn")
class WebauthnAuthModule(PasskeyAuthModule):
    """Auth module validate time-based one time password."""

    DEFAULT_TITLE = "Time-based One Time Password"
    MAX_RETRY_TIME = 5

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        """Initialize the user data store."""
        super().__init__(hass, config)
        self._users: dict[str, str] | None = None
        self._user_store = Store[dict[str, dict[str, str]]](
            hass, STORAGE_VERSION, STORAGE_KEY, private=True, atomic_writes=True
        )
        self._init_lock = asyncio.Lock()

    @property
    def input_schema(self) -> vol.Schema:
        """Validate login flow input data."""
        return vol.Schema(
            {vol.Required("id"): str},
            extra=vol.ALLOW_EXTRA,
        )

    async def _async_load(self) -> None:
        """Load stored data."""
        async with self._init_lock:
            if self._users is not None:
                return

            if (data := await self._user_store.async_load()) is None:
                data = cast(dict[str, dict[str, str]], {STORAGE_USERS: {}})

            self._users = data.get(STORAGE_USERS, {})

    async def _async_save(self) -> None:
        """Save data."""
        await self._user_store.async_save({STORAGE_USERS: self._users or {}})

    async def async_setup_flow(self, user: str) -> SetupFlow:
        """Return a data entry flow handler for setup module.

        Mfa module should extend SetupFlow
        """
        user = await self.hass.auth.async_get_user(user)
        assert user is not None
        return WebauthnSetupFlow(self, self.input_schema, user)

    async def async_setup_user(self, user_id: str, setup_data: Any) -> str:
        """Set up auth module for user."""
        if self._users is None:
            await self._async_load()

        self._users[user_id] = setup_data.get("passkey")

        await self._async_save()
        return self._users[user_id]

    async def async_depose_user(self, user_id: str) -> None:
        """Depose auth module for user."""
        if self._users is None:
            await self._async_load()

        if self._users.pop(user_id, None):  # type: ignore[union-attr]
            await self._async_save()

    async def async_is_user_setup(self, user_id: str) -> bool:
        """Return whether user is setup."""
        if self._users is None:
            await self._async_load()

        return user_id in self._users  # type: ignore[operator]


class WebauthnSetupFlow(SetupFlow):
    """Handler for the setup flow."""

    def __init__(
        self, auth_module: WebauthnAuthModule, setup_schema: vol.Schema, user: User
    ) -> None:
        """Initialize the setup flow."""
        super().__init__(auth_module, setup_schema, user.id)
        # to fix typing complaint
        self._auth_module: WebauthnAuthModule = auth_module
        self._user = user
        self._challenge: Optional[bytes]

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        options: dict[str, str] = {}

        if user_input:
            hass = self._auth_module.hass
            passkey = await hass.async_add_executor_job(
                _generate_verification,
                user_input,
                self._challenge,
            )
            self._challenge = None
            result = await self._auth_module.async_setup_user(
                self._user_id, {"passkey": passkey}
            )
            _LOGGER.info("Completed")

        else:
            hass = self._auth_module.hass
            (options, self._challenge) = await hass.async_add_executor_job(
                _generate_options,
                str(self._user.id),
                str(self._user.name),
            )

        return self.async_show_form(
            step_id="init",
            data_schema=self._setup_schema,
            description_placeholders={
                "options": options,
            },
            errors=errors,
        )
