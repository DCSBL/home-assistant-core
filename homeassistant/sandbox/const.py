"""Constants for the integration sandbox."""

from typing import TYPE_CHECKING, Final

from homeassistant.util.hass_dict import HassKey

if TYPE_CHECKING:
    from .manager import SandboxManager
    from .policy import SandboxPolicy

CONF_SANDBOX: Final = "sandbox"
CONF_CUSTOM_INTEGRATIONS: Final = "custom_integrations"
CONF_TRUSTED_INTEGRATIONS: Final = "trusted_integrations"
CONF_ISOLATION: Final = "isolation"
CONF_FILE_ISOLATION: Final = "file_isolation"

# One process for all sandboxed integrations, or one process per integration.
# Per config entry is planned, the protocol already carries the entry id.
ISOLATION_SHARED: Final = "shared"
ISOLATION_INTEGRATION: Final = "integration"

GROUP_CUSTOM: Final = "custom"

DATA_SANDBOX_CONFIG: HassKey[dict] = HassKey("sandbox_config")
DATA_SANDBOX_POLICY: HassKey[SandboxPolicy] = HassKey("sandbox_policy")
DATA_SANDBOX_MANAGER: HassKey[SandboxManager] = HassKey("sandbox_manager")

CONSENT_STORAGE_KEY: Final = "core.sandbox_consent"
CONSENT_STORAGE_VERSION: Final = 1

# Directory below the config dir that holds one private directory per sandbox.
SANDBOX_DATA_DIR: Final = ".sandbox"

# Entity platforms that work in a sandbox. These have no dependencies outside
# the sandbox; their state and entity services are mirrored generically.
# Platforms that need http, media or another shared building block are left out
# until that building block gets a sandbox side.
SANDBOX_PLATFORMS: Final = frozenset(
    {
        "alarm_control_panel",
        "binary_sensor",
        "button",
        "climate",
        "cover",
        "date",
        "datetime",
        "device_tracker",
        "event",
        "fan",
        "humidifier",
        "lawn_mower",
        "light",
        "lock",
        "number",
        "remote",
        "scene",
        "select",
        "sensor",
        "siren",
        "switch",
        "text",
        "time",
        "update",
        "vacuum",
        "valve",
        "water_heater",
        "weather",
    }
)

# Integrations a sandboxed integration may depend on. They are set up inside the
# sandbox, so they may only hold state of that sandbox.
SANDBOX_LOCAL_DEPENDENCIES: Final = SANDBOX_PLATFORMS | {
    "persistent_notification",
    "zone",
}

# Limits on what a sandbox can send to core.
MAX_FRAME_SIZE: Final = 1024 * 1024
MAX_MESSAGES_PER_SECOND: Final = 2000
MAX_ENTITIES: Final = 5000
MAX_ATTRIBUTES: Final = 256
MAX_LOG_LINE: Final = 4096

# The sandbox runs as this uid range when core runs as root.
SANDBOX_UID_BASE: Final = 200000
SANDBOX_UID_RANGE: Final = 60000

STARTUP_TIMEOUT: Final = 60
SETUP_TIMEOUT: Final = 300
CALL_TIMEOUT: Final = 300
