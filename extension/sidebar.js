// content.js (same isolated world) calls Sidebar.show(videoId) / Sidebar.hide().

const Sidebar = (() => {
  let host = null; // the element we put in the page
  const els = {}; // references to parts we update later

  function build() {
    host = document.createElement("div");
    host.id = "yt-assistant-host";
    const root = host.attachShadow({ mode: "open" });

    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = chrome.runtime.getURL("sidebar.css");

    const panel = document.createElement("div");
    panel.className = "panel";
    panel.innerHTML = `
      <div class="header">
        <span>Learning Assistant</span>
        <span class="video-id"></span>
      </div>
      <div class="status">Sidebar loaded.</div>
    `; // static text only: nothing from the page or the user is inserted as HTML

    root.append(css, panel);
    els.videoId = panel.querySelector(".video-id");
    els.status = panel.querySelector(".status");
  }

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
    els.videoId.textContent = videoId;
    mount();
  }

  function hide() {
    if (host) host.remove();
  }
  // Show a one-line status . isErrro turns it red .

  function setStatus(text , isError= false){
    if(!host) return;
    els.status.textContent = text ;
    els.status.classList.toggle("error" , isError);
  }


  // YouTube re-renders its page and the window can be resized: re-check the placement regularly.
  function keepAttached() {
    if (host) mount();
  }

  return { show, hide,setStatus , keepAttached, els };
})();