"""Config flow of the evil integration."""

from homeassistant.config_entries import ConfigFlow


class SandboxEvilFlow(ConfigFlow, domain="sandbox_evil"):
    """Config flow of the evil integration."""

    VERSION = 1
