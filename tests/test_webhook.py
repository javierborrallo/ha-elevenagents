"""Tests for webhook transport, dispatch, the read path and the write path."""

from __future__ import annotations

import asyncio
import hmac
import json
from datetime import timedelta
from typing import TYPE_CHECKING, Never
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.service import async_set_service_schema
from homeassistant.util import dt as dt_util
from homeassistant.util.aiohttp import MockRequest

from custom_components.elevenagents.const import (
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    EVENT_ACTION,
    EVENT_CONVERSATION_GRACE_SECONDS,
    MAX_BODY_BYTES,
    OPT_CONFIRM_TIMEOUT,
    OPT_CONTROL_ENTITIES,
    OPT_EXCEPTIONS,
    OPT_READ_ENTITIES,
    OPT_SENSITIVE,
    OUTCOME_CONFIRMATION_REQUIRED,
    OUTCOME_DONE,
    OUTCOME_FAILED,
    OUTCOME_NO_CHANGE,
    OUTCOME_UNCONFIRMED,
    RATE_LIMIT_BURST,
    SECRET_HEADER,
)
from custom_components.elevenagents.webhook import (
    TokenBucket,
    async_handle_webhook,
    async_read,
    async_write,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from homeassistant.helpers import entity_registry as er

# homeassistant 2026.9.1's http server sets self.app["hass"] as a plain key.
# aiohttp 3.14.3 raises NotAppKeyWarning for that pattern. pyproject.toml
# turns every warning into an error, so this module ignores this one category.
pytestmark = pytest.mark.filterwarnings(
    "ignore::aiohttp.web_exceptions.NotAppKeyWarning"
)


@pytest.fixture
def no_network_sync():
    """
    Keep a debounced registry sync off the network.

    A test that jumps the clock far enough can make the registry's own
    debounced sync (triggered by creating an entity) look due. Nothing in
    this module needs that sync to run.
    """
    with patch("custom_components.elevenagents.expose.async_sync_tools", AsyncMock()):
        yield


def test_token_bucket_allows_the_burst_then_refuses():
    """Ten tokens, refilled one per second."""
    clock = [0.0]
    bucket = TokenBucket(burst=10, per_second=1.0, now=lambda: clock[0])

    assert all(bucket.take() for _ in range(10))
    assert bucket.take() is False

    clock[0] = 2.5
    assert bucket.take() is True
    assert bucket.take() is True
    assert bucket.take() is False


async def test_wrong_secret_is_401_with_no_body(hass, hass_client_no_auth, setup_entry):
    """A bad secret is transport, not conversation."""
    client = await hass_client_no_auth()

    response = await client.post(
        f"/api/webhook/{setup_entry.data[CONF_WEBHOOK_ID]}",
        headers={SECRET_HEADER: "wrong"},
        json={"tool": "get_state", "entity_id": []},
    )

    assert response.status == 401
    assert await response.text() == ""


async def test_bad_secret_never_consumes_a_token(
    hass, hass_client_no_auth, setup_entry
):
    """The secret is checked before the bucket, so a bad secret cannot drain it."""
    client = await hass_client_no_auth()

    for _ in range(RATE_LIMIT_BURST + 1):
        response = await client.post(
            f"/api/webhook/{setup_entry.data[CONF_WEBHOOK_ID]}",
            headers={SECRET_HEADER: "wrong"},
            json={"tool": "get_state", "entity_id": []},
        )
        assert response.status == 401

    response = await client.post(
        f"/api/webhook/{setup_entry.data[CONF_WEBHOOK_ID]}",
        headers={SECRET_HEADER: setup_entry.data[CONF_WEBHOOK_SECRET]},
        json={"tool": "get_state", "entity_id": []},
    )
    assert response.status == 200


async def test_secret_check_uses_compare_digest_on_bytes(
    hass, hass_client_no_auth, setup_entry, monkeypatch
):
    """The secret comparison runs through hmac.compare_digest on encoded bytes."""
    calls: list[tuple[object, object]] = []
    original = hmac.compare_digest

    def spy(a, b) -> bool:
        calls.append((a, b))
        return original(a, b)

    monkeypatch.setattr(
        "custom_components.elevenagents.webhook.hmac.compare_digest", spy
    )
    client = await hass_client_no_auth()

    await client.post(
        f"/api/webhook/{setup_entry.data[CONF_WEBHOOK_ID]}",
        headers={SECRET_HEADER: setup_entry.data[CONF_WEBHOOK_SECRET]},
        json={"tool": "get_state", "entity_id": []},
    )

    assert len(calls) == 1
    assert all(isinstance(side, bytes) for side in calls[0])


async def test_handles_a_mock_request_with_no_content_type_attribute(hass, setup_entry):
    """
    The webhook dispatcher calls handlers with MockRequest for cloudhooks.

    MockRequest carries headers but no content_type attribute.
    """
    request = MockRequest(
        content=json.dumps({"tool": "get_state", "entity_id": []}).encode(),
        mock_source="test",
        method="POST",
        headers={
            SECRET_HEADER: setup_entry.data[CONF_WEBHOOK_SECRET],
            "Content-Type": "application/json",
        },
    )

    response = await async_handle_webhook(
        hass,
        setup_entry.data[CONF_WEBHOOK_ID],
        request,  # type: ignore[arg-type]
    )

    assert response.status == 200


async def test_wrong_content_type_is_415(hass, hass_client_no_auth, setup_entry):
    """Refused before the body is parsed."""
    client = await hass_client_no_auth()

    response = await client.post(
        f"/api/webhook/{setup_entry.data[CONF_WEBHOOK_ID]}",
        headers={
            SECRET_HEADER: setup_entry.data[CONF_WEBHOOK_SECRET],
            "Content-Type": "text/plain",
        },
        data="hello",
    )

    assert response.status == 415


async def test_oversized_body_is_413(hass, hass_client_no_auth, setup_entry):
    """Refused from Content-Length, before the body is read."""
    client = await hass_client_no_auth()

    response = await client.post(
        f"/api/webhook/{setup_entry.data[CONF_WEBHOOK_ID]}",
        headers={
            SECRET_HEADER: setup_entry.data[CONF_WEBHOOK_SECRET],
            "Content-Type": "application/json",
        },
        data=b"x" * (MAX_BODY_BYTES + 1),
    )

    assert response.status == 413


async def _pieces(body: bytes, size: int) -> AsyncIterator[bytes]:
    """Yield the body in pieces, with a pause between them."""
    for start in range(0, len(body), size):
        yield body[start : start + size]
        await asyncio.sleep(0.05)


async def test_a_body_that_arrives_in_pieces_is_read_whole(
    hass, hass_client_no_auth, setup_entry
):
    """The webhook reads to the end of the body, not the first piece."""
    client = await hass_client_no_auth()
    body = json.dumps({"tool": "nonsense", "padding": "x" * 20_000}).encode()

    response = await client.post(
        f"/api/webhook/{setup_entry.data[CONF_WEBHOOK_ID]}",
        headers={
            SECRET_HEADER: setup_entry.data[CONF_WEBHOOK_SECRET],
            "Content-Type": "application/json",
        },
        data=_pieces(body, 4096),
    )

    assert response.status == 200
    detail = json.loads(await response.text())["detail"]
    assert detail != "body is not valid JSON"


async def test_an_oversized_body_in_pieces_is_413(
    hass, hass_client_no_auth, setup_entry
):
    """A body without Content-Length is refused once it passes the limit."""
    client = await hass_client_no_auth()
    body = b"x" * (MAX_BODY_BYTES + 1)

    response = await client.post(
        f"/api/webhook/{setup_entry.data[CONF_WEBHOOK_ID]}",
        headers={
            SECRET_HEADER: setup_entry.data[CONF_WEBHOOK_SECRET],
            "Content-Type": "application/json",
        },
        data=_pieces(body, 64 * 1024),
    )

    assert response.status == 413


async def test_unknown_discriminator_is_200_and_malformed(
    hass, hass_client_no_auth, setup_entry
):
    """A body the agent could correct comes back as words, not a number."""
    client = await hass_client_no_auth()

    response = await client.post(
        f"/api/webhook/{setup_entry.data[CONF_WEBHOOK_ID]}",
        headers={SECRET_HEADER: setup_entry.data[CONF_WEBHOOK_SECRET]},
        json={"tool": "nonsense"},
    )

    assert response.status == 200
    body = json.loads(await response.text())
    assert body["error"] == "malformed_request"
    assert "detail" in body


async def test_unparseable_body_is_200_and_malformed(
    hass, hass_client_no_auth, setup_entry
):
    """Broken JSON is the agent's to fix, so it gets a body."""
    client = await hass_client_no_auth()

    response = await client.post(
        f"/api/webhook/{setup_entry.data[CONF_WEBHOOK_ID]}",
        headers={
            SECRET_HEADER: setup_entry.data[CONF_WEBHOOK_SECRET],
            "Content-Type": "application/json",
        },
        data="{not json",
    )

    assert response.status == 200
    assert json.loads(await response.text())["error"] == "malformed_request"


async def test_read_returns_one_result_per_entity_and_one_failure_does_not_fail_the_batch(  # noqa: E501
    hass, config_entry, entity_registry
):
    """Six readable lights are not discarded because a seventh is missing."""
    one = entity_registry.async_get_or_create("light", "demo", "one")
    hass.states.async_set(one.entity_id, "on", {"friendly_name": "One"})

    hass.config_entries.async_update_entry(
        config_entry, options={OPT_READ_ENTITIES: [one.id]}
    )

    payload = {"tool": "get_state", "entity_id": [one.entity_id, "light.gone"]}
    body = await async_read(hass, config_entry, payload)

    assert "ok" not in body
    assert body["results"][0]["state"] == "on"
    assert body["results"][0]["name"] == "One"
    assert body["results"][1] == {"entity_id": "light.gone", "error": "not_found"}


async def test_read_of_an_unselected_entity_is_not_found(
    hass, config_entry, entity_registry
):
    """An entity that exists but was never selected reveals nothing."""
    lock = entity_registry.async_get_or_create("lock", "demo", "front")
    hass.states.async_set(lock.entity_id, "locked", {})

    hass.config_entries.async_update_entry(config_entry, options={})

    body = await async_read(
        hass, config_entry, {"tool": "get_state", "entity_id": [lock.entity_id]}
    )

    assert body["results"] == [{"entity_id": lock.entity_id, "error": "not_found"}]


async def test_read_reaches_entities_selected_only_for_control(
    hass, config_entry, entity_registry
):
    """Control implies read."""
    one = entity_registry.async_get_or_create("light", "demo", "one")
    hass.states.async_set(one.entity_id, "on", {})

    hass.config_entries.async_update_entry(
        config_entry, options={OPT_CONTROL_ENTITIES: [one.id]}
    )

    body = await async_read(
        hass, config_entry, {"tool": "get_state", "entity_id": [one.entity_id]}
    )

    assert body["results"][0]["state"] == "on"


async def test_read_with_a_non_list_entity_id_is_malformed(hass, config_entry):
    """The schema says array; a string is the agent's to fix."""
    body = await async_read(
        hass, config_entry, {"tool": "get_state", "entity_id": "light.one"}
    )

    assert body["error"] == "malformed_request"


async def test_write_that_changes_state_is_done_and_carries_the_projection(
    hass, config_entry, entity_registry
):
    """last_updated advanced, so Home Assistant wrote."""
    one = entity_registry.async_get_or_create("light", "demo", "one")
    hass.states.async_set(one.entity_id, "off", {"friendly_name": "One"})

    async def turn_on(call) -> None:
        hass.states.async_set(one.entity_id, "on", {"friendly_name": "One"})

    hass.services.async_register("light", "turn_on", turn_on)
    hass.config_entries.async_update_entry(
        config_entry, options={OPT_CONTROL_ENTITIES: [one.id]}
    )

    body = await async_write(
        hass,
        config_entry,
        {"tool": "call_service", "entity_id": one.entity_id, "action": "light.turn_on"},
    )

    assert body["outcome"] == OUTCOME_DONE
    assert body["state"] == "on"
    assert body["action"] == "light.turn_on"


async def test_write_that_writes_nothing_is_no_change_not_a_failure(
    hass, config_entry, entity_registry
):
    """Turning off an already-off light succeeds and writes nothing."""
    one = entity_registry.async_get_or_create("light", "demo", "one")
    hass.states.async_set(one.entity_id, "off", {})

    async def turn_off(call) -> None:
        return None

    hass.services.async_register("light", "turn_off", turn_off)
    hass.config_entries.async_update_entry(
        config_entry, options={OPT_CONTROL_ENTITIES: [one.id]}
    )

    body = await async_write(
        hass,
        config_entry,
        {
            "tool": "call_service",
            "entity_id": one.entity_id,
            "action": "light.turn_off",
        },
    )

    assert body["outcome"] == OUTCOME_NO_CHANGE
    assert body["state"] == "off"
    assert "error" not in body


async def _write_a_late_device(
    hass, config_entry, entity_registry, domain: str, delay: float | None
) -> tuple[dict[str, object], float]:
    """
    Run open on a device that writes its state delay seconds after the call.

    A delay of None never writes. Return the body and the seconds it took.
    """
    device = entity_registry.async_get_or_create(domain, "demo", "late")
    # Feature 1 is OPEN for both covers and valves.
    attributes = {"supported_features": 1}
    hass.states.async_set(device.entity_id, "closed", attributes)

    async def open_device(call) -> None:
        if delay is not None:
            hass.loop.call_later(
                delay, hass.states.async_set, device.entity_id, "opening", attributes
            )

    service = f"open_{domain}"
    hass.services.async_register(domain, service, open_device)
    hass.config_entries.async_update_entry(
        config_entry, options={OPT_CONTROL_ENTITIES: [device.id]}
    )

    start = asyncio.get_running_loop().time()
    body = await async_write(
        hass,
        config_entry,
        {
            "tool": "call_service",
            "entity_id": device.entity_id,
            "action": f"{domain}.{service}",
        },
    )
    return body, asyncio.get_running_loop().time() - start


@pytest.mark.parametrize("domain", ["cover", "valve"])
async def test_a_device_that_moves_after_the_call_is_done(
    hass, config_entry, entity_registry, domain
):
    """A cover or valve writes its state when it starts to move, not when called."""
    body, _ = await _write_a_late_device(
        hass, config_entry, entity_registry, domain, 0.1
    )

    assert body["outcome"] == OUTCOME_DONE
    assert body["state"] == "opening"
    assert body["moving"] is True


async def test_a_cover_that_never_moves_is_no_change_after_the_wait(
    hass, config_entry, entity_registry
):
    """The wait ends at its limit, and the answer is no_change."""
    with patch(
        "custom_components.elevenagents.webhook.MOVING_DEVICE_WAIT_SECONDS", 0.2
    ):
        body, took = await _write_a_late_device(
            hass, config_entry, entity_registry, "cover", None
        )

    assert body["outcome"] == OUTCOME_NO_CHANGE
    assert body["state"] == "closed"
    assert "moving" not in body
    assert took >= 0.2


async def test_a_cover_that_writes_during_the_call_is_not_moving(
    hass, config_entry, entity_registry
):
    """A write before the call returns needs no wait and carries no moving."""
    cover = entity_registry.async_get_or_create("cover", "demo", "quick")
    hass.states.async_set(cover.entity_id, "closed", {"supported_features": 1})

    async def open_cover(call) -> None:
        hass.states.async_set(cover.entity_id, "open", {"supported_features": 1})

    hass.services.async_register("cover", "open_cover", open_cover)
    hass.config_entries.async_update_entry(
        config_entry, options={OPT_CONTROL_ENTITIES: [cover.id]}
    )

    body = await async_write(
        hass,
        config_entry,
        {
            "tool": "call_service",
            "entity_id": cover.entity_id,
            "action": "cover.open_cover",
        },
    )

    assert body["outcome"] == OUTCOME_DONE
    assert "moving" not in body


async def test_a_light_does_not_wait_for_a_late_write(
    hass, config_entry, entity_registry
):
    """Only covers and valves wait; a light answers at once."""
    one = entity_registry.async_get_or_create("light", "demo", "one")
    hass.states.async_set(one.entity_id, "off", {})

    async def turn_on(call) -> None:
        hass.loop.call_later(0.1, hass.states.async_set, one.entity_id, "on", {})

    hass.services.async_register("light", "turn_on", turn_on)
    hass.config_entries.async_update_entry(
        config_entry, options={OPT_CONTROL_ENTITIES: [one.id]}
    )

    body = await async_write(
        hass,
        config_entry,
        {"tool": "call_service", "entity_id": one.entity_id, "action": "light.turn_on"},
    )

    assert body["outcome"] == OUTCOME_NO_CHANGE
    await asyncio.sleep(0.15)


async def test_write_that_changes_only_an_attribute_is_still_done(
    hass, config_entry, entity_registry
):
    """last_updated advances even when the state string stays the same."""
    thermostat = entity_registry.async_get_or_create("climate", "demo", "salon")
    hass.states.async_set(
        thermostat.entity_id, "heat", {"supported_features": 1, "temperature": 19}
    )

    async def set_temperature(call) -> None:
        hass.states.async_set(
            thermostat.entity_id, "heat", {"supported_features": 1, "temperature": 21}
        )

    hass.services.async_register("climate", "set_temperature", set_temperature)
    async_set_service_schema(hass, "climate", "set_temperature", TEMPERATURE_SCHEMA)
    hass.config_entries.async_update_entry(
        config_entry, options={OPT_CONTROL_ENTITIES: [thermostat.id]}
    )

    body = await async_write(
        hass,
        config_entry,
        {
            "tool": "call_service",
            "entity_id": thermostat.entity_id,
            "action": "climate.set_temperature",
            "service_data": {"temperature": 21},
        },
    )

    assert body["outcome"] == OUTCOME_DONE
    assert body["temperature"] == 21


async def test_write_slower_than_the_timeout_is_unconfirmed_and_still_runs(
    hass, config_entry, entity_registry
):
    """The timeout stops the waiting, never the work."""
    one = entity_registry.async_get_or_create("light", "demo", "one")
    hass.states.async_set(one.entity_id, "off", {})
    finished = asyncio.Event()

    async def slow_turn_on(call) -> None:
        await asyncio.sleep(0.3)
        hass.states.async_set(one.entity_id, "on", {})
        finished.set()

    hass.services.async_register("light", "turn_on", slow_turn_on)
    hass.config_entries.async_update_entry(
        config_entry,
        options={OPT_CONTROL_ENTITIES: [one.id], OPT_CONFIRM_TIMEOUT: 0.05},
    )

    body = await async_write(
        hass,
        config_entry,
        {"tool": "call_service", "entity_id": one.entity_id, "action": "light.turn_on"},
    )

    assert body["outcome"] == OUTCOME_UNCONFIRMED
    assert body["check_state_next"] is True
    assert "state" not in body

    await asyncio.wait_for(finished.wait(), 2)
    assert hass.states.get(one.entity_id).state == "on"


async def test_write_validation_error_relays_the_translation_key(
    hass, config_entry, entity_registry
):
    """Home Assistant's own vocabulary reaches the agent unchanged."""
    thermostat = entity_registry.async_get_or_create("climate", "demo", "salon")
    # TARGET_TEMPERATURE. climate.set_temperature filters on it, and the
    # handler authorises against the real service descriptions.
    hass.states.async_set(thermostat.entity_id, "heat", {"supported_features": 1})

    async def set_temperature(call) -> Never:
        raise ServiceValidationError(
            translation_domain="climate",
            translation_key="temp_out_of_range",
            translation_placeholders={"check_temp": "45.0", "min_temp": "7.0"},
        )

    hass.services.async_register("climate", "set_temperature", set_temperature)
    async_set_service_schema(hass, "climate", "set_temperature", TEMPERATURE_SCHEMA)
    hass.config_entries.async_update_entry(
        config_entry, options={OPT_CONTROL_ENTITIES: [thermostat.id]}
    )

    body = await async_write(
        hass,
        config_entry,
        {
            "tool": "call_service",
            "entity_id": thermostat.entity_id,
            "action": "climate.set_temperature",
            "service_data": {"temperature": 45},
        },
    )

    assert body["outcome"] == OUTCOME_FAILED
    assert body["error"] == "temp_out_of_range"
    assert body["check_temp"] == "45.0"


async def test_write_to_an_unselected_entity_is_failed_not_found(
    hass, config_entry, entity_registry
):
    """The entity check runs first, so the action is never mentioned."""
    lock = entity_registry.async_get_or_create("lock", "demo", "front")
    hass.states.async_set(lock.entity_id, "locked", {})
    hass.config_entries.async_update_entry(config_entry, options={})

    body = await async_write(
        hass,
        config_entry,
        {"tool": "call_service", "entity_id": lock.entity_id, "action": "lock.unlock"},
    )

    assert body["outcome"] == OUTCOME_FAILED
    assert body["error"] == "not_found"


async def _sensitive_lock(
    hass, setup_entry, entity_registry, mark
) -> tuple[er.RegistryEntry, list[object]]:
    """Register a sensitive lock and return it with its recorded unlock calls."""
    lock = entity_registry.async_get_or_create("lock", "demo", "front")
    hass.states.async_set(lock.entity_id, "locked", {"friendly_name": "Puerta"})
    calls: list[object] = []

    async def unlock(call) -> None:
        calls.append(call)
        hass.states.async_set(lock.entity_id, "unlocked", {"friendly_name": "Puerta"})

    hass.services.async_register("lock", "unlock", unlock)
    hass.config_entries.async_update_entry(
        setup_entry,
        options={OPT_CONTROL_ENTITIES: [lock.id], OPT_SENSITIVE: {lock.id: mark}},
    )
    return lock, calls


def _payload(entity_id: str, **extra: object) -> dict[str, object]:
    return {
        "tool": "call_service",
        "entity_id": entity_id,
        "action": "lock.unlock",
        **extra,
    }


async def test_a_sensitive_action_asks_first_and_runs_after_the_user_confirms(
    hass, setup_entry, entity_registry
):
    """A confirmed action runs once the user's own turn carries the code back."""
    lock, calls = await _sensitive_lock(
        hass, setup_entry, entity_registry, {"confirm": True, "refuse_automatic": True}
    )

    first = await async_write(
        hass, setup_entry, _payload(lock.entity_id, conversation_id="c1")
    )
    assert first["outcome"] == OUTCOME_CONFIRMATION_REQUIRED
    assert calls == []

    code = first["confirmation_code"]
    history = json.dumps(
        {
            "entries": [
                {
                    "role": "agent",
                    "tool_results": [{"result_value": json.dumps(first)}],
                },
                {"role": "agent", "message": "¿Abro la puerta?"},
                {"role": "user", "message": "sí"},
            ]
        }
    )
    second = await async_write(
        hass,
        setup_entry,
        _payload(
            lock.entity_id,
            conversation_id="c1",
            conversation_history=history,
            confirmation_code=code,
        ),
    )
    assert second["outcome"] == OUTCOME_DONE
    assert len(calls) == 1


async def test_a_sensitive_action_without_a_conversation_id_is_refused(
    hass, setup_entry, entity_registry
):
    """Tools not synced yet carry no conversation_id, so the action is refused."""
    lock, calls = await _sensitive_lock(
        hass, setup_entry, entity_registry, {"confirm": True, "refuse_automatic": True}
    )

    body = await async_write(hass, setup_entry, _payload(lock.entity_id))

    assert body["error"] == "conversation_unknown"
    assert calls == []


async def test_an_ordinary_entity_needs_no_conversation_id(
    hass, setup_entry, entity_registry
):
    """A non-sensitive entity is untouched by the missing conversation_id."""
    lock, _calls = await _sensitive_lock(
        hass, setup_entry, entity_registry, {"confirm": True, "refuse_automatic": True}
    )
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_CONTROL_ENTITIES: [lock.id]}
    )

    body = await async_write(hass, setup_entry, _payload(lock.entity_id))

    assert body["outcome"] == OUTCOME_DONE


