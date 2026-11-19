"""Support for exposing regular REST commands as services."""

from collections.abc import Mapping
import re
from typing import Any

import probatio

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry, ConfigSubentryData
from homeassistant.const import (
    CONF_AUTHENTICATION,
    CONF_HEADERS,
    CONF_METHOD,
    CONF_PASSWORD,
    CONF_PATH,
    CONF_PAYLOAD,
    CONF_TIMEOUT,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    HTTP_BASIC_AUTHENTICATION,
    HTTP_DIGEST_AUTHENTICATION,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.template import Template
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_ACTION,
    CONF_CONTENT_TYPE,
    CONF_INSECURE_CIPHER,
    CONF_SKIP_URL_ENCODING,
    DOMAIN,
    SECTION_ADDITIONAL,
    SECTION_AUTH,
    SUBENTRY_TYPE_COMMAND,
)
from .services import async_register_rest_command, async_setup_services

DEFAULT_TIMEOUT = 10
DEFAULT_METHOD = "get"
DEFAULT_VERIFY_SSL = True

SUPPORT_REST_METHODS = ["get", "patch", "post", "put", "delete"]


COMMAND_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_URL): cv.template,
        probatio.Optional(CONF_METHOD, default=DEFAULT_METHOD): probatio.All(
            probatio.Lower, probatio.In(SUPPORT_REST_METHODS)
        ),
        probatio.Optional(CONF_HEADERS): probatio.Schema({cv.string: cv.template}),
        probatio.Optional(CONF_AUTHENTICATION): probatio.In(
            [HTTP_BASIC_AUTHENTICATION, HTTP_DIGEST_AUTHENTICATION]
        ),
        # A colon cannot be encoded into basic credentials, RFC 7617#section-2
        probatio.Inclusive(CONF_USERNAME, "authentication"): probatio.All(
            cv.string, probatio.Match(r"^[^:]*$")
        ),
        probatio.Inclusive(probatio.Secret(CONF_PASSWORD), "authentication"): cv.string,
        probatio.Optional(CONF_PAYLOAD): cv.template,
        probatio.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): probatio.Coerce(int),
        probatio.Optional(CONF_CONTENT_TYPE): cv.string,
        probatio.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
        probatio.Optional(CONF_INSECURE_CIPHER, default=False): cv.boolean,
        probatio.Optional(CONF_SKIP_URL_ENCODING, default=False): cv.boolean,
    }
)

CONFIG_SCHEMA = probatio.Schema(
    {DOMAIN: cv.schema_with_slug_keys(COMMAND_SCHEMA)}, extra=probatio.ALLOW_EXTRA
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the REST command component."""
    async_setup_services(hass)
    async_import_yaml_commands(hass, config.get(DOMAIN, {}))

    return True


def flatten(data: Mapping[str, Any]) -> dict[str, Any]:
    """Move the values out of the form sections and drop empty values."""
    flat = dict(data)
    for key in (SECTION_AUTH, SECTION_ADDITIONAL):
        flat.update(flat.pop(key, {}))
    # An empty username or password is valid, like in YAML: some servers
    # expect only a username, others only a token as the password
    return {
        key: value
        for key, value in flat.items()
        if value not in (None, "", {})
        or (key in (CONF_USERNAME, CONF_PASSWORD) and value == "")
    }


def merge_command_config(
    server: Mapping[str, Any], command: Mapping[str, Any]
) -> dict[str, Any]:
    """Combine the settings of a server with those of one of its commands."""
    server, command = flatten(server), flatten(command)
    config = {**server, **command}
    config[CONF_URL] = server.get(CONF_URL, "") + config.pop(CONF_PATH, "")
    # Headers of the command are added to those of the server, and win.
    # Header names are case-insensitive, so they are compared in lowercase.
    headers = {
        str(name).lower(): (name, value)
        for source in (server, command)
        for name, value in source.get(CONF_HEADERS, {}).items()
    }
    if headers:
        config[CONF_HEADERS] = dict(headers.values())
    return config


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Set up the REST commands configured in the UI."""
    for subentry in entry.subentries.values():
        command_config = merge_command_config(entry.data, subentry.data)
        name: str = command_config.pop(CONF_ACTION)
        issue_id = f"invalid_command_{subentry.subentry_id}"
        try:
            validated = COMMAND_SCHEMA(command_config)
        except probatio.Invalid as err:
            # One broken command must not take the other commands down with it
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                config_entry_id=entry.entry_id,
                config_subentry_id=subentry.subentry_id,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="invalid_command",
                translation_placeholders={
                    "action": f"{DOMAIN}.{name}",
                    "server": entry.title,
                    "error": str(err),
                },
            )
            continue
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        entry.async_on_unload(async_register_rest_command(hass, name, validated))

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Unload the REST commands configured in the UI."""


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when a command is added, changed or removed."""
    hass.config_entries.async_schedule_reload(entry.entry_id)


