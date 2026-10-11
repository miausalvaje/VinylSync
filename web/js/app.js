import { stateStore } from "./state.js";
import { startVisualizer } from "./visualizer.js";
import { setupLyricsSync } from "./lyrics-sync.js";
import { setupBackground } from "./background.js";
import { setupFullscreen } from "./fullscreen.js";
import { setupSettings } from "./settings.js";
import { setupManualSearch } from "./manual-search.js";

const visualizerView = document.querySelector("#visualizer-view");
const lyricsView = document.querySelector("#lyrics-view");
const stateLabel = document.querySelector("#state-label");
const identifying = document.querySelector("#identifying-indicator");
let forcedView = false;

function setView(name, manual = false) {
  if (manual) forcedView = true;
  if (!manual && forcedView) return;
  const lyrics = name === "lyrics";
  visualizerView.classList.toggle("view--active", !lyrics);
  lyricsView.classList.toggle("view--active", lyrics);
  lyricsView.setAttribute("aria-hidden", String(!lyrics));
}

stateStore.onState((payload) => {
  const state = payload.state || "idle";
  stateLabel.textContent = state === "playing" ? "Track identified" : state;
  identifying.hidden = state !== "identifying";
  if (state === "no_source") stateLabel.textContent = "No audio source available";
  if (state === "error") stateLabel.textContent = payload.message || "Server error";
  const track = payload.track;
  document.querySelector("#track-title").textContent = track?.title || "Waiting for track";
  document.querySelector("#track-artist").textContent = track?.artist || "Waiting for track";
  const locked = Boolean(track?.locked);
  document.querySelector("#track-lock").hidden = !locked;
  document.querySelector("#unlock-track").hidden = !locked;
  if (state === "playing") setView("lyrics");
  if (state === "idle" || state === "listening") setView("visualizer");
});

stateStore.connect();
startVisualizer(stateStore);
setupLyricsSync(stateStore);
setupBackground(stateStore);
setupFullscreen();
setupSettings(stateStore);
setupManualSearch(stateStore);
document.querySelector("#unlock-track").addEventListener("click", async () => {
  await fetch("/api/track/unlock", { method: "POST" });
});

let inactivityTimer;
function resetInactivity() {
  document.body.classList.remove("ui-idle");
  clearTimeout(inactivityTimer);
  const panelOpen = document.querySelector("#settings-panel")?.classList.contains("side-panel--open");
  const dialogOpen = document.querySelector("#search-dialog")?.open;
  if (panelOpen || dialogOpen) return;
  inactivityTimer = setTimeout(() => document.body.classList.add("ui-idle"), 3000);
}
["mousemove", "keydown", "touchstart"].forEach((eventName) => {
  document.addEventListener(eventName, resetInactivity, { passive: true });
});
resetInactivity();

document.addEventListener("keydown", (event) => {
  if (event.target.matches("input, select, textarea")) return;
  if (event.key.toLowerCase() === "v") setView(lyricsView.classList.contains("view--active") ? "visualizer" : "lyrics", true);
  if (event.key.toLowerCase() === "s") {
    document.querySelector("#settings-button")?.click();
  }
  if (event.key.toLowerCase() === "m") document.querySelector("#manual-search-button").click();
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    document.querySelector(event.key === "ArrowLeft" ? "#offset-down" : "#offset-up").click();
  }
  if (event.key === "Escape") {
    const settingsPanel = document.querySelector("#settings-panel");
    const searchDialog = document.querySelector("#search-dialog");
    if (settingsPanel?.classList.contains("side-panel--open")) {
      const closeBtn = settingsPanel.querySelector(".panel-close");
      closeBtn ? closeBtn.click() : settingsPanel.classList.remove("side-panel--open");
      return;
    }
    if (searchDialog?.open) {
      searchDialog.close();
      return;
    }
    forcedView = false;
    setView("visualizer");
  }
});
