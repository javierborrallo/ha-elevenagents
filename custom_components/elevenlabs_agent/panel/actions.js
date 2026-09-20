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

export function actionScreen(
  hass,
  { controlled, exceptions, availableActions, onChange },
) {
  const t = strings(hass.language);
  // Local copy of the mapping. Every toggle rewrites one key here, then the
  // whole mapping is handed back through onChange.
  const state = { exceptions: { ...exceptions } };

  const host = document.createElement("div");
  host.style.cssText =
    "display:flex;flex-direction:column;gap:12px;height:100%;overflow:auto";

  if (!controlled.length) {
    const empty = document.createElement("p");
    empty.style.cssText = "color:var(--secondary-text-color)";
    empty.textContent = t.actionsEmpty;
    host.appendChild(empty);
    return host;
  }

  const byDomain = new Map();
  for (const entityId of controlled) {
    const domain = domainOf(entityId);
    if (!byDomain.has(domain)) byDomain.set(domain, []);
    byDomain.get(domain).push(entityId);
  }

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

  for (const domain of [...byDomain.keys()].sort()) {
    const entityIds = byDomain.get(domain).sort();
    const group = collapsible(groupHeader(domain, entityIds));
    group.style.cssText =
      "border:1px solid var(--divider-color);border-radius:12px;" +
      "background:var(--card-background-color)";
    const groupBody = bodyContainerFor(group);
    const refreshGroupHeader = () => {
      setHeader(group, groupHeader(domain, entityIds));
    };

    for (const entityId of entityIds) {
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

      groupBody.appendChild(row);
    }

    host.appendChild(group);
  }

  return host;
}
