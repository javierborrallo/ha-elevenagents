// Icon paths, from Material Design Icons. Each is the "d" of one path.
export const ICON = {
  check: "M21,7L9,19L3.5,13.5L4.91,12.09L9,16.17L19.59,5.59L21,7Z",
  alert:
    "M12,2L1,21H23M12,6L19.53,19H4.47M11,10V14H13V10M11,16V18H13V16",
  chevronRight: "M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z",
  chevronDown: "M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z",
  text:
    "M14,17H7V15H14M17,13H7V11H17M17,9H7V7H17M19,3H5C3.89,3 3,3.89 3,5V19A2,2" +
    " 0 0,0 5,21H19A2,2 0 0,0 21,19V5C21,3.89 20.1,3 19,3Z",
  tune:
    "M3,17V19H9V17H3M3,5V7H13V5H3M13,21V19H21V17H13V15H11V21H13M7,9V11H3V13H7" +
    "V15H9V9H7M21,13V11H11V13H21M15,9H17V7H21V5H17V3H15V9Z",
  rotate:
    "M12,6V9L16,5L12,1V4A8,8 0 0,0 4,12C4,13.57 4.46,15.03 5.24,16.26L6.7,14" +
    ".8C6.25,13.97 6,13 6,12A6,6 0 0,1 12,6M18.76,7.74L17.3,9.2C17.74,10.04 " +
    "18,11 18,12A6,6 0 0,1 12,18V15L8,19L12,23V20A8,8 0 0,0 20,12C20,10.43 1" +
    "9.54,8.97 18.76,7.74Z",
  eye:
    "M12,9A3,3 0 0,0 9,12A3,3 0 0,0 12,15A3,3 0 0,0 15,12A3,3 0 0,0 12,9M12," +
    "17A5,5 0 0,1 7,12A5,5 0 0,1 12,7A5,5 0 0,1 17,12A5,5 0 0,1 12,17M12,4.5" +
    "C7,4.5 2.73,7.61 1,12C2.73,16.39 7,19.5 12,19.5C17,19.5 21.27,16.39 23,"+
    "12C21.27,7.61 17,4.5 12,4.5Z",
  toggle:
    "M17,7H7A5,5 0 0,0 2,12A5,5 0 0,0 7,17H17A5,5 0 0,0 22,12A5,5 0 0,0 17,7" +
    "M17,15A3,3 0 0,1 14,12A3,3 0 0,1 17,9A3,3 0 0,1 20,12A3,3 0 0,1 17,15Z",
  minusCircle:
    "M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 " +
    "12,2M7,13V11H17V13H7Z",
  shapes: "M11,13H3V3H11V13M3,21H11V15H3V21M13,21H21V11H13V21M13,3V9H21V3H13Z",
  shieldCheck:
    "M10,17L6,13L7.41,11.59L10,14.17L16.59,7.58L18,9M12,1L3,5V11C3,16.55 " +
    "6.84,21.74 12,23C17.16,21.74 21,16.55 21,11V5L12,1Z",
  home:
    "M10,20V14H14V20H19V12H22L12,3L2,12H5V20H10Z",
  arrowLeft: "M20,11V13H8L13.5,18.5L12.08,19.92L4.16,12L12.08,4.08L13.5,5.5L8,11H20Z",
};

