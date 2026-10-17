"""Tests for the check that follows each sync, and the stored result."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.elevenagents.const import CHECK_DEBOUNCE, DOMAIN
from custom_components.elevenagents.elevenlabs_client import ElevenLabsError
from custom_components.elevenagents.status import (
    async_record_check,
    check_signal,
)
from custom_components.elevenagents.tool_sync import async_sync_tools

if TYPE_CHECKING:
    from pytest_homeassistant_custom_component.typing import WebSocketGenerator

# homeassistant 2026.9.1's http server sets self.app["hass"] as a plain key.
# aiohttp 3.14.3 raises NotAppKeyWarning for that pattern. pyproject.toml
# turns every warning into an error, so this module ignores this one category.
pytestmark = pytest.mark.filterwarnings(
    "ignore::aiohttp.web_exceptions.NotAppKeyWarning"
)


@pytest.fixture(autouse=True)
def mock_sync_tools():
    """Keep the registry listener's debounced sync off the network."""
    with patch(
        "custom_components.elevenagents.expose.async_sync_tools", AsyncMock()
    ) as mock:
        yield mock


SYNC = "custom_components.elevenagents.tool_sync._sync"

GOOD: dict[str, Any] = {
    "ok": True,
    "checks": [{"check": "key", "status": "ok", "detail": None}],
}
BAD: dict[str, Any] = {
    "ok": False,
    "checks": [{"check": "key", "status": "problem", "detail": "refused"}],
}


@pytest.fixture
def run_check():
    """Replace the connection check with a mock that answers GOOD."""
    with patch(
        "custom_components.elevenagents.status.async_run_check",
        AsyncMock(return_value=GOOD),
    ) as mock:
        yield mock


async def _elapse(hass, freezer, seconds: float = CHECK_DEBOUNCE) -> None:
    """Move the clock, fire the due timers and let the check finish."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_a_sync_that_succeeds_is_followed_by_a_check(
    hass, setup_entry, freezer, run_check
):
    """The sensors learn the outcome of every sync."""
    with patch(SYNC, AsyncMock()):
        await async_sync_tools(hass, setup_entry, setup_entry.runtime_data.client)
    await _elapse(hass, freezer)

    run_check.assert_awaited_once()
    assert setup_entry.runtime_data.last_check == GOOD


async def test_a_sync_that_fails_is_followed_by_a_check(
    hass, setup_entry, freezer, run_check
):
    """A failed sync is when the sensors matter most."""
    with (
        patch(SYNC, AsyncMock(side_effect=ElevenLabsError("boom"))),
        pytest.raises(ElevenLabsError),
    ):
        await async_sync_tools(hass, setup_entry, setup_entry.runtime_data.client)
    await _elapse(hass, freezer)

    run_check.assert_awaited_once()


async def test_a_sync_that_loses_its_runtime_data_still_raises(hass, setup_entry):
    """An unload mid-sync does not replace the sync's own exception."""

    async def _lose_runtime_data(*_args: object, **_kwargs: object) -> None:
        del setup_entry.runtime_data
        message = "boom"
        raise ElevenLabsError(message)

    with (
        patch(SYNC, AsyncMock(side_effect=_lose_runtime_data)),
        pytest.raises(ElevenLabsError, match="boom"),
    ):
        await async_sync_tools(hass, setup_entry, setup_entry.runtime_data.client)


async def test_a_burst_of_syncs_gives_one_check(hass, setup_entry, freezer, run_check):
    """Three syncs in a row cost one check."""
    with patch(SYNC, AsyncMock()):
        for _ in range(3):
            await async_sync_tools(hass, setup_entry, setup_entry.runtime_data.client)
    await _elapse(hass, freezer)

    run_check.assert_awaited_once()


async def test_no_check_runs_before_the_cooldown(hass, setup_entry, freezer, run_check):
    """The check waits for the burst to end."""
    with patch(SYNC, AsyncMock()):
        await async_sync_tools(hass, setup_entry, setup_entry.runtime_data.client)
    await _elapse(hass, freezer, CHECK_DEBOUNCE - 1)

    run_check.assert_not_awaited()


async def test_a_problem_after_a_sync_is_a_warning(
    hass, setup_entry, freezer, run_check, caplog
):
    """The administrator reads the same detail in the log."""
    run_check.return_value = BAD
    with patch(SYNC, AsyncMock()):
        await async_sync_tools(hass, setup_entry, setup_entry.runtime_data.client)
    await _elapse(hass, freezer)

    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name.startswith("custom_components")
    ]
    assert any("refused" in r.getMessage() for r in warnings)


async def test_a_clean_check_after_a_sync_logs_no_warning(
    hass, setup_entry, freezer, run_check, caplog
):
    """A working agent does not fill the log."""
    with patch(SYNC, AsyncMock()):
        await async_sync_tools(hass, setup_entry, setup_entry.runtime_data.client)
    await _elapse(hass, freezer)

    assert not [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and r.name.startswith("custom_components")
    ]


async def test_the_check_service_stores_and_announces(hass, setup_entry, run_check):
    """Running the check by hand refreshes the sensors."""
    heard: list[int] = []

    @callback
    def _heard() -> None:
        heard.append(1)

    async_dispatcher_connect(hass, check_signal(setup_entry.entry_id), _heard)

    result = await hass.services.async_call(
        DOMAIN,
        "check",
        {"config_entry_id": setup_entry.entry_id},
        blocking=True,
        return_response=True,
    )
    await hass.async_block_till_done()

    assert result == GOOD
    assert setup_entry.runtime_data.last_check == GOOD
    assert heard == [1]


async def test_the_websocket_check_stores_its_result(
    hass, setup_entry, run_check, hass_ws_client: WebSocketGenerator
):
    """The panel's check refreshes the sensors too."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "elevenagents/check", "entry_id": setup_entry.entry_id}
    )
    await client.receive_json()

    assert setup_entry.runtime_data.last_check == GOOD


async def test_a_check_on_an_unloaded_entry_stores_nothing(
    hass, setup_entry, run_check
):
    """runtime_data does not exist once the entry is unloaded."""
    await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()

    assert await async_record_check(hass, setup_entry) == GOOD


# --- M1: a check that outlives a reload must not write into the new runtime -


async def test_a_check_that_outlives_a_reload_does_not_store_into_the_new_runtime(
    hass, setup_entry
):
    """A stale probe answering after a reload must not flash the new sensors."""
    old_runtime = setup_entry.runtime_data
    heard: list[int] = []

    @callback
    def _heard() -> None:
        heard.append(1)

    async_dispatcher_connect(hass, check_signal(setup_entry.entry_id), _heard)

    async def _reload_mid_check(*_args: object, **_kwargs: object) -> dict[str, Any]:
        """Simulate a reload finishing while this check is still running."""
        setup_entry.runtime_data = replace(old_runtime, last_check=None)
        return BAD

    with patch(
        "custom_components.elevenagents.status.async_run_check",
        AsyncMock(side_effect=_reload_mid_check),
    ):
        result = await async_record_check(hass, setup_entry)

    assert result == BAD
    assert setup_entry.runtime_data.last_check is None
    assert heard == []
