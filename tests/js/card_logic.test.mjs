import { test } from "node:test";
import assert from "node:assert/strict";
import {
  STATES, nextState, preflightProblem, gridOptions, formFor, FORM_LIMITS,
  formatClock, progress, limitsFrom, addLine, cutLastAgentLine, HOLD_MS, createHold,
  chipFor, mergeChip, chipLabel, chipIcon, chipTone, endReason, endedSummary, cardConfig,
  sessionStart, microphoneRefused, keepRunning, releaseOutput,
} from "../../custom_components/elevenagents/card/logic.js";

test("a tap on Talk starts connecting from ready or ended", () => {
  assert.equal(nextState(STATES.READY, { type: "start" }), STATES.CONNECTING);
  assert.equal(nextState(STATES.ENDED, { type: "start" }), STATES.CONNECTING);
  assert.equal(nextState(STATES.LISTENING, { type: "start" }), STATES.LISTENING);
});

test("cancel returns to ready only while connecting", () => {
  assert.equal(nextState(STATES.CONNECTING, { type: "cancel" }), STATES.READY);
  assert.equal(nextState(STATES.SPEAKING, { type: "cancel" }), STATES.SPEAKING);
});

test("the mode moves between listening and speaking once connected", () => {
  const open = nextState(STATES.CONNECTING, { type: "connected" });
  assert.equal(open, STATES.LISTENING);
  assert.equal(nextState(open, { type: "mode", mode: "speaking" }), STATES.SPEAKING);
  assert.equal(nextState(STATES.SPEAKING, { type: "mode", mode: "listening" }), STATES.LISTENING);
  assert.equal(nextState(STATES.READY, { type: "mode", mode: "speaking" }), STATES.READY);
});

test("an end always lands on ended", () => {
  for (const s of Object.values(STATES)) {
    assert.equal(nextState(s, { type: "ended" }), STATES.ENDED);
  }
});

test("no secure context is the first problem, before the microphone", () => {
  assert.equal(preflightProblem({ isSecureContext: false, hasMediaDevices: false }), "https");
  assert.equal(preflightProblem({ isSecureContext: true, hasMediaDevices: false }), "microphone");
  assert.equal(preflightProblem({ isSecureContext: true, hasMediaDevices: true }), null);
});

test("the card fills a resizable cell down to one row", () => {
  assert.deepEqual(gridOptions(), { columns: 12, rows: 5, min_columns: 4, min_rows: 1 });
});

test("a write answer names the entity and the outcome", () => {
  const chip = chipFor({
    tool_name: "ha_perform_action", tool_call_id: "t1", is_error: false,
    full_tool_result: JSON.stringify({ outcome: "done", entity_id: "light.kitchen_lights", action: "light.turn_off", name: "Kitchen Lights", state: "off" }),
  });
  assert.deepEqual(chip, { id: "t1", kind: "write", target: "Kitchen Lights", outcome: "done", error: null, domain: "light" });
});

test("a confirmation code never reaches the chip", () => {
  const chip = chipFor({
    tool_name: "ha_perform_action", tool_call_id: "t2", is_error: false,
    full_tool_result: JSON.stringify({ outcome: "confirmation_required", entity_id: "lock.front_door", action: "lock.unlock", name: "Front Door", confirmation_code: "4F2A9C" }),
  });
  assert.equal(chip.outcome, "confirmation_required");
  assert.ok(!JSON.stringify(chip).includes("4F2A9C"));
});

test("a read answer names what it found and counts it", () => {
  const chip = chipFor({
    tool_name: "ha_get_state", tool_call_id: "t3", is_error: false,
    full_tool_result: JSON.stringify({ results: [
      { entity_id: "light.a", name: "Kitchen Lights", state: "on" },
      { entity_id: "light.b", error: "not_found" },
    ] }),
  });
  assert.deepEqual(chip, { id: "t3", kind: "read", target: "Kitchen Lights", count: 1, outcome: "read", error: null, domain: null });
});

