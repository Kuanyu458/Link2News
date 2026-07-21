/**
 * 週報連結收集器 — Cloudflare Worker
 *
 * Endpoints:
 *   POST /webhook              LINE Messaging API webhook（驗簽後存訊息）
 *   GET  /links?since=<ms>     取出 line_timestamp >= since 的連結（需 X-Api-Secret）
 *   GET  /selections?week=<w>  取出該週的術語勾選回覆（需 X-Api-Secret）
 *   POST /await-selection      標記「開始等待某週的編號回覆」（需 X-Api-Secret）
 *                              body: {"week":"2026-W28"}；純數字回覆只在等待中才收
 *
 * Bindings（wrangler.toml / dashboard 設定）:
 *   DB                  D1 database
 *   LINE_CHANNEL_SECRET LINE channel secret（驗簽用, secret）
 *   API_SECRET          Mac 端拉資料用的共享密鑰（secret）
 */

const URL_RE = /https?:\/\/[^\s"'<>）)\]】」]+/g;
const NUMERIC_REPLY_RE = /^[\d\s,，、]+$/;

// Rich Menu 可選的生成模型（label 顯示於快速回覆按鈕；quick reply 上限 13 項）
const FALLBACK_MODELS = [
  { label: "Claude CLI default", value: "claude-cli" },
  { label: "Codex CLI default", value: "codex-cli" },
];

function availableModels(env) {
  const configured = parseJson(env.AVAILABLE_MODELS_JSON, null);
  if (!Array.isArray(configured)) return FALLBACK_MODELS;
  const valid = configured.filter((item) => item && typeof item.label === "string" &&
    typeof item.value === "string").slice(0, 10);
  return valid.length ? valid : FALLBACK_MODELS;
}

function defaultModel(env) {
  return String(env.DEFAULT_MODEL || availableModels(env)[0].value);
}
const JOB_STATUS_KEY = "generation_job";
const MAC_LAST_POLL_KEY = "mac_last_poll";
const ACTIVE_JOB_STATUSES = new Set(["queued", "running"]);
const API_VERSION = "v1";
const CLAIM_LEASE_MS = 5 * 60 * 1000;
const ARTIFACT_WEEK_RE = /^\d{4}-W\d{2}$/;
const ARTIFACT_TTL_SECONDS = 90 * 24 * 60 * 60;
const MAX_ARTIFACT_BYTES = 50 * 1024 * 1024;
const ARTIFACT_TYPES = {
  report: {
    contentType: "application/pdf",
    filename: (week) => `weekly_${week}.pdf`,
  },
  podcast: {
    contentType: "audio/mpeg",
    filename: () => "podcast.mp3",
  },
};

async function lineReply(env, replyToken, text, quickReplyItems) {
  const message = { type: "text", text: text.slice(0, 4900) };
  if (quickReplyItems && quickReplyItems.length) {
    message.quickReply = {
      items: quickReplyItems.map((it) => ({
        type: "action",
        action: { type: "message", label: it.label.slice(0, 20), text: it.text },
      })),
    };
  }
  await fetch("https://api.line.me/v2/bot/message/reply", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ replyToken, messages: [message] }),
  });
}

async function getState(env, key) {
  const row = await env.DB.prepare("SELECT value FROM state WHERE key = ?").bind(key).first();
  return row ? row.value : null;
}

async function setState(env, key, value) {
  await env.DB.prepare(
    "INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value"
  ).bind(key, value).run();
}

function parseJson(value, fallback = null) {
  try { return value ? JSON.parse(value) : fallback; } catch (_) { return fallback; }
}

function artifactObjectKey(week, kind) {
  return `reports/${week}/${ARTIFACT_TYPES[kind].filename(week)}`;
}

