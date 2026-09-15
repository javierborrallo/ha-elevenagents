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
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TargetSelector,
)
from homeassistant.helpers.service import async_get_all_descriptions

from .const import (
    CONF_AGENT_ID,
    CONF_API_KEY,
    CONF_RESPONSE_TIMEOUT,
    CONF_TOOL_IDS,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DEFAULT_CONFIRM_TIMEOUT,
    DEFAULT_RESPONSE_TIMEOUT,
    DOMAIN,
    ISSUE_ROTATION_UNSYNCED,
    OPT_AUTO_EXPOSE,
    OPT_CONFIRM_TIMEOUT,
    OPT_CONTROL_ENTITIES,
    OPT_CONTROL_TARGET,
    OPT_EXCEPTIONS,
    OPT_READ_ENTITIES,
    OPT_READ_TARGET,
    TOOL_CALL_SERVICE,
    TOOL_GET_STATE,
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
from .permissions import (
    Permissions,
    entity_id_for,
    from_options,
    granted_services,
    resolve_target,
)
from .tool_sync import async_sync_tools
from .webhook import WEBHOOK_NAME, async_handle_webhook

if TYPE_CHECKING:
    from collections.abc import Mapping

    from elevenlabs.types import AgentSummaryResponseModel

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str})

REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str})

ROTATE_SCHEMA = vol.Schema({vol.Required("confirm", default=True): bool})


class ElevenLabsAgentConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the ElevenLabs Agent integration."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialise the state carried between the key step and the agent step."""
        self._api_key: str = ""
        self._agents: list[AgentSummaryResponseModel] = []
        self._agent_id: str = ""
        self._colliding: list[str] = []

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

        ours = {TOOL_GET_STATE, TOOL_CALL_SERVICE}
        page = await async_list_tools(client)
        return [
            tool.id
            for tool in page.tools
            if tool.id in attached and getattr(tool.tool_config, "name", None) in ours
        ]

    def _create_entry(self) -> ConfigFlowResult:
        """Create the entry for the chosen agent."""
        return self.async_create_entry(
            title=self._agent_id,
            data={
                CONF_API_KEY: self._api_key,
                CONF_AGENT_ID: self._agent_id,
                CONF_WEBHOOK_ID: webhook.async_generate_id(),
                CONF_WEBHOOK_SECRET: secrets.token_urlsafe(32),
                CONF_TOOL_IDS: {},
            },
            options={},
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

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,  # noqa: ARG004 - the flow reads it through self.config_entry
    ) -> ElevenLabsAgentOptionsFlow:
        """Return the options flow for this entry."""
        return ElevenLabsAgentOptionsFlow()


