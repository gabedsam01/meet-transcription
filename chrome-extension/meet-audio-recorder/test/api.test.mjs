import test from "node:test";
import assert from "node:assert/strict";

import { buildRecordingForm, describeFetchError, describeHttpError } from "../src/api.js";

test("buildRecordingForm sends token in form data with recording metadata", () => {
  const blob = new Blob(["audio"], { type: "audio/webm;codecs=opus" });
  const form = buildRecordingForm(
    blob,
    {
      meeting_url: "https://meet.google.com/abc-defg-hij",
      meeting_title: "Weekly Sync",
      started_at: "2026-06-06T10:00:00.000Z",
      ended_at: "2026-06-06T10:05:00.000Z",
      duration_seconds: 300,
      include_microphone: true,
      extension_version: "0.1.0",
      mime_type: "audio/webm;codecs=opus",
    },
    { token: "secret-token", fileName: "meet.webm" },
  );

  assert.equal(form.get("upload_token"), "secret-token");
  assert.equal(form.get("meeting_url"), "https://meet.google.com/abc-defg-hij");
  assert.equal(form.get("meeting_title"), "Weekly Sync");
  assert.equal(form.get("duration_seconds"), "300");
  assert.equal(form.get("include_microphone"), "true");
  assert.equal(form.get("extension_version"), "0.1.0");
  assert.equal(form.get("mime_type"), "audio/webm;codecs=opus");
  assert.equal(form.get("source"), "chrome-extension");
  assert.equal(form.has("file"), true);
});

test("describeFetchError maps browser CORS failures to friendly copy", () => {
  assert.equal(
    describeFetchError(new TypeError("Failed to fetch")),
    "O backend bloqueou a extensão. Verifique se a versão do servidor suporta CORS para a extensão.",
  );
});

test("describeFetchError maps generic network failures to backend unavailable", () => {
  assert.equal(
    describeFetchError(new Error("ECONNREFUSED")),
    "Backend indisponível. Verifique a URL e tente novamente.",
  );
});

test("describeHttpError never echoes response details that could contain tokens", async () => {
  const response = new Response(
    JSON.stringify({ detail: "upstream echoed secret-token-value" }),
    { status: 500 },
  );

  const message = await describeHttpError(response);

  assert.equal(message, "Falha no envio (HTTP 500).");
  assert.doesNotMatch(message, /secret-token-value/);
});