function hexToBytes(hex) {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i += 1) {
    bytes[i] = Number.parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

function base64UrlEncode(bytes) {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function base64UrlDecode(value) {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/") +
    "=".repeat((4 - value.length % 4) % 4);
  try {
    return Uint8Array.from(atob(padded), (ch) => ch.charCodeAt(0));
  } catch (_) {
    return null;
  }
}

async function artifactSigningKey(env, usages) {
  if (!env.ARTIFACT_SIGNING_SECRET) return null;
  return crypto.subtle.importKey(
    "raw", new TextEncoder().encode(env.ARTIFACT_SIGNING_SECRET),
    { name: "HMAC", hash: "SHA-256" }, false, usages
  );
}

function artifactSignaturePayload(pathname, expires) {
  return new TextEncoder().encode(`${pathname}\n${expires}`);
}

async function createArtifactUrl(request, env, week, kind) {
  const key = await artifactSigningKey(env, ["sign"]);
  if (!key) throw new Error("ARTIFACT_SIGNING_SECRET is not configured");
  const pathname = `/media/${week}/${kind}`;
  const expires = Math.floor(Date.now() / 1000) + ARTIFACT_TTL_SECONDS;
  const mac = await crypto.subtle.sign(
    "HMAC", key, artifactSignaturePayload(pathname, expires)
  );
  const url = new URL(pathname, request.url);
  url.searchParams.set("expires", String(expires));
  url.searchParams.set("sig", base64UrlEncode(new Uint8Array(mac)));
  return { url: url.toString(), expiresAt: expires * 1000 };
}

async function verifyArtifactUrl(url, env) {
  const expires = Number(url.searchParams.get("expires") || 0);
  const signature = base64UrlDecode(url.searchParams.get("sig") || "");
  if (!Number.isInteger(expires) || expires <= Math.floor(Date.now() / 1000) || !signature) {
    return false;
  }
  const key = await artifactSigningKey(env, ["verify"]);
  if (!key) return false;
  return crypto.subtle.verify(
    "HMAC", key, signature, artifactSignaturePayload(url.pathname, expires)
  );
}

async function uploadArtifact(request, env, week, kind) {
  if (!env.ARTIFACTS) return new Response("artifact storage unavailable", { status: 503 });
  const spec = ARTIFACT_TYPES[kind];
  const contentType = (request.headers.get("content-type") || "").split(";", 1)[0].trim();
  const contentLength = Number(request.headers.get("content-length") || 0);
  const sha256 = (request.headers.get("x-content-sha256") || "").toLowerCase();
  if (contentType !== spec.contentType) {
    return Response.json({ ok: false, error: "invalid content type" }, { status: 415 });
  }
  if (!Number.isInteger(contentLength) || contentLength <= 0) {
    return Response.json({ ok: false, error: "content length required" }, { status: 411 });
  }
  if (contentLength > MAX_ARTIFACT_BYTES) {
    return Response.json({ ok: false, error: "artifact exceeds 50 MB" }, { status: 413 });
  }
  if (!/^[0-9a-f]{64}$/.test(sha256)) {
    return Response.json({ ok: false, error: "valid SHA-256 required" }, { status: 400 });
  }
  const durationMs = kind === "podcast"
    ? Math.max(0, Number.parseInt(request.headers.get("x-duration-ms") || "0", 10) || 0)
    : 0;
  const objectKey = artifactObjectKey(week, kind);
  let object;
  try {
    object = await env.ARTIFACTS.put(objectKey, request.body, {
      sha256: hexToBytes(sha256).buffer,
      httpMetadata: {
        contentType: spec.contentType,
        contentDisposition: `inline; filename="${spec.filename(week)}"`,
        cacheControl: "private, max-age=3600",
      },
      customMetadata: {
        week, kind, sha256,
        durationMs: String(durationMs),
      },
    });
  } catch (error) {
    return Response.json(
      { ok: false, error: `upload failed: ${String(error.message || error)}` },
      { status: 400 }
    );
  }
  const signed = await createArtifactUrl(request, env, week, kind);
  return Response.json({
    ok: true,
    artifact: {
      kind, filename: spec.filename(week), contentType: spec.contentType,
      size: object?.size || contentLength, sha256, durationMs,
      url: signed.url, expiresAt: signed.expiresAt,
    },
  });
}

async function serveArtifact(request, env, url, week, kind) {
  if (!env.ARTIFACTS) return new Response("artifact storage unavailable", { status: 503 });
  if (!(await verifyArtifactUrl(url, env))) return new Response("forbidden", { status: 403 });
  const objectKey = artifactObjectKey(week, kind);
  const object = request.method === "HEAD"
    ? await env.ARTIFACTS.head(objectKey)
    : await env.ARTIFACTS.get(objectKey, { onlyIf: request.headers, range: request.headers });
  if (!object) return new Response("not found", { status: 404 });

  const headers = new Headers();
  object.writeHttpMetadata(headers);
  headers.set("etag", object.httpEtag);
  headers.set("accept-ranges", "bytes");
  headers.set("x-content-type-options", "nosniff");
  headers.set("cache-control", "private, max-age=3600");
  let status = 200;
  const isPartialGet = request.method === "GET" && request.headers.has("range") &&
    object.range && typeof object.range.offset === "number";
  if (isPartialGet) {
    const length = Number(object.range.length || 0);
    headers.set("content-range", `bytes ${object.range.offset}-${object.range.offset + length - 1}/${object.size}`);
    headers.set("content-length", String(length));
    status = 206;
  } else {
    headers.set("content-length", String(object.size));
  }
  if (request.method === "HEAD") return new Response(null, { status, headers });
  if (!("body" in object)) return new Response(null, { status: 412, headers });
  return new Response(object.body, { status, headers });
}

function formatTaipei(ms) {
  if (!ms) return "—";
  return new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(ms));
}

function elapsedText(fromMs, now = Date.now()) {
  if (!fromMs) return "—";
  const mins = Math.max(0, Math.floor((now - fromMs) / 60000));
  if (mins < 1) return "不到 1 分鐘";
  if (mins < 60) return `${mins} 分鐘`;
  return `${Math.floor(mins / 60)} 小時 ${mins % 60} 分鐘`;
}

function taipeiWeekStartMs(now = Date.now()) {
  const TAIPEI_OFFSET_MS = 8 * 60 * 60 * 1000;
  const local = new Date(now + TAIPEI_OFFSET_MS);
  const daysSinceMonday = (local.getUTCDay() + 6) % 7;
  local.setUTCHours(0, 0, 0, 0);
  return local.getTime() - daysSinceMonday * 24 * 60 * 60 * 1000 - TAIPEI_OFFSET_MS;
}

async function weeklyLinkCount(env, sourceId, now = Date.now()) {
  const { results } = await env.DB.prepare(
    "SELECT DISTINCT url FROM links WHERE source_id = ? AND line_timestamp >= ?"
  ).bind(sourceId, taipeiWeekStartMs(now)).all();
  return results.length;
}

function isActiveJob(job, now = Date.now()) {
  if (!job || !ACTIVE_JOB_STATUSES.has(job.status)) return false;
  if (job.status !== "running") return true;
  return now - Number(job.updatedAt || job.startedAt || 0) <= 6 * 60 * 60 * 1000;
}

async function generationStatusMessage(env, sourceId = "") {
  let job = parseJson(await getState(env, JOB_STATUS_KEY));
  if (!job) {
    const legacyRequestedAt = Number(await getState(env, "generate_requested") || 0);
    if (legacyRequestedAt) {
      const model = (await getState(env, "model")) || defaultModel(env);
      const modelLabel = (availableModels(env).find((m) => m.value === model) || {}).label || model;
      job = {
        id: String(legacyRequestedAt), status: "queued", requestedAt: legacyRequestedAt,
        updatedAt: legacyRequestedAt, model, modelLabel, phase: "等待 Mac 接手",
      };
      await setState(env, JOB_STATUS_KEY, JSON.stringify(job));
    }
  }
  if (job && sourceId && job.sourceId && job.sourceId !== sourceId) {
    job = null;
  }
  if (!job) return "💭 目前沒有週報生成工作。\n按「生成週報」即可建立新工作。";

  const now = Date.now();
  const model = job.modelLabel || job.model || "—";
  if (job.status === "queued") {
    const lastPoll = Number(await getState(env, MAC_LAST_POLL_KEY) || 0);
    const macState = !lastPoll || now - lastPoll > 6 * 60 * 1000
      ? "⚠️ Mac 最近沒有回報輪詢，可能尚未開機或輪詢器未執行。"
      : `💻 Mac 最後輪詢：${formatTaipei(lastPoll)}（${elapsedText(lastPoll, now)}前）`;
    const taskLine = job.mode === "regenerate"
      ? `工作：重新生成 ${job.week || "上次週報"}\n`
      : "";
    return `⏳ 週報已排隊，尚未開始\n${taskLine}` +
      (job.linkCount ? `連結：${job.linkCount} 個\n` : "") +
      `模型：${model}\n排隊時間：${formatTaipei(job.requestedAt)}\n` +
      `已等待：${elapsedText(job.requestedAt, now)}\n\n${macState}\n` +
      `Mac 開機並登入後，最多約 3 分鐘會開始。`;
  }
  if (job.status === "running") {
    const stale = now - Number(job.updatedAt || job.startedAt || 0) > 10 * 60 * 1000;
    const progressTotal = Number(job.progressTotal || job.linkCount || 0);
    const progressDone = Math.min(Number(job.progressDone || 0), progressTotal || Infinity);
    const progressLine = progressTotal > 0
      ? `連結處理：${progressDone}/${progressTotal} 已完成\n`
      : "";
    const taskLine = job.mode === "regenerate"
      ? `工作：重新生成 ${job.week || "上次週報"}\n`
      : "";
    return `🛠️ 週報正在生成\n${taskLine}` +
      `階段：${job.phase || "處理中"}\n${progressLine}模型：${model}\n` +
      `開始時間：${formatTaipei(job.startedAt)}\n已執行：${elapsedText(job.startedAt, now)}\n` +
      `最後更新：${formatTaipei(job.updatedAt)}` +
      (stale ? "\n\n⚠️ 進度已超過 10 分鐘未更新，Mac 可能休眠或流程停滯。" : "");
  }
  if (job.status === "completed") {
    const delivery = job.deliveryStatus === "ready"
      ? "\n📱 PDF 與 Podcast 已發布到手機閱讀連結。"
      : job.deliveryStatus === "failed"
        ? "\n⚠️ 週報已完成，但手機檔案發布失敗，可在 Mac 執行補送。"
        : "";
    const pdfUrl = job.artifacts?.report?.url ? `\n📄 ${job.artifacts.report.url}` : "";
    const podcastUrl = job.artifacts?.podcast?.url ? `\n🎧 ${job.artifacts.podcast.url}` : "";
    return `✅ 最近一次週報已完成\n模型：${model}\n` +
      `完成時間：${formatTaipei(job.completedAt || job.updatedAt)}\n` +
      `總耗時：${elapsedText(job.startedAt, job.completedAt || job.updatedAt)}` +
      (job.phase ? `\n結果：${job.phase}` : "") + delivery + pdfUrl + podcastUrl;
  }
  return `❌ 最近一次週報生成失敗\n階段：${job.phase || "不明"}\n` +
    `時間：${formatTaipei(job.failedAt || job.updatedAt)}\n` +
    (job.error ? `原因：${job.error}` : "請查看 Mac 端日誌。");
}

async function verifyLineSignature(secret, bodyText, signature) {
  if (!signature) return false;
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(bodyText));
  const expected = btoa(String.fromCharCode(...new Uint8Array(mac)));
  return expected === signature;
}

