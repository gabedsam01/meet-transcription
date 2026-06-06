// config.js
//
// Pure configuration helpers shared by the popup and service worker. These are
// intentionally Chrome-API-free so they can be covered by dependency-free Node
// tests.

export const PERMISSION_DENIED_MESSAGE =
  "Permissão negada. A extensão precisa de acesso ao domínio do backend para enviar gravações.";

export const INVALID_BACKEND_URL_MESSAGE =
  "Informe uma URL válida começando com https://";

/**
 * Normalize and validate the backend URL entered by the user.
 *
 * Production backends must use HTTPS. Local development may use
 * http://localhost:<port>. The returned value is always an origin without a
 * trailing slash, so API paths cannot accidentally double up slashes.
 *
 * @param {string} value
 * @returns {string}
 */
export function normalizeBackendUrl(value) {
  const raw = String(value || "").trim().replace(/\/+$/, "");
  if (!raw) {
    throw new Error(INVALID_BACKEND_URL_MESSAGE);
  }

  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error(INVALID_BACKEND_URL_MESSAGE);
  }

  if (parsed.pathname !== "/" || parsed.search || parsed.hash) {
    throw new Error("Informe apenas a origem do backend, sem caminho ou parâmetros.");
  }

  if (parsed.protocol === "https:") {
    return parsed.origin;
  }

  if (parsed.protocol === "http:" && parsed.hostname === "localhost") {
    return parsed.origin;
  }

  throw new Error(INVALID_BACKEND_URL_MESSAGE);
}

/**
 * Convert a normalized backend URL into the exact host permission requested at
 * runtime. Never request a broad wildcard from user input.
 *
 * @param {string} backendUrl
 * @returns {string}
 */
export function backendOriginPattern(backendUrl) {
  const normalized = normalizeBackendUrl(backendUrl);
  const parsed = new URL(normalized);
  if (parsed.protocol === "http:" && parsed.hostname === "localhost") {
    return "http://localhost/*";
  }
  return `${normalized}/*`;
}
