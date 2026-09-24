// What kinds of tile exist, and what each one needs configured. Studio builds
// its whole editor from this table instead of asking people to type a command
// name and hand-write params JSON.
//
// Keep in step with:
//   - HANDLERS in agents/windows/agent.py: every action `type` here must be a
//     real handler, or the tile answers "unknown command";
//   - WIDGETS in js/widgets/index.js: every widget `type` here must be mounted;
//   - ICONS in js/render.js: every `icon` here must exist, or the tile renders
//     label-only (CLAUDE.md records this happening twice); a "brand:<key>"
//     icon (WEB_PRESETS, APP_BRANDS) must exist in js/brand-icons.js;
//   - db.py's seeds: a seeded tile should be recognised as its entry below,
//     not fall through to "custom".
//
// Names and descriptions live in js/studio-i18n.js as `type.<id>.name` and
// `type.<id>.desc`, and field labels as `field.<param>`.
//
// Field `control` values: path | url | text | select | toggle | devices | city.
// `section: "appearance"` puts a field under Appearance instead of Setup.
// `advanced: true` puts it under the collapsed "More settings".

const ACTIVE_STYLE_FIELD = {
  param: "active_style",
  control: "select",
  section: "appearance",
  options: ["normal", "alert"],
};

// "Ask before running": the phone shows Run / Cancel before sending the
// command. On by default where a mis-tap costs something (power, stopping a
// program, closing the agent); available under More settings elsewhere.
const CONFIRM_FIELD = { param: "confirm", control: "toggle" };

export const GROUPS = ["launch", "actions", "sound", "widgets", "other"];

export const CATALOG = [
  {
    id: "vpn",
    group: "actions",
    kind: "action",
    type: "launch_app",
    target: "windows",
    stateKey: "vpn.running",
    icon: "shield",
    fixedParams: {},
    fields: [
      { param: "path", control: "path", required: true, highlight: true },
      { ...ACTIVE_STYLE_FIELD, default: "normal" },
      { param: "process_name", control: "text", advanced: true },
    ],
  },
  {
    id: "website",
    group: "launch",
    kind: "action",
    type: "open_url",
    target: "windows",
    stateKey: null,
    icon: "globe",
    fixedParams: {},
    fields: [{ param: "url", control: "url", required: true, highlight: true }],
  },
  {
    id: "app",
    group: "launch",
    kind: "action",
    type: "launch_app",
    target: "windows",
    stateKey: null,
    icon: "terminal",
    fixedParams: {},
    fields: [
      { param: "path", control: "path", required: true, highlight: true, installed: true },
      { param: "args", control: "text", advanced: true },
      { param: "fallback_path", control: "text", advanced: true },
      { param: "process_name", control: "text", advanced: true },
      { ...CONFIRM_FIELD, advanced: true },
    ],
  },
  {
    id: "screenshot",
    group: "actions",
    kind: "action",
    type: "screenshot",
    target: "windows",
    stateKey: null,
    icon: "camera",
    fixedParams: {},
    fields: [],
  },
  {
    id: "process_toggle",
    group: "actions",
    kind: "action",
    type: "process_toggle",
    target: "windows",
    stateKey: null,
    icon: "power",
    fixedParams: {},
    fields: [
      { param: "process_name", control: "text", required: true },
      { param: "path", control: "path" },
      // A second tap stops the program (the VPN, say): tech debt #21.
      { ...CONFIRM_FIELD, default: true },
    ],
  },
  {
    id: "agent_shutdown",
    group: "actions",
    kind: "action",
    type: "agent_shutdown",
    target: "windows",
    stateKey: null,
    icon: "power",
    fixedParams: {},
    fields: [{ ...CONFIRM_FIELD, default: true }],
  },
  {
    id: "hotkey",
    group: "actions",
    kind: "action",
    type: "send_keys",
    target: "windows",
    stateKey: null,
    icon: "keyboard",
    fixedParams: {},
    fields: [
      { param: "keys", control: "text", required: true, highlight: true },
      { ...CONFIRM_FIELD, advanced: true },
    ],
  },
  {
    id: "power",
    group: "actions",
    kind: "action",
    type: "power",
    target: "windows",
    stateKey: null,
    icon: "lock",
    fixedParams: {},
    fields: [
      { param: "action", control: "select", options: ["lock", "sleep", "restart", "shutdown"], default: "lock", highlight: true },
      { ...CONFIRM_FIELD, default: true },
    ],
  },
  {
    id: "clipboard",
    group: "actions",
    kind: "action",
    type: "clipboard_set",
    target: "windows",
    stateKey: null,
    icon: "clipboard",
    fixedParams: {},
    fields: [],
  },
  {
    id: "mic",
    group: "sound",
    kind: "action",
    type: "audio_mute_toggle",
    target: "windows",
    stateKey: "mic.muted",
    icon: "mic",
    fixedParams: { device: "microphone" },
    fields: [{ ...ACTIVE_STYLE_FIELD, default: "alert" }],
  },
  {
    id: "speaker_mute",
    group: "sound",
    kind: "action",
    type: "audio_mute_toggle",
    target: "windows",
    stateKey: "speaker.muted",
    icon: "headphones",
    fixedParams: { device: "speaker" },
    fields: [{ ...ACTIVE_STYLE_FIELD, default: "alert" }],
  },
  {
    id: "volume",
    group: "sound",
    kind: "action",
    type: "audio_volume_set",
    target: "windows",
    stateKey: "speaker.volume",
    icon: "speaker",
    // render.js only draws a slider at width >= 2. A 1-wide volume tile is a
    // plain button that does nothing useful.
    minWidth: 2,
    fixedParams: { device: "speaker" },
    fields: [],
  },
  {
    id: "media",
    group: "sound",
    kind: "action",
    type: "media_key",
    target: "windows",
    stateKey: null,
    icon: "media",
    fixedParams: {},
    fields: [
      {
        param: "key",
        control: "select",
        options: ["play_pause", "next", "prev", "stop", "volume_up", "volume_down"],
        default: "play_pause",
        highlight: true,
      },
    ],
  },
  {
    id: "audio_switch",
    group: "sound",
    kind: "action",
    type: "audio_switch",
    target: "windows",
    stateKey: "speaker.device_name",
    icon: "audio-switch",
    fixedParams: {},
    fields: [{ param: "devices", control: "devices" }],
  },
  {
    id: "clock_weather",
    group: "widgets",
    kind: "widget",
    type: "clock_weather",
    // Widgets talk to no agent. "backend" keeps setAgentOffline() and the
    // execute path away from them.
    target: "backend",
    stateKey: null,
    icon: null,
    defaultWidth: 2,
    fixedParams: {},
    fields: [
      { param: "city", control: "city" },
      { param: "show_seconds", control: "toggle" },
    ],
  },
  {
    id: "pc_stats",
    group: "widgets",
    kind: "widget",
    type: "pc_stats",
    // Unlike the clock, this one shows the agent's data, so it names the
    // agent whose PC it describes. Widgets are never greyed or pressed, so
    // the target only picks the state keys (see js/widgets/pc-stats.js).
    target: "windows",
    stateKey: null,
    icon: null,
    defaultWidth: 2,
    fixedParams: {},
    fields: [],
  },
  {
    // Anything the table above doesn't recognise. Keeps hand-made and legacy
    // tiles editable exactly as before: a raw type and raw params.
    id: "custom",
    group: "other",
    kind: null,
    type: null,
    target: null,
    stateKey: null,
    icon: null,
    fixedParams: {},
    fields: [],
  },
];

