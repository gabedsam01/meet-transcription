// background.js — MV3 service worker.
//
// Responsibilities:
//   * Mint a tabCapture media stream id for the active Meet tab (requires the
//     popup "Start recording" user gesture — see startRecording()).
//   * Create/destroy the offscreen document that hosts MediaRecorder.
//   * Relay start/stop to the offscreen document and track recording state.
//   * Show a clear "REC" badge while recording.
//
// Message protocol this worker HANDLES (sendMessage from popup/content):
//   { type:"start-recording", tabId? }   -> begins capture for the Meet tab.
//   { type:"stop-recording" }            -> stops + triggers upload.
//   { type:"get-state" }                 -> returns { recording, tabId, error }.
//   { type:"content-call-ended", ... }   -> from content.js; auto-stops.
//
// Messages this worker RECEIVES from the offscreen document (target:"background"):
//   offscreen-started | offscreen-stopped | offscreen-uploaded | offscreen-error.
//
// The service worker is the SINGLE source of recording state, so the popup can
// reopen at any time and re-read it via "get-state".

const OFFSCREEN_DOCUMENT_PATH = "src/offscreen.html";

// In-memory recording state. Survives popup closes; resets if the worker is
// evicted (in which case nothing is recording anyway).
const state = {
  recording: false,
  tabId: null,
  meetingUrl: "",
  meetingTitle: "",
  startedAt: "",
  lastError: "",
  lastUpload: null,
};

// ---------------------------------------------------------------------------
// Message routing
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message.type !== "string") {
    return false;
  }

  // Messages addressed to the offscreen document are not for us.
  if (message.target === "offscreen") {
    return false;
  }

  // Replies coming back FROM the offscreen document.
  if (message.target === "background") {
    handleOffscreenReply(message);
    return false;
  }

  switch (message.type) {
    case "start-recording":
      startRecording(message.tabId)
        .then(() => sendResponse({ ok: true }))
        .catch((err) =>
          sendResponse({ ok: false, error: userMessage(err) }),
        );
      return true; // async response

    case "stop-recording":
      stopRecording()
        .then(() => sendResponse({ ok: true }))
        .catch((err) =>
          sendResponse({ ok: false, error: userMessage(err) }),
        );
      return true;

    case "get-state":
      sendResponse(getPublicState());
      return false;

    case "content-call-ended":
      // The user left the Meet call; stop ONLY if this is the tab we are
      // recording, so a second Meet tab ending its call can't cut our recording.
      if (state.recording && sender && sender.tab && sender.tab.id === state.tabId) {
        void stopRecording();
      }
      return false;

    default:
      return false;
  }
});

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

/**
 * Start recording the given tab (or the active tab). MUST be invoked from a
 * user gesture (the popup button), because chrome.tabCapture.getMediaStreamId
 * requires one.
 * @param {number} [tabId]
 */
async function startRecording(tabId) {
  if (state.recording) {
    throw new Error("Já existe uma gravação em andamento.");
  }
  state.lastError = "";
  state.lastUpload = null; // clear the previous run's "uploaded" message.

  const tab = await resolveTab(tabId);
  if (!tab || !tab.id) {
    throw new Error("Abra uma aba do Google Meet para gravar.");
  }
  if (!/^https:\/\/meet\.google\.com\//.test(tab.url || "")) {
    throw new Error("A aba ativa não é uma reunião do Google Meet.");
  }

  // (1) Mint the media stream id for THIS tab. Requires the user gesture that
  // came from the popup button click.
  const streamId = await getMediaStreamId(tab.id);

  // (2) Load persisted backend settings (URL + token).
  const settings = await loadSettings();
  if (!settings.backendUrl || !settings.token) {
    throw new Error("Configure a URL do backend e o token antes de gravar.");
  }
  const withMic = settings.captureMic === true;

  // (3) Ensure the offscreen document exists.
  await ensureOffscreenDocument();

  // (4) Tell the offscreen document to begin recording.
  state.tabId = tab.id;
  state.meetingUrl = tab.url || "";
  state.meetingTitle = tab.title || "Google Meet";
  state.startedAt = new Date().toISOString();

  // The offscreen document's onMessage listener registers asynchronously while
  // its module evaluates, which can finish AFTER createDocument() resolves. Retry
  // the start until the listener is up, so recording never silently fails to begin.
  await sendToOffscreen({
    target: "offscreen",
    type: "offscreen-start",
    streamId,
    withMic,
    metadata: {
      meeting_url: state.meetingUrl,
      meeting_title: state.meetingTitle,
      started_at: state.startedAt,
    },
    settings: {
      backendUrl: settings.backendUrl,
      token: settings.token,
    },
  });

  // Optimistically mark recording; the offscreen "offscreen-started" confirms.
  state.recording = true;
  await showRecordingBadge(true);
}

/**
 * Mint a tabCapture media stream id. Wrapped in a promise because the callback
 * form is the one universally available across Chrome versions.
 * @param {number} targetTabId
 * @returns {Promise<string>}
 */
function getMediaStreamId(targetTabId) {
  return new Promise((resolve, reject) => {
    try {
      chrome.tabCapture.getMediaStreamId({ targetTabId }, (streamId) => {
        const err = chrome.runtime.lastError;
        if (err || !streamId) {
          reject(
            new Error(
              "Não foi possível capturar o áudio da aba. Clique em gravar a partir da reunião.",
            ),
          );
          return;
        }
        resolve(streamId);
      });
    } catch (e) {
      reject(
        new Error(
          "Captura da aba indisponível. Verifique as permissões da extensão.",
        ),
      );
    }
  });
}

