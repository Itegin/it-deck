const tbody = document.getElementById("items-tbody");
const form = document.getElementById("item-form");
const paramsError = document.getElementById("params-error");
const devicesMessage = document.getElementById("devices-message");
// Compact layout's target workspace. Kept out of `fields` on purpose --
// that map is the item-edit form, and this picker exists precisely so
// Compact no longer depends on it.
const compactWorkspaceSelect = document.getElementById("compact-workspace-select");

const deviceLabels = {
  primary: document.getElementById("primary-device-label"),
  secondary: document.getElementById("secondary-device-label"),
};

const fields = {
  id: document.getElementById("field-id"),
  workspaceId: document.getElementById("field-workspace-id"),
  label: document.getElementById("field-label"),
  icon: document.getElementById("field-icon"),
  color: document.getElementById("field-color"),
  activeColor: document.getElementById("field-active-color"),
  alertColor: document.getElementById("field-alert-color"),
  falseColor: document.getElementById("field-false-color"),
  kind: document.getElementById("field-kind"),
  type: document.getElementById("field-type"),
  target: document.getElementById("field-target"),
  stateKey: document.getElementById("field-state-key"),
  row: document.getElementById("field-row"),
  col: document.getElementById("field-col"),
  width: document.getElementById("field-width"),
  height: document.getElementById("field-height"),
  params: document.getElementById("field-params"),
  primaryDevice: document.getElementById("field-primary-device"),
  secondaryDevice: document.getElementById("field-secondary-device"),
};

const AUDIO_SWITCH_TYPE = "audio_switch";

// The two params keys the device pickers write. Named after the env vars
// handle_audio_switch (agents/windows/handlers/audio.py) actually reads:
// today it reads OUTPUT_DEVICE_PRIMARY/SECONDARY from os.getenv() only and
// has no per-item params path at all, so nothing consumes these keys yet.
// Teaching the agent to prefer params over .env is a deliberate follow-up;
// no Windows agent file is touched by this change.
const PRIMARY_PARAM = "output_device_primary";
const SECONDARY_PARAM = "output_device_secondary";

// Bumped on every load and on every hide/close, so a slow reply for a Target
// (or a Type) the user has since moved away from can't repopulate the
// dropdowns with the wrong agent's devices.
let deviceRequestSeq = 0;

// Fallback only. New items follow compactWorkspaceSelect (i.e. whatever the
// table is currently filtered to); this is what's used before that picker has
// a value -- whichever workspace loaded first (position=0, i.e. "Home" today).
let defaultWorkspaceId = null;

// Last /api/workspaces response, kept so switching the workspace filter can
// re-render the table from memory instead of refetching.
let allWorkspaces = [];

// Studio Mode has no login UI, so the agent token is collected once via a
// plain prompt() and kept in memory only for the rest of this page load --
// never localStorage/sessionStorage, per this project's convention of
// avoiding browser storage APIs. Resetting on reload is an accepted
// tradeoff for a desktop-only admin page, not an oversight.
let agentToken = null;

function getAgentToken() {
  if (agentToken === null) {
    agentToken = prompt("Agent token (X-Agent-Token) for Studio Mode:") || "";
  }
  return agentToken;
}

async function loadItems() {
  const response = await fetch("/api/workspaces");
  const workspaces = await response.json();
  defaultWorkspaceId = workspaces[0] ? workspaces[0].id : null;
  allWorkspaces = workspaces;
  populateWorkspaceSelect(workspaces);
  // Before renderFilteredTable(), which reads the picker's live value to
  // decide what to show.
  populateCompactWorkspaceSelect(workspaces);
  renderFilteredTable();
}

// The table shows one workspace at a time -- the one the toolbar picker is
// on. Reads allWorkspaces, never refetches, so switching the picker is
// instant. Falls back to the first workspace when the picker has no value
// yet, the same default populateWorkspaceSelect/defaultWorkspaceId use.
function renderFilteredTable() {
  const selected = compactWorkspaceSelect.value;
  // String() on both sides for the same reason populateCompactWorkspaceSelect
  // does it: workspace.id is a number, a select's value is always a string.
  const workspace =
    allWorkspaces.find((candidate) => String(candidate.id) === selected) || allWorkspaces[0];
  // Kept as the raw params string here (unlike the dashboard's
  // fetchWorkspaces in api.js, which parses it) -- the form's textarea
  // and the CRUD endpoints both want the JSON string form directly.
  renderTable(workspace ? workspace.items : []);
}

