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

// Where the client credential lives. /ws/client now opens with a hello frame
// carrying this token (see backend/app/ws/client.py), mirroring the handshake
// the Windows agent has always used on /ws/agent.
//
// It is a shared secret typed into a phone, not an identity: it says "you are
// allowed to drive this deck", nothing more. That is the same trust model the
// AGENT_TOKEN already sets for every write endpoint, and deliberately a
// *different* secret from it -- this one ships to a browser on a plain-http
// LAN, so leaking it must not also hand over /api/items and agent
// impersonation.
const TOKEN_STORAGE_KEY = "itdeck.client_token";

// Reads the token, accepting a one-time handoff via ?token=... so the phone can
// be set up by opening a link instead of typing a secret into a prompt on a
// touch keyboard. The param is stripped from the URL immediately after it is
// stored: leaving it there would park the secret in history, in the PWA's saved
// start URL, and in any screenshot of the address bar.
function readStoredToken() {
  let stored = null;
  try {
    stored = localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch (e) {
    // Private mode / blocked site data. Fall through to the query param and
    // the prompt, both of which still work for this one page load.
  }

  const fromQuery = new URLSearchParams(location.search).get("token");
  if (fromQuery) {
    stored = fromQuery;
    try {
      localStorage.setItem(TOKEN_STORAGE_KEY, fromQuery);
    } catch (e) {
      // Not fatal: the value below still authenticates this session.
    }
    const url = new URL(location.href);
    url.searchParams.delete("token");
    history.replaceState(null, "", url.pathname + url.search + url.hash);
  }

  return stored;
}

function forgetToken() {
  try {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch (e) {
    // Nothing to do -- the prompt below will ask again regardless.
  }
}

// The resolved token, and whether the user has already dismissed being asked
// for one. Held at module scope because acquiring it must happen *before* the
// socket opens -- see acquireToken().
let clientToken = null;
let tokenPromptDismissed = false;

// Asked for only when there is nothing stored, so the ordinary case (the deck
// on the phone, already set up) never sees a dialog.
//
// Called from connect(), before the WebSocket is constructed, and deliberately
// NOT from the "open" handler: prompt() blocks the main thread, the server
// starts its 5s HELLO_TIMEOUT the moment the socket opens, and nobody types a
// token on a phone keyboard in five seconds. Asking first means the hello frame
// goes out immediately on open, with a value already in hand.
//
// Returns "" rather than null when dismissed, so a hello is still sent and the
// server still answers with a close code -- a dismissed prompt should reach the
// same visible "rejected" state as a wrong token, not a different silent one.
function acquireToken() {
  const stored = readStoredToken();
  if (stored) {
    return stored;
  }
  if (tokenPromptDismissed) {
    return "";
  }
  const entered = window.prompt("IT-Deck: enter the client token to connect");
  if (entered) {
    try {
      localStorage.setItem(TOKEN_STORAGE_KEY, entered);
    } catch (e) {
      // As above.
    }
    return entered;
  }
  tokenPromptDismissed = true;
  return "";
}

let socket = null;
let backoff = 1000;

// readyState === OPEN is no longer sufficient to mean "this socket may carry
// commands": between open and the server's verdict on the hello frame there is
// a window where the socket is OPEN but unauthenticated. A command sent into
// that window is discarded server-side, and its req_id would then strand until
// ws.js's own 8s backstop -- past the 5s resolution budget CLAUDE.md calls
// non-negotiable. Any inbound frame proves the handshake passed, because the
// server sends nothing at all until it has.
let authenticated = false;
const resultCallbacks = [];
const stateChangeCallbacks = [];
const agentStatusCallbacks = [];
const workspaceUpdateCallbacks = [];
const commandStateCallbacks = [];
const settingsUpdateCallbacks = [];

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
  // Before the socket exists, so a prompt cannot race the server's handshake
  // timeout (see acquireToken).
  if (!clientToken) {
    clientToken = acquireToken();
  }
  authenticated = false;
  socket = new WebSocket(`ws://${location.host}/ws/client`);

  socket.addEventListener("open", () => {
    // Connection succeeded, so the next disconnect should start backing off
    // from scratch again instead of continuing to climb.
    backoff = 1000;
    // The server expects this as the *first* frame and will close the socket
    // after HELLO_TIMEOUT without it. Sent from inside the open handler rather
    // than once at startup so every reconnect down the backoff ladder
    // re-authenticates on its own, with no extra bookkeeping.
    socket.send(JSON.stringify({ type: "hello", token: clientToken }));
  });

  socket.addEventListener("message", (event) => {
    // The server sends nothing before the hello is accepted, so the arrival of
    // any frame is itself the proof. No extra ack message needed.
    authenticated = true;
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
    } else if (message.type === "settings_update") {
      // Unlike workspace_update, this carries its payload rather than being a
      // bare "go re-fetch" signal -- there is one word to deliver and nothing
      // to re-render, so a round trip back to the API would buy nothing.
      for (const callback of settingsUpdateCallbacks) {
        callback(message.settings || {});
      }
    }
  });

  socket.addEventListener("close", (event) => {
    // 4001 is the server's "your hello was rejected" (ws/client.py's
    // CLOSE_UNAUTHORIZED). Dropping the stored token on that code is what stops
    // a wrong secret from retrying itself forever: without it the reconnect
    // ladder would re-present the same bad token every time and never ask the
    // user for a better one. Any other code is an ordinary disconnect and must
    // *not* clear it -- a backend restart would otherwise log the phone out.
    authenticated = false;
    if (event.code === 4001) {
      console.warn("[IT-Deck] client token rejected; will ask again on reconnect");
      forgetToken();
      if (clientToken) {
        // A real value was presented and refused: drop it and ask again on the
        // next connect().
        clientToken = null;
        tokenPromptDismissed = false;
      }
      // If clientToken was already "" the user dismissed the prompt, and
      // tokenPromptDismissed stays set -- otherwise every backoff tick would
      // re-open the dialog and trap them in it.
    }
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
  if (!socket || socket.readyState !== WebSocket.OPEN || !authenticated) {
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
  if (!socket || socket.readyState !== WebSocket.OPEN || !authenticated) {
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

// Another panel (or another device) changed a shared setting. Currently only
// the theme; the callback gets the whole settings object so adding a second
// key later needs no change here.
export function onSettingsUpdate(callback) {
  settingsUpdateCallbacks.push(callback);
}
