import { strings } from "./strings.js";
import { button, dirtyReporter, el, field, icon, ICON, isAdvanceable } from "./ui.js";
import { fullWebhookUrl, isHaUrl, previewOrigin, statusView } from "./webhook.js";

const PRE_TOOL_SPEECH_VALUES = ["auto", "force", "off"];

/**
 * Bind an ha-selector to hass, or fall back to a plain input when the
 * component is missing. Both paths report changes through onChange.
 *
 * `helperText` states a constraint on the value. ha-selector reserves room
 * for it under the control whether it is set or not, so it goes there
 * rather than into a line of ours below an empty reserved row.
 */
function selectorField(hass, selector, value, onChange, helperText) {
  if (customElements.get("ha-selector")) {
    const node = document.createElement("ha-selector");
    node.hass = hass;
    node.selector = selector;
    node.value = value;
    if (helperText) node.helper = helperText;
    node.addEventListener("value-changed", (event) => {
      onChange(event.detail.value);
    });
    return node;
  }
  return fallbackField(selector, value, onChange, helperText);
}

// Put a plain control and its constraint in one box, so the fallback
// reads the way ha-selector does.
function withHelper(control, helperText) {
  if (!helperText) return control;
  const box = el("div", "field");
  box.appendChild(control);
  box.appendChild(el("div", "hint", helperText));
  return box;
}

// A plain HTML substitute for ha-selector, used only when it is unavailable.
function fallbackField(selector, value, onChange, helperText) {
  if (selector.boolean) {
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(value);
    input.addEventListener("change", () => onChange(input.checked));
    return withHelper(input, helperText);
  }
  if (selector.number) {
    const input = document.createElement("input");
    input.type = "number";
    input.className = "text";
    input.min = String(selector.number.min);
    input.value = value == null ? "" : String(value);
    input.addEventListener("change", () => onChange(Number(input.value)));
    return withHelper(input, helperText);
  }
  if (selector.select) {
    const select = document.createElement("select");
    select.className = "text";
    for (const option of selector.select.options) {
      const choice = document.createElement("option");
      choice.value = option.value;
      choice.textContent = option.label;
      choice.selected = option.value === value;
      select.appendChild(choice);
    }
    select.addEventListener("change", () => onChange(select.value));
    return withHelper(select, helperText);
  }
  // Area selector fallback: area ids typed by hand, comma-separated.
  const input = document.createElement("input");
  input.type = "text";
  input.className = "text";
  input.value = Array.isArray(value) ? value.join(", ") : "";
  input.addEventListener("change", () => {
    onChange(
      input.value
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean),
    );
  });
  return withHelper(input, helperText);
}

function describeRotateOutcome(t, result) {
  if (result && result.outcome === "ok") return t.rotateSuccess;
  return t.rotateFailed.replace("{detail}", (result && result.detail) || "");
}

// One rotate button: browser confirmation, then the call, then the outcome.
function rotateRow(t, what, label, confirmText, onRotate) {
  const row = el("div", "field");
  const action = button(label, "danger", async () => {
    if (!window.confirm(confirmText)) return;
    status.textContent = "";
    status.textContent = describeRotateOutcome(t, await onRotate(what));
  });
  const status = el("div", "hint");
  row.appendChild(action);
  row.appendChild(status);
  return row;
}

/**
 * The agent's opening line, the confirmation timeout, the one writable
 * ElevenLabs behaviour setting and the webhook URL. Show the full webhook
 * URL and the last webhook check, read-only.
 *
 * The opening line lives on the ElevenAgents agent and the other two live in
 * the config entry, so a save can touch two places. `agent` is null when
 * ElevenLabs could not be reached, and the opening line is then left out.
 */
