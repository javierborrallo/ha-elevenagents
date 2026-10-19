"""Tests for the connection check, its service and its WebSocket command."""

from __future__ import annotations

import copy
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import aiohttp
import httpx
import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component

from custom_components.elevenagents.check import CHECKS, async_run_check
from custom_components.elevenagents.const import (
    CONF_RESPONSE_TIMEOUT,
    CONF_TOOL_IDS,
    DEFAULT_RESPONSE_TIMEOUT,
    DOMAIN,
    OPT_CONFIRM_TIMEOUT,
    OPT_PUBLIC_URL,
    OPT_READ_ENTITIES,
    TOOL_CALL_SERVICE,
    TOOL_GET_STATE,
)
from custom_components.elevenagents.elevenlabs_client import (
    ElevenLabsAuthError,
    ElevenLabsError,
    ElevenLabsNotFoundError,
    ElevenLabsScopeError,
)
from custom_components.elevenagents.tool_sync import async_build_definitions

if TYPE_CHECKING:
    from pytest_homeassistant_custom_component.typing import WebSocketGenerator

# homeassistant 2026.9.1's http server sets self.app["hass"] as a plain key.
# aiohttp 3.14.3 raises NotAppKeyWarning for that pattern. pyproject.toml
# turns every warning into an error, so this module ignores this one category.
pytestmark = pytest.mark.filterwarnings(
    "ignore::aiohttp.web_exceptions.NotAppKeyWarning"
)

TOOL_IDS = {TOOL_GET_STATE: "tool_get", TOOL_CALL_SERVICE: "tool_call"}


def _agent(*, protected: bool = True, tool_ids: list[str] | None = None) -> Any:
    """Build an agent read with the two fields the check looks at."""
    attached = list(TOOL_IDS.values()) if tool_ids is None else tool_ids
    return SimpleNamespace(
        platform_settings=SimpleNamespace(
            auth=SimpleNamespace(enable_auth=protected, allowlist=[])
        ),
        conversation_config=SimpleNamespace(
            agent=SimpleNamespace(prompt=SimpleNamespace(tool_ids=attached))
        ),
    )


@pytest.fixture
async def synced(hass, setup_entry):
    """Store the tool ids, and return the definitions a sync would send now."""
    hass.config_entries.async_update_entry(
        setup_entry, data={**setup_entry.data, CONF_TOOL_IDS: TOOL_IDS}
    )
    return await async_build_definitions(hass, setup_entry)


WEBHOOK_URL = "https://home.example/api/webhook/webhook_test"


@pytest.fixture
def elevenlabs(synced, aioclient_mock):
    """Answer the agent and tool reads as a healthy workspace would."""
    aioclient_mock.post(WEBHOOK_URL, status=401)
    by_id = {TOOL_IDS[name]: config for name, config in synced.items()}

    async def get_tool(_client: object, tool_id: str) -> SimpleNamespace:
        return SimpleNamespace(tool_config=by_id[tool_id])

    with (
        patch(
            "custom_components.elevenagents.check.async_get_agent",
            AsyncMock(return_value=_agent()),
        ) as get_agent,
        patch(
            "custom_components.elevenagents.check.async_get_tool",
            AsyncMock(side_effect=get_tool),
        ) as get_tool_mock,
    ):
        yield SimpleNamespace(get_agent=get_agent, get_tool=get_tool_mock, by_id=by_id)


def _status(result: dict[str, Any]) -> dict[str, str]:
    """Map each check to its status."""
    return {item["check"]: item["status"] for item in result["checks"]}


def _detail(result: dict[str, Any], check: str) -> Any:
    """Return the detail of one check."""
    return next(item["detail"] for item in result["checks"] if item["check"] == check)


async def test_a_healthy_setup_passes_every_check(hass, setup_entry, elevenlabs):
    """The owner reads one line to know everything works."""
    result = await async_run_check(hass, setup_entry)

    assert result["ok"] is True
    assert _status(result) == {
        "loaded": "ok",
        "key": "ok",
        "protected": "ok",
        "tools_attached": "ok",
        "tools_current": "ok",
        "webhook": "ok",
        "timeouts": "ok",
        "missing_entities": "ok",
    }


async def test_a_refused_key_skips_what_needs_elevenlabs(hass, setup_entry, elevenlabs):
    """Three failures that share one cause would bury the cause."""
    elevenlabs.get_agent.side_effect = ElevenLabsAuthError("401")

    result = await async_run_check(hass, setup_entry)

    status = _status(result)
    assert result["ok"] is False
    assert status["key"] == "problem"
    assert status["protected"] == "skipped"
    assert status["tools_attached"] == "skipped"
    assert status["tools_current"] == "skipped"
    assert status["timeouts"] == "ok"
    assert status["missing_entities"] == "ok"