class ElevenLabsAgentOptionsFlow(OptionsFlowWithReload):
    """
    Options flow: a menu of seven unrelated destinations.

    OptionsFlowWithReload reloads the entry when the flow ends with changed
    options. The integration registers no update listener of its own, and
    combining the two raises ValueError.
    """

    def __init__(self) -> None:
        """Initialise the state carried from the control step to the exceptions."""
        self._pending: dict[str, Any] = {}
        self._added: set[str] = set()
        self._pending_carried: dict[str, Any] = {}

    def _merge(self, **keys: Any) -> ConfigFlowResult:
        """Write the given keys into a full copy of the stored options."""
        return self.async_create_entry(data={**self.config_entry.options, **keys})

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002 - a menu carries none
    ) -> ConfigFlowResult:
        """Show the menu of independent destinations."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "read",
                "control",
                "exceptions",
                "timeout",
                "auto_expose",
                "rotate_secret",
                "rotate_webhook",
            ],
        )

    async def async_step_read(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the entities and areas the agent may read."""
        if user_input is not None:
            target = user_input[OPT_READ_TARGET]
            resolved = resolve_target(self.hass, target)
            return self._merge(
                **{
                    OPT_READ_TARGET: target,
                    OPT_READ_ENTITIES: sorted(resolved),
                }
            )

        return self.async_show_form(
            step_id="read",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(OPT_READ_TARGET): TargetSelector()}),
                {OPT_READ_TARGET: self.config_entry.options.get(OPT_READ_TARGET)},
            ),
        )

    async def async_step_control(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the entities and areas the agent may control."""
        if user_input is not None:
            target = user_input[OPT_CONTROL_TARGET]
            resolved = resolve_target(self.hass, target)
            current = set(self.config_entry.options.get(OPT_CONTROL_ENTITIES, ()))
            self._added = resolved - current
            self._pending = {
                OPT_CONTROL_TARGET: target,
                OPT_CONTROL_ENTITIES: sorted(resolved),
            }
            return await self.async_step_granted()

        return self.async_show_form(
            step_id="control",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(OPT_CONTROL_TARGET): TargetSelector()}),
                {OPT_CONTROL_TARGET: self.config_entry.options.get(OPT_CONTROL_TARGET)},
            ),
        )

    async def async_step_granted(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show what this submission granted, then save it or open the exceptions."""
        if user_input is not None:
            if user_input.get("open_exceptions"):
                self._pending_carried = dict(self._pending)
                return await self.async_step_exceptions()
            return self._merge(**self._pending)

        descriptions = await async_get_all_descriptions(self.hass)
        perms = from_options({**self.config_entry.options, **self._pending})

        lines: list[str] = []
        for key in sorted(self._added):
            entity_id = entity_id_for(self.hass, key)
            if entity_id is None:
                continue
            services = granted_services(self.hass, perms, key, descriptions)
            lines.append(f"{entity_id}: {', '.join(sorted(services))}")

        return self.async_show_form(
            step_id="granted",
            data_schema=vol.Schema(
                {vol.Optional("open_exceptions", default=False): bool}
            ),
            description_placeholders={"granted": "\n".join(lines)},
        )

    async def async_step_exceptions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """List every controlled entity's services, ticked; store the unticked ones."""
        merged = {**self.config_entry.options, **self._pending_carried}
        control_ids = set(merged.get(OPT_CONTROL_ENTITIES, ()))
        current_exceptions = merged.get(OPT_EXCEPTIONS, {})

        descriptions = await async_get_all_descriptions(self.hass)

        catalogue: dict[str, tuple[str, set[str]]] = {}
        for key in sorted(control_ids):
            entity_id = entity_id_for(self.hass, key)
            if entity_id is None:
                _LOGGER.warning(
                    "Entity %s no longer exists; dropping it from the exception screen",
                    key,
                )
                continue
            full = granted_services(
                self.hass,
                Permissions(read=frozenset(), control=frozenset({key}), exceptions={}),
                key,
                descriptions,
            )
            catalogue[entity_id] = (key, full)

        if user_input is not None:
            exceptions: dict[str, list[str]] = {}
            for entity_id, (key, full) in catalogue.items():
                kept = set(user_input.get(entity_id, []))
                removed = full - kept
                if removed:
                    exceptions[key] = sorted(removed)

            pending = {**self._pending_carried, OPT_EXCEPTIONS: exceptions}
            self._pending_carried = {}
            return self._merge(**pending)

        schema: dict[Any, Any] = {}
        for entity_id, (key, full) in catalogue.items():
            removed_now = set(current_exceptions.get(key, ()))
            kept_now = sorted(full - removed_now)
            selector = SelectSelector(
                SelectSelectorConfig(options=sorted(full), multiple=True)
            )
            schema[vol.Optional(entity_id, default=kept_now)] = selector

        return self.async_show_form(
            step_id="exceptions", data_schema=vol.Schema(schema)
        )

    async def async_step_timeout(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the confirmation timeout, refusing one that crosses the agent's."""
        agent_timeout = self.config_entry.data.get(
            CONF_RESPONSE_TIMEOUT, DEFAULT_RESPONSE_TIMEOUT
        )
        errors: dict[str, str] = {}

        if user_input is not None:
            value = int(user_input[OPT_CONFIRM_TIMEOUT])
            if value >= agent_timeout:
                errors[OPT_CONFIRM_TIMEOUT] = "timeout_not_below_agent"
            else:
                return self._merge(**{OPT_CONFIRM_TIMEOUT: value})

        current = self.config_entry.options.get(
            OPT_CONFIRM_TIMEOUT, DEFAULT_CONFIRM_TIMEOUT
        )
        schema = vol.Schema(
            {
                vol.Required(OPT_CONFIRM_TIMEOUT, default=current): NumberSelector(
                    NumberSelectorConfig(min=1, mode=NumberSelectorMode.BOX)
                )
            }
        )
        return self.async_show_form(
            step_id="timeout", data_schema=schema, errors=errors
        )

    async def async_step_auto_expose(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Toggle exposing a newly added entity to the agent automatically."""
        if user_input is not None:
            return self._merge(**{OPT_AUTO_EXPOSE: user_input[OPT_AUTO_EXPOSE]})

        current = self.config_entry.options.get(OPT_AUTO_EXPOSE, False)
        schema = vol.Schema({vol.Required(OPT_AUTO_EXPOSE, default=current): bool})
        return self.async_show_form(step_id="auto_expose", data_schema=schema)

    async def async_step_rotate_secret(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Rotate the webhook shared secret. There is no grace period."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get("confirm"):
                entry = self.config_entry
                new_secret = secrets.token_urlsafe(32)
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_WEBHOOK_SECRET: new_secret}
                )
                try:
                    await async_sync_tools(self.hass, entry, entry.runtime_data.client)
                except ElevenLabsError:
                    ir.async_create_issue(
                        self.hass,
                        DOMAIN,
                        ISSUE_ROTATION_UNSYNCED,
                        is_fixable=False,
                        severity=ir.IssueSeverity.ERROR,
                        translation_key=ISSUE_ROTATION_UNSYNCED,
                    )
                    errors["base"] = "rotation_unsynced"
                    return self.async_show_form(
                        step_id="rotate_secret",
                        data_schema=ROTATE_SCHEMA,
                        errors=errors,
                    )
                ir.async_delete_issue(self.hass, DOMAIN, ISSUE_ROTATION_UNSYNCED)
            return self._merge()

        return self.async_show_form(step_id="rotate_secret", data_schema=ROTATE_SCHEMA)

    async def async_step_rotate_webhook(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Rotate the webhook id. The new id is stored only once the sync succeeds."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get("confirm"):
                entry = self.config_entry
                old_id = entry.data[CONF_WEBHOOK_ID]
                new_id = webhook.async_generate_id()
                webhook.async_register(
                    self.hass,
                    DOMAIN,
                    WEBHOOK_NAME,
                    new_id,
                    async_handle_webhook,
                    local_only=False,
                    allowed_methods=["POST"],
                )
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_WEBHOOK_ID: new_id}
                )
                try:
                    await async_sync_tools(self.hass, entry, entry.runtime_data.client)
                except ElevenLabsError:
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, CONF_WEBHOOK_ID: old_id}
                    )
                    webhook.async_unregister(self.hass, new_id)
                    ir.async_create_issue(
                        self.hass,
                        DOMAIN,
                        ISSUE_ROTATION_UNSYNCED,
                        is_fixable=False,
                        severity=ir.IssueSeverity.ERROR,
                        translation_key=ISSUE_ROTATION_UNSYNCED,
                    )
                    errors["base"] = "rotation_unsynced"
                    return self.async_show_form(
                        step_id="rotate_webhook",
                        data_schema=ROTATE_SCHEMA,
                        errors=errors,
                    )
                else:
                    webhook.async_unregister(self.hass, old_id)
                    ir.async_delete_issue(self.hass, DOMAIN, ISSUE_ROTATION_UNSYNCED)
            return self._merge()

        return self.async_show_form(step_id="rotate_webhook", data_schema=ROTATE_SCHEMA)
