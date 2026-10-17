import { strings } from "./strings.js";
import { button, el } from "./ui.js";

// Collapse every run of whitespace to one space.
function squeeze(text) {
  return text.replace(/\s+/g, " ").trim();
}

// Return how `text` carries the recommended block: "missing", "twice",
// "outdated" or "current". The marker counts the copies. The whole block,
// compared without whitespace differences, tells a current copy from an
// old one.
export function blockState(text, marker, block) {
  const copies = text.split(marker).length - 1;
  if (copies === 0) return "missing";
  if (copies > 1) return "twice";
  return squeeze(text).includes(squeeze(block)) ? "current" : "outdated";
}

// Return the sentence under the editor for one block state.
function blockNote(t, state) {
  if (state === "missing") return t.promptBlockMissing;
  if (state === "twice") return t.promptBlockTwice;
  if (state === "outdated") return t.promptBlockOutdated;
  return t.promptBlockPresent;
}

// Copy a textarea's content. The clipboard API needs a secure context, so
// a plain http instance falls back to a selection the user can copy.
function copyFrom(textarea) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(textarea.value).then(
      () => true,
      () => false,
    );
  }
  textarea.focus();
  textarea.select();
  try {
    return Promise.resolve(document.execCommand("copy"));
  } catch (err) {
    return Promise.resolve(false);
  }
}

function pane(titleText, textarea, extra) {
  const node = el("div", "pane");
  const header = document.createElement("header");
  header.appendChild(el("h4", null, titleText));
  if (extra) header.appendChild(extra);
  node.appendChild(header);
  node.appendChild(textarea);
  return node;
}

/**
 * The agent's prompt beside the one this integration recommends.
 *
 * The left pane is editable and is what gets saved. The right pane is read
 * only: the user copies from it and pastes what they want. Nothing is
 * merged automatically.
 */
export function promptScreen(
  hass,
  { current, block, marker, onSave, onDirty, exposeSave },
) {
  const t = strings(hass.language);

  const host = el("div", "titled");

  const panes = el("div", "panes");

  const mine = document.createElement("textarea");
  mine.value = current;
  mine.spellcheck = false;
  let reported = false;
  mine.addEventListener("input", () => {
    note.textContent = blockNote(t, blockState(mine.value, marker, block));
    if (reported) return;
    reported = true;
    if (onDirty) onDirty();
  });
  panes.appendChild(pane(t.promptYours, mine));

  const theirs = document.createElement("textarea");
  theirs.value = block;
  theirs.readOnly = true;
  theirs.spellcheck = false;
  const copy = button(t.copy, "plain", async () => {
    copy.textContent = (await copyFrom(theirs)) ? t.copied : t.copyManually;
    setTimeout(() => {
      copy.textContent = t.copy;
    }, 2000);
  });
  panes.appendChild(pane(t.promptRecommended, theirs, copy));

  host.appendChild(panes);
  panes.classList.add("grow");

  const note = el("p", "subtitle");
  note.textContent = blockNote(t, blockState(current, marker, block));
  host.appendChild(note);

  if (exposeSave) exposeSave(() => onSave(mine.value));
  return host;
}
