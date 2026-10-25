// Detects the current video, even though YouTube never reloads the page, and drives the sidebar.

let currentVideoId = null;

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

async function prepareVideo(videoId) {
  Sidebar.setStatus("Preparing this video...");
  const result = await callBackground({ type: "ingest", video_id: videoId });
  if (videoId !== currentVideoId) return; // you moved to another video while we waited: drop this answer
  if (result.ok) {
    const { chunks, cached } = result.data;
    Sidebar.setStatus(`Ready \u00b7 ${chunks} sections${cached ? " (already prepared)" : ""}`);
  } else {
    Sidebar.setStatus(result.error.message, true);
  }
}

function onVideoChanged(videoId) {
  console.log("[YT ChatBot] video changed:", videoId);
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

// 1) YouTube fires this on `document` every time its in-page navigation finishes.
document.addEventListener("yt-navigate-finish", checkVideo);
// 2) Safety net: a cheap check once a second also keeps the sidebar attached if YouTube re-renders.
setInterval(() => {
  checkVideo();
  if (currentVideoId) Sidebar.keepAttached();
}, 1000);
// 3) The page we landed on at load time.
checkVideo();