async def test_an_event_turn_is_refused_on_a_sensitive_entity(
    hass, setup_entry, entity_registry
):
    """An automatic turn may not act on a sensitive entity when refuse is on."""
    lock, calls = await _sensitive_lock(
        hass, setup_entry, entity_registry, {"confirm": True, "refuse_automatic": True}
    )
    setup_entry.runtime_data.gate.register_event_conversation("ev1")

    body = await async_write(
        hass, setup_entry, _payload(lock.entity_id, conversation_id="ev1")
    )

    assert body["error"] == "not_allowed_without_a_person"
    assert calls == []


async def test_an_unresolved_conversation_id_placeholder_is_unknown(
    hass, setup_entry, entity_registry
):
    """A template the platform failed to fill must not pass as a real conversation."""
    lock, calls = await _sensitive_lock(
        hass, setup_entry, entity_registry, {"confirm": True, "refuse_automatic": True}
    )

    body = await async_write(
        hass,
        setup_entry,
        _payload(lock.entity_id, conversation_id="{{system__conversation_id}}"),
    )

    assert body["error"] == "conversation_unknown"
    assert calls == []


async def test_a_tool_call_shortly_after_the_turn_is_still_an_event_turn(
    hass, setup_entry, entity_registry, freezer, no_network_sync
):
    """The grace window keeps a finished event turn's id recognised briefly."""
    lock, calls = await _sensitive_lock(
        hass, setup_entry, entity_registry, {"confirm": True, "refuse_automatic": True}
    )
    gate = setup_entry.runtime_data.gate
    gate.register_event_conversation("ev1")
    gate.end_event_conversation("ev1", dt_util.utcnow().timestamp())

    freezer.tick(timedelta(seconds=10))

    body = await async_write(
        hass, setup_entry, _payload(lock.entity_id, conversation_id="ev1")
    )

    assert body["error"] == "not_allowed_without_a_person"
    assert calls == []


