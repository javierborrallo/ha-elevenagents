"""Shared fixtures."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elevenlabs_agent.const import (
    CONF_AGENT_ID,
    CONF_API_KEY,
    CONF_TOOL_IDS,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DOMAIN,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom integrations in every test."""
    return


@pytest.fixture
async def config_entry(hass) -> MockConfigEntry:
    """
    Return a config entry already added to hass.

    An external URL is set because the config flow refuses to create an
    entry without one, so no entry can exist without it in production.
    """
    await hass.config.async_update(external_url="https://home.example")
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="agent_test",
        data={
            CONF_API_KEY: "sk_test",
            CONF_AGENT_ID: "agent_test",
            CONF_WEBHOOK_ID: "webhook_test",
            CONF_WEBHOOK_SECRET: "secret_test",
            CONF_TOOL_IDS: {},
        },
        options={},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup_entry(hass, config_entry) -> MockConfigEntry:
    """Set up the config entry through Home Assistant's own setup state machine."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
