// Detects the current video, even though YouTube never reloads the page, and drives the sidebar.

let currentVideoId = null;
let activePort = null; // the open connection while an answer is streaming
let chatHistory = []; // [{role: "user" | "assistant", content}] for the current video
let videoReady = false; // true once the backend has finished preparing this video
let summaryPort = null; // the open connection while the summary is being made

function getVideoId() {
  const url = new URL(location.href);
  if (url.pathname !== "/watch") return null; // home page, search, Shorts, ... are ignored for now
  return url.searchParams.get("v");
}

// Ask the background worker to do something. Always resolves to {ok, data} or {ok: false, error}.
async function callBackground(message) {
  try {
    const reply = await chrome.runtime.sendMessage(message);
    return reply || { ok: false, error: { code: "no_reply", message: "No reply from the extension." } };
  } catch {
    // Happens when the extension was reloaded while this tab stayed open.
    return { ok: false, error: { code: "extension_error", message: "The extension was reloaded. Refresh this page." } };
  }
}

// Jump the YouTube player to a moment. Called when a timestamp in the sidebar is clicked.
function seekTo(seconds) {
  const player = document.querySelector("#movie_player");
  if (player && player.classList.contains("ad-showing")) {
    Sidebar.notice("An ad is playing. Try again when it ends.");
    return;
  }
  const video = document.querySelector("#movie_player video") || document.querySelector("video");
  if (!video) {
    Sidebar.notice("Couldn't find the video player.");
    return;
  }
  video.currentTime = seconds;
  video.play().catch(() => {}); // a click counts as a user gesture, so this is normally allowed
}

// Asks for the summary through a long-lived port (see background.js: it keeps the worker alive while we wait).
function loadSummary(videoId) {
  closeSummaryPort();
  Sidebar.setSummaryStatus("Summarizing... the first time can take a few minutes.", false, true);

  let port;
  try {
    port = chrome.runtime.connect({ name: "summary" });
  } catch {
    Sidebar.setSummaryStatus("The extension was reloaded. Refresh this page.", true);
    return;
  }
  summaryPort = port;
  let gotResult = false;

  port.onMessage.addListener((message) => {
    if (port !== summaryPort || videoId !== currentVideoId) return; // you moved on while we waited
    if (message.type === "result") {
      gotResult = true;
      if (message.result.ok) {
        Sidebar.setSummaryStatus("");
        Sidebar.setSummary(message.result.data, seekTo);
      } else {
        Sidebar.setSummaryStatus(message.result.error.message, true);
      }
    } else if (message.type === "closed") {
      if (!gotResult) Sidebar.setSummaryStatus("The summary was interrupted. Reload the page to retry.", true);
      closeSummaryPort();
    }
  });
  port.onDisconnect.addListener(() => {
    if (port === summaryPort) {
      summaryPort = null;
      if (!gotResult && videoId === currentVideoId) Sidebar.setSummaryStatus("The summary was interrupted. Reload the page to retry.", true);
    }
  });
  port.postMessage({ videoId });
}

function closeSummaryPort() {
  if (!summaryPort) return;
  const port = summaryPort;
  summaryPort = null; // set first so the disconnect handler does not report an error
  try {
    port.disconnect();
  } catch {
    // already closed
  }
}

// "New chat": stop any answer in progress, forget the conversation, empty the chat box.
function newChat() {
  if (activePort) {
    const port = activePort;
    activePort = null;
    port.disconnect();
  }
  chatHistory = [];
  Sidebar.clearChat();
  Sidebar.setChatEnabled(videoReady, "This video isn't ready.");
}

// Called when the chat is over (answer finished, failed, or was cut off).
function endChat(answer) {
  if (answer.isPending()) answer.setError("No answer received. Please try again.");
  activePort = null;
  Sidebar.setChatEnabled(videoReady, "This video isn't ready.");
}

