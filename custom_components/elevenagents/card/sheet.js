// The detail sheet of the ElevenAgents card. It shows the conversation over the page.

import { el, icon } from "./dom.js";

const MAIN_ICON = { talk: "mic", hang: "hang", cancel: "close" };

const STYLE = `
:host { position: fixed; inset: 0; z-index: 1000; display: block; color: var(--primary-text-color);
  font-family: var(--ha-font-family-body, system-ui, sans-serif); }
.backdrop { position: absolute; inset: 0; background: rgba(0, 0, 0, 0.45); }
.panel { position: absolute; left: 0; right: 0; bottom: 0; margin: 0 auto; max-width: 560px; max-height: 88vh;
  display: flex; flex-direction: column; border-radius: 20px 20px 0 0; box-shadow: 0 -4px 24px rgba(0, 0, 0, 0.25);
  background: var(--ha-card-background, var(--card-background-color, #fff));
  --ea-accent: var(--primary-color, #03a9f4); --ea-tint: rgba(var(--rgb-primary-color, 3, 169, 244), 0.14);
  --ea-danger: var(--error-color, #db4437); --ea-sub: var(--secondary-text-color, #727272);
  --ea-soft: var(--secondary-background-color, #e5e5e5); --ea-line: var(--divider-color, rgba(0, 0, 0, 0.12));
  --ea-warn: var(--warning-color, #ffa600); --ea-warn-bg: color-mix(in srgb, var(--warning-color, #ffa600) 16%, transparent); }
.handle { align-self: center; width: 36px; height: 4px; border-radius: 2px; background: var(--ea-line); margin: 10px 0 4px; }
.head { display: flex; align-items: center; gap: 12px; padding: 8px 20px 12px; }
.avatar { flex: 0 0 auto; width: 44px; height: 44px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
  background: var(--ea-tint); color: var(--ea-accent); }
.panel[data-state="listening"] .avatar { background: var(--ea-sub); color: var(--card-background-color, #fff); }
.panel[data-state="speaking"] .avatar { background: var(--ea-accent); color: var(--text-primary-color, #fff); box-shadow: 0 0 0 5px var(--ea-tint); }
.panel[data-state="ended"] .avatar, .panel[data-state="connecting"] .avatar { background: var(--ea-soft); color: var(--ea-sub); }
.text { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.title { font-size: 18px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.status { font-size: 13px; color: var(--ea-sub); }
.panel[data-state="listening"] .status { color: var(--primary-text-color); font-weight: 600; }
.panel[data-state="speaking"] .status { color: var(--ea-accent); font-weight: 600; }
.close { flex: 0 0 auto; width: 44px; height: 44px; border: none; border-radius: 50%; padding: 0; background: transparent;
  color: var(--ea-sub); display: flex; align-items: center; justify-content: center; cursor: pointer; }
.clock { display: flex; flex-direction: column; gap: 6px; padding: 0 20px 16px; }
.track { height: 6px; border-radius: 3px; background: var(--ea-soft); overflow: hidden; }
.fill { height: 100%; width: 0; background: var(--ea-accent); }
.clock-row { display: flex; justify-content: space-between; font-size: 12px; color: var(--ea-sub); }
.controls { display: flex; gap: 10px; padding: 0 20px 12px; }
.tile { flex: 1 1 0; height: 64px; border-radius: 12px; border: 1px solid var(--ea-line); background: var(--ea-soft);
  color: var(--primary-text-color); font: inherit; font-size: 12px; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 4px; cursor: pointer; }
.tile[aria-pressed="true"] { background: var(--primary-text-color); color: var(--card-background-color, #fff); }
.tile.talk { border: none; background: var(--ea-accent); color: var(--text-primary-color, #fff); font-weight: 600; }
.tile.hang { border: none; background: var(--ea-danger); color: var(--text-primary-color, #fff); font-weight: 600; }
.slot { display: flex; }
form { display: flex; gap: 8px; align-items: center; margin: 0; padding: 0 20px 16px; }
input { flex: 1 1 auto; min-width: 0; height: 44px; box-sizing: border-box; padding: 0 14px; border-radius: 22px;
  border: 1px solid var(--ea-line); background: var(--ea-soft); color: var(--primary-text-color); font: inherit; font-size: 15px; }
.send { flex: 0 0 auto; width: 44px; height: 44px; border-radius: 50%; border: none; padding: 0; background: var(--ea-accent);
  color: var(--text-primary-color, #fff); display: flex; align-items: center; justify-content: center; cursor: pointer; }
.send:disabled, input:disabled { opacity: 0.5; }
.scroll { flex: 1 1 auto; min-height: 0; overflow-y: auto; border-top: 1px solid var(--ea-line); padding: 16px 20px;
  display: flex; flex-direction: column; gap: 18px; }
.section, .list { display: flex; flex-direction: column; gap: 10px; }
.label { font-size: 12px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--ea-sub); }
.action { display: flex; align-items: center; gap: 12px; font-size: 14px; font-weight: 600; }
.dot { flex: 0 0 auto; width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
  background: var(--ea-tint); color: var(--ea-accent); }
.action.warn .dot { background: var(--ea-warn-bg); color: var(--ea-warn); }
.action.bad .dot { background: color-mix(in srgb, var(--error-color, #db4437) 14%, transparent); color: var(--ea-danger); }
.say { font-size: 14px; line-height: 1.45; }
.say.user, .say.cut { color: var(--ea-sub); }
.tag { margin-left: 6px; font-size: 12px; font-weight: 600; }
.empty { font-size: 14px; color: var(--ea-sub); }
.foot { display: flex; align-items: center; gap: 6px; padding: 12px 20px 20px; border-top: 1px solid var(--ea-line);
  font-size: 12px; color: var(--ea-sub); }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
[hidden] { display: none !important; }
`;

