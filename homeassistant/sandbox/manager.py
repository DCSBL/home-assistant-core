"""Start sandboxes and check everything they send to core.

Core treats a sandbox as untrusted. Every message from it is checked against
what that sandbox is allowed to touch: only config entries core gave it, only
services and events in the domains of its own integrations, and only entities
on platforms that have been forwarded to it.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import socket
import sys
import time
from typing import TYPE_CHECKING, Any
import zlib

from homeassistant import loader
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_CLOSE
from homeassistant.core import (
    Event,
    EventOrigin,
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import (
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.service import async_extract_entity_ids

from . import confine
from .const import (
    CALL_TIMEOUT,
    DATA_SANDBOX_MANAGER,
    MAX_ATTRIBUTES,
    MAX_ENTITIES,
    MAX_LOG_LINE,
    MAX_MESSAGES_PER_SECOND,
    SANDBOX_DATA_DIR,
    SANDBOX_PLATFORMS,
    SANDBOX_UID_BASE,
    SANDBOX_UID_RANGE,
    SETUP_TIMEOUT,
    STARTUP_TIMEOUT,
)
from .policy import SandboxPolicy, async_load_policy
from .protocol import Channel, SandboxError

if TYPE_CHECKING:
    from .proxy import SandboxProxyEntity

_LOGGER = logging.getLogger(__name__)

# Environment variables the sandbox inherits. Everything else, such as the
# Supervisor token, stays behind.
_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "TZ", "PYTHONHASHSEED")

_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

ISSUE_SANDBOX_UNSUPPORTED = "sandbox_unsupported"


class _StaleMessage(Exception):
    """A message about an entry that was unloaded in the meantime."""


async def async_setup(hass: HomeAssistant, conf: dict[str, Any]) -> None:
    """Load the policy and prepare the manager, before integrations load."""
    policy = await async_load_policy(hass, conf)
    if not policy.isolate_custom:
        return
    manager = hass.data[DATA_SANDBOX_MANAGER] = SandboxManager(hass, policy)
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_CLOSE, manager.async_shutdown)


@callback
def async_get_manager(hass: HomeAssistant) -> SandboxManager:
    """Return the sandbox manager."""
    return hass.data[DATA_SANDBOX_MANAGER]


class SandboxManager:
    """Start one sandbox per group, on first use."""

    def __init__(self, hass: HomeAssistant, policy: SandboxPolicy) -> None:
        """Initialize."""
        self.hass = hass
        self.policy = policy
        self._sandboxes: dict[str, Sandbox] = {}
        self._lock = asyncio.Lock()

    async def async_get_sandbox(self, group: str) -> Sandbox:
        """Return the running sandbox of a group, starting it if needed."""
        async with self._lock:
            if (sandbox := self._sandboxes.get(group)) is not None and sandbox.running:
                return sandbox
            custom = await loader.async_get_custom_components(self.hass)
            integrations = {
                domain: integration
                for domain, integration in custom.items()
                if integration.sandbox_group == group
            }
            sandbox = Sandbox(self.hass, self.policy, group, integrations)
            await sandbox.async_start()
            self._sandboxes[group] = sandbox
            return sandbox

    async def async_shutdown(self, event: Event | None = None) -> None:
        """Stop all sandboxes."""
        await asyncio.gather(
            *(sandbox.async_stop() for sandbox in self._sandboxes.values())
        )
        self._sandboxes.clear()


@dataclass(slots=True)
class _EntryState:
    """What core knows about one config entry inside a sandbox."""

    entry: ConfigEntry
    add_entities: dict[str, AddConfigEntryEntitiesCallback] = field(
        default_factory=dict
    )
    pending: dict[str, dict[str, Any]] = field(default_factory=dict)


class Sandbox:
    """One sandbox process, as seen from core."""

    def __init__(
        self,
        hass: HomeAssistant,
        policy: SandboxPolicy,
        group: str,
        integrations: dict[str, loader.Integration],
    ) -> None:
        """Initialize."""
        self.hass = hass
        self.group = group
        self.integrations = integrations
        self.domains = frozenset(integrations)
        self._policy = policy
        self.logger = logging.getLogger(f"{__name__}.{group}")
        self.confinement: str | None = None
        self.pid: int | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._channel: Channel | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._entries: dict[str, _EntryState] = {}
        self._known_entries: set[str] = set()
        self._entities: dict[str, SandboxProxyEntity] = {}
        self._services: dict[str, set[str]] = {}
        self._rate_window = 0.0
        self._rate_count = 0
        self._rate_warned = False
        self._stopping = False

    @property
    def running(self) -> bool:
        """Return if the sandbox process is running."""
        return self._channel is not None and not self._channel.closed.is_set()

    @property
    def data_dir(self) -> str:
        """Return the private directory of this sandbox."""
        return self.hass.config.path(SANDBOX_DATA_DIR, self.group)

    async def async_start(self) -> None:
        """Start the process and wait until its Home Assistant runs."""
        parent, child = socket.socketpair()
        env = {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}
        env["HOME"] = self.data_dir
        try:
            self._process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-m",
                "homeassistant.sandbox",
                "--fd",
                str(child.fileno()),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                pass_fds=(child.fileno(),),
                env=env,
                start_new_session=True,
            )
        finally:
            child.close()

        reader, writer = await asyncio.open_connection(sock=parent)
        self._channel = Channel(
            reader,
            writer,
            {},
            name=f"sandbox {self.group}",
            on_notification=self._async_on_notification,
        )
        self._create_task(self._channel.async_run())
        self._create_task(self._async_read_log())
        self._create_task(self._async_watch_process())

        uid = SANDBOX_UID_BASE + zlib.crc32(self.group.encode()) % SANDBOX_UID_RANGE
        params = {
            "domains": sorted(self.domains),
            "config_dir": self.hass.config.config_dir,
            "data_dir": self.data_dir,
            "integration_dirs": {
                domain: str(integration.file_path)
                for domain, integration in self.integrations.items()
            },
            "uid": uid,
            "gid": uid,
            "file_isolation": self._policy.file_isolation,
            "log_level": logging.getLogger(__name__).getEffectiveLevel(),
            "core_config": self._core_config(),
        }
        await self.hass.async_add_executor_job(
            Path(self.data_dir).mkdir, 0o700, True, True
        )
        try:
            info = await self._channel.async_call("init", params, STARTUP_TIMEOUT)
        except (SandboxError, TimeoutError) as err:
            await self.async_stop()
            raise HomeAssistantError(
                f"Sandbox {self.group} failed to start: {err}"
            ) from err

        self.pid = info.get("pid")
        self.confinement = info.get("confinement")
        if self.confinement == confine.CONFINEMENT_FULL:
            self.logger.info(
                "Sandbox %s started as pid %s, uid %s, for %s",
                self.group,
                self.pid,
                info.get("uid"),
                ", ".join(sorted(self.domains)),
            )
        else:
            self.logger.warning(
                "Sandbox %s started as pid %s for %s, but without file isolation:"
                " the integrations can not reach core's memory, but can still read"
                " files that Home Assistant can read. File isolation needs Linux"
                " and Home Assistant running as root, as in the container",
                self.group,
                self.pid,
                ", ".join(sorted(self.domains)),
            )

    def _core_config(self) -> dict[str, Any]:
        config = self.hass.config
        return {
            "location_name": config.location_name,
            "latitude": config.latitude,
            "longitude": config.longitude,
            "elevation": config.elevation,
            "radius": config.radius,
            "country": config.country,
            "language": config.language,
            "currency": config.currency,
            "unit_system": config.units._name,  # noqa: SLF001
            "time_zone": config.time_zone,
        }

    def _create_task(self, coro: Any) -> None:
        task = self.hass.async_create_background_task(
            coro, f"sandbox {self.group}", eager_start=False
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _async_read_log(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        stderr = self._process.stderr
        while line := await stderr.readline():
            text = line[:MAX_LOG_LINE].decode(errors="replace").rstrip()
            level_name, _, message = text.partition(" ")
            if (level := _LOG_LEVELS.get(level_name)) is None:
                level, message = logging.WARNING, text
            self.logger.log(level, "[%s] %s", self.group, message)

    async def _async_watch_process(self) -> None:
        assert self._process is not None
        code = await self._process.wait()
        if self._channel is not None:
            self._channel.close()
        if self._stopping or self.hass.is_stopping:
            return
        self.logger.error(
            "Sandbox %s stopped unexpectedly with exit code %s; its entities are"
            " unavailable until the integrations are reloaded",
            self.group,
            code,
        )
        for entity in list(self._entities.values()):
            entity.async_sandbox_gone()

    async def async_stop(self) -> None:
        """Stop the process."""
        self._stopping = True
        if self._channel is not None:
            self._channel.close()
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                async with asyncio.timeout(10):
                    await self._process.wait()
            except TimeoutError:
                self._process.kill()

    async def _async_call(
        self, method: str, params: dict[str, Any], timeout: float = CALL_TIMEOUT
    ) -> Any:
        if self._channel is None or not self.running:
            raise HomeAssistantError(f"Sandbox {self.group} is not running")
        return await self._channel.async_call(method, params, timeout)

    # Calls from core to the sandbox.

    async def async_setup_component(self, domain: str, config: Any) -> bool:
        """Set up an integration inside the sandbox."""
        try:
            await self._async_call(
                "setup_component", {"domain": domain, "config": config}, SETUP_TIMEOUT
            )
        except SandboxError as err:
            self.logger.error("Setup of %s in sandbox failed: %s", domain, err)
            self._async_report_unsupported(domain, str(err))
            return False
        ir.async_delete_issue(
            self.hass, "homeassistant", f"{ISSUE_SANDBOX_UNSUPPORTED}.{domain}"
        )
        return True

    async def async_setup_entry(self, entry: ConfigEntry) -> None:
        """Set up a config entry inside the sandbox."""
        self._entries[entry.entry_id] = _EntryState(entry)
        self._known_entries.add(entry.entry_id)
        try:
            await self._async_call(
                "setup_entry", {"entry": entry.as_dict()}, SETUP_TIMEOUT
            )
        except SandboxError as err:
            self._entries.pop(entry.entry_id, None)
            if err.error_type == "not_ready":
                raise ConfigEntryNotReady(str(err)) from err
            if err.error_type == "unsupported":
                self._async_report_unsupported(entry.domain, str(err))
            raise ConfigEntryError(str(err)) from err

    @callback
    def async_begin_unload(self, entry: ConfigEntry) -> None:
        """Stop accepting entities for an entry that core is unloading."""
        self._entries.pop(entry.entry_id, None)

    async def async_unload_entry(self, entry: ConfigEntry) -> bool:
        """Unload a config entry inside the sandbox."""
        self._entries.pop(entry.entry_id, None)
        return bool(
            await self._async_call("unload_entry", {"entry_id": entry.entry_id})
        )

    async def async_remove_entry(self, entry: ConfigEntry) -> None:
        """Let the integration clean up a removed config entry."""
        await self._async_call("remove_entry", {"entry": entry.as_dict()})

    async def async_call_entity_service(
        self, call: ServiceCall, key: str, data: dict[str, Any]
    ) -> ServiceResponse:
        """Handle an entity service call for one proxied entity."""
        response = await self._async_call(
            "call_service",
            {
                "domain": call.domain,
                "service": call.service,
                "data": data,
                "entity_ids": [key],
                "return_response": call.return_response,
            },
        )
        if not call.return_response or not isinstance(response, dict):
            return None
        result = response.get(key)
        return result if isinstance(result, dict) else None

    @callback
    def async_register_platform(
        self,
        entry: ConfigEntry,
        platform: str,
        async_add_entities: AddConfigEntryEntitiesCallback,
    ) -> None:
        """Connect a forwarded platform in core to the sandbox."""
        if (state := self._entries.get(entry.entry_id)) is None:
            return
        state.add_entities[platform] = async_add_entities
        pending = [
            info
            for info in list(state.pending.values())
            if info["platform"] == platform
        ]
        for info in pending:
            del state.pending[info["key"]]
            self._async_entity_state(info)

    @callback
    def async_entity_removed(self, key: str) -> None:
        """Forget a proxy entity that core removed."""
        self._entities.pop(key, None)

    # Messages from the sandbox. Everything here is untrusted input.

    @callback
    def _async_on_notification(self, method: str, params: dict[str, Any]) -> None:
        if not self._async_rate_ok():
            return
        handler: Callable[[dict[str, Any]], None] | None = {
            "entity_state": self._async_entity_state,
            "entity_removed": self._async_entity_gone,
            "services": self._async_services,
            "bus_event": self._async_bus_event,
            "entry_updated": self._async_entry_updated,
        }.get(method)
        if handler is None:
            self.logger.warning("Sandbox %s sent unknown %s", self.group, method)
            return
        try:
            handler(params)
        except _StaleMessage:
            pass
        except (KeyError, TypeError, ValueError) as err:
            self.logger.warning(
                "Sandbox %s sent an invalid %s: %s", self.group, method, err
            )

    @callback
    def _async_rate_ok(self) -> bool:
        now = time.monotonic()
        if now - self._rate_window >= 1:
            self._rate_window = now
            self._rate_count = 0
        self._rate_count += 1
        if self._rate_count <= MAX_MESSAGES_PER_SECOND:
            return True
        if not self._rate_warned:
            self._rate_warned = True
            self.logger.warning(
                "Sandbox %s sends more than %s messages per second, dropping",
                self.group,
                MAX_MESSAGES_PER_SECOND,
            )
        return False

    def _own_entry(self, entry_id: Any) -> _EntryState:
        if (state := self._entries.get(entry_id)) is not None:
            return state
        if entry_id in self._known_entries:
            raise _StaleMessage
        raise ValueError(f"entry {entry_id} does not belong to this sandbox")

    @callback
    def _async_entity_state(self, info: dict[str, Any]) -> None:
        key = info["key"]
        if not isinstance(key, str):
            raise TypeError("key")
        if (entity := self._entities.get(key)) is not None:
            entity.async_update_from_sandbox(info)
            return

        from .proxy import SandboxProxyEntity  # noqa: PLC0415

        state = self._own_entry(info["entry_id"])
        platform = info["platform"]
        if platform not in SANDBOX_PLATFORMS or info["domain"] != state.entry.domain:
            raise ValueError(f"platform {platform} of {info['domain']} is not allowed")
        if len(self._entities) + len(state.pending) >= MAX_ENTITIES:
            raise ValueError("too many entities")
        if len(info.get("attributes") or {}) > MAX_ATTRIBUTES:
            raise ValueError("too many attributes")
        if (add_entities := state.add_entities.get(platform)) is None:
            state.pending[key] = info
            return
        entity = SandboxProxyEntity(self, state.entry, info)
        self._entities[key] = entity
        add_entities([entity])

    @callback
    def _async_entity_gone(self, params: dict[str, Any]) -> None:
        key = params["key"]
        for state in self._entries.values():
            state.pending.pop(key, None)
        if (entity := self._entities.pop(key, None)) is not None:
            entity.async_sandbox_removed()

    @callback
    def _async_services(self, params: dict[str, Any]) -> None:
        domain = params["domain"]
        if domain not in self.domains:
            raise ValueError(f"services in {domain} are not allowed")
        services: dict[str, str] = params["services"]
        current = self._services.setdefault(domain, set())
        for name in current - services.keys():
            self.hass.services.async_remove(domain, name)
            current.discard(name)
        for name, supports_response in services.items():
            if name in current:
                continue
            if not isinstance(name, str) or not name.isidentifier():
                raise ValueError(f"invalid service name {name}")
            self.hass.services.async_register(
                domain,
                name,
                self._async_handle_domain_service,
                schema=None,
                supports_response=SupportsResponse(supports_response),
            )
            current.add(name)

    async def _async_handle_domain_service(self, call: ServiceCall) -> ServiceResponse:
        """Forward a service of a sandboxed integration's own domain."""
        data = dict(call.data)
        entity_ids: list[str] | None = None
        if any(key in data for key in ("entity_id", "device_id", "area_id")):
            referenced = await async_extract_entity_ids(call)
            entity_ids = [
                entity.sandbox_key
                for entity_id in referenced
                if (entity := self._entity_by_id(entity_id)) is not None
            ]
            for key in ("entity_id", "device_id", "area_id", "floor_id", "label_id"):
                data.pop(key, None)
        response = await self._async_call(
            "call_service",
            {
                "domain": call.domain,
                "service": call.service,
                "data": data,
                "entity_ids": entity_ids,
                "return_response": call.return_response,
            },
        )
        return response if isinstance(response, dict) else None

    def _entity_by_id(self, entity_id: str) -> SandboxProxyEntity | None:
        for entity in self._entities.values():
            if entity.entity_id == entity_id:
                return entity
        return None

    @callback
    def _async_bus_event(self, params: dict[str, Any]) -> None:
        event_type = params["event_type"]
        data = params["data"]
        if not isinstance(event_type, str) or not isinstance(data, dict):
            raise TypeError("event")
        if not any(
            event_type == domain or event_type.startswith(f"{domain}_")
            for domain in self.domains
        ):
            raise ValueError(f"event {event_type} is outside the sandbox's domains")
        self.hass.bus.async_fire(event_type, data, EventOrigin.remote)

    @callback
    def _async_entry_updated(self, params: dict[str, Any]) -> None:
        state = self._own_entry(params["entry_id"])
        if not isinstance(params["data"], dict) or not isinstance(
            params["options"], dict
        ):
            raise TypeError("data and options must be objects")
        self.hass.config_entries.async_update_entry(
            state.entry,
            data=params["data"],
            options=params["options"],
            title=str(params["title"]),
            unique_id=params["unique_id"]
            if isinstance(params["unique_id"], str)
            else None,
            version=int(params["version"]),
            minor_version=int(params["minor_version"]),
        )

    @callback
    def _async_report_unsupported(self, domain: str, reason: str) -> None:
        integration = self.integrations.get(domain)
        ir.async_create_issue(
            self.hass,
            "homeassistant",
            f"{ISSUE_SANDBOX_UNSUPPORTED}.{domain}",
            is_fixable=True,
            is_persistent=False,
            issue_domain=domain,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_SANDBOX_UNSUPPORTED,
            translation_placeholders={
                "domain": domain,
                "name": integration.name if integration else domain,
                "reason": reason[:500],
            },
            data={
                "domain": domain,
                "version": str(integration.version)
                if integration and integration.version
                else "",
                "name": integration.name if integration else domain,
                "reason": reason[:500],
            },
        )