// Called by the sidebar when the user sends a question. Streams the answer through a long-lived port.
function askQuestion(question) {
  const videoId = currentVideoId;
  if (!videoId || activePort) return; // one question at a time
  Sidebar.addUserMessage(question);
  const answer = Sidebar.startAssistantMessage();
  Sidebar.setChatEnabled(false, "Waiting for the answer...");

  let port;
  try {
    port = chrome.runtime.connect({ name: "ask" });
  } catch {
    answer.setError("The extension was reloaded. Refresh this page.");
    endChat(answer);
    return;
  }
  activePort = port;

  let streamed = "";
  let gotFinal = false;
  let sources = []; // the moments the backend retrieved for this question
  port.onMessage.addListener((event) => {
    if (port !== activePort) return; // an old stream: ignore it
    if (event.type === "sources") {
      sources = event.sources || [];
    } else if (event.type === "token") {
      streamed += event.text;
      answer.setText(streamed);
    } else if (event.type === "final") {
      gotFinal = true;
      // The citation-checked version replaces the raw stream, and its [M:SS] marks become buttons.
      answer.setAnswer(event.answer, seekTo);
      // A small model sometimes cites nothing: then show the moments it was based on, labelled as such.
      if (!event.citations || event.citations.length === 0) answer.setRelated(sources, seekTo);
      chatHistory.push({ role: "user", content: question }, { role: "assistant", content: event.answer });
    } else if (event.type === "error") {
      if (event.message) answer.setError(event.message);
    } else if (event.type === "closed") {
      if (!gotFinal && streamed && !answer.hasError()) {
        answer.setText(streamed + "\n\n(The answer was cut off. Please try again.)");
      }
      port.disconnect();
      endChat(answer);
    }
  });
  port.onDisconnect.addListener(() => {
    if (port === activePort) endChat(answer);
  });
  port.postMessage({ videoId, question, history: chatHistory.slice(-6) });
}

// Reads the transcript straight from the YouTube page itself (same origin: YouTube never blocks this),
// so the backend does not have to ask YouTube and risk being blocked (common on cloud/free hosts).
//
// Strategy:
//   1. Read ytInitialPlayerResponse from inline <script> tags already in the DOM — fast, no extra
//      request, and correct even after YouTube's in-page navigation fires.
//   2. If the DOM data belongs to a different video (stale after SPA navigation), re-fetch the page.
//   3. Fetch the actual caption file using the track URL found in the player response.
//      Try fmt=json3 first; fall back to the raw URL if that fails.
//
// Returns [{text, start, duration}, ...] or null on any failure.
// If null is returned, the backend will attempt its own fetch — which may be blocked on cloud hosts.
async function fetchPageTranscript() {
  try {
    const videoId = currentVideoId;
    let player = null;
    const marker = "ytInitialPlayerResponse = ";

    // 1. Try inline scripts already present in the DOM (avoids a full page re-fetch on first load).
    for (const script of document.querySelectorAll("script:not([src])")) {
      const text = script.textContent;
      const at = text.indexOf(marker);
      if (at === -1) continue;
      const jsonStart = at + marker.length;
      let depth = 0, end = -1;
      for (let i = jsonStart; i < text.length; i++) {
        if (text[i] === "{") depth++;
        else if (text[i] === "}") { if (--depth === 0) { end = i + 1; break; } }
      }
      if (end === -1) continue;
      try {
        const p = JSON.parse(text.slice(jsonStart, end));
        // Guard: the inline data can be stale after SPA navigation — only use it for the right video.
        if (p?.videoDetails?.videoId === videoId) { player = p; break; }
      } catch { /* malformed block — try the next script tag */ }
    }

    // 2. DOM data was missing or for a different video — fetch the page fresh.
    if (!player) {
      const html = await (await fetch(location.href, { credentials: "same-origin" })).text();
      const at = html.indexOf(marker);
      if (at === -1) return null;
      const jsonStart = at + marker.length;
      let depth = 0, end = -1;
      for (let i = jsonStart; i < html.length; i++) {
        if (html[i] === "{") depth++;
        else if (html[i] === "}") { if (--depth === 0) { end = i + 1; break; } }
      }
      if (end === -1) return null;
      try { player = JSON.parse(html.slice(jsonStart, end)); } catch { return null; }
    }

    const tracks = player?.captions?.playerCaptionsTracklistRenderer?.captionTracks;
    if (!tracks || tracks.length === 0) return null;

    // Prefer English, and prefer hand-made captions over auto-generated ("asr") ones.
    const score = (t) => (t.languageCode?.startsWith("en") ? 2 : 0) + (t.kind !== "asr" ? 1 : 0);
    const track = [...tracks].sort((a, b) => score(b) - score(a))[0];

    // Fetch the caption file. No explicit credentials= needed — same-origin is the default.
    // Retry without fmt=json3 if the server rejects it (some tracks only serve the default format).
    let captionResponse = await fetch(track.baseUrl + "&fmt=json3");
    if (!captionResponse.ok) {
      captionResponse = await fetch(track.baseUrl);
      if (!captionResponse.ok) {
        console.warn("[YT ChatBot] Caption URL returned", captionResponse.status, "— will let backend try.");
        return null;
      }
    }
    const data = await captionResponse.json();
    const snippets = (data.events || [])
      .filter((e) => e.segs && e.segs.length)
      .map((e) => ({
        text: e.segs
          .map((s) => s.utf8 || "")
          .join("")
          .replace(/\n/g, " ")
          .trim(),
        start: (e.tStartMs || 0) / 1000,
        duration: (e.dDurationMs || 0) / 1000,
      }))
      .filter((s) => s.text);

    if (snippets.length > 0) {
      console.log("[YT ChatBot] Browser fetched", snippets.length, "transcript snippets — backend will not call YouTube.");
      return snippets;
    }
    return null;
  } catch (err) {
    console.warn("[YT ChatBot] fetchPageTranscript failed:", err);
    return null; // backend will attempt its own fetch (may be blocked on cloud hosts)
  }
}

