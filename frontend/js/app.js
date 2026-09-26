import { fetchWorkspaces, fetchSettings } from "./api.js";
import { renderWorkspace, renderWorkspaceSelector, renderError, renderDeckDots, markDeckEntrance, updateTileState, setAgentOffline, setConnectionDown, setTileCommandState, getTileMeta } from "./render.js";
import { sendExecute, sendSetValue, onCommandState, onStateChange, onAgentStatus, onConnectionChange, onWorkspaceUpdate, onSettingsUpdate, onAuthError } from "./ws.js";
import { initTheme, applyTheme, applyMode } from "./theme.js";
import { showContextMenu, showTextSheet } from "./contextmenu.js";
import { showToast } from "./toast.js";
import { maybeShowOnboarding } from "./onboarding.js";
import { attachDeckSwipe } from "./swipe.js";

// Device-local "which workspace does this deck show" choice. Deliberately
// not part of any server state -- multiple phones can point at different
// workspaces from the same backend.
const STORAGE_KEY = "itdeck:workspaceId";

// localStorage throws, rather than returning null, when site data is blocked
// or in some private modes. The saved deck is a convenience: without it the
// deck still draws, it just asks (or picks the only one) again.
function readSavedWorkspace() {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch (e) {
    return null;
  }
}

function saveWorkspace(id) {
  try {
    if (id === null) {
      localStorage.removeItem(STORAGE_KEY);
    } else {
      localStorage.setItem(STORAGE_KEY, String(id));
    }
  } catch (e) {
    // Not saved; this visit still shows the chosen deck.
  }
}

// The rows of the deck on screen, by id, for the tap handler below: it needs
// a tile's type and params, and render.js hands it only the id.
let itemsById = new Map();

// Types whose "Run?" row is red: the ones a mis-tap can't take back.
const DESTRUCTIVE_TYPES = new Set(["power", "process_toggle", "agent_shutdown"]);

// Same cap as the agent's (agents/windows/handlers/clipboard.py).
const CLIPBOARD_MAX_CHARS = 100000;

// A tap: most tiles just run, but two kinds ask first.
function handleTileTap(itemId) {
  const item = itemsById.get(itemId);
  if (!item) {
    sendExecute(itemId);
    return;
  }
  if (item.type === "clipboard_set") {
    showTextSheet(item, {
      placeholder: "Text to put on the PC's clipboard",
      sendLabel: "Send to PC",
      cancelLabel: "Cancel",
      maxLength: CLIPBOARD_MAX_CHARS,
      onSubmit: (text) => sendSetValue(itemId, text, { track: true }),
    });
    return;
  }
  // "Ask before running" (Studio): Run / Cancel first. Power, a program
  // on/off and Close Agent have it on by default -- tech debt #21.
  if (item.params && item.params.confirm) {
    showContextMenu(item, [
      { label: "Run", destructive: DESTRUCTIVE_TYPES.has(item.type), action: () => sendExecute(itemId) },
      { label: "Cancel", action: () => {} },
    ]);
    return;
  }
  sendExecute(itemId);
}

function handleTileLongPress(item) {
  showContextMenu(item, [
    {
      label: "Force Stop",
      destructive: true,
      action: () => sendExecute(item.id, { overrideType: "force_stop" }),
    },
    {
      label: "Cancel",
      action: () => {},
    },
  ]);
}

// Maps api.js's tagged failure kinds onto what the person holding the phone
// can actually do next. The four cases have four different fixes, so they
// get four different messages rather than one string for every failure.
function describeLoadFailure(err) {
  switch (err.kind) {
    case "unreachable":
      return `Can't reach the backend at ${window.location.host}. Check the backend is running and that this phone is on the same network.`;
    case "status":
      return `The backend answered HTTP ${err.message} for /api/workspaces. Check the backend log.`;
    case "badReply":
      return "The backend's reply wasn't valid JSON. Check the backend log.";
    case "badParams":
      return `${err.message} has invalid params JSON, so the deck can't be drawn. Fix its Params field in Studio.`;
    default:
      // Not an api.js failure at all -- a bug in the render path reaches
      // the same catch. Say so plainly instead of blaming the backend.
      return `Couldn't draw the deck: ${err.message}`;
  }
}

