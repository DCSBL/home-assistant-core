"""The minimal Home Assistant that runs inside a sandbox.

The sandbox has a real but empty HomeAssistant instance: its own state machine,
service registry, registries and config entries, which only ever hold what
belongs to the integrations of this sandbox. Integrations run unchanged on top
of it. Everything that core should know about is sent over the channel:

- entity states and the entity and device information core needs to register
  a stand-in entity,
- services the integration registers in its own domain,
- events the integration fires in its own domain,
- changes the integration makes to its own config entries.

Core sends config entries to set up, and service calls to handle.
"""

import asyncio
from collections.abc import Callable, Mapping
from datetime import datetime
from functools import partial
import logging
import os
import socket
from types import MappingProxyType
from typing import Any, override

from homeassistant import bootstrap, loader
from homeassistant.config_entries import (
    ConfigEntries,
    ConfigEntry,
    ConfigEntryItems,
    ConfigEntryState,
)
from homeassistant.const import (
    EVENT_SERVICE_REGISTERED,
    EVENT_SERVICE_REMOVED,
    EVENT_STATE_CHANGED,
    MATCH_ALL,
)
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import (
    device_registry as dr,
    entity_platform,
    entity_registry as er,
)
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.typing import Undefined
from homeassistant.setup import async_setup_component
from homeassistant.util.unit_system import get_unit_system

from .const import CALL_TIMEOUT, SANDBOX_LOCAL_DEPENDENCIES
from .protocol import Channel, Handler, SandboxError

_LOGGER = logging.getLogger(__name__)

# State attributes core calculates itself from the entity properties.
CORE_ATTRIBUTES = frozenset(
    {
        "assumed_state",
        "attribution",
        "device_class",
        "entity_picture",
        "friendly_name",
        "icon",
        "supported_features",
        "unit_of_measurement",
    }
)

# Events of the sandbox's own Home Assistant that core must never receive.
INTERNAL_EVENTS = frozenset(
    {
        EVENT_STATE_CHANGED,
        EVENT_SERVICE_REGISTERED,
        EVENT_SERVICE_REMOVED,
    }
)


def _device_info_json(hass: HomeAssistant, device_info: Any) -> dict[str, Any] | None:
    """Turn device info into JSON that core can register.

    A via device is referenced by the identifiers of that device, as device ids
    of the sandbox mean nothing to core.
    """
    if not device_info:
        return None
    if isinstance(device_info, dr.ChildDeviceInfo):
        _LOGGER.warning("Child devices are not supported in a sandbox yet")
        return None
    info = (
        device_info.as_dict()
        if isinstance(device_info, dr.DeviceInfo)
        else dict(device_info)
    )
    result: dict[str, Any] = {}
    for key, value in info.items():
        if key in ("identifiers", "connections"):
            result[key] = [list(item) for item in value]
        elif key == "via_device":
            result["via_device"] = list(value)
        elif key == "via_device_id":
            if (via := dr.async_get(hass).async_get(value)) and via.identifiers:
                result["via_device"] = list(next(iter(via.identifiers)))
        elif value is None or isinstance(value, (str, int, float, bool)):
            result[key] = value
        else:
            result[key] = str(value)
    return result


class SandboxConfigEntries(ConfigEntries):
    """Config entries of a sandbox.

    Core owns the config entries. The sandbox only keeps the ones core sent it
    in memory, and reports every change an integration makes.
    """

    def __init__(self, hass: HomeAssistant, runtime: SandboxRuntime) -> None:
        """Initialize."""
        super().__init__(hass, {})
        self._runtime = runtime

    @override
    async def async_initialize(self) -> None:
        """Start empty, entries come from core."""
        self._entries = ConfigEntryItems(self.hass)
        self._initialized.set()

    @override
    def _async_schedule_save(self) -> None:
        """Do not persist, core is the source of truth."""

    @override
    def async_update_entry(self, entry: ConfigEntry, **kwargs: Any) -> bool:
        """Update an entry and tell core."""
        changed = super().async_update_entry(entry, **kwargs)
        if changed:
            self._runtime.notify(
                "entry_updated",
                {
                    "entry_id": entry.entry_id,
                    "data": dict(entry.data),
                    "options": dict(entry.options),
                    "title": entry.title,
                    "unique_id": entry.unique_id,
                    "version": entry.version,
                    "minor_version": entry.minor_version,
                },
            )
        return changed


