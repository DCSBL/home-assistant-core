"""Tests for REST commands set up from a config entry."""

from typing import Any

from multidict import CIMultiDict
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.test_util.aiohttp import AiohttpClientMocker

from homeassistant.components.rest_command.const import DOMAIN, SUBENTRY_TYPE_COMMAND
from homeassistant.config_entries import (
    SOURCE_IMPORT,
    ConfigEntryState,
    ConfigSubentryData,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from .conftest import TEST_URL

SERVER_URL = "https://example.com"


def _entry(
    server: dict[str, Any] | None = None, **commands: dict[str, Any]
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=SERVER_URL,
        data={"url": SERVER_URL, **(server or {})},
        subentries_data=[
            ConfigSubentryData(
                data={"action": action, **command},
                subentry_type=SUBENTRY_TYPE_COMMAND,
                title=action,
            )
            for action, command in commands.items()
        ],
    )


async def test_call_command_from_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test a command from the UI renders its templates with the action data."""
    entry = _entry(
        {
            "additional": {
                "headers": {"Authorization": "Bearer token", "X-Api-Version": "1"}
            }
        },
        open_door={
            "path": "/{{ door }}",
            "method": "post",
            "payload": '{"door": "{{ door }}"}',
            "additional": {
                "headers": {"X-Door": "{{ door }}", "x-api-version": "2"},
                "content_type": "application/json",
            },
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    aioclient_mock.post(
        TEST_URL + "front",
        content=b'{"ok": true}',
        headers={"Content-Type": "application/json"},
    )
    response = await hass.services.async_call(
        DOMAIN,
        "open_door",
        {"door": "front"},
        blocking=True,
        return_response=True,
    )

    assert response["content"] == {"ok": True}
    assert len(aioclient_mock.mock_calls) == 1
    assert aioclient_mock.mock_calls[0][2] == b'{"door": "front"}'
    headers = aioclient_mock.mock_calls[0][3]
    assert headers["X-Door"] == "front"
    assert headers["Authorization"] == "Bearer token"
    assert CIMultiDict(headers).getall("X-Api-Version") == ["2"]


async def test_unload_removes_actions(hass: HomeAssistant) -> None:
    """Test unloading the entry removes its actions and keeps reload."""
    entry = _entry(from_ui={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, "from_ui")

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not hass.services.has_service(DOMAIN, "from_ui")
    assert hass.services.has_service(DOMAIN, "reload")


async def test_invalid_stored_command(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry
) -> None:
    """Test a stored command that no longer validates does not block the others."""
    entry = _entry(broken={"path": "/{{ door"}, working={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.services.has_service(DOMAIN, "broken")
    assert hass.services.has_service(DOMAIN, "working")
    broken = next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.data["action"] == "broken"
    )
    issue_id = f"invalid_command_{broken.subentry_id}"
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders["action"] == "rest_command.broken"

    # Deleting the command removes its issue
    hass.config_entries.async_remove_subentry(entry, broken.subentry_id)
    await hass.async_block_till_done()
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_import_from_yaml(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test the commands from YAML are imported, grouped per server."""
    device = {
        "headers": {"Authorization": "Bearer token", "X-Api-Version": "2"},
        "verify_ssl": False,
    }
    assert await async_setup_component(
        hass,
        DOMAIN,
        {
            DOMAIN: {
                "backlight_on": {
                    **device,
                    "url": SERVER_URL + "/api/state",
                    "method": "put",
                    "headers": {
                        **device["headers"],
                        "Content-Type": "application/json",
                    },
                    "payload": '{"backlight_on": true}',
                },
                "reboot": {
                    **device,
                    "url": SERVER_URL + "/api/system/reboot",
                    "method": "put",
                },
                "other_server": {"url": "http://{{ host }}/door"},
            }
        },
    )
    await hass.async_block_till_done()

    entries = {
        entry.title: entry for entry in hass.config_entries.async_entries(DOMAIN)
    }
    assert set(entries) == {SERVER_URL, "http://"}
    device_entry = entries[SERVER_URL]
    assert device_entry.source == SOURCE_IMPORT
    assert device_entry.state is ConfigEntryState.LOADED
    assert device_entry.data == {
        "url": SERVER_URL,
        "auth": {},
        "additional": {
            "headers": {"Authorization": "Bearer token", "X-Api-Version": "2"},
            "timeout": 10,
            "verify_ssl": False,
            "insecure_cipher": False,
        },
    }
    assert {
        subentry.title: subentry.data for subentry in device_entry.subentries.values()
    } == {
        "backlight_on": {
            "action": "backlight_on",
            "path": "/api/state",
            "method": "put",
            "payload": '{"backlight_on": true}',
            "additional": {
                "headers": {"Content-Type": "application/json"},
                "skip_url_encoding": False,
            },
        },
        "reboot": {
            "action": "reboot",
            "path": "/api/system/reboot",
            "method": "put",
            "additional": {"skip_url_encoding": False},
        },
    }
    other = next(iter(entries["http://"].subentries.values()))
    assert other.data["path"] == "{{ host }}/door"
    assert issue_registry.async_get_issue(DOMAIN, "yaml_imported") is not None
    assert not hass.config_entries.subentries.async_progress()

    aioclient_mock.put(SERVER_URL + "/api/state")
    aioclient_mock.get("http://example.org/door")
    await hass.services.async_call(DOMAIN, "backlight_on", blocking=True)
    await hass.services.async_call(
        DOMAIN, "other_server", {"host": "example.org"}, blocking=True
    )
    headers = aioclient_mock.mock_calls[0][3]
    assert headers["Authorization"] == "Bearer token"
    assert headers["Content-Type"] == "application/json"
    assert len(aioclient_mock.mock_calls) == 2


async def test_import_only_new_commands(hass: HomeAssistant) -> None:
    """Test an import keeps the commands that were imported before."""
    entry = _entry(existing={"path": "/ui"})
    entry.add_to_hass(hass)

    assert await async_setup_component(
        hass,
        DOMAIN,
        {
            DOMAIN: {
                "existing": {"url": SERVER_URL + "/yaml"},
                "new": {"url": SERVER_URL + "/new"},
            }
        },
    )
    await hass.async_block_till_done()

    commands = {
        subentry.data["action"]: subentry.data
        for config_entry in hass.config_entries.async_entries(DOMAIN)
        for subentry in config_entry.subentries.values()
    }
    assert commands["existing"]["path"] == "/ui"
    assert commands["new"]["path"] == "/new"
    assert hass.services.has_service(DOMAIN, "existing")
    assert hass.services.has_service(DOMAIN, "new")


async def test_no_import_without_yaml(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry
) -> None:
    """Test no entry is created when there is no YAML."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    assert not hass.config_entries.async_entries(DOMAIN)
    assert issue_registry.async_get_issue(DOMAIN, "yaml_imported") is None
