// offscreen.js
//
// Runs inside the offscreen document. It owns the actual audio capture and the
// MediaRecorder (see recorder.js), and uploads the finished recording via
// api.js. It talks to the service worker (background.js) only through
// chrome.runtime messages.
//
// Message protocol (target: "offscreen"):
//   { target:"offscreen", type:"offscreen-start",
//     streamId, withMic, metadata, settings }   -> begins recording
//   { target:"offscreen", type:"offscreen-stop" } -> stops + uploads
//
// Replies back to background (target:"background"):
//   { target:"background", type:"offscreen-started" }
//   { target:"background", type:"offscreen-stopped" }
//   { target:"background", type:"offscreen-uploaded", result }
//   { target:"background", type:"offscreen-error", message }

import { RECORDING_MIME_TYPE, TabRecorder } from "./recorder.js";
import { uploadRecording } from "./api.js";

// Single recorder instance for this offscreen document's lifetime.
let recorder = null;
let pendingMetadata = null;
let pendingSettings = null;

chrome.runtime.onMessage.addListener((message) => {
  if (!message || message.target !== "offscreen") {
    return; // not for us
  }

  switch (message.type) {
    case "offscreen-start":
      void handleStart(message);
      break;
    case "offscreen-stop":
      void handleStop();
      break;
    default:
      // Unknown message; ignore silently.
      break;
  }
  // We reply asynchronously via sendToBackground(), not via sendResponse.
});

/**
 * Begin recording the tab (and optionally mic).
 * @param {Object} message
 */
async function handleStart(message) {
  try {
    if (recorder && recorder.isRecording) {
      throw new Error("Já existe uma gravação em andamento.");
    }
    pendingMetadata = message.metadata || {};
    pendingSettings = message.settings || {};

    recorder = new TabRecorder();
    await recorder.start(message.streamId, { withMic: Boolean(message.withMic) });

    sendToBackground({ type: "offscreen-started" });
  } catch (err) {
    recorder = null;
    sendToBackground({
      type: "offscreen-error",
      message: friendly(err, "Não foi possível iniciar a gravação."),
    });
  }
}

/** Stop recording, then upload the resulting blob. */
async function handleStop() {
  try {
    if (!recorder) {
      throw new Error("Nenhuma gravação ativa.");
    }
    const { blob, durationSeconds, micError } = await recorder.stop();
    recorder = null;

    const endedAt = new Date().toISOString();
    const metadata = {
      meeting_url: pendingMetadata.meeting_url || "",
      meeting_title: pendingMetadata.meeting_title || "",
      started_at: pendingMetadata.started_at || endedAt,
      ended_at: endedAt,
      duration_seconds: durationSeconds,
      include_microphone: pendingMetadata.include_microphone === true,
      extension_version: pendingMetadata.extension_version || "",
      mime_type: blob.type || RECORDING_MIME_TYPE,
    };

    sendToBackground({ type: "offscreen-stopped", micError });

    // Upload using the persisted backend URL + token.
    const result = await uploadRecording(blob, metadata, {
      backendUrl: pendingSettings.backendUrl,
      token: pendingSettings.token,
    });

    sendToBackground({ type: "offscreen-uploaded", result });
  } catch (err) {
    sendToBackground({
      type: "offscreen-error",
      message: friendly(err, "Falha ao finalizar ou enviar a gravação."),
    });
  } finally {
    pendingMetadata = null;
    pendingSettings = null;
  }
}

/**
 * Send a message back to the service worker.
 * @param {Object} payload
 */
function sendToBackground(payload) {
  chrome.runtime
    .sendMessage({ target: "background", ...payload })
    .catch(() => {
      /* background may be asleep; the badge/state are recoverable. */
    });
}

/**
 * Extract a user-safe message from an error (never includes the token).
 * @param {unknown} err
 * @param {string} fallback
 * @returns {string}
 */
function friendly(err, fallback) {
  if (err && typeof err.message === "string" && err.message) {
    return err.message;
  }
  return fallback;
}
