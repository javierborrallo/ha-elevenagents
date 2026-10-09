"""Webhook transport: authentication, metering, dispatch, and the two tool paths."""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from typing import TYPE_CHECKING, Any

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.helpers.service import async_get_all_descriptions
from homeassistant.util import dt as dt_util

from .const import (
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DEFAULT_CONFIRM_TIMEOUT,
    DISCRIMINATOR_CALL_SERVICE,
    DISCRIMINATOR_GET_STATE,
    DOMAIN,
    ERROR_NOT_FOUND,
    MAX_BODY_BYTES,
    OPT_CONFIRM_TIMEOUT,
    OUTCOME_DONE,
    OUTCOME_FAILED,
    OUTCOME_NO_CHANGE,
    OUTCOME_UNCONFIRMED,
    RATE_LIMIT_BURST,
    RATE_LIMIT_PER_SECOND,
    REASON_CONVERSATION_UNKNOWN,
    SECRET_HEADER,
)
from .permissions import authorise_action, authorise_read, from_options, permission_key
from .response import error_fields, malformed, project, read_results, write_result
from .sensitive import sensitive_mark
from .tool_sync import service_data_keys

if TYPE_CHECKING:
    from collections.abc import Callable

    from aiohttp.web import Request
    from homeassistant.core import HomeAssistant

    from . import ElevenLabsAgentConfigEntry, RuntimeData
    from .permissions import Permissions

WEBHOOK_NAME = "ElevenAgents"


class TokenBucket:
    """A token bucket rate limiter with an injectable clock."""

    def __init__(
        self,
        burst: int = RATE_LIMIT_BURST,
        per_second: float = RATE_LIMIT_PER_SECOND,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        """Start the bucket full, refilling at per_second tokens per second."""
        self._burst = burst
        self._per_second = per_second
        self._now = now
        self._tokens = float(burst)
        self._updated = now()

    def take(self) -> bool:
        """Take one token. Return False when the bucket is empty."""
        elapsed = self._now() - self._updated
        self._updated = self._now()
        self._tokens = min(self._burst, self._tokens + elapsed * self._per_second)
        if self._tokens < 1:
            return False
        self._tokens -= 1
        return True


def async_register(hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry) -> None:
    """Register the webhook for one config entry."""
    webhook.async_register(
        hass,
        DOMAIN,
        WEBHOOK_NAME,
        entry.data[CONF_WEBHOOK_ID],
        async_handle_webhook,
        local_only=False,
        allowed_methods=["POST"],
    )


def async_unregister(hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry) -> None:
    """Remove the webhook for one config entry."""
    webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])


