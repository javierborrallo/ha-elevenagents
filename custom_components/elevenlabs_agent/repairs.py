"""Repair issues: auth, timeouts, rotation and missing entities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_AGENT_ID,
    CONF_API_KEY,
    DEFAULT_CONFIRM_TIMEOUT,
    DEFAULT_RESPONSE_TIMEOUT,
    DOMAIN,
    ISSUE_AGENT_UNPROTECTED,
    ISSUE_MISSING_ENTITIES,
    ISSUE_TIMEOUT_CROSSED,
    OPT_CONFIRM_TIMEOUT,
    PANEL_URL_PATH,
)
from .elevenlabs_client import async_get_agent, async_get_client, async_patch_agent
from .permissions import entity_id_for, missing_keys

if TYPE_CHECKING:
    from collections.abc import Mapping

    from elevenlabs import AsyncElevenLabs
    from elevenlabs.types import GetAgentResponseModel
    from homeassistant.components.repairs import RepairsFlowResult
    from homeassistant.core import HomeAssistant

    from . import ElevenLabsAgentConfigEntry


def is_protected(agent: GetAgentResponseModel) -> bool:
    """Return whether the agent requires auth or restricts callers by origin."""
    auth = agent.platform_settings.auth if agent.platform_settings else None
    if auth is None:
        return False
    return bool(auth.enable_auth) or bool(auth.allowlist)


@callback
def async_check_agent(
    hass: HomeAssistant,
    entry: ElevenLabsAgentConfigEntry,
    agent: GetAgentResponseModel,
) -> None:
    """Raise or clear the agent_unprotected issue for one entry."""
    if is_protected(agent):
        ir.async_delete_issue(hass, DOMAIN, ISSUE_AGENT_UNPROTECTED)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_AGENT_UNPROTECTED,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_AGENT_UNPROTECTED,
        data={"entry_id": entry.entry_id},
    )


@callback
def async_check_timeouts(
    hass: HomeAssistant,
    entry: ElevenLabsAgentConfigEntry,
    tool_config: Mapping[str, Any],
) -> None:
    """Raise or clear the timeout_crossed issue for one entry."""
    confirm_timeout = entry.options.get(OPT_CONFIRM_TIMEOUT, DEFAULT_CONFIRM_TIMEOUT)
    agent_timeout = tool_config.get("response_timeout_secs", DEFAULT_RESPONSE_TIMEOUT)

    if confirm_timeout < agent_timeout:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_TIMEOUT_CROSSED)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_TIMEOUT_CROSSED,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_TIMEOUT_CROSSED,
        translation_placeholders={
            "confirm_timeout": str(confirm_timeout),
            "agent_timeout": str(agent_timeout),
        },
    )


def _missing_names(hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry) -> str:
    """Return the missing keys as a comma-separated list, entity_ids where known."""
    names = (
        entity_id_for(hass, key) or key for key in missing_keys(hass, entry.options)
    )
    return ", ".join(sorted(names))


@callback
def async_check_missing(hass: HomeAssistant, entry: ElevenLabsAgentConfigEntry) -> None:
    """Raise or clear the missing_entities issue for one entry."""
    names = _missing_names(hass, entry)
    if not names:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_MISSING_ENTITIES)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_MISSING_ENTITIES,
        is_fixable=False,
        learn_more_url=f"homeassistant://{PANEL_URL_PATH}",
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_MISSING_ENTITIES,
        translation_placeholders={"entities": names},
    )


async def async_apply_auth_fix(
    hass: HomeAssistant,
    entry: ElevenLabsAgentConfigEntry,
    client: AsyncElevenLabs,
) -> None:
    """
    Turn on auth for the agent, keeping every other platform setting.

    The PATCH is read-modify-write. platform_settings also holds privacy
    settings, call limits and widget configuration, and sending a
    constructed object would reset whatever the user had.
    """
    agent_id = entry.data[CONF_AGENT_ID]
    agent = await async_get_agent(client, agent_id)

    settings: dict[str, Any] = (
        agent.platform_settings.model_dump(exclude_none=True)
        if agent.platform_settings is not None
        else {}
    )
    settings.setdefault("auth", {})["enable_auth"] = True

    await async_patch_agent(client, agent_id, platform_settings=settings)
    ir.async_delete_issue(hass, DOMAIN, ISSUE_AGENT_UNPROTECTED)


class ElevenLabsAgentRepairFlow(RepairsFlow):
    """Fix flow that turns on auth for an unprotected agent."""

    def __init__(self, entry: ElevenLabsAgentConfigEntry) -> None:
        """Store the entry the fix applies to."""
        super().__init__()
        self.entry = entry

    async def async_step_init(
        self, _user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Start the flow at its confirm step."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """
        Apply the fix on confirmation.

        Turning on auth breaks an agent reached from an embedded web
        widget until the widget presents a signed URL. The confirm step's
        own text carries that warning.
        """
        if user_input is not None:
            client = async_get_client(self.hass, self.entry.data[CONF_API_KEY])
            await async_apply_auth_fix(self.hass, self.entry, client)
            return self.async_create_entry(data={})

        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """
    Return the flow that fixes an issue.

    The platform loader raises HomeAssistantError when this function is
    missing, which takes the whole repairs platform down with it. Only
    agent_unprotected is fixable; every other issue never reaches here.
    """
    if issue_id != ISSUE_AGENT_UNPROTECTED:
        raise data_entry_flow.UnknownStep(issue_id)

    entry_id = cast("str", (data or {}).get("entry_id"))
    entry = hass.config_entries.async_get_known_entry(entry_id)
    return ElevenLabsAgentRepairFlow(entry)
