// Studio's Access dialog: see and change the two tokens.
//
// Both are "admin" out of the box and most people never need this; it is here
// for the ones who share their Wi-Fi. The backend (PUT /api/access) saves a
// change to config.env and applies it at once, so there is no restart:
// phones on the old phone token are signed out and ask for the new one once,
// and a new Studio token is handed back to studio.js through onAgentToken so
// this page keeps working.
//
// Same token rule as the backend's TOKEN_PATTERN: URL-safe (the phone token
// rides in the QR link) and nothing that could break a config.env line.

import { el } from "./dom.js";
import { t } from "./studio-i18n.js";

const TOKEN_PATTERN = /^[A-Za-z0-9._~-]{4,64}$/;
// Reserved: the launcher resets a token of this shape to "admin" on start
// (see LEGACY_TOKEN_SHAPE in backend/app/api/access.py).
const LEGACY_TOKEN_SHAPE = /^[0-9a-f]{32}$/;

function randomDigits(count) {
  // crypto.getRandomValues works on plain http, unlike crypto.randomUUID
  // (secure contexts only), and Studio is usually opened over plain http.
  const values = new Uint32Array(count);
  crypto.getRandomValues(values);
  return Array.from(values, (v) => String(v % 10)).join("");
}

export function initAccess(dialog, openButton, { request, currentAgentToken, onAgentToken, showToast }) {
  let fields = null;
  let editable = true;
  let original = { client: "", agent: "" };

  function tokenField(id, labelKey, hintKey, pinLength) {
    const input = el("input", {
      id,
      class: "input access-input",
      type: "password",
      autocomplete: "off",
      spellcheck: "false",
      "aria-describedby": `${id}-hint ${id}-error`,
    });
    const error = el("p", { id: `${id}-error`, class: "message error", role: "alert" });
    const reveal = el("button", {
      type: "button",
      class: "btn btn-ghost",
      "aria-pressed": "false",
      text: t("access.show"),
      onClick: () => {
        const shown = input.type === "text";
        input.type = shown ? "password" : "text";
        reveal.setAttribute("aria-pressed", String(!shown));
        reveal.textContent = shown ? t("access.show") : t("access.hide");
      },
    });
    const generate = el("button", {
      type: "button",
      class: "btn",
      text: t("access.random"),
      onClick: () => {
        input.value = randomDigits(pinLength);
        // A PIN nobody can see is a PIN nobody can type into the phone.
        input.type = "text";
        reveal.setAttribute("aria-pressed", "true");
        reveal.textContent = t("access.hide");
        error.textContent = "";
        input.focus();
      },
    });
    const wrap = el("div", { class: "field access-field" }, [
      el("label", { class: "field-label", for: id, text: t(labelKey) }),
      el("p", { id: `${id}-hint`, class: "field-hint", text: t(hintKey) }),
      el("div", { class: "access-row" }, [input, reveal, generate]),
      error,
    ]);
    return { wrap, input, error, controls: [input, reveal, generate] };
  }

  const status = el("p", { class: "message", role: "status" });
  const saveButton = el("button", { type: "button", class: "btn btn-primary", text: t("common.save"), onClick: save });
  const cancelButton = el("button", { type: "button", class: "btn btn-ghost", text: t("common.cancel"), onClick: () => dialog.close() });
  const readOnlyNote = el("p", { class: "field-hint access-readonly", text: t("access.readOnly") });

  const client = tokenField("access-client", "access.client", "access.clientHint", 6);
  const agent = tokenField("access-agent", "access.agent", "access.agentHint", 8);
  fields = { client, agent };

  dialog.replaceChildren(
    el("h2", { id: "access-title", text: t("access.title") }),
    el("p", { text: t("access.body") }),
    readOnlyNote,
    client.wrap,
    agent.wrap,
    el("p", { class: "field-hint", text: t("access.rule") }),
    status,
    el("div", { class: "dialog-actions" }, [cancelButton, saveButton]),
  );
  dialog.setAttribute("aria-labelledby", "access-title");
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener("close", () => openButton.focus());

  function setEditable(on) {
    editable = on;
    readOnlyNote.hidden = on;
    saveButton.hidden = !on;
    for (const field of Object.values(fields)) {
      for (const control of field.controls) control.disabled = !on;
    }
  }

  function validate(field) {
    const value = field.input.value.trim();
    field.error.textContent =
      TOKEN_PATTERN.test(value) && !LEGACY_TOKEN_SHAPE.test(value) ? "" : t("access.invalid");
    return !field.error.textContent;
  }

  async function open() {
    status.textContent = "";
    status.classList.remove("error");
    for (const field of Object.values(fields)) {
      field.error.textContent = "";
      field.input.type = "password";
    }
    let result = await request("/api/access");
    if (result.status === 401) {
      // The stored Studio token is out of date -- typically changed from
      // another browser. request() has dropped it, so asking again puts up
      // the token prompt instead of just reporting the failure.
      result = await request("/api/access");
    }
    if (!result.ok) {
      showToast(t("access.loadFailed", { detail: result.detail }));
      return;
    }
    original = { client: result.data.client_token || "", agent: currentAgentToken() || "" };
    client.input.value = original.client;
    agent.input.value = original.agent;
    setEditable(Boolean(result.data.editable));
    if (!dialog.open) dialog.showModal();
    (editable ? client.input : cancelButton).focus();
  }

  async function save() {
    // Only what changed is checked and sent: a hand-set token in config.env
    // that predates these rules must not block changing the other one.
    const clientChanged = client.input.value.trim() !== original.client;
    const agentChanged = agent.input.value.trim() !== original.agent;
    client.error.textContent = "";
    agent.error.textContent = "";
    const okClient = !clientChanged || validate(client);
    const okAgent = !agentChanged || validate(agent);
    if (!okClient || !okAgent) {
      (okClient ? agent.input : client.input).focus();
      return;
    }
    const body = {};
    if (clientChanged) body.client_token = client.input.value.trim();
    if (agentChanged) body.agent_token = agent.input.value.trim();
    if (!Object.keys(body).length) {
      dialog.close();
      return;
    }

    saveButton.disabled = true;
    status.classList.remove("error");
    status.textContent = t("common.saving");
    const result = await request("/api/access", { method: "PUT", body });
    saveButton.disabled = false;
    if (!result.ok) {
      status.classList.add("error");
      status.textContent = t("error.save", { detail: result.detail });
      return;
    }
    // Before anything else talks to the backend: the old Studio token has
    // just stopped working.
    if (body.agent_token) onAgentToken(body.agent_token);
    dialog.close();
    showToast(body.client_token ? t("access.savedPhone") : t("access.saved"));
  }

  openButton.addEventListener("click", open);
}
