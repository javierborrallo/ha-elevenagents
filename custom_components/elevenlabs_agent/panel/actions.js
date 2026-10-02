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

// Return the sensitive checkbox and its two options for one entity.
function sensitiveControls(t, entityId, state, report) {
  const box = document.createElement("div");
  box.style.cssText =
    "display:flex;flex-direction:column;gap:2px;padding:4px 0 8px;" +
    "border-bottom:1px solid var(--divider-color)";
  const mark = state.sensitive[entityId] || { confirm: true, refuse_automatic: true };
  const main = checkRow(t.sensitiveLabel, Boolean(state.sensitive[entityId]));
  const subs = document.createElement("div");
  subs.style.cssText = "display:flex;flex-direction:column;gap:2px;padding-left:24px";
  const confirm = checkRow(t.sensitiveConfirm, mark.confirm);
  const refuse = checkRow(t.sensitiveRefuse, mark.refuse_automatic);
  const warning = document.createElement("small");
  warning.style.cssText = "color:var(--warning-color)";
  warning.textContent = t.sensitiveUnprotected;
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
  box.append(main.row, subs);
  paint();
  return box;
}

export function actionScreen(
  hass,
  { controlled, exceptions, sensitive, availableActions, onChange, onSensitiveChange },
) {
  const t = strings(hass.language);
  // Local copies of the exceptions and the sensitive marks. Every toggle rewrites
  // one key, then the changed mapping goes back through onChange or onSensitiveChange.
  const state = { exceptions: { ...exceptions }, sensitive: { ...(sensitive || {}) } };

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
      rowBody.appendChild(
        sensitiveControls(t, entityId, state, () => {
          if (onSensitiveChange) onSensitiveChange({ ...state.sensitive });
        }),
      );

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
