"""HA harness: `pip install pytest-homeassistant-custom-component`, then `pytest tests_ha`."""

from pathlib import Path

import custom_components  # the plugin's testing_config package; add ours to its search path
import pytest

custom_components.__path__.append(str(Path(__file__).resolve().parents[1] / "custom_components"))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
