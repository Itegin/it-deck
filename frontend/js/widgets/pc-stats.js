// The PC-load widget: CPU and memory as bars, network speed as text.
//
// It draws from the agent's live state (ctx.onState, see ./index.js): the
// poller reports pc.cpu, pc.ram, pc.net_down and pc.net_up once a second,
// and only while a Dashboard is watching, so this costs the PC nothing when
// the phone is away. Colours come from currentColor alone, like every
// widget, so each theme paints it without a rule here.

const KEYS = ["pc.cpu", "pc.ram", "pc.net_down", "pc.net_up"];

function el(tag, className, parent, text) {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = text;
  parent.appendChild(node);
  return node;
}

export function formatRate(bytesPerSecond) {
  const value = Number(bytesPerSecond);
  if (!Number.isFinite(value) || value < 0) return "—";
  if (value < 1000) return `${Math.round(value)} B/s`;
  if (value < 1e6) return `${Math.round(value / 1000)} KB/s`;
  const mb = value / 1e6;
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB/s`;
}

export function mountPcStats(tile, item, ctx) {
  // Same pattern as the clock: the widget draws its own content, the label
  // stays as the accessible name.
  tile.classList.add("tile-widget", "widget-pc-stats");
  for (const child of tile.querySelectorAll(":scope > .icon")) child.remove();
  const label = tile.querySelector(":scope > .label");
  if (label) label.classList.add("widget-a11y-label");

  const body = el("div", "widget-body", tile);

  function meter(name) {
    const row = el("div", "ps-row", body);
    el("span", "ps-name", row, name);
    const track = el("span", "ps-track", row);
    const fill = el("span", "ps-fill", track);
    const value = el("span", "ps-value", row, "—");
    return { fill, value };
  }
  const cpu = meter("CPU");
  const ram = meter("RAM");
  const net = el("div", "ps-net", body);
  const down = el("span", "ps-down", net, "↓ —");
  const up = el("span", "ps-up", net, "↑ —");

  const prefix = `${item.target || "windows"}:`;
  const values = {};

  function setMeter(parts, percent) {
    const p = Number(percent);
    if (!Number.isFinite(p)) return;
    const clamped = Math.max(0, Math.min(100, Math.round(p)));
    parts.fill.style.width = `${clamped}%`;
    parts.value.textContent = `${clamped}%`;
  }

  if (ctx) {
    ctx.onState((changed) => {
      let touched = false;
      for (const key of KEYS) {
        if (Object.prototype.hasOwnProperty.call(changed, prefix + key)) {
          values[key] = changed[prefix + key];
          touched = true;
        }
      }
      if (!touched) return;
      setMeter(cpu, values["pc.cpu"]);
      setMeter(ram, values["pc.ram"]);
      if (values["pc.net_down"] !== undefined) down.textContent = `↓ ${formatRate(values["pc.net_down"])}`;
      if (values["pc.net_up"] !== undefined) up.textContent = `↑ ${formatRate(values["pc.net_up"])}`;
    });
  }

  // Nothing to stop: no timers, and the state subscription ends with the
  // widget (see ./index.js).
  return function destroy() {};
}