function populateWorkspaceSelect(workspaces) {
  fields.workspaceId.innerHTML = "";
  for (const workspace of workspaces) {
    const option = document.createElement("option");
    option.value = workspace.id;
    option.textContent = workspace.name;
    fields.workspaceId.appendChild(option);
  }
}

// Deliberately a separate function from populateWorkspaceSelect rather than a
// shared helper, so nothing about the item form's own dropdown changes.
// compactLayout() ends with loadItems(), which lands back here -- so the
// current pick is captured and restored, otherwise compacting one workspace
// would silently re-point the picker at the first one for the next press.
function populateCompactWorkspaceSelect(workspaces) {
  const previous = compactWorkspaceSelect.value;
  compactWorkspaceSelect.innerHTML = "";
  for (const workspace of workspaces) {
    const option = document.createElement("option");
    option.value = workspace.id;
    option.textContent = workspace.name;
    compactWorkspaceSelect.appendChild(option);
  }
  if (previous && workspaces.some((workspace) => String(workspace.id) === previous)) {
    compactWorkspaceSelect.value = previous;
  }
}

function renderTable(items) {
  tbody.innerHTML = "";
  for (const item of items) {
    const tr = document.createElement("tr");

    for (const value of [item.label, item.type, item.target, `${item.row},${item.col}`]) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    }

    const actionsTd = document.createElement("td");
    // Same flex-row-with-a-gap idiom as .toolbar and #item-form
    // .form-actions -- without it Edit and Delete sit flush, which is
    // worst for the destructive one of the pair.
    actionsTd.className = "row-actions";

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.textContent = "Edit";
    editBtn.addEventListener("click", () => openForm(item));

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "danger";
    deleteBtn.textContent = "Delete";
    deleteBtn.addEventListener("click", () => deleteItem(item));

    actionsTd.append(editBtn, deleteBtn);
    tr.appendChild(actionsTd);

    tbody.appendChild(tr);
  }
}

// Used only to pre-fill the active/alert color pickers -- malformed
// params shouldn't block opening the form (the params textarea itself
// still shows the raw string, errors included, for the user to fix).
function parseParamsLoosely(paramsString) {
  try {
    return JSON.parse(paramsString) || {};
  } catch {
    return {};
  }
}

function isAudioSwitch() {
  return fields.type.value.trim() === AUDIO_SWITCH_TYPE;
}

function setDevicesMessage(text, isError) {
  devicesMessage.textContent = text;
  devicesMessage.classList.toggle("error", Boolean(isError));
  devicesMessage.hidden = !text;
}

function currentDeviceSelection() {
  return {
    primary: fields.primaryDevice.value,
    secondary: fields.secondaryDevice.value,
  };
}

// devicesLoaded=false is the pre-load/failed state: `devices` being empty
// then means "no list to compare against", not "the agent doesn't have it",
// and the saved option is labelled accordingly.
function populateDeviceSelect(select, devices, selectedId, devicesLoaded = true) {
  select.innerHTML = "";

  const noneOption = document.createElement("option");
  noneOption.value = "";
  noneOption.textContent = "— none —";
  select.appendChild(noneOption);

  for (const device of devices) {
    const option = document.createElement("option");
    // The value is SoundVolumeView's "Command-Line Friendly ID" verbatim --
    // the exact string /SwitchDefault expects. Never shortened or parsed.
    option.value = device.id;
    // Name alone is not a unique label: two endpoints on the same machine
    // can both be called "Микрофон", so the direction is part of the label,
    // not decoration.
    option.textContent = `${device.name} (${device.direction})`;
    select.appendChild(option);
  }

  // A saved ID can be absent from the list -- device unplugged, or the item
  // was configured against a different agent. Show it as a selected option
  // rather than letting select.value silently fall back to "none", which
  // would read as "never configured" and quietly drop the ID on save.
  if (selectedId && !devices.some((device) => device.id === selectedId)) {
    const missing = document.createElement("option");
    missing.value = selectedId;
    missing.textContent = devicesLoaded
      ? `${selectedId} (not in this agent's list)`
      : `${selectedId} (saved)`;
    select.appendChild(missing);
  }

  select.value = selectedId || "";
}