function sourceInfo(source) {
  if (!source) return { type: "unknown", id: "" };
  if (source.type === "group") return { type: "group", id: source.groupId };
  if (source.type === "room") return { type: "room", id: source.roomId };
  return { type: "user", id: source.userId || "" };
}

function allowedSourceIds(env) {
  return String(env.ALLOWED_SOURCE_IDS || "")
    .split(",").map((value) => value.trim()).filter(Boolean);
}

function isAllowedSource(env, sourceId) {
  return !!sourceId && allowedSourceIds(env).includes(sourceId);
}

function defaultSourceId(env) {
  return allowedSourceIds(env)[0] || "";
}

async function handleGenerateButton(env, event) {
  const sourceId = sourceInfo(event.source).id;
  const current = parseJson(await getState(env, JOB_STATUS_KEY));
  if (isActiveJob(current)) {
    const text = !current.sourceId || current.sourceId === sourceId
      ? await generationStatusMessage(env, sourceId) +
        "\n\n（再次按「生成週報」即可更新進度）"
      : "ℹ️ 另一個已授權來源目前有週報工作進行中，請稍後再試。";
    await lineReply(env, event.replyToken, text);
    return;
  }

  const model = (await getState(env, "model")) || defaultModel(env);
  const label = (availableModels(env).find((m) => m.value === model) || {}).label || model;
  const linkCount = await weeklyLinkCount(env, sourceId, event.timestamp || Date.now());
  if (linkCount === 0) {
    await lineReply(env, event.replyToken,
      "📭 目前沒有讀取到本週連結，尚無法生成週報。\n請先把連結分享到聊天室。");
    return;
  }
  await lineReply(env, event.replyToken,
    `📊 目前讀取到本週 ${linkCount} 個連結。\n生成模型：${label}\n\n確定要開始生成週報嗎？`,
    [
      { label: "✅ 確認生成", text: "確認生成週報" },
      { label: "❌ 取消", text: "取消生成週報" },
    ]);
}