# Where the settings of an imported command are stored, like the forms show them
SERVER_SECTIONS = {
    SECTION_AUTH: (CONF_AUTHENTICATION, CONF_USERNAME, CONF_PASSWORD),
    SECTION_ADDITIONAL: (
        CONF_HEADERS,
        CONF_TIMEOUT,
        CONF_VERIFY_SSL,
        CONF_INSECURE_CIPHER,
    ),
}
COMMAND_SECTIONS = {
    SECTION_ADDITIONAL: (CONF_CONTENT_TYPE, CONF_HEADERS, CONF_SKIP_URL_ENCODING),
}
# Settings that a YAML command takes from its server entry
SERVER_KEYS = (
    CONF_AUTHENTICATION,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_TIMEOUT,
    CONF_VERIFY_SSL,
    CONF_INSECURE_CIPHER,
)


def _to_sections(
    flat: dict[str, Any], sections: Mapping[str, tuple[str, ...]]
) -> dict[str, Any]:
    """Put plain values in the form sections they are stored in."""
    data = dict(flat)
    for section, keys in sections.items():
        data[section] = {key: data.pop(key) for key in keys if key in data}
    return data


def _to_storable(command_config: dict[str, Any]) -> dict[str, Any]:
    """Turn a validated YAML command back into plain values."""
    data: dict[str, Any] = {}
    for key, value in command_config.items():
        if isinstance(value, Template):
            data[key] = value.template
        elif key == CONF_HEADERS:
            data[key] = {name: header.template for name, header in value.items()}
        else:
            data[key] = value
    return data


def _split_url(url: str) -> tuple[str, str]:
    """Split a URL into the server address and the path after it."""
    # The address stops before a template, the path keeps the whole template
    fixed = re.split(r"\{[{%#]", url, maxsplit=1)[0]
    server = match.group(0) if (match := re.match(r"[^:/]+://[^/]*", fixed)) else ""
    return server, url[len(server) :]


@callback
def async_import_yaml_commands(
    hass: HomeAssistant, commands: dict[str, dict[str, Any]]
) -> None:
    """Import the commands from YAML that are not imported yet, per server."""
    if not commands:
        return

    imported = {
        subentry.data[CONF_ACTION]
        for entry in hass.config_entries.async_entries(DOMAIN)
        for subentry in entry.subentries.values()
    }
    servers: dict[tuple[tuple[str, Any], ...], dict[str, dict[str, Any]]] = {}
    for name, command_config in commands.items():
        if name in imported:
            continue
        data = _to_storable(command_config)
        url, data[CONF_PATH] = _split_url(data.pop(CONF_URL))
        server = {key: data.pop(key) for key in SERVER_KEYS if key in data}
        server[CONF_URL] = url
        servers.setdefault(tuple(sorted(server.items())), {})[name] = data

    for server_key, server_commands in servers.items():
        server_data = dict(server_key)
        # Headers that every command sends with the same value move to the server
        shared = dict(
            set.intersection(
                *(
                    set(data.get(CONF_HEADERS, {}).items())
                    for data in server_commands.values()
                )
            )
        )
        if shared:
            server_data[CONF_HEADERS] = shared
        subentries = []
        for name, data in server_commands.items():
            headers = {
                key: value
                for key, value in data.pop(CONF_HEADERS, {}).items()
                if key not in shared
            }
            if headers:
                data[CONF_HEADERS] = headers
            if not data[CONF_PATH]:
                del data[CONF_PATH]
            subentries.append(
                ConfigSubentryData(
                    data=_to_sections({CONF_ACTION: name, **data}, COMMAND_SECTIONS),
                    subentry_type=SUBENTRY_TYPE_COMMAND,
                    title=name,
                    unique_id=None,
                )
            )
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data={
                    "server": _to_sections(server_data, SERVER_SECTIONS),
                    "subentries": subentries,
                },
            ),
            eager_start=True,
        )

    ir.async_create_issue(
        hass,
        DOMAIN,
        "yaml_imported",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="yaml_imported",
    )
