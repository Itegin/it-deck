// Studio's editor panel. It is built from js/tile-catalog.js, so the person
// picks what a tile does from cards and fills in only what that kind of
// tile needs. The command name and the params JSON are no longer the
// interface. Raw JSON is still there, under "More settings", for anything
// the catalog doesn't model.
//
// Rules carried over from the previous studio.js (see CLAUDE.md and the tech
// reference, §6):
//
// - Colours: a state colour key PRESENT in params is an explicit choice and
//   is written back. An ABSENT key is "use the theme's colour" and stays
//   absent. The decision is never made by comparing against the teal/red
//   defaults: every tile saved through the old Studio carries all three keys,
//   and an equality test would silently drop a colour someone chose.
// - Audio devices: a device list that failed to load means "unknown", not
//   "none", so a saved ID survives a save made while the agent is offline.
//   Only a list that did load, with "none" picked, removes the key.
// - Every async result (devices, city search) is checked against a request
//   sequence and against its element still being on the page, so a slow
//   reply can't fill in a tile the person has since moved away from.

import { ICONS, ICON_TEXT, ICON_IMAGE, BRAND_PREFIX, hasEntry, iconText, isIconImage } from "./render.js";
import { BRAND_ICONS, BRAND_NAMES } from "./brand-icons.js";
import {
  CATALOG,
  GROUPS,
  COLOR_PARAMS,
  CUSTOM_ID,
  ICON_PARAMS,
  WEB_PRESETS,
  brandForApp,
  cleanPath,
  cleanUrl,
  detectEntry,
  entryById,
  managedParams,
} from "./tile-catalog.js";
import { LANG, t } from "./studio-i18n.js";

const PRIMARY_PARAM = "output_device_primary";
const SECONDARY_PARAM = "output_device_secondary";

// The brand palette only (see the it-deck-visual-design skill): the default
// tile, the accent purple, active teal, alert red, plus the yellow and
// blue the seeded decks already use.
const SWATCHES = ["#2a2f38", "#8e5ff5", "#0d9488", "#dc2626", "#f2c14e", "#3b82f6"];

const DEFAULT_COLORS = {
  active_color: "#0d9488",
  alert_color: "#dc2626",
};

const SVG_OPEN =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">';
// Type-card glyphs for the two entries with no tile icon of their own.
const CARD_GLYPHS = {
  clock_weather: `${SVG_OPEN}<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>`,
  custom: `${SVG_OPEN}<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/></svg>`,
};

// Which of the picker's four tabs an item.icon value belongs to.
function iconMode(icon) {
  if (icon === ICON_TEXT) return "text";
  if (icon === ICON_IMAGE) return "image";
  if (String(icon || "").startsWith(BRAND_PREFIX)) return "brand";
  return "symbol";
}

let deviceRequestSeq = 0;
let appsRequestSeq = 0;
let iconRequestSeq = 0;
let citySearchSeq = 0;

