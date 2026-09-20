import { strings } from "./strings.js";
import { ICON, el, icon } from "./ui.js";

// Return the area an entity belongs to, through its own registry entry or
// through the device that owns it. Returns null when it has no area.
function areaIdOf(hass, entityId) {
  const registered = (hass.entities || {})[entityId];
  if (!registered) return null;
  if (registered.area_id) return registered.area_id;
  const device = (hass.devices || {})[registered.device_id];
  return device ? device.area_id || null : null;
}

// Group every entity by area, sorted by area name, with the entities that
// have no area in a final group.
function groupsOf(hass, noAreaLabel) {
  const areas = hass.areas || {};
  const byArea = new Map();

  for (const entityId of Object.keys(hass.states || {})) {
    const areaId = areaIdOf(hass, entityId);
    if (!byArea.has(areaId)) byArea.set(areaId, []);
    const state = hass.states[entityId];
    byArea.get(areaId).push({
      entity_id: entityId,
      name: state.attributes.friendly_name || entityId,
    });
  }

  const groups = [];
  for (const [areaId, rows] of byArea) {
    rows.sort((left, right) => left.name.localeCompare(right.name));
    groups.push({
      id: areaId || "",
      name: areaId && areas[areaId] ? areas[areaId].name : noAreaLabel,
      last: !areaId,
      rows,
    });
  }
  groups.sort((left, right) => {
    if (left.last !== right.last) return left.last ? 1 : -1;
    return left.name.localeCompare(right.name);
  });
  return groups;
}

function matches(row, needle) {
  if (!needle) return true;
  return (
    row.name.toLowerCase().includes(needle) ||
    row.entity_id.toLowerCase().includes(needle)
  );
}

// The entity icon, through Home Assistant's own element when the frontend
// provides it. The fallback keeps the row's spacing without an icon.
function entityIcon(hass, entityId) {
  if (customElements.get("ha-state-icon")) {
    const node = document.createElement("ha-state-icon");
    node.hass = hass;
    node.stateObj = hass.states[entityId];
    node.className = "icon";
    return node;
  }
  return el("span", "icon");
}

/**
 * A searchable list of every entity, grouped by area, with a checkbox on
 * each row and one on each area that takes the whole area at once.
 *
 * `selected` is the initial list of entity ids. Every change is reported
 * through `onChange` as a new sorted array.
 */
export function entityTree(hass, { selected, onChange }) {
  const t = strings(hass.language);
  const chosen = new Set(selected);
  const expanded = new Set();
  let needle = "";

  const host = el("div", "tree");

  const toolbar = el("div", "tree-toolbar");
  const search = el("input", "search");
  search.type = "search";
  search.placeholder = t.treeSearch;
  toolbar.appendChild(search);
  const counter = el("span", "count");
  toolbar.appendChild(counter);
  const clear = el("button", "plain", t.treeClear);
  clear.type = "button";
  toolbar.appendChild(clear);
  host.appendChild(toolbar);

  const list = el("div", "tree-list");
  host.appendChild(list);

  const groups = groupsOf(hass, t.treeNoArea);

  function report() {
    onChange([...chosen].sort());
  }

  function setGroup(group, visible, on) {
    for (const row of visible) {
      if (on) {
        chosen.add(row.entity_id);
      } else {
        chosen.delete(row.entity_id);
      }
    }
    report();
    paint();
  }

  function paint() {
    const scroll = list.scrollTop;
    list.textContent = "";
    counter.textContent = t.treeSelected.replace("{count}", chosen.size);

    let shown = 0;
    for (const group of groups) {
      const visible = group.rows.filter((row) => matches(row, needle));
      if (!visible.length) continue;
      shown += visible.length;

      const picked = visible.filter((row) => chosen.has(row.entity_id)).length;
      const open = needle !== "" || expanded.has(group.id);

      const head = el("div", "group-head");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = picked === visible.length;
      box.indeterminate = picked > 0 && picked < visible.length;
      box.addEventListener("click", (event) => {
        event.stopPropagation();
        setGroup(group, visible, picked !== visible.length);
      });
      head.appendChild(box);
      head.appendChild(icon(open ? ICON.chevronDown : ICON.chevronRight));
      head.appendChild(icon(ICON.home));
      head.appendChild(el("div", "name", group.name));
      head.appendChild(el("span", "count", `${picked}/${visible.length}`));
      head.addEventListener("click", () => {
        if (expanded.has(group.id)) {
          expanded.delete(group.id);
        } else {
          expanded.add(group.id);
        }
        paint();
      });
      list.appendChild(head);

      if (!open) continue;

      for (const entry of visible) {
        const row = el("div", "row");
        const rowBox = document.createElement("input");
        rowBox.type = "checkbox";
        rowBox.checked = chosen.has(entry.entity_id);
        row.appendChild(rowBox);
        row.appendChild(entityIcon(hass, entry.entity_id));
        const name = el("div", "name");
        name.appendChild(el("span", null, entry.name));
        name.appendChild(el("span", "eid", entry.entity_id));
        row.appendChild(name);
        row.addEventListener("click", (event) => {
          if (event.target !== rowBox) rowBox.checked = !rowBox.checked;
          if (rowBox.checked) {
            chosen.add(entry.entity_id);
          } else {
            chosen.delete(entry.entity_id);
          }
          report();
          paint();
        });
        list.appendChild(row);
      }
    }

    if (!shown) list.appendChild(el("div", "empty", t.treeEmpty));
    list.scrollTop = scroll;
  }

  search.addEventListener("input", () => {
    needle = search.value.trim().toLowerCase();
    paint();
  });
  clear.addEventListener("click", () => {
    chosen.clear();
    report();
    paint();
  });

  paint();
  return host;
}