async function confirmGeneration(env, event) {
  const sourceId = sourceInfo(event.source).id;
  const current = parseJson(await getState(env, JOB_STATUS_KEY));
  if (isActiveJob(current)) {
    await lineReply(env, event.replyToken,
      !current.sourceId || current.sourceId === sourceId
        ? await generationStatusMessage(env, sourceId)
        : "ℹ️ 另一個已授權來源目前有週報工作進行中。");
    return;
  }

  const model = (await getState(env, "model")) || defaultModel(env);
  const label = (availableModels(env).find((m) => m.value === model) || {}).label || model;
  const linkCount = await weeklyLinkCount(env, sourceId, event.timestamp || Date.now());
  if (linkCount === 0) {
    await lineReply(env, event.replyToken, "📭 本週連結數為 0，已取消生成。");
    return;
  }

  const requestedAt = Number(event.timestamp || Date.now());
  const job = {
    id: String(requestedAt), status: "queued", requestedAt,
    updatedAt: Date.now(), model, modelLabel: label, linkCount, sourceId,
    phase: "等待 Mac 接手",
  };
  await setState(env, "generate_requested", job.id);
  await setState(env, JOB_STATUS_KEY, JSON.stringify(job));
  await lineReply(env, event.replyToken,
    `🗯️ 已確認 ${linkCount} 個連結並排入生成佇列（模型：${label}）。\n` +
    "Mac 最多約 3 分鐘會開始；再次按「生成週報」可查看進度。");
}

