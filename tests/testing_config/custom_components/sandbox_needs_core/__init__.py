"""An integration that needs a building block the sandbox does not offer."""

from homeassistant.core import HomeAssistant


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up."""
    return True
