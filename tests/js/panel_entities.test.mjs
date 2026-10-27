import { test } from "node:test";
import assert from "node:assert/strict";
import { offeredIds, shownIds } from "../../custom_components/elevenagents/panel/entities.js";

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

const REGISTRY = {
  "light.desk": {},
  "sensor.battery": { entity_category: "diagnostic" },
  "select.power_on_behavior": { entity_category: "config" },
};
const IDS = ["light.desk", "sensor.battery", "select.power_on_behavior", "sensor.unregistered"];

test("technical entities are hidden by default", () => {
  assert.deepEqual(shownIds(IDS, REGISTRY, [], false), ["light.desk", "sensor.unregistered"]);
});

test("the checkbox shows the technical entities", () => {
  assert.deepEqual(shownIds(IDS, REGISTRY, [], true), IDS);
});

test("a selected technical entity is always shown", () => {
  assert.deepEqual(
    shownIds(IDS, REGISTRY, ["sensor.battery"], false),
    ["light.desk", "sensor.battery", "sensor.unregistered"],
  );
});
