// DOM helpers and icons of the ElevenAgents card.

const PATHS = {
  mic: ["M9 5a3 3 0 0 1 6 0v6a3 3 0 0 1-6 0z", "M5 11a7 7 0 0 0 14 0", "M12 18v3"],
  micOff: ["M9 5a3 3 0 0 1 6 0v6a3 3 0 0 1-6 0z", "M5 11a7 7 0 0 0 14 0", "M12 18v3", "M3 3l18 18"],
  hang: ["M3 15c5-4.5 13-4.5 18 0l-2.5 2.5-3.5-1.5v-2.4a10 10 0 0 0-6 0V16l-3.5 1.5z"],
  close: ["M6 6l12 12", "M18 6L6 18"],
  keys: ["M4 6h16a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z", "M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"],
  send: ["M5 12h14", "M13 6l6 6-6 6"],
  wave: ["M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4"],
  lock: ["M7 11V8a5 5 0 0 1 10 0v3", "M5 11h14v9H5z"],
  bulb: ["M9 18h6M10 21h4", "M12 3a6 6 0 0 0-4 10.5c.8.8 1 1.5 1 2.5h6c0-1 .2-1.7 1-2.5A6 6 0 0 0 12 3z"],
  search: ["M11 5a6 6 0 1 0 0 12a6 6 0 0 0 0-12z", "M20 20l-4.5-4.5"],
  check: ["M5 12l5 5 9-10"],
  clock: ["M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18z", "M12 7v5l3 2"],
};

// Create an element with an optional class and text.
export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Create an icon by its name. The icon takes the text color.
export function icon(name, size = 20) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  for (const d of PATHS[name] || []) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}