test("a read of more than two entities only counts them", () => {
  const results = ["a", "b", "c"].map((x) => ({ entity_id: `light.${x}`, name: x, state: "on" }));
  const chip = chipFor({
    tool_name: "ha_get_state", tool_call_id: "t4", is_error: false,
    full_tool_result: JSON.stringify({ results }),
  });
  assert.deepEqual(chip, { id: "t4", kind: "read", target: "", count: 3, outcome: "read", error: null, domain: null });
});

test("a refused write keeps its error code", () => {
  const chip = chipFor({
    tool_call_id: "t4",
    full_tool_result: JSON.stringify({ outcome: "failed", entity_id: "lock.x", action: "lock.open", error: "action_not_allowed" }),
  });
  assert.equal(chip.error, "action_not_allowed");
});

test("without a payload the chip is generic", () => {
  assert.deepEqual(chipFor({ tool_call_id: "t5", is_error: true }), { id: "t5", kind: "generic", target: "", outcome: "error", error: null, domain: null });
  assert.deepEqual(chipFor({ tool_call_id: "t6", is_error: false, full_tool_result: "not json" }), { id: "t6", kind: "generic", target: "", outcome: "done", error: null, domain: null });
});

test("one tool call makes one chip, and the full one wins", () => {
  const generic = chipFor({ tool_call_id: "t1", is_error: false });
  const full = chipFor({ tool_call_id: "t1", full_tool_result: JSON.stringify({ outcome: "done", name: "Kitchen Lights", action: "light.turn_off" }) });
  assert.deepEqual(mergeChip(mergeChip([], generic), full), [full]);
  assert.deepEqual(mergeChip(mergeChip([], full), generic), [full]);
  const other = chipFor({ tool_call_id: "t2", is_error: false });
  assert.equal(mergeChip([full], other).length, 2);
});

test("the end reason comes from the SDK details", () => {
  assert.equal(endReason({ reason: "user" }), "user");
  assert.equal(endReason({ reason: "agent" }), "agent");
  assert.equal(endReason({ reason: "error", message: "x" }), "error");
  assert.equal(endReason(undefined), "error");
});

const T = {
  readMany: (n) => `Read ${n}`,
  usingHouse: "Using the house",
  outcome: { read: "read", done: "done", confirmation_required: "needs confirmation", failed: "failed" },
  endedShort: { user: "You hung up", agent: "Ended", error: "Cut off" },
  actions: (n) => `${n} actions`,
};

test("the form follows the measured size", () => {
  assert.deepEqual(FORM_LIMITS, { rowMaxHeight: 80, squareMaxWidth: 240, stripMaxHeight: 200 });
  assert.equal(formFor(360, 56), "row");
  assert.equal(formFor(176, 56), "row");
  assert.equal(formFor(176, 184), "square");
  assert.equal(formFor(360, 120), "strip");
  assert.equal(formFor(360, 184), "strip");
  assert.equal(formFor(360, 200), "full");
  assert.equal(formFor(239, 400), "square");
});

test("a card measured at zero keeps its form", () => {
  assert.equal(formFor(0, 0), null);
  assert.equal(formFor(360, 0), null);
  assert.equal(formFor(0, 300), null);
});

test("the clock reads minutes and seconds", () => {
  assert.equal(formatClock(0), "0:00");
  assert.equal(formatClock(84), "1:24");
  assert.equal(formatClock(180), "3:00");
  assert.equal(formatClock(61.9), "1:01");
});

test("progress needs a positive maximum", () => {
  assert.deepEqual(progress(84, 180), { elapsed: "1:24", max: "3:00", fraction: 84 / 180 });
  assert.deepEqual(progress(200, 180), { elapsed: "3:20", max: "3:00", fraction: 1 });
  assert.deepEqual(progress(84, null), { elapsed: "1:24", max: null, fraction: null });
  assert.deepEqual(progress(84, -1), { elapsed: "1:24", max: null, fraction: null });
});

test("limits keep only positive numbers", () => {
  assert.deepEqual(limitsFrom({ token: "x", limits: { max_duration_seconds: 180, silence_end_call_timeout: 60 } }),
    { maxSeconds: 180, silenceSeconds: 60 });
  assert.deepEqual(limitsFrom({ token: "x", limits: { max_duration_seconds: null, silence_end_call_timeout: -1 } }),
    { maxSeconds: null, silenceSeconds: null });
  assert.deepEqual(limitsFrom({ token: "x" }), { maxSeconds: null, silenceSeconds: null });
  assert.deepEqual(limitsFrom(undefined), { maxSeconds: null, silenceSeconds: null });
});