export const CUSTOM_ID = "custom";

export function entryById(id) {
  return CATALOG.find((entry) => entry.id === id) || CATALOG.find((entry) => entry.id === CUSTOM_ID);
}

// Which catalog entry an existing row is. Order matters: VPN is a launch_app
// with the vpn.running state key (the same key standalone/launcher.py's
// watched_process_name() selects on), so it is tested before the generic app.
export function detectEntry(item) {
  const params = item.params || {};
  if (item.kind === "widget") {
    return entryById(["clock_weather", "pc_stats"].includes(item.type) ? item.type : CUSTOM_ID);
  }
  if (item.kind !== "action") {
    return entryById(CUSTOM_ID);
  }
  switch (item.type) {
    case "launch_app":
      return entryById(item.state_key === "vpn.running" ? "vpn" : "app");
    case "open_url":
      return entryById("website");
    case "audio_mute_toggle":
      if (params.device === "microphone") return entryById("mic");
      if (params.device === "speaker") return entryById("speaker_mute");
      return entryById(CUSTOM_ID);
    case "audio_volume_set":
      return entryById("volume");
    case "audio_switch":
      return entryById("audio_switch");
    case "screenshot":
      return entryById("screenshot");
    case "process_toggle":
      return entryById("process_toggle");
    case "agent_shutdown":
      return entryById("agent_shutdown");
    case "send_keys":
      return entryById("hotkey");
    case "media_key":
      return entryById("media");
    case "power":
      return entryById("power");
    case "clipboard_set":
      return entryById("clipboard");
    default:
      return entryById(CUSTOM_ID);
  }
}

// Params keys that aren't free-form "extra" keys. The editor owns these, and
// the "More settings" JSON box shows everything else.
export const COLOR_PARAMS = ["active_color", "alert_color", "false_color"];
export const DEVICE_PARAMS = ["output_device_primary", "output_device_secondary"];
// Written by the icon picker (see tileIconFor in js/render.js), on any tile.
export const ICON_PARAMS = ["icon_text", "icon_img"];

