"""Tests for the config flow."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.network import NoURLAvailableError

from custom_components.elevenagents.const import (
    CONF_AGENT_ID,
    CONF_API_KEY,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DOMAIN,
    OPT_PUBLIC_URL,
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
    *,
    external: bool = True,
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
                "custom_components.elevenagents.public_url.get_url",
                return_value="https://home.example",
                side_effect=None if external else NoURLAvailableError,
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
    assert result["description_placeholders"] == {
        "panel_url": "/elevenagents",
        "webhook_url": (
            f"https://home.example/api/webhook/{result['data'][CONF_WEBHOOK_ID]}"
        ),
        "webhook_path": f"/api/webhook/{result['data'][CONF_WEBHOOK_ID]}",
    }


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


def _public_url_marker(schema: vol.Schema) -> vol.Marker:
    """Return the schema key of the public URL field."""
    marker: vol.Marker = next(key for key in schema.schema if key == OPT_PUBLIC_URL)
    return marker


async def test_the_webhook_url_starts_as_the_external_url(hass):
    """The user sees the URL ElevenLabs will call and may change it."""
    with _patch_client_and_url(agents=AGENTS_RESPONSE):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )

    marker = _public_url_marker(result["data_schema"])
    assert type(marker) is vol.Optional
    assert marker.description == {"suggested_value": "https://home.example"}


async def test_the_webhook_url_starts_empty_without_an_external_url(hass):
    """A private Home Assistant has no URL to offer."""
    with _patch_client_and_url(agents=AGENTS_RESPONSE, external=False):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )

    assert not (_public_url_marker(result["data_schema"]).description or {}).get(
        "suggested_value"
    )


async def test_the_prefilled_external_url_stores_nothing(hass):
    """The webhook follows the external URL when the user keeps it."""
    with _patch_client_and_url(agents=AGENTS_RESPONSE):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**API_KEY_INPUT, OPT_PUBLIC_URL: "https://home.example"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_AGENT_ID: "agent_a"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {}


async def test_a_refused_value_comes_back_in_the_form(hass):
    """The user corrects the typed value, and the key is not echoed."""
    with _patch_client_and_url(agents=AGENTS_RESPONSE):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**API_KEY_INPUT, OPT_PUBLIC_URL: "https://hooks.example.com/ha"},
        )

    schema = result["data_schema"].schema
    assert _public_url_marker(result["data_schema"]).description == {
        "suggested_value": "https://hooks.example.com/ha"
    }
    api_key = next(key for key in schema if key == CONF_API_KEY)
    assert not (api_key.description or {}).get("suggested_value")


@pytest.mark.parametrize("value", [None, "", "   "])
async def test_an_empty_public_url_keeps_the_external_url(hass, value):
    """The tools use the external URL when the field is left empty."""
    user_input = dict(API_KEY_INPUT)
    if value is not None:
        user_input[OPT_PUBLIC_URL] = value
    with _patch_client_and_url(agents=AGENTS_RESPONSE):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_AGENT_ID: "agent_a"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {}


async def test_a_public_url_overrides_an_external_url_in_the_flow(hass):
    """An external URL that ElevenLabs cannot reach gets replaced from the start."""
    with _patch_client_and_url(agents=AGENTS_RESPONSE):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**API_KEY_INPUT, OPT_PUBLIC_URL: "https://hooks.example.com"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_AGENT_ID: "agent_a"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {OPT_PUBLIC_URL: "https://hooks.example.com"}


async def test_the_public_url_reaches_the_options_of_the_entry(hass):
    """The tools call the public URL from the first sync."""
    with _patch_client_and_url(agents=AGENTS_RESPONSE, external=False):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**API_KEY_INPUT, OPT_PUBLIC_URL: "https://Hooks.example.com/"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_AGENT_ID: "agent_a"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {OPT_PUBLIC_URL: "https://hooks.example.com"}


async def test_a_public_url_with_a_path_is_refused_in_the_flow(hass):
    """The webhook path is appended, so a path of our own would break it."""
    with _patch_client_and_url(agents=AGENTS_RESPONSE, external=False):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**API_KEY_INPUT, OPT_PUBLIC_URL: "https://hooks.example.com/ha"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {OPT_PUBLIC_URL: "invalid_public_url"}


async def test_an_internal_url_alone_is_not_enough(hass) -> None:
    """
    prefer_external only orders the preference; it does not require one.

    MEASURED 12 September 2026 against a running instance with no external
    URL: the flow created the entry and would have written
    http://192.168.1.10:8123/api/webhook/... into the user's ElevenLabs
    workspace, a URL the agent can never reach. The flow now asks for
    a public URL instead.
    """
    await hass.config.async_update(
        internal_url="http://192.168.1.10:8123", external_url=None
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    assert result["type"] is FlowResultType.FORM
    assert OPT_PUBLIC_URL in result["data_schema"].schema


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
        tools=[_tool("tool_old", "ha_perform_action")],
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
            _tool("tool_old", "ha_perform_action"),
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
                _tool("tool_old", "ha_perform_action"),
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
            tools=[_tool("tool_old", "ha_perform_action")],
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


@pytest.mark.parametrize("value", ["", "   "])
async def test_an_empty_public_url_is_refused_without_an_external_url(hass, value):
    """Without either URL the tools would point nowhere."""
    with _patch_client_and_url(agents=AGENTS_RESPONSE, external=False):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**API_KEY_INPUT, OPT_PUBLIC_URL: value}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {OPT_PUBLIC_URL: "no_public_url"}
