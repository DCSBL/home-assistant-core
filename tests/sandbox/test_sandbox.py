"""End-to-end tests: real custom integrations in a real sandbox process."""

import asyncio
from collections.abc import AsyncGenerator, Callable
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant.common import MockConfigEntry, async_capture_events

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.loader import async_get_integration
from homeassistant.sandbox import confine, manager as sandbox_manager
from homeassistant.sandbox.const import DATA_SANDBOX_MANAGER
from homeassistant.setup import async_setup_component


async def _wait_for(check: Callable[[], Any], timeout: float = 30) -> Any:
    """Wait until check returns something truthy; the sandbox is a real process."""
    async with asyncio.timeout(timeout):
        while not (result := check()):
            await asyncio.sleep(0.05)
    return result


@pytest.fixture
async def sandbox_config() -> dict[str, Any]:
    """Return the sandbox configuration of a test."""
    return {"custom_integrations": True, "file_isolation": False}


@pytest.fixture
async def sandbox(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    tmp_path: Path,
    sandbox_config: dict[str, Any],
) -> AsyncGenerator[None]:
    """Turn the sandbox on, and stop its processes after the test."""
    with patch.object(sandbox_manager, "SANDBOX_DATA_DIR", str(tmp_path / "sandbox")):
        await sandbox_manager.async_setup(hass, sandbox_config)
        yield
        if (manager := hass.data.get(DATA_SANDBOX_MANAGER)) is not None:
            await manager.async_shutdown()


@pytest.mark.usefixtures("sandbox")
async def test_integration_runs_unchanged_in_sandbox(hass: HomeAssistant) -> None:
    """Entities, services, events and entry updates work through the sandbox."""
    integration = await async_get_integration(hass, "sandbox_demo")
    assert integration.sandbox_group == "custom"

    started = async_capture_events(hass, "sandbox_demo_started")
    spoofed = async_capture_events(hass, "zwave_js_value_notification")
    entry = MockConfigEntry(
        domain="sandbox_demo", data={"host": "1.2.3.4"}, version=1, minor_version=1
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED

    power = await _wait_for(lambda: hass.states.get("sensor.demo_meter_power"))
    assert power.state == "1234.5"
    assert power.attributes["unit_of_measurement"] == "W"
    assert power.attributes["device_class"] == "power"
    assert power.attributes["state_class"] == "measurement"
    assert power.attributes["phase"] == 1
    assert power.attributes["friendly_name"] == "Demo meter Power"

    # Registered in core's registries, under the integration's own name.
    entity_registry = er.async_get(hass)
    power_entry = entity_registry.async_get("sensor.demo_meter_power")
    assert power_entry.platform == "sandbox_demo"
    assert power_entry.unique_id == f"{entry.entry_id}_power"
    device = dr.async_get(hass).async_get(power_entry.device_id)
    assert device.identifiers == {("sandbox_demo", entry.entry_id)}
    assert device.manufacturer == "Sandbox"

    # Disabled by default, as the integration says, but known to core.
    pid_entry = await _wait_for(
        lambda: entity_registry.async_get_entity_id(
            "sensor", "sandbox_demo", f"{entry.entry_id}_pid"
        )
    )
    assert (
        entity_registry.async_get(pid_entry).disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )

    # Entity services go to the real entity in the sandbox.
    relay = await _wait_for(lambda: hass.states.get("switch.demo_meter_relay"))
    assert relay.state == STATE_OFF
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.demo_meter_relay"}, blocking=True
    )
    await _wait_for(
        lambda: hass.states.get("switch.demo_meter_relay").state == STATE_ON
    )

    # A service of the integration's own domain, answered from the sandbox.
    response = await hass.services.async_call(
        "sandbox_demo", "ping", {"value": 42}, blocking=True, return_response=True
    )
    assert response["pong"] == 42
    assert response["pid"] != os.getpid()

    # Events in the integration's own domain arrive, others do not.
    await _wait_for(lambda: started)
    assert started[0].data == {"entry_id": entry.entry_id}
    assert not spoofed

    # The migration ran in the sandbox and was saved in core.
    assert entry.minor_version == 2
    assert entry.data == {"host": "1.2.3.4", "migrated": True}

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get("sensor.demo_meter_power").state == "unavailable"