async function prepareVideo(videoId) {
  Sidebar.setStatus("Preparing this video...", false, true);
  const transcript = await fetchPageTranscript();
  if (videoId !== currentVideoId) return; // you moved to another video while this was reading the page
  if (!transcript) {
    // fetchPageTranscript() failed or the video has no captions the extension could read.
    // The backend will try to fetch the transcript itself — this will fail on cloud hosts
    // (Render, Railway, …) because YouTube blocks requests from their IP ranges.
    // If REQUIRE_BROWSER_TRANSCRIPT=true is set on the server, a clear error will be returned.
    console.warn("[YT ChatBot] Could not read transcript from page — backend will attempt its own fetch (may be blocked on cloud hosts).");
  }
  const result = await callBackground({ type: "ingest", videoId, transcript });
  if (videoId !== currentVideoId) return; // you moved to another video while we waited: drop this answer
  if (result.ok) {
    const { chunks, cached } = result.data;
    Sidebar.setStatus(`Ready \u00b7 ${chunks} sections${cached ? " (already prepared)" : ""}`);
    videoReady = true;
    Sidebar.setChatEnabled(true);
    loadSummary(videoId);
  } else {
    Sidebar.setStatus(result.error.message, true);
    Sidebar.setChatEnabled(false, "This video can't be used.");
  }
}

function onVideoChanged(videoId) {
  console.log("[YT ChatBot] video changed:", videoId);
  // Anything belonging to the previous video stops here.
  if (activePort) {
    const port = activePort;
    activePort = null;
    port.disconnect(); // tells the background worker to stop reading the old answer
  }
  closeSummaryPort();
  chatHistory = [];
  videoReady = false;
  if (videoId) {
    Sidebar.show(videoId);
    prepareVideo(videoId);
  } else {
    Sidebar.hide();
  }
}

function checkVideo() {
  const id = getVideoId();
  if (id === currentVideoId) return; // nothing new: ignore duplicate signals
  currentVideoId = id;
  onVideoChanged(id);
}

Sidebar.onAsk(askQuestion);
Sidebar.onNewChat(newChat);

// 1) YouTube fires this on `document` every time its in-page navigation finishes.
document.addEventListener("yt-navigate-finish", checkVideo);
// 2) Safety net: a cheap check once a second also keeps the sidebar attached if YouTube re-renders.
setInterval(() => {
  checkVideo();
  if (currentVideoId) Sidebar.keepAttached();
}, 1000);
// 3) The page we landed on at load time.
checkVideo();
 