async def test_a_tool_call_past_the_grace_window_is_no_longer_an_event_turn(
    hass, setup_entry, entity_registry, freezer, no_network_sync
):
    """Past the grace window the id is pruned and the usual rules apply."""
    lock, calls = await _sensitive_lock(
        hass, setup_entry, entity_registry, {"confirm": True, "refuse_automatic": True}
    )
    gate = setup_entry.runtime_data.gate
    gate.register_event_conversation("ev1")
    gate.end_event_conversation("ev1", dt_util.utcnow().timestamp())

    freezer.tick(timedelta(seconds=EVENT_CONVERSATION_GRACE_SECONDS + 1))

    body = await async_write(
        hass, setup_entry, _payload(lock.entity_id, conversation_id="ev1")
    )

    assert body["outcome"] == OUTCOME_CONFIRMATION_REQUIRED
    assert calls == []
    assert "ev1" not in gate.event_conversations


async def test_an_event_conversation_survives_an_entry_reload(
    hass, setup_entry, entity_registry
):
    """The gate lives outside RuntimeData, so a reload does not forget it."""
    lock, calls = await _sensitive_lock(
        hass, setup_entry, entity_registry, {"confirm": False, "refuse_automatic": True}
    )
    setup_entry.runtime_data.gate.register_event_conversation("ev1")

    with patch("custom_components.elevenagents.async_sync_tools", AsyncMock()):
        assert await hass.config_entries.async_reload(setup_entry.entry_id)
        await hass.async_block_till_done()

    body = await async_write(
        hass, setup_entry, _payload(lock.entity_id, conversation_id="ev1")
    )

    assert body["error"] == "not_allowed_without_a_person"
    assert calls == []


