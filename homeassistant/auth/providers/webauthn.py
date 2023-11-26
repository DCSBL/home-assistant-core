"""Home Assistant auth provider."""
from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
import logging
from typing import Any, cast

import bcrypt
import voluptuous as vol

from homeassistant.const import CONF_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.auth.models import User

from homeassistant.data_entry_flow import FlowResultType

from ..models import Credentials, UserMeta
from . import AUTH_PROVIDER_SCHEMA, AUTH_PROVIDERS, AuthProvider, LoginFlow

import asyncio
import json
import logging
from typing import Any, Optional, cast

import voluptuous as vol

from homeassistant.auth.models import User
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.storage import Store

from webauthn.authentication.verify_authentication_response import (
    VerifiedAuthentication,
)

_LOGGER = logging.getLogger(__name__)

REQUIREMENTS = ["webauthn==1.11.1"]

STORAGE_VERSION = 1
STORAGE_KEY = "auth_provider.webauthn"


def _disallow_id(conf: dict[str, Any]) -> dict[str, Any]:
    """Disallow ID in config."""
    if CONF_ID in conf:
        raise vol.Invalid("ID is not allowed for the homeassistant auth provider.")

    return conf


CONFIG_SCHEMA = vol.All(AUTH_PROVIDER_SCHEMA, _disallow_id)


@callback
def async_get_provider(hass: HomeAssistant) -> WebauthnAuthProvider:
    """Get the provider."""
    for prv in hass.auth.auth_providers:
        if prv.type == "webauthn":
            return cast(WebauthnAuthProvider, prv)

    raise RuntimeError("Provider not found")


class InvalidAuth(HomeAssistantError):
    """Raised when we encounter invalid authentication."""


class InvalidUser(HomeAssistantError):
    """Raised when invalid user is specified.

    Will not be raised when validating authentication.
    """


class Data:
    """Hold the user data."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the user data store."""
        self.hass = hass
        self._store = Store[dict[str, list[dict[str, str]]]](
            hass, STORAGE_VERSION, STORAGE_KEY, private=True, atomic_writes=True
        )
        self._data: dict[str, list[dict[str, str]]] | None = None
        self._challenge: bytes | None = None

    async def async_load(self) -> None:
        """Load stored data."""
        if (data := await self._store.async_load()) is None:
            data = cast(dict[str, list[dict[str, str]]], {"users": []})

        self._data = data

    @property
    def users(self) -> list[dict[str, str]]:
        """Return users."""
        assert self._data is not None
        return self._data["users"]

    # def validate_login(self, username: str, password: str) -> None:
    #     """Validate a username and password.

    #     Raises InvalidAuth if auth invalid.
    #     """
    #     username = self.normalize_username(username)
    #     dummy = b"$2b$12$CiuFGszHx9eNHxPuQcwBWez4CwDTOcLTX5CbOpV6gef2nYuXkY7BO"
    #     found = None

    #     # Compare all users to avoid timing attacks.
    #     for user in self.users:
    #         if self.normalize_username(user["username"]) == username:
    #             found = user

    #     if found is None:
    #         # check a hash to make timing the same as if user was found
    #         bcrypt.checkpw(b"foo", dummy)
    #         raise InvalidAuth

    #     user_hash = base64.b64decode(found["password"])

    #     # bcrypt.checkpw is timing-safe
    #     if not bcrypt.checkpw(password.encode(), user_hash):
    #         raise InvalidAuth

    # def hash_password(self, password: str, for_storage: bool = False) -> bytes:
    #     """Encode a password."""
    #     hashed: bytes = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12))

    #     if for_storage:
    #         hashed = base64.b64encode(hashed)
    #     return hashed

    def add_auth(self, username: str, password: str) -> None:
        """Add a new authenticated user/pass."""
        username = self.normalize_username(username)

        if any(
            self.normalize_username(user["username"]) == username for user in self.users
        ):
            raise InvalidUser

        self.users.append(
            {
                "username": username,
                "password": self.hash_password(password, True).decode(),
            }
        )

    async def async_generate_register_options(
        self, user_id: str, username: str
    ) -> tuple[dict[str, str], Optional[bytes]]:
        from webauthn import generate_registration_options, options_to_json

        options = generate_registration_options(
            rp_id="localhost",  # TODO: Find actual url
            rp_name="Home Assistant",
            user_id=user_id,
            user_name=username,
        )

        self._challenge = options.challenge

        # Hacky method to get an object we can send
        # Simply calling json.dumps() on the options object fails
        json_options = options_to_json(options)
        return json.loads(json_options)

    async def async_generate_verification(
        self, user_id: str, credential: dict[str:str]
    ) -> dict[str, str]:
        """Generate a secret, url, and QR code."""
        from webauthn import options_to_json, verify_registration_response

        challenge = self._challenge
        self._challenge = None

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
        passkey = json.loads(json_options)

        self.users.append(
            {
                "user_id": user_id,
                "credential_id": passkey["credentialId"],
                "credential_public_key": passkey["credentialPublicKey"],
            }
        )
        await self.async_save()

        return passkey

    async def async_generate_auth_options(
        self,
    ) -> tuple[dict[str, str], Optional[bytes]]:
        from webauthn import generate_authentication_options, options_to_json

        options = generate_authentication_options(
            rp_id="localhost",  # TODO: Find actual url
        )

        self._challenge = options.challenge

        # Hacky method to get an object we can send
        # Simply calling json.dumps() on the options object fails
        json_options = options_to_json(options)
        return json.loads(json_options)

    async def async_validate_login(
        self, credential: dict[str:str]
    ) -> VerifiedAuthentication:
        from webauthn import verify_authentication_response, options_to_json

        challenge = self._challenge
        self._challenge = None

        # Find credential by ID
        found = None
        for user in self.users:
            if user["credential_id"] == credential["id"]:
                found = user

        if found is None:
            raise InvalidAuth

        return verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_origin="http://localhost:8123",  # TODO: Find actual origin
            expected_rp_id="localhost",  # TODO: Find actual URL
            credential_public_key=found["credential_public_key"],
            credential_current_sign_count=1,
            require_user_verification=True,
        )

    async def async_save(self) -> None:
        """Save data."""
        if self._data is not None:
            await self._store.async_save(self._data)