@pytest.mark.usefixtures("sandbox")
async def test_unsupported_dependency_asks_for_consent(hass: HomeAssistant) -> None:
    """An integration that needs a core building block raises a repair issue."""
    assert not await async_setup_component(hass, "sandbox_needs_core", {})
    issue = ir.async_get(hass).async_get_issue(
        "homeassistant", "sandbox_unsupported.sandbox_needs_core"
    )
    assert issue is not None
    assert issue.is_fixable
    assert issue.data["version"] == "2.0.0"
    assert "input_boolean" in issue.translation_placeholders["reason"]


@pytest.mark.usefixtures("sandbox")
@pytest.mark.parametrize(
    "sandbox_config",
    [
        {
            "custom_integrations": True,
            "file_isolation": False,
            "trusted_integrations": ["sandbox_demo"],
        }
    ],
)
async def test_trusted_integration_runs_in_core(hass: HomeAssistant) -> None:
    """A trusted integration is loaded in core as before."""
    assert (await async_get_integration(hass, "sandbox_demo")).sandbox_group is None
    assert (await async_get_integration(hass, "sandbox_evil")).sandbox_group == "custom"
    assert (await async_get_integration(hass, "sensor")).sandbox_group is None


async def _run_evil(hass: HomeAssistant, config_dir: Path) -> dict[str, Any]:
    """Run the evil integration next to a core entity and return what it reached."""
    (config_dir / ".storage").mkdir(parents=True)
    (config_dir / "secrets.yaml").write_text("password: hunter2\n")
    (config_dir / ".storage" / "auth").write_text("{}")
    hass.config.config_dir = str(config_dir)
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"alarm": {}}}
    )
    entry = MockConfigEntry(
        domain="sandbox_evil", data={"core_config_dir": hass.config.config_dir}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    report = await _wait_for(lambda: hass.states.get("sensor.evil_report"))
    assert hass.states.get("input_boolean.alarm").state == STATE_OFF
    return dict(report.attributes)


@pytest.mark.usefixtures("sandbox")
async def test_evil_integration_is_contained(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without file isolation the process boundary still holds."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "do-not-leak")
    report = await _run_evil(hass, tmp_path / "config")
    # Only the operating system can stop this, see the next test.
    assert report["secrets_absolute"] == "read"
    # Through its own config dir the sandbox only sees its private data.
    assert report["secrets_via_hass"].startswith("blocked")
    assert report["supervisor_token"] == "blocked"
    assert report["other_states"] == "0"
    assert report["other_entries"] == "0"
    assert report["other_service"] == "blocked: ServiceNotFound"


@pytest.mark.skipif(not confine.can_confine(), reason="needs root on Linux")
@pytest.mark.usefixtures("sandbox")
@pytest.mark.parametrize(
    "sandbox_config", [{"custom_integrations": True, "file_isolation": True}]
)
async def test_evil_integration_cannot_read_files(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With file isolation the config directory of core is out of reach."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "do-not-leak")
    # The sandbox user must be able to reach the config dir, as with /config.
    # The temporary directories of pytest are only accessible to their owner.
    config_parent = Path(tempfile.mkdtemp(prefix="sandbox-test-", dir="/tmp"))
    config_parent.chmod(0o755)
    try:
        report = await _run_evil(hass, config_parent / "config")
    finally:
        shutil.rmtree(config_parent)
    manager = hass.data[DATA_SANDBOX_MANAGER]
    sandbox = await manager.async_get_sandbox("custom")
    assert sandbox.confinement == confine.CONFINEMENT_FULL
    assert report["secrets_via_hass"].startswith("blocked")
    assert report["secrets_absolute"].startswith("blocked")
    assert report["auth_storage"].startswith("blocked")
    assert report["supervisor_token"] == "blocked"
    assert report["other_service"] == "blocked: ServiceNotFound"
