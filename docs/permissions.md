# Permissions

## Confirmation

Some entities should not move on a misheard word: a lock, an alarm, a garage door. Under **Confirm
actions** in the panel, tick **Requires confirmation** on such an entity. Two options appear, both
on by default.

- **In conversations, ask before acting.** The agent's first call to act on the entity runs
  nothing. The agent hears `confirmation_required` and a code, and asks you to confirm. If you
  confirm, it repeats the call with the code, and the action runs.
- **In automatic events, refuse.** An automatic event has no person in it, so nobody can confirm.
  With this option on, the agent cannot act on the entity from an automatic event.

The agent asks, but Home Assistant decides. The repeated call runs only if it comes from the same
conversation, within 60 seconds, for the same entity, action and data, and only if you spoke after
the agent asked. A code the agent invents, reuses or carries to another entity is refused.

An entity that requires confirmation with both options off is not protected. The panel warns you.

If you stop letting the agent control an entity, its confirmation setting is kept. The agent cannot
act on the entity anyway, and the setting is back in place if you give control again.

Marking an entity does not mark a script, scene or group that moves it. A call to that script,
scene or group runs without asking. Mark it too, or do not give the agent control of it.

## Entities without a `unique_id`

Entities from YAML, templates and groups enter Home Assistant's entity registry only when they
declare a `unique_id`. Without one, the integration knows such an entity only by its `entity_id`.
Renaming it is a deletion followed by a new entity: the permission stays on the old name, and any
entity outside the registry that later takes that name receives it. An entity inside the registry
never does. The panel marks these entities **No unique_id**.

When one disappears, the integration cannot tell a deletion from a reload, so it removes nothing by
itself. If the entity is still gone ten seconds later, an issue under Settings > System > Repairs
lists it, and the panel shows it under **Missing entities**, where you remove its permissions.
Ignore the issue to keep them.

Add a `unique_id` and the entity enters the registry as a new entity. Its old permissions show
under **Missing entities**; select it again and the integration follows it like any other entity.