async function loadDevices(target, selected) {
  const seq = ++deviceRequestSeq;
  setDevicesMessage("Loading devices…", false);

  // Clear to just the saved selection up front: whatever the previous Target
  // returned is wrong for this one, and every failure path below leaves the
  // pickers in this state -- empty but usable, per the "must not block the
  // rest of the form" requirement.
  populateDeviceSelect(fields.primaryDevice, [], selected.primary, false);
  populateDeviceSelect(fields.secondaryDevice, [], selected.secondary, false);

  let response;
  try {
    response = await fetch(`/api/agents/${encodeURIComponent(target)}/list_devices`, {
      method: "POST",
      headers: { "X-Agent-Token": getAgentToken() },
    });
  } catch (err) {
    if (seq !== deviceRequestSeq) {
      return;
    }
    setDevicesMessage(`Could not load devices: ${err.message}`, true);
    return;
  }

  if (seq !== deviceRequestSeq) {
    return;
  }

  if (!response.ok) {
    // 404 = agent offline, 504 = agent didn't answer inside the backend's 5s
    // budget, 401 = missing/wrong token. The endpoint's `detail` already
    // phrases each of these for a human ("agent offline").
    const error = await response.json().catch(() => ({}));
    setDevicesMessage(
      `Could not load devices: ${error.detail || `HTTP ${response.status}`}`,
      true,
    );
    return;
  }

  const result = await response.json();
  if (seq !== deviceRequestSeq) {
    return;
  }

  // A 200 still carries the agent's own outcome: the endpoint returns the
  // agent's reply verbatim, so SoundVolumeView failing on a live, responsive
  // agent arrives here as {"status": "error"} with HTTP 200.
  if (result.status !== "ok") {
    setDevicesMessage(`Could not load devices: ${result.message || "agent reported an error"}`, true);
    return;
  }

  // Render only: handle_audio_switch drives `/SwitchDefault primary secondary
  // 0`, and that trailing 0 is the render/multimedia role -- a capture
  // endpoint is not a valid choice for it, so inputs are filtered out rather
  // than offered and left to fail at execute time.
  const outputs = (result.devices || []).filter((device) => device.direction === "Render");
  populateDeviceSelect(fields.primaryDevice, outputs, selected.primary);
  populateDeviceSelect(fields.secondaryDevice, outputs, selected.secondary);

  setDevicesMessage(outputs.length ? "" : "This agent reported no output devices.", false);
}

// Shows/hides via the .hidden property, the same idiom form/paramsError
// already use on this page, rather than a new class or style toggle.
function syncDeviceFields(selected) {
  const show = isAudioSwitch() && Boolean(fields.target.value);
  deviceLabels.primary.hidden = !show;
  deviceLabels.secondary.hidden = !show;

  if (!show) {
    deviceRequestSeq++;
    setDevicesMessage("", false);
    // Cleared, not merely hidden. Only loadDevices resets these selects, and
    // it doesn't run on this branch -- so without this they keep the
    // previously edited item's options and selected IDs. Editing a plain
    // item's Type into "audio_switch" afterwards would then read those stale
    // IDs back out via currentDeviceSelection() and save another item's
    // devices into this one's params.
    populateDeviceSelect(fields.primaryDevice, [], "", false);
    populateDeviceSelect(fields.secondaryDevice, [], "", false);
    return;
  }

  loadDevices(fields.target.value, selected);
}

