// What kinds of tile exist, and what each one needs configured. Studio builds
// its whole editor from this table instead of asking people to type a command
// name and hand-write params JSON.
//
// Keep in step with:
//   - HANDLERS in agents/windows/agent.py: every action `type` here must be a
//     real handler, or the tile answers "unknown command";
//   - WIDGETS in js/widgets/index.js: every widget `type` here must be mounted;
//   - ICONS in js/render.js: every `icon` here must exist, or the tile renders
//     label-only (CLAUDE.md records this happening twice);
//   - db.py's seeds: a seeded tile should be recognised as its entry below,
//     not fall through to "custom".
//
// Names and descriptions live in js/studio-i18n.js as `type.<id>.name` and
// `type.<id>.desc`, and field labels as `field.<param>`.
//
// Field `control` values: path | text | select | toggle | devices | city.
// `section: "appearance"` puts a field under Appearance instead of Setup.
// `advanced: true` puts it under the collapsed "More settings".

const ACTIVE_STYLE_FIELD = {
  param: "active_style",
  control: "select",
  section: "appearance",
  options: ["normal", "alert"],
};

export const GROUPS = ["actions", "sound", "widgets", "other"];

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
    id: "app",
    group: "actions",
    kind: "action",
    type: "launch_app",
    target: "windows",
    stateKey: null,
    icon: "terminal",
    fixedParams: {},
    fields: [
      { param: "path", control: "path", required: true, highlight: true },
      { param: "fallback_path", control: "text", advanced: true },
      { param: "process_name", control: "text", advanced: true },
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
    return entryById(item.type === "clock_weather" ? "clock_weather" : CUSTOM_ID);
  }
  if (item.kind !== "action") {
    return entryById(CUSTOM_ID);
  }
  switch (item.type) {
    case "launch_app":
      return entryById(item.state_key === "vpn.running" ? "vpn" : "app");
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
    default:
      return entryById(CUSTOM_ID);
  }
}

// Params keys that aren't free-form "extra" keys. The editor owns these, and
// the "More settings" JSON box shows everything else.
export const COLOR_PARAMS = ["active_color", "alert_color", "false_color"];
export const DEVICE_PARAMS = ["output_device_primary", "output_device_secondary"];

export function managedParams(entry) {
  const keys = new Set([...COLOR_PARAMS, ...Object.keys(entry.fixedParams)]);
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