@pytest.mark.parametrize(
    "key", ["entity_id", "device_id", "area_id", "floor_id", "label_id"]
)
async def test_service_data_cannot_retarget_the_call(
    hass, config_entry, entity_registry, key
):
    """A target inside service_data would reach entities the agent may not control."""
    allowed = entity_registry.async_get_or_create("light", "demo", "allowed")
    other = entity_registry.async_get_or_create("light", "demo", "other")
    for entry in (allowed, other):
        hass.states.async_set(entry.entity_id, "off")
    calls: list[object] = []

    async def turn_on(call) -> None:
        calls.append(call)

    hass.services.async_register("light", "turn_on", turn_on)
    hass.config_entries.async_update_entry(
        config_entry, options={OPT_CONTROL_ENTITIES: [allowed.id]}
    )

    body = await async_write(
        hass,
        config_entry,
        {
            "tool": "call_service",
            "entity_id": allowed.entity_id,
            "action": "light.turn_on",
            "service_data": {key: other.entity_id},
        },
    )

    assert body["error"] == "malformed_request"
    assert calls == []


TEMPERATURE_SCHEMA = {
    "target": {"entity": [{"domain": ["climate"]}]},
    "fields": {"temperature": {"selector": {"number": {}}}},
}


async def _granted_light(hass, config_entry, entity_registry) -> tuple[str, list]:
    """Register one controlled light with a described turn_on and return its calls."""
    light = entity_registry.async_get_or_create("light", "demo", "granted")
    hass.states.async_set(light.entity_id, "off")
    calls: list[object] = []

    async def turn_on(call) -> None:
        calls.append(call)

    hass.services.async_register("light", "turn_on", turn_on)
    async_set_service_schema(
        hass,
        "light",
        "turn_on",
        {
            "target": {"entity": [{"domain": ["light"]}]},
            "fields": {
                "brightness": {"selector": {"number": {}}},
                "group_members": {"selector": {"entity": {}}},
            },
        },
    )
    hass.config_entries.async_update_entry(
        config_entry, options={OPT_CONTROL_ENTITIES: [light.id]}
    )
    return light.entity_id, calls


