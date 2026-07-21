import assert from "node:assert/strict";
import { createHash, webcrypto } from "node:crypto";
import { readFile } from "node:fs/promises";

if (!globalThis.crypto) globalThis.crypto = webcrypto;

const source = await readFile(new URL("../collector/worker.js", import.meta.url), "utf8");
const worker = (await import(
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
)).default;

class FakeR2 {
  constructor() { this.objects = new Map(); }

  async put(key, value, options = {}) {
    const bytes = new Uint8Array(await new Response(value).arrayBuffer());
    if (options.sha256) {
      const actual = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
      assert.deepEqual(actual, new Uint8Array(options.sha256));
    }
    const stored = {
      key, bytes, size: bytes.length,
      httpMetadata: options.httpMetadata || {},
      customMetadata: options.customMetadata || {},
      httpEtag: `"${createHash("md5").update(bytes).digest("hex")}"`,
    };
    this.objects.set(key, stored);
    return stored;
  }

  _object(stored, bytes = stored.bytes, range = null) {
    return {
      ...stored, body: new Response(bytes).body, range,
      writeHttpMetadata(headers) {
        const metadata = stored.httpMetadata;
        if (metadata.contentType) headers.set("content-type", metadata.contentType);
        if (metadata.contentDisposition) headers.set("content-disposition", metadata.contentDisposition);
        if (metadata.cacheControl) headers.set("cache-control", metadata.cacheControl);
      },
    };
  }

  async head(key) {
    const stored = this.objects.get(key);
    if (!stored) return null;
    const object = this._object(stored);
    delete object.body;
    return object;
  }

  async get(key, options = {}) {
    const stored = this.objects.get(key);
    if (!stored) return null;
    const rangeHeader = options.range?.get?.("range") || "";
    const match = rangeHeader.match(/^bytes=(\d+)-(\d*)$/);
    if (!match) return this._object(stored);
    const start = Number(match[1]);
    const requestedEnd = match[2] ? Number(match[2]) : stored.size - 1;
    const end = Math.min(requestedEnd, stored.size - 1);
    const bytes = stored.bytes.slice(start, end + 1);
    return this._object(stored, bytes, { offset: start, length: bytes.length });
  }
}

const env = {
  API_SECRET: "api-secret",
  ARTIFACT_SIGNING_SECRET: "artifact-signing-secret-for-tests",
  ARTIFACTS: new FakeR2(),
};

const reportBytes = new TextEncoder().encode("%PDF-test-report");
const reportSha = createHash("sha256").update(reportBytes).digest("hex");
let response = await worker.fetch(new Request(
  "https://collector.test/artifacts/2026-W28/report",
  {
    method: "PUT", body: reportBytes,
    headers: {
      "content-type": "application/pdf",
      "content-length": String(reportBytes.length),
      "x-content-sha256": reportSha,
    },
  }), env);
assert.equal(response.status, 403);

response = await worker.fetch(new Request(
  "https://collector.test/artifacts/2026-W28/report",
  {
    method: "PUT", body: reportBytes,
    headers: {
      "x-api-secret": env.API_SECRET,
      "content-type": "application/pdf",
      "content-length": String(reportBytes.length),
      "x-content-sha256": reportSha,
    },
  }), env);
assert.equal(response.status, 200);
const reportArtifact = (await response.json()).artifact;
assert.equal(reportArtifact.contentType, "application/pdf");
assert.match(reportArtifact.url, /\/media\/2026-W28\/report\?expires=/);

response = await worker.fetch(new Request(reportArtifact.url), env);
assert.equal(response.status, 200);
assert.equal(response.headers.get("content-type"), "application/pdf");
assert.match(response.headers.get("content-disposition"), /inline/);
assert.deepEqual(new Uint8Array(await response.arrayBuffer()), reportBytes);

response = await worker.fetch(new Request(reportArtifact.url, { method: "HEAD" }), env);
assert.equal(response.status, 200);
assert.equal(Number(response.headers.get("content-length")), reportBytes.length);

const podcastBytes = new TextEncoder().encode("0123456789abcdef");
const podcastSha = createHash("sha256").update(podcastBytes).digest("hex");
response = await worker.fetch(new Request(
  "https://collector.test/artifacts/2026-W28/podcast",
  {
    method: "PUT", body: podcastBytes,
    headers: {
      "x-api-secret": env.API_SECRET,
      "content-type": "audio/mpeg",
      "content-length": String(podcastBytes.length),
      "x-content-sha256": podcastSha,
      "x-duration-ms": "123456",
    },
  }), env);
assert.equal(response.status, 200);
const podcastArtifact = (await response.json()).artifact;
assert.equal(podcastArtifact.durationMs, 123456);

response = await worker.fetch(new Request(podcastArtifact.url, {
  headers: { range: "bytes=4-9" },
}), env);
assert.equal(response.status, 206);
assert.equal(response.headers.get("content-range"), "bytes 4-9/16");
assert.equal(await response.text(), "456789");

const tampered = new URL(reportArtifact.url);
tampered.searchParams.set("sig", `${tampered.searchParams.get("sig")}x`);
response = await worker.fetch(new Request(tampered), env);
assert.equal(response.status, 403);

console.log("worker private artifact upload and delivery: ok");
