"""Decide where an integration runs, and remember what the user allowed."""

from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import (
    CONF_CUSTOM_INTEGRATIONS,
    CONF_FILE_ISOLATION,
    CONF_ISOLATION,
    CONF_TRUSTED_INTEGRATIONS,
    CONSENT_STORAGE_KEY,
    CONSENT_STORAGE_VERSION,
    DATA_SANDBOX_POLICY,
    GROUP_CUSTOM,
    ISOLATION_INTEGRATION,
    ISOLATION_SHARED,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class SandboxPolicy:
    """Where integrations run.

    Built-in integrations always run in core. A custom integration runs in a
    sandbox, unless the user trusted it: in configuration.yaml, or through the
    repair flow. Trust given through the repair flow is pinned to the version
    the user saw, so an update has to be approved again.
    """

    isolate_custom: bool = False
    isolation: str = ISOLATION_SHARED
    file_isolation: bool = True
    yaml_trusted: frozenset[str] = frozenset()
    consent: dict[str, dict[str, Any]] = field(default_factory=dict)

    def group_for(self, domain: str, version: str | None) -> str | None:
        """Return the sandbox group of a custom integration, None for core."""
        if not self.isolate_custom or self.is_trusted(domain, version):
            return None
        if self.isolation == ISOLATION_INTEGRATION:
            return f"{GROUP_CUSTOM}.{domain}"
        return GROUP_CUSTOM

    def is_trusted(self, domain: str, version: str | None) -> bool:
        """Return if the user allowed this integration to run in core."""
        if domain in self.yaml_trusted:
            return True
        if (granted := self.consent.get(domain)) is None:
            return False
        return granted.get("version") == version


def _consent_store(hass: HomeAssistant) -> Store[dict[str, Any]]:
    return Store(hass, CONSENT_STORAGE_VERSION, CONSENT_STORAGE_KEY, private=True)


async def async_load_policy(hass: HomeAssistant, conf: dict[str, Any]) -> SandboxPolicy:
    """Create the policy from the configuration and the stored consent."""
    stored = await _consent_store(hass).async_load() or {}
    policy = SandboxPolicy(
        isolate_custom=conf.get(CONF_CUSTOM_INTEGRATIONS, False),
        isolation=conf.get(CONF_ISOLATION, ISOLATION_SHARED),
        file_isolation=conf.get(CONF_FILE_ISOLATION, True),
        yaml_trusted=frozenset(conf.get(CONF_TRUSTED_INTEGRATIONS, [])),
        consent=dict(stored.get("trusted", {})),
    )
    hass.data[DATA_SANDBOX_POLICY] = policy
    return policy


async def async_trust_integration(
    hass: HomeAssistant, domain: str, version: str | None
) -> None:
    """Allow a custom integration to run in core, from the next restart on."""
    store = _consent_store(hass)
    stored = await store.async_load() or {}
    trusted = dict(stored.get("trusted", {}))
    trusted[domain] = {"version": version}
    # The active policy is left alone on purpose: where an integration runs is
    # decided once, before its code is imported, so this applies after a restart.
    await store.async_save({"trusted": trusted})
    _LOGGER.warning(
        "The user allowed custom integration %s (version %s) to run inside core"
        " with full access; this takes effect after a restart",
        domain,
        version,
    )


@callback
def async_get_policy(hass: HomeAssistant) -> SandboxPolicy | None:
    """Return the active policy, if the sandbox is configured."""
    return hass.data.get(DATA_SANDBOX_POLICY)