def _turn_on(entity_id: str, service_data: dict[str, object]) -> dict[str, object]:
    return {
        "tool": "call_service",
        "entity_id": entity_id,
        "action": "light.turn_on",
        "service_data": service_data,
    }


@pytest.mark.parametrize(
    "value",
    [
        "{{ states('lock.front') }}",
        "{% if true %}x{% endif %}",
        "{# note #}",
        ["{{ 1 }}"],
        {"nested": "{{ 1 }}"},
    ],
)
async def test_service_data_cannot_carry_a_template(
    hass, config_entry, entity_registry, value
):
    """Home Assistant would render a template with every state in reach."""
    entity_id, calls = await _granted_light(hass, config_entry, entity_registry)

    body = await async_write(
        hass, config_entry, _turn_on(entity_id, {"brightness": value})
    )

    assert body["error"] == "malformed_request"
    assert calls == []


async def test_service_data_keys_must_be_declared_scalars(
    hass, config_entry, entity_registry
):
    """A key the tool schema does not declare is refused before the call."""
    entity_id, calls = await _granted_light(hass, config_entry, entity_registry)

    body = await async_write(
        hass,
        config_entry,
        _turn_on(entity_id, {"group_members": ["light.other"]}),
    )
    assert body["error"] == "malformed_request"

    body = await async_write(hass, config_entry, _turn_on(entity_id, {"spare": 1}))
    assert body["error"] == "malformed_request"
    assert calls == []

    body = await async_write(
        hass, config_entry, _turn_on(entity_id, {"brightness": 120})
    )
    assert body["outcome"] != "failed"
    assert len(calls) == 1


