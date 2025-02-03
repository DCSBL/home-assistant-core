"""Config flow for HomeWizard."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class HomeWizardConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for P1 meter."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initiated by the user."""
        errors: dict[str, str] | None = None
        if user_input is not None:
            return self.async_create_entry(
                title="HomeWizard",
                data={},
            )

        user_input = user_input or {}
        return self.async_show_form(
            step_id="user",
            errors=errors,
        )
