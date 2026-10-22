"""
Config flow: credential validation, agent selection, entry creation and reauth.

The options flow that lets a user edit an existing entry lives here too:
setup and options share the same file by Home Assistant's own convention.
"""

from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import webhook
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
)
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
    OPT_PUBLIC_URL,
    PANEL_URL_PATH,
    TOOL_GET_STATE,
    TOOL_PERFORM_ACTION,
)
from .elevenlabs_client import (
    ElevenLabsAuthError,
    ElevenLabsError,
    ElevenLabsNotFoundError,
    ElevenLabsScopeError,
    async_get_agent,
    async_get_client,
    async_list_agents,
    async_list_tools,
    async_patch_agent,
)
from .public_url import PublicUrlError, ha_origin, public_url_option

if TYPE_CHECKING:
    from collections.abc import Mapping

    from elevenlabs.types import AgentSummaryResponseModel

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str})


def _user_schema(suggested: str | None) -> vol.Schema:
    """Return the first form, with the webhook URL field prefilled."""
    description = {"suggested_value": suggested} if suggested else None
    return USER_SCHEMA.extend(
        {vol.Optional(OPT_PUBLIC_URL, description=description): str}
    )


REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str})

ROTATE_SCHEMA = vol.Schema({vol.Required("confirm", default=True): bool})


class ElevenAgentsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the ElevenAgents integration."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialise the state carried between the key step and the agent step."""
        self._api_key: str = ""
        self._agents: list[AgentSummaryResponseModel] = []
        self._agent_id: str = ""
        self._colliding: list[str] = []
        self._options: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """
        Validate the API key by listing the workspace's agents.

        Ask for the webhook URL as well. The field starts as the Home
        Assistant URL, or empty without one.
        """
        errors: dict[str, str] = {}
        suggested = ha_origin(self.hass)

        if user_input is not None:
            suggested = user_input.get(OPT_PUBLIC_URL, "")
            try:
                public_url = public_url_option(self.hass, suggested)
            except PublicUrlError as err:
                errors[OPT_PUBLIC_URL] = err.code
            else:
                self._options = (
                    {} if public_url is None else {OPT_PUBLIC_URL: public_url}
                )

        if user_input is not None and not errors:
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
            step_id="user",
            data_schema=_user_schema(suggested),
            errors=errors,
        )

    async def async_step_agent(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which agent this entry controls, then create the entry."""
        if user_input is not None:
            agent_id = user_input[CONF_AGENT_ID]
            await self.async_set_unique_id(agent_id)
            self._abort_if_unique_id_configured()
            self._agent_id = agent_id

            self._colliding = await self._async_colliding_tools(agent_id)
            if self._colliding:
                return await self.async_step_duplicate()

            return self._create_entry()

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

    async def _async_colliding_tools(self, agent_id: str) -> list[str]:
        """
        Return the agent's attached tool ids whose name is one of ours.

        A duplicate name elsewhere in the workspace is harmless. The model
        only sees two of them when both are attached to this agent.
        """
        client = async_get_client(self.hass, self._api_key)
        agent = await async_get_agent(client, agent_id)
        agent_config = agent.conversation_config.agent
        prompt = None if agent_config is None else agent_config.prompt
        attached = set() if prompt is None else set(prompt.tool_ids or [])
        if not attached:
            return []

        ours = {TOOL_GET_STATE, TOOL_PERFORM_ACTION}
        page = await async_list_tools(client)
        return [
            tool.id
            for tool in page.tools
            if tool.id in attached and getattr(tool.tool_config, "name", None) in ours
        ]

    def _create_entry(self) -> ConfigFlowResult:
        """Create the entry for the chosen agent."""
        webhook_id = webhook.async_generate_id()
        webhook_path = webhook.async_generate_path(webhook_id)
        origin = self._options.get(OPT_PUBLIC_URL) or ha_origin(self.hass) or ""
        return self.async_create_entry(
            title=self._agent_id,
            data={
                CONF_API_KEY: self._api_key,
                CONF_AGENT_ID: self._agent_id,
                CONF_WEBHOOK_ID: webhook_id,
                CONF_WEBHOOK_SECRET: secrets.token_urlsafe(32),
                CONF_TOOL_IDS: {},
            },
            options=self._options,
            description_placeholders={
                "panel_url": f"/{PANEL_URL_PATH}",
                "webhook_url": f"{origin}{webhook_path}",
                "webhook_path": webhook_path,
            },
        )

    async def async_step_duplicate(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002 - a menu carries none
    ) -> ConfigFlowResult:
        """Report tools whose name collides, and offer two ways forward."""
        return self.async_show_menu(
            step_id="duplicate", menu_options=["detach", "keep"]
        )

    async def async_step_detach(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002 - a menu carries none
    ) -> ConfigFlowResult:
        """Remove the colliding tools from the agent, then create the entry."""
        client = async_get_client(self.hass, self._api_key)
        agent = await async_get_agent(client, self._agent_id)
        agent_config = agent.conversation_config.agent
        prompt = None if agent_config is None else agent_config.prompt
        attached = [] if prompt is None else list(prompt.tool_ids or [])
        remaining = [tool_id for tool_id in attached if tool_id not in self._colliding]

        await async_patch_agent(
            client,
            self._agent_id,
            conversation_config={"agent": {"prompt": {"tool_ids": remaining}}},
        )
        return self._create_entry()

    async def async_step_keep(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002 - a menu carries none
    ) -> ConfigFlowResult:
        """Leave the agent alone and create the entry."""
        return self._create_entry()

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],  # noqa: ARG002 - the entry already holds it
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