ACTION_KEYS = {"entity_id", "action", "outcome", "error", "conversation_id"}


def _listen_for_actions(hass) -> list[dict[str, object]]:
    """Record the data of every elevenagents_action event."""
    heard: list[dict[str, object]] = []
    hass.bus.async_listen(EVENT_ACTION, lambda event: heard.append(dict(event.data)))
    return heard


async def _post(hass, client, entry, payload, supplied=None) -> None:
    """Post one payload to the webhook and let the event bus settle."""
    await client.post(
        f"/api/webhook/{entry.data[CONF_WEBHOOK_ID]}",
        headers={SECRET_HEADER: supplied or entry.data[CONF_WEBHOOK_SECRET]},
        json=payload,
    )
    await hass.async_block_till_done()


async def test_a_permitted_action_fires_one_action_event(
    hass, hass_client_no_auth, setup_entry, entity_registry
):
    """The event carries exactly five fields."""
    light = entity_registry.async_get_or_create("light", "demo", "one")
    hass.states.async_set(light.entity_id, "off", {"friendly_name": "One"})

    async def turn_on(call) -> None:
        hass.states.async_set(light.entity_id, "on", {"friendly_name": "One"})

    hass.services.async_register("light", "turn_on", turn_on)
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_CONTROL_ENTITIES: [light.id]}
    )
    heard = _listen_for_actions(hass)

    await _post(
        hass,
        await hass_client_no_auth(),
        setup_entry,
        {
            "tool": "call_service",
            "entity_id": light.entity_id,
            "action": "light.turn_on",
            "conversation_id": "c1",
        },
    )

    assert heard == [
        {
            "entity_id": light.entity_id,
            "action": "light.turn_on",
            "outcome": OUTCOME_DONE,
            "error": None,
            "conversation_id": "c1",
        }
    ]


