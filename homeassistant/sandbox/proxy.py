"""Stand-ins in core for an integration that runs in a sandbox.

For a sandboxed integration the loader never imports its code. It hands out
these objects instead:

- a proxy component, whose setup and unload of config entries are calls to the
  sandbox,
- a proxy platform per entity platform, which adds proxy entities,
- a proxy config_flow platform, so that existing config entries load. New flows
  are refused until config flows can run inside the sandbox.

A proxy entity mirrors the state the real entity has in the sandbox, and sends
service calls for it to the sandbox.
"""

from types import ModuleType
from typing import TYPE_CHECKING, Any, override

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.service import remove_entity_service_fields
from homeassistant.helpers.typing import ConfigType

from .const import DATA_SANDBOX_MANAGER, SANDBOX_PLATFORMS

if TYPE_CHECKING:
    from homeassistant.loader import Integration

    from .manager import Sandbox


async def _async_sandbox(hass: HomeAssistant, group: str) -> Sandbox:
    return await hass.data[DATA_SANDBOX_MANAGER].async_get_sandbox(group)


class SandboxProxyComponent(ModuleType):
    """Stands in for the component of a sandboxed integration."""

    def __init__(self, integration: Integration, group: str) -> None:
        """Initialize."""
        super().__init__(f"homeassistant.sandbox.proxy.{integration.domain}")
        self.domain = integration.domain
        self.group = group
        # Entity platforms the integration has a file for, forwarded in core.
        self.platforms = integration.platforms_on_disk(sorted(SANDBOX_PLATFORMS))

    async def async_setup(self, hass: HomeAssistant, config: ConfigType) -> bool:
        """Set up the integration inside the sandbox."""
        sandbox = await _async_sandbox(hass, self.group)
        return await sandbox.async_setup_component(self.domain, config.get(self.domain))

    async def async_setup_entry(self, hass: HomeAssistant, entry: ConfigEntry) -> bool:
        """Set up a config entry inside the sandbox, then its platforms in core."""
        sandbox = await _async_sandbox(hass, self.group)
        await sandbox.async_setup_entry(entry)
        await hass.config_entries.async_forward_entry_setups(entry, self.platforms)
        return True

    async def async_unload_entry(self, hass: HomeAssistant, entry: ConfigEntry) -> bool:
        """Unload the platforms in core, then the entry inside the sandbox."""
        sandbox = await _async_sandbox(hass, self.group)
        sandbox.async_begin_unload(entry)
        unloaded = await hass.config_entries.async_unload_platforms(
            entry, self.platforms
        )
        return await sandbox.async_unload_entry(entry) and unloaded

    async def async_remove_entry(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Let the integration clean up inside the sandbox."""
        sandbox = await _async_sandbox(hass, self.group)
        await sandbox.async_remove_entry(entry)

    async def async_migrate_entry(
        self, hass: HomeAssistant, entry: ConfigEntry
    ) -> bool:
        """Migrate inside the sandbox.

        The real flow handler, and with it the current version, only exists in
        the sandbox. It migrates when the entry is set up there and sends the
        result back as an entry update.
        """
        return True


class SandboxProxyPlatform(ModuleType):
    """Stands in for an entity platform of a sandboxed integration."""

    def __init__(self, domain: str, platform: str, group: str) -> None:
        """Initialize."""
        super().__init__(f"homeassistant.sandbox.proxy.{domain}.{platform}")
        self.platform = platform
        self.group = group

    async def async_setup_entry(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: AddConfigEntryEntitiesCallback,
    ) -> None:
        """Connect the platform to the sandbox, which reports the entities."""
        sandbox = await _async_sandbox(hass, self.group)
        sandbox.async_register_platform(entry, self.platform, async_add_entities)


class SandboxConfigFlowBase(ConfigFlow):
    """Config flow of a sandboxed integration.

    The real handler and its version live in the sandbox, so this one claims the
    highest version: core then leaves migration to the sandbox.
    """

    VERSION = 2**31
    MINOR_VERSION = 2**31

    async def _async_not_supported(self) -> ConfigFlowResult:
        return self.async_abort(reason="sandbox_config_flow_not_supported")

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Refuse, config flows do not run in a sandbox yet."""
        return await self._async_not_supported()

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Refuse, config flows do not run in a sandbox yet."""
        return await self._async_not_supported()

    def __getattr__(self, name: str) -> Any:
        """Refuse every other step, such as discovery and reauth."""
        if name.startswith("async_step_"):
            return lambda *args, **kwargs: self._async_not_supported()
        raise AttributeError(name)


def create_proxy_component(integration: Integration, group: str) -> ModuleType:
    """Create the stand-in for the component."""
    return SandboxProxyComponent(integration, group)


def create_proxy_platform(
    integration: Integration, group: str, platform: str
) -> ModuleType:
    """Create the stand-in for a platform, or raise for an unsupported one."""
    domain = integration.domain
    if platform == "config_flow":
        module = ModuleType(f"homeassistant.sandbox.proxy.{domain}.config_flow")
        # Defining the subclass registers it as the handler of the domain.
        module.SandboxConfigFlow = type(  # type: ignore[attr-defined]
            f"SandboxConfigFlow_{domain}",
            (SandboxConfigFlowBase,),
            {"__module__": module.__name__},
            domain=domain,
        )
        return module
    if platform in SANDBOX_PLATFORMS:
        return SandboxProxyPlatform(domain, platform, group)
    raise ModuleNotFoundError(
        f"Platform {domain}.{platform} is not available for an integration that"
        " runs in a sandbox",
        name=f"{integration.pkg_path}.{platform}",
    )


def _device_info(
    hass: HomeAssistant, info: dict[str, Any] | None
) -> dr.DeviceInfo | None:
    """Rebuild device info from what a sandbox sent, keeping known fields only."""
    if not info:
        return None
    fields: dict[str, Any] = {}
    for key, value in info.items():
        if key in ("identifiers", "connections"):
            fields[key] = {(str(a), str(b)) for a, b in value}
        elif key == "via_device":
            via = dr.async_get(hass).async_get_device(
                identifiers={(str(value[0]), str(value[1]))}
            )
            if via is not None:
                fields["via_device_id"] = via.id
        elif key in dr.DeviceInfo._field_set and key not in (  # noqa: SLF001
            "via_device_id",
            "created_at",
            "modified_at",
        ):
            fields[key] = value
    return dr.DeviceInfo(**fields)


class SandboxProxyEntity(Entity):
    """Mirror of an entity that runs in a sandbox."""

    _attr_should_poll = False

    def __init__(
        self, sandbox: Sandbox, entry: ConfigEntry, info: dict[str, Any]
    ) -> None:
        """Initialize from the first description the sandbox sent."""
        self._sandbox = sandbox
        self.sandbox_key: str = info["key"]
        if (unique_id := info.get("unique_id")) is not None:
            self._attr_unique_id = str(unique_id)
        self._attr_device_info = _device_info(sandbox.hass, info.get("device_info"))
        self._attr_has_entity_name = bool(info.get("has_entity_name"))
        if (category := info.get("entity_category")) is not None:
            self._attr_entity_category = EntityCategory(category)
        self._attr_entity_registry_enabled_default = bool(
            info.get("enabled_default", True)
        )
        self._attr_entity_registry_visible_default = bool(
            info.get("visible_default", True)
        )
        self._apply(info)

    def _apply(self, info: dict[str, Any]) -> None:
        name = info.get("name")
        self._attr_name = None if name is None else str(name)
        self._attr_available = bool(info.get("available", True))
        self._attr_state = None if info["state"] is None else str(info["state"])
        self._attr_extra_state_attributes = dict(info.get("attributes") or {})
        self._attr_capability_attributes = info.get("capabilities") or None
        self._attr_device_class = info.get("device_class")
        self._attr_unit_of_measurement = info.get("unit")
        self._attr_icon = info.get("icon")
        self._attr_entity_picture = info.get("entity_picture")
        self._attr_attribution = info.get("attribution")
        self._attr_assumed_state = bool(info.get("assumed_state"))
        supported = info.get("supported_features")
        self._attr_supported_features = None if supported is None else int(supported)

    @callback
    def async_update_from_sandbox(self, info: dict[str, Any]) -> None:
        """Apply a new description from the sandbox."""
        self._apply(info)
        if self.hass is not None and self.platform is not None:
            self.async_write_ha_state()

    @callback
    def async_sandbox_removed(self) -> None:
        """Remove this entity, the sandbox removed the real one."""
        if self.hass is not None and self.platform is not None:
            self.hass.async_create_task(self.async_remove())

    @callback
    def async_sandbox_gone(self) -> None:
        """Mark this entity unavailable, the sandbox stopped."""
        self._attr_available = False
        if self.hass is not None and self.platform is not None:
            self.async_write_ha_state()

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Forget this entity."""
        self._sandbox.async_entity_removed(self.sandbox_key)

    async def async_sandbox_service_call(self, call: ServiceCall) -> ServiceResponse:
        """Handle an entity service call in the sandbox.

        Called by the entity service helper instead of the method of the
        service, which only exists on the real entity.
        """
        return await self._sandbox.async_call_entity_service(
            call, self.sandbox_key, remove_entity_service_fields(call)
        )
