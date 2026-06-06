// popup.js
//
// The popup UI. It:
//   * lets the user start/stop a recording (the Start click is the REQUIRED
//     user gesture for chrome.tabCapture — see background.js);
//   * shows a clear recording indicator;
//   * persists the backend URL, upload token, and mic preference in
//     chrome.storage.local. The token field is type=password (masked) and is
//     NEVER logged anywhere.
//
// Message protocol (popup -> background): "start-recording", "stop-recording",
// "get-state". The popup is stateless: it always re-reads state on open.

const els = {
  startBtn: document.getElementById("start-btn"),
  stopBtn: document.getElementById("stop-btn"),
  indicator: document.getElementById("indicator"),
  indicatorLabel: document.getElementById("indicator-label"),
  status: document.getElementById("status"),
  captureMic: document.getElementById("capture-mic"),
  backendUrl: document.getElementById("backend-url"),
  uploadToken: document.getElementById("upload-token"),
  settingsForm: document.getElementById("settings-form"),
  settingsStatus: document.getElementById("settings-status"),
};

// Sentinel shown in the token field when a token is already stored, so we never
// have to read the real secret back into the popup.
const TOKEN_PLACEHOLDER = "••••••••";
let hasStoredToken = false;

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", async () => {
  await loadSettingsIntoForm();
  await refreshState();

  els.startBtn.addEventListener("click", onStartClick);
  els.stopBtn.addEventListener("click", onStopClick);
  els.captureMic.addEventListener("change", onMicToggle);
  els.settingsForm.addEventListener("submit", onSaveSettings);
});

// ---------------------------------------------------------------------------
// Recording controls
// ---------------------------------------------------------------------------

async function onStartClick() {
  setStatus("");
  els.startBtn.disabled = true;
  try {
    // This message is sent synchronously inside the click handler, preserving
    // the user gesture that chrome.tabCapture requires in the worker.
    const reply = await sendToBackground({ type: "start-recording" });
    if (!reply || !reply.ok) {
      throw new Error((reply && reply.error) || "Não foi possível iniciar.");
    }
    setStatus("Gravando…");
  } catch (err) {
    setStatus(messageOf(err), true);
  } finally {
    await refreshState();
  }
}

async function onStopClick() {
  setStatus("Finalizando e enviando…");
  els.stopBtn.disabled = true;
  try {
    const reply = await sendToBackground({ type: "stop-recording" });
    if (!reply || !reply.ok) {
      throw new Error((reply && reply.error) || "Não foi possível parar.");
    }
  } catch (err) {
    setStatus(messageOf(err), true);
  } finally {
    await refreshState();
  }
}

async function onMicToggle() {
  // Persist the preference immediately so it applies to the next recording.
  await chrome.storage.local.set({ captureMic: els.captureMic.checked });
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

async function loadSettingsIntoForm() {
  const stored = await chrome.storage.local.get([
    "backendUrl",
    "uploadToken",
    "captureMic",
  ]);
  els.backendUrl.value = stored.backendUrl || "";
  els.captureMic.checked = stored.captureMic === true;

  hasStoredToken = Boolean(stored.uploadToken);
  // Show a mask if a token already exists; never reveal the real value.
  els.uploadToken.value = hasStoredToken ? TOKEN_PLACEHOLDER : "";
}

async function onSaveSettings(event) {
  event.preventDefault();
  const backendUrl = els.backendUrl.value.trim();
  const tokenInput = els.uploadToken.value;

  const toSave = { backendUrl };

  // Only overwrite the token when the user actually typed a new one (i.e. it is
  // not the untouched mask). This avoids clobbering a stored token with "••••".
  if (tokenInput && tokenInput !== TOKEN_PLACEHOLDER) {
    toSave.uploadToken = tokenInput;
    hasStoredToken = true;
    els.uploadToken.value = TOKEN_PLACEHOLDER;
  } else if (!tokenInput) {
    // Empty field means "clear the token".
    toSave.uploadToken = "";
    hasStoredToken = false;
  }

  await chrome.storage.local.set(toSave);
  setSettingsStatus("Configurações salvas.");
}

// ---------------------------------------------------------------------------
// State / rendering
// ---------------------------------------------------------------------------

async function refreshState() {
  let st;
  try {
    st = await sendToBackground({ type: "get-state" });
  } catch {
    st = { recording: false };
  }
  renderState(st || { recording: false });
}

/**
 * @param {Object} st - the public state from background.js.
 */
function renderState(st) {
  const recording = Boolean(st.recording);
  els.startBtn.disabled = recording;
  els.stopBtn.disabled = !recording;
  els.captureMic.disabled = recording;

  els.indicator.classList.toggle("recording", recording);
  els.indicator.classList.toggle("idle", !recording);
  els.indicatorLabel.textContent = recording ? "Gravando" : "Parado";

  if (st.error) {
    setStatus(st.error, true);
  } else if (recording) {
    setStatus("Gravando…");
  } else if (st.lastUpload) {
    setStatus("Gravação enviada com sucesso.");
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Send a message to the service worker and await its reply.
 * @param {Object} message
 * @returns {Promise<any>}
 */
function sendToBackground(message) {
  return chrome.runtime.sendMessage(message);
}

function setStatus(text, isError = false) {
  els.status.textContent = text || "";
  els.status.classList.toggle("error", Boolean(isError));
}

function setSettingsStatus(text) {
  els.settingsStatus.textContent = text || "";
}

/**
 * Extract a safe display message from an error. Never contains the token.
 * @param {unknown} err
 * @returns {string}
 */
function messageOf(err) {
  if (err && typeof err.message === "string" && err.message) {
    return err.message;
  }
  return "Erro inesperado.";
}
