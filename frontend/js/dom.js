// The one small element builder Studio's dialogs share (the guide, Access,
// What's new). Props: "class", "text" (textContent -- never markup),
// "on<Event>" listeners, anything else as an attribute; null/undefined
// props and falsy children are skipped, so optional bits read inline.
export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
    else node.setAttribute(key, value);
  }
  node.append(...children.filter(Boolean));
  return node;
}
