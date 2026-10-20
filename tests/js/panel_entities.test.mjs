import { test } from "node:test";
import assert from "node:assert/strict";
import { offeredIds } from "../../custom_components/elevenagents/panel/entities.js";

const ALL = ["light.desk", "sensor.temperature", "lock.front"];

test("without an offer every entity is listed", () => {
  assert.deepEqual(offeredIds(ALL, null, []), ALL);
});

test("an offer keeps only the offered entities", () => {
  assert.deepEqual(offeredIds(ALL, ["light.desk", "lock.front"], []), ["light.desk", "lock.front"]);
});

test("a selected entity stays listed so the user can remove it", () => {
  assert.deepEqual(
    offeredIds(ALL, ["light.desk"], ["sensor.temperature"]),
    ["light.desk", "sensor.temperature"],
  );
});
