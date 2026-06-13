const state = {
  session: null,
  cueIndex: 0,
  windowStart: 0,
  windowSeconds: 60,
  pendingG: false,
  stopTimer: null,
};

const audio = document.getElementById("audio");
const cueList = document.getElementById("cue-list");

function pad2(value) {
  return String(value).padStart(2, "0");
}

function formatClock(seconds) {
  const safe = Math.max(0, seconds || 0);
  const minutesTotal = Math.floor(safe / 60);
  const hours = Math.floor(minutesTotal / 60);
  const minutes = minutesTotal % 60;
  const wholeSeconds = Math.floor(safe % 60);
  const millis = Math.floor((safe - Math.floor(safe)) * 1000);
  if (hours > 0) {
    return `${pad2(hours)}:${pad2(minutes)}:${pad2(wholeSeconds)}.${String(millis).padStart(3, "0")}`;
  }
  return `${pad2(minutes)}:${pad2(wholeSeconds)}.${String(millis).padStart(3, "0")}`;
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds)) {
    return "0.0s";
  }
  if (seconds >= 60) {
    const minutes = Math.floor(seconds / 60);
    const rest = seconds - minutes * 60;
    return `${minutes}m${rest.toFixed(1)}s`;
  }
  return `${seconds.toFixed(1)}s`;
}

function pathStem(path) {
  const name = String(path || "").split(/[\\/]/).pop() || "";
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
}

function currentCue() {
  return state.session.cues[state.cueIndex] || null;
}

function timelineEnd() {
  return Math.max(1, state.session.timeline_end_s || 1);
}

function clampWindow() {
  const maxStart = Math.max(0, timelineEnd() - state.windowSeconds);
  state.windowStart = Math.max(0, Math.min(maxStart, state.windowStart));
}

function ensureCueVisible() {
  const cue = currentCue();
  if (!cue) {
    return;
  }
  const windowEnd = state.windowStart + state.windowSeconds;
  if (cue.start_s < state.windowStart) {
    state.windowStart = cue.start_s - state.windowSeconds * 0.15;
  } else if (cue.end_s > windowEnd) {
    state.windowStart = cue.end_s - state.windowSeconds * 0.85;
  }
  clampWindow();
}

function setCue(index, { scroll = true } = {}) {
  state.cueIndex = Math.max(0, Math.min(state.session.cues.length - 1, index));
  ensureCueVisible();
  render();
  if (scroll) {
    document.querySelector(".cue.active")?.scrollIntoView({ block: "nearest" });
  }
}

function moveCue(delta) {
  stopPlayback();
  setCue(state.cueIndex + delta);
}

function goStart() {
  state.pendingG = false;
  state.windowStart = 0;
  setCue(0);
}

function goEnd() {
  state.pendingG = false;
  state.windowStart = Math.max(0, timelineEnd() - state.windowSeconds);
  setCue(state.session.cues.length - 1);
}

function pageWindow(factor) {
  state.windowStart += state.windowSeconds * factor;
  clampWindow();
  selectCueNear(state.windowStart + state.windowSeconds / 2);
}

function zoomWindow(factor) {
  const cue = currentCue();
  const focus = cue ? (cue.start_s + cue.end_s) / 2 : state.windowStart + state.windowSeconds / 2;
  state.windowSeconds = Math.max(10, Math.min(timelineEnd(), state.windowSeconds * factor));
  state.windowStart = focus - state.windowSeconds / 2;
  clampWindow();
  render();
}

function selectCueNear(seconds) {
  const index = state.session.cues.findIndex((cue) => cue.end_s >= seconds);
  setCue(index === -1 ? state.session.cues.length - 1 : index);
}

function togglePlayback() {
  if (!audio.paused) {
    stopPlayback();
    return;
  }
  const cue = currentCue();
  if (!cue) {
    return;
  }
  audio.currentTime = cue.start_s;
  audio.play();
  clearTimeout(state.stopTimer);
  state.stopTimer = setTimeout(() => {
    stopPlayback();
  }, Math.max(50, (cue.end_s - cue.start_s) * 1000));
  renderPlayback(`playing ${formatClock(cue.start_s)} -> ${formatClock(cue.end_s)}`);
}

function stopPlayback() {
  clearTimeout(state.stopTimer);
  state.stopTimer = null;
  audio.pause();
  renderPlayback("stopped");
}

function renderPlayback(text) {
  document.getElementById("playback-label").textContent = text;
}

