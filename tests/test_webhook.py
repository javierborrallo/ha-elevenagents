"""Tests for webhook transport, dispatch, the read path and the write path."""

from __future__ import annotations

import asyncio
import hmac
import json
from typing import Never

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util.aiohttp import MockRequest

from custom_components.elevenlabs_agent.const import (
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    MAX_BODY_BYTES,
    OPT_CONFIRM_TIMEOUT,
    OPT_CONTROL_ENTITIES,
    OPT_READ_ENTITIES,
    OUTCOME_DONE,
    OUTCOME_FAILED,
    OUTCOME_NO_CHANGE,
    OUTCOME_UNCONFIRMED,
    RATE_LIMIT_BURST,
    SECRET_HEADER,
)
from custom_components.elevenlabs_agent.webhook import (
    TokenBucket,
    async_handle_webhook,
    async_read,
    async_write,
)

# homeassistant 2026.9.1's http server sets self.app["hass"] as a plain key.
# aiohttp 3.14.3 raises NotAppKeyWarning for that pattern. pyproject.toml
# turns every warning into an error, so this module ignores this one category.
pytestmark = pytest.mark.filterwarnings(
    "ignore::aiohttp.web_exceptions.NotAppKeyWarning"
)


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
        "custom_components.elevenlabs_agent.webhook.hmac.compare_digest", spy
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
