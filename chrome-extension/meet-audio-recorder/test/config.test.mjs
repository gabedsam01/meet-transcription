import test from "node:test";
import assert from "node:assert/strict";

import { backendOriginPattern, normalizeBackendUrl } from "../src/config.js";

test("normalizes https backend URL and builds exact origin permission", () => {
  const normalized = normalizeBackendUrl("https://meet-transcription.example/");

  assert.equal(normalized, "https://meet-transcription.example");
  assert.equal(
    backendOriginPattern(normalized),
    "https://meet-transcription.example/*",
  );
});

test("allows localhost http URL for development", () => {
  const normalized = normalizeBackendUrl("http://localhost:8000/");

  assert.equal(normalized, "http://localhost:8000");
  assert.equal(backendOriginPattern(normalized), "http://localhost/*");
});

test("rejects non-https non-localhost backend URLs", () => {
  assert.throws(
    () => normalizeBackendUrl("http://example.com"),
    /Informe uma URL válida começando com https:\/\//,
  );
});

test("rejects non-localhost loopback aliases over http", () => {
  assert.throws(
    () => normalizeBackendUrl("http://127.0.0.1:8000"),
    /Informe uma URL válida começando com https:\/\//,
  );
});

test("rejects malformed backend URLs", () => {
  assert.throws(
    () => normalizeBackendUrl("meet-transcription.example"),
    /Informe uma URL válida começando com https:\/\//,
  );
});
