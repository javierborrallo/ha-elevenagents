"""Tests for the config flow."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.network import NoURLAvailableError

from custom_components.elevenagents.const import (
    CONF_AGENT_ID,
    CONF_API_KEY,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DOMAIN,
)
from custom_components.elevenagents.elevenlabs_client import (
    ElevenLabsAuthError,
    ElevenLabsError,
    ElevenLabsNotFoundError,
    ElevenLabsScopeError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

# homeassistant 2026.9.1's http server sets self.app["hass"] as a plain key.
# aiohttp 3.14.3 raises NotAppKeyWarning for that pattern. pyproject.toml
# turns every warning into an error, so this module ignores this one category.
pytestmark = pytest.mark.filterwarnings(
    "ignore::aiohttp.web_exceptions.NotAppKeyWarning"
)

API_KEY_INPUT = {CONF_API_KEY: "sk_test"}

AGENTS_RESPONSE = SimpleNamespace(
    agents=[
        SimpleNamespace(agent_id="agent_b", name="Bravo"),
        SimpleNamespace(agent_id="agent_a", name="Alpha"),
    ]
)


def _tool(tool_id: str, name: str) -> SimpleNamespace:
    """Return a tool listing entry shaped like the SDK's."""
    return SimpleNamespace(id=tool_id, tool_config=SimpleNamespace(name=name))


def _agent_with(tool_ids: list[str]) -> SimpleNamespace:
    """Return an agent whose prompt has these tools attached."""
    return SimpleNamespace(
        conversation_config=SimpleNamespace(
            agent=SimpleNamespace(prompt=SimpleNamespace(tool_ids=tool_ids))
        )
    )


@contextmanager
def _patch_client_and_url(
    agents: object = None,
    list_side_effect: Exception | None = None,
    attached: list[str] | None = None,
    tools: list[object] | None = None,
) -> Iterator[None]:
    """
    Patch the client, the listings and the URL check.

    attached and tools default to a workspace with no colliding name, so a
    test that does not care about the collision step never meets it.
    """
    with ExitStack() as stack:
        stack.enter_context(
            patch("custom_components.elevenagents.config_flow.async_get_client")
        )
        stack.enter_context(
            patch(
                "custom_components.elevenagents.config_flow.async_list_agents",
                return_value=agents,
                side_effect=list_side_effect,
            )
        )
        stack.enter_context(
            patch(
                "custom_components.elevenagents.config_flow.async_get_agent",
                return_value=_agent_with(attached or []),
            )
        )
        stack.enter_context(
            patch(
                "custom_components.elevenagents.config_flow.async_list_tools",
                return_value=SimpleNamespace(tools=tools or []),
            )
        )
        stack.enter_context(
            patch(
                "custom_components.elevenagents.config_flow.get_url",
                return_value="https://home.example",
            )
        )
        yield


async def test_successful_setup_lists_agents_then_creates_the_entry(hass):
    """Step one validates the key; step two picks the agent from a dropdown."""
    with _patch_client_and_url(agents=AGENTS_RESPONSE):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], API_KEY_INPUT
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "agent"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_AGENT_ID: "agent_a"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == "sk_test"
    assert result["data"][CONF_AGENT_ID] == "agent_a"
    assert result["data"][CONF_WEBHOOK_ID]
    assert result["data"][CONF_WEBHOOK_SECRET]
    assert result["data"][CONF_WEBHOOK_ID] != result["data"][CONF_WEBHOOK_SECRET]
    assert result["options"] == {}
    assert result["description_placeholders"] == {"panel_url": "/elevenagents"}


async def test_agent_step_offers_a_dropdown_sorted_by_name(hass):
    """The picker is labelled by name and valued by ID, sorted for a long list."""
    with _patch_client_and_url(agents=AGENTS_RESPONSE):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], API_KEY_INPUT
        )

    selector = result["data_schema"].schema[CONF_AGENT_ID]
    assert selector.config["options"] == [
        {"value": "agent_a", "label": "Alpha"},
        {"value": "agent_b", "label": "Bravo"},
    ]


async def test_empty_workspace_reports_no_agents(hass):
    """An empty workspace cannot offer a dropdown, so step one stops here."""
    with _patch_client_and_url(agents=SimpleNamespace(agents=[])):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], API_KEY_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"]["base"] == "no_agents"


async def test_revoked_key_reports_invalid_auth(hass):
    """A 401 from listing agents is a credentials problem."""
    with _patch_client_and_url(list_side_effect=ElevenLabsAuthError("nope")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], API_KEY_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"]["base"] == "invalid_auth"


async def test_key_without_convai_scope_reports_insufficient_scope(hass):
    """A 403 means the key cannot reach convai. It is not that the key is wrong."""
    with _patch_client_and_url(list_side_effect=ElevenLabsScopeError("forbidden")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], API_KEY_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "insufficient_scope"


async def test_other_failure_listing_agents_reports_cannot_connect(hass):
    """Any other ElevenLabsError while listing agents is a connectivity problem."""
    with _patch_client_and_url(list_side_effect=ElevenLabsError("boom")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], API_KEY_INPUT
        )

    assert result["errors"]["base"] == "cannot_connect"