function openForm(item) {
  paramsError.hidden = true;

  // Carried down to syncDeviceFields rather than assigned to the selects
  // here: the <option>s don't exist until the agent's device list arrives.
  let savedDevices = { primary: "", secondary: "" };

  if (item) {
    fields.id.value = item.id;
    fields.workspaceId.value = item.workspace_id;
    fields.label.value = item.label;
    fields.icon.value = item.icon || "";
    fields.color.value = item.color || "#2a2f38";
    fields.kind.value = item.kind;
    fields.type.value = item.type;
    fields.target.value = item.target;
    fields.stateKey.value = item.state_key || "";
    fields.row.value = item.row;
    fields.col.value = item.col;
    fields.width.value = item.width;
    fields.height.value = item.height;
    // active_color/alert_color live inside params, not their own DB
    // columns -- pre-fill from there, falling back to the same
    // hardcoded teal/red the dashboard itself falls back to, so an
    // untouched item's picker reflects what's actually rendering now.
    const existingParams = parseParamsLoosely(item.params);
    // Pretty-print for readability; fall back to the raw string if it
    // somehow isn't valid JSON so a malformed value is still visible/editable.
    try {
      fields.params.value = JSON.stringify(JSON.parse(item.params), null, 2);
    } catch {
      fields.params.value = item.params;
    }
    fields.activeColor.value = existingParams.active_color || "#0d9488";
    fields.alertColor.value = existingParams.alert_color || "#dc2626";
    // Unlike active/alert (which fall back to a fixed hardcoded color when
    // unset), the false-state fallback is item.color itself -- a
    // per-item, not fixed, value. Pre-filling with that same value (not
    // a fixed constant) means saving unchanged writes back the color
    // that was already rendering, so untouched items stay visually
    // identical even though false_color now has a concrete value in params.
    fields.falseColor.value = existingParams.false_color || item.color || "#2a2f38";
    savedDevices = {
      primary: existingParams[PRIMARY_PARAM] || "",
      secondary: existingParams[SECONDARY_PARAM] || "",
    };
  } else {
    form.reset();
    fields.id.value = "";
    // The workspace the table is currently filtered to, not whichever one
    // loaded first: with filtering on, a new item defaulting to a workspace
    // the user isn't looking at would save and then immediately vanish from
    // the table. Safe to read after form.reset() -- the picker lives in the
    // toolbar, outside #item-form, so reset() doesn't touch it.
    fields.workspaceId.value = compactWorkspaceSelect.value || (defaultWorkspaceId ?? "");
    fields.color.value = "#2a2f38";
    fields.activeColor.value = "#0d9488";
    fields.alertColor.value = "#dc2626";
    fields.falseColor.value = "#2a2f38";
    fields.width.value = 1;
    fields.height.value = 1;
    fields.params.value = "{}";
  }

  form.hidden = false;
  // After form.hidden = false, so "Loading devices…" lands on a form the
  // user can already see.
  syncDeviceFields(savedDevices);
}

function closeForm() {
  form.hidden = true;
  paramsError.hidden = true;
  // Same reason syncDeviceFields bumps it: a reply still in flight when the
  // form closes must not populate it for whatever item is opened next.
  deviceRequestSeq++;
  setDevicesMessage("", false);
}

async function deleteItem(item) {
  if (!confirm(`Delete "${item.label}"?`)) {
    return;
  }
  const response = await fetch(`/api/items/${item.id}`, {
    method: "DELETE",
    headers: { "X-Agent-Token": getAgentToken() },
  });
  if (!response.ok) {
    // Adding auth here means delete can now fail (e.g. wrong/empty token,
    // 401) in a way it never could before -- silently reloading the table
    // as if it worked would hide that from the user.
    const error = await response.json().catch(() => ({}));
    alert(`Delete failed (${response.status}): ${error.detail || "unknown error"}`);
    return;
  }
  await loadItems();
}

