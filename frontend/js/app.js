import { fetchWorkspaces } from "./api.js";
import { renderWorkspace, renderWorkspaceSelector, renderError, updateTileState, setAgentOffline, setTileCommandState, getTileMeta } from "./render.js";
import { sendExecute, sendSetValue, onCommandState, onStateChange, onAgentStatus, onWorkspaceUpdate } from "./ws.js";
import { showContextMenu } from "./contextmenu.js";
import { showToast } from "./toast.js";

// Device-local "which workspace does this deck show" choice. Deliberately
// not part of any server state -- multiple phones can point at different
// workspaces from the same backend.
const STORAGE_KEY = "itdeck:workspaceId";

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

function loadWorkspace(workspace) {
  renderWorkspace(workspace, sendExecute, sendSetValue, handleTileLongPress);
}

function showSelector(workspaces) {
  renderWorkspaceSelector(workspaces, (workspace) => {
    localStorage.setItem(STORAGE_KEY, String(workspace.id));
    loadWorkspace(workspace);
  });
}

async function init() {
  try {
    const workspaces = await fetchWorkspaces();

    if (!workspaces.length) {
      renderError("The backend has no workspaces yet, so there's nothing to show.");
      return;
    }

    const savedId = localStorage.getItem(STORAGE_KEY);
    let workspace = workspaces.find((w) => String(w.id) === savedId);

    if (!workspace) {
      // Backward compatibility with the RINA-PC checklist's existing
      // ?workspace=N instructions -- once it resolves, persist it so
      // future visits on this device skip the param entirely.
      const requestedId = new URLSearchParams(window.location.search).get("workspace");
      workspace = workspaces.find((w) => String(w.id) === requestedId);
      if (workspace) {
        localStorage.setItem(STORAGE_KEY, String(workspace.id));
      }
    }

    if (workspace) {
      loadWorkspace(workspace);
    } else {
      showSelector(workspaces);
    }
  } catch (err) {
    renderError(describeLoadFailure(err));
  }
}

init();

document.getElementById("switch-workspace-link").addEventListener("click", (event) => {
  event.preventDefault();
  localStorage.removeItem(STORAGE_KEY);
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
// Studio Mode edits arrive as a bare signal, not the changed data itself --
// refetching and fully re-rendering is simplest and cheap enough here
// (edits are infrequent), same as init()'s own first load.
onWorkspaceUpdate(() => init());

// iOS Safari only applies :active styles on tap if some element has a touch
// listener attached; this empty listener exists solely to enable that.
document.body.addEventListener('touchstart', function(){}, {passive: true});
