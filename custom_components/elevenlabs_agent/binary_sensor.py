"""The status sensors: one for the agent, one for each check."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .check import CHECKS, PROBLEM, SKIPPED
from .const import DOMAIN
from .status import check_signal

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import ElevenLabsAgentConfigEntry

OVERALL = "agent"
WEBHOOK = "webhook"


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - the platform signature
    entry: ElevenLabsAgentConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the agent sensor and one sensor for each check."""
    async_add_entities(
        [AgentSensor(entry), *(CheckSensor(entry, check) for check in CHECKS)]
    )


class StatusSensor(BinarySensorEntity):
    """A problem sensor that follows the last connection check."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, entry: ElevenLabsAgentConfigEntry, key: str) -> None:
        """Name the sensor by its key and attach it to the agent's device."""
        self._entry = entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="ElevenLabs",
            entry_type=DeviceEntryType.SERVICE,
        )

    def _result(self) -> dict[str, Any] | None:
        """Return the last check result, or None before the first check."""
        return self._entry.runtime_data.last_check

    async def async_added_to_hass(self) -> None:
        """Write the state again after each announced check."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                check_signal(self._entry.entry_id),
                self.async_write_ha_state,
            )
        )


class AgentSensor(StatusSensor):
    """The agent as a whole."""

    def __init__(self, entry: ElevenLabsAgentConfigEntry) -> None:
        """Create the agent sensor."""
        super().__init__(entry, OVERALL)

    @property
    def is_on(self) -> bool | None:
        """Return True when a check other than the webhook reports a problem."""
        result = self._result()
        if result is None:
            return None
        return any(
            item["status"] == PROBLEM and item["check"] != WEBHOOK
            for item in result["checks"]
        )


class CheckSensor(StatusSensor):
    """One check of the connection check."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # tools_current's entity lists can reach several kilobytes on a large
    # home, and they change on every check. Keep them out of the recorder.
    _unrecorded_attributes = frozenset(
        {"tools", "missing", "extra", "unreadable", "detail"}
    )

    def __init__(self, entry: ElevenLabsAgentConfigEntry, check: str) -> None:
        """Create the sensor of one check."""
        super().__init__(entry, check)
        self._check = check

    def _item(self) -> dict[str, Any] | None:
        """Return this check's line of the last result."""
        result = self._result()
        if result is None:
            return None
        for item in result["checks"]:
            if item["check"] == self._check:
                return dict(item)
        return None

    @property
    def is_on(self) -> bool | None:
        """Return True on a problem and None when the check did not run."""
        item = self._item()
        if item is None or item["status"] == SKIPPED:
            return None
        return bool(item["status"] == PROBLEM)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the check's detail. A dict detail gives one attribute per key."""
        item = self._item()
        if item is None or item["detail"] is None:
            return None
        if isinstance(item["detail"], dict):
            return dict(item["detail"])
        return {"detail": item["detail"]}