test("lines keep their order and a repeated line counts once", () => {
  let lines = addLine([], "user", "Open the lock");
  lines = addLine(lines, "agent", "Are you sure?");
  lines = addLine(lines, "user", "Yes");
  lines = addLine(lines, "user", "Yes");
  assert.deepEqual(lines, [
    { source: "user", text: "Open the lock", cut: false },
    { source: "agent", text: "Are you sure?", cut: false },
    { source: "user", text: "Yes", cut: false },
  ]);
  assert.equal(addLine(lines, "ai", "Done").at(-1).source, "agent");
});

test("an interruption cuts the last agent line only", () => {
  const lines = [
    { source: "agent", text: "First", cut: false },
    { source: "agent", text: "Second", cut: false },
    { source: "user", text: "Stop", cut: false },
  ];
  assert.deepEqual(cutLastAgentLine(lines).map((l) => l.cut), [false, true, false]);
  assert.deepEqual(cutLastAgentLine([{ source: "user", text: "Hi", cut: false }]),
    [{ source: "user", text: "Hi", cut: false }]);
});

function fakeTimers() {
  const pending = new Map();
  let next = 1;
  return {
    pending,
    set: (fn, ms) => { const id = next++; pending.set(id, { fn, ms }); return id; },
    clear: (id) => { pending.delete(id); },
  };
}

test("a hold fires after its time, and a cancel stops it", () => {
  const timers = fakeTimers();
  let held = 0;
  const hold = createHold(() => { held += 1; }, timers);
  hold.start();
  assert.equal(HOLD_MS, 500);
  assert.equal([...timers.pending.values()][0].ms, 500);
  hold.cancel();
  assert.equal(timers.pending.size, 0);
  hold.start();
  for (const { fn } of [...timers.pending.values()]) fn();
  assert.equal(held, 1);
});

test("a second start leaves one timer", () => {
  const timers = fakeTimers();
  const hold = createHold(() => {}, timers);
  hold.start();
  hold.start();
  assert.equal(timers.pending.size, 1);
});

test("a chip reads as target and outcome", () => {
  const write = chipFor({ tool_call_id: "a", full_tool_result: JSON.stringify({ outcome: "done", entity_id: "light.k", name: "Kitchen" }) });
  assert.equal(chipLabel(write, T), "Kitchen · done");
  const failed = chipFor({ tool_call_id: "b", full_tool_result: JSON.stringify({ outcome: "failed", entity_id: "lock.x", name: "Door", error: "action_not_allowed" }) });
  assert.equal(chipLabel(failed, T), "Door · failed (action_not_allowed)");
  const many = chipFor({ tool_call_id: "c", full_tool_result: JSON.stringify({ results: ["a", "b", "c"].map((x) => ({ entity_id: `light.${x}`, name: x })) }) });
  assert.equal(chipLabel(many, T), "Read 3");
  const one = chipFor({ tool_call_id: "d", full_tool_result: JSON.stringify({ results: [{ entity_id: "light.a", name: "Lamp" }] }) });
  assert.equal(chipLabel(one, T), "Lamp · read");
  assert.equal(chipLabel(chipFor({ tool_call_id: "e" }), T), "Using the house · done");
});

test("a confirmation code never reaches the chip label", () => {
  const chip = chipFor({ tool_call_id: "t2", full_tool_result: JSON.stringify({ outcome: "confirmation_required", entity_id: "lock.front_door", name: "Front Door", confirmation_code: "4F2A9C" }) });
  assert.ok(!chipLabel(chip, T).includes("4F2A9C"));
  assert.equal(chipLabel(chip, T), "Front Door · needs confirmation");
});

