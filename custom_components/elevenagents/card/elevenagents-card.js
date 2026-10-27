// The ElevenAgents card. It opens a voice conversation with the agent from a dashboard.

import {
  STATES, nextState, preflightProblem, gridOptions, formFor,
  chipFor, mergeChip, chipLabel, chipIcon, chipTone, endReason, endedSummary, cardConfig,
  limitsFrom, progress, addLine, cutLastAgentLine, createHold,
  sessionStart, microphoneRefused, keepRunning, releaseOutput,
} from "./logic.js";
import { stringsFor } from "./strings.js";
import { el, icon } from "./dom.js";
import "./sheet.js";

const SDK_URL = new URL("./vendor/elevenlabs-client-1.30.0.iife.js", import.meta.url).href;
const HOLD_SLOP = 8;
const BAR_SCALES = [10, 22, 36, 26, 14, 6];
const NO_LIMITS = Object.freeze({ maxSeconds: null, silenceSeconds: null });
const MAIN_ICON = { talk: "mic", hang: "hang", cancel: "close" };
const SHEET_LABEL = { talk: "talk", hang: "hangUp", cancel: "cancel" };

let sdkPromise = null;
let activeCard = null;

// Load the SDK once, the first time a card is built.
function loadSdk() {
  if (window.ElevenLabsClient) return Promise.resolve(window.ElevenLabsClient);
  if (!sdkPromise) {
    sdkPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = SDK_URL;
      script.onload = () =>
        window.ElevenLabsClient ? resolve(window.ElevenLabsClient) : reject(new Error("SDK global missing"));
      script.onerror = () => {
        sdkPromise = null;
        reject(new Error("SDK failed to load"));
      };
      document.head.append(script);
    });
  }
  return sdkPromise;
}