// ---------------------------------------------------------------------------
// Stop
// ---------------------------------------------------------------------------

/** Stop the current recording (which triggers the upload in the offscreen doc). */
async function stopRecording() {
  if (!state.recording) {
    return; // nothing to do; keep it idempotent
  }
  try {
    await chrome.runtime.sendMessage({ target: "offscreen", type: "offscreen-stop" });
  } finally {
    state.recording = false;
    await showRecordingBadge(false);
  }
}

// ---------------------------------------------------------------------------
// Offscreen replies
// ---------------------------------------------------------------------------

/**
 * Handle a message coming back from the offscreen document.
 * @param {Object} message
 */
function handleOffscreenReply(message) {
  switch (message.type) {
    case "offscreen-started":
      state.recording = true;
      void showRecordingBadge(true);
      break;

    case "offscreen-stopped":
      state.recording = false;
      void showRecordingBadge(false);
      if (message.micError) {
        state.lastError = message.micError;
      }
      break;

    case "offscreen-uploaded":
      state.lastUpload = message.result || {};
      state.lastError = "";
      // The recording is fully done; tear down the offscreen document.
      void closeOffscreenDocument();
      break;

    case "offscreen-error":
      state.recording = false;
      state.lastError = message.message || "Erro na gravação.";
      void showRecordingBadge(false);
      void closeOffscreenDocument();
      break;

    default:
      break;
  }
}

// ---------------------------------------------------------------------------
// Offscreen document lifecycle
// ---------------------------------------------------------------------------

/** Create the offscreen document if it is not already open. */
async function ensureOffscreenDocument() {
  // hasDocument is available on recent Chrome; guard for older builds.
  if (chrome.offscreen.hasDocument) {
    const exists = await chrome.offscreen.hasDocument();
    if (exists) {
      return;
    }
  }
  try {
    await chrome.offscreen.createDocument({
      url: OFFSCREEN_DOCUMENT_PATH,
      // USER_MEDIA covers getUserMedia (tab + mic capture) and MediaRecorder.
      reasons: ["USER_MEDIA"],
      justification:
        "Record Google Meet tab audio (and optional microphone) with MediaRecorder.",
    });
  } catch (err) {
    // If it already exists (race), that's fine; otherwise re-throw friendly.
    if (!/single offscreen/i.test(String(err && err.message))) {
      throw new Error("Não foi possível iniciar o componente de gravação.");
    }
  }
}

/**
 * Send a message to the offscreen document, retrying while its listener is still
 * registering. Re-sends only on the "receiving end does not exist" race; any other
 * error propagates immediately.
 * @param {Object} message
 * @param {{attempts?:number, delayMs?:number}} [opts]
 */
async function sendToOffscreen(message, { attempts = 6, delayMs = 50 } = {}) {
  let lastError;
  for (let i = 0; i < attempts; i += 1) {
    try {
      return await chrome.runtime.sendMessage(message);
    } catch (err) {
      lastError = err;
      const text = String((err && err.message) || "");
      if (!/Receiving end does not exist|Could not establish connection/i.test(text)) {
        throw err;
      }
      await delay(delayMs);
    }
  }
  throw lastError || new Error("Componente de gravação indisponível.");
}

/** @param {number} ms @returns {Promise<void>} */
function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Close the offscreen document, if any. */
async function closeOffscreenDocument() {
  try {
    if (chrome.offscreen.hasDocument) {
      const exists = await chrome.offscreen.hasDocument();
      if (!exists) {
        return;
      }
    }
    await chrome.offscreen.closeDocument();
  } catch {
    /* ignore; document may already be gone */
  }
}

// ---------------------------------------------------------------------------
// Badge + state helpers
// ---------------------------------------------------------------------------

/**
 * Toggle the "REC" badge on the toolbar action.
 * @param {boolean} on
 */
async function showRecordingBadge(on) {
  try {
    await chrome.action.setBadgeBackgroundColor({ color: "#d93025" }); // Google red
    await chrome.action.setBadgeText({ text: on ? "REC" : "" });
  } catch {
    /* setBadge can fail if the action is gone; non-fatal */
  }
}

/** @returns {Object} the popup-facing snapshot of recording state. */
function getPublicState() {
  return {
    recording: state.recording,
    tabId: state.tabId,
    meetingUrl: state.meetingUrl,
    meetingTitle: state.meetingTitle,
    startedAt: state.startedAt,
    error: state.lastError,
    lastUpload: state.lastUpload,
  };
}

/**
 * Resolve a tab by id, or fall back to the active tab in the current window.
 * @param {number} [tabId]
 * @returns {Promise<chrome.tabs.Tab|undefined>}
 */
async function resolveTab(tabId) {
  if (typeof tabId === "number") {
    try {
      return await chrome.tabs.get(tabId);
    } catch {
      /* fall through to active tab */
    }
  }
  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
  return active;
}

/**
 * Read persisted settings from chrome.storage.local.
 * @returns {Promise<{backendUrl:string, token:string, captureMic:boolean}>}
 */
async function loadSettings() {
  const stored = await chrome.storage.local.get([
    "backendUrl",
    "uploadToken",
    "captureMic",
  ]);
  return {
    backendUrl: (stored.backendUrl || "").trim(),
    token: stored.uploadToken || "",
    captureMic: stored.captureMic === true,
  };
}

/**
 * Map any error to a user-safe message. NEVER includes secrets/tokens.
 * @param {unknown} err
 * @returns {string}
 */
function userMessage(err) {
  if (err && typeof err.message === "string" && err.message) {
    return err.message;
  }
  return "Erro inesperado. Tente novamente.";
}
