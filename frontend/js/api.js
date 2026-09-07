// The dashboard's only error surface is renderError, so a failure has to
// arrive here as something more specific than "it didn't work". Each throw
// below tags the error with a `kind` that app.js turns into a message
// naming the actual failure -- backend unreachable, a bad HTTP status, a
// non-JSON reply, and a malformed params row are four different problems
// with four different fixes, and they used to collapse into one string.
function loadFailure(kind, detail) {
  const error = new Error(detail);
  error.kind = kind;
  return error;
}

// Settings are a flat key/value read, with none of fetchWorkspaces' per-item
// params parsing to go wrong -- so these throw plainly and let theme.js decide
// what a failure means, rather than carrying the tagged `kind` that exists
// purely so app.js can name which part of a workspace payload was bad.
export async function fetchTheme() {
  const response = await fetch("/api/settings");
  if (!response.ok) {
    throw new Error(`GET /api/settings returned HTTP ${response.status}`);
  }
  const settings = await response.json();
  return settings.theme;
}

export async function putTheme(theme) {
  const response = await fetch("/api/settings/theme", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ theme }),
  });
  if (!response.ok) {
    throw new Error(`PUT /api/settings/theme returned HTTP ${response.status}`);
  }
  return (await response.json()).theme;
}

export async function fetchWorkspaces() {
  let response;
  try {
    response = await fetch("/api/workspaces");
  } catch (err) {
    // fetch only rejects on transport-level failures -- backend down, wrong
    // LAN IP, phone off the network. It never rejects on a 4xx/5xx, which
    // is why the !response.ok check below is a separate kind.
    throw loadFailure("unreachable", err.message);
  }

  if (!response.ok) {
    throw loadFailure("status", String(response.status));
  }

  let workspaces;
  try {
    workspaces = await response.json();
  } catch (err) {
    throw loadFailure("badReply", err.message);
  }

  for (const workspace of workspaces) {
    for (const item of workspace.items) {
      try {
        item.params = JSON.parse(item.params);
      } catch (err) {
        // One malformed params row takes the whole dashboard down (same as
        // before -- the bare SyntaxError propagated too). Naming the item
        // is what makes it fixable: params is only editable per item, so
        // "which row" is the entire question.
        throw loadFailure("badParams", `"${item.label}" (item ${item.id})`);
      }
    }
  }

  return workspaces;
}