@AUTH_PROVIDERS.register("webauthn")
class WebauthnAuthProvider(AuthProvider):
    """Auth provider based on a local storage of users in Home Assistant config dir."""

    DEFAULT_TITLE = "Home Assistant Local"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize an Home Assistant auth provider."""
        super().__init__(*args, **kwargs)
        self.data: Data | None = None
        self._init_lock = asyncio.Lock()

    async def async_initialize(self) -> None:
        """Initialize the auth provider."""
        async with self._init_lock:
            if self.data is not None:
                return

            data = Data(self.hass)
            await data.async_load()
            self.data = data

    async def async_login_flow(self, context: dict[str, Any] | None) -> LoginFlow:
        """Return a flow to login."""
        return HassLoginFlow(self)

    async def async_generate_webauthn_options(
        self, user: User
    ) -> tuple[dict[str, str], Optional[bytes]]:
        """Generate webauthn options."""
        if self.data is None:
            await self.async_initialize()
            assert self.data is not None

        return await self.data.async_generate_register_options(user.id, user.name)

    async def async_add_auth(self, user: User, credential: dict[str:str]) -> bool:
        """Validate webauthn registration."""
        if self.data is None:
            await self.async_initialize()
            assert self.data is not None

        ## TODO: Catch exceptions
        await self.data.async_generate_verification(user.id, credential)

        return True

    async def async_generate_authentication_options(self) -> None:
        """Validate a username and password."""
        if self.data is None:
            await self.async_initialize()
            assert self.data is not None

        return await self.data.async_generate_auth_options()

    async def async_login(self, credentials: dict[str:str]) -> None:
        """Validate a username and password."""
        if self.data is None:
            await self.async_initialize()
            assert self.data is not None

        return await self.data.async_validate_login(credentials)


class HassLoginFlow(LoginFlow):
    """Handler for the login flow."""

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Handle the step of the form."""
        errors = {}

        if user_input is not None:
            try:
                await cast(WebauthnAuthProvider, self._auth_provider).async_login()
            except InvalidAuth:
                errors["base"] = "invalid_auth"

            if not errors:
                return await self.async_finish(user_input)

        options = await cast(
            WebauthnAuthProvider, self._auth_provider
        ).async_generate_authentication_options()

        # return self.async_show_form(
        #     step_id="init",
        #     data_schema=vol.Schema(
        #         {
        #             vol.Required("username"): str,
        #         }
        #     ),
        #     description_placeholders=options,
        #     errors=errors,
        # )
        return self.async_external_step(step_id="init", url="test.nl", description_placeholders=options)