async function handleRegenerateButton(env, event) {
  const sourceId = sourceInfo(event.source).id;
  const current = parseJson(await getState(env, JOB_STATUS_KEY));
  if (isActiveJob(current)) {
    await lineReply(env, event.replyToken,
      await generationStatusMessage(env, sourceId) + "\n\n目前已有工作，完成後才能重新生成上次週報。");
    return;
  }
  const snap = parseJson(await getState(env, "snapshot"), {});
  if (!snap.week) {
    await lineReply(env, event.replyToken,
      "目前找不到上一次週報資料，請先完成一次「生成週報」。");
    return;
  }
  const model = (await getState(env, "model")) || defaultModel(env);
  const label = (availableModels(env).find((m) => m.value === model) || {}).label || model;
  const linkCount = Number(snap.linkCount || current?.progressTotal || current?.linkCount || 0);
  await lineReply(env, event.replyToken,
    `🔁 將沿用 ${snap.week} 已解析的素材重新生成。\n` +
    (linkCount ? `原始連結：${linkCount} 個\n` : "") +
    `生成模型：${label}\n\n不會重新讀取聊天室連結，確定要繼續嗎？`,
    [
      { label: "✅ 確認重新生成", text: "確認重新生成上次週報" },
      { label: "❌ 取消", text: "取消生成週報" },
    ]);
}

async function confirmRegeneration(env, event) {
  const sourceId = sourceInfo(event.source).id;
  const current = parseJson(await getState(env, JOB_STATUS_KEY));
  if (isActiveJob(current)) {
    await lineReply(env, event.replyToken, await generationStatusMessage(env, sourceId));
    return;
  }
  const snap = parseJson(await getState(env, "snapshot"), {});
  if (!snap.week) {
    await lineReply(env, event.replyToken, "找不到上一次週報資料，已取消重新生成。");
    return;
  }
  const model = (await getState(env, "model")) || defaultModel(env);
  const label = (availableModels(env).find((m) => m.value === model) || {}).label || model;
  const requestedAt = Number(event.timestamp || Date.now());
  const linkCount = Number(snap.linkCount || current?.progressTotal || current?.linkCount || 0);
  const job = {
    id: `${requestedAt}-regen`, status: "queued", requestedAt,
    updatedAt: Date.now(), model, modelLabel: label, linkCount, sourceId,
    progressDone: linkCount, progressTotal: linkCount,
    mode: "regenerate", week: snap.week,
    phase: `等待 Mac 重新生成 ${snap.week}`,
  };
  await setState(env, "generate_requested", job.id);
  await setState(env, JOB_STATUS_KEY, JSON.stringify(job));
  await lineReply(env, event.replyToken,
    `🔁 ${snap.week} 已排入重新生成佇列（模型：${label}）。\n` +
    "Mac 最多約 3 分鐘會開始；再次按「生成週報」可查看進度。");
}

