// content.js (same isolated world) calls Sidebar.show(videoId) / Sidebar.hide().

const SIDEBAR_CSS = `
/* Everything here applies ONLY inside our Shadow DOM, so YouTube's CSS cannot touch it and ours cannot leak out. */

:host {
  display: block;
  margin-bottom: 16px;
  --bg: #ffffff;
  --text: #0f0f0f;
  --muted: #606060;
  --border: #d9d9d9;
  --accent: #065fd4;
  --soft: #f2f2f2;
}

/* YouTube marks dark mode with a "dark" attribute on <html>. */
:host-context(html[dark]) {
  --bg: #212121;
  --text: #f1f1f1;
  --muted: #aaaaaa;
  --border: #3f3f3f;
  --accent: #3ea6ff;
  --soft: #2d2d2d;
}

/* Fallback when we could not find YouTube's right-hand column: float over the page. */
:host(.floating) {
  position: fixed;
  top: 72px;
  right: 16px;
  width: 360px;
  max-width: calc(100vw - 32px);
  z-index: 2147483000;
}

.panel {
  box-sizing: border-box;
  min-height: 120px;
  padding: 12px 14px;
  font-family: Roboto, Arial, sans-serif;
  font-size: 14px;
  color: var(--text);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 12px;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  font-weight: 500;
  font-size: 16px;
}

.video-id {
  font-size: 12px;
  font-weight: 400;
  color: var(--muted);
}

.status {
  color: var(--muted);
}

.status.error,
.summary-status.error {
  color: #d93025;
}

.notice {
  margin-top: 6px;
  font-size: 12px;
  color: var(--accent);
}

.summary-status {
  margin-top: 10px;
  color: var(--muted);
}

.summary {
  max-height: 240px;
  margin-top: 10px;
  padding-right: 4px;
  overflow-y: auto;
}

.section-title {
  margin: 10px 0 4px;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--muted);
}

.section-title:first-child {
  margin-top: 0;
}

.overview {
  line-height: 1.45;
}

.points {
  margin: 0;
  padding: 0;
  list-style: none;
}

.points li {
  display: flex;
  gap: 8px;
  padding: 4px 0;
  line-height: 1.4;
}

.ts {
  flex: none;
  align-self: flex-start;
  padding: 1px 6px;
  font: inherit;
  font-size: 12px;
  color: var(--accent);
  background: transparent;
  border: 1px solid var(--accent);
  border-radius: 6px;
  cursor: pointer;
}

.ts:hover {
  background: var(--accent);
  color: var(--bg);
}

.chat {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
}

.messages {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 260px;
  margin-bottom: 8px;
  overflow-y: auto;
}

.messages:empty {
  display: none;
}

.msg {
  padding: 8px 10px;
  line-height: 1.4;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  border-radius: 10px;
}

.msg.user {
  align-self: flex-end;
  max-width: 85%;
  color: var(--bg);
  background: var(--accent);
}

.msg.assistant {
  align-self: flex-start;
  background: var(--soft);
}

.msg.pending {
  color: var(--muted);
  font-style: italic;
}

.msg.error {
  color: #d93025;
}

.composer {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}

.composer textarea {
  flex: 1;
  box-sizing: border-box;
  padding: 8px 10px;
  font: inherit;
  color: var(--text);
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 8px;
  resize: none;
}

.composer textarea:focus {
  outline: 2px solid var(--accent);
  outline-offset: -1px;
}

.composer button {
  padding: 8px 12px;
  font: inherit;
  color: var(--bg);
  background: var(--accent);
  border: 0;
  border-radius: 8px;
  cursor: pointer;
}

.composer button:disabled,
.composer textarea:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
`;

