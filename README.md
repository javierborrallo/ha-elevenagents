# ElevenLabs Agent for Home Assistant

Let an [ElevenLabs conversational agent](https://elevenlabs.io/docs/agents-platform/overview)
operate your Home Assistant installation through controlled, automatically synchronised tools.

> **Status: beta.** 0.1 works, and it is a first version. Expect changes that break an
> existing configuration. The configuration screens are replaced in 0.2.

## What this is

This is **not** "ElevenLabs text-to-speech for Home Assistant" — Home Assistant already
ships [that integration](https://www.home-assistant.io/integrations/elevenlabs/).

Here, the ElevenLabs agent is the conversational and reasoning layer, and Home Assistant
stays the source of truth and the secure execution layer:

```
You speak to the agent
      ↓
Agent calls a secure Home Assistant webhook tool
      ↓
Integration validates the request against its permission model
      ↓
Home Assistant reads state or runs an allowed action
      ↓
Agent answers naturally
```

## The point of it

You should never have to hand-write a tool schema in the ElevenLabs dashboard. The
integration inspects the entities you choose to expose, generates safe tool definitions
from them, synchronises them with your agent, and keeps them aligned as your Home
Assistant configuration changes.

## Design principles

- **Bring your own key.** Your ElevenLabs API key, and nothing else. No infrastructure
  operated by anyone else.
- **No inbound access to the Home Assistant API.** ElevenLabs never receives a Home
  Assistant token, nor access to the REST or WebSocket API.
- **Deny by default.** No entity is exposed until you select it. Selecting one grants
  every action it accepts, and you remove the ones you do not want.
- **The agent never gets unrestricted `call_service`.** Every inbound request is
  validated against a local allowlist before anything executes.

## Requirements

- Home Assistant 2026.6.4 or newer
- An ElevenLabs account with an agent, and an API key scoped for Conversational AI
  **write** access — a key scoped only to text-to-speech will not be able to
  synchronise tools
- A way for ElevenLabs to reach your Home Assistant webhook from the internet
  (Nabu Casa, a Cloudflare tunnel, or a reverse proxy)

## Setting it up

**Set your external URL first.** Settings → System → Network. The integration writes that
address into your agent's tools, so it refuses to configure without one. An internal
address would leave the agent calling a host it cannot reach.

1. Add the integration and paste your API key. It is validated against ElevenLabs before
   anything else happens.
2. Pick your agent from the list the integration reads back from your account.
3. Choose what the agent may **read**, and what it may **control**. Both start empty.
4. Optionally remove individual actions from a controlled entity — see *Selecting a lock*
   below before you skip this step.
5. Paste the prompt block into your agent's system prompt.

The integration then creates two tools in your agent, `ha_get_state` and `ha_call_service`,
and keeps their schemas aligned with your selection. Both point at one webhook, secured with
a shared secret. You can re-run the synchronisation at any time with the
`elevenlabs_agent.sync_tools` action, and rotate either the secret or the webhook address
from the options.

## What the agent can do

`ha_get_state` reads. It carries the entities you selected for reading **and** the ones you
selected for control, because being able to open a lock without being able to ask whether it
is open makes no sense. It accepts several entities in one call.

`ha_call_service` acts. It carries only the entities you selected for control, and only the
actions those entities accept, minus anything you removed.

An entity you did not select does not appear in either schema, and the server answers
`not_found` if the agent asks for it anyway — the same answer it gives for an entity that
does not exist. Nothing reveals what you chose to keep private.

## Security

Read this section before you expose a lock, an alarm or a garage door.

**An open agent is a door into your house.** By default an ElevenLabs agent accepts a
conversation from anyone holding its ID, and that ID is not a secret. Someone who talks to it
forges nothing: the commands arrive through the legitimate channel, carrying the right secret,
and do exactly what you approved. The integration raises a repair issue when it finds an agent
with neither signed URLs nor a hostname allowlist, and the repair closes the door for you.

**A Home Assistant upgrade can widen what is granted.** A new action added to a domain you
already exposed is granted on the entities you already selected, and appears ticked the next
time you open the exceptions screen. Selection is by entity, and it follows the entity.

**A timeout means unknown, not failure.** When Home Assistant does not confirm in time, the
integration reports `unconfirmed`, and the action may still have run. The prompt block tells
the agent to read the state instead of repeating the command.

**An entity outside the entity registry is identified by its `entity_id`.** Entities from
YAML, from templates and from groups never enter the registry. Rename one and its permission
stops matching: the agent loses access, and nothing says so.

## The recommended prompt block

The integration never writes to your agent's system prompt. That text is yours.

Paste these two sections into your agent's prompt, under the `Tools` and `Error handling`
headings [ElevenLabs recommends](https://elevenlabs.io/docs/agents-platform/best-practices/prompting-guide).
They are the only channel that reaches the model, and three rules of the integration's
contract exist nowhere else.

```markdown
# Tools

You operate a Home Assistant installation through two tools.

Use `ha_get_state` to find out what something is doing. Call it before you answer any
question about state, every time, even when the same question came up earlier in this
conversation. It takes a list, so ask for every entity you need in one call.

Use `ha_call_service` to change something. One call per action. Use only the entity ids
and the actions the tool offers you. When the user asks for something the tool does not
offer, tell them you cannot do that.

# Error handling

Read the response before you speak. Say results in plain language: field names and codes
such as `no_change` or `not_found` are for you, never for the listener.

## After a read

The response carries one entry per entity, under `results`.

An entry with a `state` is an answer. Use `name` and `area` to refer to the device the
way the user would.

An entry with `"error": "not_found"` is not available to you. Answer with the entries you
did get and leave that one out entirely. You know nothing about whether it exists.

## After an action

Every action comes back with an `outcome`. Decide what to say from that field:

1. `done` — it worked, and the entry carries the state the device is in now. Tell the
   user that state. When `assumed_state` is true, add that the device does not confirm
   its own state.
2. `no_change` — Home Assistant changed nothing, and the state in the entry is what the
   device holds now. When that state is what the user asked for, tell them it was already
   that way. When it is not, tell them the command went out and the device did not move.
3. `unconfirmed` — the command was sent and no result arrived in time. Call `ha_get_state`
   on the same entity and answer from what it returns. Never call `ha_call_service` again
   for that action: it may well have worked, and the read is what tells you.
4. `failed` — nothing ran. Say the action did not go through and give the reason in your
   own words. When the reason is `action_not_allowed`, the entity is reachable but that
   action is not permitted. When it is `not_found`, follow the rule under *After a read*.

## Anything else

A response carrying `"error": "malformed_request"` means the request itself was wrong.
Tell the user it could not be understood, and move on. The `detail` field is for
diagnosis; keep it to yourself.
```

## Uninstalling

Removing the integration detaches its two tools from your agent and then deletes them from
your workspace. Both steps are best effort: if ElevenLabs cannot be reached, the entry is
removed anyway and the tools stay behind.

Delete them by hand from the ElevenLabs dashboard, under the agent's tools. They are named
`ha_get_state` and `ha_call_service`. A tool you created yourself is never touched — the
integration only ever acts on the IDs it stored when it created them.

## Roadmap

| Version | Scope |
| --- | --- |
| 0.1.0 | Config flow, secure webhook, `ha_get_state` and `ha_call_service`, UI entity selection, permission allowlist, tool synchronisation |
| 0.2 | A configuration panel replacing the options flow, a reworked action-restriction screen, the ElevenLabs settings this integration writes exposed as settings you edit here, and an editor that offers the prompt block once at setup |
| 0.3 | `ha_list_entities`, `ha_list_areas`, richer tool descriptions, sync diffs and status, diagnostics |
| 0.4 | Push Home Assistant events to the agent |
| 0.5 | Confirmation for sensitive actions |

## Installation

Not yet available. This section will describe HACS installation once there is
something to install.

## Home Assistant versions

Two versions appear in this repository and they mean different things.

`hacs.json` declares **2026.6.4**, the minimum a user needs to install the integration.
`requirements_dev.txt` pins the newest release, which is what development and CI run against.
Developing against the newest release surfaces deprecations early; keeping the minimum lower
avoids excluding users who have not updated yet.

`pytest-homeassistant-custom-component` pins one exact Home Assistant version, so it moves with
`requirements_dev.txt`. Dependabot ignores both.

## Dependency updates

Dependabot is enabled and proposes updates for pip packages, GitHub Actions and
devcontainer features. It does not touch `homeassistant`, whose version must match the
`homeassistant` key in `hacs.json`.

A Renovate configuration is kept alongside it as `.github/_renovate.json`, inactive.

## Credits

Scaffolded from [ludeeus/integration_blueprint](https://github.com/ludeeus/integration_blueprint).

## License

MIT — see [LICENSE](LICENSE).