// Tiny element builder. Text goes through textContent, never innerHTML. The
// only innerHTML writes in this file take module-authored SVG literals.
function h(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
    // Properties, not attributes: a textarea ignores a value attribute, and
    // an attribute only sets an input's *default* value.
    else if (key === "value" || key === "checked") node[key] = value;
    else if (key in node && typeof value !== "string") node[key] = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

let idCounter = 0;
function uid(prefix) {
  idCounter += 1;
  return `${prefix}-${idCounter}`;
}

function entryName(entry) {
  return t(`type.${entry.id}.name`);
}

export function createInspector(root, ctx) {
  // ctx: { api, getWorkspace, firstFreeCell, firstFreeDockSlot, targets,
  //        onSaved(item), onDeleted(), onClose() }
  let state = null;
  let opener = null;

  function renderEmpty() {
    state = null;
    root.replaceChildren(
      h("div", { class: "inspector-empty" }, [
        h("h2", { id: "inspector-title", text: t("inspector.emptyTitle") }),
        h("p", { text: t("inspector.emptyBody") }),
      ]),
    );
  }

  // origin (new tiles only): "cell" -- a grid cell's "+" was clicked, so the
  // tile belongs there; "dock" -- the bar's "+"; "button" -- "+ New tile",
  // where a quick-launch tile defaults to the bar.
  function open({ item, cell, origin = "cell" }) {
    opener = document.activeElement;
    deviceRequestSeq++;
    citySearchSeq++;

    const workspace = ctx.getWorkspace();
    const entry = item ? detectEntry(item) : entryById("app");
    const params = item ? { ...item.params } : {};
    const managed = managedParams(entry);

    const extra = {};
    for (const [key, value] of Object.entries(params)) {
      if (entry.id === CUSTOM_ID ? !COLOR_PARAMS.includes(key) && !ICON_PARAMS.includes(key) : !managed.has(key)) {
        extra[key] = value;
      }
    }

    const fieldValues = {};
    for (const field of entry.fields) {
      if (field.control === "city") {
        fieldValues.city = { city: params.city || "", lat: params.lat ?? "", lon: params.lon ?? "" };
      } else if (field.control === "devices") {
        // handled by state.devices
      } else if (params[field.param] !== undefined) {
        fieldValues[field.param] = params[field.param];
      } else if (field.default !== undefined) {
        fieldValues[field.param] = field.default;
      }
    }

    const colors = {};
    for (const key of COLOR_PARAMS) {
      colors[key] = Object.prototype.hasOwnProperty.call(params, key) ? params[key] : null;
    }

    state = {
      item,
      origin,
      originalParams: params,
      entry,
      // An item whose params didn't parse opens with the raw text in the
      // JSON box, so it can be fixed rather than silently replaced.
      paramsInvalid: Boolean(item && item.paramsInvalid),
      // An existing tile opens with its type collapsed to one line. Changing
      // what a tile does is the rare edit, and the full gallery pushed the
      // fields people actually came for below the fold.
      typeExpanded: !item,
      draft: {
        label: item ? item.label : entryName(entry),
        icon: item ? item.icon || "" : entry.icon || "",
        iconText: params.icon_text || "",
        iconImg: isIconImage(params.icon_img) ? params.icon_img : "",
        color: item ? item.color || "#2a2f38" : "#2a2f38",
        colors,
        kind: item ? item.kind : entry.kind,
        type: item ? item.type : entry.type,
        target: item ? item.target : entry.target,
        stateKey: item ? item.state_key || "" : entry.stateKey || "",
        workspaceId: item ? item.workspace_id : workspace.id,
        row: item ? item.row : cell.row,
        col: item ? item.col : cell.col,
        width: item ? item.width || 1 : 1,
        height: item ? item.height || 1 : 1,
        dock: item ? Boolean(item.dock) : false,
        fieldValues,
        devices: { primary: params[PRIMARY_PARAM] || "", secondary: params[SECONDARY_PARAM] || "", loaded: false },
        extraText: item && item.paramsInvalid ? item.paramsRaw : JSON.stringify(extra, null, 2),
      },
    };

    if (!item) {
      applyEntryDefaults(entry, null);
    }

    render();

    const title = root.querySelector("#inspector-title");
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const rect = root.getBoundingClientRect();
    if (rect.top < 0 || rect.top > window.innerHeight * 0.6) {
      root.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
    }
    if (title) {
      title.focus({ preventScroll: true });
    }
  }

  function close({ restoreFocus = true } = {}) {
    deviceRequestSeq++;
    citySearchSeq++;
    const previous = opener;
    opener = null;
    renderEmpty();
    if (restoreFocus && previous && previous.isConnected) {
      previous.focus();
    }
    ctx.onClose();
  }

  // Width, icon, label and state key for a newly chosen type. Only values
  // the person hasn't made their own are replaced.
  function applyEntryDefaults(entry, previousEntry) {
    const draft = state.draft;
    const workspace = ctx.getWorkspace();

    if (!draft.label.trim() || (previousEntry && draft.label === entryName(previousEntry))) {
      draft.label = entryName(entry);
    }
    if (!draft.icon || (previousEntry && draft.icon === previousEntry.icon)) {
      draft.icon = entry.icon || "";
    }
    if (entry.id !== CUSTOM_ID) {
      draft.kind = entry.kind;
      draft.type = entry.type;
      draft.target = entry.target;
      draft.stateKey = entry.stateKey || "";
    }
    // Where a new tile goes: the bar when it was asked for there, or when it
    // is a quick-launch tile added with "+ New tile"; otherwise the grid.
    // Anything that isn't a 1x1 action can't live in the bar at all.
    const barAllowed = draft.kind === "action" && !(entry.minWidth > 1);
    if (!state.item) {
      const wantBar = state.origin === "dock" || (state.origin === "button" && entry.group === "launch");
      setDock(barAllowed && wantBar);
    } else if (draft.dock && !barAllowed) {
      setDock(false);
    }
    if (draft.dock) return;

    const wanted = Math.max(entry.minWidth || 1, state.item ? draft.width : entry.defaultWidth || 1);
    // Don't widen past the grid edge. The backend would refuse it anyway,
    // but a form that opens already invalid is a bad first impression.
    const room = workspace ? workspace.grid_cols - draft.col : wanted;
    draft.width = Math.max(1, Math.min(wanted, room));
  }

  // Moves the draft between the grid and the quick-launch bar, picking a
  // free spot on the side it lands on. A tile already on that side keeps
  // its own spot.
  function setDock(on) {
    const draft = state.draft;
    const wasDock = state.item ? Boolean(state.item.dock) : false;
    if (on) {
      if (draft.dock) return;
      const slot = wasDock ? state.item.col : ctx.firstFreeDockSlot();
      if (slot < 0) return; // bar full: stays in the grid
      draft.dock = true;
      draft.width = 1;
      draft.height = 1;
      draft.row = 0;
      draft.col = slot;
    } else {
      if (!draft.dock) return;
      draft.dock = false;
      const cell = !wasDock && state.item ? { row: state.item.row, col: state.item.col } : ctx.firstFreeCell();
      if (cell) {
        draft.row = cell.row;
        draft.col = cell.col;
      }
    }
  }

  function switchEntry(entryId) {
    const previous = state.entry;
    const entry = entryById(entryId);
    if (entry === previous) return;

    const values = {};
    for (const field of entry.fields) {
      const carried = state.draft.fieldValues[field.param];
      if (carried !== undefined && previous.fields.some((f) => f.param === field.param && f.control === field.control)) {
        values[field.param] = carried;
      } else if (field.control === "city") {
        values.city = { city: "", lat: "", lon: "" };
      } else if (field.default !== undefined) {
        values[field.param] = field.default;
      }
    }
    state.draft.fieldValues = values;
    state.entry = entry;
    applyEntryDefaults(entry, previous);
    render();
    const radio = root.querySelector(`input[name="tile-type"][value="${CSS.escape(entry.id)}"]`);
    if (radio) radio.focus();
  }

  // ── Rendering ────────────────────────────────────────────────────────────

  function render() {
    const { item } = state;
    const titleText = item ? t("inspector.editTitle", { label: item.label }) : t("inspector.newTitle");

    const head = h("div", { class: "inspector-head" }, [
      h("h2", { id: "inspector-title", tabindex: "-1", text: titleText }),
      h("button", {
        type: "button",
        class: "btn btn-ghost",
        "aria-label": t("common.close"),
        text: "✕",
        onClick: () => close(),
      }),
    ]);

    const body = h("div", { class: "inspector-body" }, [
      renderTypeStep(),
      renderSetupStep(),
      renderAppearanceStep(),
      renderPlacementStep(),
      renderAdvanced(),
    ]);

    const message = h("p", { class: "message", role: "alert", id: "inspector-message" });
    const foot = h("div", { class: "inspector-foot" }, [
      message,
      h("button", { type: "submit", class: "btn btn-primary", text: t("common.save") }),
      h("button", { type: "button", class: "btn", text: t("common.cancel"), onClick: () => close() }),
      item
        ? h("button", {
            type: "button",
            class: "btn btn-danger",
            text: t("common.delete"),
            "aria-label": t("inspector.deleteNamed", { label: item.label }),
            onClick: () => remove(),
          })
        : null,
    ]);

    const form = h("form", { novalidate: true, onSubmit: (event) => { event.preventDefault(); save(); } }, [head, body, foot]);
    root.replaceChildren(form);
  }

  function step(number, title, children) {
    return h("section", { class: "step" }, [
      h("h3", { class: "step-title" }, [h("span", { class: "step-num", text: String(number) }), title]),
      ...children,
    ]);
  }

  function typeCard(entry) {
    return h("label", { class: "type-card" }, [
      h("input", {
        type: "radio",
        name: "tile-type",
        value: entry.id,
        checked: entry === state.entry,
        onChange: () => switchEntry(entry.id),
      }),
      h("span", { class: "type-icon", html: hasEntry(ICONS, entry.icon) ? ICONS[entry.icon] : CARD_GLYPHS[entry.id] || CARD_GLYPHS.custom }),
      h("span", { class: "type-name", text: entryName(entry) }),
      h("span", { class: "type-desc", text: t(`type.${entry.id}.desc`) }),
    ]);
  }

  function renderTypeStep() {
    if (!state.typeExpanded) {
      return step(1, t("step.type"), [
        h("div", { class: "type-current" }, [
          h("div", { class: "type-gallery" }, [typeCard(state.entry)]),
          h("button", {
            type: "button",
            class: "btn",
            text: t("type.change"),
            onClick: () => {
              state.typeExpanded = true;
              render();
              const radio = root.querySelector('input[name="tile-type"]:checked');
              if (radio) radio.focus();
            },
          }),
        ]),
      ]);
    }
    const groups = GROUPS.map((group) => {
      const entries = CATALOG.filter((entry) => entry.group === group);
      if (!entries.length) return null;
      const titleId = uid("type-group");
      return h("div", { class: "type-group", role: "radiogroup", "aria-labelledby": titleId }, [
        h("p", { class: "type-group-title", id: titleId, text: t(`group.${group}`) }),
        h("div", { class: "type-gallery" }, entries.map(typeCard)),
      ]);
    });
    return step(1, t("step.type"), groups);
  }

  function renderSetupStep() {
    const { entry, draft } = state;
    const children = [];

    if (entry.id === CUSTOM_ID) {
      children.push(
        h("div", { class: "field-grid" }, [
          selectField(t("field.kind"), ["action", "widget"], draft.kind, (value) => { draft.kind = value; }, (v) => v),
          textField(t("field.type"), draft.type || "", (value) => { draft.type = value; }, { hint: t("hint.type"), required: true }),
        ]),
      );
    }

    const fields = entry.fields.filter((field) => !field.section && !field.advanced);
    for (const field of fields) {
      children.push(renderField(field));
    }

    if (!children.length) {
      children.push(h("p", { class: "field-hint", text: t("setup.nothing") }));
    }
    return step(2, t("step.setup"), children);
  }

  function renderField(field) {
    const { entry, draft } = state;
    const values = draft.fieldValues;
    const label = t(`field.${field.param}`);

    switch (field.control) {
      case "path":
        return pathField(field, label);
      case "url":
        return urlField(field, label);
      case "text":
        return textField(label, values[field.param] || "", (value) => { values[field.param] = value; }, {
          hint: t(`hint.${entry.id}.${field.param}`, null, t(`hint.${field.param}`, null, "")),
          required: field.required,
          mono: true,
        });
      case "select":
        return selectField(label, field.options, values[field.param] ?? field.default, (value) => {
          values[field.param] = value;
        }, (option) => t(`option.${field.param}.${option}`));
      case "toggle":
        return h("label", { class: "check" }, [
          h("input", {
            type: "checkbox",
            checked: Boolean(values[field.param]),
            onChange: (event) => { values[field.param] = event.target.checked; },
          }),
          label,
        ]);
      case "devices":
        return devicesField();
      case "city":
        return cityField();
      default:
        return null;
    }
  }

  function textField(label, value, onInput, { hint, required, mono, type = "text", min } = {}) {
    const id = uid("field");
    const hintId = hint ? uid("hint") : null;
    return h("div", { class: "field" }, [
      h("label", { class: "field-label", for: id, text: label }),
      h("input", {
        id,
        class: "input",
        type,
        min,
        value: value ?? "",
        required,
        spellcheck: mono ? "false" : undefined,
        "aria-describedby": hintId,
        style: mono ? "font-family: ui-monospace, SFMono-Regular, Consolas, monospace" : undefined,
        onInput: (event) => onInput(event.target.value),
      }),
      hint ? h("p", { class: "field-hint", id: hintId, text: hint }) : null,
    ]);
  }

  function selectField(label, options, value, onChange, optionLabel) {
    const id = uid("field");
    const select = h(
      "select",
      { id, onChange: (event) => onChange(event.target.value) },
      options.map((option) => h("option", { value: option, text: optionLabel(option) })),
    );
    select.value = value ?? options[0];
    return h("div", { class: "field" }, [h("label", { class: "field-label", for: id, text: label }), select]);
  }

  // The path to launch. For a VPN tile this is the whole setup, so it gets the
  // accent outline, a Required badge until it has a value, and the
  // instructions to go with it.
  function pathField(field, label) {
    const { entry, draft } = state;
    const id = uid("path");
    const hintId = uid("hint");
    const current = draft.fieldValues[field.param] || "";

    const badge = h("span", { class: "badge" });
    function syncBadge(value) {
      const done = Boolean(cleanPath(value));
      badge.classList.toggle("is-done", done);
      badge.textContent = done ? t("badge.set") : t("badge.required");
      badge.hidden = !field.required;
    }
    syncBadge(current);

    const input = h("input", {
      id,
      class: "input",
      value: current,
      required: field.required,
      spellcheck: "false",
      autocomplete: "off",
      placeholder: t(`placeholder.${entry.id}.path`, null, t("placeholder.path")),
      "aria-describedby": hintId,
      onInput: (event) => {
        draft.fieldValues[field.param] = event.target.value;
        syncBadge(event.target.value);
      },
    });

    const hints = [h("p", { class: "field-hint", id: hintId, text: t(`hint.${entry.id}.path`, null, t("hint.path")) })];
    hints.push(h("p", { class: "field-hint", text: t("hint.copyPath") }));
    if (entry.id === "vpn") {
      hints.push(h("p", { class: "field-hint", text: t("hint.vpn.admin") }));
    }

    return h("div", { class: `field${field.highlight ? " field-highlight" : ""}` }, [
      h("label", { class: "field-label", for: id }, [label, badge]),
      field.installed ? installedPicker(input, syncBadge) : null,
      input,
      ...hints,
    ]);
  }

  // "Choose from installed": the Start Menu shortcuts of the PC the agent
  // runs on (agents/windows/handlers/apps.py). Picking one fills the path,
  // the launch arguments, and -- if the person hasn't made them their own --
  // the name and the logo.
  function installedPicker(pathInput, syncBadge) {
    const { draft } = state;
    const message = h("p", { class: "message", role: "status" });
    const filter = h("input", { class: "input", type: "search", placeholder: t("apps.search"), autocomplete: "off", hidden: true });
    const list = h("ul", { class: "search-results app-results", hidden: true });
    let apps = [];

    function showList() {
      const query = filter.value.trim().toLowerCase();
      const shown = apps.filter((app) => !query || app.name.toLowerCase().includes(query)).slice(0, 60);
      list.replaceChildren(
        ...shown.map((app) => {
          const brand = brandForApp(app.name);
          return h("li", {}, [
            h("button", {
              type: "button",
              onClick: () => {
                draft.fieldValues.path = app.path;
                pathInput.value = app.path;
                syncBadge(app.path);
                if (app.args) draft.fieldValues.args = app.args;
                else delete draft.fieldValues.args;
                if (!draft.label.trim() || draft.label === entryName(state.entry)) draft.label = app.name;
                if (brand && iconMode(draft.icon) === "symbol") draft.icon = brand;
                render();
              },
            }, [
              brand ? h("span", { class: "app-logo", html: BRAND_ICONS[brand.slice(BRAND_PREFIX.length)] }) : null,
              app.name,
            ]),
          ]);
        }),
      );
      list.hidden = !shown.length;
      message.textContent = shown.length ? "" : t("apps.none");
    }

    async function load() {
      const seq = ++appsRequestSeq;
      message.textContent = t("apps.loading");
      message.classList.remove("error");
      const result = await ctx.api(`/api/agents/${encodeURIComponent(draft.target)}/list_apps`, { method: "POST" });
      if (seq !== appsRequestSeq || !message.isConnected) return;
      if (!result.ok || !result.data || result.data.status !== "ok") {
        const detail = result.ok ? (result.data && result.data.message) || "agent error" : result.detail;
        message.textContent = t("apps.failed", { detail });
        message.classList.add("error");
        return;
      }
      apps = result.data.apps || [];
      filter.hidden = false;
      filter.focus();
      showList();
    }

    filter.addEventListener("input", showList);
    return h("div", { class: "installed" }, [
      h("button", { type: "button", class: "btn", text: t("apps.pick"), onClick: load }),
      filter,
      message,
      list,
    ]);
  }

  // A Website tile's address, with one-tap presets above it.
  function urlField(field, label) {
    const { draft } = state;
    const id = uid("url");
    const hintId = uid("hint");
    const input = h("input", {
      id,
      class: "input",
      type: "url",
      value: draft.fieldValues[field.param] || "",
      required: field.required,
      spellcheck: "false",
      autocomplete: "off",
      inputmode: "url",
      placeholder: "https://web.telegram.org",
      "aria-describedby": hintId,
      onInput: (event) => { draft.fieldValues[field.param] = event.target.value; },
    });
    const presets = WEB_PRESETS.map((preset) =>
      h("button", {
        type: "button",
        class: "preset-chip",
        onClick: () => {
          draft.fieldValues[field.param] = preset.url;
          draft.label = preset.label;
          draft.icon = preset.icon;
          render();
        },
      }, [
        h("span", { class: "app-logo", html: BRAND_ICONS[preset.icon.slice(BRAND_PREFIX.length)] }),
        preset.label,
      ]),
    );
    return h("div", { class: `field${field.highlight ? " field-highlight" : ""}` }, [
      h("span", { class: "field-label", text: t("presets.title") }),
      h("div", { class: "preset-chips" }, presets),
      h("label", { class: "field-label", for: id, text: label }),
      input,
      h("p", { class: "field-hint", id: hintId, text: t("hint.url") }),
    ]);
  }

  function devicesField() {
    const { draft } = state;
    const primary = h("select", { id: uid("dev"), onChange: (e) => { draft.devices.primary = e.target.value; } });
    const secondary = h("select", { id: uid("dev"), onChange: (e) => { draft.devices.secondary = e.target.value; } });
    const message = h("p", { class: "message", role: "status" });

    populateDeviceSelect(primary, [], draft.devices.primary, false);
    populateDeviceSelect(secondary, [], draft.devices.secondary, false);

    const wrapper = h("div", { class: "field" }, [
      h("div", { class: "field-grid" }, [
        h("div", { class: "field" }, [h("label", { class: "field-label", for: primary.id, text: t("field.primaryDevice") }), primary]),
        h("div", { class: "field" }, [h("label", { class: "field-label", for: secondary.id, text: t("field.secondaryDevice") }), secondary]),
      ]),
      h("p", { class: "field-hint", text: t("hint.devices") }),
      message,
    ]);

    loadDevices(primary, secondary, message);
    return wrapper;
  }

  function populateDeviceSelect(select, devices, selectedId, loaded) {
    select.replaceChildren(h("option", { value: "", text: t("devices.none") }));
    for (const device of devices) {
      // Name alone isn't unique: two endpoints can both be called "Микрофон".
      // A device Windows remembers but isn't plugged in is still selectable,
      // since you may be configuring it before plugging it back, but it is
      // marked.
      const disconnected = device.is_active === false ? `, ${t("devices.disconnected")}` : "";
      select.append(h("option", { value: device.id, text: `${device.name} (${device.direction}${disconnected})` }));
    }
    // A saved ID the list doesn't contain is kept visible and selected, so
    // it isn't mistaken for "never configured" and dropped on save.
    if (selectedId && !devices.some((device) => device.id === selectedId)) {
      select.append(
        h("option", {
          value: selectedId,
          text: loaded ? t("devices.missing", { id: selectedId }) : t("devices.saved", { id: selectedId }),
        }),
      );
    }
    select.value = selectedId || "";
  }

  async function loadDevices(primary, secondary, message) {
    const seq = ++deviceRequestSeq;
    const { draft } = state;
    draft.devices.loaded = false;
    message.textContent = t("devices.loading");
    message.classList.remove("error");

    const result = await ctx.api(`/api/agents/${encodeURIComponent(draft.target)}/list_devices`, { method: "POST" });
    if (seq !== deviceRequestSeq || !message.isConnected) return;

    // A 200 can still carry the agent's own failure (SoundVolumeView erroring
    // on a live agent), so status is checked on both levels.
    if (!result.ok || !result.data || result.data.status !== "ok") {
      const detail = result.ok ? (result.data && result.data.message) || "agent error" : result.detail;
      message.textContent = t("devices.failed", { detail });
      message.classList.add("error");
      return;
    }

    // Output devices only: audio_switch switches the render role.
    const outputs = (result.data.devices || []).filter((device) => device.direction === "Render");
    populateDeviceSelect(primary, outputs, draft.devices.primary, true);
    populateDeviceSelect(secondary, outputs, draft.devices.secondary, true);
    draft.devices.loaded = true;
    message.textContent = outputs.length ? "" : t("devices.empty");
  }

  function cityField() {
    const { draft } = state;
    const value = draft.fieldValues.city;
    const searchId = uid("city");
    const current = h("p", { class: "field-hint" });
    const results = h("ul", { class: "search-results", hidden: true });
    const message = h("p", { class: "message", role: "status" });
    const latInput = h("input", { class: "input", type: "number", step: "0.01", min: "-90", max: "90", value: value.lat, "aria-label": t("field.lat") });
    const lonInput = h("input", { class: "input", type: "number", step: "0.01", min: "-180", max: "180", value: value.lon, "aria-label": t("field.lon") });

    function syncCurrent() {
      const has = value.lat !== "" && value.lon !== "";
      current.textContent = has
        ? t("city.current", { city: value.city || "—", lat: value.lat, lon: value.lon })
        : t("city.notSet");
    }
    syncCurrent();

    latInput.addEventListener("input", () => { value.lat = latInput.value; syncCurrent(); });
    lonInput.addEventListener("input", () => { value.lon = lonInput.value; syncCurrent(); });

    const search = h("input", {
      id: searchId,
      class: "input",
      type: "search",
      value: value.city,
      placeholder: t("placeholder.city"),
      autocomplete: "off",
      onKeydown: (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          runSearch();
        }
      },
    });

    async function runSearch() {
      const query = search.value.trim();
      if (query.length < 2) {
        message.textContent = t("city.tooShort");
        return;
      }
      const seq = ++citySearchSeq;
      message.textContent = t("city.searching");
      message.classList.remove("error");
      results.hidden = true;
      const result = await ctx.api(`/api/widgets/geocode?q=${encodeURIComponent(query)}&lang=${LANG}`);
      if (seq !== citySearchSeq || !message.isConnected) return;
      if (!result.ok) {
        message.textContent = t("city.failed", { detail: result.detail });
        message.classList.add("error");
        return;
      }
      if (!result.data.length) {
        message.textContent = t("city.none");
        return;
      }
      message.textContent = "";
      results.replaceChildren(
        ...result.data.map((place) =>
          h("li", {}, [
            h("button", {
              type: "button",
              onClick: () => {
                value.city = place.name;
                value.lat = Math.round(place.lat * 100) / 100;
                value.lon = Math.round(place.lon * 100) / 100;
                latInput.value = value.lat;
                lonInput.value = value.lon;
                search.value = place.name;
                results.hidden = true;
                syncCurrent();
                search.focus();
              },
            }, [place.name, " ", h("small", { text: [place.admin1, place.country].filter(Boolean).join(", ") })]),
          ]),
        ),
      );
      results.hidden = false;
    }

    return h("div", { class: "field field-highlight" }, [
      h("label", { class: "field-label", for: searchId, text: t("field.city") }),
      h("div", { class: "search-row" }, [
        search,
        h("button", { type: "button", class: "btn", text: t("city.find"), onClick: runSearch }),
      ]),
      message,
      results,
      current,
      h("details", { class: "advanced" }, [
        h("summary", { text: t("city.manual") }),
        h("div", { class: "field-row" }, [
          h("div", { class: "field" }, [h("span", { class: "field-hint", text: t("field.lat") }), latInput]),
          h("div", { class: "field" }, [h("span", { class: "field-hint", text: t("field.lon") }), lonInput]),
          h("div", { class: "field" }, [
            h("span", { class: "field-hint", text: t("field.cityName") }),
            h("input", { class: "input", value: value.city, onInput: (e) => { value.city = e.target.value; syncCurrent(); } }),
          ]),
        ]),
      ]),
    ]);
  }

  function renderAppearanceStep() {
    const { entry, draft } = state;
    const children = [
      textField(t("field.label"), draft.label, (value) => { draft.label = value; }, {
        hint: draft.kind === "widget" ? t("hint.widgetLabel") : null,
        required: true,
      }),
    ];

    if (draft.kind !== "widget") {
      children.push(iconField());
    }

    children.push(colorField());

    // State colours only mean something on a tile that reports state.
    if (draft.kind === "action" && draft.stateKey) {
      for (const field of entry.fields.filter((f) => f.section === "appearance")) {
        children.push(renderField(field));
      }
      children.push(
        h("div", { class: "field" }, [
          h("span", { class: "field-label", text: t("field.stateColors") }),
          h("p", { class: "field-hint", text: t("hint.stateColors") }),
          stateColorRow("active_color", t("color.active"), DEFAULT_COLORS.active_color),
          stateColorRow("alert_color", t("color.alert"), DEFAULT_COLORS.alert_color),
          stateColorRow("false_color", t("color.off"), draft.color),
        ]),
      );
    }

    return step(3, t("step.appearance"), children);
  }

  // Four kinds of icon, one tab each: the stroke glyphs, brand logos, up to
  // three characters of text, and the site's own icon fetched by the agent.
  function iconField() {
    const { draft } = state;
    const mode = iconMode(draft.icon);
    const labelId = uid("icons");
    const modes = ["symbol", "brand", "text", "image"];

    const tabs = h("div", { class: "segmented", role: "group", "aria-labelledby": labelId },
      modes.map((m) =>
        h("button", {
          type: "button",
          "aria-pressed": String(m === mode),
          class: m === mode ? "is-active" : "",
          text: t(`icon.mode.${m}`),
          onClick: () => {
            if (m === mode) return;
            if (m === "symbol") draft.icon = hasEntry(ICONS, state.entry.icon) ? state.entry.icon : "";
            else if (m === "brand") draft.icon = `${BRAND_PREFIX}${Object.keys(BRAND_ICONS)[0]}`;
            else if (m === "text") draft.icon = ICON_TEXT;
            else draft.icon = ICON_IMAGE;
            render();
          },
        }),
      ),
    );

    const radioGrid = (entries, isChecked) => {
      const name = uid("icon");
      return h("div", { class: "icon-picker", role: "radiogroup", "aria-labelledby": labelId },
        entries.map(([key, svg, title]) =>
          h("label", { class: "icon-choice", title }, [
            h("input", {
              type: "radio",
              name,
              value: key,
              checked: isChecked(key),
              "aria-label": title,
              onChange: () => { draft.icon = key; },
            }),
            svg ? h("span", { html: svg }) : h("span", { text: "∅" }),
          ]),
        ),
      );
    };

    let panel;
    if (mode === "symbol") {
      panel = radioGrid(
        [["", null, t("icon.none")], ...Object.entries(ICONS).map(([key, svg]) => [key, svg, key])],
        (key) => draft.icon === key || (!key && !hasEntry(ICONS, draft.icon)),
      );
    } else if (mode === "brand") {
      panel = radioGrid(
        Object.entries(BRAND_ICONS).map(([key, svg]) => [`${BRAND_PREFIX}${key}`, svg, BRAND_NAMES[key]]),
        (key) => draft.icon === key,
      );
    } else if (mode === "text") {
      panel = h("div", { class: "field" }, [
        h("input", {
          class: "input icon-text-input",
          value: draft.iconText,
          maxlength: "8",
          autocomplete: "off",
          "aria-label": t("icon.mode.text"),
          placeholder: "TG",
          onInput: (event) => { draft.iconText = event.target.value; },
        }),
        h("p", { class: "field-hint", text: t("icon.textHint") }),
      ]);
    } else {
      const preview = h("div", { class: "icon-image-preview" });
      const message = h("p", { class: "message", role: "status" });
      const showPreview = () => {
        preview.replaceChildren(
          draft.iconImg ? h("img", { alt: "", src: draft.iconImg }) : h("span", { class: "field-hint", text: t("icon.imageNone") }),
        );
      };
      showPreview();
      const fetchIcon = async () => {
        const url = cleanUrl(draft.fieldValues.url);
        if (!/^https?:/.test(url)) {
          message.textContent = t("icon.needUrl");
          message.classList.add("error");
          return;
        }
        const seq = ++iconRequestSeq;
        message.textContent = t("icon.fetching");
        message.classList.remove("error");
        const result = await ctx.api(`/api/agents/${encodeURIComponent(draft.target)}/fetch_icon`, { method: "POST", body: { url } });
        if (seq !== iconRequestSeq || !message.isConnected) return;
        if (!result.ok || !result.data || result.data.status !== "ok") {
          const detail = result.ok ? (result.data && result.data.message) || "agent error" : result.detail;
          message.textContent = t("icon.fetchFailed", { detail });
          message.classList.add("error");
          return;
        }
        if (!isIconImage(result.data.icon)) {
          message.textContent = t("icon.fetchFailed", { detail: "bad image" });
          message.classList.add("error");
          return;
        }
        draft.iconImg = result.data.icon;
        message.textContent = "";
        showPreview();
      };
      const canFetch = state.entry.fields.some((field) => field.control === "url");
      panel = h("div", { class: "field" }, [
        h("div", { class: "icon-image-row" }, [
          preview,
          canFetch ? h("button", { type: "button", class: "btn", text: t("icon.fetch"), onClick: fetchIcon }) : null,
        ]),
        canFetch ? null : h("p", { class: "field-hint", text: t("icon.imageOnlyWebsite") }),
        message,
      ]);
    }

    return h("div", { class: "field" }, [
      h("span", { class: "field-label", id: labelId, text: t("field.icon") }),
      tabs,
      panel,
    ]);
  }

  function colorField() {
    const { draft } = state;
    const id = uid("color");
    const input = h("input", { id, type: "color", value: normalizeHex(draft.color) });
    const swatchButtons = SWATCHES.map((hex) =>
      h("button", {
        type: "button",
        class: "swatch",
        style: `background:${hex}`,
        "aria-label": hex,
        "aria-pressed": String(hex.toLowerCase() === String(draft.color).toLowerCase()),
        onClick: () => setColor(hex),
      }),
    );
    function setColor(hex) {
      draft.color = hex;
      input.value = normalizeHex(hex);
      for (const button of swatchButtons) {
        button.setAttribute("aria-pressed", String(button.getAttribute("aria-label") === hex));
      }
    }
    input.addEventListener("input", () => setColor(input.value));
    return h("div", { class: "field" }, [
      h("label", { class: "field-label", for: id, text: t("field.color") }),
      h("div", { class: "color-row" }, [input, h("div", { class: "swatches" }, swatchButtons)]),
    ]);
  }

  function stateColorRow(key, label, fallback) {
    const { draft } = state;
    const explicit = draft.colors[key] !== null;
    const picker = h("input", {
      type: "color",
      value: normalizeHex(explicit ? draft.colors[key] : fallback),
      disabled: !explicit,
      "aria-label": label,
      onInput: (event) => { draft.colors[key] = event.target.value; },
    });
    const themeBox = h("input", {
      type: "checkbox",
      checked: !explicit,
      onChange: (event) => {
        picker.disabled = event.target.checked;
        draft.colors[key] = event.target.checked ? null : picker.value;
      },
    });
    return h("div", { class: "color-row" }, [
      picker,
      h("span", { text: label, style: "min-width: 90px" }),
      h("label", { class: "check field-hint" }, [themeBox, t("color.theme")]),
    ]);
  }

  function renderPlacementStep() {
    const { draft, entry } = state;
    const barAllowed = draft.kind === "action" && !(entry.minWidth > 1);
    const where = barAllowed
      ? h("div", { class: "field" }, [
          h("span", { class: "field-label", text: t("dock.where") }),
          h("div", { class: "segmented", role: "group" }, [
            ["grid", false],
            ["dock", true],
          ].map(([key, value]) =>
            h("button", {
              type: "button",
              "aria-pressed": String(draft.dock === value),
              class: draft.dock === value ? "is-active" : "",
              text: t(`dock.where.${key}`),
              onClick: () => {
                if (draft.dock === value) return;
                setDock(value);
                render();
                // After render(): it rebuilds the form, message line included.
                if (value && !draft.dock) setMessage(t("dock.full"));
              },
            }),
          )),
        ])
      : null;

    if (draft.dock) {
      return step(4, t("step.placement"), [
        where,
        textField(t("dock.position"), Number(draft.col) + 1, (value) => {
          draft.col = value === "" ? "" : Number(value) - 1;
        }, { type: "number", min: "1" }),
        h("p", { class: "field-hint", text: t("dock.hint") }),
      ]);
    }
    // Row and column are shown counting from 1, the way a person counts
    // cells. The API and the database count from 0.
    const num = (label, key, min, offset = 0) =>
      textField(label, draft[key] === "" ? "" : Number(draft[key]) + offset, (value) => {
        draft[key] = value === "" ? "" : Number(value) - offset;
      }, { type: "number", min: String(min) });
    return step(4, t("step.placement"), [
      where,
      h("div", { class: "field-row" }, [
        num(t("field.width"), "width", entry.minWidth || 1),
        num(t("field.height"), "height", 1),
        num(t("field.row"), "row", 1, 1),
        num(t("field.col"), "col", 1, 1),
      ]),
      h("p", { class: "field-hint", text: entry.minWidth ? t("hint.minWidth", { n: entry.minWidth }) : t("hint.placement") }),
    ]);
  }

  function renderAdvanced() {
    const { entry, draft } = state;
    const advancedFields = entry.fields.filter((field) => field.advanced).map(renderField);
    const targets = [...new Set([...ctx.targets(), draft.target].filter(Boolean))];
    const textareaId = uid("json");

    const targetField = selectField(t("field.target"), targets, draft.target, (value) => {
      draft.target = value;
      // A different agent has different devices.
      if (entry.fields.some((field) => field.control === "devices")) render();
    }, (v) => v);

    return h("details", { class: "advanced step", open: state.paramsInvalid || entry.id === CUSTOM_ID }, [
      h("summary", { text: t("step.advanced") }),
      advancedFields.length ? h("div", { class: "field-grid" }, advancedFields) : null,
      h("div", { class: "field-grid" }, [
        targetField,
        textField(t("field.stateKey"), draft.stateKey, (value) => { draft.stateKey = value.trim(); }, { hint: t("hint.stateKey"), mono: true }),
      ]),
      h("div", { class: "field" }, [
        h("label", { class: "field-label", for: textareaId, text: t("field.extraParams") }),
        h("textarea", { id: textareaId, spellcheck: "false", value: draft.extraText, onInput: (e) => { draft.extraText = e.target.value; } }),
        h("p", { class: "field-hint", text: t("hint.extraParams") }),
      ]),
    ]);
  }

  // ── Save / delete ────────────────────────────────────────────────────────

  function setMessage(text, isError = true) {
    const message = root.querySelector("#inspector-message");
    if (!message) return;
    message.textContent = text;
    message.classList.toggle("error", isError);
  }

  function buildBody() {
    const { entry, draft, originalParams } = state;

    if (!draft.label.trim()) return { error: t("error.label") };
    if (entry.id === CUSTOM_ID && !String(draft.type || "").trim()) return { error: t("error.type") };

    let extra;
    try {
      extra = draft.extraText.trim() ? JSON.parse(draft.extraText) : {};
      if (typeof extra !== "object" || extra === null || Array.isArray(extra)) throw new Error(t("error.jsonObject"));
    } catch (err) {
      return { error: t("error.json", { detail: err.message }) };
    }

    const params = { ...extra, ...entry.fixedParams };

    for (const field of entry.fields) {
      const value = draft.fieldValues[field.param];
      switch (field.control) {
        case "path": {
          const path = cleanPath(value);
          if (field.required && !path) return { error: t("error.path", { field: t(`field.${field.param}`) }) };
          if (path) params[field.param] = path;
          break;
        }
        case "url": {
          const typed = String(value || "").trim();
          const url = cleanUrl(typed);
          if (field.required && !typed) return { error: t("error.required", { field: t(`field.${field.param}`) }) };
          if (typed && !url) return { error: t("error.url") };
          if (url) params[field.param] = url;
          break;
        }
        case "text": {
          const text = String(value || "").trim();
          if (field.required && !text) return { error: t("error.required", { field: t(`field.${field.param}`) }) };
          if (text) params[field.param] = text;
          break;
        }
        case "select":
          params[field.param] = value ?? field.default;
          break;
        case "toggle":
          if (value) params[field.param] = true;
          break;
        case "devices":
          for (const [key, picked] of [[PRIMARY_PARAM, draft.devices.primary], [SECONDARY_PARAM, draft.devices.secondary]]) {
            if (picked) {
              params[key] = picked;
            } else if (!draft.devices.loaded && originalParams[key]) {
              // The list never loaded, so "none" here means "unknown".
              params[key] = originalParams[key];
            }
          }
          break;
        case "city": {
          const { city, lat, lon } = draft.fieldValues.city;
          const hasLat = lat !== "" && lat !== null;
          const hasLon = lon !== "" && lon !== null;
          if (hasLat !== hasLon) return { error: t("error.coords") };
          if (hasLat) {
            const latN = Number(lat);
            const lonN = Number(lon);
            if (!(latN >= -90 && latN <= 90 && lonN >= -180 && lonN <= 180)) return { error: t("error.coords") };
            params.lat = latN;
            params.lon = lonN;
            if (String(city).trim()) params.city = String(city).trim();
          }
          break;
        }
        default:
          break;
      }
    }

    for (const key of COLOR_PARAMS) {
      if (draft.colors[key] !== null) params[key] = draft.colors[key];
    }

    // Icon params live only as long as the icon kind that uses them.
    for (const key of ICON_PARAMS) delete params[key];
    if (draft.kind !== "widget") {
      if (draft.icon === ICON_TEXT) {
        if (!iconText(draft.iconText)) return { error: t("error.iconText") };
        params.icon_text = iconText(draft.iconText);
      } else if (draft.icon === ICON_IMAGE) {
        if (!draft.iconImg) return { error: t("error.iconImage") };
        params.icon_img = draft.iconImg;
      }
    }

    for (const key of ["width", "height", "row", "col"]) {
      if (draft[key] === "" || !Number.isInteger(Number(draft[key]))) return { error: t("error.placement") };
    }
    if (entry.minWidth && Number(draft.width) < entry.minWidth) {
      return { error: t("hint.minWidth", { n: entry.minWidth }) };
    }

    return {
      body: {
        workspace_id: Number(draft.workspaceId),
        row: Number(draft.row),
        col: Number(draft.col),
        width: Number(draft.width),
        height: Number(draft.height),
        label: draft.label.trim(),
        icon: draft.kind === "widget" ? state.item?.icon ?? null : draft.icon || null,
        color: draft.color,
        kind: draft.kind,
        type: String(draft.type).trim(),
        target: draft.target,
        params: JSON.stringify(params),
        state_key: draft.stateKey || null,
        dock: Boolean(draft.dock),
      },
    };
  }

  async function save() {
    const { body, error } = buildBody();
    if (error) {
      setMessage(error);
      return;
    }
    setMessage(t("common.saving"), false);
    const id = state.item ? state.item.id : null;
    const result = await ctx.api(id ? `/api/items/${id}` : "/api/items", { method: id ? "PUT" : "POST", body });
    if (!state) return;
    if (!result.ok) {
      setMessage(t("error.save", { detail: result.detail }));
      return;
    }
    const saved = result.data;
    close({ restoreFocus: false });
    ctx.onSaved(saved);
  }

  async function remove() {
    const { item } = state;
    if (!confirm(t("confirm.delete", { label: item.label }))) return;
    const result = await ctx.api(`/api/items/${item.id}`, { method: "DELETE" });
    if (!result.ok) {
      setMessage(t("error.delete", { detail: result.detail }));
      return;
    }
    close({ restoreFocus: false });
    ctx.onDeleted(item);
  }

  renderEmpty();

  return {
    open,
    close,
    isOpen: () => state !== null,
    currentItemId: () => (state && state.item ? state.item.id : null),
    currentCell: () => (state && !state.item ? { row: state.draft.row, col: state.draft.col } : null),
  };
}

// <input type="color"> only accepts #rrggbb.
function normalizeHex(value) {
  const text = String(value || "").trim();
  if (/^#[0-9a-f]{6}$/i.test(text)) return text.toLowerCase();
  const short = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(text);
  if (short) return `#${short[1]}${short[1]}${short[2]}${short[2]}${short[3]}${short[3]}`.toLowerCase();
  return "#2a2f38";
}