// Every rule the panel uses. The panel renders inside a shadow root, so
// these stay out of the Home Assistant document.
export const CSS = `
:host{display:block;height:100%;box-sizing:border-box;color:var(--primary-text-color)}
*{box-sizing:border-box}
.screen{display:flex;flex-direction:column;height:100%}
.toolbar{display:flex;align-items:center;gap:16px;height:56px;padding:0 8px;
 flex:0 0 auto;
 background:var(--app-header-background-color,var(--card-background-color));
 color:var(--app-header-text-color,var(--primary-text-color));
 border-bottom:1px solid var(--divider-color)}
.toolbar .title{font-size:20px;font-weight:400}
button.icon-button{display:flex;align-items:center;justify-content:center;
 width:40px;height:40px;border-radius:50%;border:none;background:none;
 color:inherit;cursor:pointer;padding:0;flex:0 0 auto}
button.icon-button:hover{background:rgba(127,127,127,.2)}
.page{flex:1;min-height:0;overflow:auto;padding:16px 16px 48px}
.stack{max-width:720px;margin:0 auto;display:flex;flex-direction:column;gap:16px}
.card{background:var(--ha-card-background,var(--card-background-color,#fff));
 border-radius:var(--ha-card-border-radius,12px);
 box-shadow:var(--ha-card-box-shadow,none);
 border:var(--ha-card-border-width,1px) solid
 var(--ha-card-border-color,var(--divider-color));overflow:hidden}
.card-header{font-size:20px;font-weight:400;padding:16px 16px 4px;margin:0}
.card-content{padding:16px}
.status{display:flex;align-items:center;gap:16px;padding:16px}
.status-icon{width:40px;height:40px;border-radius:50%;display:flex;
 align-items:center;justify-content:center;flex:0 0 auto;
 color:var(--text-primary-color,#fff)}
.status-icon.ok{background:var(--success-color,#4caf50)}
.status-icon.bad{background:var(--error-color,#db4437)}
.status-text{display:flex;flex-direction:column;min-width:0;flex:1}
button.status{width:100%;border:none;background:none;font:inherit;color:inherit;
 text-align:left;cursor:pointer}
button.status:hover{background:var(--secondary-background-color)}
.check-list{display:flex;flex-direction:column}
.check-row{display:flex;align-items:flex-start;gap:12px;padding:10px 0}
.check-row+.check-row{border-top:1px solid var(--divider-color)}
.check-row .status-icon{width:28px;height:28px;flex:none}
.status-icon.skipped{background:var(--disabled-color,#bdbdbd)}
.status-text strong{font-size:16px;font-weight:500}
.status-text small{color:var(--secondary-text-color);font-size:13px;
 overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nav-item{display:flex;align-items:center;gap:16px;padding:12px 16px;width:100%;
 background:none;border:none;color:inherit;font:inherit;text-align:left;
 cursor:pointer}
.nav-item:hover{background:var(--secondary-background-color)}
.nav-item+.nav-item{border-top:1px solid var(--divider-color)}
.nav-item .grow{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}
.nav-item .headline{font-size:15px}
.nav-item .supporting{font-size:13px;color:var(--secondary-text-color)}
svg.icon{width:24px;height:24px;fill:currentColor;flex:0 0 auto}
.muted{color:var(--secondary-text-color)}
dialog{width:min(900px,94vw);height:min(760px,88vh);max-width:none;
 max-height:none;padding:0;border:none;
 border-radius:var(--ha-card-border-radius,12px);
 background:var(--card-background-color,#fff);color:var(--primary-text-color);
 box-shadow:0 24px 38px 3px rgba(0,0,0,.14)}
dialog::backdrop{background:rgba(0,0,0,.5)}
.dlg{display:flex;flex-direction:column;height:100%}
.dlg-head{padding:16px 24px 12px;border-bottom:1px solid var(--divider-color)}
.dlg-head h2{margin:0;font-size:20px;font-weight:500}
.dlg-head p{margin:4px 0 0;font-size:13px;color:var(--secondary-text-color)}
.dlg-body{flex:1;min-height:0;overflow:auto;padding:16px 24px;display:flex;
 flex-direction:column;gap:16px}
.dlg-foot{display:flex;align-items:center;gap:8px;justify-content:flex-end;
 padding:12px 16px;border-top:1px solid var(--divider-color)}
.outcome{margin-right:auto;font-size:13px;color:var(--secondary-text-color);
 padding-left:8px}
button.primary{padding:8px 18px;border-radius:9999px;border:none;
 background:var(--primary-color);color:var(--text-primary-color,#fff);
 font:inherit;font-size:14px;cursor:pointer}
button.plain{padding:8px 18px;border-radius:9999px;border:none;background:none;
 color:var(--primary-color);font:inherit;font-size:14px;cursor:pointer}
button.danger{padding:8px 18px;border-radius:9999px;background:none;
 border:1px solid var(--error-color);color:var(--error-color);font:inherit;
 font-size:14px;cursor:pointer;align-self:flex-start}
button[disabled]{opacity:.5;cursor:default}
.tree{display:flex;flex-direction:column;gap:12px;height:100%;min-height:0}
.tree-toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.technical{display:flex;gap:6px;align-items:center;font-size:14px;color:var(--secondary-text-color);cursor:pointer}
.search{flex:1;min-width:200px;padding:10px 14px;font:inherit;font-size:15px;
 border-radius:9999px;border:1px solid var(--divider-color);
 background:var(--card-background-color);color:inherit}
.tree-list{flex:1;min-height:0;overflow:auto;
 border:1px solid var(--divider-color);border-radius:12px;
 background:var(--card-background-color)}
.group-head{display:flex;align-items:center;gap:12px;padding:10px 12px;
 cursor:pointer;background:var(--secondary-background-color);
 border-bottom:1px solid var(--divider-color)}
.group-head .name{flex:1;min-width:0}
.row{display:flex;align-items:center;gap:12px;padding:8px 12px 8px 44px;
 cursor:pointer;border-bottom:1px solid var(--divider-color)}
.row:hover,.group-head:hover{background:var(--secondary-background-color)}
.row .name{flex:1;min-width:0;display:flex;flex-direction:column}
.row .name span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row .eid{font-size:12px;color:var(--secondary-text-color)}
.count{font-size:12px;color:var(--secondary-text-color);white-space:nowrap}
input[type=checkbox]{width:18px;height:18px;accent-color:var(--primary-color);
 flex:0 0 auto;margin:0;cursor:pointer}
.empty{padding:32px;text-align:center;color:var(--secondary-text-color)}
.panes{display:grid;grid-template-columns:1fr 1fr;gap:16px;flex:1;min-height:0}
.pane{display:flex;flex-direction:column;gap:8px;min-height:0}
.pane header{display:flex;align-items:center;gap:8px}
.pane h4{margin:0;font-size:14px;font-weight:500;flex:1}
textarea{flex:1;min-height:200px;padding:12px;font-family:monospace;
 font-size:13px;line-height:1.5;border-radius:12px;resize:none;
 border:1px solid var(--divider-color);background:var(--card-background-color);
 color:inherit}
textarea[readonly]{background:var(--secondary-background-color)}
.field{display:flex;flex-direction:column;gap:6px}
.field .label{font-weight:500;font-size:14px}
.field .hint{font-size:12px;color:var(--secondary-text-color)}
input.text{padding:10px 12px;font:inherit;border-radius:8px;
 border:1px solid var(--divider-color);background:var(--card-background-color);
 color:inherit}
.wizard{display:flex;flex-direction:column;gap:16px;padding:16px;flex:1;
 min-height:0}
.wizard-body{flex:1;min-height:0;display:flex;flex-direction:column;overflow:auto}
.wizard-body>*,.dlg-body>.tree,.dlg-body>.titled{flex:1;min-height:0}
.wizard-foot{display:flex;gap:8px;align-items:center;justify-content:flex-end}
h3.title{margin:0;font-size:20px;font-weight:500}
p.subtitle{margin:0;color:var(--secondary-text-color);font-size:13px}
.titled{display:flex;flex-direction:column;gap:12px;height:100%;min-height:0}
.titled>.grow{flex:1;min-height:0}
@media(max-width:800px){.panes{grid-template-columns:1fr}
 .row{padding-left:32px}}
`;

