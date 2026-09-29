# ElevenAgents for Home Assistant

Let an [ElevenLabs conversational agent](https://elevenlabs.io/docs/agents-platform/overview)
operate your Home Assistant installation through controlled, automatically synchronised tools.

> **Status: beta.** Expect changes that break an existing configuration.

## What this is

This is **not** "ElevenLabs text-to-speech for Home Assistant" — Home Assistant already
ships [that integration](https://www.home-assistant.io/integrations/elevenlabs/).

Here, the ElevenAgents agent is the conversational and reasoning layer, and Home Assistant
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

The integration writes your agent's tool schemas for you. It inspects the entities you
choose to expose, generates the tool definitions from them, synchronises them with your
agent, and keeps them aligned as your Home Assistant configuration changes.

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
- An ElevenLabs account with an agent, and an API key scoped for ElevenAgents
  **write** access — a key scoped only to text-to-speech will not be able to
  synchronise tools
- A way for ElevenLabs to reach your Home Assistant webhook from the internet
  (Nabu Casa, a Cloudflare tunnel, ngrok, or a reverse proxy)

## Setting it up

**Set your external URL first.** Settings → System → Network. The integration writes that
address into your agent's tools, so it refuses to configure without one.

Adding the integration asks for two things: your API key, validated against ElevenLabs
before anything else happens, and your agent, picked from the list read back from your
account.

Nothing is exposed yet. The closing message links to the configuration panel, which is also
what **Configure** opens on the integration page. The first time, the panel walks six steps:

1. **Agent settings** — the opening line, how long an action waits to be confirmed, and
   whether the agent speaks before it acts.
2. **Prompt** — your agent's prompt beside the block this integration recommends, to copy
   across as much or as little as you want.
3. **Read** — the entities the agent may read.
4. **Control** — the entities it may act on.
5. **Restrictions** — actions to take back from a controlled entity. Skippable.
6. **Automatic exposure** — what happens to entities you add to Home Assistant later.

Afterwards the panel opens on a page of cards instead of the wizard: the agent's prompt,
settings and rotations, and the four entity screens, each behind its own row.

The integration creates two tools in your agent, `ha_get_state` and `ha_call_service`, and
keeps their schemas aligned with your selection. Both point at one webhook, secured with a
shared secret. You can re-run the synchronisation at any time with the
`elevenlabs_agent.sync_tools` action, and rotate either the secret or the webhook address
from the panel.

**Check that everything works** by clicking the status card at the top of the panel, or with
the `elevenlabs_agent.check` action. It confirms that the integration is loaded, that
ElevenLabs accepts your API key, that the agent is protected, that both tools are attached to
it and match your selection, that your webhook answers at its external address, that the
confirmation wait is shorter than the agent's, and that no entity you granted has disappeared.
It talks to ElevenLabs, never to the agent, so it spends no conversation minutes. The action
returns the same report, for use in an automation. The webhook test calls your external address
from Home Assistant itself: behind a router that cannot reach its own public address from
inside, it reports a problem ElevenLabs would not see.

**Watch it from a dashboard.** The integration adds a device for the agent with a binary sensor
for each of those checks and one for the agent as a whole. Each shows *OK* or *Problem*. They
update after every synchronisation and every check you run, and never poll. A check that could not
run shows *Unknown*; while the integration is not loaded, every sensor is *Unavailable*. The
*Tools up to date* sensor lists, as attributes, the entities the agent is missing and those it
still holds after you removed them. The panel marks the first kind *Not synced*. The agent sensor
leaves out the webhook test, because of the router case above; the webhook's own sensor still
shows it.

## What the agent can do

`ha_get_state` reads. It carries the entities you selected for reading **and** the ones you
selected for control. It accepts several entities in one call.

`ha_call_service` acts. It carries only the entities you selected for control, and only the
actions those entities accept, minus anything you removed.

An entity you did not select does not appear in either schema, and the server answers
`not_found` if the agent asks for it anyway — the same answer it gives for an entity that
does not exist. Nothing reveals what you chose to keep private.

**The schemas follow Home Assistant.** Rename an entity, move it or its device to another area,
or rename an area, and the agent's tools are updated about ten seconds later. A renamed entity
keeps its permissions. Delete an entity and its permissions go with it, so a new entity never
inherits them by taking the same name.

## Security

Read this section before you expose a lock, an alarm or a garage door.

**An open agent is a door into your house.** By default an ElevenAgents agent accepts a
conversation from anyone holding its ID, and that ID is not a secret. Someone who talks to it
forges nothing: the commands arrive through the legitimate channel, carrying the right secret,
and do exactly what you approved. The integration raises a repair issue when it finds an agent
with neither signed URLs nor a hostname allowlist, and the repair closes the door for you.

**A Home Assistant upgrade can widen what is granted.** A new action added to a domain you
already exposed is granted on the entities you already selected, and appears ticked the next
time you open the restrictions screen. Selection is by entity, and it follows the entity.

**Automatic exposure can grant control without a review.** Two switches, both off by default,
decide whether entities registered after setup join: one for reading, one for control. They act
inside the areas you chose, and only when an entity registers — a device you move into a watched
area later is not reconsidered. So a device already inside your selection can gain an entity,
through a firmware update or a new sub-device, and that entity becomes controllable without
anyone having looked at it.

**A timeout means unknown, not failure.** When Home Assistant does not confirm in time, the
integration reports `unconfirmed`, and the action may still have run. The prompt block tells
the agent to read the state instead of repeating the command.

**An entity outside the entity registry is identified by its `entity_id`.** Entities from
YAML, from templates and from groups enter the registry only when they declare a `unique_id`.
Without one, a rename is a deletion followed by a new entity: the permission stays on the old
name, and any entity that later takes that name receives it. The panel marks these entities
**No unique_id**.

When one disappears, the integration cannot tell a deletion from a reload, so it removes nothing
by itself. If the entity is still gone ten seconds later, a repair issue lists it, and the panel
shows it under **Missing entities**, where you remove its permissions. Ignore the issue to keep
them. Add a `unique_id` and the entity is followed like any other.

## The recommended prompt block

Your agent's prompt is yours. The integration writes it only when you press save on the
panel's prompt screen, which shows your text beside this block and merges nothing by itself.

Paste these two sections into your agent's prompt, under the `Tools` and `Error handling`
headings [ElevenLabs recommends](https://elevenlabs.io/docs/agents-platform/best-practices/prompting-guide).
They are the only channel that reaches the model, and three rules of the integration's
contract exist nowhere else.

```markdown
# Tools

You operate a Home Assistant installation through two tools.

## Choosing the device

Before every `ha_call_service`, match the user's words to the catalogue in the tool's
`entity_id` description, by name and room:

- One entry matches and it can do what was asked: call the tool on that entry.
- Several entries match: ask which one.
- Nothing matches, or the entry that matches cannot do what was asked: say so and call
  nothing. Do not change a different device that could, and do not do something close
  to what was asked instead.

Changing something the user did not ask for is worse than changing nothing.

## Using the tools

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

When you could do only part of a request, say which part you did not do.

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

## Events

An automation can hand the agent something that just happened. The agent reads it as a message,
can look at the house or act through the same two tools, and answers. What it does next — call you
through a flow you build in ElevenLabs, or turn the heating on — is up to your agent.

```yaml
action: elevenlabs_agent.send_event
data:
  config_entry_id: <your entry>
  message: "The house dropped to 10 °C."
  entity_id: sensor.living_room_temperature
  priority: high
response_variable: reply
```

`entity_id` adds that entity's current state, and must be one the agent may read. `event_type`,
`priority` and `data` reach your agent as dynamic variables. The reply comes back in
`response_variable` and as an `elevenlabs_agent_reply` event.

Each delivered event is a short text conversation and **spends ElevenLabs credits**. Three settings
in the panel keep that in hand: a cooldown for the same event (5 minutes; high priority skips it),
a limit per hour (20; nothing skips it), and a memory window (30 minutes) during which recent events
travel with the next one. A dropped event fires `elevenlabs_agent_event_dropped`; one that could not
be delivered fires `elevenlabs_agent_event_failed` and fails the action with the reason.

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
| 0.3 | Richer tool descriptions, sync diffs and status sensors |
| 0.4 | Push Home Assistant events to the agent |
| 0.5 | Confirmation for sensitive actions |

## Installation

Not published yet.

## Home Assistant versions

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

## Credits

Scaffolded from [ludeeus/integration_blueprint](https://github.com/ludeeus/integration_blueprint).

## License

MIT — see [LICENSE](LICENSE).