async def test_a_refused_action_fires_an_action_event_with_the_error(
    hass, hass_client_no_auth, setup_entry, entity_registry
):
    """A refusal is recorded as failed, with its reason."""
    lock = entity_registry.async_get_or_create("lock", "demo", "front")
    hass.states.async_set(lock.entity_id, "locked", {"supported_features": 0})
    hass.config_entries.async_update_entry(
        setup_entry,
        options={
            OPT_CONTROL_ENTITIES: [lock.id],
            OPT_EXCEPTIONS: {lock.id: ["lock.unlock"]},
        },
    )
    heard = _listen_for_actions(hass)

    await _post(
        hass,
        await hass_client_no_auth(),
        setup_entry,
        {"tool": "call_service", "entity_id": lock.entity_id, "action": "lock.unlock"},
    )

    assert len(heard) == 1
    assert heard[0]["outcome"] == OUTCOME_FAILED
    assert heard[0]["error"] == "action_not_allowed"
    assert set(heard[0]) == ACTION_KEYS


async def test_the_action_event_never_carries_service_data(
    hass, hass_client_no_auth, setup_entry, entity_registry
):
    """No key and no value of service_data reaches the event."""
    light = entity_registry.async_get_or_create("light", "demo", "one")
    hass.states.async_set(light.entity_id, "off", {"friendly_name": "One"})
    hass.services.async_register("light", "turn_on", AsyncMock())
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_CONTROL_ENTITIES: [light.id]}
    )
    heard = _listen_for_actions(hass)

    await _post(
        hass,
        await hass_client_no_auth(),
        setup_entry,
        {
            "tool": "call_service",
            "entity_id": light.entity_id,
            "action": "light.turn_on",
            "service_data": {"brightness": 4242, "code": "s3cr3t-code"},
        },
    )

    assert len(heard) == 1
    assert set(heard[0]) == ACTION_KEYS
    assert "service_data" not in heard[0]
    text = json.dumps(heard[0])
    assert "4242" not in text
    assert "s3cr3t-code" not in text


