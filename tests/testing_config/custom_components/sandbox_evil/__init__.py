"""An integration that tries to reach what is not its own.

Every attempt is recorded in the attributes of one sensor, so a test can check
from core what the integration managed to do.
"""

import os
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

DOMAIN = "sandbox_evil"


def _try_read(path: str) -> str:
    try:
        Path(path).read_text()
    except OSError as err:
        return f"blocked: {type(err).__name__}"
    return "read"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Try everything, then report through a sensor."""
    core_config_dir = entry.data["core_config_dir"]
    results: dict[str, str] = {
        "secrets_via_hass": await hass.async_add_executor_job(
            _try_read, hass.config.path("secrets.yaml")
        ),
        "secrets_absolute": await hass.async_add_executor_job(
            _try_read, os.path.join(core_config_dir, "secrets.yaml")
        ),
        "auth_storage": await hass.async_add_executor_job(
            _try_read, os.path.join(core_config_dir, ".storage", "auth")
        ),
        "supervisor_token": "found" if "SUPERVISOR_TOKEN" in os.environ else "blocked",
        "other_states": str(
            len(
                [
                    s
                    for s in hass.states.async_all()
                    if not s.entity_id.startswith("sensor.")
                ]
            )
        ),
        "other_entries": str(
            len([e for e in hass.config_entries.async_entries() if e.domain != DOMAIN])
        ),
    }
    try:
        await hass.services.async_call(
            "input_boolean",
            "turn_on",
            {"entity_id": "input_boolean.alarm"},
            blocking=True,
        )
        results["other_service"] = "called"
    except Exception as err:  # noqa: BLE001
        results["other_service"] = f"blocked: {type(err).__name__}"

    hass.data[DOMAIN] = results
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])
    return True
