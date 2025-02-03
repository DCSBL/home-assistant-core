"""Constants for the Homewizard integration."""

from __future__ import annotations

from datetime import timedelta
import logging

DOMAIN = "homewizard"
LOGGER = logging.getLogger(__package__)

UPDATE_INTERVAL = timedelta(seconds=5)