async function handleWebhook(request, env) {
  const bodyText = await request.text();
  const ok = await verifyLineSignature(
    env.LINE_CHANNEL_SECRET, bodyText, request.headers.get("x-line-signature")
  );
  if (!ok) return new Response("bad signature", { status: 403 });

  const body = JSON.parse(bodyText);
  for (const event of body.events || []) {
    if (event.type !== "message" || event.message?.type !== "text") continue;
    const text = event.message.text || "";
    const src = sourceInfo(event.source);
    if (!isAllowedSource(env, src.id)) continue;
    const urls = text.match(URL_RE) || [];

    if (text.trim() === "生成週報") {
      await handleGenerateButton(env, event);
      continue;
    }
    if (text.trim() === "重新生成上次週報") {
      await handleRegenerateButton(env, event);
      continue;
    }
    if (text.trim() === "確認生成週報") {
      await confirmGeneration(env, event);
      continue;
    }
    if (text.trim() === "確認重新生成上次週報") {
      await confirmRegeneration(env, event);
      continue;
    }
    if (text.trim() === "取消生成週報") {
      await lineReply(env, event.replyToken, "已取消，未建立週報生成工作。");
      continue;
    }

    if (urls.length > 0) {
      for (const url of urls) {
        await env.DB.prepare(
          "INSERT OR IGNORE INTO links " +
          "(url, message_text, source_type, source_id, sender_id, line_timestamp, webhook_event_id) " +
          "VALUES (?, ?, ?, ?, ?, ?, ?)"
        ).bind(url, text, src.type, src.id, event.source?.userId || "", event.timestamp,
          event.webhookEventId || null).run();
      }
    } else if (text.trim() === "生成週報") {
      // 手動觸發：設旗標，Mac 端輪詢器（每 3 分鐘）會取走並執行 pipeline
      const model = (await getState(env, "model")) || defaultModel(env);
      const label = (availableModels(env).find((m) => m.value === model) || {}).label || model;
      const current = parseJson(await getState(env, JOB_STATUS_KEY));
      const runningIsAbandoned = current?.status === "running" &&
        Date.now() - Number(current.updatedAt || current.startedAt || 0) > 6 * 60 * 60 * 1000;
      if (current && ACTIVE_JOB_STATUSES.has(current.status) && !runningIsAbandoned) {
        await lineReply(env, event.replyToken,
          `ℹ️ 已有一份週報${current.status === "queued" ? "在排隊" : "正在生成"}，本次不重複排入。\n\n` +
          await generationStatusMessage(env, src.id));
        continue;
      }
      const requestedAt = Number(event.timestamp || Date.now());
      const job = {
        id: String(requestedAt), status: "queued", requestedAt,
        updatedAt: Date.now(), model, modelLabel: label, sourceId: src.id,
        phase: "等待 Mac 接手",
      };
      await setState(env, "generate_requested", job.id);
      await setState(env, JOB_STATUS_KEY, JSON.stringify(job));
      await lineReply(env, event.replyToken,
        `🗞️ 已排入生成佇列（模型：${label}）。\nMac 端每 3 分鐘檢查一次，開始與完成時都會通知你。`);
    } else if (text.trim() === "查看生成進度") {
      await lineReply(env, event.replyToken, await generationStatusMessage(env, src.id));
    } else if (text.trim() === "指定生成模型") {
      const models = availableModels(env);
      const current = (await getState(env, "model")) || defaultModel(env);
      const curLabel = (models.find((m) => m.value === current) || {}).label || current;
      await lineReply(env, event.replyToken,
        `目前模型：${curLabel}\n請選擇下次生成使用的模型：`,
        models.map((m) => ({ label: m.label, text: `設定模型：${m.label}` })));
    } else if (text.trim().startsWith("設定模型：")) {
      const label = text.trim().slice("設定模型：".length).trim();
      const hit = availableModels(env).find((m) => m.label === label);
      if (hit) {
        await setState(env, "model", hit.value);
        await lineReply(env, event.replyToken, `✅ 已設定生成模型：${hit.label}\n下次「生成週報」即套用。`);
      } else {
        await lineReply(env, event.replyToken, `找不到模型「${label}」，請點「指定生成模型」重新選擇。`);
      }
    } else if (text.trim() === "查看待處理清單") {
      const snap = JSON.parse((await getState(env, "snapshot")) || "{}");
      const items = snap.unresolved || [];
      const msg = items.length
        ? `⚠️ 上次生成（${snap.week || "—"}）有 ${items.length} 條連結無法自動解析：\n\n` +
          items.slice(0, 10).map((u, i) => `〔${i + 1}〕\n${u}`).join("\n\n")
        : `✅ 上次生成（${snap.week || "尚未生成過"}）沒有待處理的連結。`;
      await lineReply(env, event.replyToken, msg);
    } else if (text.trim() === "本週新術語介紹") {
      const snap = JSON.parse((await getState(env, "snapshot")) || "{}");
      const terms = snap.terms || [];
      if (!terms.length) {
        await lineReply(env, event.replyToken,
          "還沒有本週術語資料——先「生成週報」一次，術語摘要會同步到這裡。");
      } else {
        // 每次隨機挑一個介紹，再按再抽
        const t = terms[Math.floor(Math.random() * terms.length)];
        await lineReply(env, event.replyToken,
          `📖 本週術語隨機一則（${snap.week || "—"}）\n\n◉ ${t.term}\n\n${t.blurb}\n\n（共 ${terms.length} 個，再按一次換下一個）`);
      }
    } else if (NUMERIC_REPLY_RE.test(text.trim())) {
      // 純數字回覆：只在「等待勾選」狀態時視為術語選擇
      const row = await env.DB.prepare("SELECT value FROM state WHERE key = 'awaiting_week'").first();
      if (row && row.value) {
        await env.DB.prepare(
          "INSERT INTO term_selections (week, raw_reply, sender_id, line_timestamp) VALUES (?, ?, ?, ?)"
        ).bind(row.value, text.trim(), event.source?.userId || "", event.timestamp).run();
      }
    }
  }
  return new Response("ok");
}

function requireSecret(request, env) {
  const bearer = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  const supplied = bearer || request.headers.get("x-api-secret") || "";
  return !!env.API_SECRET && supplied === env.API_SECRET;
}

async function createApiJob(env, body) {
  const current = parseJson(await getState(env, JOB_STATUS_KEY));
  if (isActiveJob(current)) {
    return Response.json({ ok: false, error: "job already active", job: current }, { status: 409 });
  }
  const sourceId = defaultSourceId(env) || "api";
  const mode = body.mode === "regenerate" ? "regenerate" : "all";
  const requestedAt = Date.now();
  const configuredModel = (await getState(env, "model")) || defaultModel(env);
  const model = String(body.model || configuredModel).slice(0, 120);
  const job = {
    id: `${requestedAt}-api`, status: "queued", requestedAt, updatedAt: requestedAt,
    sourceId, mode, week: body.week || null, model,
    phase: "等待 Mac 接手",
  };
  await setState(env, "generate_requested", job.id);
  await setState(env, JOB_STATUS_KEY, JSON.stringify(job));
  return Response.json({ ok: true, job }, { status: 202 });
}

