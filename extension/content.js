// Detects the current video, even though YouTube never reloads the page, and drives the sidebar.

let currentVideoId = null;
let activePort = null; // the open connection while an answer is streaming
let chatHistory = []; // [{role: "user" | "assistant", content}] for the current video
let videoReady = false; // true once the backend has finished preparing this video

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

async function loadSummary(videoId) {
  Sidebar.setSummaryStatus("Summarizing... the first time can take a few minutes.");
  const result = await callBackground({ type: "summary", videoId });
  if (videoId !== currentVideoId) return; // you moved on while we waited
  if (result.ok) {
    Sidebar.setSummaryStatus("");
    Sidebar.setSummary(result.data, seekTo);
  } else {
    Sidebar.setSummaryStatus(result.error.message, true);
  }
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

async function prepareVideo(videoId) {
  Sidebar.setStatus("Preparing this video...");
  const result = await callBackground({ type: "ingest", videoId });
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

// 1) YouTube fires this on `document` every time its in-page navigation finishes.
document.addEventListener("yt-navigate-finish", checkVideo);
// 2) Safety net: a cheap check once a second also keeps the sidebar attached if YouTube re-renders.
setInterval(() => {
  checkVideo();
  if (currentVideoId) Sidebar.keepAttached();
}, 1000);
// 3) The page we landed on at load time.
checkVideo();