const Sidebar = (() => {
  let host = null; // the element we put in the page
  let askHandler = null; // called with the question text when the user sends one
  const els = {}; // references to parts we update later

  // Put our CSS inside the shadow root. A constructed stylesheet is not affected by the page's
  // Content-Security-Policy; the <style> element is only a fallback for very old browsers.
  function applyStyles(root) {
    try {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(SIDEBAR_CSS);
      root.adoptedStyleSheets = [sheet];
    } catch {
      const style = document.createElement("style");
      style.textContent = SIDEBAR_CSS;
      root.append(style);
    }
  }

  function build() {
    host = document.createElement("div");
    host.id = "yt-assistant-host";
    const root = host.attachShadow({ mode: "open" });

    const panel = document.createElement("div");
    panel.className = "panel";
    panel.innerHTML = `
      <div class="header">
        <span>Learning Assistant</span>
        <span class="video-id"></span>
      </div>
      <div class="status">Sidebar loaded.</div>
      <div class="notice"></div>
      <div class="summary-status" hidden></div>
      <div class="summary" hidden>
        <div class="section-title">Summary</div>
        <div class="overview"></div>
        <div class="section-title">Key points</div>
        <ul class="points"></ul>
      </div>
      <div class="chat">
        <div class="messages"></div>
        <form class="composer">
          <textarea rows="2" placeholder="Preparing this video..." disabled></textarea>
          <button type="submit" disabled>Send</button>
        </form>
      </div>
    `; // static text only: nothing from the page or the user is inserted as HTML

    applyStyles(root);
    root.append(panel);
    els.videoId = panel.querySelector(".video-id");
    els.status = panel.querySelector(".status");
    els.notice = panel.querySelector(".notice");
    els.summaryStatus = panel.querySelector(".summary-status");
    els.summary = panel.querySelector(".summary");
    els.overview = panel.querySelector(".overview");
    els.points = panel.querySelector(".points");
    els.messages = panel.querySelector(".messages");
    els.form = panel.querySelector(".composer");
    els.input = panel.querySelector(".composer textarea");
    els.send = panel.querySelector(".composer button");

    // YouTube has keyboard shortcuts (space, k, f, m, arrows...). Keep what we type from reaching them.
    for (const type of ["keydown", "keyup", "keypress"]) {
      els.input.addEventListener(type, (event) => event.stopPropagation());
    }
    // Enter sends, Shift+Enter makes a new line. (isComposing: do not send while an input method is mid-word.)
    els.input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        els.form.requestSubmit();
      }
    });
    els.form.addEventListener("submit", (event) => {
      event.preventDefault();
      const question = els.input.value.trim();
      if (!question || els.input.disabled || !askHandler) return;
      els.input.value = "";
      askHandler(question);
    });
  }

  // The right-hand column, but ONLY when YouTube is really showing it beside the video (wide window).
  // In a narrow window YouTube stacks it under the video (or hides it): there we return null.
  function sideColumn() {
    const secondary = document.querySelector("#secondary");
    const primary = document.querySelector("#primary");
    if (!secondary || !primary) return null;
    const s = secondary.getBoundingClientRect();
    const p = primary.getBoundingClientRect();
    const besideVideo = s.width > 200 && s.left >= p.right - 2; // starts to the right of the video column
    return besideVideo ? secondary : null;
  }

  // Idempotent: put the host where it belongs right now. Safe to call every second.
  function mount() {
    const column = sideColumn();
    if (column) {
      host.classList.remove("floating");
      if (column.firstElementChild !== host) column.prepend(host);
    } else {
      host.classList.add("floating"); // narrow window: float over the page instead of being lost
      if (host.parentElement !== document.body) document.body.append(host);
    }
  }

  function show(videoId) {
    if (!host) build();
    if (els.videoId.textContent !== videoId) {
      // A different video: clear what belonged to the previous one.
      setSummary(null);
      setSummaryStatus("");
      els.notice.textContent = "";
      clearChat();
      setChatEnabled(false, "Preparing this video...");
    }
    els.videoId.textContent = videoId;
    mount();
  }

  function hide() {
    if (host) host.remove();
  }

  // Show a one-line status. isError turns it red.
  function setStatus(text, isError = false) {
    if (!host) return;
    els.status.textContent = text;
    els.status.classList.toggle("error", isError);
  }

  // The line under the status that says "Summarizing..." or shows an error. Empty text hides it.
  function setSummaryStatus(text, isError = false) {
    if (!host) return;
    els.summaryStatus.textContent = text;
    els.summaryStatus.hidden = !text;
    els.summaryStatus.classList.toggle("error", isError);
  }

  // Show {overview, key_points: [{start, ts, text}]}, or pass null to clear. onSeek(seconds) runs on a timestamp click.
  // Everything is inserted as TEXT, never as HTML: the summary comes from a language model.
  function setSummary(summary, onSeek) {
    if (!host) return;
    els.points.replaceChildren();
    if (!summary) {
      els.overview.textContent = "";
      els.summary.hidden = true;
      return;
    }
    els.overview.textContent = summary.overview;
    for (const point of summary.key_points) {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.className = "ts";
      button.textContent = point.ts;
      button.title = `Jump to ${point.ts}`;
      button.addEventListener("click", () => onSeek(point.start));
      const text = document.createElement("span");
      text.textContent = point.text;
      li.append(button, text);
      els.points.append(li);
    }
    els.summary.hidden = false;
  }

  // A short message that disappears by itself (for example "An ad is playing").
  let noticeTimer = null;
  function notice(text) {
    if (!host) return;
    els.notice.textContent = text;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => (els.notice.textContent = ""), 3500);
  }

  // ---- Chat (Step 20) ----

  function onAsk(handler) {
    askHandler = handler;
  }

  function scrollChat() {
    els.messages.scrollTop = els.messages.scrollHeight;
  }

  function addUserMessage(text) {
    const message = document.createElement("div");
    message.className = "msg user";
    message.textContent = text; // text only, never HTML
    els.messages.append(message);
    scrollChat();
  }

  // Adds an empty assistant bubble and returns a small handle to fill it as the answer streams in.
  function startAssistantMessage() {
    const message = document.createElement("div");
    message.className = "msg assistant pending";
    message.textContent = "Thinking...";
    els.messages.append(message);
    scrollChat();
    return {
      setText(text) {
        message.classList.remove("pending");
        message.textContent = text; // text only: the answer comes from a language model
        scrollChat();
      },
      setError(text) {
        message.classList.remove("pending");
        message.classList.add("error");
        message.textContent = text;
        scrollChat();
      },
      isPending: () => message.classList.contains("pending"),
      hasError: () => message.classList.contains("error"),
    };
  }

  function clearChat() {
    els.messages.replaceChildren();
  }

  // Turn the input on or off. `hint` is the grey text shown inside it while it is off.
  function setChatEnabled(enabled, hint) {
    if (!host) return;
    els.input.disabled = !enabled;
    els.send.disabled = !enabled;
    els.input.placeholder = enabled ? "Ask about this video..." : hint || "Please wait...";
  }

  // YouTube re-renders its page and the window can be resized: re-check the placement regularly.
  function keepAttached() {
    if (host) mount();
  }

  return {
    show,
    hide,
    setStatus,
    setSummaryStatus,
    setSummary,
    notice,
    onAsk,
    addUserMessage,
    startAssistantMessage,
    setChatEnabled,
    keepAttached,
    els,
  };
})();
