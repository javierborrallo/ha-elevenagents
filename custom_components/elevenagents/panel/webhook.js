// Pure helpers for the webhook URL field. They touch no DOM.

// Return the value without spaces and without trailing slashes.
export function trimOrigin(value) {
  return String(value || "").trim().replace(/\/+$/, "");
}

// Return the origin with the webhook path, or "" when the origin is empty.
export function fullWebhookUrl(value, path) {
  const origin = trimOrigin(value);
  return origin ? `${origin}${path || ""}` : "";
}

// Return true when the value is the Home Assistant URL.
export function isHaUrl(value, haUrl) {
  return Boolean(haUrl) && trimOrigin(value).toLowerCase() === trimOrigin(haUrl).toLowerCase();
}

// Return the origin to preview, "" when the value is empty, or null when
// the value is not a bare https origin and not the Home Assistant URL.
export function previewOrigin(value, haUrl) {
  const origin = trimOrigin(value);
  if (!origin) return "";
  if (isHaUrl(origin, haUrl)) return origin;
  return /^https:\/\/[^/\s?#@]+$/i.test(origin) ? origin : null;
}

// Return the probe status when the probe is for the value, or "none".
export function statusView(value, probe) {
  if (!probe) return "none";
  const origin = trimOrigin(value).toLowerCase();
  if (!origin || origin !== trimOrigin(probe.value).toLowerCase()) return "none";
  return probe.status;
}