export function icon(path) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  const shape = document.createElementNS("http://www.w3.org/2000/svg", "path");
  shape.setAttribute("d", path);
  svg.appendChild(shape);
  return svg;
}

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

export function button(label, className, onClick) {
  const node = el("button", className, label);
  node.addEventListener("click", onClick);
  return node;
}

// Enable or disable a button and dim it to show which state it is in.
export function setEnabled(node, enabled) {
  node.disabled = !enabled;
}

// The bar every screen sits under: a back arrow that leaves the panel, and
// the integration's name.
export function toolbar(titleText, backLabel, onBack) {
  const bar = el("div", "toolbar");
  const back = el("button", "icon-button");
  back.type = "button";
  back.title = backLabel;
  back.setAttribute("aria-label", backLabel);
  back.appendChild(icon(ICON.arrowLeft));
  back.addEventListener("click", onBack);
  bar.appendChild(back);
  bar.appendChild(el("div", "title", titleText));
  return bar;
}

// A card, optionally with a header line. The returned element carries the
// body as `.body`, which is unpadded so a list can reach the card's edges.
export function card(headerText) {
  const node = el("div", "card");
  if (headerText) node.appendChild(el("div", "card-header", headerText));
  const body = el("div");
  node.appendChild(body);
  node.body = body;
  return node;
}

// The connection line at the top of the page: a coloured disc, a headline
// and one line of detail.
export function statusCard(ok, headline, detail, onClick) {
  const node = card();
  const row = el(onClick ? "button" : "div", "status");
  if (onClick) {
    row.type = "button";
    row.addEventListener("click", onClick);
  }
  const disc = el("div", `status-icon ${ok ? "ok" : "bad"}`);
  disc.appendChild(icon(ok ? ICON.check : ICON.alert));
  row.appendChild(disc);
  const text = el("div", "status-text");
  text.appendChild(el("strong", null, headline));
  text.appendChild(el("small", null, detail));
  row.appendChild(text);
  if (onClick) row.appendChild(icon(ICON.chevronRight));
  node.body.appendChild(row);
  return node;
}

// One row of a navigation card: an icon, a headline, one line of
// explanation, and a chevron.
function navItem({ iconPath, headline, supporting, onClick }) {
  const node = el("button", "nav-item");
  node.type = "button";
  node.appendChild(icon(iconPath));
  const grow = el("div", "grow");
  grow.appendChild(el("div", "headline", headline));
  if (supporting) grow.appendChild(el("div", "supporting", supporting));
  node.appendChild(grow);
  node.appendChild(icon(ICON.chevronRight));
  node.addEventListener("click", onClick);
  return node;
}

