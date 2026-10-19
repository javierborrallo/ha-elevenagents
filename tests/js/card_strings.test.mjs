import { test } from "node:test";
import assert from "node:assert/strict";
import { STRINGS, stringsFor } from "../../custom_components/elevenagents/card/strings.js";

test("both languages carry the same keys", () => {
  assert.deepEqual(Object.keys(STRINGS.es).sort(), Object.keys(STRINGS.en).sort());
});

test("Spanish is chosen for any es locale, English otherwise", () => {
  assert.equal(stringsFor("es-ES"), STRINGS.es);
  assert.equal(stringsFor("es"), STRINGS.es);
  assert.equal(stringsFor("en-GB"), STRINGS.en);
  assert.equal(stringsFor(undefined), STRINGS.en);
});

test("every chip outcome has a label", () => {
  for (const lang of [STRINGS.en, STRINGS.es]) {
    for (const outcome of ["read", "done", "no_change", "unconfirmed", "failed", "confirmation_required", "error"]) {
      assert.equal(typeof lang.outcome[outcome], "string");
    }
  }
});

test("the read count and the interruption have a label", () => {
  assert.equal(STRINGS.en.readMany(18), "Read 18 entities");
  assert.equal(STRINGS.es.readMany(18), "Consultó 18 entidades");
  for (const lang of [STRINGS.en, STRINGS.es]) assert.equal(typeof lang.interrupted, "string");
});

test("the conversation lines and the action count read as expected", () => {
  assert.equal(STRINGS.en.youLine("Yes"), "You: Yes");
  assert.equal(STRINGS.es.youLine("Sí"), "Tú: Sí");
  assert.equal(STRINGS.en.actions(1), "1 action");
  assert.equal(STRINGS.en.actions(2), "2 actions");
  assert.equal(STRINGS.es.actions(1), "1 acción");
  assert.equal(STRINGS.es.actions(0), "0 acciones");
});

test("every end reason has a short label", () => {
  for (const lang of [STRINGS.en, STRINGS.es]) {
    for (const reason of ["user", "agent", "error"]) assert.equal(typeof lang.endedShort[reason], "string");
  }
});

test("the sheet labels exist in both languages", () => {
  assert.deepEqual(Object.keys(STRINGS.es.sheet).sort(), Object.keys(STRINGS.en.sheet).sort());
  assert.equal(STRINGS.en.sheet.max("3:00"), "max 3:00");
  assert.equal(STRINGS.es.sheet.max("3:00"), "máximo 3:00");
  assert.equal(STRINGS.en.sheet.silence(60), "Hangs up after 60 s of silence");
  assert.equal(STRINGS.es.sheet.silence(60), "Cuelga tras 60 s de silencio");
});