// Put the named icon in a slot. Do nothing when the slot holds it already.
function setIcon(slot, name) {
  if (slot.dataset.icon === name) return;
  slot.dataset.icon = name;
  slot.replaceChildren(icon(name));
}

class ElevenAgentsCardSheet extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.handlers = { main: () => {}, mute: () => {}, send: () => false, close: () => {} };
    this.build();
  }

  connectedCallback() {
    this.closeButton.focus();
  }

  build() {
    const style = el("style");
    style.textContent = STYLE;
    const backdrop = el("div", "backdrop");
    backdrop.addEventListener("click", () => this.handlers.close());
    this.panel = el("div", "panel");
    this.panel.setAttribute("role", "dialog");
    this.panel.setAttribute("aria-modal", "true");

    const head = el("div", "head");
    const avatar = el("div", "avatar");
    avatar.append(icon("wave", 22));
    const text = el("div", "text");
    this.titleNode = el("div", "title");
    this.statusNode = el("div", "status");
    text.append(this.titleNode, this.statusNode);
    this.closeButton = el("button", "close");
    this.closeButton.type = "button";
    this.closeButton.append(icon("close"));
    this.closeButton.addEventListener("click", () => this.handlers.close());
    head.append(avatar, text, this.closeButton);

    this.clockBox = el("div", "clock");
    this.track = el("div", "track");
    this.fill = el("div", "fill");
    this.track.append(this.fill);
    const clockRow = el("div", "clock-row");
    this.elapsedNode = el("span");
    this.maxNode = el("span");
    clockRow.append(this.elapsedNode, this.maxNode);
    this.clockBox.append(this.track, clockRow);

    const controls = el("div", "controls");
    this.muteTile = el("button", "tile");
    this.muteTile.type = "button";
    this.muteIcon = el("span", "slot");
    this.muteLabel = el("span");
    this.muteTile.append(this.muteIcon, this.muteLabel);
    this.muteTile.addEventListener("click", () => this.handlers.mute());
    this.mainTile = el("button", "tile");
    this.mainTile.type = "button";
    this.mainIcon = el("span", "slot");
    this.mainLabel = el("span");
    this.mainTile.append(this.mainIcon, this.mainLabel);
    this.mainTile.addEventListener("click", () => this.handlers.main());
    controls.append(this.muteTile, this.mainTile);

    this.form = el("form");
    this.inputLabel = el("label", "sr");
    this.inputLabel.htmlFor = "sheet-message";
    this.input = el("input");
    this.input.id = "sheet-message";
    this.input.type = "text";
    this.sendButton = el("button", "send");
    this.sendButton.type = "submit";
    this.sendButton.append(icon("send"));
    this.form.append(this.inputLabel, this.input, this.sendButton);
    this.form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (this.handlers.send(this.input.value)) this.input.value = "";
    });

    const scroll = el("div", "scroll");
    this.actionsSection = el("div", "section");
    this.actionsLabel = el("div", "label");
    this.actionsList = el("div", "list");
    this.actionsSection.append(this.actionsLabel, this.actionsList);
    this.linesSection = el("div", "section");
    this.linesLabel = el("div", "label");
    this.linesList = el("div", "list");
    this.linesSection.append(this.linesLabel, this.linesList);
    this.emptyNode = el("div", "empty");
    scroll.append(this.actionsSection, this.linesSection, this.emptyNode);

    this.foot = el("div", "foot");
    this.silenceNode = el("span");
    this.foot.append(icon("clock", 14), this.silenceNode);

    this.panel.append(el("div", "handle"), head, this.clockBox, controls, this.form, scroll, this.foot);
    this.shadowRoot.append(style, backdrop, this.panel);
    this.addEventListener("keydown", (event) => {
      if (event.key === "Escape") this.handlers.close();
    });
  }

  // Show one view of the conversation.
  update(view) {
    const t = view.strings;
    this.panel.dataset.state = view.state;
    this.panel.setAttribute("aria-label", view.title);
    this.titleNode.textContent = view.title;
    this.statusNode.textContent = view.status;
    this.closeButton.setAttribute("aria-label", t.sheet.close);

    this.clockBox.hidden = view.clock === null;
    if (view.clock !== null) {
      this.elapsedNode.textContent = view.clock.elapsed;
      this.maxNode.textContent = view.clock.max ? t.sheet.max(view.clock.max) : "";
      this.track.hidden = view.clock.fraction === null;
      this.fill.style.width = `${Math.round((view.clock.fraction || 0) * 100)}%`;
    }

    this.muteTile.hidden = !view.open;
    this.muteTile.setAttribute("aria-pressed", String(view.muted));
    this.muteLabel.textContent = view.muted ? t.sheet.unmute : t.sheet.mute;
    setIcon(this.muteIcon, view.muted ? "micOff" : "mic");
    this.mainTile.className = `tile ${view.main}`;
    this.mainLabel.textContent = view.mainLabel;
    setIcon(this.mainIcon, MAIN_ICON[view.main]);

    this.input.disabled = !view.inputEnabled;
    this.sendButton.disabled = !view.inputEnabled;
    this.input.placeholder = t.messagePlaceholder;
    this.inputLabel.textContent = t.messageLabel;
    this.sendButton.setAttribute("aria-label", t.send);

    this.actionsLabel.textContent = t.sheet.actions;
    this.actionsSection.hidden = view.actions.length === 0;
    this.actionsList.replaceChildren(...view.actions.map((action) => {
      const row = el("div", `action ${action.tone}`);
      const dot = el("span", "dot");
      dot.append(icon(action.icon, 16));
      row.append(dot, el("span", "", action.text));
      return row;
    }));

    this.linesLabel.textContent = t.sheet.conversation;
    this.linesSection.hidden = view.lines.length === 0;
    this.linesList.replaceChildren(...view.lines.map((line) => {
      const node = el("div", `say ${line.source}`, line.source === "user" ? t.youLine(line.text) : line.text);
      if (line.cut) {
        node.classList.add("cut");
        node.append(el("span", "tag", t.interrupted));
      }
      return node;
    }));
    this.emptyNode.textContent = t.sheet.empty;
    this.emptyNode.hidden = view.actions.length > 0 || view.lines.length > 0;

    this.foot.hidden = view.silence === null;
    this.silenceNode.textContent = view.silence || "";
  }
}

if (!customElements.get("elevenagents-card-sheet")) {
  customElements.define("elevenagents-card-sheet", ElevenAgentsCardSheet);
}