export function settingsScreen(
  hass,
  { config, agent, saveConfig, saveAgent, probeWebhook, onDirty, exposeSave },
) {
  const t = strings(hass.language);
  const reportDirty = dirtyReporter(onDirty);
  const settings = config.agent_settings || {};
  const events = config.events || {
    cooldown_minutes: 5,
    hourly_limit: 20,
    continuity_minutes: 30,
  };
  const draft = {
    first_message: agent ? agent.first_message : "",
    confirm_timeout: config.confirm_timeout,
    pre_tool_speech: settings.pre_tool_speech || "auto",
    public_url: config.public_url || config.ha_url || "",
    cooldown_minutes: events.cooldown_minutes,
    hourly_limit: events.hourly_limit,
    continuity_minutes: events.continuity_minutes,
  };
  const limits = (agent && agent.limits) || {};
  const initialLimits = {
    max_duration_seconds: limits.max_duration_seconds ?? null,
    silence_end_call_timeout:
      limits.silence_end_call_timeout === -1 ? null : limits.silence_end_call_timeout ?? null,
  };
  const limitDraft = { ...initialLimits };
  const initialMessage = draft.first_message;

  const group = el("div", "titled");

  if (agent) {
    const input = document.createElement("input");
    input.type = "text";
    input.className = "text";
    input.value = draft.first_message;
    input.addEventListener("input", () => {
      draft.first_message = input.value;
      reportDirty();
    });
    group.appendChild(field(t.firstMessageLabel, t.firstMessageHint, input));
  } else {
    group.appendChild(
      field(t.firstMessageLabel, t.firstMessageOffline, el("div", "hint", "—")),
    );
  }

  if (agent) {
    const limitInput = (key, placeholder) => {
      const input = document.createElement("input");
      input.type = "number";
      input.className = "text";
      input.min = "1";
      if (placeholder) input.placeholder = placeholder;
      if (limitDraft[key] !== null) input.value = String(limitDraft[key]);
      input.addEventListener("input", () => {
        limitDraft[key] = input.value === "" ? null : Number(input.value);
        reportDirty();
      });
      return input;
    };
    group.appendChild(
      field(t.maxDurationLabel, "", limitInput("max_duration_seconds", "")),
    );
    group.appendChild(
      field(
        t.silenceTimeoutLabel,
        "",
        limitInput("silence_end_call_timeout", t.silenceTimeoutOff),
      ),
    );
  }

  group.appendChild(
    field(
      t.confirmTimeoutLabel,
      t.confirmTimeoutDescription,
      selectorField(
        hass,
        { number: { min: 1, mode: "box" } },
        draft.confirm_timeout,
        (value) => {
          draft.confirm_timeout = value;
          reportDirty();
        },
        t.confirmTimeoutHint.replace("{timeout}", config.agent_timeout),
      ),
    ),
  );

  group.appendChild(
    field(
      t.preToolSpeechLabel,
      t.preToolSpeechHint,
      selectorField(
        hass,
        {
          select: {
            mode: "dropdown",
            options: PRE_TOOL_SPEECH_VALUES.map((value) => ({
              value,
              label: t[`preToolSpeech${value[0].toUpperCase()}${value.slice(1)}`],
            })),
          },
        },
        draft.pre_tool_speech,
        (value) => {
          draft.pre_tool_speech = value;
          reportDirty();
        },
      ),
    ),
  );

  const savedOrigin = config.public_url || config.ha_url || "";
  const initialOrigin = draft.public_url;
  const origin = document.createElement("input");
  origin.type = "url";
  origin.className = "text";
  origin.placeholder = "https://hooks.example.com";
  origin.value = draft.public_url;
  const originHint = el("div", "hint");
  const backToHa = button(t.webhookBackToHa, "plain", () => {
    origin.value = config.ha_url;
    onOrigin();
    reportDirty();
  });
  const originField = field(t.webhookOriginLabel, null, origin);
  originField.insertBefore(originHint, origin);
  originField.appendChild(backToHa);
  group.appendChild(originField);

  const fullUrl = document.createElement("input");
  fullUrl.type = "text";
  fullUrl.className = "text";
  fullUrl.readOnly = true;
  fullUrl.addEventListener("focus", () => fullUrl.select());
  const fullField = field(t.webhookFullLabel, t.webhookFullHint, fullUrl);
  const status = el("div", "check-row");
  const statusIcon = el("div", "status-icon");
  const statusText = el("div", "hint");
  status.appendChild(statusIcon);
  status.appendChild(statusText);
  const testButton = button(t.webhookTest, "plain", () => probe(origin.value));
  fullField.appendChild(status);
  fullField.appendChild(testButton);
  group.appendChild(fullField);

  // The last probe: the value it called and its status.
  let lastProbe = null;

  // Call the webhook URL that the value gives, and show the answer.
  // An unchanged saved value calls the current webhook URL.
  async function probe(value) {
    if (!probeWebhook) return;
    const current = { value, status: "checking", detail: null };
    lastProbe = current;
    onOrigin();
    let answer;
    try {
      answer = await probeWebhook(value === savedOrigin ? undefined : value);
    } catch (err) {
      answer = { status: "problem", detail: null };
    }
    if (lastProbe !== current) return;
    lastProbe = { value, status: answer.status, detail: answer.detail };
    onOrigin();
  }

  // Return the sentence for one probe status and its detail.
  function statusSentence(view, detail) {
    if (view === "checking") return t.webhookStatusChecking;
    if (view === "ok") return t.webhookStatusOk;
    if (detail === "invalid_public_url") return t.invalidPublicUrl;
    if (detail === "no_public_url") return t.noPublicUrl;
    return detail ? `${t.webhookStatusProblem} (${detail})` : t.webhookStatusProblem;
  }

  // Update the hint, the buttons, the full URL and the status from the field.
  function onOrigin() {
    draft.public_url = origin.value;
    const usesHa = isHaUrl(origin.value, config.ha_url);
    originHint.textContent = usesHa ? t.webhookOriginHintHa : t.webhookOriginHintOther;
    backToHa.style.display = !config.ha_url || usesHa ? "none" : "";
    const preview = previewOrigin(origin.value, config.ha_url);
    if (preview === null) fullUrl.value = t.webhookFullInvalid;
    else fullUrl.value = fullWebhookUrl(preview, config.webhook_path) || t.webhookFullNone;
    const view = statusView(origin.value, lastProbe);
    status.style.display = view === "none" ? "none" : "";
    testButton.style.display = view === "none" && preview && probeWebhook ? "" : "none";
    const tone = { ok: "ok", problem: "bad", checking: "skipped" }[view] || "skipped";
    statusIcon.className = `status-icon ${tone}`;
    statusIcon.replaceChildren(
      ...(view === "ok" ? [icon(ICON.check)] : view === "problem" ? [icon(ICON.alert)] : []),
    );
    statusText.textContent = statusSentence(view, lastProbe && lastProbe.detail);
  }
  origin.addEventListener("input", () => {
    onOrigin();
    reportDirty();
  });
  onOrigin();
  if (savedOrigin) probe(savedOrigin);

  const numberRow = (label, hint, key, min, max) =>
    field(
      label,
      hint,
      selectorField(
        hass,
        { number: { min: min, max: max, mode: "box" } },
        draft[key],
        (value) => {
          draft[key] = value;
          reportDirty();
        },
      ),
    );
  group.appendChild(
    numberRow(t.eventsCooldownLabel, t.eventsCooldownHint, "cooldown_minutes", 0, 1440),
  );
  group.appendChild(
    numberRow(t.eventsLimitLabel, t.eventsLimitHint, "hourly_limit", 1, 1000),
  );
  group.appendChild(
    numberRow(
      t.eventsContinuityLabel,
      t.eventsContinuityHint,
      "continuity_minutes",
      0,
      1440,
    ),
  );

  async function doSave() {
    if (agent && draft.first_message !== initialMessage) {
      const written = await saveAgent({ first_message: draft.first_message });
      if (!isAdvanceable(written)) return written;
    }
    if (agent) {
      const changed = {};
      if (limitDraft.max_duration_seconds !== initialLimits.max_duration_seconds) {
        changed.max_duration_seconds = limitDraft.max_duration_seconds;
      }
      if (
        limitDraft.silence_end_call_timeout !== initialLimits.silence_end_call_timeout
      ) {
        changed.silence_end_call_timeout = limitDraft.silence_end_call_timeout ?? -1;
      }
      if (Object.keys(changed).length > 0) {
        const written = await saveAgent(changed);
        if (!isAdvanceable(written)) return written;
      }
    }
    // Save settings before events. A failed settings save stops here.
    const settingsData = {
      confirm_timeout: draft.confirm_timeout,
      agent_settings: { pre_tool_speech: draft.pre_tool_speech },
    };
    // Send the webhook URL only when the user changed it.
    if (draft.public_url !== initialOrigin) settingsData.public_url = draft.public_url;
    const settingsSaved = await saveConfig("settings", settingsData);
    if (!isAdvanceable(settingsSaved)) return settingsSaved;
    return saveConfig("events", {
      cooldown_minutes: Number(draft.cooldown_minutes),
      hourly_limit: Number(draft.hourly_limit),
      continuity_minutes: Number(draft.continuity_minutes),
    });
  }
  if (exposeSave) exposeSave(doSave);

  return group;
}

