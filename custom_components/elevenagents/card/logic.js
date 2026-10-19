// Pure logic of the ElevenAgents card. It uses no DOM and no SDK.

export const STATES = Object.freeze({
  READY: "ready",
  CONNECTING: "connecting",
  LISTENING: "listening",
  SPEAKING: "speaking",
  ENDED: "ended",
});

const OPEN = new Set([STATES.LISTENING, STATES.SPEAKING]);

// Return the card config with its defaults.
export function cardConfig(config) {
  return {
    title: config.title || "",
    show_transcript: config.show_transcript !== false,
    ringtone: config.ringtone !== false,
  };
}

// Return the state after one event.
export function nextState(state, event) {
  switch (event.type) {
    case "start":
      return state === STATES.READY || state === STATES.ENDED ? STATES.CONNECTING : state;
    case "cancel":
      return state === STATES.CONNECTING ? STATES.READY : state;
    case "connected":
      return state === STATES.CONNECTING ? STATES.LISTENING : state;
    case "mode":
      if (!OPEN.has(state)) return state;
      return event.mode === "speaking" ? STATES.SPEAKING : STATES.LISTENING;
    case "ended":
      return STATES.ENDED;
    default:
      return state;
  }
}

// Return the problem that stops a start before any cost, or null.
export function preflightProblem(env) {
  if (!env.isSecureContext) return "https";
  if (!env.hasMediaDevices) return "microphone";
  return null;
}

// Return the sections-view grid size. Rows are 56 px high.
export function gridOptions() {
  return { columns: 12, rows: 5, min_columns: 4, min_rows: 1 };
}

export const FORM_LIMITS = Object.freeze({ rowMaxHeight: 80, squareMaxWidth: 240, stripMaxHeight: 200 });

// Return the form of the card for its measured size in CSS pixels, or null for no size.
export function formFor(width, height) {
  if (!width || !height) return null;
  if (height < FORM_LIMITS.rowMaxHeight) return "row";
  if (width < FORM_LIMITS.squareMaxWidth) return "square";
  if (height < FORM_LIMITS.stripMaxHeight) return "strip";
  return "full";
}

// Return a duration in seconds as minutes and seconds.
export function formatClock(seconds) {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

// Return the elapsed time, the maximum and the used fraction. Without a positive maximum, only the elapsed time.
export function progress(elapsedSeconds, maxSeconds) {
  const elapsed = formatClock(elapsedSeconds);
  if (typeof maxSeconds !== "number" || maxSeconds <= 0) return { elapsed, max: null, fraction: null };
  return { elapsed, max: formatClock(maxSeconds), fraction: Math.min(1, elapsedSeconds / maxSeconds) };
}

// Return the two limits of a token answer. A limit that is not a positive number is null.
export function limitsFrom(result) {
  const limits = (result && result.limits) || {};
  const positive = (value) => (typeof value === "number" && value > 0 ? value : null);
  return {
    maxSeconds: positive(limits.max_duration_seconds),
    silenceSeconds: positive(limits.silence_end_call_timeout),
  };
}

// Add one line of the conversation. A line equal to the last line of the same source is ignored.
export function addLine(lines, source, text) {
  const who = source === "user" ? "user" : "agent";
  const last = lines[lines.length - 1];
  if (last && last.source === who && last.text === text) return lines;
  return [...lines, { source: who, text, cut: false }];
}

// Mark the last agent line as cut.
export function cutLastAgentLine(lines) {
  const index = lines.map((line) => line.source).lastIndexOf("agent");
  if (index < 0) return lines;
  return lines.map((line, i) => (i === index ? { ...line, cut: true } : line));
}

export const HOLD_MS = 500;

const BROWSER_TIMERS = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (id) => clearTimeout(id),
};

// Detect a long press. start() arms the timer, cancel() stops it.
export function createHold(onHold, timers = BROWSER_TIMERS) {
  let timer = null;
  const cancel = () => {
    if (timer !== null) timers.clear(timer);
    timer = null;
  };
  return {
    start() {
      cancel();
      timer = timers.set(() => {
        timer = null;
        onHold();
      }, HOLD_MS);
    },
    cancel,
  };
}

function parse(raw) {
  if (typeof raw !== "string") return null;
  try {
    const value = JSON.parse(raw);
    return value && typeof value === "object" ? value : null;
  } catch {
    return null;
  }
}

// Build the chip for one tool answer. The chip never carries a confirmation code.
export function chipFor(event) {
  const id = event.tool_call_id ?? null;
  const data = parse(event.full_tool_result);
  if (data && Array.isArray(data.results)) {
    const names = data.results.filter((r) => !r.error).map((r) => r.name || r.entity_id);
    const target = names.length <= 2 ? names.join(", ") : "";
    return { id, kind: "read", target, count: names.length, outcome: "read", error: null, domain: null };
  }
  if (data && typeof data.outcome === "string") {
    return {
      id,
      kind: "write",
      target: String(data.name || data.entity_id || ""),
      outcome: data.outcome,
      error: typeof data.error === "string" ? data.error : null,
      domain: typeof data.entity_id === "string" && data.entity_id.includes(".") ? data.entity_id.split(".")[0] : null,
    };
  }
  return { id, kind: "generic", target: "", outcome: event.is_error ? "error" : "done", error: null, domain: null };
}

// Add a chip. A chip with the same id replaces the earlier one, unless it is only generic.
export function mergeChip(chips, chip) {
  if (chip.id === null) return [...chips, chip];
  const index = chips.findIndex((c) => c.id === chip.id);
  if (index < 0) return [...chips, chip];
  if (chip.kind === "generic" && chips[index].kind !== "generic") return chips;
  return chips.map((c, i) => (i === index ? chip : c));
}

// Return the text of a chip.
export function chipLabel(chip, t) {
  if (chip.kind === "read" && (chip.count > 2 || !chip.target)) return t.readMany(chip.count);
  const target = chip.kind === "generic" ? t.usingHouse : chip.target;
  const outcome = t.outcome[chip.outcome] || chip.outcome;
  const suffix = chip.error ? ` (${chip.error})` : "";
  return `${target} · ${outcome}${suffix}`;
}

// Return the icon name of a chip.
export function chipIcon(chip) {
  if (chip.kind === "read") return "search";
  if (chip.domain === "lock") return "lock";
  if (chip.domain === "light") return "bulb";
  return "check";
}

// Return the tone of a chip.
export function chipTone(chip) {
  if (chip.outcome === "confirmation_required" || chip.outcome === "unconfirmed") return "warn";
  if (chip.outcome === "failed" || chip.outcome === "error") return "bad";
  return "good";
}

// Return why the conversation ended.
export function endReason(details) {
  const reason = details && details.reason;
  return reason === "user" || reason === "agent" ? reason : "error";
}

// Return the summary of an ended conversation.
export function endedSummary(reason, seconds, actionCount, t) {
  return [t.endedShort[reason] || t.endedShort.error, formatClock(seconds), t.actions(actionCount)].join(" · ");
}