async def test_picking_an_already_configured_agent_aborts(hass, config_entry):
    """
    The same agent cannot be onboarded twice, even under a different name.

    single_config_entry blocks a second "user" flow outright once one entry
    exists, before any step runs, which would hide the unique_id check this
    test targets. async_has_entries is patched out to isolate that check.
    """
    agents = SimpleNamespace(
        agents=[SimpleNamespace(agent_id=config_entry.unique_id, name="Existing")]
    )
    with (
        _patch_client_and_url(agents=agents),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_has_entries",
            return_value=False,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], API_KEY_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_AGENT_ID: config_entry.unique_id}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_no_external_url_aborts_before_asking_for_anything(hass):
    """A tool pointing nowhere is worse than refusing to set up."""
    with patch(
        "custom_components.elevenagents.config_flow.get_url",
        side_effect=NoURLAvailableError,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_external_url"


async def test_an_internal_url_alone_is_not_enough(hass) -> None:
    """
    prefer_external only orders the preference; it does not require one.

    MEASURED 12 September 2026 against a running instance with no external
    URL: the flow created the entry and would have written
    http://192.168.1.10:8123/api/webhook/... into the user's ElevenLabs
    workspace, an address the agent can never reach.
    """
    await hass.config.async_update(
        internal_url="http://192.168.1.10:8123", external_url=None
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_external_url"


async def test_reauth_updates_the_api_key_and_keeps_everything_else(hass, config_entry):
    """Reauth re-asks for the API key only and keeps agent id, webhook id and secret."""
    with (
        patch("custom_components.elevenagents.config_flow.async_get_client"),
        patch("custom_components.elevenagents.config_flow.async_get_agent"),
    ):
        result = await config_entry.start_reauth_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "sk_new"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_API_KEY] == "sk_new"
    assert config_entry.data[CONF_WEBHOOK_ID] == "webhook_test"
    assert config_entry.data[CONF_WEBHOOK_SECRET] == "secret_test"


async def test_reauth_with_a_still_revoked_key_reports_invalid_auth(hass, config_entry):
    """A reauth submission is validated exactly like the initial setup."""
    with (
        patch("custom_components.elevenagents.config_flow.async_get_client"),
        patch(
            "custom_components.elevenagents.config_flow.async_get_agent",
            side_effect=ElevenLabsAuthError("still no"),
        ),
    ):
        result = await config_entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "sk_still_bad"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_auth"
    assert config_entry.data[CONF_API_KEY] == "sk_test"


async def test_reauth_with_a_deleted_agent_reports_agent_not_found(hass, config_entry):
    """Reauth still needs agent_not_found: the agent id is not re-asked here."""
    with (
        patch("custom_components.elevenagents.config_flow.async_get_client"),
        patch(
            "custom_components.elevenagents.config_flow.async_get_agent",
            side_effect=ElevenLabsNotFoundError("gone"),
        ),
    ):
        result = await config_entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "sk_test"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "agent_not_found"


async def test_no_collision_creates_the_entry_without_a_word(hass) -> None:
    """A step that always appears is a step nobody reads."""
    with _patch_client_and_url(
        agents=AGENTS_RESPONSE,
        attached=["tool_theirs"],
        tools=[_tool("tool_theirs", "weather")],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], API_KEY_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_AGENT_ID: "agent_a"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_a_colliding_name_on_the_agent_stops_to_ask(hass) -> None:
    """The harm is at the attachment, so that is what is checked."""
    with _patch_client_and_url(
        agents=AGENTS_RESPONSE,
        attached=["tool_old"],
        tools=[_tool("tool_old", "ha_call_service")],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], API_KEY_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_AGENT_ID: "agent_a"}
        )

    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"detach", "keep"}


async def test_the_same_name_unattached_is_not_the_agents_problem(hass) -> None:
    """
    A duplicate sitting in the workspace is untidy, not harmful.

    The agent carries an attachment here on purpose. With none at all the
    check returns before it ever reads the workspace, so the test would
    pass whether the comparison looked at the attachments or not.
    """
    with _patch_client_and_url(
        agents=AGENTS_RESPONSE,
        attached=["tool_theirs"],
        tools=[
            _tool("tool_theirs", "weather"),
            _tool("tool_old", "ha_call_service"),
        ],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], API_KEY_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_AGENT_ID: "agent_a"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_detach_removes_only_the_colliding_tools(hass) -> None:
    """A tool the user attached for another purpose survives."""
    with (
        _patch_client_and_url(
            agents=AGENTS_RESPONSE,
            attached=["tool_old", "tool_theirs"],
            tools=[
                _tool("tool_old", "ha_call_service"),
                _tool("tool_theirs", "weather"),
            ],
        ),
        patch(
            "custom_components.elevenagents.config_flow.async_patch_agent"
        ) as patch_agent,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], API_KEY_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_AGENT_ID: "agent_a"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "detach"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    sent = patch_agent.call_args.kwargs["conversation_config"]
    assert sent["agent"]["prompt"]["tool_ids"] == ["tool_theirs"]


async def test_continuing_anyway_touches_nothing(hass) -> None:
    """Their workspace, their call, now made knowingly."""
    with (
        _patch_client_and_url(
            agents=AGENTS_RESPONSE,
            attached=["tool_old"],
            tools=[_tool("tool_old", "ha_call_service")],
        ),
        patch(
            "custom_components.elevenagents.config_flow.async_patch_agent"
        ) as patch_agent,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], API_KEY_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_AGENT_ID: "agent_a"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "keep"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert not patch_agent.called
