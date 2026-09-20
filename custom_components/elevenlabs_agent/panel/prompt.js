import { strings } from "./strings.js";
import { button, el } from "./ui.js";

// True when `text` already carries the recommended block.
function hasBlock(text, marker) {
  return text.includes(marker);
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
    note.textContent = hasBlock(mine.value, marker)
      ? t.promptBlockPresent
      : t.promptBlockMissing;
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
  note.textContent = hasBlock(current, marker)
    ? t.promptBlockPresent
    : t.promptBlockMissing;
  host.appendChild(note);

  if (exposeSave) exposeSave(() => onSave(mine.value));
  return host;
}
