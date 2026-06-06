// api.js
//
// Thin client for the Meet Transcription backend upload endpoint.
//
// The backend exposes:
//   POST {backendUrl}/api/recordings/upload
//   Auth:    Authorization: Bearer <EXTENSION_UPLOAD_TOKEN>
//   Body:    multipart/form-data
//     - file            (the audio blob, field name MUST be "file")
//     - meeting_url     (string)
//     - meeting_title   (string)
//     - started_at      (ISO 8601 string)
//     - ended_at        (ISO 8601 string)
//     - duration_seconds(number, as string)
//     - source          ("chrome-extension")
//
// SECURITY: the upload token is a secret. It is sent only in the Authorization
// header and is NEVER logged, never written to disk by this module, and never
// echoed back in error messages.

/**
 * Upload a recorded audio blob to the backend.
 *
 * @param {Blob} blob - the recorded audio (audio/webm;codecs=opus).
 * @param {Object} metadata - meeting metadata.
 * @param {string} metadata.meeting_url
 * @param {string} metadata.meeting_title
 * @param {string} metadata.started_at - ISO 8601 timestamp.
 * @param {string} metadata.ended_at - ISO 8601 timestamp.
 * @param {number} metadata.duration_seconds
 * @param {Object} options
 * @param {string} options.backendUrl - backend origin, e.g. "http://localhost:8000".
 * @param {string} options.token - the EXTENSION_UPLOAD_TOKEN (Bearer).
 * @param {string} [options.fileName] - suggested file name for the upload.
 * @returns {Promise<Object>} parsed JSON response (or {} when no body).
 * @throws {Error} on missing config or any non-2xx response. The thrown
 *   Error.message is safe to show in the UI and NEVER contains the token.
 */
export async function uploadRecording(blob, metadata, { backendUrl, token, fileName } = {}) {
  if (!backendUrl) {
    throw new Error("Configure a URL do backend nas configurações.");
  }
  if (!token) {
    throw new Error("Configure o token de upload nas configurações.");
  }
  if (!blob || blob.size === 0) {
    throw new Error("Gravação vazia: nada para enviar.");
  }

  // Normalise the backend URL so we never produce a double slash.
  const base = String(backendUrl).replace(/\/+$/, "");
  const url = `${base}/api/recordings/upload`;

  const form = new FormData();
  // Field name MUST be "file" to match the backend contract.
  form.append("file", blob, fileName || defaultFileName(metadata));
  form.append("meeting_url", metadata.meeting_url ?? "");
  form.append("meeting_title", metadata.meeting_title ?? "");
  form.append("started_at", metadata.started_at ?? "");
  form.append("ended_at", metadata.ended_at ?? "");
  form.append("duration_seconds", String(metadata.duration_seconds ?? ""));
  form.append("source", "chrome-extension");

  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: {
        // Do NOT set Content-Type manually: the browser adds the multipart
        // boundary automatically when the body is a FormData instance.
        Authorization: `Bearer ${token}`,
      },
      body: form,
    });
  } catch (networkError) {
    // Never surface the token; only a friendly, generic message.
    throw new Error(
      "Não foi possível conectar ao backend. Verifique a URL e sua conexão.",
    );
  }

  if (!response.ok) {
    throw new Error(await describeHttpError(response));
  }

  // Best-effort JSON parse; some backends answer 204/empty.
  try {
    const text = await response.text();
    return text ? JSON.parse(text) : {};
  } catch {
    return {};
  }
}

/**
 * Build a friendly, secret-free error message from a failed HTTP response.
 * @param {Response} response
 * @returns {Promise<string>}
 */
async function describeHttpError(response) {
  let detail = "";
  try {
    const text = await response.text();
    // The backend returns FastAPI-style {"detail": "..."} payloads. We surface
    // only that field; we never echo headers (which could contain the token).
    if (text) {
      try {
        const parsed = JSON.parse(text);
        detail = typeof parsed.detail === "string" ? parsed.detail : "";
      } catch {
        detail = text.slice(0, 200);
      }
    }
  } catch {
    // ignore body read failures
  }

  if (response.status === 401 || response.status === 403) {
    return "Token de upload inválido ou sem permissão. Revise o token.";
  }
  if (response.status === 413) {
    return "Arquivo muito grande para o limite do backend (EXTENSION_UPLOAD_MAX_MB).";
  }
  const suffix = detail ? ` (${detail})` : "";
  return `Falha no envio (HTTP ${response.status})${suffix}.`;
}

/**
 * Derive a default file name from the meeting metadata.
 * @param {Object} metadata
 * @returns {string}
 */
function defaultFileName(metadata) {
  const stamp = (metadata && metadata.started_at ? metadata.started_at : new Date().toISOString())
    .replace(/[:.]/g, "-");
  return `meet-recording-${stamp}.webm`;
}
