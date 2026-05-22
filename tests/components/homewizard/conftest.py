"""Fixtures for HomeWizard integration tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

from homewizard_energy.errors import UnsupportedError
from homewizard_energy.models import (
    Batteries,
    CombinedModels,
    Device,
    Measurement,
    State,
    System,
    Token,
)
import pytest

from homeassistant.components.homewizard.const import DOMAIN
from homeassistant.const import CONF_IP_ADDRESS, CONF_TOKEN
from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry, get_fixture_path, load_json_object_fixture

# v2 API versions used in fixtures:
# v2.0.0 initial release
# v2.1.0 adds /api/batteries (P1 Meter and kWh Meter)
# v2.2.0 adds batteries permissions support
V1_API_FIXTURE_ROOT = "v1"
V2_API_VERSION_INITIAL = "v2.0.0"
V2_API_VERSION_BATTERIES = "v2.1.0"
V2_API_VERSION_PERMISSIONS = "v2.2.0"

V2_DEVICE_FIXTURE_API_VERSION: dict[str, str] = {
    "HWE-P1": V2_API_VERSION_BATTERIES,
    "HWE-KWH1": V2_API_VERSION_BATTERIES,
    "HWE-P1-no-batteries": V2_API_VERSION_PERMISSIONS,
}


def _fixture_exists(fixture_path: str) -> bool:
    """Return if a fixture file exists."""
    return get_fixture_path(fixture_path, DOMAIN).exists()


def _load_fixture(fixture_path: str) -> dict[str, object]:
    """Load a json fixture."""
    return load_json_object_fixture(fixture_path, DOMAIN)


def _load_optional_state(fixture_path: str) -> State | None:
    """Load an optional state fixture."""
    if not _fixture_exists(fixture_path):
        return None
    return State.from_dict(_load_fixture(fixture_path))


def _load_optional_system(fixture_path: str) -> System | None:
    """Load an optional system fixture."""
    if not _fixture_exists(fixture_path):
        return None
    return System.from_dict(_load_fixture(fixture_path))


def _load_optional_batteries(fixture_path: str) -> Batteries | None:
    """Load an optional batteries fixture."""
    if not _fixture_exists(fixture_path):
        return None
    return Batteries.from_dict(_load_fixture(fixture_path))


def _build_v1_combined_models(device_fixture: str) -> CombinedModels:
    """Build a CombinedModels payload from v1 fixtures."""
    fixture_base_path = f"{V1_API_FIXTURE_ROOT}/{device_fixture}"
    return CombinedModels(
        device=Device.from_dict(_load_fixture(f"{fixture_base_path}/device.json")),
        measurement=Measurement.from_dict(
            _load_fixture(f"{fixture_base_path}/data.json")
        ),
        state=_load_optional_state(f"{fixture_base_path}/state.json"),
        system=_load_optional_system(f"{fixture_base_path}/system.json"),
        batteries=_load_optional_batteries(f"{fixture_base_path}/batteries.json"),
    )


def _build_v2_combined_models(v2_fixture_base_path: str) -> CombinedModels:
    """Build a CombinedModels payload from v2 fixtures."""
    return CombinedModels(
        device=Device.from_dict(_load_fixture(f"{v2_fixture_base_path}/device.json")),
        measurement=Measurement.from_dict(
            _load_fixture(f"{v2_fixture_base_path}/measurement.json")
        ),
        state=_load_optional_state(f"{v2_fixture_base_path}/state.json"),
        system=_load_optional_system(f"{v2_fixture_base_path}/system.json"),
        batteries=_load_optional_batteries(f"{v2_fixture_base_path}/batteries.json"),
    )


@pytest.fixture
def device_fixture() -> str:
    """Return the device fixture name used for tests."""
    return "HWE-P1"


@pytest.fixture
def v2_api_version(device_fixture: str) -> str:
    """Return the v2 API version for a v2 device fixture."""
    if device_fixture not in V2_DEVICE_FIXTURE_API_VERSION:
        raise AssertionError(
            f"Missing v2 fixture api_version mapping for {device_fixture}"
        )
    return V2_DEVICE_FIXTURE_API_VERSION[device_fixture]


@pytest.fixture
def mock_homewizardenergy(
    device_fixture: str,
) -> Generator[MagicMock]:
    """Return a mock bridge."""
    with (
        patch(
            "homeassistant.components.homewizard.HomeWizardEnergyV1",
            autospec=True,
        ) as homewizard,
        patch(
            "homeassistant.components.homewizard.config_flow.HomeWizardEnergyV1",
            new=homewizard,
        ),
        patch(
            "homeassistant.components.homewizard.has_v2_api",
            autospec=True,
            return_value=False,
        ),
        patch(
            "homeassistant.components.homewizard.config_flow.has_v2_api",
            autospec=True,
            return_value=False,
        ),
    ):
        client = homewizard.return_value
        client.combined.return_value = _build_v1_combined_models(device_fixture)

        # device() call is used during configuration flow
        client.device.return_value = client.combined.return_value.device

        yield client


@pytest.fixture
def mock_homewizardenergy_v2(
    device_fixture: str,
    v2_api_version: str,
) -> Generator[MagicMock]:
    """Return a mock bridge."""
    v2_fixture_base_path = f"{v2_api_version}/{device_fixture}"

    with (
        patch(
            "homeassistant.components.homewizard.HomeWizardEnergyV2",
            autospec=True,
        ) as homewizard,
        patch(
            "homeassistant.components.homewizard.config_flow.HomeWizardEnergyV2",
            new=homewizard,
        ),
    ):
        client = homewizard.return_value
        client.combined.return_value = _build_v2_combined_models(v2_fixture_base_path)

        batteries = client.combined.return_value.batteries
        if batteries is None:
            client.batteries.side_effect = UnsupportedError(
                "Batteries is not supported"
            )
        else:
            client.batteries.return_value = batteries

        # device() call is used during configuration flow
        client.device.return_value = client.combined.return_value.device

        # Authorization flow is used during configuration flow
        client.get_token.return_value = Token.from_dict(
            _load_fixture(f"{V2_API_VERSION_INITIAL}/generic/token.json")
        ).token

        yield client


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Mock setting up a config entry."""
    with patch(
        "homeassistant.components.homewizard.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return the default mocked config entry."""
    return MockConfigEntry(
        title="Device",
        domain=DOMAIN,
        data={
            "product_name": "P1 Meter",
            "product_type": "HWE-P1",
            "serial": "5c2fafabcdef",
            CONF_IP_ADDRESS: "127.0.0.1",
        },
        unique_id="HWE-P1_5c2fafabcdef",
    )


@pytest.fixture
def mock_config_entry_v2(device_fixture: str) -> MockConfigEntry:
    """Return the default mocked config entry."""
    return MockConfigEntry(
        title="Device",
        domain=DOMAIN,
        data={
            CONF_IP_ADDRESS: "127.0.0.1",
            CONF_TOKEN: "00112233445566778899ABCDEFABCDEF",
        },
        unique_id=f"{device_fixture}_5c2fafabcdef",
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_homewizardenergy: MagicMock,
) -> MockConfigEntry:
    """Set up the HomeWizard integration for testing."""
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    return mock_config_entry


@pytest.fixture
async def init_integration_v2(
    hass: HomeAssistant,
    mock_config_entry_v2: MockConfigEntry,
    mock_homewizardenergy_v2: MagicMock,
) -> MockConfigEntry:
    """Set up the HomeWizard integration with a v2 config entry for testing."""
    mock_config_entry_v2.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry_v2.entry_id)
    await hass.async_block_till_done()

    return mock_config_entry_v2


@pytest.fixture
def mock_onboarding() -> Generator[MagicMock]:
    """Mock that Home Assistant is currently onboarding."""
    with patch(
        "homeassistant.components.onboarding.async_is_onboarded",
        return_value=False,
    ) as mock_onboarding:
        yield mock_onboarding