const STYLE = `
:host { display: block; height: 100%; }
ha-card { box-sizing: border-box; height: 100%; overflow: hidden; display: flex; flex-direction: column; gap: 12px; padding: 12px;
  font-family: var(--ha-font-family-body, system-ui, sans-serif); color: var(--primary-text-color);
  -webkit-user-select: none; user-select: none; -webkit-touch-callout: none;
  --ea-accent: var(--primary-color, #03a9f4); --ea-tint: rgba(var(--rgb-primary-color, 3, 169, 244), 0.14);
  --ea-danger: var(--error-color, #db4437); --ea-sub: var(--secondary-text-color, #727272);
  --ea-soft: var(--secondary-background-color, #e5e5e5); --ea-line: var(--divider-color, rgba(0, 0, 0, 0.12));
  --ea-warn: var(--warning-color, #ffa600); --ea-warn-bg: color-mix(in srgb, var(--warning-color, #ffa600) 16%, transparent); }
.top { display: flex; align-items: center; gap: 12px; min-width: 0; }
.avatar { flex: 0 0 auto; width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
  background: var(--ea-tint); color: var(--ea-accent); box-shadow: 0 0 0 calc(var(--level, 0) * 8px) var(--ea-tint);
  transition: box-shadow 80ms linear; }
.card[data-state="listening"] .avatar { background: var(--ea-sub); color: var(--card-background-color, #fff); }
.card[data-state="speaking"] .avatar { background: var(--ea-accent); color: var(--text-primary-color, #fff); }
.card[data-state="ended"] .avatar, .card[data-state="connecting"] .avatar { background: var(--ea-soft); color: var(--ea-sub); }
.bars { display: none; flex: 0 0 auto; align-items: center; gap: 4px; height: 40px; }
.bars span { width: 4px; border-radius: 2px; background: var(--ea-sub); height: calc(4px + var(--level, 0) * var(--k)); }
.card[data-state="speaking"] .bars span { background: var(--ea-accent); }
.text { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.name { font-size: 15px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.status { font-size: 13px; color: var(--ea-sub); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.card[data-state="listening"] .status { color: var(--primary-text-color); font-weight: 600; }
.card[data-state="speaking"] .status { color: var(--ea-accent); font-weight: 600; }
.card.has-problem .status { color: var(--ea-warn); font-weight: 600; }
.line { display: none; font-size: 14px; line-height: 1.35; overflow: hidden; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.round { flex: 0 0 auto; width: 40px; height: 40px; border-radius: 50%; padding: 0; border: 1px solid var(--ea-line);
  background: var(--ea-soft); color: var(--primary-text-color); display: flex; align-items: center; justify-content: center; cursor: pointer; }
.round[aria-pressed="true"] { background: var(--primary-text-color); color: var(--card-background-color, #fff); }
.main { flex: 0 0 auto; width: 48px; height: 48px; border-radius: 50%; padding: 0; border: none; background: var(--ea-accent);
  color: var(--text-primary-color, #fff); display: flex; align-items: center; justify-content: center; cursor: pointer; }
.main.hang { background: var(--ea-danger); }
.main.cancel { background: var(--ea-soft); color: var(--primary-text-color); border: 1px solid var(--ea-line); }
.body { flex: 1 1 auto; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; padding: 0 2px; }
.you { font-size: 13px; color: var(--ea-sub); }
.agent { font-size: 16px; line-height: 1.4; }
.agent.hint, .agent.cut { color: var(--ea-sub); }
.agent.fresh { animation: fade 240ms ease-out; }
@keyframes fade { from { opacity: 0; } to { opacity: 1; } }
.tag { margin-left: 6px; font-size: 12px; font-weight: 600; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border-radius: 999px; font-size: 12px; font-weight: 600;
  background: var(--ea-tint); color: var(--primary-text-color); }
.chip svg { color: var(--ea-accent); }
.chip.warn { background: var(--ea-warn-bg); } .chip.warn svg { color: var(--ea-warn); }
.chip.bad { background: color-mix(in srgb, var(--error-color, #db4437) 14%, transparent); } .chip.bad svg { color: var(--ea-danger); }
.problem { display: flex; flex-direction: column; gap: 4px; border-radius: 12px; padding: 12px; background: var(--ea-warn-bg); }
.problem-title { font-size: 15px; font-weight: 600; } .problem-body { font-size: 14px; color: var(--ea-sub); }
form { display: flex; gap: 8px; align-items: center; margin: 0; }
input { flex: 1 1 auto; min-width: 0; height: 44px; box-sizing: border-box; padding: 0 14px; border-radius: 22px;
  border: 1px solid var(--ea-line); background: var(--ea-soft); color: var(--primary-text-color); font: inherit; font-size: 15px;
  -webkit-user-select: text; user-select: text; }
.send { flex: 0 0 auto; width: 44px; height: 44px; border-radius: 50%; border: none; padding: 0; background: var(--ea-accent);
  color: var(--text-primary-color, #fff); display: flex; align-items: center; justify-content: center; cursor: pointer; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
[hidden] { display: none !important; }
.card[data-form="full"] { min-height: 200px; }
.card[data-form="row"] { padding: 0 8px 0 10px; justify-content: center; }
.card[data-form="row"] .avatar { width: 36px; height: 36px; }
.card[data-form="row"] .main { width: 40px; height: 40px; }
.card[data-form="row"] .name { font-size: 14px; }
.card[data-form="row"] .status { font-size: 12px; }
.card[data-form="row"] :is(.round, .body, .problem, form) { display: none !important; }
.card[data-form="strip"] { justify-content: center; }
.card[data-form="strip"] :is(.avatar, .name) { display: none; }
.card[data-form="strip"] .bars { display: flex; }
.card[data-form="strip"] .line { display: -webkit-box; }
.card[data-form="strip"] :is(.keys, .body, .problem, form) { display: none !important; }
.card[data-form="square"] .top { flex: 1 1 auto; display: grid; grid-template-rows: auto 1fr auto; justify-items: center; align-items: center; gap: 8px; }
.card[data-form="square"] .text { display: contents; }
.card[data-form="square"] .name { grid-row: 1; justify-self: stretch; }
.card[data-form="square"] .main { grid-row: 2; width: 64px; height: 64px; box-shadow: 0 0 0 calc(8px + var(--level, 0) * 10px) var(--ea-ring, transparent); }
.card[data-form="square"] .main.hang { --ea-ring: color-mix(in srgb, var(--error-color, #db4437) 16%, transparent); }
.card[data-form="square"] .main svg { width: 28px; height: 28px; }
.card[data-form="square"] .status { grid-row: 3; max-width: 100%; }
.card[data-form="square"] :is(.avatar, .bars, .line, .round, .body, .problem, form) { display: none !important; }
`;

class ElevenAgentsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.state = STATES.READY;
    this.form = "full";
    this.conversation = null;
    this.run = 0;
    this.muted = false;
    this.keyboardOpen = false;
    this.lines = [];
    this.chips = [];
    this.actions = [];
    this.shownText = "";
    this.limits = NO_LIMITS;
    this.startedAt = null;
    this.endedAt = null;
    this.clock = null;
    this.endedReason = null;
    this.problem = null;
    this.frame = null;
    this.sheet = null;
    this.built = false;
  }

  static getConfigElement() {
    return document.createElement("elevenagents-card-editor");
  }

  static getStubConfig() {
    return { show_transcript: true };
  }

  setConfig(config) {
    this.config = cardConfig(config);
    if (this.built) this.render();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this.built) this.build();
    this.render();
  }

  getGridOptions() {
    return gridOptions();
  }

  getCardSize() {
    return 6;
  }

  get t() {
    return stringsFor(this._hass && this._hass.language);
  }

  connectedCallback() {
    if (this._hass && !this.built) this.build();
  }

  disconnectedCallback() {
    this.closeSheet();
    this.finish("user");
  }

  build() {
    this.built = true;
    const style = el("style");
    style.textContent = STYLE;
    this.root = el("ha-card", "card");

    const top = el("div", "top");
    this.avatar = el("div", "avatar");
    this.avatar.append(icon("wave"));
    this.bars = el("div", "bars");
    for (const scale of BAR_SCALES) {
      const bar = el("span");
      bar.style.setProperty("--k", `${scale}px`);
      this.bars.append(bar);
    }
    const text = el("div", "text");
    this.nameNode = el("div", "name");
    this.statusNode = el("div", "status");
    this.lineNode = el("div", "line");
    text.append(this.nameNode, this.statusNode, this.lineNode);
    this.muteButton = el("button", "round mute");
    this.muteButton.type = "button";
    this.muteButton.addEventListener("click", () => this.onMute());
    this.keysButton = el("button", "round keys");
    this.keysButton.type = "button";
    this.keysButton.append(icon("keys"));
    this.keysButton.addEventListener("click", () => {
      this.keyboardOpen = !this.keyboardOpen;
      this.render();
    });
    this.mainButton = el("button", "main");
    this.mainButton.type = "button";
    this.mainButton.addEventListener("click", () => this.onMain());
    top.append(this.avatar, this.bars, text, this.muteButton, this.keysButton, this.mainButton);

    this.body = el("div", "body");
    this.problemBox = el("div", "problem");
    this.problemTitle = el("div", "problem-title");
    this.problemBody = el("div", "problem-body");
    this.problemBox.append(this.problemTitle, this.problemBody);

    this.formNode = el("form");
    this.inputLabel = el("label", "sr");
    this.inputLabel.htmlFor = "message";
    this.input = el("input");
    this.input.id = "message";
    this.input.type = "text";
    this.sendButton = el("button", "send");
    this.sendButton.type = "submit";
    this.sendButton.append(icon("send"));
    this.formNode.append(this.inputLabel, this.input, this.sendButton);
    this.formNode.addEventListener("submit", (event) => {
      event.preventDefault();
      this.onSend();
    });

    this.root.append(top, this.body, this.problemBox, this.formNode);
    this.shadowRoot.append(style, this.root);
    this.watchSize();
    this.watchHold();
    loadSdk().catch((err) => console.warn("ElevenAgents card:", err && err.message));
  }

  // Measure the card in the full form, then choose the form.
  watchSize() {
    new ResizeObserver(() => {
      this.root.dataset.form = "full";
      const box = this.getBoundingClientRect();
      this.root.dataset.form = this.form;
      const form = formFor(box.width, box.height);
      if (form && form !== this.form) {
        this.form = form;
        this.render();
      }
    }).observe(this);
  }

  // Open the sheet on a long press that starts outside a control.
  watchHold() {
    const hold = createHold(() => this.openSheet());
    let origin = null;
    const stop = () => {
      origin = null;
      hold.cancel();
    };
    this.root.addEventListener("pointerdown", (event) => {
      if (event.button > 0 || event.target.closest("button, input, form")) return;
      origin = { x: event.clientX, y: event.clientY };
      hold.start();
    });
    this.root.addEventListener("pointermove", (event) => {
      if (origin && Math.hypot(event.clientX - origin.x, event.clientY - origin.y) > HOLD_SLOP) stop();
    });
    for (const type of ["pointerup", "pointercancel", "pointerleave"]) this.root.addEventListener(type, stop);
    this.root.addEventListener("contextmenu", (event) => {
      if (!event.target.closest("input")) event.preventDefault();
    });
  }

  isOpen() {
    return this.state === STATES.LISTENING || this.state === STATES.SPEAKING;
  }

  mainKind() {
    if (this.state === STATES.CONNECTING) return "cancel";
    return this.isOpen() ? "hang" : "talk";
  }

  mainLabel() {
    const t = this.t;
    if (this.state === STATES.CONNECTING) return t.cancel;
    if (this.isOpen()) return t.hangUp;
    if (this.state === STATES.ENDED) return t.talkAgain;
    return t.talk;
  }

  // Return the length of the current or last conversation in seconds.
  duration() {
    if (this.startedAt === null) return 0;
    return Math.floor(((this.endedAt || Date.now()) - this.startedAt) / 1000);
  }

  statusText() {
    const t = this.t;
    if (this.problem !== null) return t.problem[this.problem].title;
    if (this.state === STATES.ENDED && this.endedReason) {
      return endedSummary(this.endedReason, this.duration(), this.actions.length, t);
    }
    if (this.isOpen()) return `${t.state[this.state]} · ${progress(this.duration(), null).elapsed}`;
    return t.state[this.state];
  }

  shortStatus() {
    const t = this.t;
    if (this.problem !== null) return t.problem[this.problem].title;
    if (this.state === STATES.ENDED && this.endedReason) return t.endedShort[this.endedReason];
    return t.state[this.state];
  }

  stripLine() {
    const last = this.lines[this.lines.length - 1];
    if (!last) return this.t.readyHint;
    return last.source === "user" ? this.t.youLine(last.text) : last.text;
  }

  render() {
    if (!this.built || !this.config) return;
    const t = this.t;
    const open = this.isOpen();
    this.root.dataset.state = this.state;
    this.root.dataset.form = this.form;
    this.root.classList.toggle("has-problem", this.problem !== null);
    this.nameNode.textContent = this.config.title || t.defaultTitle;
    this.statusNode.textContent = this.form === "square" ? this.shortStatus() : this.statusText();
    this.lineNode.textContent = this.stripLine();
    this.lineNode.hidden = !this.config.show_transcript;

    const kind = this.mainKind();
    if (this.mainButton.dataset.kind !== kind) {
      this.mainButton.dataset.kind = kind;
      this.mainButton.className = kind === "talk" ? "main" : `main ${kind}`;
      this.mainButton.replaceChildren(icon(MAIN_ICON[kind]));
    }
    this.mainButton.setAttribute("aria-label", this.mainLabel());

    this.muteButton.hidden = !open;
    const muteIcon = this.muted ? "micOff" : "mic";
    if (this.muteButton.dataset.icon !== muteIcon) {
      this.muteButton.dataset.icon = muteIcon;
      this.muteButton.replaceChildren(icon(muteIcon));
    }
    this.muteButton.setAttribute("aria-pressed", String(this.muted));
    this.muteButton.setAttribute("aria-label", this.muted ? t.unmute : t.mute);
    this.keysButton.hidden = !open;
    this.keysButton.setAttribute("aria-label", t.keyboard);
    this.keysButton.setAttribute("aria-pressed", String(this.keyboardOpen));

    this.formNode.hidden = !open || !this.keyboardOpen;
    this.input.placeholder = t.messagePlaceholder;
    this.inputLabel.textContent = t.messageLabel;
    this.sendButton.setAttribute("aria-label", t.send);

    this.problemBox.hidden = this.problem === null;
    if (this.problem !== null) {
      this.problemTitle.textContent = t.problem[this.problem].title;
      this.problemBody.textContent = t.problem[this.problem].body;
    }
    this.body.hidden = !this.config.show_transcript;
    if (this.config.show_transcript) this.renderBody();
    if (this.sheet) this.sheet.update(this.sheetView());
  }

  // Show the last user line, the last agent line and the chips in the full form.
  renderBody() {
    const t = this.t;
    this.body.replaceChildren();
    const last = this.lines[this.lines.length - 1];
    if (!last) {
      if (this.state === STATES.READY || this.state === STATES.CONNECTING) {
        this.body.append(el("div", "agent hint", t.readyHint));
      }
    } else {
      const user = this.lines.filter((line) => line.source === "user").pop();
      if (user) this.body.append(el("div", "you", t.youLine(user.text)));
      if (last.source === "agent") {
        const line = el("div", "agent", last.text);
        if (this.state === STATES.ENDED) line.classList.add("hint");
        if (last.text !== this.shownText) line.classList.add("fresh");
        if (last.cut) {
          line.classList.add("cut");
          line.append(el("span", "tag", t.interrupted));
        }
        this.shownText = last.text;
        this.body.append(line);
      }
    }
    const chips = this.state === STATES.ENDED ? this.actions : this.chips;
    if (chips.length) {
      const row = el("div", "chips");
      for (const chip of chips) row.append(this.chipNode(chip));
      this.body.append(row);
    }
  }

  chipNode(chip) {
    const node = el("span", `chip ${chipTone(chip)}`);
    node.append(icon(chipIcon(chip), 14), document.createTextNode(chipLabel(chip, this.t)));
    return node;
  }

  // Build the view the sheet shows.
  sheetView() {
    const t = this.t;
    const kind = this.mainKind();
    return {
      strings: t,
      title: this.config.title || t.defaultTitle,
      state: this.state,
      status: this.statusText(),
      clock: this.startedAt === null ? null : progress(this.duration(), this.limits.maxSeconds),
      silence: this.limits.silenceSeconds ? t.sheet.silence(this.limits.silenceSeconds) : null,
      open: this.isOpen(),
      muted: this.muted,
      main: kind,
      mainLabel: t.sheet[SHEET_LABEL[kind]],
      inputEnabled: this.isOpen(),
      actions: this.actions.map((chip) => ({ icon: chipIcon(chip), tone: chipTone(chip), text: chipLabel(chip, t) })),
      lines: this.lines,
    };
  }

  openSheet() {
    if (this.sheet || !this.config) return;
    const sheet = document.createElement("elevenagents-card-sheet");
    sheet.handlers = {
      main: () => this.onMain(),
      mute: () => this.onMute(),
      send: (text) => this.sendText(text),
      close: () => this.closeSheet(),
    };
    sheet.update(this.sheetView());
    document.body.append(sheet);
    this.sheet = sheet;
  }

  closeSheet() {
    if (!this.sheet) return;
    this.sheet.remove();
    this.sheet = null;
  }

  setState(event) {
    this.state = nextState(this.state, event);
    if (this.isOpen() && this.clock === null) this.clock = setInterval(() => this.render(), 1000);
    if (!this.isOpen() && this.clock !== null) {
      clearInterval(this.clock);
      this.clock = null;
    }
    this.render();
  }

  onMain() {
    if (this.state === STATES.READY || this.state === STATES.ENDED) this.start();
    else if (this.state === STATES.CONNECTING) this.cancel();
    else this.finish("user");
  }

  async start() {
    this.problem = preflightProblem({
      isSecureContext: window.isSecureContext,
      hasMediaDevices: Boolean(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
    });
    if (this.problem) {
      this.render();
      return;
    }
    const previous = activeCard && activeCard !== this ? activeCard.finish("user") : null;
    activeCard = this;
    const run = ++this.run;
    this.lines = [];
    this.chips = [];
    this.actions = [];
    this.shownText = "";
    this.limits = NO_LIMITS;
    this.startedAt = null;
    this.endedAt = null;
    this.endedReason = null;
    this.muted = false;
    this.keyboardOpen = false;
    this.setState({ type: "start" });
    // Wait until the other card ends its session.
    await previous;
    if (run !== this.run) return;
    try {
      const result = await this._hass.callWS({ type: "elevenagents/conversation_url" });
      if (run !== this.run) return;
      this.limits = limitsFrom(result);
      const sdk = await loadSdk();
      if (run !== this.run) return;
      const conversation = await sdk.Conversation.startSession(this.sessionOptions(result.signed_url, run));
      if (run !== this.run || this.state !== STATES.CONNECTING) {
        await conversation.endSession().catch(() => {});
        return;
      }
      this.conversation = conversation;
      keepRunning(conversation.input && conversation.input.context);
      this.startedAt = Date.now();
      this.setState({ type: "connected" });
      this.tick();
    } catch (err) {
      if (run !== this.run) return;
      if (microphoneRefused(err)) {
        console.warn("ElevenAgents card: microphone refused", err.name);
        this.problem = "microphone";
      } else {
        console.warn("ElevenAgents card: the conversation could not start", err && err.message);
        this.problem = "start";
      }
      this.setState({ type: "cancel" });
    }
  }

  // Build the SDK callbacks. A callback of an earlier run does nothing.
  sessionOptions(signedUrl, run) {
    const current = (fn) => (...args) => {
      if (run === this.run) fn(...args);
    };
    return {
      ...sessionStart(signedUrl),
      onModeChange: current(({ mode }) => {
        this.setState({ type: "mode", mode });
      }),
      onMessage: current(({ source, message }) => {
        this.lines = addLine(this.lines, source, message);
        if (source === "user") this.chips = [];
        this.render();
      }),
      onInterruption: current(() => {
        this.lines = cutLastAgentLine(this.lines);
        this.render();
      }),
      onAgentToolResponse: current((event) => {
        const chip = chipFor(event);
        this.chips = mergeChip(this.chips, chip);
        this.actions = mergeChip(this.actions, chip);
        this.render();
      }),
      onDisconnect: current((details) => {
        releaseOutput(this.conversation);
        this.endedReason = this.endedReason || endReason(details);
        this.endedAt = Date.now();
        this.conversation = null;
        if (activeCard === this) activeCard = null;
        this.setState({ type: "ended" });
      }),
      onError: (message) => console.warn("ElevenAgents card:", message),
    };
  }

  tick() {
    cancelAnimationFrame(this.frame);
    const step = () => {
      if (!this.conversation) {
        this.root.style.setProperty("--level", "0");
        return;
      }
      const level = this.state === STATES.SPEAKING ? this.conversation.getOutputVolume() : this.conversation.getInputVolume();
      this.root.style.setProperty("--level", String(Math.min(1, level * 2)));
      this.frame = requestAnimationFrame(step);
    };
    this.frame = requestAnimationFrame(step);
  }

  cancel() {
    this.run += 1;
    if (activeCard === this) activeCard = null;
    this.setState({ type: "cancel" });
  }

  finish(reason) {
    if (this.state === STATES.CONNECTING) {
      this.cancel();
      return;
    }
    if (!this.conversation) return;
    this.endedReason = reason;
    const conversation = this.conversation;
    const run = this.run;
    this.conversation = null;
    return conversation
      .endSession()
      .catch((err) => {
        console.warn("ElevenAgents card:", err && err.message);
        if (run !== this.run) return;
        this.endedAt = Date.now();
        if (activeCard === this) activeCard = null;
        this.setState({ type: "ended" });
      })
      .finally(() => releaseOutput(conversation));
  }

  onMute() {
    if (!this.conversation) return;
    this.muted = !this.muted;
    this.conversation.setMicMuted(this.muted);
    this.render();
  }

  // Send a typed message. Return true when it was sent.
  sendText(text) {
    const value = text.trim();
    if (!value || !this.conversation) return false;
    this.conversation.sendUserMessage(value);
    this.lines = addLine(this.lines, "user", value);
    this.chips = [];
    this.render();
    return true;
  }

  onSend() {
    if (this.sendText(this.input.value)) this.input.value = "";
  }
}

