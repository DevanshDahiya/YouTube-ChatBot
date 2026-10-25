// Detects the current video, even though YouTube never reloads the page, and drives the sidebar.

let currentVideoId = null;

function getVideoId() {
  const url = new URL(location.href);
  if (url.pathname !== "/watch") return null; // home page, search, Shorts, ... are ignored for now
  return url.searchParams.get("v");
}

// Later steps will ingest the video and fill the sidebar here.
function onVideoChanged(videoId) {
  console.log("[YT ChatBot] video changed:", videoId);
  if (videoId) Sidebar.show(videoId);
  else Sidebar.hide();
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