// A card whose body is a list of navigation rows.
export function navCard(headerText, items) {
  const node = card(headerText);
  for (const item of items) node.body.appendChild(navItem(item));
  return node;
}

// A title and a one-sentence subtitle above content that fills the rest of
// the available height.
export function titled(titleText, subtitleText, content) {
  const node = el("div", "titled");
  node.appendChild(el("h3", "title", titleText));
  node.appendChild(el("p", "subtitle", subtitleText));
  content.classList.add("grow");
  node.appendChild(content);
  return node;
}

// A labelled field: a heading line, a line saying what the field does,
// and the control. A constraint on the value belongs to the control
// itself, not here.
export function field(labelText, descriptionText, control) {
  const node = el("div", "field");
  node.appendChild(el("div", "label", labelText));
  if (descriptionText) node.appendChild(el("div", "hint", descriptionText));
  node.appendChild(control);
  return node;
}

// True when a save outcome allows moving forward or closing a dialog.
export function isAdvanceable(result) {
  return (
    Boolean(result) &&
    (result.outcome === "ok" || result.outcome === "saved_not_sent")
  );
}

// Turn a save outcome into the sentence a user reads.
export function describeSaveOutcome(t, result) {
  if (!result) return "";
  if (result.outcome === "ok") return t.saved;
  if (result.outcome === "saved_not_sent") return t.savedNotSent;
  if (result.detail === "timeout_not_below_agent") return t.timeoutNotBelowAgent;
  if (result.detail === "events_out_of_range") return t.eventsOutOfRange;
  if (result.detail === "invalid_public_url") return t.invalidPublicUrl;
  if (result.detail === "no_public_url") return t.noPublicUrl;
  return t.saveFailed.replace("{detail}", result.detail || "");
}

// Report a screen's first edit at most once, however many fields it holds.
export function dirtyReporter(onDirty) {
  let fired = false;
  return () => {
    if (fired) return;
    fired = true;
    if (onDirty) onDirty();
  };
}

/**
 * Open a modal dialog inside `root`.
 *
 * `build` returns the dialog body. It receives the dialog and `allowSave`,
 * which enables or disables the save button. `onSave` is optional: without
 * it the dialog shows only a close button. With it, the dialog saves,
 * reports the outcome, and closes when the outcome allows it.
 *
 * `saveLabel` and `saveClass` replace the text and the class of the save
 * button. `saveDisabled` shows the save button disabled at first.
 */
export function openDialog(
  root,
  { t, title, subtitle, build, onSave, saveLabel, saveClass, saveDisabled },
) {
  const dialog = document.createElement("dialog");
  const frame = el("div", "dlg");

  const head = el("div", "dlg-head");
  head.appendChild(el("h2", null, title));
  if (subtitle) head.appendChild(el("p", null, subtitle));
  frame.appendChild(head);

  let save = null;
  const allowSave = (enabled) => {
    if (save) setEnabled(save, enabled);
  };

  const body = el("div", "dlg-body");
  body.appendChild(build(dialog, allowSave));
  frame.appendChild(body);

  const foot = el("div", "dlg-foot");
  const outcome = el("div", "outcome");
  foot.appendChild(outcome);

  const close = button(onSave ? t.cancel : t.close, "plain", () => {
    dialog.close();
  });
  foot.appendChild(close);

  if (onSave) {
    save = button(saveLabel || t.save, saveClass || "primary", async () => {
      outcome.textContent = "";
      setEnabled(save, false);
      const result = await onSave();
      setEnabled(save, true);
      if (isAdvanceable(result)) {
        dialog.close();
        return;
      }
      outcome.textContent = describeSaveOutcome(t, result);
    });
    if (saveDisabled) setEnabled(save, false);
    foot.appendChild(save);
  }

  frame.appendChild(foot);
  dialog.appendChild(frame);
  dialog.addEventListener("close", () => dialog.remove());
  root.appendChild(dialog);
  dialog.showModal();
  return dialog;
}

/**
 * Size the panel host.
 *
 * Home Assistant gives that element a background, a display, a box-sizing
 * and the safe-area padding, and no height at all. Without a height every
 * "height:100%" below resolves against the content and the lists collapse.
 * The display and box-sizing are repeated here on purpose: they carry the
 * layout, an element left at its default display ignores a height
 * outright, and repeating them costs nothing.
 */
export function fillPanelHeight(element) {
  let node = element.parentNode;
  while (node) {
    if (node.host) {
      node = node.host;
      continue;
    }
    if (node.tagName === "HA-PANEL-CUSTOM") {
      Object.assign(node.style, {
        display: "block",
        boxSizing: "border-box",
        height: "100%",
        minHeight: "100vh",
      });
      return;
    }
    node = node.parentNode;
  }
}