// The same job describeLoadFailure does, one layer down: turn the reason off
// the wire into something that says what happened and what to do next. The
// backend already phrases these for a human ("agent offline", "timeout"), but
// they describe the system's view, not the person's -- and none of them name
// the tile that was actually pressed.
function describeCommandFailure(itemId, message) {
  const meta = getTileMeta(itemId);
  const label = meta && meta.label ? meta.label : "That command";
  const agent = meta && meta.target ? meta.target : "its";

  switch (message) {
    case "agent offline":
      return `${label} didn't run — the ${agent} agent isn't connected. Start the IT-Deck Agent on that PC.`;
    case "timeout":
      // Deliberately not "it failed": the command reached the agent and the
      // reply is what went missing, so it may well have run.
      return `${label} didn't answer in time. It may still have run — check the PC before pressing again.`;
    case "item not found":
      return `${label} is no longer in the catalog. Reload the deck to pick up the change.`;
    default:
      return message ? `${label} failed: ${message}` : `${label} failed.`;
  }
}

// The decks from the last fetch, in order, and which one is on screen: what
// a swipe moves through.
let deckList = [];
let currentDeckId = null;

// Studio's "Switch decks by swiping". On until the server says otherwise, as
// it was before the setting existed; the dots switch decks either way.
let deckSwipeEnabled = true;

function loadWorkspace(workspace) {
  itemsById = new Map(workspace.items.map((item) => [item.id, item]));
  currentDeckId = workspace.id;
  renderWorkspace(workspace, handleTileTap, sendSetValue, handleTileLongPress);
  renderDeckDots(deckList, workspace.id, (target, index) => showDeck(target, index));
  // First deck this device has ever shown: the tour, once.
  maybeShowOnboarding();
}

// Moves to another deck and remembers it, as picking it from the list does.
// No wrap-around: at the last deck a swipe further does nothing, which is
// what the dots already say.
function showDeck(target, index) {
  const from = deckList.findIndex((w) => w.id === currentDeckId);
  if (!target || target.id === currentDeckId) {
    return;
  }
  saveWorkspace(target.id);
  loadWorkspace(target);
  markDeckEntrance(index > from ? 1 : -1);
}

// The next surviving deck after `goneId` in the old order, else the nearest
// one before it; null when this phone never saw that deck in a list.
function successorOf(oldList, goneId, newList) {
  const index = oldList.findIndex((w) => String(w.id) === goneId);
  if (index < 0) {
    return null;
  }
  const byId = new Map(newList.map((w) => [w.id, w]));
  const candidates = [...oldList.slice(index + 1), ...oldList.slice(0, index).reverse()];
  const hit = candidates.find((w) => byId.has(w.id));
  return hit ? byId.get(hit.id) : null;
}

function switchDeck(step) {
  // Only from a deck: on the picker or an error screen there is no "next".
  if (currentDeckId === null) {
    return;
  }
  const index = deckList.findIndex((w) => w.id === currentDeckId) + step;
  if (index >= 0 && index < deckList.length) {
    showDeck(deckList[index], index);
  }
}

function showSelector(workspaces) {
  currentDeckId = null;
  renderWorkspaceSelector(workspaces, (workspace) => {
    saveWorkspace(workspace.id);
    loadWorkspace(workspace);
  });
}

// Which init() is the latest. Two Studio saves in quick succession start two
// fetches, and they can answer out of order; only the newest may draw, or the
// deck would settle on the older catalog.
let initSeq = 0;

async function init() {
  const seq = ++initSeq;
  try {
    const workspaces = await fetchWorkspaces();
    if (seq !== initSeq) {
      return;
    }
    const previousDeckList = deckList;
    deckList = workspaces;

    if (!workspaces.length) {
      currentDeckId = null;
      renderError("The backend has no workspaces yet, so there's nothing to show.");
      return;
    }

    const savedId = readSavedWorkspace();
    let workspace = workspaces.find((w) => String(w.id) === savedId);

    if (!workspace) {
      // Backward compatibility with the RINA-PC checklist's existing
      // ?workspace=N instructions -- once it resolves, persist it so
      // future visits on this device skip the param entirely.
      const requestedId = new URLSearchParams(window.location.search).get("workspace");
      workspace = workspaces.find((w) => String(w.id) === requestedId);
      if (workspace) {
        saveWorkspace(workspace.id);
      }
    }

    // A deck was chosen here once but is gone: deleted in Studio, possibly
    // while it was on screen (the delete's workspace_update lands here). The
    // choice was already made, so this is no first visit and no picker: the
    // deck that took its place in the order, or the first one.
    if (!workspace && savedId !== null) {
      workspace = successorOf(previousDeckList, savedId, workspaces) || workspaces[0];
      saveWorkspace(workspace.id);
    }

    // One deck and no choice saved yet: show the deck, not a menu offering a
    // single option. This is the very first screen after scanning the QR code
    // on a fresh install, and "pick one of one" is a step that teaches
    // nothing. The selector is still reachable any time via "Switch deck",
    // and the moment a second deck exists this behaves exactly as before.
    if (!workspace && workspaces.length === 1) {
      workspace = workspaces[0];
    }

    if (workspace) {
      loadWorkspace(workspace);
    } else {
      showSelector(workspaces);
    }
  } catch (err) {
    if (seq === initSeq) {
      currentDeckId = null;
      renderError(describeLoadFailure(err));
    }
  }
}

