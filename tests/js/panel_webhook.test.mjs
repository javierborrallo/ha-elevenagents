import { test } from "node:test";
import assert from "node:assert/strict";
import {
  trimOrigin, fullWebhookUrl, isHaUrl, previewOrigin, statusView,
} from "../../custom_components/elevenagents/panel/webhook.js";

const PATH = "/api/webhook/abc";

test("the origin loses spaces and trailing slashes", () => {
  assert.equal(trimOrigin("  https://hooks.example.com// "), "https://hooks.example.com");
  assert.equal(trimOrigin("   "), "");
});

test("the full URL is the origin and the path, or empty", () => {
  assert.equal(fullWebhookUrl("https://hooks.example.com/", PATH), "https://hooks.example.com/api/webhook/abc");
  assert.equal(fullWebhookUrl("", PATH), "");
});

test("the Home Assistant URL matches without case or trailing slash", () => {
  assert.equal(isHaUrl("https://Home.example/", "https://home.example"), true);
  assert.equal(isHaUrl("https://hooks.example.com", "https://home.example"), false);
  assert.equal(isHaUrl("", ""), false);
});

test("the probe result shows only for the value it probed", () => {
  const probe = { value: "https://home.example", status: "ok" };
  assert.equal(statusView("https://Home.example/", probe), "ok");
  assert.equal(statusView("https://hooks.example.com", probe), "none");
  assert.equal(statusView("https://home.example", { value: "https://home.example", status: "checking" }), "checking");
  assert.equal(statusView("https://home.example", null), "none");
  assert.equal(statusView("", { value: "", status: "problem" }), "none");
});

test("the preview takes an https origin or the Home Assistant URL", () => {
  const ha = "http://home.example:8123";
  assert.equal(previewOrigin("https://hooks.example.com/", ha), "https://hooks.example.com");
  assert.equal(previewOrigin("http://home.example:8123", ha), "http://home.example:8123");
  assert.equal(previewOrigin("  ", ha), "");
  assert.equal(previewOrigin("https://hooks.example.com/ha", ha), null);
  assert.equal(previewOrigin("http://hooks.example.com", ha), null);
  assert.equal(previewOrigin("hooks.example.com", ha), null);
});
