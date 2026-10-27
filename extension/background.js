// The service worker. It is the only part that talks to the backend.
// Content scripts send it a message (or open a port for streaming); it calls the API and replies with plain results.

importScripts("config.js"); // defines BACKEND_URL and API_KEY

// Headers for every request. The key is only sent when one is configured.
function headers(extra = {}) {
  return API_KEY ? { ...extra, "X-API-Key": API_KEY } : extra;
}

// Turn a failed HTTP response into {code, message}.
// Our backend sends {"detail": {"code", "message"}}. FastAPI's own validation errors send a LIST of problems.
function toError(status, body) {
  const detail = body && body.detail;
  if (detail && detail.code) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0]; // e.g. {loc: ["body", "video_id"], msg: "Field required"}
    return { code: "bad_request", message: `Bad request: ${(first.loc || []).join(".")} ${first.msg}` };
  }
  return { code: "http_" + status, message: `Backend error (${status}).` };
}

const UNREACHABLE = {
  code: "backend_unreachable",
  message: "Can't reach the backend. Is the server running and is BACKEND_URL in config.js right?",
};

// One-shot requests. Always resolves to {ok: true, data} or {ok: false, error: {code, message}}. Never throws.
async function api(path, options) {
  let response;
  try {
    response = await fetch(BACKEND_URL + path, { ...options, headers: headers(options && options.headers) });
  } catch {
    return { ok: false, error: UNREACHABLE };
  }

  let body = null;
  try {
    body = await response.json();
  } catch {
    // not JSON: keep body null
  }

  if (response.ok) return { ok: true, data: body };
  return { ok: false, error: toError(response.status, body) };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "ingest") {
    api("/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_id: message.videoId, transcript: message.transcript || undefined }),
    }).then(sendResponse);
    return true; // tells Chrome we will reply later, so keep the message channel open
  }
  return false;
});

// ---- Streaming answers (Step 20) -------------------------------------------------------------

// Turn one Server-Sent Event block ("event: token\ndata: {...}") into {type: "token", ...data}.
function parseSseBlock(block) {
  let name = "message";
  const dataLines = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  try {
    return { type: name, ...JSON.parse(dataLines.join("\n")) };
  } catch {
    return null; // a broken event is skipped instead of breaking the whole answer
  }
}

// Runs the /ask request and forwards every event to the content script through the port.
async function streamAnswer(port, { videoId, question, history }) {
  let open = true;
  const controller = new AbortController();
  port.onDisconnect.addListener(() => {
    open = false;
    controller.abort(); // the tab closed or moved to another video: stop reading
  });
  const send = (message) => {
    if (open) port.postMessage(message);
  };
  // Messages sent through the port count as activity, so Chrome does not stop this worker during a slow answer.
  const keepAlive = setInterval(() => send({ type: "ping" }), 20000);

  try {
    const response = await fetch(BACKEND_URL + "/ask", {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ video_id: videoId, question, chat_history: history || [] }),
      signal: controller.signal,
    });

    if (!response.ok) {
      let body = null;
      try {
        body = await response.json();
      } catch {
        // keep null
      }
      send({ type: "error", message: toError(response.status, body).message });
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let end;
      while ((end = buffer.indexOf("\n\n")) !== -1) {
        const event = parseSseBlock(buffer.slice(0, end));
        buffer = buffer.slice(end + 2);
        if (event) send(event);
      }
    }
  } catch {
    send({ type: "error", message: open ? UNREACHABLE.message : "" });
  } finally {
    clearInterval(keepAlive);
    send({ type: "closed" });
  }
}

// The summary can take minutes the first time. A one-shot message would let Chrome stop this worker after ~30s
// of silence, so it runs over a port that sends a ping every 20s until the reply is ready.
async function runSummary(port, { videoId }) {
  let open = true;
  port.onDisconnect.addListener(() => (open = false));
  const keepAlive = setInterval(() => open && port.postMessage({ type: "ping" }), 20000);
  try {
    const result = await api(`/summary/${encodeURIComponent(videoId)}`);
    if (open) port.postMessage({ type: "result", result });
  } finally {
    clearInterval(keepAlive);
    if (open) port.postMessage({ type: "closed" });
  }
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name === "ask") port.onMessage.addListener((message) => streamAnswer(port, message));
  else if (port.name === "summary") port.onMessage.addListener((message) => runSummary(port, message));
});