class ElevenAgentsCardEditor extends HTMLElement {
  setConfig(config) {
    this.config = config;
    this.render();
  }

  set hass(hass) {
    this._hass = hass;
    this.render();
  }

  render() {
    if (!this._hass || !this.config) return;
    const t = stringsFor(this._hass.language).editor;
    if (!this.form) {
      this.form = document.createElement("ha-form");
      this.form.addEventListener("value-changed", (event) => {
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: event.detail.value }, bubbles: true, composed: true }));
      });
      this.append(this.form);
    }
    this.form.hass = this._hass;
    this.form.data = { show_transcript: true, ...this.config };
    this.form.schema = [
      { name: "title", selector: { text: {} } },
      { name: "show_transcript", selector: { boolean: {} } },
    ];
    this.form.computeLabel = (schema) => t[schema.name];
  }
}

if (!customElements.get("elevenagents-card")) {
  customElements.define("elevenagents-card", ElevenAgentsCard);
}
if (!customElements.get("elevenagents-card-editor")) {
  customElements.define("elevenagents-card-editor", ElevenAgentsCardEditor);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "elevenagents-card")) {
  window.customCards.push({
    type: "elevenagents-card",
    name: "ElevenAgents",
    description: "Talk to the ElevenLabs agent from a dashboard.",
  });
}
