"""Config flow of the sandbox demo integration."""

from homeassistant.config_entries import ConfigFlow


class SandboxDemoFlow(ConfigFlow, domain="sandbox_demo"):
    """Config flow of the sandbox demo integration."""

    VERSION = 1
    MINOR_VERSION = 2