async def test_an_open_agent_is_a_problem(hass, setup_entry, elevenlabs):
    """Anyone holding the agent id can talk to it."""
    elevenlabs.get_agent.return_value = _agent(protected=False)

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["protected"] == "problem"


async def test_a_detached_tool_is_named(hass, setup_entry, elevenlabs):
    """A tool removed from the agent by hand leaves the agent blind or mute."""
    elevenlabs.get_agent.return_value = _agent(tool_ids=["tool_get"])

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["tools_attached"] == "problem"
    assert _detail(result, "tools_attached") == [TOOL_CALL_SERVICE]


async def test_a_stale_tool_is_named(hass, setup_entry, entity_registry, elevenlabs):
    """A selection the agent never received is the failure this check exists for."""
    light = entity_registry.async_get_or_create("light", "demo", "new")
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_READ_ENTITIES: [light.id]}
    )

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["tools_current"] == "problem"
    assert _detail(result, "tools_current") == {
        "tools": [TOOL_GET_STATE],
        "missing": [light.entity_id],
        "extra": [],
        "unreadable": [],
    }


async def test_a_tool_deleted_in_elevenlabs_is_named(hass, setup_entry, elevenlabs):
    """The next sync recreates it; until then the agent lacks it."""
    elevenlabs.get_tool.side_effect = ElevenLabsNotFoundError("404")

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["tools_current"] == "problem"
    assert _detail(result, "tools_current") == {
        "tools": [TOOL_CALL_SERVICE, TOOL_GET_STATE],
        "missing": [],
        "extra": [],
        "unreadable": [TOOL_CALL_SERVICE, TOOL_GET_STATE],
    }


async def test_a_tool_read_that_errors_is_unreadable(hass, setup_entry, elevenlabs):
    """A server error on a tool read is treated like a tool nobody can read."""
    elevenlabs.get_tool.side_effect = ElevenLabsError("500")

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["tools_current"] == "problem"
    assert _detail(result, "tools_current") == {
        "tools": [TOOL_CALL_SERVICE, TOOL_GET_STATE],
        "missing": [],
        "extra": [],
        "unreadable": [TOOL_CALL_SERVICE, TOOL_GET_STATE],
    }


async def test_a_tool_read_that_cannot_reach_elevenlabs_is_unreadable(
    hass, setup_entry, elevenlabs
):
    """A transport error on a tool read is treated the same as an API error."""
    elevenlabs.get_tool.side_effect = httpx.ConnectError("down")

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["tools_current"] == "problem"
    assert _detail(result, "tools_current") == {
        "tools": [TOOL_CALL_SERVICE, TOOL_GET_STATE],
        "missing": [],
        "extra": [],
        "unreadable": [TOOL_CALL_SERVICE, TOOL_GET_STATE],
    }


async def test_an_unreachable_agent_read_skips_the_remote_checks(
    hass, setup_entry, elevenlabs
):
    """ElevenLabs unreachable skips the checks that need it, like a refused key."""
    elevenlabs.get_agent.side_effect = httpx.ConnectError("down")

    result = await async_run_check(hass, setup_entry)

    status = _status(result)
    assert result["ok"] is False
    assert status["key"] == "problem"
    assert _detail(result, "key") == "unreachable"
    assert status["protected"] == "skipped"
    assert status["tools_attached"] == "skipped"
    assert status["tools_current"] == "skipped"


# --- I3: the key check uses a fixed vocabulary, never the raw error ---------


async def test_a_forbidden_key_reports_scope(hass, setup_entry, elevenlabs):
    """A key that lacks the scope to read the agent is not the same as a wrong key."""
    elevenlabs.get_agent.side_effect = ElevenLabsScopeError("403")

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["key"] == "problem"
    assert _detail(result, "key") == "scope"


async def test_a_deleted_agent_reports_agent_not_found(hass, setup_entry, elevenlabs):
    """The agent id is wrong or the agent was deleted; the key itself is fine."""
    elevenlabs.get_agent.side_effect = ElevenLabsNotFoundError("404")

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["key"] == "problem"
    assert _detail(result, "key") == "agent_not_found"


async def test_a_5xx_key_failure_reports_the_status(hass, setup_entry, elevenlabs):
    """A status this integration does not name still reaches the user as a number."""
    elevenlabs.get_agent.side_effect = ElevenLabsError("boom", status_code=500)

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["key"] == "problem"
    assert _detail(result, "key") == "HTTP 500"


async def test_a_key_failure_with_no_status_reports_unreachable(
    hass, setup_entry, elevenlabs
):
    """An ElevenLabsError with no status code is a transport failure too."""
    elevenlabs.get_agent.side_effect = ElevenLabsError("boom")

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["key"] == "problem"
    assert _detail(result, "key") == "unreachable"