def _entry_for_webhook(
    hass: HomeAssistant, webhook_id: str
) -> ElevenLabsAgentConfigEntry | None:
    """Return the config entry that owns a webhook id."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(CONF_WEBHOOK_ID) == webhook_id:
            return entry
    return None


def _authenticated_entry(
    hass: HomeAssistant, webhook_id: str, request: Request
) -> ElevenLabsAgentConfigEntry | None:
    """Return the entry that owns this webhook once the secret matches."""
    entry = _entry_for_webhook(hass, webhook_id)
    if entry is None:
        return None

    supplied = request.headers.get(SECRET_HEADER, "")
    secret = entry.data.get(CONF_WEBHOOK_SECRET, "")
    if not hmac.compare_digest(supplied.encode(), secret.encode()):
        return None
    return entry


async def _parsed_json_body(request: Request) -> dict[str, Any] | web.Response:
    """
    Validate the content type and size, then parse the JSON body.

    Returns the parsed payload, or a ready response on a transport
    failure.
    """
    content_type = request.headers.get("Content-Type", "")
    if content_type.partition(";")[0].strip().lower() != "application/json":
        return web.Response(status=415)

    content_length = request.headers.get("Content-Length")
    if content_length is not None and int(content_length) > MAX_BODY_BYTES:
        return web.Response(status=413)

    body = await request.content.read(MAX_BODY_BYTES + 1)
    if len(body) > MAX_BODY_BYTES:
        return web.Response(status=413)

    try:
        payload = json.loads(body)
    except ValueError:
        return web.json_response(malformed("body is not valid JSON"))

    if not isinstance(payload, dict):
        return web.json_response(malformed("body must be a JSON object"))
    return payload


async def async_handle_webhook(
    hass: HomeAssistant, webhook_id: str, request: Request
) -> web.Response:
    """
    Authenticate, meter, validate and dispatch one webhook call.

    Each step returns immediately and can only produce the failure its
    own shape can express. The secret is checked before a token is
    taken, so an unauthenticated caller cannot drain the bucket.
    """
    entry = _authenticated_entry(hass, webhook_id, request)
    if entry is None:
        return web.Response(status=401)

    if not entry.runtime_data.bucket.take():
        return web.Response(status=429)

    result = await _parsed_json_body(request)
    if isinstance(result, web.Response):
        return result
    payload = result

    tool = payload.get("tool")
    if tool == DISCRIMINATOR_GET_STATE:
        return web.json_response(await async_read(hass, entry, payload))
    if tool == DISCRIMINATOR_CALL_SERVICE:
        return web.json_response(await async_write(hass, entry, payload))
    return web.json_response(malformed(f"unknown tool: {tool!r}"))


async def async_read(
    hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry, payload: dict[str, Any]
) -> dict[str, Any]:
    """Read the requested entities. One failure does not fail the batch."""
    entity_ids = payload.get("entity_id")
    if not isinstance(entity_ids, list):
        return malformed("entity_id must be an array")

    perms = from_options(entry.options)
    results: list[dict[str, Any]] = []

    for entity_id in entity_ids:
        if not isinstance(entity_id, str):
            results.append({"entity_id": str(entity_id), "error": ERROR_NOT_FOUND})
            continue
        if authorise_read(hass, perms, entity_id) is not None:
            results.append({"entity_id": entity_id, "error": ERROR_NOT_FOUND})
            continue
        projected = project(hass, entity_id)
        results.append(projected or {"entity_id": entity_id, "error": ERROR_NOT_FOUND})

    return read_results(results)


# Service call keys that choose the target. Only entity_id at the top level may do that.
TARGET_KEYS = frozenset({"entity_id", "device_id", "area_id", "floor_id", "label_id"})

# Markers that open a Home Assistant template.
TEMPLATE_MARKERS = ("{{", "{%", "{#")


def _has_template(value: Any) -> bool:
    """Return True when a value, or any value nested in it, holds a template marker."""
    if isinstance(value, str):
        return any(marker in value for marker in TEMPLATE_MARKERS)
    if isinstance(value, dict):
        return any(_has_template(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_template(item) for item in value)
    return False


def _parsed_write_payload(
    payload: dict[str, Any],
) -> tuple[str, str, dict[str, Any]] | dict[str, Any]:
    """
    Validate and unpack a call_service payload.

    Returns the entity_id, action and service_data, or a malformed
    response body when the shape is wrong.
    """
    entity_id = payload.get("entity_id")
    action = payload.get("action")
    if not isinstance(entity_id, str) or not isinstance(action, str):
        return malformed("entity_id and action are required strings")
    if action.count(".") != 1:
        return malformed("action must be domain.service")

    service_data = payload.get("service_data") or {}
    if not isinstance(service_data, dict):
        return malformed("service_data must be an object")
    targets = TARGET_KEYS.intersection(service_data)
    if targets:
        return malformed(f"service_data cannot carry {', '.join(sorted(targets))}")
    if _has_template(service_data):
        return malformed("service_data cannot carry a template")
    return entity_id, action, service_data


def _resolved_conversation_id(raw: Any) -> str | None:
    """Return the conversation id, or None when missing or an unfilled template."""
    if not isinstance(raw, str) or not raw or "{{" in raw:
        return None
    return raw


def _sensitive_answer(
    hass: HomeAssistant,
    entry: ElevenLabsAgentConfigEntry,
    payload: dict[str, Any],
    target: tuple[str, str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the gate's answer for a sensitive entity, or None when it may run."""
    entity_id, action, service_data = target
    mark = sensitive_mark(entry.options, permission_key(hass, entity_id))
    if mark is None:
        return None
    runtime: RuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is None:
        return write_result(
            OUTCOME_FAILED,
            entity_id,
            action,
            extra={"error": REASON_CONVERSATION_UNKNOWN},
        )
    state = hass.states.get(entity_id)
    raw_name = (
        entity_id if state is None else state.attributes.get("friendly_name", entity_id)
    )
    code = payload.get("confirmation_code")
    return runtime.gate.check(
        mark,
        conversation_id=_resolved_conversation_id(payload.get("conversation_id")),
        history=payload.get("conversation_history"),
        code=str(code) if code else None,
        entity_id=entity_id,
        name=str(raw_name),
        action=action,
        service_data=service_data,
        now=dt_util.utcnow().timestamp(),
    )


def _refusal(
    hass: HomeAssistant,
    perms: Permissions,
    descriptions: dict[str, dict[str, Any]],
    call: tuple[str, str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the answer that refuses the call, or None when it may go on."""
    entity_id, action, service_data = call
    error = authorise_action(hass, perms, entity_id, action, descriptions)
    if error is not None:
        return write_result(OUTCOME_FAILED, entity_id, action, extra={"error": error})
    undeclared = set(service_data) - service_data_keys(descriptions, action)
    if undeclared:
        return malformed(f"service_data cannot carry {', '.join(sorted(undeclared))}")
    return None


async def async_write(
    hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry, payload: dict[str, Any]
) -> dict[str, Any]:
    """
    Perform one action and report what was observed.

    The service call runs as a background task owned by the entry. The
    wait is shielded, so a timeout stops the waiting without cancelling
    the call.
    """
    parsed = _parsed_write_payload(payload)
    if isinstance(parsed, dict):
        return parsed
    entity_id, action, service_data = parsed

    perms = from_options(entry.options)
    descriptions = await async_get_all_descriptions(hass)

    refused = _refusal(hass, perms, descriptions, (entity_id, action, service_data))
    if refused is not None:
        return refused

    gated = _sensitive_answer(hass, entry, payload, (entity_id, action, service_data))
    if gated is not None:
        return gated

    before = hass.states.get(entity_id)
    before_updated = None if before is None else before.last_updated

    domain, service = action.split(".", 1)
    task = entry.async_create_background_task(
        hass,
        hass.services.async_call(
            domain, service, {**service_data, "entity_id": entity_id}, blocking=True
        ),
        f"{DOMAIN} {action} {entity_id}",
    )

    timeout = float(entry.options.get(OPT_CONFIRM_TIMEOUT, DEFAULT_CONFIRM_TIMEOUT))
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout)
    except TimeoutError:
        return write_result(OUTCOME_UNCONFIRMED, entity_id, action)
    except Exception as err:  # noqa: BLE001 - every failure is reported, not raised
        code, extra = error_fields(err)
        return write_result(
            OUTCOME_FAILED, entity_id, action, extra={"error": code, **extra}
        )

    after = hass.states.get(entity_id)
    after_updated = None if after is None else after.last_updated
    outcome = OUTCOME_DONE if after_updated != before_updated else OUTCOME_NO_CHANGE

    return write_result(outcome, entity_id, action, project(hass, entity_id))
