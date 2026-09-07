const MAX_BACKOFF = 30000;

// Backstop for a req_id the server never resolves. Deliberately longer than
// the backend's own 5s budget (track(req_id, 5.0) in ws/client.py) so a real
// "timeout" result always wins this race and the user sees the server's
// reason rather than a synthetic one. It exists because two paths genuinely
// produce no server reply at all: sendExecute's readyState guard drops the
// command before it is ever sent, and a socket that stays down past 5s misses
// the timeout broadcast entirely. CLAUDE.md calls an unresolved req_id a bug,
// so something has to close those two holes on this side.
const COMMAND_TIMEOUT_MS = 8000;

let socket = null;
let backoff = 1000;
const resultCallbacks = [];
const stateChangeCallbacks = [];
const agentStatusCallbacks = [];
const workspaceUpdateCallbacks = [];
const commandStateCallbacks = [];

// req_id -> {itemId, timer}. The correlation lives here because req_ids are
// generated here and nowhere else; every consumer downstream works in
// item_ids. Matching on req_id rather than the result's own item_id is not a
// preference: three server paths omit item_id entirely (item-not-found in
// ws/client.py, and the unknown-command reply in the Windows agent), while
// req_id is present on every one of them.
const inFlight = new Map();

// item_id -> how many of its req_ids are still outstanding. A count, not a
// boolean: two quick taps on one tile produce two req_ids, and the first
// result back must not clear the indicator while the second is still in the
// air. Only execute (taps) is tracked -- see sendExecute.
const inFlightPerItem = new Map();

function connect() {
  socket = new WebSocket(`ws://${location.host}/ws/client`);

  socket.addEventListener("open", () => {
    // Connection succeeded, so the next disconnect should start backing off
    // from scratch again instead of continuing to climb.
    backoff = 1000;
  });

  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "result") {
      settleRequest(message.req_id, message.status, message.message);
      for (const callback of resultCallbacks) {
        callback(message);
      }
    } else if (message.type === "state") {
      for (const callback of stateChangeCallbacks) {
        callback(message.data);
      }
    } else if (message.type === "agent_status") {
      for (const callback of agentStatusCallbacks) {
        callback({ agent: message.agent, status: message.status });
      }
    } else if (message.type === "workspace_update") {
      for (const callback of workspaceUpdateCallbacks) {
        callback();
      }
    }
  });

  socket.addEventListener("close", () => {
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, MAX_BACKOFF);
  });

  socket.addEventListener("error", () => {
    socket.close();
  });
}

connect();

// crypto.randomUUID() requires a secure context (HTTPS or localhost) and
// this app is accessed over plain http://<lan-ip>:8000 from the phone, so
// it would silently be undefined there. Use a manual id instead.
function generateReqId() {
  return Date.now() + "-" + Math.random().toString(36).slice(2);
}

function notifyCommandState(itemId, phase, message) {
  for (const callback of commandStateCallbacks) {
    callback({ itemId, phase, message });
  }
}

function trackRequest(reqId, itemId) {
  const count = (inFlightPerItem.get(itemId) || 0) + 1;
  inFlightPerItem.set(itemId, count);
  inFlight.set(reqId, {
    itemId,
    timer: setTimeout(() => settleRequest(reqId, "error", "timeout"), COMMAND_TIMEOUT_MS),
  });
  // Only on the transition from idle to busy: a second tap while the first is
  // still out shouldn't re-announce a state the tile is already showing.
  if (count === 1) {
    notifyCommandState(itemId, "pending");
  }
}

function settleRequest(reqId, status, message) {
  const entry = inFlight.get(reqId);
  // An unknown req_id is the normal case, not an anomaly: hub.broadcast_to_clients
  // fans every result out to every connected client, so a second phone's
  // results arrive here too. Filtering on what this client actually sent is
  // the correct place to do it -- the backend has no client identity to
  // filter by. Also covers a req_id this side already timed out.
  if (!entry) {
    return;
  }
  clearTimeout(entry.timer);
  inFlight.delete(reqId);

  const remaining = (inFlightPerItem.get(entry.itemId) || 1) - 1;
  if (remaining > 0) {
    inFlightPerItem.set(entry.itemId, remaining);
  } else {
    inFlightPerItem.delete(entry.itemId);
  }

  // Anything that isn't exactly "ok" is a failure. Testing for === "error"
  // instead would silently read an unexpected value as success: the Windows
  // agent spreads its handler's dict last into the result frame
  // (**result in agents/windows/agent.py) and nothing validates that field.
  if (status !== "ok") {
    notifyCommandState(entry.itemId, "error", message);
  } else if (remaining === 0) {
    // Held back while another tap on the same tile is still outstanding --
    // the tile is still genuinely busy.
    notifyCommandState(entry.itemId, "ok");
  }
}

export function sendExecute(itemId, { overrideType } = {}) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  const reqId = generateReqId();
  const message = {
    cmd: "execute",
    item_id: itemId,
    req_id: reqId,
  };
  // Only set for Long Press's Force Stop menu option, which needs a
  // different agent command than a normal tap on the same tile -- see
  // backend/app/ws/client.py's _handle_execute for how this is consumed.
  if (overrideType) {
    message.override_type = overrideType;
  }
  socket.send(JSON.stringify(message));
  // Tracked only after the readyState guard and the send itself, never
  // before: a command the guard dropped (or that threw on send) is never
  // going to produce a result, and tracking it would strand a req_id that
  // only the 8s timeout could clear -- showing the user a failure for a
  // command that was never attempted.
  trackRequest(reqId, itemId);
}

export function sendSetValue(itemId, value) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  socket.send(
    JSON.stringify({
      cmd: "set_value",
      item_id: itemId,
      value,
      req_id: generateReqId(),
    })
  );
}

export function onResult(callback) {
  resultCallbacks.push(callback);
}

// Resolved per-item command feedback: {itemId, phase: "pending"|"ok"|"error",
// message}. Distinct from onResult, which stays the raw verbatim frame.
export function onCommandState(callback) {
  commandStateCallbacks.push(callback);
}

export function onStateChange(callback) {
  stateChangeCallbacks.push(callback);
}

export function onAgentStatus(callback) {
  agentStatusCallbacks.push(callback);
}

export function onWorkspaceUpdate(callback) {
  workspaceUpdateCallbacks.push(callback);
}