async def test_the_key_check_never_puts_the_raw_error_in_the_detail(
    hass, setup_entry, elevenlabs
):
    """A header dump must never reach the recorder or the user."""
    elevenlabs.get_agent.side_effect = ElevenLabsError(
        "headers: {'x': 'y'}, status_code: 500, body: {}", status_code=500
    )

    result = await async_run_check(hass, setup_entry)

    assert "headers" not in str(_detail(result, "key"))
    assert "body" not in str(_detail(result, "key"))


async def test_the_key_check_logs_the_full_error_at_debug(
    hass, setup_entry, elevenlabs, caplog
):
    """The administrator can still see the real error, at debug level."""
    caplog.set_level(logging.DEBUG)
    elevenlabs.get_agent.side_effect = ElevenLabsError(
        "the real detail", status_code=500
    )

    await async_run_check(hass, setup_entry)

    assert any(
        "the real detail" in r.getMessage()
        for r in caplog.records
        if r.name.startswith("custom_components")
    )


async def test_an_entity_left_on_the_agent_is_extra(hass, setup_entry, elevenlabs):
    """A tool edited by hand can hold an entity nobody selected."""
    edited = copy.deepcopy(elevenlabs.by_id["tool_get"])
    schema = edited["api_schema"]["request_body_schema"]
    schema["properties"]["entity_id"]["items"]["enum"] = ["light.old"]
    elevenlabs.by_id["tool_get"] = edited

    result = await async_run_check(hass, setup_entry)

    assert _detail(result, "tools_current") == {
        "tools": [TOOL_GET_STATE],
        "missing": [],
        "extra": ["light.old"],
        "unreadable": [],
    }


async def test_a_tool_with_no_enum_is_named_but_not_unreadable(
    hass, setup_entry, elevenlabs
):
    """A hand edit that removed the enum is a real difference, not a read failure."""
    edited = copy.deepcopy(elevenlabs.by_id["tool_get"])
    del edited["api_schema"]["request_body_schema"]["properties"]["entity_id"]
    elevenlabs.by_id["tool_get"] = edited

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["tools_current"] == "problem"
    detail = _detail(result, "tools_current")
    assert detail["tools"] == [TOOL_GET_STATE]
    assert detail["unreadable"] == []


async def test_a_changed_description_names_the_tool_only(hass, setup_entry, elevenlabs):
    """A field other than the enum names the tool and no entity."""
    edited = copy.deepcopy(elevenlabs.by_id["tool_call"])
    edited["description"] = "edited by hand"
    elevenlabs.by_id["tool_call"] = edited

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["tools_current"] == "problem"
    assert _detail(result, "tools_current") == {
        "tools": [TOOL_CALL_SERVICE],
        "missing": [],
        "extra": [],
        "unreadable": [],
    }


def test_checks_lists_the_report_in_order():
    """The sensors and the report use one list of names."""
    assert CHECKS == (
        "loaded",
        "key",
        "protected",
        "tools_attached",
        "tools_current",
        "webhook",
        "timeouts",
        "missing_entities",
    )


async def test_crossed_timeouts_are_a_problem(hass, setup_entry, elevenlabs):
    """The same rule as the timeout_crossed repair."""
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_CONFIRM_TIMEOUT: 999}
    )

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["timeouts"] == "problem"


async def test_equal_timeouts_are_a_problem(hass, setup_entry, elevenlabs):
    """The panel and the README say 'shorter', not 'at most'."""
    agent_timeout = int(
        setup_entry.data.get(CONF_RESPONSE_TIMEOUT, DEFAULT_RESPONSE_TIMEOUT)
    )
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_CONFIRM_TIMEOUT: agent_timeout}
    )

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["timeouts"] == "problem"


async def test_a_missing_entity_is_named(hass, setup_entry, elevenlabs):
    """The same list the panel and the repair show."""
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_READ_ENTITIES: ["sensor.gone"]}
    )

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["missing_entities"] == "problem"
    assert _detail(result, "missing_entities") == ["sensor.gone"]


async def test_an_unloaded_entry_is_still_checked(hass, setup_entry, elevenlabs):
    """The entry is in trouble precisely when someone runs this."""
    await hass.config_entries.async_unload(setup_entry.entry_id)

    with patch(
        "custom_components.elevenagents.check.async_get_client",
        return_value=object(),
    ):
        result = await async_run_check(hass, setup_entry)

    assert _status(result)["loaded"] == "problem"
    assert _status(result)["key"] == "ok"


# --- M2: the webhook probe is skipped when the entry is not loaded ----------


