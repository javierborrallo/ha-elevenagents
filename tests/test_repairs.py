"""Tests for the three repair issues and the auth fix flow."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from elevenlabs.types import GetAgentResponseModel
from elevenlabs.types.agent_call_limits import AgentCallLimits
from elevenlabs.types.agent_metadata_response_model import AgentMetadataResponseModel
from elevenlabs.types.agent_platform_settings_response_model import (
    AgentPlatformSettingsResponseModel,
)
from elevenlabs.types.allowlist_item import AllowlistItem
from elevenlabs.types.auth_settings import AuthSettings
from elevenlabs.types.conversational_config import ConversationalConfig
from elevenlabs.types.privacy_config_output import PrivacyConfigOutput
from homeassistant import data_entry_flow
from homeassistant.helpers import issue_registry as ir

from custom_components.elevenlabs_agent.const import (
    CONF_AGENT_ID,
    DOMAIN,
    ISSUE_AGENT_UNPROTECTED,
    ISSUE_MISSING_ENTITIES,
    ISSUE_TIMEOUT_CROSSED,
    OPT_CONFIRM_TIMEOUT,
    OPT_CONTROL_ENTITIES,
    OPT_EXCEPTIONS,
    PANEL_URL_PATH,
)
from custom_components.elevenlabs_agent.repairs import (
    ElevenLabsAgentRepairFlow,
    async_apply_auth_fix,
    async_check_agent,
    async_check_missing,
    async_check_timeouts,
    async_create_fix_flow,
)

# homeassistant 2026.9.1's http server sets self.app["hass"] as a plain key.
# aiohttp 3.14.3 raises NotAppKeyWarning for that pattern. pyproject.toml
# turns every warning into an error, so this module ignores this one category.
pytestmark = pytest.mark.filterwarnings(
    "ignore::aiohttp.web_exceptions.NotAppKeyWarning"
)


def _agent(
    platform_settings: AgentPlatformSettingsResponseModel | None,
) -> GetAgentResponseModel:
    """Build a minimal, real GetAgentResponseModel around one platform_settings."""
    return GetAgentResponseModel(
        agent_id="agent_test",
        name="Test agent",
        conversation_config=ConversationalConfig(),
        metadata=AgentMetadataResponseModel(
            created_at_unix_secs=0, updated_at_unix_secs=0
        ),
        platform_settings=platform_settings,
    )


async def test_open_agent_raises_the_issue(hass, setup_entry):
    """Anyone holding the agent_id can converse with an unprotected agent."""
    agent = _agent(
        AgentPlatformSettingsResponseModel(
            auth=AuthSettings(enable_auth=False, allowlist=[])
        )
    )

    async_check_agent(hass, setup_entry, agent)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_AGENT_UNPROTECTED)
    assert issue is not None
    assert issue.is_fixable is True
    assert issue.data == {"entry_id": setup_entry.entry_id}


async def test_an_allowlist_counts_as_protected(hass, setup_entry):
    """The two mechanisms are alternatives, not a pair."""
    agent = _agent(
        AgentPlatformSettingsResponseModel(
            auth=AuthSettings(
                enable_auth=False,
                allowlist=[AllowlistItem(hostname="example.com")],
            )
        )
    )

    async_check_agent(hass, setup_entry, agent)

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_AGENT_UNPROTECTED) is None


async def test_a_protected_agent_clears_a_standing_issue(hass, setup_entry):
    """A later check must delete the issue, not merely skip creating it."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_AGENT_UNPROTECTED,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_AGENT_UNPROTECTED,
    )
    agent = _agent(
        AgentPlatformSettingsResponseModel(
            auth=AuthSettings(enable_auth=True, allowlist=[])
        )
    )

    async_check_agent(hass, setup_entry, agent)

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_AGENT_UNPROTECTED) is None


async def test_the_fix_preserves_the_rest_of_platform_settings(hass, setup_entry):
    """A constructed PATCH would reset privacy settings and call limits."""
    client = AsyncMock()
    client.conversational_ai.agents.get.return_value = _agent(
        AgentPlatformSettingsResponseModel(
            auth=AuthSettings(enable_auth=False, allowlist=[]),
            privacy=PrivacyConfigOutput(record_voice=True),
            call_limits=AgentCallLimits(daily_limit=100000),
        )
    )

    await async_apply_auth_fix(hass, setup_entry, client)

    sent = client.conversational_ai.agents.update.call_args.kwargs["platform_settings"]
    assert sent["auth"]["enable_auth"] is True
    assert sent["privacy"] == {"record_voice": True}
    assert sent["call_limits"] == {"daily_limit": 100000}