class SandboxRuntime:
    """Run integrations inside the sandbox."""

    def __init__(self, params: dict[str, Any], config_dir: str) -> None:
        """Initialize."""
        self.domains: frozenset[str] = frozenset(params["domains"])
        self._params = params
        self.hass = HomeAssistant(config_dir)
        self.channel: Channel | None = None
        self._entities: dict[str, Entity] = {}
        self._unsubs: list[Callable[[], None]] = []
        self.handlers: dict[str, Handler] = {
            "setup_component": self._async_setup_component,
            "setup_entry": self._async_setup_entry,
            "unload_entry": self._async_unload_entry,
            "remove_entry": self._async_remove_entry,
            "call_service": self._async_call_service,
        }

    def notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a notification to core."""
        if self.channel is None or self.channel.closed.is_set():
            return
        try:
            self.channel.notify(method, params)
        except SandboxError as err:
            _LOGGER.warning("Could not send %s to core: %s", method, err)

    async def async_start(self) -> None:
        """Start the sandbox's Home Assistant."""
        hass = self.hass
        core = self._params["core_config"]
        hass.config.skip_pip = True
        hass.config.location_name = core["location_name"]
        hass.config.latitude = core["latitude"]
        hass.config.longitude = core["longitude"]
        hass.config.elevation = core["elevation"]
        hass.config.radius = core["radius"]
        hass.config.country = core["country"]
        hass.config.language = core["language"]
        hass.config.currency = core["currency"]
        hass.config.units = get_unit_system(core["unit_system"])
        await hass.config.async_set_time_zone(core["time_zone"])

        loader.async_setup(hass)
        hass.config_entries = SandboxConfigEntries(hass, self)
        if not await bootstrap.async_load_base_functionality(hass):
            raise SandboxError("Failed to load the sandbox registries")

        # Entities that are disabled by default still run here, so that the
        # user can enable them in core. Core applies the default itself.
        registry = er.async_get(hass)
        registry.async_get_or_create = partial(  # type: ignore[method-assign]
            self._async_get_or_create_enabled, registry.async_get_or_create
        )

        bus = hass.bus
        self._unsubs.extend(
            (
                bus.async_listen(EVENT_STATE_CHANGED, self._async_state_changed),
                bus.async_listen(EVENT_SERVICE_REGISTERED, self._async_services),
                bus.async_listen(EVENT_SERVICE_REMOVED, self._async_services),
                bus.async_listen(MATCH_ALL, self._async_forward_event),
            )
        )
        await hass.async_start()

    async def async_stop(self) -> None:
        """Stop the sandbox's Home Assistant."""
        for unsub in self._unsubs:
            unsub()
        await self.hass.async_stop()

    @staticmethod
    def _async_get_or_create_enabled(
        original: Callable[..., er.RegistryEntry], *args: Any, **kwargs: Any
    ) -> er.RegistryEntry:
        kwargs.pop("disabled_by", None)
        return original(*args, **kwargs)

    async def _async_check_dependencies(self, domain: str) -> loader.Integration:
        integration = await loader.async_get_integration(self.hass, domain)
        if integration.is_built_in:
            raise SandboxError(f"{domain} is not a custom integration", "unsupported")
        dependencies = await integration.resolve_dependencies() or set()
        if missing := dependencies - SANDBOX_LOCAL_DEPENDENCIES - self.domains:
            raise SandboxError(
                f"{domain} depends on {', '.join(sorted(missing))}, which is not"
                " available in a sandbox",
                "unsupported",
            )
        return integration

    def _check_domain(self, domain: Any) -> str:
        if domain not in self.domains:
            raise SandboxError(f"{domain} does not run in this sandbox", "denied")
        return str(domain)

    async def _async_setup_component(self, params: dict[str, Any]) -> bool:
        domain = self._check_domain(params["domain"])
        await self._async_check_dependencies(domain)
        config = params.get("config")
        if not await async_setup_component(
            self.hass, domain, {} if config is None else {domain: config}
        ):
            raise SandboxError(f"Setup of {domain} failed", "setup_failed")
        return True

    async def _async_setup_entry(self, params: dict[str, Any]) -> dict[str, Any]:
        data = params["entry"]
        domain = self._check_domain(data["domain"])
        entry_id = data["entry_id"]
        entries = self.hass.config_entries
        if entries.async_get_entry(entry_id) is not None:
            await self._async_forget_entry(entry_id)

        entry = ConfigEntry(
            created_at=datetime.fromisoformat(data["created_at"]),
            data=data["data"],
            disabled_by=None,
            discovery_keys=MappingProxyType({}),
            domain=domain,
            entry_id=entry_id,
            minor_version=data["minor_version"],
            modified_at=datetime.fromisoformat(data["modified_at"]),
            options=data["options"],
            pref_disable_new_entities=False,
            pref_disable_polling=data["pref_disable_polling"],
            source=data["source"],
            subentries_data=data["subentries"],
            title=data["title"],
            unique_id=data["unique_id"],
            version=data["version"],
        )
        entries._entries[entry_id] = entry  # noqa: SLF001
        await entries.async_setup(entry_id)

        if entry.state is ConfigEntryState.LOADED:
            return {"state": entry.state.value}
        reason = entry.reason or entry.state.value
        error_type = (
            "not_ready"
            if entry.state is ConfigEntryState.SETUP_RETRY
            else "setup_error"
        )
        # Core decides about retries, the sandbox forgets the attempt.
        await self._async_forget_entry(entry_id)
        raise SandboxError(reason, error_type)

    async def _async_forget_entry(self, entry_id: str) -> bool:
        entries = self.hass.config_entries
        if (entry := entries.async_get_entry(entry_id)) is None:
            return True
        entry.async_cancel_retry_setup()
        result = True
        if entry.state.recoverable and entry.state is not ConfigEntryState.NOT_LOADED:
            result = await entries.async_unload(entry_id)
        del entries._entries[entry_id]  # noqa: SLF001
        return result

    async def _async_unload_entry(self, params: dict[str, Any]) -> bool:
        return await self._async_forget_entry(params["entry_id"])

    async def _async_remove_entry(self, params: dict[str, Any]) -> None:
        data = params["entry"]
        domain = self._check_domain(data["domain"])
        integration = await loader.async_get_integration(self.hass, domain)
        component = await integration.async_get_component()
        if not hasattr(component, "async_remove_entry"):
            return
        entry = ConfigEntry(
            data=data["data"],
            discovery_keys=MappingProxyType({}),
            domain=domain,
            entry_id=data["entry_id"],
            minor_version=data["minor_version"],
            options=data["options"],
            source=data["source"],
            subentries_data=data["subentries"],
            title=data["title"],
            unique_id=data["unique_id"],
            version=data["version"],
        )
        await component.async_remove_entry(self.hass, entry)

    async def _async_call_service(self, params: dict[str, Any]) -> Any:
        domain = params["domain"]
        data = dict(params.get("data") or {})
        if (entity_ids := params.get("entity_ids")) is not None:
            if unknown := set(entity_ids) - self._entities.keys():
                raise SandboxError(f"Unknown entities {unknown}", "denied")
            data["entity_id"] = entity_ids
        elif domain not in self.domains:
            raise SandboxError(f"Service domain {domain} is not ours", "denied")
        async with asyncio.timeout(CALL_TIMEOUT):
            return await self.hass.services.async_call(
                domain,
                params["service"],
                data,
                blocking=True,
                return_response=params.get("return_response", False),
            )

    def _find_entity(self, entity_id: str) -> Entity | None:
        for domain in self.domains:
            for platform in entity_platform.async_get_platforms(self.hass, domain):
                if (entity := platform.entities.get(entity_id)) is not None:
                    return entity
        return None

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        if (new_state := event.data["new_state"]) is None:
            if self._entities.pop(entity_id, None) is not None:
                self.notify("entity_removed", {"key": entity_id})
            return
        if (entity := self._entities.get(entity_id)) is None:
            if (entity := self._find_entity(entity_id)) is None:
                return
            self._entities[entity_id] = entity
        try:
            description = self._describe(
                self.hass, entity, new_state.state, new_state.attributes
            )
        except Exception:
            _LOGGER.exception("Could not describe %s", entity_id)
            return
        self.notify("entity_state", description)

    @staticmethod
    def _describe(
        hass: HomeAssistant,
        entity: Entity,
        state: str,
        attributes: Mapping[str, Any],
    ) -> dict[str, Any]:
        platform = entity.platform
        capabilities = entity.capability_attributes or {}
        name = entity.name
        return {
            "key": entity.entity_id,
            "domain": platform.platform_name,
            "platform": platform.domain,
            "entry_id": platform.config_entry.entry_id
            if platform.config_entry
            else None,
            "unique_id": entity.unique_id,
            "device_info": _device_info_json(hass, entity.device_info),
            "has_entity_name": entity.has_entity_name,
            "entity_category": entity.entity_category,
            "enabled_default": entity.entity_registry_enabled_default,
            "visible_default": entity.entity_registry_visible_default,
            "name": None if name is Undefined else name,
            "state": state,
            "available": entity.available,
            "attributes": {
                key: value
                for key, value in attributes.items()
                if key not in CORE_ATTRIBUTES and key not in capabilities
            },
            "capabilities": capabilities,
            "device_class": entity.device_class,
            "unit": entity.unit_of_measurement,
            "icon": entity.icon,
            "entity_picture": entity.entity_picture,
            "attribution": entity.attribution,
            "assumed_state": entity.assumed_state,
            "supported_features": entity.supported_features,
        }

    @callback
    def _async_services(self, event: Event) -> None:
        if (domain := event.data["domain"]) not in self.domains:
            return
        services = self.hass.services.async_services_for_domain(domain)
        self.notify(
            "services",
            {
                "domain": domain,
                "services": {
                    name: service.supports_response.value
                    for name, service in services.items()
                },
            },
        )

    @callback
    def _async_forward_event(self, event: Event) -> None:
        event_type = str(event.event_type)
        if event_type in INTERNAL_EVENTS:
            return
        for domain in self.domains:
            if event_type == domain or event_type.startswith(f"{domain}_"):
                self.notify("bus_event", {"event_type": event_type, "data": event.data})
                return


async def async_run(
    sock: socket.socket, init_id: int, params: dict[str, Any], confinement: str
) -> None:
    """Run the sandbox until core closes the channel."""
    config_dir = os.getcwd()
    runtime = SandboxRuntime(params, config_dir)
    reader, writer = await asyncio.open_connection(sock=sock)
    channel = Channel(reader, writer, runtime.handlers, name="core")
    runtime.channel = channel
    reader_task = asyncio.create_task(channel.async_run())
    await runtime.async_start()
    channel.answer(
        init_id,
        {
            "pid": os.getpid(),
            "uid": os.getuid(),
            "confinement": confinement,
        },
    )
    await channel.closed.wait()
    reader_task.cancel()
    await runtime.async_stop()
