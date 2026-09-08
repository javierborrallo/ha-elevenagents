# ElevenLabs Agent for Home Assistant

Let an [ElevenLabs conversational agent](https://elevenlabs.io/docs/agents-platform/overview)
operate your Home Assistant installation through controlled, automatically synchronised tools.

> **Status: early development.** Nothing is usable yet. The integration is being
> built against a latency spike first; see the roadmap below.

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

- **Bring your own key.** Your ElevenLabs API key and agent ID. No infrastructure
  operated by anyone else.
- **No inbound access to the Home Assistant API.** ElevenLabs never receives a Home
  Assistant token, nor access to the REST or WebSocket API.
- **Deny by default.** Only the entities, domains and actions you explicitly approve.
  Locks, alarms, cameras and exterior access are hidden unless you opt in.
- **The agent never gets unrestricted `call_service`.** Every inbound request is
  validated against a local allowlist before anything executes.

## Requirements

- Home Assistant 2026.6.4 or newer
- An ElevenLabs account with an agent, and an API key scoped for Conversational AI
  **write** access — a key scoped only to text-to-speech will not be able to
  synchronise tools
- A way for ElevenLabs to reach your Home Assistant webhook from the internet
  (Nabu Casa, a Cloudflare tunnel, or a reverse proxy)

## Roadmap

| Version | Scope |
| --- | --- |
| 0.1.0 | Config flow, secure webhook, `ha_get_state` and `ha_call_service`, UI entity selection, permission allowlist, tool synchronisation |
| 0.2 | `ha_list_entities`, `ha_list_areas`, richer tool descriptions, sync diffs and status |
| 0.3 | Push Home Assistant events to the agent |
| 0.4 | Confirmation for sensitive actions |

## Installation

Not yet available. This section will describe HACS installation once there is
something to install.

## Dependency updates

Dependabot is enabled and proposes updates for pip packages, GitHub Actions and
devcontainer features. It does not touch `homeassistant`, whose version must match the
`homeassistant` key in `hacs.json`.

A Renovate configuration is kept alongside it as `.github/_renovate.json`, inactive. Use one
or the other, never both.

## Credits

Scaffolded from [ludeeus/integration_blueprint](https://github.com/ludeeus/integration_blueprint).

## License

MIT — see [LICENSE](LICENSE).
