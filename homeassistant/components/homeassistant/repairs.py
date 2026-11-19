"""Repairs for Home Assistant."""

from homeassistant.components.repairs import (
    ConfirmRepairFlow,
    RepairsFlow,
    RepairsFlowResult,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.sandbox.policy import async_trust_integration

from .const import DOMAIN


class IntegrationNotFoundFlow(RepairsFlow):
    """Handler for an issue fixing flow."""

    def __init__(self, data: dict[str, str]) -> None:
        """Initialize."""
        self.domain = data["domain"]
        self.description_placeholders: dict[str, str] = data

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Handle the first step of a fix flow."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["confirm", "ignore"],
            description_placeholders=self.description_placeholders,
        )

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Handle the confirm step of a fix flow."""
        entries = self.hass.config_entries.async_entries(self.domain)
        for entry in entries:
            await self.hass.config_entries.async_remove(entry.entry_id)
        return self.async_create_entry(data={})

    async def async_step_ignore(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Handle the ignore step of a fix flow."""
        ir.async_get(self.hass).async_ignore(
            DOMAIN, f"integration_not_found.{self.domain}", True
        )
        return self.async_abort(
            reason="issue_ignored",
            description_placeholders=self.description_placeholders,
        )


class OrphanedConfigEntryFlow(RepairsFlow):
    """Handler for an issue fixing flow."""

    def __init__(self, data: dict[str, str]) -> None:
        """Initialize."""
        self.entry_id = data["entry_id"]
        self.description_placeholders = data

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Handle the first step of a fix flow."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["confirm", "ignore"],
            description_placeholders=self.description_placeholders,
        )

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Handle the confirm step of a fix flow."""
        await self.hass.config_entries.async_remove(self.entry_id)
        return self.async_create_entry(data={})

    async def async_step_ignore(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Handle the ignore step of a fix flow."""
        ir.async_get(self.hass).async_ignore(
            DOMAIN, f"orphaned_ignored_entry.{self.entry_id}", True
        )
        return self.async_abort(
            reason="issue_ignored",
            description_placeholders=self.description_placeholders,
        )


class SandboxUnsupportedFlow(RepairsFlow):
    """Let the user allow a custom integration to run inside core.

    This is the consent step: the integration then gets full access to Home
    Assistant, so the flow says so plainly and only acts on an explicit choice.
    """

    def __init__(self, data: dict[str, str]) -> None:
        """Initialize."""
        self.domain = data["domain"]
        self.version = data.get("version") or None
        self.description_placeholders = {
            "domain": self.domain,
            "name": data.get("name", self.domain),
            "version": self.version or "-",
            "reason": data.get("reason", ""),
        }

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Show what allowing means."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["trust", "ignore"],
            description_placeholders=self.description_placeholders,
        )

    async def async_step_trust(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Allow the integration in core, from the next restart on."""
        await async_trust_integration(self.hass, self.domain, self.version)
        return self.async_create_entry(data={})

    async def async_step_ignore(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Keep the integration in the sandbox."""
        ir.async_get(self.hass).async_ignore(
            DOMAIN, f"sandbox_unsupported.{self.domain}", True
        )
        return self.async_abort(
            reason="issue_ignored",
            description_placeholders=self.description_placeholders,
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str] | None
) -> RepairsFlow:
    """Create flow."""

    if issue_id.split(".", maxsplit=1)[0] == "integration_not_found":
        assert data
        return IntegrationNotFoundFlow(data)
    if issue_id.split(".", maxsplit=1)[0] == "orphaned_ignored_entry":
        assert data
        return OrphanedConfigEntryFlow(data)
    if issue_id.split(".", maxsplit=1)[0] == "sandbox_unsupported":
        assert data
        return SandboxUnsupportedFlow(data)
    return ConfirmRepairFlow()