init();

attachDeckSwipe(document.getElementById("deck"), switchDeck, { isEnabled: () => deckSwipeEnabled });

// Read here rather than in init(), which re-runs on every Studio save; after
// this, settings_update frames keep it current (and a reconnect re-reads it). A failed read leaves
// swiping on, which is what the deck did before the setting existed.
function readDeckSwipe() {
  fetchSettings()
    .then((settings) => {
      if (settings.deck_swipe !== undefined) {
        deckSwipeEnabled = settings.deck_swipe !== false;
      }
    })
    .catch(() => {});
}
readDeckSwipe();

document.getElementById("switch-workspace-link").addEventListener("click", (event) => {
  event.preventDefault();
  saveWorkspace(null);
  const url = new URL(window.location.href);
  url.searchParams.delete("workspace");
  history.replaceState({}, "", url);
  init();
});

// Press feedback. ws.js has already resolved the raw result frame back to the
// item that was pressed (by req_id -- several server paths omit item_id), so
// this only has to decide where each phase is shown: the ring/dot on the tile
// says which, the toast says why. Success gets no toast at all -- for a tile
// that reports state the colour change is the confirmation, and for one that
// doesn't, .tile-ok already says it landed.
onCommandState(({ itemId, phase, message }) => {
  setTileCommandState(itemId, phase);
  if (phase === "error") {
    showToast(describeCommandFailure(itemId, message));
  }
});

onStateChange((data) => updateTileState(data));
onAgentStatus(({ agent, status }) => setAgentOffline(agent, status === "offline"));
// Nothing is greyed out here -- with the socket down the deck has no way to
// know what the agent is doing, and greying tiles would claim it does. The
// clock takeover is the whole response (see updateClockTakeover).
// A socket that comes back after being down has missed every frame in
// between: a deck deleted or re-added in Studio (SQLite reuses the ids, so a
// stale tile could now name a different command) or swiping turned off. It
// catches up with one refetch of each, only on the way back up -- the first
// connect has init() and readDeckSwipe() above already.
let connectionWasDown = false;
onConnectionChange((up) => {
  setConnectionDown(!up);
  if (!up) {
    connectionWasDown = true;
  } else if (connectionWasDown) {
    connectionWasDown = false;
    init();
    readDeckSwipe();
  }
});
// Studio Mode edits arrive as a bare signal, not the changed data itself --
// refetching and fully re-rendering is simplest and cheap enough here
// (edits are infrequent), same as init()'s own first load.
onWorkspaceUpdate(() => init());
onAuthError(() =>
  showToast("Token rejected -- reopen the link IT-Deck printed, or check config.env.")
);

// The theme is a shared, server-stored choice, so it is wired up on its own
// rather than through init(): it must survive a workspace that fails to load,
// and it must not be re-fetched every time a Studio edit re-renders the grid.
initTheme((err) => showToast(`Couldn't save the theme: ${err.message}`));

// Another panel pressed the theme button, or Studio changed the light/dark
// mode. Applied, not re-fetched -- the frame already carries the new value,
// and applyTheme/applyMode re-check it against their allowlists before it
// reaches the DOM.
//
// Each key is tested for *presence*, and that is load-bearing rather than
// defensive: settings.py broadcasts only the key that changed, so a mode frame
// carries no theme at all. Calling applyTheme(undefined) would normalize to
// the default and silently flip every other panel's deck to Flat.
onSettingsUpdate((settings) => {
  if (settings.mode !== undefined) {
    applyMode(settings.mode);
  }
  if (settings.theme !== undefined) {
    applyTheme(settings.theme);
  }
  if (settings.deck_swipe !== undefined) {
    deckSwipeEnabled = settings.deck_swipe !== false;
  }
});

// iOS Safari only applies :active styles on tap if some element has a touch
// listener attached; this empty listener exists solely to enable that.
document.body.addEventListener('touchstart', function(){}, {passive: true});
