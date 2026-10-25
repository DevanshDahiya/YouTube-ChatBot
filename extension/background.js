// Step 18: the service worker. It is the only part that talks to the backend.
// Content scripts send it a message; it calls the API and sends back a plain result.

const BACKEND_URL = "http://127.0.0.1:8000"; // swapped for the deployed URL on Day 3

// Always resolves to {ok: true, data} or {ok: false, error: {code, message}}. Never throws.
async function api(path, options) {
  let response;
  try {
    response = await fetch(BACKEND_URL + path, options);
  } catch {
    return {
      ok: false,
      error: { code: "backend_unreachable", message: "Can't reach the backend. Is it running on 127.0.0.1:8000?" },
    };
  }

  let body = null;
  try {
    body = await response.json();
  } catch {
    // not JSON: keep body null
  }

  if (response.ok) return { ok: true, data: body };

  // Our backend sends {"detail": {"code", "message"}}. FastAPI's own validation errors send a LIST of problems.
  const detail = body && body.detail;
  let error;
  if (detail && detail.code) {
    error = detail;
  } else if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0]; // e.g. {loc: ["body", "video_id"], msg: "Field required"}
    error = { code: "bad_request", message: `Bad request: ${(first.loc || []).join(".")} ${first.msg}` };
  } else {
    error = { code: "http_" + response.status, message: `Backend error (${response.status}).` };
  }
  return { ok: false, error };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "ingest") {
    api("/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_id: message.videoId }),
    }).then(sendResponse);
    return true; // tells Chrome we will reply later, so keep the message channel open
  }
  if (message.type === "summary") {
    api(`/summary/${encodeURIComponent(message.videoId)}`).then(sendResponse);
    return true;
  }
  return false;
});