async function compactLayout() {
  // Read from the toolbar's own picker, never the item form's -- the form
  // field only holds a meaningful value once an Edit view has been opened
  // this page load, so Compact used to target a workspace the user never
  // chose and had no way to see.
  const workspaceId = compactWorkspaceSelect.value;
  if (!workspaceId) {
    return;
  }
  // Still named in the prompt even though the picker is now visible right
  // next to the button: this is a non-undoable rewrite, so the dialog says
  // exactly which workspace is about to be repacked.
  const workspaceName = compactWorkspaceSelect.selectedOptions[0]?.textContent || workspaceId;
  if (!confirm(`Repack all tiles in "${workspaceName}" into the top-left? This rewrites their positions and can't be undone.`)) {
    return;
  }
  const response = await fetch(`/api/workspaces/${workspaceId}/compact`, {
    method: "POST",
    headers: { "X-Agent-Token": getAgentToken() },
  });
  if (!response.ok) {
    // Same handling as deleteItem(): a bad token (401) or missing
    // workspace (404) must surface, not silently reload as if it worked.
    const error = await response.json().catch(() => ({}));
    alert(`Compact failed (${response.status}): ${error.detail || "unknown error"}`);
    return;
  }
  // Same close-then-reload order as the submit handler. Mandatory here, not
  // cosmetic: an open form still holds the item's pre-compact row/col (and
  // loadItems() rebuilds the workspace <select>, resetting it to the first
  // option), so a Save afterwards would push the item back out of the packed
  // layout and into whichever workspace the reset dropdown landed on.
  closeForm();
  await loadItems();
}

document.getElementById("new-item-btn").addEventListener("click", () => openForm(null));
document.getElementById("compact-btn").addEventListener("click", compactLayout);
document.getElementById("cancel-btn").addEventListener("click", closeForm);

// Re-filters from allWorkspaces, no server round-trip. No risk of double
// rendering from populateCompactWorkspaceSelect's own `.value =`: assigning
// value programmatically doesn't fire "change".
compactWorkspaceSelect.addEventListener("change", renderFilteredTable);

// Type is a free-text input, not a select, so "input" is the event that
// catches it -- the pickers appear the moment the typed value reaches
// "audio_switch" exactly, and hide again on the next keystroke past it.
// Both handlers keep whatever is currently picked as the selection to
// restore, so re-resolving against a new Target doesn't discard it.
fields.type.addEventListener("input", () => syncDeviceFields(currentDeviceSelection()));
fields.target.addEventListener("change", () => syncDeviceFields(currentDeviceSelection()));

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  let paramsObj;
  try {
    paramsObj = JSON.parse(fields.params.value);
  } catch (err) {
    paramsError.textContent = `Params must be valid JSON: ${err.message}`;
    paramsError.hidden = false;
    return;
  }
  paramsError.hidden = true;

  // Merge the color pickers in last -- they're the source of truth for
  // active_color/alert_color/false_color, overriding whatever the raw
  // params textarea happened to have typed in for those same keys.
  paramsObj.active_color = fields.activeColor.value;
  paramsObj.alert_color = fields.alertColor.value;
  paramsObj.false_color = fields.falseColor.value;

  // Written only for audio_switch, and only when something is actually
  // selected. Unlike the color pickers above, an empty value here means
  // "unknown", not "none": if the device list failed to load (agent
  // offline), both selects are empty, and assigning unconditionally would
  // erase IDs the item already had. Clearing a device ID deliberately is
  // still possible by deleting the key in the raw params textarea.
  if (isAudioSwitch()) {
    if (fields.primaryDevice.value) {
      paramsObj[PRIMARY_PARAM] = fields.primaryDevice.value;
    }
    if (fields.secondaryDevice.value) {
      paramsObj[SECONDARY_PARAM] = fields.secondaryDevice.value;
    }
  }

  const paramsValue = JSON.stringify(paramsObj);

  const body = {
    workspace_id: Number(fields.workspaceId.value),
    row: Number(fields.row.value),
    col: Number(fields.col.value),
    width: Number(fields.width.value),
    height: Number(fields.height.value),
    label: fields.label.value,
    icon: fields.icon.value || null,
    color: fields.color.value,
    kind: fields.kind.value,
    type: fields.type.value,
    target: fields.target.value,
    params: paramsValue,
    state_key: fields.stateKey.value || null,
  };

  const id = fields.id.value;
  const url = id ? `/api/items/${id}` : "/api/items";
  const method = id ? "PUT" : "POST";

  const response = await fetch(url, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Agent-Token": getAgentToken(),
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    paramsError.textContent = `Save failed (${response.status}): ${error.detail || "unknown error"}`;
    paramsError.hidden = false;
    return;
  }

  closeForm();
  await loadItems();
});

loadItems();