async def test_the_action_event_never_carries_the_confirmation_code(
    hass, hass_client_no_auth, setup_entry, entity_registry
):
    """A confirmation_required answer carries a code. The event does not."""
    lock, _ = await _sensitive_lock(
        hass, setup_entry, entity_registry, {"confirm": True, "refuse_automatic": True}
    )
    heard = _listen_for_actions(hass)
    client = await hass_client_no_auth()
    response = await client.post(
        f"/api/webhook/{setup_entry.data[CONF_WEBHOOK_ID]}",
        headers={SECRET_HEADER: setup_entry.data[CONF_WEBHOOK_SECRET]},
        json=_payload(lock.entity_id, conversation_id="c1"),
    )
    code = (await response.json())["confirmation_code"]
    await hass.async_block_till_done()

    assert len(heard) == 1
    assert heard[0]["outcome"] == OUTCOME_CONFIRMATION_REQUIRED
    assert set(heard[0]) == ACTION_KEYS
    assert all(code not in str(value) for value in heard[0].values())


async def test_a_malformed_write_fires_an_action_event_without_outcome(
    hass, hass_client_no_auth, setup_entry
):
    """A malformed request has no outcome."""
    heard = _listen_for_actions(hass)

    await _post(
        hass,
        await hass_client_no_auth(),
        setup_entry,
        {"tool": "call_service", "entity_id": "light.one"},
    )

    assert heard == [
        {
            "entity_id": "light.one",
            "action": None,
            "outcome": None,
            "error": "malformed_request",
            "conversation_id": None,
        }
    ]


async def test_a_read_fires_no_action_event(hass, hass_client_no_auth, setup_entry):
    """Reads would flood the logbook."""
    heard = _listen_for_actions(hass)

    await _post(
        hass,
        await hass_client_no_auth(),
        setup_entry,
        {"tool": "get_state", "entity_id": ["light.one"]},
    )

    assert heard == []


async def test_a_wrong_secret_fires_no_action_event(
    hass, hass_client_no_auth, setup_entry
):
    """A 401 stays out of the logbook."""
    heard = _listen_for_actions(hass)

    await _post(
        hass,
        await hass_client_no_auth(),
        setup_entry,
        {"tool": "call_service", "entity_id": "light.one", "action": "light.turn_on"},
        supplied="wrong",
    )

    assert heard == []


async def test_an_exhausted_bucket_fires_no_action_event(
    hass, hass_client_no_auth, setup_entry
):
    """A 429 stays out of the logbook."""
    while setup_entry.runtime_data.bucket.take():
        pass
    heard = _listen_for_actions(hass)

    await _post(
        hass,
        await hass_client_no_auth(),
        setup_entry,
        {"tool": "call_service", "entity_id": "light.one", "action": "light.turn_on"},
    )

    assert heard == []


async def test_a_write_whose_state_holds_a_set_still_answers(
    hass, hass_client_no_auth, setup_entry, entity_registry
):
    """An attribute that is not plain JSON never empties the response."""
    one = entity_registry.async_get_or_create("light", "demo", "odd")
    hass.states.async_set(one.entity_id, "off", {})

    async def turn_on(call) -> None:
        hass.states.async_set(one.entity_id, "on", {"effect_list": {"rainbow"}})

    hass.services.async_register("light", "turn_on", turn_on)
    hass.config_entries.async_update_entry(
        setup_entry, options={OPT_CONTROL_ENTITIES: [one.id]}
    )

    client = await hass_client_no_auth()
    response = await client.post(
        f"/api/webhook/{setup_entry.data[CONF_WEBHOOK_ID]}",
        headers={SECRET_HEADER: setup_entry.data[CONF_WEBHOOK_SECRET]},
        json={
            "tool": "call_service",
            "entity_id": one.entity_id,
            "action": "light.turn_on",
        },
    )

    assert response.status == 200
    body = await response.json()
    assert body["outcome"] == OUTCOME_DONE
    assert body["effect_list"] == ["rainbow"]