async function ingestApiLinks(env, body) {
  const items = Array.isArray(body.items) ? body.items.slice(0, 100) : [];
  if (!items.length) {
    return Response.json({ ok: false, error: "items must be a non-empty array" }, { status: 400 });
  }
  const sourceId = defaultSourceId(env) || "api";
  let accepted = 0;
  for (const [index, item] of items.entries()) {
    let parsed;
    try { parsed = new URL(String(item.url || "")); } catch (_) { continue; }
    if (!['http:', 'https:'].includes(parsed.protocol)) continue;
    const externalId = String(item.external_id || `${Date.now()}-${index}`).slice(0, 200);
    const timestamp = Number(item.timestamp || Date.now());
    await env.DB.prepare(
      "INSERT OR IGNORE INTO links " +
      "(url, message_text, source_type, source_id, sender_id, line_timestamp, webhook_event_id) " +
      "VALUES (?, ?, 'api', ?, 'api', ?, ?)"
    ).bind(parsed.toString(), String(item.text || "").slice(0, 5000), sourceId,
      timestamp, `api:${externalId}`).run();
    accepted += 1;
  }
  return Response.json({ ok: true, accepted, source_id: sourceId }, { status: 202 });
}

async function claimPendingJob(env) {
  const row = await env.DB.prepare(
    "SELECT value FROM state WHERE key = 'generate_requested'").first();
  if (!row?.value) return new Response(null, { status: 204 });
  const now = Date.now();
  const job = parseJson(await getState(env, JOB_STATUS_KEY), {});
  if (Number(job.leaseExpiresAt || 0) > now) return new Response(null, { status: 204 });
  const model = job.model || (await getState(env, "model")) || defaultModel(env);
  const claimed = {
    ...job, id: job.id || row.value, status: "running", model,
    phase: "Mac 已領取工作，準備啟動", claimedAt: now,
    startedAt: job.startedAt || now, updatedAt: now,
    leaseExpiresAt: now + CLAIM_LEASE_MS,
  };
  await setState(env, JOB_STATUS_KEY, JSON.stringify(claimed));
  return Response.json({ ok: true, job: claimed });
}

