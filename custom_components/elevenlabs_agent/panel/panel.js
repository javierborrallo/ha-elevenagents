import { strings } from "./strings.js";
import { entityTree } from "./entities.js";
import { actionScreen } from "./actions.js";
import { autoExposeScreen, operationsScreen, settingsScreen } from "./settings.js";
import { promptScreen } from "./prompt.js";
import { BLOCK_MARKER, RECOMMENDED_BLOCK } from "./block.js";
import {
  button,
  CSS,
  describeSaveOutcome,
  el,
  fillPanelHeight,
  ICON,
  icon,
  isAdvanceable,
  navCard,
  openDialog,
  setEnabled,
  statusCard,
  titled,
  toolbar,
} from "./ui.js";

const WS = {
  configGet: "elevenlabs_agent/config/get",
  configSave: "elevenlabs_agent/config/save",
  agentGet: "elevenlabs_agent/agent/get",
  agentSave: "elevenlabs_agent/agent/save",
  rotate: "elevenlabs_agent/rotate",
  check: "elevenlabs_agent/check",
};

// The icon and the disc colour of each check status.
const STATUS_ICON = { ok: ICON.check, problem: ICON.alert, skipped: ICON.minusCircle };
const STATUS_CLASS = { ok: "ok", problem: "bad", skipped: "skipped" };

// The name the toolbar shows. It names the integration, not the entry.
const PANEL_TITLE = "ElevenLabs Agent";

// The six wizard steps, in the fixed order the spec sets.
const WIZARD_STEPS = [
  "settings",
  "prompt",
  "read",
  "control",
  "actions",
  "exposure",
];

// Return the entity ids the check found missing on the agent.
function unsyncedFrom(result) {
  for (const item of result.checks) {
    if (item.check === "tools_current" && item.detail && item.detail.missing) {
      return item.detail.missing.slice();
    }
  }
  return [];
}

class ElevenLabsAgentPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  connectedCallback() {
    fillPanelHeight(this);
  }

  set hass(hass) {
    this._hass = hass;
    this._load();
  }

  set narrow(value) {}

  set route(value) {}

  set panel(value) {}

  // The entry this panel edits. It arrives in the query string when the
  // integration page opens the panel, and from config/get when the link
  // the config flow leaves behind opens it instead.
  _callWS(type, extra) {
    const message = { type, ...(extra || {}) };
    if (this._entryId) message.entry_id = this._entryId;
    return this._hass.callWS(message);
  }

  // Read the stored configuration, and the agent behind it. The agent call
  // reaches ElevenLabs and may fail; a failure is a state the page shows,
  // not an error that stops it.
  async _fetch() {
    this._config = await this._callWS(WS.configGet);
    this._entryId = this._config.entry_id;
    try {
      this._agent = await this._callWS(WS.agentGet);
    } catch (err) {
      this._agent = null;
    }
  }

  async _load() {
    if (this._loaded || !this._hass) return;
    this._loaded = true;
    this._t = strings(this._hass.language);
    this._entryId = new URLSearchParams(window.location.search).get(
      "config_entry",
    );
    try {
      await this._fetch();
    } catch (err) {
      this._loadError = (err && err.message) || String(err);
      this._paint();
      return;
    }
    // The empty state decides the face. Read once, at open, and never again:
    // reaching "configured" mid-wizard must not bounce the user out.
    this._mode = this._config.configured ? "sections" : "wizard";
    if (this._mode === "wizard") {
      this._wizardStep = 0;
      this._draft = {
        read: this._config.read_entity_ids.slice(),
        control: this._config.control_entity_ids.slice(),
        exceptions: { ...this._config.exception_entity_ids },
      };
    }
    this._paint();
  }

  async _reload() {
    await this._fetch();
    this._paint();
  }

  _paint() {
    const root = this.shadowRoot;
    root.textContent = "";
    const style = document.createElement("style");
    style.textContent = CSS;
    root.appendChild(style);

    if (this._loadError) {
      root.appendChild(el("div", "empty", this._loadError));
      return;
    }
    if (!this._config) {
      root.appendChild(el("div", "empty", this._t ? this._t.loading : ""));
      return;
    }
    const screen = el("div", "screen");
    screen.appendChild(
      toolbar(PANEL_TITLE, this._t.back, () => window.history.back()),
    );
    screen.appendChild(
      this._mode === "wizard" ? this._renderWizard() : this._renderSections(),
    );
    root.appendChild(screen);
  }

  // ---- Saving ----

  _saveConfig(block, data) {
    return this._callWS(WS.configSave, { block, data });
  }

  _saveAgent(payload) {
    return this._callWS(WS.agentSave, payload);
  }

  // ---- The configured page ----

  _renderSections() {
    const t = this._t;
    const page = el("div", "page");
    const stack = el("div", "stack");

    const online = this._agent !== null;
    stack.appendChild(
      statusCard(
        online,
        online ? t.statusConnected : t.statusDisconnected,
        online ? this._agent.name : t.statusDisconnectedDetail,
        () => this._openCheck(),
      ),
    );

    stack.appendChild(
      navCard(t.sectionAgent, [
        {
          iconPath: ICON.text,
          headline: t.navPrompt,
          supporting: t.navPromptHint,
          onClick: () => this._openPrompt(),
        },
        {
          iconPath: ICON.tune,
          headline: t.navSettings,
          supporting: t.navSettingsHint,
          onClick: () => this._openSettings(),
        },
        {
          iconPath: ICON.rotate,
          headline: t.navRotations,
          supporting: t.navRotationsHint,
          onClick: () => this._openRotations(),
        },
      ]),
    );

    const entityItems = [
      {
        iconPath: ICON.eye,
        headline: t.navRead,
        supporting: t.navReadHint,
        onClick: () => this._openEntities("read"),
      },
      {
        iconPath: ICON.toggle,
        headline: t.navControl,
        supporting: t.navControlHint,
        onClick: () => this._openEntities("control"),
      },
      {
        iconPath: ICON.minusCircle,
        headline: t.navExceptions,
        supporting: t.navExceptionsHint,
        onClick: () => this._openExceptions(),
      },
      {
        iconPath: ICON.shapes,
        headline: t.navAutoExpose,
        supporting: t.navAutoExposeHint,
        onClick: () => this._openAutoExpose(),
      },
    ];
    if (this._config.missing.length > 0) {
      entityItems.push({
        iconPath: ICON.alert,
        headline: t.navMissing,
        supporting: t.navMissingHint,
        onClick: () => this._openMissing(),
      });
    }
    stack.appendChild(navCard(t.sectionEntities, entityItems));

    page.appendChild(stack);
    return page;
  }

  // ---- Dialogs ----

  _openEntities(kind) {
    const t = this._t;
    let ids = this._config[`${kind}_entity_ids`].slice();
    openDialog(this.shadowRoot, {
      t,
      title: kind === "read" ? t.wizardStepRead : t.wizardStepControl,
      subtitle:
        kind === "read" ? t.wizardReadSubtitle : t.wizardControlSubtitle,
      build: () =>
        entityTree(this._hass, {
          selected: ids,
          unregistered: this._config.unregistered_entity_ids,
          unsynced: this._config.unsynced_entity_ids,
          onChange: (next) => {
            ids = next;
          },
        }),
      onSave: async () => {
        const result = await this._saveConfig("entities", {
          read: kind === "read" ? ids : this._config.read_entity_ids,
          control: kind === "control" ? ids : this._config.control_entity_ids,
        });
        if (isAdvanceable(result)) await this._reload();
        return result;
      },
    });
  }

  _openExceptions() {
    const t = this._t;
    let exceptions = { ...this._config.exception_entity_ids };
    openDialog(this.shadowRoot, {
      t,
      title: t.wizardStepActions,
      subtitle: t.wizardActionsSubtitle,
      build: () =>
        actionScreen(this._hass, {
          controlled: this._config.control_entity_ids,
          exceptions,
          availableActions: this._config.available_actions,
          onChange: (next) => {
            exceptions = next;
          },
        }),
      onSave: async () => {
        const result = await this._saveConfig("exceptions", exceptions);
        if (isAdvanceable(result)) await this._reload();
        return result;
      },
    });
  }

  _openSettings() {
    const t = this._t;
    let commit = null;
    openDialog(this.shadowRoot, {
      t,
      title: t.settingsHeading,
      subtitle: t.settingsSubtitle,
      build: () =>
        settingsScreen(this._hass, {
          config: this._config,
          agent: this._agent,
          saveConfig: (block, data) => this._saveConfig(block, data),
          saveAgent: (payload) => this._saveAgent(payload),
          exposeSave: (save) => {
            commit = save;
          },
        }),
      onSave: async () => {
        const result = await commit();
        if (isAdvanceable(result)) await this._reload();
        return result;
      },
    });
  }

  _openCheck() {
    const t = this._t;
    openDialog(this.shadowRoot, {
      t,
      title: t.checkTitle,
      build: () => {
        const host = el("div", "check-list");
        host.appendChild(el("p", null, t.checkRunning));
        this._callWS(WS.check)
          .then((result) => {
            this._config.unsynced_entity_ids = unsyncedFrom(result);
            this._paintCheck(host, result);
          })
          .catch((err) => {
            host.textContent = t.checkFailed.replace(
              "{detail}",
              (err && err.message) || "",
            );
          });
        return host;
      },
    });
  }

  // Replace the dialog body with one row for each check.
  _paintCheck(host, result) {
    const t = this._t;
    host.textContent = "";
    host.appendChild(
      el("p", null, result.ok ? t.checkAllOk : t.checkProblems),
    );
    for (const item of result.checks) {
      const row = el("div", "check-row");
      const disc = el("div", `status-icon ${STATUS_CLASS[item.status]}`);
      disc.appendChild(icon(STATUS_ICON[item.status]));
      row.appendChild(disc);
      const text = el("div", "status-text");
      text.appendChild(el("strong", null, t[`check_${item.check}`] || item.check));
      const detail = this._checkDetail(item);
      if (detail) text.appendChild(el("small", null, detail));
      row.appendChild(text);
      host.appendChild(row);
    }
  }

  // Return the sentence under one check, or an empty string.
  _checkDetail(item) {
    const t = this._t;
    if (item.status === "skipped") return t.checkSkipped;
    if (item.status === "ok") return "";
    const detail = item.detail;
    if (item.check === "key" && detail === "refused") return t.checkKeyRefused;
    if (item.check === "protected") return t.checkProtectedDetail;
    if (item.check === "loaded") return t.checkLoadedDetail.replace("{detail}", detail);
    if (item.check === "timeouts") {
      return t.checkTimeoutsDetail
        .replace("{confirm}", String(detail.confirm_timeout))
        .replace("{agent}", String(detail.agent_timeout));
    }
    if (item.check === "tools_current" && typeof detail === "object") {
      return this._toolsDetail(detail);
    }
    if (Array.isArray(detail)) return detail.join(", ");
    return detail ? String(detail) : "";
  }

  // Return the sentence under tools_current: the entities first, then the tools.
  _toolsDetail(detail) {
    const t = this._t;
    const parts = [];
    if (detail.missing.length > 0) {
      parts.push(t.checkToolsMissing.replace("{list}", this._names(detail.missing)));
    }
    if (detail.extra.length > 0) {
      parts.push(t.checkToolsExtra.replace("{list}", this._names(detail.extra)));
    }
    if (detail.unreadable.length > 0) {
      parts.push(t.checkToolsUnreadable.replace("{list}", detail.unreadable.join(", ")));
    }
    if (parts.length === 0) {
      parts.push(t.checkToolsOther.replace("{list}", detail.tools.join(", ")));
    }
    return parts.join(" ");
  }

  // Return the friendly name of each entity id, or the id when no state exists.
  _names(ids) {
    return ids
      .map((id) => {
        const state = this._hass.states[id];
        if (state && state.attributes.friendly_name) return state.attributes.friendly_name;
        return id;
      })
      .join(", ");
  }

  _openMissing() {
    const t = this._t;
    const forget = new Set();
    openDialog(this.shadowRoot, {
      t,
      title: t.navMissing,
      subtitle: t.missingSubtitle,
      saveLabel: t.missingRemove,
      saveClass: "danger",
      saveDisabled: true,
      build: (_dialog, allowSave) => {
        const list = el("div", "tree-list");
        for (const item of this._config.missing) {
          const row = el("div", "row");
          const box = document.createElement("input");
          box.type = "checkbox";
          box.addEventListener("change", () => {
            if (box.checked) forget.add(item.key);
            else forget.delete(item.key);
            allowSave(forget.size > 0);
          });
          row.appendChild(box);
          const name = el("div", "name");
          name.appendChild(el("span", null, item.entity_id || t.missingUnknown));
          const where = [];
          if (item.read) where.push(t.missingRead);
          if (item.control) where.push(t.missingControl);
          if (item.restricted) where.push(t.missingRestricted);
          name.appendChild(el("span", "eid", where.join(" · ")));
          row.appendChild(name);
          list.appendChild(row);
        }
        return list;
      },
      onSave: async () => {
        const result = await this._saveConfig("forget", { keys: [...forget] });
        if (isAdvanceable(result)) await this._reload();
        return result;
      },
    });
  }

  _openAutoExpose() {
    const t = this._t;
    let commit = null;
    openDialog(this.shadowRoot, {
      t,
      title: t.exposureHeading,
      subtitle: t.exposureSubtitle,
      build: () =>
        autoExposeScreen(this._hass, {
          config: this._config,
          saveConfig: (block, data) => this._saveConfig(block, data),
          exposeSave: (save) => {
            commit = save;
          },
        }),
      onSave: async () => {
        const result = await commit();
        if (isAdvanceable(result)) await this._reload();
        return result;
      },
    });
  }

  _openRotations() {
    const t = this._t;
    openDialog(this.shadowRoot, {
      t,
      title: t.operationsHeading,
      build: () =>
        operationsScreen(this._hass, {
          onRotate: (what) => this._callWS(WS.rotate, { what }),
        }),
    });
  }

  _openPrompt() {
    const t = this._t;
    if (this._agent === null) {
      openDialog(this.shadowRoot, {
        t,
        title: t.promptHeading,
        build: () => el("div", "empty", t.statusDisconnectedDetail),
      });
      return;
    }
    let commit = null;
    openDialog(this.shadowRoot, {
      t,
      title: t.promptHeading,
      subtitle: t.promptHint,
      build: () =>
        promptScreen(this._hass, {
          current: this._agent.prompt,
          block: RECOMMENDED_BLOCK,
          marker: BLOCK_MARKER,
          onSave: (text) => this._saveAgent({ prompt: text }),
          exposeSave: (save) => {
            commit = save;
          },
        }),
      onSave: async () => {
        const result = await commit();
        if (isAdvanceable(result)) await this._reload();
        return result;
      },
    });
  }

  // ---- The first-run wizard ----

  async _advanceWizard() {
    await this._fetch();
    if (this._wizardStep === WIZARD_STEPS.length - 1) {
      this._mode = "sections";
    } else {
      this._wizardStep += 1;
    }
    this._paint();
  }

  _renderWizard() {
    const t = this._t;
    const index = this._wizardStep;
    const key = WIZARD_STEPS[index];

    const root = el("div", "wizard");
    root.appendChild(
      el(
        "div",
        "subtitle",
        t.wizardProgress
          .replace("{step}", String(index + 1))
          .replace("{total}", String(WIZARD_STEPS.length)),
      ),
    );

    const body = el("div", "wizard-body");
    root.appendChild(body);

    const foot = el("div", "wizard-foot");
    const outcome = el("div", "outcome");
    foot.appendChild(outcome);

    const back = button(t.back, "plain", () => {
      this._wizardStep -= 1;
      this._paint();
    });
    setEnabled(back, index > 0);
    foot.appendChild(back);

    if (key === "actions") {
      foot.appendChild(button(t.skip, "plain", () => this._advanceWizard()));
    }

    const next = button(t.next, "primary", async () => {
      outcome.textContent = "";
      setEnabled(next, false);
      const result = await commit();
      setEnabled(next, true);
      if (isAdvanceable(result)) {
        await this._advanceWizard();
        return;
      }
      outcome.textContent = describeSaveOutcome(t, result);
    });
    foot.appendChild(next);
    root.appendChild(foot);

    const commit = this._buildWizardStep(key, body);
    return root;
  }

  // Build one step's content into `body` and return the function its Next
  // button calls. The wizard shows exactly one button that moves forward,
  // and pressing it saves.
  _buildWizardStep(key, body) {
    const t = this._t;
    const hass = this._hass;

    if (key === "read" || key === "control") {
      body.appendChild(
        titled(
          key === "read" ? t.wizardStepRead : t.wizardStepControl,
          key === "read" ? t.wizardReadSubtitle : t.wizardControlSubtitle,
          entityTree(hass, {
            selected: this._draft[key],
            unregistered: this._config.unregistered_entity_ids,
            unsynced: this._config.unsynced_entity_ids,
            onChange: (ids) => {
              this._draft[key] = ids;
            },
          }),
        ),
      );
      return () =>
        this._saveConfig("entities", {
          read: this._draft.read,
          control: this._draft.control,
        });
    }

    if (key === "actions") {
      body.appendChild(
        titled(
          t.wizardStepActions,
          t.wizardActionsSubtitle,
          actionScreen(hass, {
            controlled: this._draft.control,
            exceptions: this._draft.exceptions,
            availableActions: this._config.available_actions,
            onChange: (exceptions) => {
              this._draft.exceptions = exceptions;
            },
          }),
        ),
      );
      return () => this._saveConfig("exceptions", this._draft.exceptions);
    }

    if (key === "prompt") {
      if (this._agent === null) {
        body.appendChild(
          titled(
            t.promptHeading,
            t.statusDisconnectedDetail,
            el("div", "empty", t.statusDisconnectedDetail),
          ),
        );
        return () => Promise.resolve({ outcome: "ok", detail: null });
      }
      let commit = null;
      body.appendChild(
        titled(
          t.promptHeading,
          t.promptHint,
          promptScreen(hass, {
            current: this._agent.prompt,
            block: RECOMMENDED_BLOCK,
            marker: BLOCK_MARKER,
            onSave: (text) => this._saveAgent({ prompt: text }),
            exposeSave: (save) => {
              commit = save;
            },
          }),
        ),
      );
      return () => commit();
    }

    // key === "settings" or key === "exposure"
    let commit = null;
    const screen =
      key === "settings"
        ? settingsScreen(hass, {
            config: this._config,
            agent: this._agent,
            saveConfig: (block, data) => this._saveConfig(block, data),
            saveAgent: (payload) => this._saveAgent(payload),
            exposeSave: (save) => {
              commit = save;
            },
          })
        : autoExposeScreen(hass, {
            config: this._config,
            saveConfig: (block, data) => this._saveConfig(block, data),
            exposeSave: (save) => {
              commit = save;
            },
          });
    body.appendChild(
      titled(
        key === "settings" ? t.settingsHeading : t.exposureHeading,
        key === "settings" ? t.settingsSubtitle : t.exposureSubtitle,
        screen,
      ),
    );
    return () => commit();
  }
}

// Home Assistant loads the panel module again whenever the panel host is
// reconnected or the registered module URL changes, and every deploy
// changes that URL. The registry keeps the name from the first load, and
// defining it twice throws before the module finishes. Keep the first.
if (!customElements.get("elevenlabs-agent-panel")) {
  customElements.define("elevenlabs-agent-panel", ElevenLabsAgentPanel);
}
