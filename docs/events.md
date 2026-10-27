# Events

## Automatic events

An automation can tell the agent that something happened. The agent reads it as a message, can
read the house or act through the same two tools, and answers. What it does next, such as call you
through a flow you build in ElevenLabs or turn the heating on, is up to your agent.

```yaml
action: elevenagents.send_event
data:
  config_entry_id: <your entry>
  message: "The house dropped to 10 °C."
  entity_id: sensor.living_room_temperature
  priority: high
response_variable: reply
```

`entity_id` adds that entity's current state, and must be one the agent may read. `event_type`,
`priority` and `data` reach your agent as dynamic variables. The message takes up to 1,000
characters, and `data` up to 4 KiB. The reply comes back in `response_variable` and as an
`elevenagents_reply` event.

Each automatic event the agent receives is a short text conversation and spends ElevenLabs credits.
Three settings in the panel keep that in hand:

- **Event cooldown**, 5 minutes by default: the same event is not sent again within it. Events are
  the same when they share their `event_type`, or without one their `entity_id`, or without either
  their message. High priority skips the cooldown.
- **Events per hour**, 20 by default. Nothing skips it.
- **Event memory**, 30 minutes by default: recent events travel with the next one. 0 sends each
  event alone.

Recent events travel marked as already handled, so the agent does not act on them again. The mark
is an instruction to the model, not a guarantee. For an event that gives an order, set
`new_conversation: true`: it travels alone, and no later event carries it.

An event the limits stop fires `elevenagents_event_dropped`, and the action returns `dropped` with
the reason. An event that cannot reach the agent fires `elevenagents_event_failed`, and the action
fails with the reason.

## The action event

Every action the agent asks for fires `elevenagents_action` and shows in the logbook as
"ElevenAgents". The event carries `entity_id`, `action`, `outcome`, `error` and `conversation_id`.
Reads do not fire it. The action's `data` and confirmation codes are never included.

```yaml
triggers:
  - trigger: event
    event_type: elevenagents_action
    event_data:
      action: lock.unlock
actions:
  - action: notify.notify
    data:
      message: "The agent asked to unlock {{ trigger.event.data.entity_id }}: {{ trigger.event.data.outcome }}."
```
