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
