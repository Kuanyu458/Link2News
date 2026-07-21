import assert from "node:assert/strict";
import { createHmac, webcrypto } from "node:crypto";
import { readFile } from "node:fs/promises";

if (!globalThis.crypto) globalThis.crypto = webcrypto;

const source = await readFile(new URL("../collector/worker.js", import.meta.url), "utf8");
const worker = (await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`)).default;

class StateDB {
  constructor() {
    this.state = new Map();
    this.links = Array.from({ length: 35 }, (_, i) => ({ url: `https://example.test/${i}` }));
  }
  prepare(sql) {
    let args = [];
    const query = {
      bind: (...values) => { args = values; return query; },
      first: async () => {
        if (sql.includes("FROM state WHERE key = ?")) {
          const value = this.state.get(args[0]);
          return value === undefined ? null : { value };
        }
        if (sql.includes("key = 'generate_requested'")) {
          const value = this.state.get("generate_requested");
          return value === undefined ? null : { value };
        }
        throw new Error(`unsupported first: ${sql}`);
      },
      run: async () => {
        if (sql.includes("INSERT INTO state")) {
          this.state.set(args[0], args[1]);
          return { success: true };
        }
        if (sql.includes("DELETE FROM state WHERE key = 'generate_requested'")) {
          this.state.delete("generate_requested");
          return { success: true };
        }
        if (sql.includes("INSERT OR IGNORE INTO links")) {
          this.links.push({ url: args[0], source_id: args[2], line_timestamp: args[3] });
          return { success: true };
        }
        throw new Error(`unsupported run: ${sql}`);
      },
      all: async () => {
        if (sql.includes("SELECT DISTINCT url FROM links")) {
          return { results: this.links };
        }
        if (sql.includes("SELECT * FROM links")) {
          return { results: this.links };
        }
        throw new Error(`unsupported all: ${sql}`);
      },
    };
    return query;
  }
}

const db = new StateDB();
const env = {
  DB: db,
  LINE_CHANNEL_SECRET: "line-secret",
  LINE_CHANNEL_ACCESS_TOKEN: "line-token",
  API_SECRET: "api-secret",
  ALLOWED_SOURCE_IDS: "U-test",
};
const replies = [];
globalThis.fetch = async (_url, init = {}) => {
  if (init.body) replies.push(JSON.parse(init.body));
  return new Response("{}", { status: 200 });
};

async function lineMessage(text, timestamp = Date.now(), userId = "U-test") {
  const body = JSON.stringify({
    events: [{
      type: "message", replyToken: `reply-${timestamp}`, timestamp,
      source: { type: "user", userId },
      message: { type: "text", text },
    }],
  });
  const signature = createHmac("sha256", env.LINE_CHANNEL_SECRET).update(body).digest("base64");
  const request = new Request("https://example.test/webhook", {
    method: "POST", body, headers: { "x-line-signature": signature },
  });
  const response = await worker.fetch(request, env);
  assert.equal(response.status, 200);
  return replies.at(-1)?.messages?.[0]?.text || "";
}

const jobId = "1784390400000";
let message = await lineMessage("生成週報", Number(jobId) - 1000);
assert.equal(db.state.has("generation_job"), false);
assert.match(message, /本週 35 個連結/);
assert.equal(replies.at(-1).messages[0].quickReply.items[0].action.text, "確認生成週報");
assert.equal(replies.at(-1).messages[0].quickReply.items[1].action.text, "取消生成週報");

const replyCount = replies.length;
await lineMessage("生成週報", Number(jobId) - 900, "U-not-allowed");
assert.equal(replies.length, replyCount, "unauthorized LINE source must be ignored");

message = await lineMessage("取消生成週報", Number(jobId) - 500);
assert.match(message, /已取消/);
assert.equal(db.state.has("generation_job"), false);

await lineMessage("確認生成週報", Number(jobId));
let job = JSON.parse(db.state.get("generation_job"));
assert.equal(job.status, "queued");
assert.equal(job.linkCount, 35);
assert.equal(db.state.get("generate_requested"), jobId);
assert.match(replies.at(-1).messages[0].text, /已確認 35 個連結/);

message = await lineMessage("生成週報");
assert.match(message, /週報已排隊/);
assert.match(message, /連結：35 個/);
assert.match(message, /Mac 最近沒有回報輪詢/);

let response = await worker.fetch(new Request(
  "https://example.test/trigger",
  { headers: { "x-api-secret": env.API_SECRET } },
), env);
let trigger = await response.json();
assert.equal(trigger.run, true);
assert.equal(trigger.job_id, jobId);
assert.equal(db.state.has("generate_requested"), true, "GET trigger must be read-only");

response = await worker.fetch(new Request("https://example.test/api/v1/jobs/claim", {
  method: "POST", headers: { authorization: `Bearer ${env.API_SECRET}` },
}), env);
assert.equal(response.status, 200);
const claimed = (await response.json()).job;
assert.equal(claimed.id, jobId);
assert.equal(claimed.status, "running");
assert.equal(db.state.has("generate_requested"), true, "claim retains request until heartbeat");

response = await worker.fetch(new Request("https://example.test/job-status", {
  method: "POST",
  headers: { "x-api-secret": env.API_SECRET, "content-type": "application/json" },
  body: JSON.stringify({ id: jobId, status: "running", phase: "解析 5 條連結" }),
}), env);
assert.equal(response.status, 200);
assert.equal(db.state.has("generate_requested"), false, "first running heartbeat acknowledges request");
message = await lineMessage("生成週報");
assert.match(message, /週報正在生成/);
assert.match(message, /解析 5 條連結/);

await worker.fetch(new Request("https://example.test/job-status", {
  method: "POST",
  headers: { "x-api-secret": env.API_SECRET, "content-type": "application/json" },
  body: JSON.stringify({ id: jobId, status: "completed", phase: "2026-W29 週報已完成" }),
}), env);
message = await lineMessage("生成週報");
assert.match(message, /本週 35 個連結/);

db.links = [];
message = await lineMessage("生成週報");
assert.match(message, /沒有讀取到本週連結/);

response = await worker.fetch(new Request("https://example.test/api/v1/links", {
  method: "POST",
  headers: { authorization: `Bearer ${env.API_SECRET}`, "content-type": "application/json" },
  body: JSON.stringify({ items: [{ url: "https://api.example/item", external_id: "item-1" }] }),
}), env);
assert.equal(response.status, 202);
assert.equal((await response.json()).accepted, 1);

response = await worker.fetch(new Request(
  "https://example.test/api/v1/links?since=0&source_id=U-test",
  { headers: { authorization: `Bearer ${env.API_SECRET}` } },
), env);
assert.equal(response.status, 200);
assert.equal((await response.json()).at(-1).url, "https://api.example/item");

response = await worker.fetch(new Request("https://example.test/api/v1/jobs", {
  method: "POST",
  headers: { authorization: `Bearer ${env.API_SECRET}`, "content-type": "application/json" },
  body: JSON.stringify({ mode: "all" }),
}), env);
assert.equal(response.status, 202);
const apiJob = (await response.json()).job;
response = await worker.fetch(new Request(`https://example.test/api/v1/jobs/${apiJob.id}`, {
  headers: { authorization: `Bearer ${env.API_SECRET}` },
}), env);
assert.equal(response.status, 200);
assert.equal((await response.json()).job.id, apiJob.id);

console.log("worker confirmation and status flow: ok");