export default {
  async scheduled(_event, env, ctx) {
    const configured = Number.parseInt(env.DATA_RETENTION_DAYS || "90", 10);
    const days = Math.min(3650, Math.max(1, configured || 90));
    const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
    ctx.waitUntil(Promise.all([
      env.DB.prepare("DELETE FROM links WHERE line_timestamp < ?").bind(cutoff).run(),
      env.DB.prepare("DELETE FROM term_selections WHERE line_timestamp < ?").bind(cutoff).run(),
    ]));
  },

  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/healthz" && request.method === "GET") {
      return Response.json({ ok: true, service: "weekly-report-collector", api: API_VERSION });
    }

    if (url.pathname === "/webhook" && request.method === "POST") {
      return handleWebhook(request, env);
    }

    const uploadMatch = url.pathname.match(/^\/artifacts\/(\d{4}-W\d{2})\/(report|podcast)$/);
    if (uploadMatch && request.method === "PUT") {
      if (!requireSecret(request, env)) return new Response("forbidden", { status: 403 });
      const [, week, kind] = uploadMatch;
      if (!ARTIFACT_WEEK_RE.test(week)) return new Response("invalid week", { status: 400 });
      return uploadArtifact(request, env, week, kind);
    }

    const mediaMatch = url.pathname.match(/^\/media\/(\d{4}-W\d{2})\/(report|podcast)$/);
    if (mediaMatch && (request.method === "GET" || request.method === "HEAD")) {
      const [, week, kind] = mediaMatch;
      return serveArtifact(request, env, url, week, kind);
    }

    if ((url.pathname === "/links" || url.pathname === "/api/v1/links") &&
        request.method === "GET") {
      if (!requireSecret(request, env)) return new Response("forbidden", { status: 403 });
      const since = Number(url.searchParams.get("since") || 0);
      const sourceId = url.searchParams.get("source_id") || defaultSourceId(env);
      if (!sourceId) {
        return Response.json({ ok: false, error: "no allowed source configured" }, { status: 400 });
      }
      const { results } = await env.DB.prepare(
        "SELECT * FROM links WHERE source_id = ? AND line_timestamp >= ? ORDER BY line_timestamp"
      ).bind(sourceId, since).all();
      return Response.json(results);
    }

    if (url.pathname === "/api/v1/links" && request.method === "POST") {
      if (!requireSecret(request, env)) return new Response("forbidden", { status: 403 });
      return ingestApiLinks(env, await request.json());
    }

    if (url.pathname === "/api/v1/jobs" && request.method === "POST") {
      if (!requireSecret(request, env)) return new Response("forbidden", { status: 403 });
      return createApiJob(env, await request.json());
    }

    if (url.pathname === "/api/v1/jobs/claim" && request.method === "POST") {
      if (!requireSecret(request, env)) return new Response("forbidden", { status: 403 });
      await setState(env, MAC_LAST_POLL_KEY, String(Date.now()));
      return claimPendingJob(env);
    }

    const apiJobMatch = url.pathname.match(/^\/api\/v1\/jobs\/([^/]+)$/);
    if (apiJobMatch && request.method === "GET") {
      if (!requireSecret(request, env)) return new Response("forbidden", { status: 403 });
      const job = parseJson(await getState(env, JOB_STATUS_KEY));
      if (!job || job.id !== decodeURIComponent(apiJobMatch[1])) {
        return Response.json({ ok: false, error: "job not found" }, { status: 404 });
      }
      return Response.json({ ok: true, job });
    }

    if (url.pathname === "/selections" && request.method === "GET") {
      if (!requireSecret(request, env)) return new Response("forbidden", { status: 403 });
      const week = url.searchParams.get("week") || "";
      const { results } = await env.DB.prepare(
        "SELECT * FROM term_selections WHERE week = ? ORDER BY line_timestamp"
      ).bind(week).all();
      return Response.json(results);
    }

    if (url.pathname === "/trigger" && request.method === "GET") {
      if (!requireSecret(request, env)) return new Response("forbidden", { status: 403 });
      const polledAt = Date.now();
      await setState(env, MAC_LAST_POLL_KEY, String(polledAt));
      const row = await env.DB.prepare(
        "SELECT value FROM state WHERE key = 'generate_requested'").first();
      const pending = !!(row && row.value);
      let job = parseJson(await getState(env, JOB_STATUS_KEY), {});
      const model = (await getState(env, "model")) || defaultModel(env);
      const claimable = pending && Number(job.leaseExpiresAt || 0) <= polledAt;
      return Response.json({
        run: claimable, model,
        job_id: claimable ? (job.id || row.value) : null,
        mode: claimable ? (job.mode || "all") : null,
        week: claimable ? (job.week || null) : null,
      });
    }

    // Mac pipeline 回報執行階段，讓 LINE 在 Mac 離線時仍可查詢最後狀態。
    if (url.pathname === "/job-status" && request.method === "POST") {
      if (!requireSecret(request, env)) return new Response("forbidden", { status: 403 });
      const update = await request.json();
      const current = parseJson(await getState(env, JOB_STATUS_KEY), {});
      if (current.id && update.id && current.id !== update.id) {
        return Response.json({ ok: false, error: "job id mismatch" }, { status: 409 });
      }
      const now = Date.now();
      const status = ["running", "completed", "failed"].includes(update.status)
        ? update.status : (current.status || "running");
      const job = {
        ...current, id: update.id || current.id, status,
        phase: String(update.phase || current.phase || "").slice(0, 200), updatedAt: now,
      };
      const progressDone = Number(update.progress_done);
      const progressTotal = Number(update.progress_total);
      if (Number.isFinite(progressDone) && progressDone >= 0) job.progressDone = progressDone;
      if (Number.isFinite(progressTotal) && progressTotal >= 0) job.progressTotal = progressTotal;
      if (!job.startedAt && status === "running") job.startedAt = now;
      if (status === "running") {
        job.leaseExpiresAt = now + CLAIM_LEASE_MS;
        await env.DB.prepare("DELETE FROM state WHERE key = 'generate_requested'").run();
      }
      if (status === "completed") job.completedAt = now;
      if (status === "failed") {
        job.failedAt = now;
        job.error = String(update.error || "").slice(0, 500);
      }
      if (["ready", "failed"].includes(update.delivery_status)) {
        job.deliveryStatus = update.delivery_status;
      }
      if (update.artifacts && typeof update.artifacts === "object") {
        job.artifacts = update.artifacts;
      }
      await setState(env, JOB_STATUS_KEY, JSON.stringify(job));
      return Response.json({ ok: true, job });
    }

    // Mac 端生成完成後回傳摘要（術語＋待處理清單），供 Rich Menu 按鈕即時查詢
    if (url.pathname === "/snapshot" && request.method === "POST") {
      if (!requireSecret(request, env)) return new Response("forbidden", { status: 403 });
      const snap = await request.json();
      await setState(env, "snapshot", JSON.stringify(snap));
      return Response.json({ ok: true });
    }

    if (url.pathname === "/await-selection" && request.method === "POST") {
      if (!requireSecret(request, env)) return new Response("forbidden", { status: 403 });
      const { week } = await request.json();
      await env.DB.prepare(
        "INSERT INTO state (key, value) VALUES ('awaiting_week', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value"
      ).bind(week || "").run();
      return Response.json({ ok: true, awaiting: week || null });
    }

    return new Response("weekly-report collector", { status: 200 });
  },
};
