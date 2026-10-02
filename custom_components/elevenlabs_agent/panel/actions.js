import { strings } from "./strings.js";

// Turn a snake_case word into a readable label. "binary_sensor" -> "Binary sensor".
function humanizeWord(raw) {
  const spaced = raw.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function domainOf(entityId) {
  return entityId.split(".")[0];
}

// Return a collapsible container: ha-expansion-panel when Home Assistant
// provides it, a plain <details> otherwise. Collapsed by default either way.
function collapsible(headerText) {
  if (customElements.get("ha-expansion-panel")) {
    const panel = document.createElement("ha-expansion-panel");
    panel.header = headerText;
    return panel;
  }
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.style.cssText = "cursor:pointer;padding:8px 4px;font-weight:500";
  summary.textContent = headerText;
  details.appendChild(summary);
  return details;
}

// Update a collapsible's header text after it was created.
function setHeader(group, headerText) {
  if (group.tagName === "DETAILS") {
    group.querySelector("summary").textContent = headerText;
  } else {
    group.header = headerText;
  }
}

function bodyContainerFor(group) {
  const body = document.createElement("div");
  body.style.cssText =
    "display:flex;flex-direction:column;gap:4px;padding:4px 12px 12px";
  group.appendChild(body);
  return body;
}

// Return one checkbox with its label.
function checkRow(text, checked) {
  const row = document.createElement("label");
  row.style.cssText = "display:flex;align-items:center;gap:8px;padding:2px 0";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = checked;
  const label = document.createElement("span");
  label.textContent = text;
  row.append(input, label);
  return { row, input };
}

// Return the paragraph shown when no entity is controlled yet.
function emptyScreen(t) {
  const host = document.createElement("div");
  host.style.cssText =
    "display:flex;flex-direction:column;gap:12px;height:100%;overflow:auto";
  const empty = document.createElement("p");
  empty.style.cssText = "color:var(--secondary-text-color)";
  empty.textContent = t.actionsEmpty;
  host.appendChild(empty);
  return host;
}

// Group entity ids by domain. Put each domain in one collapsible section.
// headerFor(domain, entityIds) returns the section header.
// renderEntity(entityId, refreshGroupHeader) returns the row for one entity.
// The row calls refreshGroupHeader after each change.
function groupedByDomain(controlled, headerFor, renderEntity) {
  const host = document.createElement("div");
  host.style.cssText =
    "display:flex;flex-direction:column;gap:12px;height:100%;overflow:auto";

  const byDomain = new Map();
  for (const entityId of controlled) {
    const domain = domainOf(entityId);
    if (!byDomain.has(domain)) byDomain.set(domain, []);
    byDomain.get(domain).push(entityId);
  }

  for (const domain of [...byDomain.keys()].sort()) {
    const entityIds = byDomain.get(domain).sort();
    const group = collapsible(headerFor(domain, entityIds));
    group.style.cssText =
      "border:1px solid var(--divider-color);border-radius:12px;" +
      "background:var(--card-background-color)";
    const groupBody = bodyContainerFor(group);
    const refreshGroupHeader = () => setHeader(group, headerFor(domain, entityIds));

    for (const entityId of entityIds) {
      groupBody.appendChild(renderEntity(entityId, refreshGroupHeader));
    }

    host.appendChild(group);
  }

  return host;
}

export function actionScreen(hass, { controlled, exceptions, availableActions, onChange }) {
  const t = strings(hass.language);
  // A local copy of the exceptions. Every toggle rewrites one key, then the
  // changed mapping goes back through onChange.
  const state = { exceptions: { ...exceptions } };

  if (!controlled.length) return emptyScreen(t);

  function isRestricted(entityId) {
    const removed = state.exceptions[entityId];
    return Boolean(removed && removed.length);
  }

  function groupHeader(domain, entityIds) {
    const restricted = entityIds.filter(isRestricted).length;
    const count = t.actionsRestrictedCount
      .replace("{restricted}", restricted)
      .replace("{total}", entityIds.length);
    return `${humanizeWord(domain)} — ${count}`;
  }

  return groupedByDomain(controlled, groupHeader, (entityId, refreshGroupHeader) => {
    const entityState = hass.states ? hass.states[entityId] : undefined;
    const name = (entityState && entityState.attributes.friendly_name) || entityId;

    const row = collapsible(`${name} (${entityId})`);
    row.style.cssText = "border-top:1px solid var(--divider-color)";
    const rowBody = bodyContainerFor(row);

    const services = availableActions[entityId] || [];
    if (!services.length) {
      const none = document.createElement("p");
      none.style.cssText = "color:var(--secondary-text-color);margin:4px 0";
      none.textContent = t.actionsNoActions;
      rowBody.appendChild(none);
    }

    for (const service of services) {
      const label = document.createElement("label");
      label.style.cssText =
        "display:flex;align-items:center;gap:8px;padding:2px 0";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      const removedSoFar = state.exceptions[entityId] || [];
      checkbox.checked = !removedSoFar.includes(service);
      checkbox.addEventListener("change", () => {
        const removed = new Set(state.exceptions[entityId] || []);
        if (checkbox.checked) {
          removed.delete(service);
        } else {
          removed.add(service);
        }
        if (removed.size) {
          state.exceptions[entityId] = [...removed].sort();
        } else {
          delete state.exceptions[entityId];
        }
        refreshGroupHeader();
        onChange({ ...state.exceptions });
      });

      const text = document.createElement("span");
      text.textContent = humanizeWord(service.split(".")[1] || service);

      label.appendChild(checkbox);
      label.appendChild(text);
      rowBody.appendChild(label);
    }

    return row;
  });
}

// Return one entity's confirmation row: the name on the left, the
// "requires confirmation" checkbox on the right. Ticking it reveals, below
// and indented, the two protection options and the warning when both are off.
function confirmRow(t, hass, entityId, state, report) {
  const entityState = hass.states ? hass.states[entityId] : undefined;
  const name = (entityState && entityState.attributes.friendly_name) || entityId;

  const box = document.createElement("div");
  box.style.cssText =
    "display:flex;flex-direction:column;gap:2px;padding:4px 0 8px;" +
    "border-top:1px solid var(--divider-color)";

  const header = document.createElement("div");
  header.style.cssText =
    "display:flex;align-items:center;justify-content:space-between;gap:8px";
  const label = document.createElement("span");
  label.textContent = `${name} (${entityId})`;
  const main = checkRow(t.confirmRequired, Boolean(state.sensitive[entityId]));
  header.append(label, main.row);

  const mark = state.sensitive[entityId] || { confirm: true, refuse_automatic: true };
  const subs = document.createElement("div");
  subs.style.cssText = "display:flex;flex-direction:column;gap:2px;padding-left:24px";
  const confirm = checkRow(t.confirmAsk, mark.confirm);
  const refuse = checkRow(t.confirmRefuse, mark.refuse_automatic);
  const warning = document.createElement("small");
  warning.style.cssText = "color:var(--warning-color)";
  warning.textContent = t.confirmUnprotected;
  subs.append(confirm.row, refuse.row, warning);

  function paint() {
    const on = main.input.checked;
    subs.style.display = on ? "flex" : "none";
    const bare = on && !confirm.input.checked && !refuse.input.checked;
    warning.style.display = bare ? "" : "none";
  }

  function update() {
    if (main.input.checked) {
      state.sensitive[entityId] = {
        confirm: confirm.input.checked,
        refuse_automatic: refuse.input.checked,
      };
    } else {
      delete state.sensitive[entityId];
    }
    paint();
    report();
  }

  for (const input of [main.input, confirm.input, refuse.input]) {
    input.addEventListener("change", update);
  }
  box.append(header, subs);
  paint();
  return box;
}

export function confirmScreen(hass, { controlled, sensitive, onChange }) {
  const t = strings(hass.language);
  // A local copy of the sensitive marks. Every toggle rewrites one key, then
  // the changed mapping goes back through onChange.
  const state = { sensitive: { ...(sensitive || {}) } };

  if (!controlled.length) return emptyScreen(t);

  function markedCount(entityIds) {
    return entityIds.filter((id) => Boolean(state.sensitive[id])).length;
  }

  function groupHeader(domain, entityIds) {
    const count = t.confirmMarkedCount
      .replace("{marked}", markedCount(entityIds))
      .replace("{total}", entityIds.length);
    return `${humanizeWord(domain)} — ${count}`;
  }

  return groupedByDomain(controlled, groupHeader, (entityId, refreshGroupHeader) =>
    confirmRow(t, hass, entityId, state, () => {
      refreshGroupHeader();
      onChange({ ...state.sensitive });
    }),
  );
}