export function managedParams(entry) {
  const keys = new Set([...COLOR_PARAMS, ...ICON_PARAMS, ...Object.keys(entry.fixedParams)]);
  for (const field of entry.fields) {
    if (field.control === "devices") {
      DEVICE_PARAMS.forEach((key) => keys.add(key));
    } else if (field.control === "city") {
      ["city", "lat", "lon"].forEach((key) => keys.add(key));
    } else {
      keys.add(field.param);
    }
  }
  return keys;
}

// A VPN tile nobody has pointed at a client yet. The one piece of setup a
// fresh install actually needs, which is why Studio puts it in front.
export function vpnNeedsPath(item) {
  return detectEntry(item).id === "vpn" && !String((item.params || {}).path || "").trim();
}

// "Copy as path" in Explorer (Ctrl+Shift+C) wraps the path in double quotes,
// and CreateProcess doesn't want them. It's the obvious way to get a path, so
// the quotes are stripped rather than rejected.
export function cleanPath(value) {
  return String(value || "").trim().replace(/^"(.*)"$/, "$1").trim();
}

// One-tap starting points for a Website tile: fills in the address, the name
// and the logo. Only a convenience -- every value stays editable, and the
// saved tile is an ordinary open_url with no memory of which preset it came
// from, so detectEntry never has to tell presets apart.
export const WEB_PRESETS = [
  { label: "Telegram", url: "https://web.telegram.org/a/", icon: "brand:telegram" },
  { label: "Discord", url: "https://discord.com/app", icon: "brand:discord" },
  { label: "WhatsApp", url: "https://web.whatsapp.com/", icon: "brand:whatsapp" },
  { label: "YouTube", url: "https://www.youtube.com/", icon: "brand:youtube" },
  { label: "ChatGPT", url: "https://chatgpt.com/", icon: "brand:chatgpt" },
  { label: "Claude", url: "https://claude.ai/", icon: "brand:claude" },
  { label: "Gmail", url: "https://mail.google.com/", icon: "brand:gmail" },
  { label: "Google", url: "https://www.google.com/", icon: "brand:google" },
  { label: "GitHub", url: "https://github.com/", icon: "brand:github" },
  { label: "Twitch", url: "https://www.twitch.tv/", icon: "brand:twitch" },
  { label: "Spotify", url: "https://open.spotify.com/", icon: "brand:spotify" },
  { label: "Netflix", url: "https://www.netflix.com/", icon: "brand:netflix" },
  { label: "Reddit", url: "https://www.reddit.com/", icon: "brand:reddit" },
  { label: "X", url: "https://x.com/", icon: "brand:x" },
  { label: "Notion", url: "https://www.notion.so/", icon: "brand:notion" },
];

// An installed program picked from the agent's list gets its logo when its
// name says which one it is. Checked in order; first match wins.
const APP_BRANDS = [
  ["chrome", "chrome"],
  ["firefox", "firefox"],
  ["opera", "opera"],
  ["brave", "brave"],
  ["telegram", "telegram"],
  ["discord", "discord"],
  ["whatsapp", "whatsapp"],
  ["steam", "steam"],
  ["spotify", "spotify"],
  ["obs", "obs"],
  ["notion", "notion"],
  ["github", "github"],
  ["chatgpt", "chatgpt"],
  ["claude", "claude"],
  ["twitch", "twitch"],
];

export function brandForApp(name) {
  const text = String(name || "").toLowerCase();
  const hit = APP_BRANDS.find(([needle]) => text.includes(needle));
  return hit ? `brand:${hit[1]}` : null;
}

// The same schemes agents/windows/handlers/process.py's OPEN_URL_SCHEMES
// accepts, so Studio refuses what the agent would refuse, before saving.
const URL_SCHEMES = ["http:", "https:", "discord:", "tg:", "steam:", "spotify:", "zoommtg:", "slack:", "ms-settings:"];

// "youtube.com" is what people type; the agent needs a scheme. Adds https://
// to anything that has no scheme of its own, and returns "" for an address
// the agent would refuse.
export function cleanUrl(value) {
  let text = String(value || "").trim();
  if (!text) return "";
  // "localhost:8080" would otherwise parse as scheme "localhost:".
  if (/^[^\s/:]+:\d+(\/|$)/.test(text)) text = `http://${text}`;
  else if (!/^[a-z][a-z0-9+.-]*:/i.test(text)) text = `https://${text}`;
  try {
    const url = new URL(text);
    if (!URL_SCHEMES.includes(url.protocol)) return "";
    if ((url.protocol === "http:" || url.protocol === "https:") && !url.hostname) return "";
    return text;
  } catch {
    return "";
  }
}
