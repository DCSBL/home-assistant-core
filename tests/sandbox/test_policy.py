"""Tests for where integrations run, and for the user's consent."""

from typing import Any

import pytest
from pytest_homeassistant.common import async_capture_events

from homeassistant.core import HomeAssistant
from homeassistant.core_config import async_process_ha_core_config
from homeassistant.loader import async_get_integration
from homeassistant.sandbox.const import (
    CONSENT_STORAGE_KEY,
    DATA_SANDBOX_CONFIG,
    SANDBOX_PLATFORMS,
)
from homeassistant.sandbox.policy import (
    SandboxPolicy,
    async_load_policy,
    async_trust_integration,
)


def test_group_for() -> None:
    """Custom integrations go to the sandbox unless trusted."""
    assert SandboxPolicy().group_for("hacs", "2.0.0") is None

    policy = SandboxPolicy(isolate_custom=True, yaml_trusted=frozenset({"spook"}))
    assert policy.group_for("hacs", "2.0.0") == "custom"
    assert policy.group_for("spook", "4.0.0") is None

    policy = SandboxPolicy(isolate_custom=True, isolation="integration")
    assert policy.group_for("hacs", "2.0.0") == "custom.hacs"


def test_consent_is_pinned_to_version() -> None:
    """Consent given in the UI lapses when the integration is updated."""
    policy = SandboxPolicy(isolate_custom=True, consent={"hacs": {"version": "2.0.0"}})
    assert policy.group_for("hacs", "2.0.0") is None
    assert policy.group_for("hacs", "2.0.1") == "custom"


async def test_trust_is_stored(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Trust survives a restart, and only applies after one."""
    policy = await async_load_policy(hass, {"custom_integrations": True})
    await async_trust_integration(hass, "hacs", "2.0.0")
    assert hass_storage[CONSENT_STORAGE_KEY]["data"] == {
        "trusted": {"hacs": {"version": "2.0.0"}}
    }
    assert policy.group_for("hacs", "2.0.0") == "custom"

    policy = await async_load_policy(hass, {"custom_integrations": True})
    assert policy.group_for("hacs", "2.0.0") is None


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (
            {"sandbox": {"custom_integrations": True}},
            {
                "custom_integrations": True,
                "trusted_integrations": [],
                "isolation": "shared",
                "file_isolation": True,
            },
        ),
        (
            {
                "sandbox": {
                    "custom_integrations": True,
                    "trusted_integrations": "spook",
                    "isolation": "integration",
                }
            },
            {
                "custom_integrations": True,
                "trusted_integrations": ["spook"],
                "isolation": "integration",
                "file_isolation": True,
            },
        ),
    ],
)
async def test_core_config(
    hass: HomeAssistant, config: dict[str, Any], expected: dict[str, Any]
) -> None:
    """The sandbox is configured in the homeassistant section."""
    await async_process_ha_core_config(hass, config)
    assert hass.data[DATA_SANDBOX_CONFIG] == expected


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_loader_hands_out_stand_ins(hass: HomeAssistant) -> None:
    """Core never imports the code of a sandboxed integration."""
    await async_load_policy(hass, {"custom_integrations": True})
    integration = await async_get_integration(hass, "sandbox_demo")
    assert integration.sandbox_group == "custom"

    component = await integration.async_get_component()
    assert type(component).__name__ == "SandboxProxyComponent"
    assert component.platforms == ["sensor", "switch"]
    assert integration.get_component() is component

    assert integration.platforms_exists(["sensor", "switch", "diagnostics"]) == [
        "sensor",
        "switch",
    ]
    assert integration.platforms_exists(["config_flow"]) == ["config_flow"]
    sensor = await integration.async_get_platform("sensor")
    assert type(sensor).__name__ == "SandboxProxyPlatform"
    assert integration.get_platform("sensor") is sensor
    with pytest.raises(ModuleNotFoundError):
        await integration.async_get_platform("diagnostics")

    import sys  # noqa: PLC0415

    assert "custom_components.sandbox_demo" not in sys.modules
    assert "custom_components.sandbox_demo.sensor" not in sys.modules
    assert {"sensor", "switch"} <= SANDBOX_PLATFORMS


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_new_config_flow_is_refused(hass: HomeAssistant) -> None:
    """Config flows do not run in a sandbox yet, they abort."""
    await async_load_policy(hass, {"custom_integrations": True})
    events = async_capture_events(hass, "sandbox_demo_started")
    result = await hass.config_entries.flow.async_init(
        "sandbox_demo", context={"source": "user"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "sandbox_config_flow_not_supported"
    result = await hass.config_entries.flow.async_init(
        "sandbox_demo", context={"source": "zeroconf"}, data={}
    )
    assert result["type"] == "abort"
    assert not events
