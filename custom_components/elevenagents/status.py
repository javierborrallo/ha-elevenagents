"""Run the connection check after a sync, store the result and announce it."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .check import PROBLEM, async_run_check
from .const import CHECK_DEBOUNCE, SIGNAL_CHECK

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from homeassistant.core import HomeAssistant

    from . import ElevenAgentsConfigEntry, RuntimeData

_LOGGER = logging.getLogger(__name__)


def check_signal(entry_id: str) -> str:
    """Return the dispatcher signal of one config entry."""
    return SIGNAL_CHECK.format(entry_id)


async def async_record_check(
    hass: HomeAssistant, entry: ElevenAgentsConfigEntry
) -> dict[str, Any]:
    """
    Run the connection check and return its result.

    Store the result and announce it only if the entry's runtime data is
    still the one this check started with. A reload that finishes while
    the check is in flight replaces runtime_data; without this guard, the
    stale result would land in the recreated entry's sensors.
    """
    runtime: RuntimeData | None = getattr(entry, "runtime_data", None)
    result = await async_run_check(hass, entry)
    current: RuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is not None and current is runtime:
        runtime.last_check = result
        async_dispatcher_send(hass, check_signal(entry.entry_id))
    return result


def async_create_check_debouncer(
    hass: HomeAssistant, entry: ElevenAgentsConfigEntry
) -> Debouncer[Coroutine[Any, Any, None]]:
    """Return the debouncer that runs the connection check after a sync."""

    async def _after_sync() -> None:
        result = await async_record_check(hass, entry)
        problems = [item for item in result["checks"] if item["status"] == PROBLEM]
        if problems:
            _LOGGER.warning("The check after a sync found problems: %s", problems)
        else:
            _LOGGER.debug("The check after a sync found no problem")

    return Debouncer(
        hass,
        _LOGGER,
        cooldown=CHECK_DEBOUNCE,
        immediate=False,
        function=_after_sync,
    )
