// popup.js
//
// Popup UI controller. It keeps token handling secret-safe, asks for backend host
// permission at runtime, and makes the connection/recording state clear for a
// non-technical user.

import {
  backendOriginPattern,
  normalizeBackendUrl,
  PERMISSION_DENIED_MESSAGE,
} from "./config.js";

const els = {
  startBtn: document.getElementById("start-btn"),
  stopBtn: document.getElementById("stop-btn"),
  testConnectionBtn: document.getElementById("test-connection"),
  indicator: document.getElementById("indicator"),
  indicatorLabel: document.getElementById("indicator-label"),
  status: document.getElementById("status"),
  captureMic: document.getElementById("capture-mic"),
  backendUrl: document.getElementById("backend-url"),
  uploadToken: document.getElementById("upload-token"),
  settingsForm: document.getElementById("settings-form"),
  settingsStatus: document.getElementById("settings-status"),
};

const TOKEN_PLACEHOLDER = "••••••••";
const STATUS_LABELS = {
  idle: "Parado",
  recording: "Gravando",
  uploading: "Enviando",
  uploaded: "Upload concluído",
  uploadError: "Erro no upload",
  invalidToken: "Token inválido",
  backendUnavailable: "Backend indisponível",
  permissionPending: "Permissão pendente",
};

let hasStoredToken = false;

document.addEventListener("DOMContentLoaded", async () => {
  await loadSettingsIntoForm();
  await refreshState();

  els.startBtn.addEventListener("click", onStartClick);
  els.stopBtn.addEventListener("click", onStopClick);
  els.testConnectionBtn.addEventListener("click", onTestConnectionClick);
  els.captureMic.addEventListener("change", onMicToggle);
  els.settingsForm.addEventListener("submit", onSaveSettings);
});

async function onStartClick() {
  setStatusState("idle", "");
  els.startBtn.disabled = true;
  try {
    const reply = await sendToBackground({ type: "start-recording" });
    if (!reply || !reply.ok) {
      throw new Error((reply && reply.error) || "Não foi possível iniciar.");
    }
    setStatusState("recording");
  } catch (err) {
    setStatusState(classifyError(messageOf(err)), messageOf(err));
  } finally {
    await refreshState();
  }
}

async function onStopClick() {
  setStatusState("uploading");
  els.stopBtn.disabled = true;
  try {
    const reply = await sendToBackground({ type: "stop-recording" });
    if (!reply || !reply.ok) {
      throw new Error((reply && reply.error) || "Não foi possível parar.");
    }
  } catch (err) {
    setStatusState("uploadError", messageOf(err));
  } finally {
    await refreshState();
  }
}

async function onMicToggle() {
  await chrome.storage.local.set({ captureMic: els.captureMic.checked });
}

async function onSaveSettings(event) {
  event.preventDefault();
  await saveAndPing();
}

async function onTestConnectionClick() {
  await saveAndPing();
}

async function saveAndPing() {
  setSettingsStatus("Permissão pendente", "warning");
  setButtonsBusy(true);
  try {
    const backendUrl = normalizeBackendUrl(els.backendUrl.value);
    const permission = backendOriginPattern(backendUrl);
    const granted = await chrome.permissions.request({ origins: [permission] });
    if (!granted) {
      throw new Error(PERMISSION_DENIED_MESSAGE);
    }

    const tokenInput = els.uploadToken.value;
    const toSave = { backendUrl, captureMic: els.captureMic.checked };
    if (tokenInput && tokenInput !== TOKEN_PLACEHOLDER) {
      toSave.uploadToken = tokenInput;
      hasStoredToken = true;
      els.uploadToken.value = TOKEN_PLACEHOLDER;
    } else if (!tokenInput) {
      toSave.uploadToken = "";
      hasStoredToken = false;
    }
    await chrome.storage.local.set(toSave);

    const reply = await sendToBackground({ type: "test-connection" });
    if (!reply || !reply.ok) {
      throw new Error((reply && reply.error) || "Backend indisponível.");
    }
    const email = reply.userEmail || "usuário autorizado";
    setSettingsStatus(`Conectado como ${email}`, "ok");
    setStatusState("idle");
  } catch (err) {
    const message = messageOf(err);
    setSettingsStatus(message, classifyError(message) === "permissionPending" ? "warning" : "error");
    setStatusState(classifyError(message), message);
  } finally {
    setButtonsBusy(false);
    await refreshState();
  }
}

async function loadSettingsIntoForm() {
  const stored = await chrome.storage.local.get([
    "backendUrl",
    "uploadToken",
    "captureMic",
  ]);
  els.backendUrl.value = stored.backendUrl || "";
  els.captureMic.checked = stored.captureMic === true;

  hasStoredToken = Boolean(stored.uploadToken);
  els.uploadToken.value = hasStoredToken ? TOKEN_PLACEHOLDER : "";
}

async function refreshState() {
  let st;
  try {
    st = await sendToBackground({ type: "get-state" });
  } catch {
    st = { recording: false, phase: "idle" };
  }
  renderState(st || { recording: false, phase: "idle" });
}

function renderState(st) {
  const recording = Boolean(st.recording);
  els.startBtn.disabled = recording;
  els.stopBtn.disabled = !recording;
  els.captureMic.disabled = recording;

  els.indicator.classList.toggle("recording", recording);
  els.indicator.classList.toggle("idle", !recording);
  els.indicatorLabel.textContent = recording ? STATUS_LABELS.recording : STATUS_LABELS.idle;

  if (st.error) {
    setStatusState(classifyError(st.error), st.error);
  } else if (recording) {
    setStatusState("recording");
  } else if (st.phase === "uploading") {
    setStatusState("uploading");
  } else if (st.lastUpload) {
    setStatusState(
      "uploaded",
      st.warning ? `${STATUS_LABELS.uploaded}. ${st.warning}` : STATUS_LABELS.uploaded,
    );
  } else if (st.warning) {
    setStatusState("permissionPending", st.warning);
  } else {
    setStatusState("idle");
  }
}

function sendToBackground(message) {
  return chrome.runtime.sendMessage(message);
}

function setStatusState(state, message) {
  const label = STATUS_LABELS[state] || STATUS_LABELS.idle;
  els.status.textContent = message || label;
  els.status.classList.toggle("error", isErrorState(state));
  els.status.classList.toggle("warning", state === "permissionPending");
}

function setSettingsStatus(text, tone = "ok") {
  els.settingsStatus.textContent = text || "";
  els.settingsStatus.classList.toggle("error", tone === "error");
  els.settingsStatus.classList.toggle("warning", tone === "warning");
}

function setButtonsBusy(busy) {
  els.testConnectionBtn.disabled = busy;
  els.startBtn.disabled = busy;
}

function isErrorState(state) {
  return ["uploadError", "invalidToken", "backendUnavailable"].includes(state);
}

function classifyError(message) {
  if (/permissão/i.test(message)) {
    return "permissionPending";
  }
  if (/token/i.test(message)) {
    return "invalidToken";
  }
  if (/backend|cors|bloqueou|conectar/i.test(message)) {
    return "backendUnavailable";
  }
  return "uploadError";
}

function messageOf(err) {
  if (err && typeof err.message === "string" && err.message) {
    return err.message;
  }
  return "Erro inesperado.";
}