async def test_the_fix_clears_the_issue(hass, setup_entry):
    """Applying the fix must be observable without a separate re-check."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_AGENT_UNPROTECTED,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_AGENT_UNPROTECTED,
    )
    client = AsyncMock()
    client.conversational_ai.agents.get.return_value = _agent(
        AgentPlatformSettingsResponseModel(
            auth=AuthSettings(enable_auth=False, allowlist=[])
        )
    )

    await async_apply_auth_fix(hass, setup_entry, client)

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_AGENT_UNPROTECTED) is None


async def test_crossed_timeouts_raise_an_informative_issue(hass, setup_entry):
    """No fix flow: both fixes are wrong in their own way."""
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_CONFIRM_TIMEOUT: 25}
    )

    async_check_timeouts(hass, setup_entry, {"response_timeout_secs": 20})

    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_TIMEOUT_CROSSED)
    assert issue is not None
    assert issue.is_fixable is False
    placeholders = issue.translation_placeholders
    assert placeholders is not None
    assert placeholders["confirm_timeout"] == "25"
    assert placeholders["agent_timeout"] == "20"


async def test_equal_timeouts_raise_the_issue(hass, setup_entry):
    """A confirmation wait equal to the agent's is not below it."""
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_CONFIRM_TIMEOUT: 20}
    )

    async_check_timeouts(hass, setup_entry, {"response_timeout_secs": 20})

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_TIMEOUT_CROSSED) is not None


async def test_timeouts_no_longer_crossing_clears_the_issue(hass, setup_entry):
    """The issue is informative: it must disappear once the cause is gone."""
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_CONFIRM_TIMEOUT: 25}
    )
    async_check_timeouts(hass, setup_entry, {"response_timeout_secs": 20})
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_TIMEOUT_CROSSED) is not None

    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_CONFIRM_TIMEOUT: 5}
    )
    async_check_timeouts(hass, setup_entry, {"response_timeout_secs": 20})

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_TIMEOUT_CROSSED) is None


async def test_create_fix_flow_returns_the_repair_flow(hass, setup_entry):
    """The platform loader requires this symbol; a missing one fails at load."""
    flow = await async_create_fix_flow(
        hass, ISSUE_AGENT_UNPROTECTED, {"entry_id": setup_entry.entry_id}
    )

    assert isinstance(flow, ElevenLabsAgentRepairFlow)
    assert flow.entry.data[CONF_AGENT_ID] == setup_entry.data[CONF_AGENT_ID]


def _missing_setup(hass, entry) -> None:
    """Grant one entity that exists and one that does not, both keyed by entity_id."""
    hass.states.async_set("lock.yaml_present", "locked")
    hass.config_entries.async_update_entry(
        entry,
        options={
            OPT_CONTROL_ENTITIES: ["lock.yaml_gone", "lock.yaml_present"],
            OPT_EXCEPTIONS: {"lock.yaml_gone": ["lock.unlock"]},
        },
    )


async def test_a_missing_entity_raises_an_issue_that_points_to_the_panel(
    hass, setup_entry
):
    """The owner decides in the panel; the issue itself removes nothing."""
    _missing_setup(hass, setup_entry)

    async_check_missing(hass, setup_entry)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MISSING_ENTITIES)
    assert issue is not None
    assert issue.is_fixable is False
    assert issue.learn_more_url == f"homeassistant://{PANEL_URL_PATH}"
    assert issue.translation_placeholders == {"entities": "lock.yaml_gone"}
    assert setup_entry.options[OPT_CONTROL_ENTITIES] == [
        "lock.yaml_gone",
        "lock.yaml_present",
    ]


async def test_nothing_missing_clears_the_missing_issue(hass, setup_entry):
    """An entity that comes back takes the issue with it."""
    _missing_setup(hass, setup_entry)
    async_check_missing(hass, setup_entry)

    hass.states.async_set("lock.yaml_gone", "locked")
    async_check_missing(hass, setup_entry)

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MISSING_ENTITIES) is None


async def test_the_missing_issue_has_no_fix_flow(hass, setup_entry):
    """Only agent_unprotected is fixable."""
    with pytest.raises(data_entry_flow.UnknownStep):
        await async_create_fix_flow(
            hass, ISSUE_MISSING_ENTITIES, {"entry_id": setup_entry.entry_id}
        )