async def test_the_webhook_check_is_skipped_when_the_entry_is_not_loaded(
    hass, setup_entry, elevenlabs
):
    """The handler is unregistered then; a 200 would only prove it is not ours."""
    await hass.config_entries.async_unload(setup_entry.entry_id)

    with patch(
        "custom_components.elevenagents.check.async_get_client",
        return_value=object(),
    ):
        result = await async_run_check(hass, setup_entry)

    assert _status(result)["webhook"] == "skipped"


async def test_the_webhook_check_still_runs_while_loaded(hass, setup_entry, elevenlabs):
    """The skip must not swallow the real probe for a loaded entry."""
    result = await async_run_check(hass, setup_entry)

    assert _status(result)["webhook"] == "ok"


async def test_the_service_answers_with_the_result(hass, setup_entry, elevenlabs):
    """Developer Tools and automations read the same result as the panel."""
    result = await hass.services.async_call(
        DOMAIN,
        "check",
        {"config_entry_id": setup_entry.entry_id},
        blocking=True,
        return_response=True,
    )

    assert result is not None
    assert result["ok"] is True


# --- M5: the check service accepts an entry that is not loaded --------------


async def test_the_service_accepts_an_entry_that_is_not_loaded(
    hass, setup_entry, elevenlabs
):
    """An automation must get a report, not an exception, for an unloaded entry."""
    await hass.config_entries.async_unload(setup_entry.entry_id)

    result = await hass.services.async_call(
        DOMAIN,
        "check",
        {"config_entry_id": setup_entry.entry_id},
        blocking=True,
        return_response=True,
    )

    assert result is not None
    status = {item["check"]: item["status"] for item in result["checks"]}
    assert status["loaded"] == "problem"


async def test_the_service_still_rejects_an_unknown_entry(hass):
    """The lookup still validates the entry exists and belongs to this domain."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "check",
            {"config_entry_id": "not_a_real_entry"},
            blocking=True,
            return_response=True,
        )


async def test_the_websocket_command_answers_with_the_result(
    hass, setup_entry, elevenlabs, hass_ws_client: WebSocketGenerator
):
    """The panel's status card runs the same check."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "elevenagents/check", "entry_id": setup_entry.entry_id}
    )
    result = (await client.receive_json())["result"]

    assert result["ok"] is True
    assert len(result["checks"]) == 8


async def test_an_unreachable_webhook_is_a_problem(
    hass, setup_entry, elevenlabs, aioclient_mock
):
    """A dead external URL fails every tool call, and the tools still match."""
    aioclient_mock.clear_requests()
    aioclient_mock.post(WEBHOOK_URL, exc=aiohttp.ClientConnectionError())

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["webhook"] == "problem"
    assert _detail(result, "webhook") == "ClientConnectionError"
    assert _status(result)["tools_current"] == "ok"


async def test_a_webhook_that_is_not_ours_is_a_problem(
    hass, setup_entry, elevenlabs, aioclient_mock
):
    """Only this integration's handler answers 401 without the secret."""
    aioclient_mock.clear_requests()
    aioclient_mock.post(WEBHOOK_URL, status=200)

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["webhook"] == "problem"
    assert _detail(result, "webhook") == "HTTP 200"


async def test_the_webhook_probe_carries_no_secret(
    hass, setup_entry, elevenlabs, aioclient_mock
):
    """The probe must be refused, so it never reaches the tools."""
    await async_run_check(hass, setup_entry)

    calls = [call for call in aioclient_mock.mock_calls if str(call[1]) == WEBHOOK_URL]
    assert len(calls) == 1
    headers = calls[0][3] or {}
    assert "X-Elevenlabs-Agent-Secret" not in headers


async def test_no_external_url_is_a_problem(hass, setup_entry, elevenlabs):
    """ElevenLabs can reach nothing without an external URL."""
    await hass.config.async_update(external_url=None)

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["webhook"] == "problem"
    assert _detail(result, "webhook") == "no external URL"
    assert _status(result)["tools_current"] == "problem"
    assert _detail(result, "tools_current") == "no external URL"


async def test_the_webhook_probe_calls_the_public_url(
    hass, setup_entry, elevenlabs, aioclient_mock
):
    """The probe tests the address ElevenLabs calls, not the external URL."""
    public = "https://hooks.example.com/api/webhook/webhook_test"
    aioclient_mock.post(public, status=401)
    await hass.config.async_update(external_url=None)
    hass.config_entries.async_update_entry(
        setup_entry,
        options={**setup_entry.options, OPT_PUBLIC_URL: "https://hooks.example.com"},
    )

    result = await async_run_check(hass, setup_entry)

    assert _status(result)["webhook"] == "ok"
    probed = [str(call[1]) for call in aioclient_mock.mock_calls]
    assert public in probed
    assert WEBHOOK_URL not in probed