test("a chip picks its icon and tone", () => {
  const lock = chipFor({ tool_call_id: "a", full_tool_result: JSON.stringify({ outcome: "confirmation_required", entity_id: "lock.x", name: "Door" }) });
  assert.equal(chipIcon(lock), "lock");
  assert.equal(chipTone(lock), "warn");
  const light = chipFor({ tool_call_id: "b", full_tool_result: JSON.stringify({ outcome: "done", entity_id: "light.x", name: "Lamp" }) });
  assert.equal(chipIcon(light), "bulb");
  assert.equal(chipTone(light), "good");
  const read = chipFor({ tool_call_id: "c", full_tool_result: JSON.stringify({ results: [] }) });
  assert.equal(chipIcon(read), "search");
  const failed = chipFor({ tool_call_id: "d", full_tool_result: JSON.stringify({ outcome: "failed", entity_id: "switch.x" }) });
  assert.equal(chipIcon(failed), "check");
  assert.equal(chipTone(failed), "bad");
  assert.equal(chipTone(chipFor({ tool_call_id: "e", is_error: true })), "bad");
});

test("the end summary names the reason, the time and the actions", () => {
  assert.equal(endedSummary("user", 72, 2, T), "You hung up · 1:12 · 2 actions");
  assert.equal(endedSummary("error", 5, 0, T), "Cut off · 0:05 · 0 actions");
});

test("the card config turns the transcript on by default", () => {
  assert.deepEqual(cardConfig({}), { title: "", show_transcript: true });
});

test("the card config turns off only on an explicit false", () => {
  assert.deepEqual(
    cardConfig({ title: "Kitchen", show_transcript: false }),
    { title: "Kitchen", show_transcript: false },
  );
  assert.equal(cardConfig({ show_transcript: 0 }).show_transcript, true);
});

test("the card config drops the ringtone option", () => {
  assert.equal("ringtone" in cardConfig({ ringtone: true }), false);
});

test("the card config drops the text input option", () => {
  assert.equal("show_text_input" in cardConfig({ show_text_input: false }), false);
});

test("a session starts like the ElevenLabs widget", () => {
  assert.deepEqual(sessionStart("wss://example/convai"), {
    signedUrl: "wss://example/convai",
    connectionType: "websocket",
    connectionDelay: { default: 300 },
    stripAudioTags: true,
  });
});

test("a refused or missing microphone is told apart from other start failures", () => {
  for (const name of ["NotAllowedError", "NotFoundError", "NotReadableError", "SecurityError"]) {
    assert.equal(microphoneRefused({ name }), true);
  }
  assert.equal(microphoneRefused(new Error("SDK failed to load")), false);
  assert.equal(microphoneRefused(null), false);
});

function fakeContext(state) {
  const listeners = [];
  const context = {
    state,
    resumed: 0,
    addEventListener: (type, fn) => { if (type === "statechange") listeners.push(fn); },
    resume() { context.resumed += 1; context.state = "running"; return Promise.resolve(); },
    pause(next) { context.state = next; for (const fn of listeners) fn(); },
  };
  return context;
}

test("the capture resumes when Android leaves it suspended", () => {
  const context = fakeContext("suspended");
  keepRunning(context);
  assert.equal(context.resumed, 1);
  assert.equal(context.state, "running");
});

test("the capture resumes again each time Android pauses it", () => {
  const context = fakeContext("running");
  keepRunning(context);
  assert.equal(context.resumed, 0);
  context.pause("interrupted");
  context.pause("suspended");
  assert.equal(context.resumed, 2);
  context.pause("closed");
  assert.equal(context.resumed, 2);
});

test("a session without a capture context needs nothing", () => {
  assert.doesNotThrow(() => keepRunning(undefined));
});

function fakeTrack() {
  return { readyState: "live", stop() { this.readyState = "ended"; } };
}

test("the output stream is stopped and released after the session", () => {
  const track = fakeTrack();
  const element = { srcObject: { getTracks: () => [track] } };
  assert.equal(releaseOutput({ output: { audioElement: element } }), true);
  assert.equal(track.readyState, "ended");
  assert.equal(element.srcObject, null);
});

test("a session without an output stream needs nothing", () => {
  assert.equal(releaseOutput(null), false);
  assert.equal(releaseOutput({ output: {} }), false);
  assert.equal(releaseOutput({ output: { audioElement: { srcObject: null } } }), false);
});