function render() {
  const cue = currentCue();
  document.getElementById("cue-stat").textContent = `${state.cueIndex + 1}/${state.session.cues.length}`;
  const subtitleTrack = state.session.timeline_tracks.find((track) => track.kind === "subtitle");
  document.getElementById("active-stat").textContent = formatDuration(
    subtitleTrack.spans.reduce((total, span) => total + Math.max(0, span.end_s - span.start_s), 0),
  );
  document.getElementById("span-stat").textContent = formatDuration(timelineEnd());
  document.getElementById("window-range").textContent =
    `${formatClock(state.windowStart)} -> ${formatClock(state.windowStart + state.windowSeconds)}`;
  document.getElementById("window-span").textContent = `${state.windowSeconds.toFixed(1)}s`;
  document.getElementById("tick-start").textContent = formatClock(state.windowStart);
  document.getElementById("tick-mid").textContent = formatClock(state.windowStart + state.windowSeconds / 2);
  document.getElementById("tick-end").textContent = formatClock(state.windowStart + state.windowSeconds);

  if (cue) {
    document.getElementById("current-time").textContent =
      `${cue.index}  ${formatClock(cue.start_s)} -> ${formatClock(cue.end_s)}`;
    document.getElementById("current-text").textContent = cue.text || "<empty cue>";
  }

  renderTimeline();
  renderCueList();
}

function renderTimeline() {
  const root = document.getElementById("timeline-tracks");
  root.replaceChildren();
  const start = state.windowStart;
  const end = state.windowStart + state.windowSeconds;

  for (const track of state.session.timeline_tracks) {
    const row = document.createElement("div");
    row.className = "timeline-row";
    row.dataset.kind = track.kind;

    const label = document.createElement("div");
    label.className = "timeline-label";
    label.textContent = pathStem(track.label);
    row.append(label);

    const bar = document.createElement("div");
    bar.className = "timeline-bar";
    for (const span of track.spans) {
      const overlapStart = Math.max(start, span.start_s);
      const overlapEnd = Math.min(end, span.end_s);
      if (overlapEnd <= overlapStart) {
        continue;
      }
      const node = document.createElement("button");
      node.className = "timeline-span";
      if (span.cue_index === state.cueIndex) {
        node.classList.add("active");
      }
      node.style.left = `${((overlapStart - start) / state.windowSeconds) * 100}%`;
      node.style.width = `${((overlapEnd - overlapStart) / state.windowSeconds) * 100}%`;
      node.type = "button";
      node.title = span.label;
      if (span.cue_index !== null && span.cue_index !== undefined) {
        node.addEventListener("click", () => setCue(span.cue_index));
      }
      bar.append(node);
    }
    row.append(bar);
    root.append(row);
  }
}

function renderCueList() {
  cueList.replaceChildren();
  for (const [index, cue] of state.session.cues.entries()) {
    const item = document.createElement("li");
    item.className = "cue";
    if (index === state.cueIndex) {
      item.classList.add("active");
    }
    item.addEventListener("click", () => setCue(index));

    const cueIndex = document.createElement("div");
    cueIndex.className = "cue-index";
    cueIndex.textContent = String(cue.index);
    item.append(cueIndex);

    const cueTime = document.createElement("div");
    cueTime.className = "cue-time";
    cueTime.textContent = `${formatClock(cue.start_s)} -> ${formatClock(cue.end_s)}`;
    item.append(cueTime);

    const cueText = document.createElement("div");
    cueText.className = "cue-text jp-text";
    cueText.textContent = cue.text || "<empty cue>";
    item.append(cueText);
    cueList.append(item);
  }
}

function handleKey(event) {
  if (event.target.matches("select")) {
    return;
  }
  const key = event.key;
  let handled = true;
  if (key === " ") {
    togglePlayback();
  } else if (key === "j" || key === "l" || key === "ArrowDown" || key === "ArrowRight") {
    moveCue(1);
  } else if (key === "k" || key === "h" || key === "ArrowUp" || key === "ArrowLeft") {
    moveCue(-1);
  } else if (key === "g") {
    if (state.pendingG) {
      goStart();
    } else {
      state.pendingG = true;
      setTimeout(() => {
        state.pendingG = false;
      }, 700);
    }
  } else if (key === "G") {
    goEnd();
  } else if (key === "f") {
    pageWindow(1);
  } else if (key === "b") {
    pageWindow(-1);
  } else if (key === "d") {
    pageWindow(0.5);
  } else if (key === "u") {
    pageWindow(-0.5);
  } else if (key === "+" || key === "=") {
    zoomWindow(0.5);
  } else if (key === "-" || key === "_") {
    zoomWindow(2);
  } else {
    handled = false;
  }
  if (handled) {
    event.preventDefault();
  }
}

async function main() {
  const response = await fetch("/session.json");
  state.session = await response.json();
  document.title = `${state.session.title} - ja-media reader`;
  document.getElementById("media-name").textContent = pathStem(state.session.media_path);
  document.getElementById("subtitle-name").textContent = pathStem(state.session.subtitle_path);
  state.windowSeconds = Math.min(60, timelineEnd());
  clampWindow();
  render();

  document.getElementById("jp-font").addEventListener("change", (event) => {
    document.body.classList.toggle("jp-gothic", event.target.value === "gothic");
    document.body.classList.toggle("jp-system", event.target.value === "system");
  });
  document.addEventListener("keydown", handleKey);
  audio.addEventListener("ended", () => renderPlayback("stopped"));
}

main();
