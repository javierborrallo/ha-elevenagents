"""Config flow: credential validation, agent selection, entry creation and reauth."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from .const import (
    CONF_AGENT_ID,
    CONF_API_KEY,
    CONF_TOOL_IDS,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DOMAIN,
)
from .elevenlabs_client import (
    ElevenLabsAuthError,
    ElevenLabsError,
    ElevenLabsNotFoundError,
    ElevenLabsScopeError,
    async_get_agent,
    async_get_client,
    async_list_agents,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from elevenlabs.types import AgentSummaryResponseModel

USER_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str})

REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str})


class ElevenLabsAgentConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the ElevenLabs Agent integration."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialise the state carried between the key step and the agent step."""
        self._api_key: str | None = None
        self._agents: list[AgentSummaryResponseModel] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate the API key by listing the workspace's agents."""
        try:
            get_url(self.hass, allow_internal=False)
        except NoURLAvailableError:
            return self.async_abort(reason="no_external_url")

        errors: dict[str, str] = {}

        if user_input is not None:
            client = async_get_client(self.hass, user_input[CONF_API_KEY])
            try:
                agents_page = await async_list_agents(client)
            except ElevenLabsAuthError:
                errors["base"] = "invalid_auth"
            except ElevenLabsScopeError:
                errors["base"] = "insufficient_scope"
            except ElevenLabsError:
                errors["base"] = "cannot_connect"
            else:
                if not agents_page.agents:
                    errors["base"] = "no_agents"
                else:
                    self._api_key = user_input[CONF_API_KEY]
                    self._agents = agents_page.agents
                    return await self.async_step_agent()

        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    async def async_step_agent(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which agent this entry controls, then create the entry."""
        if user_input is not None:
            agent_id = user_input[CONF_AGENT_ID]
            await self.async_set_unique_id(agent_id)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=agent_id,
                data={
                    CONF_API_KEY: self._api_key,
                    CONF_AGENT_ID: agent_id,
                    CONF_WEBHOOK_ID: webhook.async_generate_id(),
                    CONF_WEBHOOK_SECRET: secrets.token_urlsafe(32),
                    CONF_TOOL_IDS: {},
                },
                options={},
            )

        options = sorted(
            (
                SelectOptionDict(value=agent.agent_id, label=agent.name)
                for agent in self._agents
            ),
            key=lambda option: option["label"],
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_AGENT_ID): SelectSelector(
                    SelectSelectorConfig(options=options)
                )
            }
        )
        return self.async_show_form(step_id="agent", data_schema=schema)

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],  # noqa: ARG002 - required by the base signature
    ) -> ConfigFlowResult:
        """Start reauthentication at the confirm step."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-ask for the API key and update the entry once it validates."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            client = async_get_client(self.hass, user_input[CONF_API_KEY])
            try:
                await async_get_agent(client, reauth_entry.data[CONF_AGENT_ID])
            except ElevenLabsAuthError:
                errors["base"] = "invalid_auth"
            except ElevenLabsScopeError:
                errors["base"] = "insufficient_scope"
            except ElevenLabsNotFoundError:
                errors["base"] = "agent_not_found"
            except ElevenLabsError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry, data_updates=user_input
                )

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=REAUTH_SCHEMA, errors=errors
        )