/**
 * The two automatic-exposure switches and the areas they watch. They save
 * together, through the "auto_expose" block.
 */
export function autoExposeScreen(
  hass,
  { config, saveConfig, onDirty, exposeSave },
) {
  const t = strings(hass.language);
  const reportDirty = dirtyReporter(onDirty);
  const draft = {
    auto_expose: Boolean(config.auto_expose),
    auto_control: Boolean(config.auto_control),
    auto_expose_areas: (config.auto_expose_areas || []).slice(),
  };

  const group = el("div", "titled");

  group.appendChild(
    field(
      t.autoExposeAreasLabel,
      t.autoExposeAreasHint,
      selectorField(
        hass,
        { area: { multiple: true } },
        draft.auto_expose_areas,
        (value) => {
          draft.auto_expose_areas = value;
          reportDirty();
        },
      ),
    ),
  );

  group.appendChild(
    field(
      t.autoExposeLabel,
      null,
      selectorField(hass, { boolean: {} }, draft.auto_expose, (value) => {
        draft.auto_expose = value;
        reportDirty();
      }),
    ),
  );

  group.appendChild(
    field(
      t.autoControlLabel,
      null,
      selectorField(hass, { boolean: {} }, draft.auto_control, (value) => {
        draft.auto_control = value;
        reportDirty();
      }),
    ),
  );

  if (exposeSave) {
    exposeSave(() =>
      saveConfig("auto_expose", {
        read: draft.auto_expose,
        control: draft.auto_control,
        areas: draft.auto_expose_areas,
      }),
    );
  }

  return group;
}

/**
 * The two rotations. Each is an immediate operation behind its own browser
 * confirmation, not a setting with a draft — there is nothing to save here.
 */
export function operationsScreen(hass, { onRotate }) {
  const t = strings(hass.language);

  const operations = el("div", "titled");
  operations.appendChild(el("p", "subtitle", t.operationsDescription));
  operations.appendChild(
    rotateRow(t, "secret", t.rotateSecretLabel, t.rotateSecretConfirm, onRotate),
  );
  operations.appendChild(
    rotateRow(t, "webhook", t.rotateWebhookLabel, t.rotateWebhookConfirm, onRotate),
  );
  return operations;
}

