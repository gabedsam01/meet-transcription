// api.js
//
// Thin client for the Meet Transcription backend ping/upload endpoints.
//
// The backend exposes:
//   POST {backendUrl}/api/recordings/ping
//   POST {backendUrl}/api/recordings/upload
//   Body:    multipart/form-data
//     - upload_token    (the user's extension upload token)
//     - file            (the audio blob, field name MUST be "file")
//     - meeting_url     (string)
//     - meeting_title   (string)
//     - started_at      (ISO 8601 string)
//     - ended_at        (ISO 8601 string)
//     - duration_seconds(number, as string)
//     - source          ("chrome-extension")
//
// SECURITY: the upload token is a secret. It is sent only in the multipart body,
// never in query strings, never logged, and never echoed back in error messages.

import { normalizeBackendUrl } from "./config.js";

export const CLIENT_NAME = "meet-audio-recorder";

/**
 * Test backend reachability and token validity.
 *
 * @param {{backendUrl:string, token:string, extensionVersion?:string}} options
 * @returns {Promise<Object>}
 */
export async function pingBackend({ backendUrl, token, extensionVersion = "" } = {}) {
  const base = requireBackendConfig(backendUrl, token);
  const form = new FormData();
  form.append("upload_token", token);
  form.append("client_name", CLIENT_NAME);
  form.append("extension_version", extensionVersion || "");

  let response;
  try {
    response = await fetch(`${base}/api/recordings/ping`, {
      method: "POST",
      body: form,
    });
  } catch (networkError) {
    throw new Error(describeFetchError(networkError));
  }

  if (!response.ok) {
    throw new Error(await describeHttpError(response));
  }

  return readJson(response);
}

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
  const base = requireBackendConfig(backendUrl, token);
  if (!blob || blob.size === 0) {
    throw new Error("Gravação vazia: nada para enviar.");
  }

  const url = `${base}/api/recordings/upload`;
  const form = buildRecordingForm(blob, metadata, { token, fileName });

  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      body: form,
    });
  } catch (networkError) {
    throw new Error(describeFetchError(networkError));
  }

  if (!response.ok) {
    throw new Error(await describeHttpError(response));
  }

  // Best-effort JSON parse; some backends answer 204/empty.
  try {
    return await readJson(response);
  } catch {
    return {};
  }
}

/**
 * Build the multipart form used by the upload endpoint.
 * @param {Blob} blob
 * @param {Object} metadata
 * @param {{token:string, fileName?:string}} options
 * @returns {FormData}
 */
export function buildRecordingForm(blob, metadata = {}, { token, fileName } = {}) {
  const form = new FormData();
  form.append("upload_token", token || "");
  form.append("file", blob, fileName || defaultFileName(metadata));
  form.append("meeting_url", metadata.meeting_url ?? "");
  form.append("meeting_title", metadata.meeting_title ?? "");
  form.append("started_at", metadata.started_at ?? "");
  form.append("ended_at", metadata.ended_at ?? "");
  form.append("duration_seconds", String(metadata.duration_seconds ?? ""));
  form.append("include_microphone", String(metadata.include_microphone === true));
  form.append("extension_version", metadata.extension_version ?? "");
  form.append("mime_type", metadata.mime_type ?? (blob && blob.type ? blob.type : ""));
  form.append("source", "chrome-extension");
  return form;
}

/**
 * Build a friendly, secret-free error message from a failed HTTP response.
 * @param {Response} response
 * @returns {Promise<string>}
 */
export async function describeHttpError(response) {
  // Do not echo arbitrary response bodies. A wrong or misconfigured backend
  // could reflect the submitted upload_token in its error payload.
  try {
    await response.text();
  } catch {
    /* ignore body read failures */
  }

  if (response.status === 401 || response.status === 403) {
    return "Token inválido ou revogado. Gere um novo token no painel.";
  }
  if (response.status === 413) {
    return "Gravação excedeu o limite permitido pelo servidor.";
  }
  if (response.status === 503) {
    return "Backend indisponível. Verifique a URL e tente novamente.";
  }
  return `Falha no envio (HTTP ${response.status}).`;
}

/**
 * Convert fetch/network failures into friendly, secret-free UI copy.
 * @param {unknown} err
 * @returns {string}
 */
export function describeFetchError(err) {
  const message = String((err && err.message) || "");
  if (/failed to fetch|load failed|networkerror/i.test(message)) {
    return "O backend bloqueou a extensão. Verifique se a versão do servidor suporta CORS para a extensão.";
  }
  return "Backend indisponível. Verifique a URL e tente novamente.";
}

/**
 * @param {string} backendUrl
 * @param {string} token
 * @returns {string}
 */
function requireBackendConfig(backendUrl, token) {
  const base = normalizeBackendUrl(backendUrl);
  if (!token) {
    throw new Error("Configure o token de upload nas configurações.");
  }
  return base;
}

/**
 * @param {Response} response
 * @returns {Promise<Object>}
 */
async function readJson(response) {
  const text = await response.text();
  return text ? JSON.parse(text) : {};
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
